"""Rocket Fmax loop on CHIA: an agent shortens RocketTile's critical path
without changing cycle behaviour.

One round:
  reset rocket-chip to the best patch -> agent edits Chisel (BashTool, stopped
  before the build) -> scope check -> build RocketConfig (Verilator +
  generated Verilog) -> in parallel: gate (riscv-tests ISA + benchmarks, mcycle
  must equal baseline) and Yosys/OpenSTA on RocketTile -> accept if build ok,
  gate ok and worst slack better than the best so far; else revert and feed the
  reason into the next prompt.

Milestones (run from this directory on the head):
  M1  chia job submit --working-dir . -- python rocket_fmax.py --baseline-only --period-ns 10
  M2  chia job submit --working-dir . -- python rocket_fmax.py --period-ns <P> --iters 1 --bypass-llm
  M3  chia job submit --working-dir . -- python rocket_fmax.py --period-ns <P> --iters 5
  --ab-tool: the agent also gets synth_ab (ab_tool.py) to elaborate its edit and
      synthesize one module of it next to the best (A/B), prompts/improve_timing_ab.md
  equivalence of a finished run's best patch vs the baseline (no loop):
      chia job submit --working-dir . -- python rocket_fmax.py --equiv-only --patch-file <best.patch>

Outputs go to --out-dir/<run-name>/ on the head (results.csv, patches/, runs/,
profile/). Needs workers advertising chipyard, verilator_run, eda, opencode_creds
(see cluster.yaml).
"""

import argparse
import csv
import dataclasses
import hashlib
import json
import os
import re
import string
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path

import ray

import ab_tool as AB
import equiv as E
import fmax_nodes as N
from chia.base.ChiaFunction import chia_cancel, get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.base.tools.BashTool import BashTool
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
from chia.trace.profiler import start_collector, stop_collector

HERE = Path(__file__).resolve().parent
EXP_YAML = HERE / "exp.yaml"
PROMPT = HERE / "prompts" / "improve_timing.md"
PROMPT_AB = HERE / "prompts" / "improve_timing_ab.md"
CSV_FIELDS = ["iter", "accepted", "wns_ns", "tns_ns", "worst_slack_ns", "fmax_mhz", "critical_path",
              "isa_pass", "bench_cycles_match", "reason", "area_um2", "elapsed_min"]
SYSTEM_MESSAGE = ("You are a hardware timing engineer working on the Rocket core in Chipyard. "
                  "You only edit Chisel sources through the chipyard_bash tool and never build, "
                  "simulate or synthesize: the surrounding loop does that after you finish.")
SYSTEM_MESSAGE_AB = ("You are a hardware timing engineer working on the Rocket core in Chipyard. "
                     "You edit Chisel sources through the chipyard_bash tool and check each edit with the "
                     "synth_ab tool (elaborate, then synthesize a module next to the current best). You never "
                     "run make, sbt, verilator or yosys yourself: the full build and gate run after you finish.")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--period-ns", type=float, default=None,
                   help="fixed clock period for abc -D and STA (required unless --equiv-only)")
    p.add_argument("--synth-flow", choices=sorted(N.SYNTH_FLOWS), default="v2",
                   help="Yosys flow (see SYNTH_FLOWS in fmax_nodes.py); fixed for a whole run")
    p.add_argument("--baseline-only", action="store_true", help="milestone 1: baseline only")
    p.add_argument("--iters", type=int, default=5)
    p.add_argument("--bypass-llm", action="store_true",
                   help="milestone 2: bypass OpenCodeLLM.prompt; the loop applies --patch-file "
                        "or a comment-only edit instead")
    p.add_argument("--patch-file", default=None, help="patch (git diff in rocket-chip) for --bypass-llm")
    p.add_argument("--marker-file", default="src/main/scala/rocket/ALU.scala",
                   help="file that gets a comment when --bypass-llm has no --patch-file")
    p.add_argument("--run-name", default=None)
    p.add_argument("--out-dir", default="~/rocket-fmax-out")
    p.add_argument("--cache-dir", default="~/chia_cache")
    p.add_argument("--flush-cache", action="store_true")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3600)
    p.add_argument("--exclude-isa", nargs="*", default=[],
                   help="ISA tests left out of the gate (they must be named explicitly, "
                        "e.g. tests that already fail on the unmodified baseline)")
    p.add_argument("--repeat-seed", type=int, default=7, help="baseline: rerun benchmarks with this seed")
    p.add_argument("--model", default="opencode/big-pickle")
    p.add_argument("--builtin-tools", action="store_true",
                   help="keep opencode's own bash/read/edit tools (they run in the opencode container, "
                        "which has no Chipyard; off by default so the agent only uses chipyard_bash/synth_ab)")
    p.add_argument("--llm-timeout", type=int, default=1800)
    p.add_argument("--ab-tool", action="store_true",
                   help="give the agent the synth_ab tool (ab_tool.py: make verilog + Yosys/OpenSTA "
                        "A/B of one module) and use prompts/improve_timing_ab.md")
    p.add_argument("--fragments", nargs="*", default=[],
                   help="config fragments put in front of RocketConfig for every round build "
                        "(e.g. the audit result); the loop writes them to RocketFmaxAudit.scala")
    p.add_argument("--start-patch", default=None,
                   help="start the rounds from this patch (e.g. best.patch of an earlier run) "
                        "instead of the unmodified rocket-chip")
    p.add_argument("--min-gain-ns", type=float, default=0.0,
                   help="accept only if worst slack improves by more than this")
    p.add_argument("--equiv", action="store_true",
                   help="after the loop: equivalence check of the best patch vs the baseline")
    p.add_argument("--equiv-only", action="store_true",
                   help="only the equivalence check of --patch-file vs the baseline (no loop)")
    p.add_argument("--equiv-cycles", type=int, default=10_000_000, help="lockstep cycles per unit")
    p.add_argument("--equiv-seed", type=int, default=1)
    p.add_argument("--equiv-timeout", type=int, default=900, help="Yosys equiv time limit per unit (s)")
    a = p.parse_args()
    if a.equiv_only and not a.patch_file:
        p.error("--equiv-only cần --patch-file")
    if not a.equiv_only and a.period_ns is None:
        p.error("--period-ns là bắt buộc")
    if a.run_name is None:
        mode = ("equiv" if a.equiv_only else "baseline" if a.baseline_only
                else "m2" if a.bypass_llm else "agent")
        a.run_name = f"{mode}-{datetime.now().strftime('%Y%m%d-%H%M')}"
    a.out_dir = os.path.expanduser(a.out_dir)
    a.cache_dir = os.path.expanduser(a.cache_dir)
    return a


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else "")


def as_json(obj) -> str:
    return json.dumps(dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj,
                      indent=2, ensure_ascii=False, default=str)


def fmt(x, nd=3):
    return "" if x is None else f"{x:.{nd}f}"


def append_row(csv_path: Path, row: dict):
    new = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def synth_row(s) -> dict:
    if s is None:
        return {}
    return {"wns_ns": fmt(s.wns_ns, 4), "tns_ns": fmt(s.tns_ns, 4), "worst_slack_ns": fmt(s.worst_slack_ns, 4),
            "fmax_mhz": fmt(s.fmax_mhz, 2), "critical_path": f"{s.crit_start} -> {s.crit_end}",
            "area_um2": fmt(s.area_um2, 0)}


def cache_has(tag: str) -> bool:
    from chia.base.cache import get_active_cache
    return bool(get(get_active_cache().has.chia_remote(tag)))


def build(a, name: str, config: str = N.CONFIG):
    cb = ChiselBuildNode(N.CHIPYARD, config, target=BuildTarget.VERILATOR, make_jobs=a.make_jobs,
                         timeout_seconds=a.build_timeout, collect_generated_src=True, name=name)
    return cb.build.chia_remote(cb)


LOOP_CONFIG = "RocketFmaxCfg"


def loop_config(a) -> str:
    return f"{N.CONFIG} + " + " ++ ".join(a.fragments) if a.fragments else N.CONFIG


def loop_config_class(a) -> tuple[str, str | None]:
    """(config name, text of the config file to write before building it or None)."""
    if not a.fragments:
        return N.CONFIG, None
    return LOOP_CONFIG, "\n".join([
        "// Generated by projects/rocket-fmax/rocket_fmax.py (--fragments); removed after the build.",
        "package chipyard", "", "import org.chipsalliance.cde.config.{Config}", "",
        f"class {LOOP_CONFIG} extends Config(", *[f"  {f} ++" for f in a.fragments],
        "  new chipyard.RocketConfig)", ""])


def build_loop(a, name: str):
    """Build for a round (or the start point): RocketConfig, or with --fragments the
    class LOOP_CONFIG written to AUDIT_CONFIG_FILE just for the build. The file is
    written after collect_diff and removed before the next round, so the scope
    check never sees it."""
    if not a.fragments:
        return get(build(a, name))
    _, text = loop_config_class(a)
    get(N.write_audit_config.chia_remote(text))
    try:
        return get(build(a, name, config=LOOP_CONFIG))
    finally:
        get(N.write_audit_config.chia_remote(None))


def ensure_start(a, out: Path, snap: dict, base: dict) -> dict:
    """Start point of the rounds when --start-patch/--fragments are given: build +
    synth/STA, cached under baseline_start_<hash>_* (no gate: check it first with
    measure_soc.py, which gates best_cfg)."""
    patch = Path(a.start_patch).expanduser().read_text() if a.start_patch else ""
    h = hashlib.sha256((patch + "\0" + "\n".join(a.fragments)).encode()).hexdigest()[:10]
    tags = {"rtl": f"baseline_start_{h}_rtl",
            "synth": f"baseline_start_{h}_synth_{a.synth_flow}_p" + f"{a.period_ns:g}".replace(".", "p")}
    hits = {k: cache_has(t) for k, t in tags.items()}
    print(f"\nstart point ({loop_config(a)}, patch {a.start_patch or '-'}): "
          + ", ".join(f"{tags[k]}={'HIT' if v else 'miss'}" for k, v in hits.items()), flush=True)
    d = out / "runs" / "start"
    write(d / "start.patch", patch)
    art = None
    if not hits["rtl"]:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], patch))
        art = build_loop(a, "start")
        save_build(d, art)
        if not art.success:
            raise SystemExit(f"start point build failed:\n{build_errors(art)}")
    rtl = get(N.extract_tile_rtl.chia_remote(art, _chia_tag=tags["rtl"]))
    synth = get(N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow, _chia_tag=tags["synth"]))
    save_synth(d, synth)
    print_synth("start", synth)
    if not synth.success:
        raise SystemExit(f"start point synth/STA lỗi: {synth.error}")
    append_row(out / "results.csv", {"iter": "start", "accepted": True, **synth_row(synth),
                                     "reason": f"start: {loop_config(a)}, patch {a.start_patch or '-'}"})
    return {"name": "start", "patch": patch, "synth": synth, "rtl": rtl}


def build_errors(art) -> str:
    # sbt is run with -Dsbt.color=always: "[\x1b[31merror\x1b[0m]" would not match "[error]".
    text = re.sub(r"\x1b\[[0-9;]*m", "", (art.stdout or "") + "\n" + (art.stderr or ""))
    lines = [l for l in text.splitlines()
             if re.search(r"\[error\]|error:|%Error|Exception|FAILED", l)]
    return "\n".join(lines[:80]) if lines else "\n".join(text.splitlines()[-60:])


def save_build(d: Path, art):
    write(d / "build.log", f"success={art.success} returncode={art.returncode}\n"
                           f"[stdout tail]\n{(art.stdout or '')[-200000:]}\n[stderr tail]\n{(art.stderr or '')[-100000:]}")


def save_synth(d: Path, s):
    write(d / "timing_paths.rpt", s.paths)
    write(d / "timing_endpoints.rpt", s.endpoints)
    write(d / "timing_families.txt", s.families)
    write(d / "sta.log", s.sta_log)
    write(d / "yosys.log", s.yosys_log_tail)
    write(d / "yosys_stat.txt", s.stat)
    summary = {k: v for k, v in dataclasses.asdict(s).items()
               if k not in ("paths", "endpoints", "families", "sta_log", "yosys_log_tail", "stat")}
    write(d / "synth_summary.json", json.dumps(summary, indent=2, default=str))


def save_gate(d: Path, g):
    write(d / "gate.json", as_json(g))
    rows = ["bench,passed,mcycle,minstret,sim_cycles"]
    rows += [f"{n},{b['passed']},{b['mcycle']},{b['minstret']},{b['sim_cycles']}" for n, b in g.bench.items()]
    write(d / "cycles.csv", "\n".join(rows) + "\n")
    if g.fail_logs:
        write(d / "sim_fail.log", "\n\n".join(f"===== {n}\n{t}" for n, t in g.fail_logs.items()))


def compare_gate(base, cur, exclude=()) -> tuple[bool, bool, list[str]]:
    """(all ISA pass except `exclude`, every benchmark passes with baseline mcycle, reasons)."""
    reasons = []
    isa_fail = [n for n in base.isa if n not in exclude and not cur.isa.get(n, False)]
    if isa_fail:
        reasons.append(f"ISA fail ({len(isa_fail)}): {', '.join(isa_fail[:15])}")
    cyc_ok = True
    for n, b in base.bench.items():
        c = cur.bench.get(n)
        if not c or not c["passed"]:
            cyc_ok = False
            reasons.append(f"{n}: không pass")
        elif c["mcycle"] != b["mcycle"]:
            cyc_ok = False
            reasons.append(f"{n}: mcycle {b['mcycle']} -> {c['mcycle']} ({c['mcycle'] - b['mcycle']:+d})")
        elif c["minstret"] != b["minstret"]:
            reasons.append(f"{n}: minstret {b['minstret']} -> {c['minstret']} (cảnh báo)")
    return not isa_fail, cyc_ok, reasons


def print_synth(label: str, s):
    print(f"\n===== {label}: flow {s.flow}, period {s.period_ns} ns")
    print(f"worst slack {fmt(s.worst_slack_ns, 4)} ns | WNS {fmt(s.wns_ns, 4)} | TNS {fmt(s.tns_ns, 4)} | "
          f"Fmax ~ {fmt(s.fmax_mhz, 2)} MHz | area {fmt(s.area_um2, 0)} um2 | "
          f"yosys {s.yosys_seconds / 60:.1f} min, sta {s.sta_seconds:.0f} s")
    print(f"critical path: {s.crit_start} -> {s.crit_end}")
    print("worst endpoint families:\n" + s.families)
    print("top-10 endpoints:\n" + "\n".join(s.endpoints.splitlines()[:15]), flush=True)


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------

def ensure_baseline(a, out: Path, elfs: dict, snap: dict) -> dict:
    """Build + gate + synth of the unmodified tree, served from the cache when
    available (tags baseline_rtl / baseline_gate / baseline_synth_<flow>_p<period>)."""
    tags = {"rtl": "baseline_rtl", "gate": "baseline_gate",
            "synth": f"baseline_synth_{a.synth_flow}_p" + f"{a.period_ns:g}".replace(".", "p")}
    hits = {k: cache_has(t) for k, t in tags.items()}
    print(f"baseline cache: " + ", ".join(f"{tags[k]}={'HIT' if v else 'miss'}" for k, v in hits.items()),
          flush=True)
    d = out / "runs" / "baseline"
    t0 = time.time()
    art = None
    if not (hits["rtl"] and hits["gate"]):
        print(get(N.reset_rocket.chia_remote(snap["rocket_head"], "")) or "rocket-chip clean", flush=True)
        art = get(build(a, "baseline"))
        save_build(d, art)
        if not art.success:
            raise SystemExit(f"baseline build failed:\n{build_errors(art)}")
        print(f"baseline build ok ({(time.time() - t0) / 60:.1f} min)", flush=True)
    # build -> (gate || synth); synth is only dispatched once the build is done.
    # Pass values, not refs: with the profiler on, a ref from chia_remote resolves
    # to a _ProfiledResult wrapper inside the next task (only get() unwraps it).
    rtl = get(N.extract_tile_rtl.chia_remote(art, _chia_tag=tags["rtl"]))
    gate_ref = N.run_gate.chia_remote(art, elfs, repeat_seed=a.repeat_seed, _chia_tag=tags["gate"])
    synth_ref = N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow, _chia_tag=tags["synth"])
    gate = get(gate_ref)
    synth = get(synth_ref)

    write(d / "rtl_info.json", json.dumps({"top": rtl.top, "files": len(rtl.files), "modules": rtl.modules,
                                           "blackboxes": list(rtl.blackboxes), "replaced": rtl.replaced},
                                          indent=2))
    save_gate(d, gate)
    save_synth(d, synth)
    print_synth("baseline", synth)
    print(f"gate: ISA {sum(gate.isa.values())}/{len(gate.isa)} pass, gate {gate.seconds / 60:.1f} min")
    for n, b in gate.bench.items():
        r = gate.bench_repeat.get(n, {})
        print(f"  {n:10s} pass={b['passed']} mcycle={b['mcycle']} minstret={b['minstret']} "
              f"sim_cycles={b['sim_cycles']} | seed {a.repeat_seed}: mcycle={r.get('mcycle')}")
    first = next(iter(gate.bench.values()), {})
    print(f"  seed line: {first.get('seed_line', '')!r} / repeat: "
          f"{next(iter(gate.bench_repeat.values()), {}).get('seed_line', '')!r}")

    unknown = [n for n in a.exclude_isa if n not in gate.isa]
    if unknown:
        raise SystemExit(f"--exclude-isa: không có test {unknown}")
    excluded_fail = [n for n in a.exclude_isa if not gate.isa[n]]
    print(f"ISA loại khỏi gate (--exclude-isa): {a.exclude_isa or 'không'}; trong đó fail ở baseline: "
          f"{excluded_fail or 'không'}")
    problems = [f"ISA fail: {n}" for n, ok in gate.isa.items() if not ok and n not in a.exclude_isa]
    problems += [f"benchmark fail: {n}" for n, b in gate.bench.items() if not b["passed"]]
    problems += [f"{n}: mcycle khác khi đổi seed ({b['mcycle']} vs {gate.bench_repeat[n]['mcycle']})"
                 for n, b in gate.bench.items()
                 if n in gate.bench_repeat and gate.bench_repeat[n]["mcycle"] != b["mcycle"]]
    if not synth.success:
        problems.append(f"synth/STA lỗi: {synth.error}")
    if problems:
        write(d / "problems.txt", "\n".join(problems))
        raise SystemExit("baseline chưa dùng được:\n  " + "\n  ".join(problems)
                         + f"\nxem {d}/sim_fail.log, yosys.log, sta.log")
    if synth.worst_slack_ns is not None:
        delay = a.period_ns - synth.worst_slack_ns
        print(f"\nđộ trễ đường găng baseline = {delay:.3f} ns (Fmax ~ {1000 / delay:.1f} MHz); "
              f"chu kỳ cố định đề xuất ~ {round(delay, 1)} ns", flush=True)
    append_row(out / "results.csv", {"iter": "baseline", "accepted": True, **synth_row(synth),
                                     "isa_pass": True, "bench_cycles_match": True, "reason": "baseline",
                                     "elapsed_min": f"{(time.time() - t0) / 60:.1f}"})
    return {"rtl": rtl, "gate": gate, "synth": synth}


# ---------------------------------------------------------------------------
# agent rounds
# ---------------------------------------------------------------------------

def render_prompt(a, i: int, best: dict, history: list[str]) -> str:
    s = best["synth"]
    return string.Template((PROMPT_AB if a.ab_tool else PROMPT).read_text()).safe_substitute(
        ITER=i, MAX_ITERS=a.iters, CONFIG_DESC=loop_config(a), PERIOD_NS=a.period_ns, BEST_NAME=best["name"],
        BEST_SLACK=fmt(s.worst_slack_ns, 4), BEST_FMAX=fmt(s.fmax_mhz, 2),
        CRIT=f"{s.crit_start} -> {s.crit_end}", ENDPOINTS=s.families,
        STAGE_DIR=N.STAGE_DIR, HISTORY="\n\n".join(history) or "(chưa có vòng nào trước)")


def stage_files(best: dict, history: list[str]) -> dict:
    s, rtl = best["synth"], best["rtl"]
    files = {"timing_paths.rpt": s.paths, "timing_endpoints.rpt": s.endpoints,
             "timing_families.txt": s.families,
             "best.patch": best["patch"], "history.md": "\n\n".join(history)}
    files.update({f"best_verilog/{n}": t for n, t in rtl.files.items()})
    return files


def run_round(a, i: int, out: Path, llm, elfs, snap, base, best, history) -> dict:
    """One round. Returns {"accepted", "reason", "synth", "rtl", "patch", ...}."""
    d = out / "runs" / f"iter{i}"
    head, outside = snap["rocket_head"], snap["outside"]
    r = {"accepted": False, "reason": "", "synth": None, "rtl": None, "patch": "",
         "isa_pass": "", "bench_cycles_match": ""}

    get(N.reset_rocket.chia_remote(head, best["patch"]))
    get(N.stage_for_agent.chia_remote(stage_files(best, history)))
    prompt = render_prompt(a, i, best, history)
    write(d / "prompt.md", prompt)

    tag = f"m2_iter{i}_llm" if a.bypass_llm else f"agent_{a.run_name}_iter{i}_llm"
    t_agent = time.time()
    if not a.bypass_llm:
        get(N.read_text_opencode.chia_remote(N.LLM_PARTIAL_FILE, True))   # stale from an earlier round
    if a.bypass_llm:
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=[], _chia_tag=tag))
        patch_in = Path(a.patch_file).read_text() if a.patch_file else None
        get(N.apply_edit.chia_remote(patch_in, a.marker_file, f"rocket-fmax milestone-2 bypass, iter{i}"))
    else:
        # With --ab-tool the two tools share the single "chipyard" slot, so nothing
        # else runs on chipyard (build, git) until both are stopped.
        slot = 0.5 if a.ab_tool else 1
        tools = [BashTool("chipyard_bash", N.CHIPYARD, timeout_seconds=120,
                          task_options={"resources": {"chipyard": slot}})]
        try:
            if a.ab_tool:
                config, config_text = loop_config_class(a)
                tools.append(AB.SynthABTool("synth_ab", best["rtl"], best["synth"], best["name"], config,
                                            config_text, a.period_ns, a.synth_flow, a.make_jobs,
                                            task_options={"resources": {"chipyard": slot}}))
            resp = get(llm.prompt.chia_remote(llm, prompt, tools=tools, _chia_tag=tag))
        except Exception as e:                                       # noqa: BLE001
            resp = None
            write(d / "agent_error.txt", traceback.format_exc())
            r["reason"] = f"LLM lỗi: {type(e).__name__}: {e}"[:500]
        finally:
            for t in tools:
                t.stop()          # free the chipyard slot before the build
        if a.ab_tool:
            write(d / "ab_cleanup.txt", get(N.ab_cleanup.chia_remote()))
            write(d / "ab_experiments.jsonl", get(N.read_text.chia_remote(AB.LOG_FILE)) or "")
    r["agent_min"] = (time.time() - t_agent) / 60
    if resp is not None:
        write(d / "agent_response.md", resp.result)
        write(d / "agent_transcript.txt", resp.stream_result)
        if not resp.success and not r["reason"]:
            r["reason"] = f"LLM không thành công (returncode {resp.returncode})"
        if not resp.success and not a.bypass_llm:
            partial = get(N.read_text_opencode.chia_remote(N.LLM_PARTIAL_FILE, True))
            if partial:
                write(d / "agent_transcript.txt", partial)

    diff = get(N.collect_diff.chia_remote(head, outside))
    r["patch"] = diff["patch"]
    write(out / "patches" / f"iter{i}.patch", diff["patch"])
    write(d / "diff_files.txt", "\n".join(diff["files"]))
    if diff["outside_changed"]:
        rest = get(N.restore_outside.chia_remote(outside))
        write(d / "outside_restore.json", json.dumps(rest, indent=2))
        if not rest["ok"]:
            raise SystemExit(f"agent sửa ngoài rocket-chip và không khôi phục được: {rest['remaining']}\n"
                             "tạo lại container chipyard (docker rm -f ... && chia up --add) rồi chạy lại")
    violations = N.scope_violations(diff["files"], diff["outside_changed"])
    if r["reason"]:
        return r
    if violations:
        r["reason"] = "vi phạm phạm vi: " + "; ".join(violations)
        return r
    if diff["patch"] == best["patch"]:
        r["reason"] = f"agent không thay đổi gì so với {best['name']}"
        return r

    t_build = time.time()
    art = build_loop(a, f"iter{i}")       # values only, see ensure_baseline
    save_build(d, art)
    r["build_min"] = (time.time() - t_build) / 60
    if not art.success:
        r["reason"] = "build lỗi:\n" + build_errors(art)
        return r

    try:
        rtl = get(N.extract_tile_rtl.chia_remote(art))
    except Exception as e:                                           # noqa: BLE001
        r["reason"] = f"không lấy được Verilog {N.TOP}: {e}"[:1000]
        return r
    r["rtl"] = rtl
    gate_ref = N.run_gate.chia_remote(art, elfs)
    synth_ref = N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow)
    gate = get(gate_ref)
    save_gate(d, gate)
    isa_ok, cyc_ok, why = compare_gate(base["gate"], gate, a.exclude_isa)
    r["isa_pass"], r["bench_cycles_match"] = isa_ok, cyc_ok
    write(d / "gate_compare.txt", "\n".join(why) or "khớp baseline")
    if not (isa_ok and cyc_ok):
        chia_cancel(synth_ref, force=True)
        r["reason"] = "gate không đạt: " + "; ".join(why)
        return r

    synth = get(synth_ref)
    save_synth(d, synth)
    r["synth"] = synth
    if not synth.success:
        r["reason"] = f"synth/STA lỗi: {synth.error}"
        return r
    old = best["synth"].worst_slack_ns
    if synth.worst_slack_ns <= old + a.min_gain_ns:
        r["reason"] = (f"slack không tốt hơn: {synth.worst_slack_ns:.4f} ns so với {old:.4f} ns "
                       f"của {best['name']} (đường găng {synth.crit_start} -> {synth.crit_end})")
        return r
    r["accepted"] = True
    r["reason"] = f"slack {old:.4f} -> {synth.worst_slack_ns:.4f} ns"
    return r


def history_entry(i: int, r: dict, resp_summary: str) -> str:
    s = r.get("synth")
    lines = [f"### Vòng {i}: {'NHẬN' if r['accepted'] else 'LOẠI'}",
             f"Lý do: {r['reason'][:3000]}"]
    if s is not None and s.success:
        lines.append(f"Kết quả: worst slack {s.worst_slack_ns:.4f} ns, đường găng {s.crit_start} -> {s.crit_end}")
    if resp_summary:
        lines.append(f"Tóm tắt của agent: {resp_summary[:800]}")
    if not r["accepted"] and r["patch"]:
        lines.append("Patch đã bị loại (đã revert, đừng lặp lại y nguyên):\n```diff\n" + r["patch"][:4000] + "\n```")
    return "\n".join(lines)


def run(a, out: Path):
    csv_path = out / "results.csv"
    elfs = get(N.load_test_elfs.chia_remote())
    print(f"ELF: {len(elfs['isa'])} ISA tests, benchmarks {list(elfs['bench'])}", flush=True)
    get(N.write_audit_config.chia_remote(None))      # leftover of an interrupted run
    snap = get(N.tree_snapshot.chia_remote())
    write(out / "runs" / "tree_snapshot.json", json.dumps(snap, indent=2))

    base = ensure_baseline(a, out, elfs, snap)
    if a.baseline_only:
        return

    llm = N.SalvageOpenCodeLLM(model=a.model, system_message=SYSTEM_MESSAGE_AB if a.ab_tool else SYSTEM_MESSAGE,
                      timeout_seconds=a.llm_timeout, retries=1, builtin_tools=a.builtin_tools)
    best = {"name": "baseline", "patch": "", "synth": base["synth"], "rtl": base["rtl"]}
    if a.start_patch or a.fragments:
        best = ensure_start(a, out, snap, base)
    start = best
    history: list[str] = []
    for i in range(1, a.iters + 1):
        t0 = time.time()
        print(f"\n######## round {i}/{a.iters}", flush=True)
        try:
            r = run_round(a, i, out, llm, elfs, snap, base, best, history)
        except SystemExit:
            raise
        except Exception as e:                                       # noqa: BLE001
            write(out / "runs" / f"iter{i}" / "error.txt", traceback.format_exc())
            r = {"accepted": False, "reason": f"lỗi hạ tầng: {type(e).__name__}: {e}"[:1000],
                 "synth": None, "patch": "", "isa_pass": "", "bench_cycles_match": ""}
        r["minutes"] = (time.time() - t0) / 60
        resp_path = out / "runs" / f"iter{i}" / "agent_response.md"
        resp_summary = resp_path.read_text()[-1500:] if resp_path.exists() else ""
        write(out / "runs" / f"iter{i}" / "decision.json",
              json.dumps({k: v for k, v in r.items() if k not in ("synth", "rtl", "patch")},
                         indent=2, ensure_ascii=False, default=str))
        append_row(csv_path, {"iter": i, "accepted": r["accepted"], **synth_row(r.get("synth")),
                              "isa_pass": r["isa_pass"], "bench_cycles_match": r["bench_cycles_match"],
                              "reason": r["reason"].replace("\n", " | ")[:1000],
                              "elapsed_min": f"{r['minutes']:.1f}"})
        print(f"round {i}: {'ACCEPT' if r['accepted'] else 'REJECT'} ({r['minutes']:.1f} min) - "
              f"{r['reason'][:400]}", flush=True)
        if r["accepted"]:
            best = {"name": f"iter{i}", "patch": r["patch"], "synth": r["synth"], "rtl": r["rtl"]}
            print_synth(f"new best iter{i}", r["synth"])
        history.append(history_entry(i, r, resp_summary))

    write(out / "patches" / "best.patch", best["patch"])
    write(out / "best.json", json.dumps({"best": best["name"], **synth_row(best["synth"])}, indent=2))
    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    print(f"\nbest: {best['name']} | " + json.dumps(synth_row(best["synth"]), ensure_ascii=False))
    if a.equiv and best is not start:
        # against the start point: with --fragments the tile RTL differs from the
        # baseline by the config itself (address map), which is not an equivalence
        run_equiv(a, out, start["rtl"], best["rtl"], best["name"], start["name"])


# ---------------------------------------------------------------------------
# equivalence: best patch vs baseline
# ---------------------------------------------------------------------------

def rtl_sources(rtl) -> dict:
    """All module texts of a TileRTL, SRAM models included (named bb_*)."""
    return {**rtl.files, **{f"bb_{m}.v": t for m, t in rtl.blackboxes.items()}}


def run_equiv(a, out: Path, base_rtl, best_rtl, label: str, ref: str = "baseline") -> dict:
    d = out / "runs" / "equiv"
    cs = E.find_units(rtl_sources(base_rtl), rtl_sources(best_rtl), N.TOP)
    info = {"label": label, "changed": cs.changed, "added": cs.added, "removed": cs.removed,
            "skipped": cs.skipped,
            "units": [{"top": u.top, "changed_inside": u.changed, "inputs": len(u.inputs),
                       "outputs": len(u.outputs), "clock": u.clock, "reset": u.reset} for u in cs.units]}
    write(d / "units.json", json.dumps(info, indent=2, ensure_ascii=False))
    print(f"\n===== equivalence {label}: module đổi {cs.changed}, thêm {cs.added}, bỏ {cs.removed}; "
          f"đơn vị kiểm: {[u.top for u in cs.units]}", flush=True)
    for u in cs.units:
        for name, text in (("gold.sv", u.gold), ("gate.sv", u.gate), ("tb.sv", E.tb_verilog(u))):
            write(d / u.top / name, text)
    formal_refs = {u.top: N.equiv_formal.chia_remote(u, a.equiv_timeout) for u in cs.units}
    sim_refs = {u.top: N.equiv_sim.chia_remote(u, a.equiv_cycles, a.equiv_seed) for u in cs.units}
    rows = []
    for u in cs.units:
        f, s = get(formal_refs[u.top]), get(sim_refs[u.top])
        write(d / u.top / "formal.json", json.dumps(f, indent=2, default=str))
        write(d / u.top / "sim.json", json.dumps(s, indent=2, default=str))
        c = f.get("counts") or {}
        rows.append({"unit": u.top, "formal": f["status"],
                     "proven": f"{c.get('proven')}/{c.get('total')}", "formal_s": round(f["seconds"]),
                     "sim": s["status"], "sim_cycles": s.get("cycles"), "bad_cycles": s.get("bad_cycles"),
                     "first_mismatch": (s.get("mismatches") or [""])[0]})
    ok = bool(rows) and all(r["sim"] == "pass" for r in rows) and not cs.skipped
    lines = [f"# Equivalence: {label} so với {ref}", "",
             f"- Module đổi: {', '.join(cs.changed) or '(không)'}",
             f"- Module thêm: {', '.join(cs.added) or '(không)'}; bỏ: {', '.join(cs.removed) or '(không)'}",
             f"- Bỏ qua: {cs.skipped or '(không)'}", "",
             "| Đơn vị | Formal | proven/total | Formal (s) | Lockstep | Cycle | Cycle lệch | Lệch đầu tiên |",
             "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['unit']} | {r['formal']} | {r['proven']} | {r['formal_s']} | {r['sim']} | "
              f"{r['sim_cycles']} | {r['bad_cycles']} | {r['first_mismatch']} |" for r in rows]
    lines += ["", f"Kết luận: {'PASS' if ok else 'CHƯA ĐẠT'} (lockstep phải pass mọi đơn vị; "
              "formal `unproven`/`timeout` là chưa chứng minh được, không phải sai)."]
    write(d / "equiv_report.md", "\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return {"ok": ok, "rows": rows}


def run_equiv_only(a, out: Path):
    snap = get(N.tree_snapshot.chia_remote())
    if cache_has("baseline_rtl"):
        base_rtl = get(N.extract_tile_rtl.chia_remote(None, _chia_tag="baseline_rtl"))
    else:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
        art = get(build(a, "equiv-baseline"))
        if not art.success:
            raise SystemExit(f"baseline build failed:\n{build_errors(art)}")
        base_rtl = get(N.extract_tile_rtl.chia_remote(art, _chia_tag="baseline_rtl"))
    patch = Path(a.patch_file).expanduser().read_text()
    write(out / "patches" / "checked.patch", patch)
    get(N.reset_rocket.chia_remote(snap["rocket_head"], patch))
    art = get(build(a, "equiv-patched"))
    save_build(out / "runs" / "equiv", art)
    if not art.success:
        raise SystemExit(f"build với patch lỗi:\n{build_errors(art)}")
    best_rtl = get(N.extract_tile_rtl.chia_remote(art))
    res = run_equiv(a, out, base_rtl, best_rtl, Path(a.patch_file).name)
    print(f"rocket-chip đang ở trạng thái đã áp {a.patch_file}")
    return res


def export_profile(out: Path):
    log = out / "profile" / "ChiaProfileCollector.log"
    if not log.exists():
        return
    # One run spans hours with long quiet gaps (build, synth): keep it in one segment.
    cmd = ["chia", "viz-profile", str(log), "--format", "html", "--output-dir", str(out / "profile"),
           "--gap-threshold", "1000000"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        print(f"profile: {' '.join(cmd)} -> rc={res.returncode}\n{res.stdout[-500:]}{res.stderr[-500:]}")
    except Exception as e:                                           # noqa: BLE001
        print(f"profile export failed ({e}); run by hand: {' '.join(cmd)}")


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    for sub in ("patches", "runs", "profile"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)

    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    start_collector(log_dir=str(out / "profile"))
    cache = start_cache(size=4, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(EXP_YAML))
    if a.flush_cache:
        get(cache.flush.chia_remote())
    bypass = Bypass(yaml_path=str(EXP_YAML))
    for fn in ("extract_tile_rtl", "run_gate", "synth_sta"):
        bypass.set_provider(fn, N.cache_provider)
        bypass.set_cond(fn, N.cache_hit_cond)
    if a.bypass_llm:
        bypass.set_provider("OpenCodeLLM.prompt", N.canned_llm_result)
    try:
        run_equiv_only(a, out) if a.equiv_only else run(a, out)
    finally:
        try:
            stop_collector()
        finally:
            stop_cache()
            export_profile(out)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
