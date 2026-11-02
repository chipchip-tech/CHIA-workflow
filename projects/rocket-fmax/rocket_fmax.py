"""Rocket Fmax loop on CHIA: raise the Fmax of RocketTile measured with Yosys + OpenSTA on
sky130_fd_sc_hd (the synthesis half of the ASIC milestone), cycle cost at most +3 % over the baseline.

One round, as CHIA's timing_opt example:
  reset rocket-chip to the best patch -> one Claude Code session edits the Chisel through
  chipyard_bash and checks its edits with synth_ab (ab_tool.py: Yosys + OpenSTA A/B; pipeline
  stages allowed) -> scope check -> build RocketConfig (a build-fix agent on a compile error) ->
  Yosys + OpenSTA of RocketTile (synth_sta, 3-4 min): a round that cannot beat the best even
  without any cycle cost stops here -> gate (riscv-tests ISA + benchmarks on Verilator) ->
  accepted when every test passes with the baseline's instruction counts, the cycle cost (geomean
  mcycle / baseline mcycle) stays within --max-ipc-loss and Fmax / cycle cost beats the best by
  more than --min-perf-gain. A functional failure goes to a debug agent (--debug-retries).

Run from this directory on the head (README.md):
  chia job submit --working-dir . -- python rocket_fmax.py --start-patch <best.patch> --run-name <name>
  --baseline-only: no rounds, synth of the baseline (and of --start-patch)

Outputs go to --out-dir/<run-name>/ (results.csv, history.md, patches/, runs/, profile/).
Needs the workers chipyard, verilator_run, claude_creds and eda of cluster.yaml.
"""

import argparse
import csv
import dataclasses
import hashlib
import json
import math
import os
import re
import string
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ray

import ab_tool as AB
import fmax_nodes as N
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.base.tools.BashTool import BashTool
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
from chia.trace.profiler import start_collector, stop_collector

HERE = Path(__file__).resolve().parent
EXP_YAML = HERE / "exp.yaml"
PROMPT = HERE / "prompts" / "improve_timing.md"
PROMPT_FIX = HERE / "prompts" / "fix_build.md"
PROMPT_DEBUG = HERE / "prompts" / "fix_failure.md"

# Cache tags (the cache is keyed by tag only, see exp.yaml):
#   rtl2: TileRTL with sram_models
#   synth_<flow>_p<period>: synth_sta (baseline_synth_v2_p10 is the one of chia-fmax-r1..r3b)
RTL_TAG = "rtl2"

CSV_FIELDS = ["iter", "accepted", "fmax_mhz", "slack_ns", "tns_ns", "area_um2", "critical_path", "cycle_pct", "perf",
              "perf_gain_pct", "isa_pass", "reason", "elapsed_min", "synth_min"]

SYSTEM_MESSAGE = ("You are a hardware timing engineer working on the Rocket core in Chipyard, targeting an ASIC "
                  "standard-cell library (sky130_fd_sc_hd). You raise frequency x IPC: you edit Chisel sources "
                  "through the chipyard_bash tool (pipeline stages allowed, with every consumer updated) and check "
                  "each edit with the synth_ab tool. You never run make, sbt, verilator or yosys yourself: the full "
                  "build, synthesis and tests run after you finish.")
SYSTEM_MESSAGE_DEBUG = ("You are a Chisel engineer working on the Rocket core in Chipyard. An edit made earlier in this "
                        "round builds but fails functional tests; you find and fix the bug through the chipyard_bash "
                        "tool and keep the intent of the edit. You never run make, sbt, verilator or yosys.")
SYSTEM_MESSAGE_FIX = ("You are a Chisel engineer working on the Rocket core in Chipyard. The build of an edit "
                      "made earlier in this round failed; you fix the compile errors through the chipyard_bash "
                      "tool and keep the intent of the edit. You never run make, sbt, verilator or yosys "
                      "through chipyard_bash.")
EMPTY_RETRY = """

## The previous session(s) of this round left no edit

{n} session(s) on this round finished with the tree back at the current best: the edits they
measured with synth_ab were undone. What they measured (delta = child - parent, positive = better):

{tried}

The last session's answer:

{answer}

Do **not** make these edits again, nor the same idea on the same logic: the numbers above already
answer it. The located worst path above is only a starting point. Pick a different endpoint family
from `timing_families.txt` (they sit close to the worst one), or one edit that shortens several
families at once, or a pipeline stage. Try at least two concrete edits, each checked with synth_ab
ending with the whole `RocketTile`. A pipeline stage is allowed: the loop measures its cycle cost on
the benchmarks and rejects the round if that cost is too high.
"""


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--period-ns", type=float, default=10.0,
                   help="clock period of Yosys (abc -D) and OpenSTA; keep it at or below the target (a wider "
                        "period lets ABC stop early and the measured Fmax drops)")
    p.add_argument("--synth-flow", choices=sorted(N.SYNTH_FLOWS), default="v2")
    p.add_argument("--baseline-only", action="store_true",
                   help="no rounds: synth of the baseline (and of --start-patch)")
    p.add_argument("--start-patch", default=None,
                   help="start the rounds from this patch (best.patch of an earlier run) instead of the "
                        "unmodified rocket-chip")
    p.add_argument("--history-from", nargs="*", default=[], metavar="RUN_DIR",
                   help="earlier runs (--out-dir/<run>) whose history.md the agent gets as earlier rounds")
    p.add_argument("--iters", type=int, default=5)
    p.add_argument("--run-name", default=None)
    p.add_argument("--out-dir", default="~/rocket-fmax-v2")
    p.add_argument("--cache-dir", default="~/chia_cache")
    p.add_argument("--flush-cache", action="store_true")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3600)
    p.add_argument("--extra-bench", nargs="*", default=["mm", "spmv"],
                   help="riscv-tests benchmarks of the gate next to " + ", ".join(N.BENCHMARKS))
    p.add_argument("--exclude-isa", nargs="*", default=["rv64ui-p-ma_data"],
                   help="ISA tests left out of the gate (rv64ui-p-ma_data already fails on the baseline)")
    p.add_argument("--repeat-seed", type=int, default=7, help="baseline: rerun the benchmarks with this seed")
    p.add_argument("--model", default="sonnet", help="Claude Code model of the agent")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--synth-timeout", type=int, default=900,
                   help="time limit of one Yosys run of the tile (s); normal is 3-5 min, a run past this "
                        "is killed and the round rejected")
    p.add_argument("--llm-timeout", type=int, default=1800, help="time limit of one agent session (s)")
    p.add_argument("--build-fixes", type=int, default=2,
                   help="when a round's build fails, let an agent fix the compile errors and rebuild, "
                        "at most this many times")
    p.add_argument("--fix-model", default=None, help="model of the build-fix and debug agents (default: --model)")
    p.add_argument("--fix-timeout", type=int, default=900, help="time limit of one build-fix/debug session (s)")
    p.add_argument("--quota-wait", type=int, default=900,
                   help="on a usage limit without a reset time, wait this long (s) and call the agent again")
    p.add_argument("--quota-retries", type=int, default=3, help="at most this many such waits per agent call")
    p.add_argument("--quota-max-wait", type=int, default=6 * 3600,
                   help="on 'You've hit your limit', wait until the reset time it gives (+2 min), unless that is "
                        "further away than this (s; weekly limit), then the call fails")
    p.add_argument("--max-ipc-loss", type=float, default=0.03,
                   help="cycle cost (geomean of mcycle / baseline mcycle) may be at most 1 + this")
    p.add_argument("--min-perf-gain", type=float, default=0.01,
                   help="Fmax / cycle cost must beat the best by more than this fraction")
    p.add_argument("--debug-retries", type=int, default=2,
                   help="debug-agent sessions after a functional failure of the gate")
    p.add_argument("--empty-retries", type=int, default=1, help="new sessions when the agent finishes without any edit")
    a = p.parse_args()
    if a.start_patch and not Path(a.start_patch).expanduser().is_file():
        p.error(f"không thấy --start-patch {a.start_patch}")
    a.history_from = [os.path.expanduser(h) for h in a.history_from]
    missing = [h for h in a.history_from if not (Path(h) / "history.md").is_file()]
    if missing:
        p.error(f"--history-from: không có history.md trong {missing}")
    if a.run_name is None:
        a.run_name = f"{'baseline' if a.baseline_only else 'fmax'}-{datetime.now().strftime('%Y%m%d-%H%M')}"
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


def append_csv(path: Path, fields: list, row: dict):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def ptag(a) -> str:
    return f"{a.period_ns:g}".replace(".", "p")


def synth_tag(a, prefix: str) -> str:
    return f"{prefix}_synth_{a.synth_flow}_p{ptag(a)}"


def bench_tag(a) -> str:
    """Cache-tag suffix of a gate with --extra-bench. `b2`: mm's "C0: <n> cycles" is parsed."""
    return "_b2" + "".join(f"_{n}" for n in a.extra_bench) if a.extra_bench else ""


def patch_hash(patch: str) -> str:
    return hashlib.sha256((patch + "\0").encode()).hexdigest()[:10]


def rtl_hash(rtl) -> str:
    """Hash of the tile's Verilog (what synth and the gate see), to spot a round that rebuilt a
    netlist already measured (fmax-r4 round 5 repeated round 3 word for word)."""
    h = hashlib.sha256()
    for name in sorted(rtl.files):
        h.update(name.encode() + b"\0" + rtl.files[name].encode() + b"\0")
    h.update(rtl.sram_models.encode())
    return h.hexdigest()[:16]


def cache_has(tag: str) -> bool:
    from chia.base.cache import get_active_cache
    return bool(get(get_active_cache().has.chia_remote(tag)))


def fmax(p: dict) -> float:
    """Fmax (Yosys + OpenSTA) of an accepted point."""
    return p["synth"].fmax_mhz


def perf(p: dict) -> float:
    """Fmax / cycle cost of an accepted point."""
    return fmax(p) / p.get("cyc", 1.0)


# ---------------------------------------------------------------------------
# Yosys + OpenSTA
# ---------------------------------------------------------------------------

def synth_row(s) -> dict:
    """results.csv columns of a synth result."""
    if s is None or not s.success:
        return {}
    return {"fmax_mhz": fmt(s.fmax_mhz, 2), "slack_ns": fmt(s.worst_slack_ns, 3), "tns_ns": fmt(s.tns_ns, 1),
            "area_um2": fmt(s.area_um2, 0), "critical_path": f"{s.crit_start} -> {s.crit_end}"}


def save_synth(d: Path, s):
    """runs/<label>/ reports of a synth result."""
    for name, text in (("timing_paths.rpt", s.paths), ("timing_endpoints.rpt", s.endpoints),
                       ("timing_families.txt", s.families), ("synth_stat.txt", s.stat),
                       ("yosys_log_tail.txt", s.yosys_log_tail), ("sta.log", s.sta_log)):
        write(d / name, text)


def synth_text(s) -> str:
    return (f"{s.fmax_mhz:.2f} MHz (slack {s.worst_slack_ns:.3f} ns, TNS {fmt(s.tns_ns, 1)} ns, area "
            f"{fmt(s.area_um2, 0)} um2, {s.crit_start} -> {s.crit_end}; Yosys {s.yosys_seconds / 60:.1f} min)"
            if s.success else f"lỗi: {s.error}")


def run_synth(a, out: Path, label: str, rtl, tag: str | None = None):
    kw = {"_chia_tag": tag} if tag else {}
    s = get(N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow, yosys_timeout=a.synth_timeout, **kw))
    save_synth(out / "runs" / label, s)
    print(f"synth {label} ({a.synth_flow}, {a.period_ns:g} ns): {synth_text(s)}", flush=True)
    return s


# ---------------------------------------------------------------------------
# build and gate
# ---------------------------------------------------------------------------

def build(a, name: str):
    """Build of RocketConfig (values only: with the profiler on, a ref passed on to another task
    would arrive as a _ProfiledResult wrapper)."""
    cb = ChiselBuildNode(N.CHIPYARD, N.CONFIG, target=BuildTarget.VERILATOR, make_jobs=a.make_jobs,
                         timeout_seconds=a.build_timeout, collect_generated_src=True, name=name)
    return get(cb.build.chia_remote(cb))


def build_errors(art) -> str:
    # sbt is run with -Dsbt.color=always: "[\x1b[31merror\x1b[0m]" would not match "[error]".
    text = re.sub(r"\x1b\[[0-9;]*m", "", (art.stdout or "") + "\n" + (art.stderr or ""))
    # An elaboration exception names its Scala line only in the stack frames: keep those of
    # rocket-chip/chipyard code, at most 12 after each exception.
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


def save_gate(d: Path, g):
    write(d / "gate.json", as_json(g))
    rows = ["bench,passed,mcycle,minstret,sim_cycles"]
    rows += [f"{n},{b['passed']},{b['mcycle']},{b['minstret']},{b['sim_cycles']}" for n, b in g.bench.items()]
    write(d / "cycles.csv", "\n".join(rows) + "\n")
    if g.fail_logs:
        write(d / "sim_fail.log", "\n\n".join(f"===== {n}\n{t}" for n, t in g.fail_logs.items()))


def ironlaw(a, base_gate, gate) -> dict:
    """{"fails": functional failures, "cyc": geomean of mcycle / baseline mcycle (None on a
    failure), "lines": per-benchmark report}. Every ISA test (but --exclude-isa) and benchmark
    must pass, with the baseline's minstret (same instructions: only cycles may change)."""
    fails = [f"ISA fail: {n}" for n in base_gate.isa if n not in a.exclude_isa and not gate.isa.get(n, False)]
    lines, prod = [], []
    for n, b in base_gate.bench.items():
        c = gate.bench.get(n)
        if not c or not c["passed"]:
            fails.append(f"{n}: không pass")
        elif c["minstret"] != b["minstret"]:
            fails.append(f"{n}: minstret {b['minstret']} -> {c['minstret']} (số lệnh khác: sai chức năng)")
        else:
            prod.append(c["mcycle"] / b["mcycle"])
            lines.append(f"{n}: mcycle {b['mcycle']} -> {c['mcycle']} ({(prod[-1] - 1) * 100:+.3f} %)")
    cyc = None if fails or not prod else math.exp(sum(math.log(x) for x in prod) / len(prod))
    if cyc is not None:
        lines.append(f"cycle cost (geomean mcycle / baseline): {cyc:.5f} ({(cyc - 1) * 100:+.3f} %)")
    return {"fails": fails, "cyc": cyc, "lines": fails + lines}


# ---------------------------------------------------------------------------
# baseline and start point
# ---------------------------------------------------------------------------

def ensure_baseline(a, out: Path, elfs: dict, snap: dict) -> dict:
    """Build + gate of the unmodified tree (the cycle reference), served from the cache when
    available (tags baseline_rtl2 / baseline_gate<bench>)."""
    tags = {"rtl": f"baseline_{RTL_TAG}", "gate": "baseline_gate" + bench_tag(a)}
    hits = {k: cache_has(t) for k, t in tags.items()}
    print("baseline cache: " + ", ".join(f"{tags[k]}={'HIT' if v else 'miss'}" for k, v in hits.items()), flush=True)
    d = out / "runs" / "baseline"
    t0 = time.time()
    art = None
    if not (hits["rtl"] and hits["gate"]):
        print(get(N.reset_rocket.chia_remote(snap["rocket_head"], "")) or "rocket-chip clean", flush=True)
        art = build(a, "baseline")
        save_build(d, art)
        if not art.success:
            raise SystemExit(f"baseline build failed:\n{build_errors(art)}")
        print(f"baseline build ok ({(time.time() - t0) / 60:.1f} min)", flush=True)
    rtl = get(N.extract_tile_rtl.chia_remote(art, _chia_tag=tags["rtl"]))
    gate = get(N.run_gate.chia_remote(art, elfs, repeat_seed=a.repeat_seed, _chia_tag=tags["gate"]))
    write(d / "rtl_info.json", json.dumps({"top": rtl.top, "files": len(rtl.files), "modules": rtl.modules,
                                           "blackboxes": list(rtl.blackboxes), "replaced": rtl.replaced},
                                          indent=2))
    save_gate(d, gate)
    print(f"gate: ISA {sum(gate.isa.values())}/{len(gate.isa)} pass, gate {gate.seconds / 60:.1f} min")
    for n, b in gate.bench.items():
        r = gate.bench_repeat.get(n, {})
        print(f"  {n:10s} pass={b['passed']} mcycle={b['mcycle']} minstret={b['minstret']} "
              f"sim_cycles={b['sim_cycles']} | seed {a.repeat_seed}: mcycle={r.get('mcycle')}")
    unknown = [n for n in a.exclude_isa if n not in gate.isa]
    if unknown:
        raise SystemExit(f"--exclude-isa: không có test {unknown}")
    problems = [f"ISA fail: {n}" for n, ok in gate.isa.items() if not ok and n not in a.exclude_isa]
    problems += [f"benchmark fail: {n}" for n, b in gate.bench.items() if not b["passed"]]
    problems += [f"{n}: mcycle khác khi đổi seed ({b['mcycle']} vs {gate.bench_repeat[n]['mcycle']})"
                 for n, b in gate.bench.items()
                 if n in gate.bench_repeat and gate.bench_repeat[n]["mcycle"] != b["mcycle"]]
    if problems:
        write(d / "problems.txt", "\n".join(problems))
        raise SystemExit("baseline chưa dùng được:\n  " + "\n  ".join(problems) + f"\nxem {d}/sim_fail.log")
    return {"name": "baseline", "patch": "", "rtl": rtl, "gate": gate}


def ensure_start(a, out: Path, snap: dict, base: dict, elfs: dict) -> dict:
    """Start point of the rounds (--start-patch): build, gate and synth, cached under
    baseline_start_<hash>_* (the RTL and gate tags are those of fmax-r4/r5)."""
    patch = Path(a.start_patch).expanduser().read_text()
    h = patch_hash(patch)
    tags = {"rtl": f"baseline_start_{h}_{RTL_TAG}", "gate": f"baseline_start_{h}_gate{bench_tag(a)}",
            "synth": synth_tag(a, f"baseline_start_{h}")}
    hits = {k: cache_has(t) for k, t in tags.items()}
    print(f"\nstart point ({N.CONFIG}, patch {a.start_patch}): "
          + ", ".join(f"{tags[k]}={'HIT' if v else 'miss'}" for k, v in hits.items()), flush=True)
    d = out / "runs" / "start"
    write(d / "start.patch", patch)
    art = None
    if not hits["rtl"] or not hits["gate"]:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], patch))
        art = build(a, "start")
        save_build(d, art)
        if not art.success:
            raise SystemExit(f"start point build failed:\n{build_errors(art)}")
    rtl = get(N.extract_tile_rtl.chia_remote(art, _chia_tag=tags["rtl"]))
    gate = get(N.run_gate.chia_remote(art, elfs, _chia_tag=tags["gate"]))
    save_gate(d, gate)
    res = ironlaw(a, base["gate"], gate)
    write(d / "gate_compare.txt", "\n".join(res["lines"]))
    print("start gate:\n  " + "\n  ".join(res["lines"]), flush=True)
    if res["fails"]:
        raise SystemExit("điểm xuất phát không qua gate:\n  " + "\n  ".join(res["fails"]))
    synth = run_synth(a, out, "start", rtl, tags["synth"])
    start = {"name": "start", "patch": patch, "rtl": rtl, "synth": synth, "cyc": res["cyc"]}
    append_csv(out / "results.csv", CSV_FIELDS,
               {"iter": "start", "accepted": True, **synth_row(synth), "isa_pass": True,
                "cycle_pct": f"{(res['cyc'] - 1) * 100:.3f}",
                "perf": f"{perf(start):.3f}" if synth.success else "", "reason": f"start: patch {a.start_patch}"})
    return start


# ---------------------------------------------------------------------------
# agent sessions
# ---------------------------------------------------------------------------

def tool_text() -> dict:
    """How the prompts name the tools (Claude Code names MCP tools mcp__<server>__<tool>)."""
    ab = {f"AB_{k.upper()}": "mcp__synth_ab__" + m
          for k, m in (("rebuild", "rebuild_verilog"), ("list", "list_modules"), ("start", "start_ab"),
                       ("result", "ab_result"))}
    return {"BASH_TOOL": "mcp__chipyard_bash__chipyard_bash_run_command", "OTHER_TOOLS": " You have no other tool.",
            **ab}


def render_prompt(a, i: int, best: dict, history: list[str], target_md: str) -> str:
    s = best["synth"]
    return string.Template(PROMPT.read_text()).safe_substitute(
        ITER=i, MAX_ITERS=a.iters, LLM_MIN=round(a.llm_timeout / 60), CONFIG_DESC=N.CONFIG,
        PERIOD_NS=f"{a.period_ns:g}", BEST_NAME=best["name"], BEST_SLACK=fmt(s.worst_slack_ns, 3),
        BEST_FMAX=fmt(s.fmax_mhz, 2), BEST_TNS=fmt(s.tns_ns, 1), BEST_AREA=fmt(s.area_um2, 0),
        BEST_IPC_LOSS=f"{(best.get('cyc', 1.0) - 1) * 100:+.2f} %", BEST_PERF=f"{perf(best):.2f}",
        MAX_IPC_LOSS=f"{a.max_ipc_loss:.1%}", MIN_PERF_GAIN=f"{a.min_perf_gain:.1%}", DEBUG_RETRIES=a.debug_retries,
        BENCHES=", ".join((*N.BENCHMARKS, *a.extra_bench)), STAGE_DIR=N.STAGE_DIR, TARGET=target_md,
        HISTORY="\n\n".join(history) or "(no earlier round)", **tool_text())


def stage_files(best: dict, history: list[str]) -> dict:
    s, rtl = best["synth"], best["rtl"]
    files = {"timing_paths.rpt": s.paths, "timing_endpoints.rpt": s.endpoints, "timing_families.txt": s.families,
             "synth_stat.txt": s.stat, "best.patch": best["patch"], "history.md": "\n\n".join(history)}
    files.update({f"best_verilog/{n}": t for n, t in rtl.files.items()})
    files.update({f"best_verilog/bb_{m}.v": t for m, t in rtl.blackboxes.items()})
    return files


def chia_target(best: dict) -> str:
    """The worst path located in the Verilog and the Chisel: a starting point for the agent,
    which still picks what to work on."""
    s = best["synth"]
    target = N.pick_target(best["rtl"], s)
    target["report"] = N.first_path(s.paths)
    target["ends"], target["sources"] = N.path_sources(best["rtl"], s, best["patch"])
    classes = get(N.find_scala_classes.chia_remote([m for _, m in target["chain"]]))
    return target_text(target, classes)


def target_text(target: dict, classes: dict) -> str:
    lines = [f"- Worst path of the current best: `{target['crit']}`",
             f"- It starts in instance `{target['start_path'] or N.TOP}` and ends in "
             f"`{target['end_path'] or N.TOP}`.",
             f"- Smallest instance holding both ends: `{target['path'] or N.TOP}` = module `{target['module']}`.",
             "- Instances from the top down (instance -> Verilog module -> Scala files that define it):"]
    for path, mod in target["chain"]:
        files = classes.get(mod) or []
        where = (", ".join(f"`{f}`" for f in files) if files else
                 "not found under rocket-chip/src/main/scala (e.g. hardfloat, which you must not edit: "
                 "change how the parent instantiates or feeds it instead)")
        lines.append(f"  - `{path or N.TOP}` -> `{mod}` -> {where}")
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
                  "added by the current best's patch; the numbers are the file's line numbers). Start from this "
                  "code; read more only where it is not enough:", "", target["sources"]]
    return "\n".join(lines)


CLAUDE_LIMIT = re.compile(r"hit your limit|RateLimitError|rate_limit on ")


def read_partial(remove: bool = True) -> str | None:
    """LLM_PARTIAL_FILE on the claude worker (transcript of a cut-off run)."""
    return get(N.read_text_claude.chia_remote(N.LLM_PARTIAL_FILE, remove))


def quota_wait_seconds(a, d: Path, prefix: str) -> int | None:
    """Seconds to wait before calling the agent again, or None when the call did not stop on a
    usage limit (or the limit resets later than --quota-max-wait)."""
    text = "\n".join(f.read_text(errors="replace") for f in (d / f"{prefix}_transcript.txt", d / f"{prefix}_error.txt")
                     if f.exists())
    if not CLAUDE_LIMIT.search(text):
        return None
    from chia.models.claude import parse_rate_limit_reset
    reset = parse_rate_limit_reset(text)
    if reset is None:
        m = re.search(r'"resetsAt":\s*(\d+)', text)
        reset = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc) if m else None
    if reset is None:
        return a.quota_wait
    wait = int((reset - datetime.now(timezone.utc)).total_seconds()) + 120
    return max(wait, 60) if wait <= a.quota_max_wait else None


def call_agent(a, llm, prompt: str, tag: str, d: Path, prefix: str, best: dict) -> str:
    """_call_agent_once, again after a wait when the call stopped on a usage limit ('You've hit
    your limit': wait until the reset time it gives), at most --quota-retries times. The edits of
    the cut-off call stay in the tree; its files are kept as <prefix>_quota<n>_*."""
    for n in range(a.quota_retries + 1):
        reason = _call_agent_once(a, llm, prompt, tag if n == 0 else f"{tag}_q{n}", d, prefix, best)
        wait = quota_wait_seconds(a, d, prefix) if reason else None
        if wait is None or n == a.quota_retries:
            return reason
        for f in d.glob(f"{prefix}_*"):
            if not f.name.startswith(f"{prefix}_quota") and f.name != f"{prefix}_prompt.md":
                f.rename(d / f.name.replace(f"{prefix}_", f"{prefix}_quota{n + 1}_", 1))
        print(f"{prefix}: hết hạn mức Claude, chờ {wait} s (tới {datetime.fromtimestamp(time.time() + wait):%H:%M}) "
              f"rồi gọi lại (lần {n + 1}/{a.quota_retries})", flush=True)
        time.sleep(wait)
    return reason


def _call_agent_once(a, llm, prompt: str, tag: str, d: Path, prefix: str, best: dict) -> str:
    """One agent call with chipyard_bash and synth_ab; writes <prefix>_response.md /
    <prefix>_transcript.txt / <prefix>_ab_experiments.jsonl into d. Returns "" or why it failed."""
    reason = ""
    read_partial()                                                   # stale from an earlier call
    # The two tools share the single "chipyard" slot, so nothing else runs on chipyard
    # (build, git) until both are stopped.
    tools = [BashTool("chipyard_bash", N.CHIPYARD, timeout_seconds=120, task_options={"resources": {"chipyard": 0.5}})]
    resp = None
    try:
        tools.append(AB.SynthABTool("synth_ab", best["rtl"], best["synth"], best["name"], N.CONFIG, a.period_ns,
                                    a.synth_flow, a.make_jobs, best["patch"],
                                    task_options={"resources": {"chipyard": 0.5}}))
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=tools, _chia_tag=tag))
    except Exception as e:                                           # noqa: BLE001
        write(d / f"{prefix}_error.txt", traceback.format_exc())
        reason = f"LLM lỗi: {type(e).__name__}: {e}"[:500]
    finally:
        for t in tools:
            t.stop()              # free the chipyard slot before the build
    write(d / f"{prefix}_ab_cleanup.txt", get(N.ab_cleanup.chia_remote()))
    write(d / f"{prefix}_ab_experiments.jsonl", get(N.read_text.chia_remote(AB.LOG_FILE)) or "")
    if resp is not None:
        write(d / f"{prefix}_response.md", resp.result)
        write(d / f"{prefix}_transcript.txt", resp.stream_result)
        if not resp.success:
            reason = reason or f"LLM không thành công (returncode {resp.returncode})"
    if resp is None or not resp.success:
        partial = read_partial()
        if partial:
            write(d / f"{prefix}_transcript.txt", partial)
    return reason


def make_llm(a, model: str, system_message: str, timeout: int):
    return N.SalvageClaudeLLM(model=model, system_message=system_message, timeout_seconds=timeout,
                              retries=1, effort=a.effort)


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


def after_agent(out: Path, d: Path, i: int, snap: dict, best: dict, r: dict, agent_reason: str,
                label: str = "") -> dict | None:
    """Diff after an agent call. An agent that timed out or failed still leaves its edits in the
    tree: they go on to the build like any other (the gate keeps them honest) and the failure is
    kept as a note. Returns the diff, or None with r["reason"] set when the round stops."""
    diff, why = collect_and_check(out, d, i, snap, best)
    r["patch"] = diff["patch"]
    if why:
        r["reason"] = "; ".join(x for x in (label, agent_reason, why) if x)
        return None
    if agent_reason:
        r.setdefault("notes", []).append(f"{label or 'agent'}: {agent_reason}, vẫn đánh giá phần đã sửa")
        print(f"round {i}: {agent_reason}; agent đã sửa {len(diff['files'])} file, vẫn build và đánh giá", flush=True)
    return diff


AB_NOTE = ("\n\nYou also have the `synth_ab` tool: `{rebuild}()` elaborates your current sources (1-3 min; call it "
           "again while it says `running`) and returns the compile errors, so use it to confirm the fix compiles "
           "before you finish. Do not start any synthesis.")


def render_fix_prompt(a, i: int, k: int, errors: str, patch: str, best: dict, notes: str) -> str:
    return string.Template(PROMPT_FIX.read_text()).safe_substitute(
        ITER=i, FIX=k, MAX_FIX=a.build_fixes, CONFIG_DESC=N.CONFIG, BEST_NAME=best["name"],
        ERRORS=errors[-12000:], PATCH=patch[-30000:], STAGE_DIR=N.STAGE_DIR,
        AB_NOTE=AB_NOTE.format(rebuild=tool_text()["AB_REBUILD"]),
        GOAL_NOTE=" (pipeline stages allowed; the loop maximizes frequency x IPC)",
        BEHAVIOUR_RULE="- Keep the edit's timing idea and its latency: if it added a pipeline stage, keep the stage "
                       "and keep every consumer of the delayed signals consistent with it.",
        NOTES=notes[-4000:].strip() or "(no summary)", **tool_text())


def render_debug_prompt(a, i: int, k: int, fails: str, patch: str, notes: str) -> str:
    return string.Template(PROMPT_DEBUG.read_text()).safe_substitute(
        ITER=i, FIX=k, MAX_FIX=a.debug_retries, FAILS=fails[-14000:], PATCH=patch[-30000:], STAGE_DIR=N.STAGE_DIR,
        AB_NOTE=AB_NOTE.format(rebuild=tool_text()["AB_REBUILD"]), NOTES=notes[-4000:].strip() or "(no summary)",
        **tool_text())


def build_with_fixes(a, i: int, out: Path, d: Path, fix_llm, snap: dict, best: dict, r: dict, diff: dict,
                     suffix: str = "") -> tuple:
    """Build of the round's edits. On a compile error the build-fix agent (as build_with_debug_retry
    in CHIA's timing_opt example) fixes it, at most --build-fixes times, with the same scope check
    and diff. Returns (artifact, diff); the artifact is None (r["reason"] set) when the round stops."""
    t_build = time.time()
    art = build(a, f"iter{i}{suffix}")
    save_build(d, art, f"build{suffix}.log")
    k = 0
    while not art.success and k < a.build_fixes:
        k += 1
        r["build_fixes"] = r.get("build_fixes", 0) + 1
        tag = f"{suffix.lstrip('_')}fix{k}"
        print(f"round {i}: build lỗi, gọi agent sửa (lần {k}/{a.build_fixes})", flush=True)
        fprompt = render_fix_prompt(a, i, k, build_errors(art), diff["patch"], best, last_response(d))
        write(d / f"{tag}_prompt.md", fprompt)
        reason = call_agent(a, fix_llm, fprompt, f"agent_{a.run_name}_iter{i}_{tag}_llm", d, tag, best)
        diff = after_agent(out, d, i, snap, best, r, reason, f"build lỗi, agent sửa lần {k}")
        if diff is None:
            return None, None
        art = build(a, f"iter{i}_{tag}")
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


def gate_and_judge(a, out: Path, label: str, art, rtl, elfs, base: dict, best: dict, r: dict,
                   seen: dict) -> dict:
    """Synth, then the gate, then the decision. A round whose Fmax cannot beat the best even at
    the baseline's cycle count stops after synth (the gate takes ~19 min, synth 3-4). A
    functional failure sets r["functional_fail"] (the text for the debug agent). Verilog identical
    to one already measured (`seen`: rtl_hash -> label) is rejected without either."""
    d = out / "runs" / label
    r["rtl"], r["rtl_hash"] = rtl, rtl_hash(rtl)
    if r["rtl_hash"] in seen:
        r["reason"] = f"Verilog {N.TOP} trùng hệt {seen[r['rtl_hash']]} (đã đo); bỏ qua synth và gate"
        return r
    t0 = time.time()
    synth = run_synth(a, out, label, rtl)
    r["synth"], r["synth_min"] = synth, (time.time() - t0) / 60
    if not synth.success:
        r["reason"] = f"Yosys/OpenSTA lỗi: {synth.error}"[:1000]
        return r
    need = perf(best) * (1 + a.min_perf_gain)
    if synth.fmax_mhz <= need:
        r["reason"] = (f"Fmax {fmax(best):.2f} -> {synth.fmax_mhz:.2f} MHz: không vượt được hiệu năng {perf(best):.2f} "
                       f"x (1 {a.min_perf_gain:+.1%}) = {need:.2f} kể cả khi cycle không tăng so với baseline; "
                       f"đường găng {synth.crit_start} -> {synth.crit_end}; không chạy gate")
        return r
    gate = get(N.run_gate.chia_remote(art, elfs))
    save_gate(d, gate)
    res = ironlaw(a, base["gate"], gate)
    write(d / "gate_compare.txt", "\n".join(res["lines"]))
    r["isa_pass"] = not any(f.startswith("ISA") for f in res["fails"])
    if res["fails"]:
        r["reason"] = "gate không đạt (chức năng): " + "; ".join(res["fails"][:10])
        logs = "\n\n".join(f"===== {n}\n{t[-2500:]}" for n, t in list(gate.fail_logs.items())[:6])
        r["functional_fail"] = "\n".join(res["fails"][:40]) + ("\n\n" + logs if logs else "")
        return r
    cyc = res["cyc"]
    new_perf = synth.fmax_mhz / cyc
    gain = new_perf / perf(best) - 1
    r.update(cyc=cyc, cycle_pct=f"{(cyc - 1) * 100:.3f}", perf=f"{new_perf:.3f}", perf_gain_pct=f"{gain * 100:.3f}")
    text = (f"Fmax {fmax(best):.2f} -> {synth.fmax_mhz:.2f} MHz, cycle {(cyc - 1) * 100:+.3f} % so với baseline, "
            f"hiệu năng {perf(best):.2f} -> {new_perf:.2f} ({gain:+.2%}); đường găng {synth.crit_start} -> "
            f"{synth.crit_end}")
    if cyc > 1 + a.max_ipc_loss:
        r["reason"] = f"tốn quá nhiều cycle: {text}; cho phép tối đa +{a.max_ipc_loss:.1%}"
    elif gain <= a.min_perf_gain:
        r["reason"] = f"hiệu năng không tăng đủ: {text}; cần > {a.min_perf_gain:+.1%}"
    else:
        r["accepted"], r["reason"] = True, text
    return r


# ---------------------------------------------------------------------------
# rounds
# ---------------------------------------------------------------------------

def last_response(d: Path) -> str:
    """Summary of the round's last agent answer (agent_response.md or retryN_response.md)."""
    files = sorted(d.glob("retry*_response.md"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    files = files or [d / "agent_response.md"]
    return files[-1].read_text()[-1500:] if files[-1].exists() else ""


def tried_edits(d: Path, diff_chars: int = 3000, max_chars: int = 12000) -> str:
    """The synth_ab measurements of the round so far (the A/B log is per round, so the last
    session's *_ab_experiments.jsonl holds them all), grouped by edit: each edit's diff on top of
    the best and its A/B results."""
    logs = sorted(d.glob("*_ab_experiments.jsonl"), key=lambda p: p.stat().st_mtime)
    if not logs:
        return ""
    edits: dict[str, list] = {}
    for line in logs[-1].read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("event") != "ab":
            continue
        c, p = e.get("child") or {}, e.get("parent") or {}
        if e.get("error") or not c.get("ok"):
            res = f"{e.get('module')}: failed ({(e.get('error') or c.get('error') or '')[:150]})"
        else:
            res = (f"{e.get('module')}: child {c.get('slack_ns')} ns vs parent {p.get('slack_ns')} ns, "
                   f"delta {e.get('delta_ns')} ns, child worst path {c.get('crit')}")
        edits.setdefault(e.get("diff") or "(diff not logged)", []).append(f"[{e.get('t')}] {res}")
    out = []
    for n, (diff, res) in enumerate(edits.items(), 1):
        out += [f"Edit {n}:", *[f"- {x}" for x in res], "", "```diff", diff[-diff_chars:].strip(), "```", ""]
    text = "\n".join(out).strip()
    return text if len(text) <= max_chars else "...(earlier edits cut)\n" + text[-max_chars:]


def history_entry(i: int, r: dict, resp_summary: str) -> str:
    lines = [f"### Vòng {i}: {'NHẬN' if r['accepted'] else 'LOẠI'}", f"Lý do: {r['reason'][:3000]}"]
    s = r.get("synth")
    if s is not None and s.success:
        lines.append(f"Synth: worst slack {s.worst_slack_ns:.3f} ns ({s.fmax_mhz:.2f} MHz), TNS {fmt(s.tns_ns, 1)} ns, "
                     f"đường găng {s.crit_start} -> {s.crit_end}")
    if r.get("cycle_pct"):
        lines.append(f"Cycle so với baseline: {r['cycle_pct']} %" +
                     (f"; hiệu năng so với bản tốt nhất: {r['perf_gain_pct']} %" if r.get("perf_gain_pct") else ""))
    if r.get("debug_fixes"):
        lines.append(f"Test lỗi, agent debug {r['debug_fixes']} lần.")
    if r.get("build_fixes"):
        lines.append(f"Build lỗi, agent sửa lỗi build {r['build_fixes']} lần.")
    if resp_summary:
        lines.append(f"Tóm tắt của agent: {resp_summary[:800]}")
    if r.get("tried") and not r["accepted"]:
        lines.append("Các sửa đổi agent đã đo bằng synth_ab trong vòng này (đều không được giữ; đừng thử lại "
                     "y nguyên hay cùng ý trên cùng logic):\n\n" + r["tried"])
    if not r["accepted"] and r["patch"]:
        lines.append("Patch đã bị loại (đã revert, đừng lặp lại y nguyên):\n```diff\n" + r["patch"][:4000] + "\n```")
    return "\n".join(lines)


def new_round() -> dict:
    return {"accepted": False, "reason": "", "synth": None, "rtl": None, "patch": "", "isa_pass": "", "build_fixes": 0}


def run_round(a, i: int, out: Path, llm, fix_llm, elfs, snap, base, best, history, seen) -> dict:
    """One round. Returns {"accepted", "reason", "synth", "rtl", "patch", "cyc", ...}."""
    d = out / "runs" / f"iter{i}"
    r = new_round()
    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    get(N.stage_for_agent.chia_remote(stage_files(best, history)))
    prompt = render_prompt(a, i, best, history, chia_target(best))
    write(d / "prompt.md", prompt)

    tag = f"agent_{a.run_name}_iter{i}_llm"
    t_agent = time.time()
    reason = call_agent(a, llm, prompt, tag, d, "agent", best)
    # a session that gives up without any edit (chia-fmax-r1 round 5: one turn, 7 s, no synth_ab)
    # gets a new session told so, at most --empty-retries times
    for k in range(1, a.empty_retries + 1):
        if reason or get(N.collect_diff.chia_remote(snap["rocket_head"], snap["outside"]))["patch"] != best["patch"]:
            break
        print(f"round {i}: agent không sửa gì, gọi phiên mới (lần {k}/{a.empty_retries})", flush=True)
        rprompt = prompt + EMPTY_RETRY.format(n=k, tried=tried_edits(d) or "(no synth_ab measurement)",
                                              answer=last_response(d)[-1500:])
        write(d / f"retry{k}_prompt.md", rprompt)
        reason = call_agent(a, llm, rprompt, f"{tag}_retry{k}", d, f"retry{k}", best)
    r["agent_min"] = (time.time() - t_agent) / 60
    r["tried"] = tried_edits(d, diff_chars=1500, max_chars=5000)

    diff = after_agent(out, d, i, snap, best, r, reason)
    if diff is None:
        return r
    art, diff = build_with_fixes(a, i, out, d, fix_llm, snap, best, r, diff)
    if art is None:
        return r
    rtl = tile_rtl(art, r)
    if rtl is None:
        return r
    r = gate_and_judge(a, out, f"iter{i}", art, rtl, elfs, base, best, r, seen)
    k = 0
    while r.get("functional_fail") and k < a.debug_retries:
        # as debug_failure in CHIA's timing_opt: the edit builds but fails a test
        k += 1
        r["debug_fixes"] = k
        fails = r.pop("functional_fail")
        print(f"round {i}: test lỗi, gọi agent debug (lần {k}/{a.debug_retries}): {r['reason'][:200]}", flush=True)
        dprompt = render_debug_prompt(a, i, k, fails, r["patch"], last_response(d))
        write(d / f"debug{k}_prompt.md", dprompt)
        debug_llm = make_llm(a, a.fix_model or a.model, SYSTEM_MESSAGE_DEBUG, a.fix_timeout)
        reason = call_agent(a, debug_llm, dprompt, f"agent_{a.run_name}_iter{i}_debug{k}_llm", d, f"debug{k}", best)
        before, failed = r["patch"], r["reason"]
        r.update(reason="", synth=None, rtl=None)
        diff = after_agent(out, d, i, snap, best, r, reason, f"test lỗi, agent debug lần {k}")
        if diff is None:
            return r
        if r["patch"] == before:
            r["reason"] = f"agent debug lần {k} không sửa gì" + (f" ({reason})" if reason else "") + f"; {failed}"
            return r
        art, diff = build_with_fixes(a, i, out, d, fix_llm, snap, best, r, diff, f"_d{k}")
        if art is None:
            return r
        rtl = tile_rtl(art, r)
        if rtl is None:
            return r
        r = gate_and_judge(a, out, f"iter{i}/debug{k}", art, rtl, elfs, base, best, r, seen)
    r.pop("functional_fail", None)
    return r


def run(a, out: Path):
    elfs = get(N.load_test_elfs.chia_remote(tuple(a.extra_bench)))
    print(f"ELF: {len(elfs['isa'])} ISA tests, benchmarks {list(elfs['bench'])}", flush=True)
    snap = get(N.tree_snapshot.chia_remote())
    write(out / "runs" / "tree_snapshot.json", json.dumps(snap, indent=2))

    base = ensure_baseline(a, out, elfs, snap)
    # the baseline is synthesized when it is the start point or measured (--baseline-only)
    if a.baseline_only or not a.start_patch:
        base["synth"] = run_synth(a, out, "baseline", base["rtl"], synth_tag(a, "baseline"))
        append_csv(out / "results.csv", CSV_FIELDS, {"iter": "baseline", "accepted": True, **synth_row(base["synth"]),
                                                     "isa_pass": True, "cycle_pct": "0.000",
                                                     "perf": fmt(fmax(base), 3) if base["synth"].success else "",
                                                     "reason": "baseline"})
    start = ensure_start(a, out, snap, base, elfs) if a.start_patch else None
    if a.baseline_only:
        return
    best = start or base
    if not best["synth"].success:
        raise SystemExit(f"{best['name']}: Yosys/OpenSTA lỗi: {best['synth'].error}")

    llm = make_llm(a, a.model, SYSTEM_MESSAGE, a.llm_timeout)
    fix_llm = make_llm(a, a.fix_model or a.model, SYSTEM_MESSAGE_FIX, a.fix_timeout)
    # earlier runs' rounds first (fmax-r5 round 1 redid an edit rejected in fmax-r4)
    history = [f"### Đợt trước: {Path(h).name}\n\n" + (Path(h) / "history.md").read_text().strip()
               for h in a.history_from]
    seen = {rtl_hash(best["rtl"]): best["name"]}                    # Verilog already measured
    for i in range(1, a.iters + 1):
        t0 = time.time()
        print(f"\n######## round {i}/{a.iters}", flush=True)
        try:
            r = run_round(a, i, out, llm, fix_llm, elfs, snap, base, best, history, seen)
        except SystemExit:
            raise
        except Exception as e:                                       # noqa: BLE001
            write(out / "runs" / f"iter{i}" / "error.txt", traceback.format_exc())
            r = {**new_round(), "reason": f"lỗi hạ tầng: {type(e).__name__}: {e}"[:1000]}
        r["minutes"] = (time.time() - t0) / 60
        if r.get("notes"):
            r["reason"] = r["reason"] + " [" + "; ".join(r["notes"]) + "]"
        resp_summary = last_response(out / "runs" / f"iter{i}")
        write(out / "runs" / f"iter{i}" / "decision.json",
              json.dumps({k: v for k, v in r.items() if k not in ("synth", "rtl", "patch")},
                         indent=2, ensure_ascii=False, default=str))
        append_csv(out / "results.csv", CSV_FIELDS,
                   {"iter": i, "accepted": r["accepted"], **synth_row(r.get("synth")), "isa_pass": r["isa_pass"],
                    "cycle_pct": r.get("cycle_pct", ""), "perf": r.get("perf", ""),
                    "perf_gain_pct": r.get("perf_gain_pct", ""), "reason": r["reason"].replace("\n", " | ")[:1000],
                    "elapsed_min": f"{r['minutes']:.1f}", "synth_min": fmt(r.get("synth_min"), 1)})
        print(f"round {i}: {'ACCEPT' if r['accepted'] else 'REJECT'} ({r['minutes']:.1f} min) - {r['reason'][:400]}",
              flush=True)
        if r.get("rtl_hash"):
            seen.setdefault(r["rtl_hash"], f"iter{i}")
        if r["accepted"]:
            best = {"name": f"iter{i}", "patch": r["patch"], "rtl": r["rtl"], "synth": r["synth"], "cyc": r["cyc"]}
        history.append(history_entry(i, r, resp_summary))
        write(out / "history.md", "\n\n".join(history[len(a.history_from):]))

    write(out / "patches" / "best.patch", best["patch"])
    write(out / "best.json", json.dumps({"best": best["name"], **synth_row(best["synth"]),
                                         "cycle_cost": round(best.get("cyc", 1.0), 5), "perf": round(perf(best), 3)},
                                        indent=2))
    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    print(f"\nbest: {best['name']} | " + json.dumps(synth_row(best["synth"]), ensure_ascii=False))


def export_profile(out: Path):
    log = out / "profile" / "ChiaProfileCollector.log"
    if not log.exists():
        return
    # One run spans hours with long quiet gaps (build, gate): keep it in one segment.
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
    try:
        run(a, out)
    finally:
        try:
            stop_collector()
        finally:
            stop_cache()
            export_profile(out)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
