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
  --loop-ab: the loop drives it instead: it picks the target module, the agent only edits
      in short sessions (prompts/improve_timing_target.md), the loop builds and runs the
      A/B, and only an edit that wins the A/B goes on to gate + full synth
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
PROMPT_FIX = HERE / "prompts" / "fix_build.md"
PROMPT_TARGET = HERE / "prompts" / "improve_timing_target.md"
BUDGET_DIR = "/home/ray/rocket_fmax_budget"   # on chipyard: BudgetBashTool logs (--loop-ab)
CSV_FIELDS = ["iter", "accepted", "wns_ns", "tns_ns", "worst_slack_ns", "fmax_mhz", "critical_path",
              "isa_pass", "bench_cycles_match", "reason", "area_um2", "elapsed_min",
              "target", "ab_delta_ns", "ab_sessions"]
SYSTEM_MESSAGE = ("You are a hardware timing engineer working on the Rocket core in Chipyard. "
                  "You only edit Chisel sources through the chipyard_bash tool and never build, "
                  "simulate or synthesize: the surrounding loop does that after you finish.")
SYSTEM_MESSAGE_AB = ("You are a hardware timing engineer working on the Rocket core in Chipyard. "
                     "You edit Chisel sources through the chipyard_bash tool and check each edit with the "
                     "synth_ab tool (elaborate, then synthesize a module next to the current best). You never "
                     "run make, sbt, verilator or yosys yourself: the full build and gate run after you finish.")
SYSTEM_MESSAGE_FIX = ("You are a Chisel engineer working on the Rocket core in Chipyard. The build of an edit "
                      "made earlier in this round failed; you fix the compile errors through the chipyard_bash "
                      "tool and keep the intent of the edit. You never run make, sbt, verilator or yosys "
                      "through chipyard_bash.")


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
    p.add_argument("--builtin-tools", nargs="?", const="on", default="on",
                   choices=["on", "off", "deny", "readonly"],
                   help="opencode's own tools (they run in the opencode container, which has no Chipyard): "
                        "on = all (m3/m4), off = none (free models then fail with 'Free usage limit "
                        "reached'), deny = all but bash/edit/webfetch denied, readonly = read/grep/glob/list/lsp")
    p.add_argument("--opencode-logs", default=None, metavar="LEVEL",
                   help="opencode --log-level (default INFO, always printed); on timeout or provider error "
                        "the stderr tail goes into <prefix>_transcript.txt")
    p.add_argument("--llm-timeout", type=int, default=1800)
    p.add_argument("--build-fixes", type=int, default=2,
                   help="when a round's build fails, let an agent fix the compile errors and rebuild, "
                        "at most this many times (0 = reject the round at once, as before)")
    p.add_argument("--fix-model", default=None, help="model of the build-fix agent (default: --model)")
    p.add_argument("--fix-timeout", type=int, default=900, help="time limit of one build-fix agent call (s)")
    p.add_argument("--quota-wait", type=int, default=900,
                   help="when the provider answers 'Free usage limit reached', wait this long (s) and call "
                        "the agent again (its edits so far stay in the tree)")
    p.add_argument("--quota-retries", type=int, default=3, help="at most this many such waits per agent call")
    p.add_argument("--ab-tool", action="store_true",
                   help="give the agent the synth_ab tool (ab_tool.py: make verilog + Yosys/OpenSTA "
                        "A/B of one module) and use prompts/improve_timing_ab.md")
    p.add_argument("--loop-ab", action="store_true",
                   help="loop-driven rounds: the loop picks the target module from the worst path, the "
                        "agent only edits it in short sessions (prompts/improve_timing_target.md), the loop "
                        "builds and synthesizes that module A/B against the best; a losing A/B goes back to "
                        "the agent (--ab-refines), a winning one goes on to gate + full synth")
    p.add_argument("--edit-timeout", type=int, default=900,
                   help="--loop-ab: time limit of one edit session (s); --llm-timeout is not used")
    p.add_argument("--edit-budget", type=int, nargs=3, default=[15, 25, 35], metavar=("SOFT", "READONLY", "HARD"),
                   help="--loop-ab: tool calls of an edit session with no edit yet: after SOFT every answer "
                        "carries a budget note, after READONLY a command that changes no file gets no output, "
                        "after HARD nothing runs and the agent is told to answer with its plan")
    p.add_argument("--edit-steps", type=int, default=45,
                   help="--loop-ab: opencode `steps` of an edit session (then a text-only answer; 0 = no limit)")
    p.add_argument("--ab-refines", type=int, default=2,
                   help="--loop-ab: extra edit sessions after a losing A/B (0 = one session per round)")
    p.add_argument("--ab-min-gain", type=float, default=0.1,
                   help="--loop-ab: the A/B must improve the module's worst slack by more than this (ns; "
                        "synthesis noise is about 0.1 ns) before the round goes on to gate + full synth")
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
    if a.loop_ab and (a.ab_tool or a.bypass_llm):
        p.error("--loop-ab không dùng chung với --ab-tool hay --bypass-llm")
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
    # An elaboration exception (lab-t4: IndexOutOfBoundsException) names its Scala line only in
    # the stack frames: keep those of rocket-chip/chipyard code, at most 12 after each exception.
    lines, frames = [], 0
    for l in text.splitlines():
        if re.search(r"\[error\]|error:|%Error|Exception|FAILED", l):
            lines.append(l)
            frames = 12
        elif frames and re.search(r"\bat (freechips|chipyard|testchipip)\.\S*\(\w+\.scala:\d+\)", l):
            lines.append(l)
            frames -= 1
    return "\n".join(lines[:120]) if lines else "\n".join(text.splitlines()[-60:])


def save_build(d: Path, art, name: str = "build.log"):
    write(d / name, f"success={art.success} returncode={art.returncode}\n"
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


QUOTA_ERROR = "Free usage limit reached"


def call_agent(a, llm, prompt: str, tag: str, d: Path, prefix: str, best: dict, ab: bool,
               budget_log: str | None = None) -> str:
    """_call_agent_once, again after --quota-wait s when the provider said its free usage limit
    was reached (lab-t4: both build-fix calls after a 45-step edit session), at most
    --quota-retries times. The edits of the cut-off call stay in the tree; its files are kept
    as <prefix>_quota<n>_*."""
    for n in range(a.quota_retries + 1):
        reason = _call_agent_once(a, llm, prompt, tag if n == 0 else f"{tag}_q{n}", d, prefix, best, ab,
                                  budget_log)
        tr = d / f"{prefix}_transcript.txt"
        if not reason or n == a.quota_retries or QUOTA_ERROR not in (tr.read_text(errors="replace")
                                                                     if tr.exists() else ""):
            return reason
        for f in d.glob(f"{prefix}_*"):
            if not f.name.startswith(f"{prefix}_quota") and f.name != f"{prefix}_prompt.md":
                f.rename(d / f.name.replace(f"{prefix}_", f"{prefix}_quota{n + 1}_", 1))
        print(f"{prefix}: provider báo '{QUOTA_ERROR}', chờ {a.quota_wait} s rồi gọi lại "
              f"(lần {n + 1}/{a.quota_retries})", flush=True)
        time.sleep(a.quota_wait)
    return reason


def _call_agent_once(a, llm, prompt: str, tag: str, d: Path, prefix: str, best: dict, ab: bool,
                     budget_log: str | None = None) -> str:
    """One agent call with chipyard_bash (and synth_ab with `ab`); writes
    <prefix>_response.md / <prefix>_transcript.txt into d. Returns "" or why it failed.
    With `budget_log`, chipyard_bash is a BudgetBashTool (--edit-budget) logging there;
    the log is copied to <prefix>_budget.jsonl."""
    reason = ""
    get(N.read_text_opencode.chia_remote(N.LLM_PARTIAL_FILE, True))   # stale from an earlier call
    # With synth_ab the two tools share the single "chipyard" slot, so nothing
    # else runs on chipyard (build, git) until both are stopped.
    slot = 0.5 if ab else 1
    if budget_log:
        tools = [N.BudgetBashTool("chipyard_bash", N.CHIPYARD, *a.edit_budget, budget_log, timeout_seconds=120,
                                  task_options={"resources": {"chipyard": slot}})]
    else:
        tools = [BashTool("chipyard_bash", N.CHIPYARD, timeout_seconds=120,
                          task_options={"resources": {"chipyard": slot}})]
    resp = None
    try:
        if ab:
            config, config_text = loop_config_class(a)
            tools.append(AB.SynthABTool("synth_ab", best["rtl"], best["synth"], best["name"], config,
                                        config_text, a.period_ns, a.synth_flow, a.make_jobs,
                                        task_options={"resources": {"chipyard": slot}}))
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=tools, _chia_tag=tag))
    except Exception as e:                                           # noqa: BLE001
        write(d / f"{prefix}_error.txt", traceback.format_exc())
        reason = f"LLM lỗi: {type(e).__name__}: {e}"[:500]
    finally:
        for t in tools:
            t.stop()              # free the chipyard slot before the build
    if budget_log:
        write(d / f"{prefix}_budget.jsonl", get(N.read_text.chia_remote(budget_log)) or "")
    if ab:
        write(d / f"{prefix}_ab_cleanup.txt", get(N.ab_cleanup.chia_remote()))
        write(d / f"{prefix}_ab_experiments.jsonl", get(N.read_text.chia_remote(AB.LOG_FILE)) or "")
    if resp is not None:
        write(d / f"{prefix}_response.md", resp.result)
        write(d / f"{prefix}_transcript.txt", resp.stream_result)
        if not resp.success:
            reason = reason or f"LLM không thành công (returncode {resp.returncode})"
            partial = get(N.read_text_opencode.chia_remote(N.LLM_PARTIAL_FILE, True))
            if partial:
                write(d / f"{prefix}_transcript.txt", partial)
    return reason


def collect_and_check(out: Path, d: Path, i: int, snap: dict, best: dict) -> tuple[dict, str]:
    """Diff of rocket-chip after an agent call, outside edits restored, scope checked.
    Returns (diff, "" or why the round stops)."""
    diff = get(N.collect_diff.chia_remote(snap["rocket_head"], snap["outside"]))
    write(out / "patches" / f"iter{i}.patch", diff["patch"])
    write(d / "diff_files.txt", "\n".join(diff["files"]))
    if diff["outside_changed"]:
        rest = get(N.restore_outside.chia_remote(snap["outside"]))
        write(d / "outside_restore.json", json.dumps(rest, indent=2))
        if not rest["ok"]:
            raise SystemExit(f"agent sửa ngoài rocket-chip và không khôi phục được: {rest['remaining']}\n"
                             "tạo lại container chipyard (docker rm -f ... && chia up --add) rồi chạy lại")
    violations = N.scope_violations(diff["files"], diff["outside_changed"])
    if violations:
        return diff, "vi phạm phạm vi: " + "; ".join(violations)
    if diff["patch"] == best["patch"]:
        return diff, f"agent không thay đổi gì so với {best['name']}"
    return diff, ""


def render_fix_prompt(a, i: int, k: int, errors: str, patch: str, best: dict, notes: str) -> str:
    ab = ("\n\nYou also have the `synth_ab` tool: `synth_ab_rebuild_verilog()` elaborates your current "
          "sources (1-3 min; call it again while it says `running`) and returns the compile errors, so use it "
          "to confirm the fix compiles before you finish. Do not start any synthesis.") if a.ab_tool else ""
    return string.Template(PROMPT_FIX.read_text()).safe_substitute(
        ITER=i, FIX=k, MAX_FIX=a.build_fixes, CONFIG_DESC=loop_config(a), BEST_NAME=best["name"],
        ERRORS=errors[-12000:], PATCH=patch[-30000:], STAGE_DIR=N.STAGE_DIR, AB_NOTE=ab,
        NOTES=notes[-4000:].strip() or "(no summary)")


def after_agent(out: Path, d: Path, i: int, snap: dict, best: dict, r: dict, agent_reason: str,
                label: str = "") -> dict | None:
    """Diff after an agent call. An agent that timed out or failed still leaves its
    edits in the tree: they go on to the build like any other (the gate keeps them
    honest) and the failure is kept as a note. Returns the diff, or None with
    r["reason"] set when the round stops (scope violation, nothing changed)."""
    diff, why = collect_and_check(out, d, i, snap, best)
    r["patch"] = diff["patch"]
    if why:
        r["reason"] = "; ".join(x for x in (label, agent_reason, why) if x)
        return None
    if agent_reason:
        r.setdefault("notes", []).append(f"{label or 'agent'}: {agent_reason}, vẫn đánh giá phần đã sửa")
        print(f"round {i}: {agent_reason}; agent đã sửa {len(diff['files'])} file, vẫn build và đánh giá",
              flush=True)
    return diff


def build_with_fixes(a, i: int, out: Path, d: Path, fix_llm, snap: dict, best: dict, r: dict, diff: dict,
                     suffix: str = "") -> tuple:
    """Build of the round's edits. On a compile error the build-fix agent (as
    build_with_debug_retry in CHIA's timing_opt example) fixes it, at most
    --build-fixes times, with the same scope check and diff. Returns (artifact,
    diff); the artifact is None (r["reason"] set) when the round stops."""
    t_build = time.time()
    art = build_loop(a, f"iter{i}{suffix}")       # values only, see ensure_baseline
    save_build(d, art, f"build{suffix}.log")
    k = 0
    while not art.success and not a.bypass_llm and k < a.build_fixes:
        k += 1
        r["build_fixes"] = r.get("build_fixes", 0) + 1
        tag = f"{suffix.lstrip('_')}fix{k}"
        errors = build_errors(art)
        print(f"round {i}: build lỗi, gọi agent sửa (lần {k}/{a.build_fixes})", flush=True)
        fprompt = render_fix_prompt(a, i, k, errors, diff["patch"], best, last_response(d))
        write(d / f"{tag}_prompt.md", fprompt)
        reason = call_agent(a, fix_llm, fprompt, f"agent_{a.run_name}_iter{i}_{tag}_llm", d, tag,
                            best, a.ab_tool)
        diff = after_agent(out, d, i, snap, best, r, reason, f"build lỗi, agent sửa lần {k}")
        if diff is None:
            return None, None
        art = build_loop(a, f"iter{i}_{tag}")
        save_build(d, art, f"build_{tag}.log")
    r["build_min"] = r.get("build_min", 0) + (time.time() - t_build) / 60
    if not art.success:
        r["reason"] = (f"build lỗi (sau {k} lần agent sửa):\n" if k else "build lỗi:\n") + build_errors(art)
        return None, diff
    return art, diff


def tile_rtl(art, r: dict):
    try:
        return get(N.extract_tile_rtl.chia_remote(art))
    except Exception as e:                                           # noqa: BLE001
        r["reason"] = f"không lấy được Verilog {N.TOP}: {e}"[:1000]
        return None


def gate_and_judge(a, d: Path, art, rtl, elfs, base: dict, best: dict, r: dict, synth=None) -> dict:
    """Gate and full synth of a built round (in parallel), then the decision. `synth`:
    a full RocketTile result already in hand (a whole-tile A/B), not synthesized again."""
    r["rtl"] = rtl
    gate_ref = N.run_gate.chia_remote(art, elfs)
    synth_ref = None if synth is not None else N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow)
    gate = get(gate_ref)
    save_gate(d, gate)
    isa_ok, cyc_ok, why = compare_gate(base["gate"], gate, a.exclude_isa)
    r["isa_pass"], r["bench_cycles_match"] = isa_ok, cyc_ok
    write(d / "gate_compare.txt", "\n".join(why) or "khớp baseline")
    if not (isa_ok and cyc_ok):
        if synth_ref is not None:
            chia_cancel(synth_ref, force=True)
        r["reason"] = "gate không đạt: " + "; ".join(why)
        return r

    synth = synth if synth is not None else get(synth_ref)
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


def new_round(i: int) -> dict:
    return {"accepted": False, "reason": "", "synth": None, "rtl": None, "patch": "",
            "isa_pass": "", "bench_cycles_match": "", "build_fixes": 0}


def run_round(a, i: int, out: Path, llm, fix_llm, elfs, snap, base, best, history) -> dict:
    """One round (one agent call). Returns {"accepted", "reason", "synth", "rtl", "patch", ...}."""
    d = out / "runs" / f"iter{i}"
    r = new_round(i)

    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    get(N.stage_for_agent.chia_remote(stage_files(best, history)))
    prompt = render_prompt(a, i, best, history)
    write(d / "prompt.md", prompt)

    tag = f"m2_iter{i}_llm" if a.bypass_llm else f"agent_{a.run_name}_iter{i}_llm"
    t_agent = time.time()
    if a.bypass_llm:
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=[], _chia_tag=tag))
        patch_in = Path(a.patch_file).read_text() if a.patch_file else None
        get(N.apply_edit.chia_remote(patch_in, a.marker_file, f"rocket-fmax milestone-2 bypass, iter{i}"))
        write(d / "agent_response.md", resp.result)
        reason = "" if resp.success else f"LLM không thành công (returncode {resp.returncode})"
    else:
        reason = call_agent(a, llm, prompt, tag, d, "agent", best, a.ab_tool)
    r["agent_min"] = (time.time() - t_agent) / 60

    diff = after_agent(out, d, i, snap, best, r, reason)
    if diff is None:
        return r
    art, diff = build_with_fixes(a, i, out, d, fix_llm, snap, best, r, diff)
    if art is None:
        return r
    rtl = tile_rtl(art, r)
    return r if rtl is None else gate_and_judge(a, d, art, rtl, elfs, base, best, r)


# ---------------------------------------------------------------------------
# --loop-ab rounds: the loop picks the target and runs the A/B, the agent edits
# ---------------------------------------------------------------------------

def target_text(target: dict, classes: dict) -> str:
    lines = [f"- Worst path of the current best: `{target['crit']}`",
             f"- It starts in instance `{target['start_path'] or N.TOP}` and ends in "
             f"`{target['end_path'] or N.TOP}`.",
             f"- **Target: instance `{target['path'] or N.TOP}` = module `{target['module']}`** "
             "(the smallest instance holding both ends).",
             "- Instances from the top down (instance -> Verilog module -> Scala files that define it):"]
    for path, mod in target["chain"]:
        files = classes.get(mod) or []
        where = (", ".join(f"`{f}`" for f in files) if files else
                 "not found under rocket-chip/src/main/scala (e.g. hardfloat, which you must not edit: "
                 "change how the parent instantiates or feeds it instead)")
        lines.append(f"  - `{path or N.TOP}` -> `{mod}` -> {where}")
    lines.append("- Work on the logic between those two ends. If it cannot be shortened inside the target, "
                 "you may move logic in the module that instantiates it.")
    if target.get("report"):
        lines += ["", "Worst path report (OpenSTA, from timing_paths.rpt):", "", "```", target["report"], "```"]
    ends = target.get("ends") or []
    if ends:
        lines += ["", "Registers at the two ends and the Scala lines the Verilog `@[...]` comments point to:"]
        for e in ends:
            locs = ", ".join(f"`{f}:{n}`" for f, n in e["locs"]) or "no `@[...]` location found"
            lines.append(f"- {e['end']}: `{e['reg']}` in `{e['inst'] or N.TOP}` (`{e['module']}`): {locs}")
    if target.get("sources"):
        lines += ["", "The Chisel around them, from the tree as it is now (`>` = a line named above, `+` = a line "
                  f"added by the current best's patch; the numbers are the file's line numbers). Start from this "
                  "code; read more only where it is not enough:", "", target["sources"]]
    return "\n".join(lines)


def first_path(paths: str, limit: int = 8000) -> str:
    """The first `Startpoint ... slack` block of an OpenSTA report_checks output."""
    i = paths.find("Startpoint")
    if i < 0:
        return ""
    j = paths.find("slack", i)
    j = paths.find("\n", j) if j >= 0 else -1
    block = paths[i:j if j >= 0 else len(paths)]
    return block if len(block) <= limit else block[:limit] + "\n... (truncated, see timing_paths.rpt)"


def render_target_prompt(a, i: int, k: int, best: dict, history: list[str], target: dict, classes: dict,
                         note: str) -> str:
    s = best["synth"]
    return string.Template(PROMPT_TARGET.read_text()).safe_substitute(
        ITER=i, MAX_ITERS=a.iters, SESSION=k + 1, MAX_SESSIONS=a.ab_refines + 1,
        EDIT_MIN=round(a.edit_timeout / 60), AB_MIN_GAIN=a.ab_min_gain,
        CONFIG_DESC=loop_config(a), PERIOD_NS=a.period_ns, BEST_NAME=best["name"],
        BEST_SLACK=fmt(s.worst_slack_ns, 4), BEST_FMAX=fmt(s.fmax_mhz, 2),
        CRIT=f"{s.crit_start} -> {s.crit_end}", ENDPOINTS=s.families, STAGE_DIR=N.STAGE_DIR,
        TARGET=target_text(target, classes), SESSION_NOTE=note, BUDGET_SOFT=a.edit_budget[0],
        BUDGET_READONLY=a.edit_budget[1], BUDGET_HARD=a.edit_budget[2],
        HISTORY="\n\n".join(history) or "(chưa có vòng nào trước)")


def budget_commands(d: Path, prefix: str, limit: int = 60) -> list[str]:
    """The commands of an edit session, from <prefix>_budget.jsonl (BudgetBashTool)."""
    f = d / f"{prefix}_budget.jsonl"
    rows = []
    for line in (f.read_text().splitlines() if f.exists() else []):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        cmd = " ".join(e.get("cmd", "").split())
        rows.append(f"{e.get('n')}. {cmd[:200]}" + (f"  [{e['action']}]" if e.get("action") else ""))
    return rows[:limit]


def budget_attempt(d: Path, prefix: str) -> str:
    """The last command of an edit session that tried to write a Scala file (in full), with
    what came of it ("stop": not run, "edit_failed": changed nothing)."""
    f = d / f"{prefix}_budget.jsonl"
    last = None
    for line in (f.read_text().splitlines() if f.exists() else []):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("attempt") or (e.get("action") == "stop" and N.edit_command(e.get("cmd", ""))):
            last = e
    if not last:
        return ""
    how = {"stop": "was not run (the session was over)",
           "edit_failed": "ran but changed nothing (its old text did not match)"}.get(last.get("action"),
                                                                                       "ran")
    return f"Command {last.get('n')} {how}:\n\n```\n{last.get('cmd', '').strip()}\n```"


def noedit_note(k: int, cmds: list[str], response: str, ab_note: str, attempt: str = "") -> str:
    """Note of a session that follows one which made no edit."""
    lines = [f"## This session: make the edit (session {k + 1})", "",
             f"Session {k} read the code but made **no edit**, and was stopped. Do not read the same things "
             "again: what it ran is below, and the target's code is quoted above. Your first or second command "
             "should be the edit."]
    if cmds:
        lines += ["", "Commands of that session:", "", "```", *cmds, "```"]
    if attempt:
        lines += ["", "Its last attempt at the edit. Check the old text against the file as it is now, "
                  "fix it, and run it as your first command:", "", attempt]
    if response.strip():
        lines += ["", "Its last answer (its plan, if it gave one):", "", "```", response.strip()[-3000:], "```"]
    if ab_note:      # an earlier session's edit is still in the tree, with its losing A/B
        lines += ["", "Before that:", "", ab_note.split("\n", 1)[1].strip()]
    return "\n".join(lines)


def session_note(k: int, ab_text: str, ab_dir: str, rdiff: str) -> str:
    if k == 0:
        return ("## This session\n\nFirst session of the round: the tree holds the current best, "
                "with no edit of this round yet.")
    return "\n".join([
        f"## This session: improve your edit (session {k + 1})",
        "",
        "Your edit from the previous session is still in the tree. It built, but its A/B was not good enough:",
        "", "```", ab_text, "```", "",
        f"- `{ab_dir}/child_paths.rpt`, `{ab_dir}/parent_paths.rpt`: worst paths of the module with your "
        "edit (child) and in the best (parent); `child_families.txt` the endpoint families.",
        f"- `{ab_dir}/child_verilog.sv`: the module's Verilog generated from your edit.",
        f"- `{ab_dir}/round.diff`: your edits of this round (undo them all with "
        f"`git -C {N.ROCKET} apply -R {ab_dir}/round.diff`).",
        "",
        "Read where the child's worst path goes now. Then improve the edit, or undo it and try a different "
        "restructuring of the same target.",
        "", "Your edits of this round so far:", "", "```diff", rdiff[-8000:], "```"])


def run_round_loop_ab(a, i: int, out: Path, llm, fix_llm, elfs, snap, base, best, history) -> dict:
    """One --loop-ab round: the loop picks the target from the best's worst path; an
    agent session edits; the loop builds (build-fix agent on a compile error) and
    synthesizes the smallest instance holding the target and all changed modules,
    from the edit and from the best. A win > --ab-min-gain goes on to gate + full
    synth; otherwise the A/B report goes to a new session (at most --ab-refines)."""
    d = out / "runs" / f"iter{i}"
    r = new_round(i)
    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    get(N.stage_for_agent.chia_remote(stage_files(best, history)))

    target = N.pick_target(best["rtl"], best["synth"])
    target["report"] = first_path(best["synth"].paths)
    target["ends"], target["sources"] = N.path_sources(best["rtl"], best["synth"], best["patch"])
    classes = get(N.find_scala_classes.chia_remote([m for _, m in target["chain"]]))
    r["target"] = f"{target['path'] or N.TOP} ({target['module']})"
    write(d / "target.json", json.dumps({**target, "scala": classes}, indent=2))
    print(f"round {i}: mục tiêu {r['target']}, đường găng {target['crit']}", flush=True)

    parent_cache: dict = {}
    note, last = session_note(0, "", "", ""), "chưa có A/B"
    t_agent, noedit, ab_note = 0.0, 0, ""
    for k in range(a.ab_refines + 1):
        r["ab_sessions"] = k + 1
        prompt = render_target_prompt(a, i, k, best, history, target, classes, note)
        write(d / f"edit{k}_prompt.md", prompt)
        t0 = time.time()
        reason = call_agent(a, llm, prompt, f"agent_{a.run_name}_iter{i}_edit{k}_llm", d, f"edit{k}",
                            best, False, budget_log=f"{BUDGET_DIR}/{a.run_name}_iter{i}_edit{k}.jsonl")
        t_agent += time.time() - t0
        r["agent_min"] = t_agent / 60
        before = r["patch"] if r["patch"] else best["patch"]
        diff = after_agent(out, d, i, snap, best, r, reason, f"phiên sửa {k + 1}")
        violation = diff is None and "vi phạm phạm vi" in r["reason"]
        if r["patch"] == before and not violation:
            # no edit in this session: the next one starts from its commands and answer
            noedit += 1
            r["noedit_sessions"] = noedit
            why = f"phiên sửa {k + 1} không sửa gì" + (f" ({reason})" if reason else "")
            print(f"round {i}: {why}", flush=True)
            if k < a.ab_refines:
                resp = d / f"edit{k}_response.md"
                note = noedit_note(k + 1, budget_commands(d, f"edit{k}"),
                                   resp.read_text() if resp.exists() else "", ab_note,
                                   budget_attempt(d, f"edit{k}"))
                r["reason"] = ""
                continue
            r["reason"] = f"{why}; hết {a.ab_refines + 1} phiên" + (f"; A/B trước đó: {last}" if k else "")
            return r
        if diff is None:
            if k:
                r["reason"] += f" (A/B trước đó: {last})"
            return r
        art, diff = build_with_fixes(a, i, out, d, fix_llm, snap, best, r, diff, f"_e{k}")
        if art is None:
            return r
        rtl = tile_rtl(art, r)
        if rtl is None:
            return r
        rdiff = get(N.round_diff.chia_remote(snap["rocket_head"], best["patch"]))
        r["round_diff"] = rdiff
        write(d / f"edit{k}_round.diff", rdiff)

        ch = AB.changed_modules(AB.module_texts(best["rtl"]), AB.module_texts(rtl))
        mods = set(ch["changed"]) | set(ch["added"])
        ab_dir = f"{N.STAGE_DIR}/ab{k}"
        if not mods:
            last = f"Verilog của {N.TOP} không đổi"
            ab_text = ("Your edit did not change the generated Verilog of RocketTile at all "
                       "(only comments, dead code, or code that elaborates to the same logic).")
            stage = {"round.diff": rdiff}
        else:
            path = N.common_path([target["path"]] + N.changed_paths(rtl, mods))
            ab = AB.loop_ab(best["rtl"], best["synth"], best["name"], rtl, path, a.period_ns, a.synth_flow,
                            parent_cache)
            write(d / f"ab{k}.txt", ab["text"])
            print(f"round {i}, phiên {k + 1}: {ab['text']}", flush=True)
            r["ab_delta_ns"] = "" if ab["delta"] is None else ab["delta"]
            last = (f"{ab['module']}: delta {ab['delta']:+.3f} ns" if ab["delta"] is not None
                    else f"{ab['module']}: synth lỗi ({ab['c'].get('error') or ab['p'].get('error')})")
            if ab["delta"] is not None and ab["delta"] > a.ab_min_gain:
                r["ab_result"] = last
                return gate_and_judge(a, d, art, rtl, elfs, base, best, r,
                                      synth=ab["child"] if ab["whole"] else None)
            ab_text = ab["text"]
            stage = {**ab["files"], "round.diff": rdiff,
                     "child_verilog.sv": AB.module_texts(rtl).get(ab["module"], "")}
        if k < a.ab_refines:
            get(N.stage_for_agent.chia_remote(stage, ab_dir))
            note = ab_note = session_note(k + 1, ab_text, ab_dir, rdiff)
    r["reason"] = f"A/B không tốt hơn {a.ab_min_gain} ns sau {a.ab_refines + 1} phiên sửa (lần cuối: {last})"
    return r


def last_response(d: Path) -> str:
    """Summary of the round's last agent answer (agent_response.md or editN_response.md)."""
    files = sorted(d.glob("edit*_response.md"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    files = files or [d / "agent_response.md"]
    return files[-1].read_text()[-1500:] if files[-1].exists() else ""


def history_entry(i: int, r: dict, resp_summary: str) -> str:
    s = r.get("synth")
    lines = [f"### Vòng {i}: {'NHẬN' if r['accepted'] else 'LOẠI'}",
             f"Lý do: {r['reason'][:3000]}"]
    if r.get("target"):
        lines.append(f"Mục tiêu: {r['target']}; số phiên sửa: {r.get('ab_sessions', '')}"
                     + (f"; A/B: {r['ab_result']}" if r.get("ab_result") else ""))
    if s is not None and s.success:
        lines.append(f"Kết quả: worst slack {s.worst_slack_ns:.4f} ns, đường găng {s.crit_start} -> {s.crit_end}")
    if r.get("build_fixes"):
        lines.append(f"Build lỗi, agent sửa lỗi build {r['build_fixes']} lần.")
    if resp_summary:
        lines.append(f"Tóm tắt của agent: {resp_summary[:800]}")
    patch = r.get("round_diff") or r["patch"]
    if not r["accepted"] and patch:
        lines.append("Patch đã bị loại (đã revert, đừng lặp lại y nguyên):\n```diff\n" + patch[:4000] + "\n```")
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

    oc_args = ["--print-logs", "--log-level", a.opencode_logs] if a.opencode_logs else None
    llm = N.SalvageOpenCodeLLM(model=a.model, system_message=SYSTEM_MESSAGE_AB if a.ab_tool else SYSTEM_MESSAGE,
                               timeout_seconds=a.edit_timeout if a.loop_ab else a.llm_timeout, retries=1,
                               builtin_tools=a.builtin_tools, extra_cli_args=oc_args,
                               max_steps=(a.edit_steps or None) if a.loop_ab else None)
    fix_llm = N.SalvageOpenCodeLLM(model=a.fix_model or a.model, system_message=SYSTEM_MESSAGE_FIX,
                                   timeout_seconds=a.fix_timeout, retries=1, builtin_tools=a.builtin_tools,
                                   extra_cli_args=oc_args)
    best = {"name": "baseline", "patch": "", "synth": base["synth"], "rtl": base["rtl"]}
    if a.start_patch or a.fragments:
        best = ensure_start(a, out, snap, base)
    start = best
    history: list[str] = []
    for i in range(1, a.iters + 1):
        t0 = time.time()
        print(f"\n######## round {i}/{a.iters}", flush=True)
        try:
            rnd = run_round_loop_ab if a.loop_ab else run_round
            r = rnd(a, i, out, llm, fix_llm, elfs, snap, base, best, history)
        except SystemExit:
            raise
        except Exception as e:                                       # noqa: BLE001
            write(out / "runs" / f"iter{i}" / "error.txt", traceback.format_exc())
            r = {"accepted": False, "reason": f"lỗi hạ tầng: {type(e).__name__}: {e}"[:1000],
                 "synth": None, "patch": "", "isa_pass": "", "bench_cycles_match": ""}
        r["minutes"] = (time.time() - t0) / 60
        if r.get("notes"):
            r["reason"] = r["reason"] + " [" + "; ".join(r["notes"]) + "]"
        resp_summary = last_response(out / "runs" / f"iter{i}")
        write(out / "runs" / f"iter{i}" / "decision.json",
              json.dumps({k: v for k, v in r.items() if k not in ("synth", "rtl", "patch", "round_diff")},
                         indent=2, ensure_ascii=False, default=str))
        append_row(csv_path, {"iter": i, "accepted": r["accepted"], **synth_row(r.get("synth")),
                              "isa_pass": r["isa_pass"], "bench_cycles_match": r["bench_cycles_match"],
                              "reason": r["reason"].replace("\n", " | ")[:1000],
                              "elapsed_min": f"{r['minutes']:.1f}", "target": r.get("target", ""),
                              "ab_delta_ns": r.get("ab_delta_ns", ""), "ab_sessions": r.get("ab_sessions", "")})
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
