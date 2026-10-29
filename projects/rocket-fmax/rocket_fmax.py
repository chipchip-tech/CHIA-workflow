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
  --chia-mode: as CHIA's timing_opt example: one long session with synth_ab
      (prompts/improve_timing_chia.md), pipeline stages allowed; accept on Fmax / cycle cost
      (geomean mcycle vs baseline, at most --max-ipc-loss), a functional failure goes to a
      debug agent (--debug-retries)
  equivalence of a finished run's best patch vs the baseline (no loop):
      chia job submit --working-dir . -- python rocket_fmax.py --equiv-only --patch-file <best.patch>

Outputs go to --out-dir/<run-name>/ on the head (results.csv, patches/, runs/,
profile/). Needs workers advertising chipyard, verilator_run, eda, and opencode_creds
(--agent opencode) or claude_creds (--agent claude), see cluster.yaml.
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
from datetime import datetime, timezone
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
PROMPT_CHIA = HERE / "prompts" / "improve_timing_chia.md"
PROMPT_DEBUG = HERE / "prompts" / "fix_failure.md"
BUDGET_DIR = "/home/ray/rocket_fmax_budget"   # on chipyard: BudgetBashTool logs (--loop-ab)
CSV_FIELDS = ["iter", "accepted", "wns_ns", "tns_ns", "worst_slack_ns", "fmax_mhz", "critical_path",
              "isa_pass", "bench_cycles_match", "reason", "area_um2", "elapsed_min",
              "target", "ab_delta_ns", "ab_sessions", "ipc_loss_pct", "perf_gain_pct"]
SYSTEM_MESSAGE = ("You are a hardware timing engineer working on the Rocket core in Chipyard. "
                  "You only edit Chisel sources through the chipyard_bash tool and never build, "
                  "simulate or synthesize: the surrounding loop does that after you finish.")
SYSTEM_MESSAGE_AB = ("You are a hardware timing engineer working on the Rocket core in Chipyard. "
                     "You edit Chisel sources through the chipyard_bash tool and check each edit with the "
                     "synth_ab tool (elaborate, then synthesize a module next to the current best). You never "
                     "run make, sbt, verilator or yosys yourself: the full build and gate run after you finish.")
SYSTEM_MESSAGE_CHIA = ("You are a hardware timing engineer working on the Rocket core in Chipyard. You raise "
                       "frequency x IPC: you edit Chisel sources through the chipyard_bash tool (pipeline stages "
                       "allowed, with every consumer updated) and check each edit with the synth_ab tool. You never "
                       "run make, sbt, verilator or yosys yourself: the full build, tests and synthesis run after "
                       "you finish.")
EMPTY_RETRY = """

## Your previous session made no edit

A session on this round finished without changing any file. Its answer:

{answer}

That wastes the round. Try at least two concrete edits on the worst path family. Check each one with
synth_ab, ending with the whole `RocketTile`. A pipeline stage is allowed: the loop measures its
cycle cost on the benchmarks and rejects the round if that cost is too high.
"""
SYSTEM_MESSAGE_DEBUG = ("You are a Chisel engineer working on the Rocket core in Chipyard. An edit made earlier in this "
                        "round builds but fails functional tests; you find and fix the bug through the chipyard_bash "
                        "tool and keep the intent of the edit. You never run make, sbt, verilator or yosys.")
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
    p.add_argument("--agent", choices=["opencode", "claude"], default="opencode",
                   help="agent CLI: opencode (worker opencode_creds) or claude = Claude Code (worker claude_creds, "
                        "logged in through the mounted ~/.claude)")
    p.add_argument("--model", default=None, help="default: opencode/big-pickle (opencode), sonnet (claude)")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
                   help="--agent claude: claude --effort")
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
    p.add_argument("--quota-max-wait", type=int, default=6 * 3600,
                   help="--agent claude: on 'You've hit your limit', wait until the reset time it gives (+2 min), "
                        "unless that is further away than this (s; weekly limit), then the call fails")
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
                   help="--loop-ab: opencode `steps` of an edit session (then a text-only answer; 0 = no limit); "
                        "claude: --max-turns = this + 10 (claude then stops without an answer)")
    p.add_argument("--ab-refines", type=int, default=2,
                   help="--loop-ab: extra edit sessions after a losing A/B (0 = one session per round)")
    p.add_argument("--ab-min-gain", type=float, default=0.1,
                   help="--loop-ab: the A/B must improve the module's worst slack by more than this (ns; "
                        "synthesis noise is about 0.1 ns) before the round goes on to gate + full synth")
    p.add_argument("--start-patch", default=None,
                   help="start the rounds from this patch (e.g. best.patch of an earlier run) "
                        "instead of the unmodified rocket-chip")
    p.add_argument("--min-gain-ns", type=float, default=0.0,
                   help="accept only if worst slack improves by more than this")
    p.add_argument("--tns-min-gain", type=float, default=0.0,
                   help="also accept a round whose worst slack did not improve if TNS (< 0, i.e. a period "
                        "below the critical path) improves by more than this fraction of the best one's "
                        "(0.05 = 5 %%) and worst slack stays within --wns-tol of the best seen; 0 = off")
    p.add_argument("--wns-tol", type=float, default=0.1,
                   help="--tns-min-gain: how far (ns) worst slack may fall below the best accepted so far")
    p.add_argument("--chia-mode", action="store_true",
                   help="as CHIA's timing_opt example: one long agent session per round with the synth_ab tool "
                        "(prompts/improve_timing_chia.md, --llm-timeout), pipeline stages allowed; accepted when "
                        "Fmax / cycle cost beats the best by --min-perf-gain and the cycle cost stays within "
                        "--max-ipc-loss of the baseline; functional failures go to a debug agent (--debug-retries)")
    p.add_argument("--extra-bench", nargs="*", default=[],
                   help="more riscv-tests benchmarks for the gate (e.g. mm spmv, with FP), next to "
                        + ", ".join(N.BENCHMARKS))
    p.add_argument("--max-ipc-loss", type=float, default=0.03,
                   help="--chia-mode: cycle cost (geomean of mcycle / baseline mcycle) may be at most 1 + this")
    p.add_argument("--min-perf-gain", type=float, default=0.01,
                   help="--chia-mode: Fmax / cycle cost must beat the best by more than this fraction")
    p.add_argument("--debug-retries", type=int, default=2,
                   help="--chia-mode: debug-agent sessions after a functional failure of the gate")
    p.add_argument("--empty-retries", type=int, default=1,
                   help="--chia-mode: new sessions when the agent finishes without any edit")
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
    if a.agent == "claude" and (a.ab_tool or a.bypass_llm):
        p.error("--agent claude chưa hỗ trợ --ab-tool hay --bypass-llm")
    if a.chia_mode and (a.loop_ab or a.ab_tool or a.bypass_llm or a.equiv):
        p.error("--chia-mode không dùng chung với --loop-ab, --ab-tool, --bypass-llm hay --equiv "
                "(đổi cycle thì không kiểm tương đương lockstep được)")
    if a.start_patch and not Path(a.start_patch).expanduser().is_file():
        p.error(f"không thấy --start-patch {a.start_patch}")
    a.ab_tool = a.ab_tool or a.chia_mode       # chia-mode: the agent gets synth_ab
    if a.model is None:
        a.model = "sonnet" if a.agent == "claude" else "opencode/big-pickle"
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


def build_loop(a, name: str):
    """Build for a round (or the start point)."""
    return get(build(a, name))


def ensure_start(a, out: Path, snap: dict, base: dict, elfs: dict) -> dict:
    """Start point of the rounds when --start-patch is given: build + synth/STA (+ gate
    with --chia-mode), cached under baseline_start_<hash>_*."""
    patch = Path(a.start_patch).expanduser().read_text()
    h = hashlib.sha256((patch + "\0").encode()).hexdigest()[:10]
    tags = {"rtl": f"baseline_start_{h}_rtl",
            "synth": f"baseline_start_{h}_synth_{a.synth_flow}_p" + f"{a.period_ns:g}".replace(".", "p")}
    if a.chia_mode:          # its cycle cost is the reference of the first round
        tags["gate"] = f"baseline_start_{h}_gate{bench_tag(a)}"
    hits = {k: cache_has(t) for k, t in tags.items()}
    print(f"\nstart point ({N.CONFIG}, patch {a.start_patch}): "
          + ", ".join(f"{tags[k]}={'HIT' if v else 'miss'}" for k, v in hits.items()), flush=True)
    d = out / "runs" / "start"
    write(d / "start.patch", patch)
    art = None
    if not hits["rtl"] or not hits.get("gate", True):
        get(N.reset_rocket.chia_remote(snap["rocket_head"], patch))
        art = build_loop(a, "start")
        save_build(d, art)
        if not art.success:
            raise SystemExit(f"start point build failed:\n{build_errors(art)}")
    rtl = get(N.extract_tile_rtl.chia_remote(art, _chia_tag=tags["rtl"]))
    gate_ref = N.run_gate.chia_remote(art, elfs, _chia_tag=tags["gate"]) if "gate" in tags else None
    synth = get(N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow, _chia_tag=tags["synth"]))
    save_synth(d, synth)
    print_synth("start", synth)
    if not synth.success:
        raise SystemExit(f"start point synth/STA lỗi: {synth.error}")
    start = {"name": "start", "patch": patch, "synth": synth, "rtl": rtl}
    row = {"iter": "start", "accepted": True, **synth_row(synth),
           "reason": f"start: patch {a.start_patch}"}
    if gate_ref is not None:
        gate = get(gate_ref)
        save_gate(d, gate)
        res = ironlaw(a, base["gate"], gate)
        write(d / "gate_compare.txt", "\n".join(res["lines"]))
        print("start gate:\n  " + "\n  ".join(res["lines"]), flush=True)
        if res["fails"]:
            raise SystemExit("điểm xuất phát không qua gate:\n  " + "\n  ".join(res["fails"]))
        start["cyc"] = res["cyc"]
        row.update(isa_pass=True, bench_cycles_match=res["cyc"] == 1.0,
                   ipc_loss_pct=f"{(res['cyc'] - 1) * 100:.3f}")
    append_row(out / "results.csv", row)
    return start


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


def bench_tag(a) -> str:
    """Cache-tag suffix of a gate with --extra-bench (the baseline gate of the 6 keeps its tag).
    `b2`: mm's "C0: <n> cycles" is parsed since chia-1 (whose cached gate has mm failed)."""
    return "_b2" + "".join(f"_{n}" for n in a.extra_bench) if a.extra_bench else ""


def ironlaw(a, base_gate, gate) -> dict:
    """--chia-mode gate: {"fails": functional failures, "cyc": geomean of mcycle / baseline mcycle
    (None on a failure), "lines": per-benchmark report}. Every ISA test (but --exclude-isa) and
    benchmark must pass, with the baseline's minstret (same instructions: only cycles may change)."""
    fails = [f"ISA fail: {n}" for n in base_gate.isa if n not in a.exclude_isa and not gate.isa.get(n, False)]
    lines, logs, prod = [], 0.0, []
    for n, b in base_gate.bench.items():
        c = gate.bench.get(n)
        if not c or not c["passed"]:
            fails.append(f"{n}: không pass")
        elif c["minstret"] != b["minstret"]:
            fails.append(f"{n}: minstret {b['minstret']} -> {c['minstret']} (số lệnh khác: sai chức năng)")
        else:
            prod.append(c["mcycle"] / b["mcycle"])
            lines.append(f"{n}: mcycle {b['mcycle']} -> {c['mcycle']} ({(prod[-1] - 1) * 100:+.3f} %)")
    import math
    cyc = None if fails or not prod else math.exp(sum(math.log(x) for x in prod) / len(prod))
    if cyc is not None:
        lines.append(f"cycle cost (geomean mcycle / baseline): {cyc:.5f} ({(cyc - 1) * 100:+.3f} %)")
    return {"fails": fails, "cyc": cyc, "lines": fails + lines}


def perf(p: dict) -> float:
    """Fmax / cycle cost of an accepted point (--chia-mode)."""
    return p["synth"].fmax_mhz / p.get("cyc", 1.0)


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
    tags = {"rtl": "baseline_rtl", "gate": "baseline_gate" + bench_tag(a),
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

def render_prompt(a, i: int, best: dict, history: list[str], target_md: str = "") -> str:
    s = best["synth"]
    if a.chia_mode:
        return string.Template(PROMPT_CHIA.read_text()).safe_substitute(
            ITER=i, MAX_ITERS=a.iters, LLM_MIN=round(a.llm_timeout / 60), CONFIG_DESC=N.CONFIG,
            PERIOD_NS=a.period_ns, BEST_NAME=best["name"], BEST_SLACK=fmt(s.worst_slack_ns, 4),
            BEST_FMAX=fmt(s.fmax_mhz, 2), BEST_IPC_LOSS=f"{(best.get('cyc', 1.0) - 1) * 100:+.2f} %",
            BEST_PERF=f"{perf(best):.2f}", MAX_IPC_LOSS=f"{a.max_ipc_loss:.1%}",
            MIN_PERF_GAIN=f"{a.min_perf_gain:.1%}", DEBUG_RETRIES=a.debug_retries,
            BENCHES=", ".join((*N.BENCHMARKS, *a.extra_bench)), STAGE_DIR=N.STAGE_DIR, TARGET=target_md,
            HISTORY="\n\n".join(history) or "(no earlier round)", **tool_text(a))
    return string.Template((PROMPT_AB if a.ab_tool else PROMPT).read_text()).safe_substitute(
        ITER=i, MAX_ITERS=a.iters, CONFIG_DESC=N.CONFIG, PERIOD_NS=a.period_ns, BEST_NAME=best["name"],
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
CLAUDE_LIMIT = re.compile(r"hit your limit|RateLimitError|rate_limit on ")


def read_partial(a, remove: bool = True) -> str | None:
    """LLM_PARTIAL_FILE on the agent's worker (transcript of a cut-off run)."""
    fn = N.read_text_claude if a.agent == "claude" else N.read_text_opencode
    return get(fn.chia_remote(N.LLM_PARTIAL_FILE, remove))


def quota_wait_seconds(a, d: Path, prefix: str) -> int | None:
    """Seconds to wait before calling the agent again, or None when the call did not stop on a
    usage limit (or, for claude, the limit resets later than --quota-max-wait)."""
    text = "\n".join(f.read_text(errors="replace") for f in (d / f"{prefix}_transcript.txt", d / f"{prefix}_error.txt")
                     if f.exists())
    if a.agent != "claude":
        return a.quota_wait if QUOTA_ERROR in text else None
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


def call_agent(a, llm, prompt: str, tag: str, d: Path, prefix: str, best: dict, ab: bool,
               budget_log: str | None = None) -> str:
    """_call_agent_once, again after a wait when the call stopped on a usage limit, at most
    --quota-retries times: opencode's free models ('Free usage limit reached', lab-t4: both
    build-fix calls after a 45-step edit session) wait --quota-wait s; claude ('You've hit your
    limit') waits until the reset time it gives. The edits of the cut-off call stay in the tree;
    its files are kept as <prefix>_quota<n>_*."""
    for n in range(a.quota_retries + 1):
        reason = _call_agent_once(a, llm, prompt, tag if n == 0 else f"{tag}_q{n}", d, prefix, best, ab,
                                  budget_log)
        wait = quota_wait_seconds(a, d, prefix) if reason else None
        if wait is None or n == a.quota_retries:
            return reason
        for f in d.glob(f"{prefix}_*"):
            if not f.name.startswith(f"{prefix}_quota") and f.name != f"{prefix}_prompt.md":
                f.rename(d / f.name.replace(f"{prefix}_", f"{prefix}_quota{n + 1}_", 1))
        print(f"{prefix}: hết hạn mức {a.agent}, chờ {wait} s (tới {datetime.fromtimestamp(time.time() + wait):%H:%M}) "
              f"rồi gọi lại (lần {n + 1}/{a.quota_retries})", flush=True)
        time.sleep(wait)
    return reason


def _call_agent_once(a, llm, prompt: str, tag: str, d: Path, prefix: str, best: dict, ab: bool,
                     budget_log: str | None = None) -> str:
    """One agent call with chipyard_bash (and synth_ab with `ab`); writes
    <prefix>_response.md / <prefix>_transcript.txt into d. Returns "" or why it failed.
    With `budget_log`, chipyard_bash is a BudgetBashTool (--edit-budget) logging there;
    the log is copied to <prefix>_budget.jsonl."""
    reason = ""
    read_partial(a)                                                   # stale from an earlier call
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
            tools.append(AB.SynthABTool("synth_ab", best["rtl"], best["synth"], best["name"], N.CONFIG,
                                        a.period_ns, a.synth_flow, a.make_jobs,
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
    if resp is None or not resp.success:
        partial = read_partial(a)
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


def ab_note(a) -> str:
    t = tool_text(a)
    return (f"\n\nYou also have the `synth_ab` tool: `{t['AB_REBUILD']}()` elaborates your current "
            "sources (1-3 min; call it again while it says `running`) and returns the compile errors, so use it "
            "to confirm the fix compiles before you finish. Do not start any synthesis.") if a.ab_tool else ""


def render_fix_prompt(a, i: int, k: int, errors: str, patch: str, best: dict, notes: str) -> str:
    if a.chia_mode:
        goal, rule = (" (pipeline stages allowed; the loop maximizes frequency x IPC)",
                      "- Keep the edit's timing idea and its latency: if it added a pipeline stage, keep the stage "
                      "and keep every consumer of the delayed signals consistent with it.")
    else:
        goal, rule = (" without changing its cycle-by-cycle behaviour",
                      f"- The behaviour must stay cycle-identical to {best['name']}: no new pipeline stages, no new "
                      "or removed\n  stall/kill/replay cycles.")
    return string.Template(PROMPT_FIX.read_text()).safe_substitute(
        ITER=i, FIX=k, MAX_FIX=a.build_fixes, CONFIG_DESC=N.CONFIG, BEST_NAME=best["name"],
        ERRORS=errors[-12000:], PATCH=patch[-30000:], STAGE_DIR=N.STAGE_DIR, AB_NOTE=ab_note(a),
        GOAL_NOTE=goal, BEHAVIOUR_RULE=rule, NOTES=notes[-4000:].strip() or "(no summary)", **tool_text(a))


def render_debug_prompt(a, i: int, k: int, fails: str, patch: str, notes: str) -> str:
    return string.Template(PROMPT_DEBUG.read_text()).safe_substitute(
        ITER=i, FIX=k, MAX_FIX=a.debug_retries, FAILS=fails[-14000:], PATCH=patch[-30000:], STAGE_DIR=N.STAGE_DIR,
        AB_NOTE=ab_note(a), NOTES=notes[-4000:].strip() or "(no summary)", **tool_text(a))


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
    a full RocketTile result already in hand (a whole-tile A/B), not synthesized again.
    The synth (~4 min) is judged first: when it rejects the round the gate (~12 min) is
    cancelled and r["tile_reject"] is set (lab-c3/c4: A/B wins the whole tile rejected)."""
    r["rtl"] = rtl
    gate_ref = N.run_gate.chia_remote(art, elfs)
    synth = synth if synth is not None else get(N.synth_sta.chia_remote(rtl, a.period_ns, a.synth_flow))
    save_synth(d, synth)
    r["synth"] = synth
    ok, why = judge(a, best, synth)
    if not ok:
        chia_cancel(gate_ref, force=True)
        r["reason"], r["tile_reject"] = why, synth.success
        return r
    gate = get(gate_ref)
    save_gate(d, gate)
    if a.chia_mode:
        return judge_chia(a, d, base, best, r, gate)
    isa_ok, cyc_ok, gwhy = compare_gate(base["gate"], gate, a.exclude_isa)
    r["isa_pass"], r["bench_cycles_match"] = isa_ok, cyc_ok
    write(d / "gate_compare.txt", "\n".join(gwhy) or "khớp baseline")
    if not (isa_ok and cyc_ok):
        r["reason"] = "gate không đạt: " + "; ".join(gwhy)
        return r
    r["accepted"], r["reason"] = True, why
    return r


def judge(a, best: dict, synth) -> tuple[bool, str]:
    """Whole-tile decision of a synth result against the best: (accepted, reason)."""
    if not synth.success:
        return False, f"synth/STA lỗi: {synth.error}"
    if a.chia_mode:
        # Fmax / cycle cost can beat the best only if Fmax alone does (cycle cost >= 1)
        bound, f0 = perf(best) * (1 + a.min_perf_gain), best["synth"].fmax_mhz
        if synth.fmax_mhz <= bound:
            return False, (f"Fmax {synth.fmax_mhz:.2f} MHz không vượt {bound:.2f} MHz (hiệu năng {perf(best):.2f} của "
                           f"{best['name']} + {a.min_perf_gain:.0%}) kể cả khi không tốn cycle; worst slack "
                           f"{synth.worst_slack_ns:.4f} ns, đường găng {synth.crit_start} -> {synth.crit_end}")
        return True, f"Fmax {f0:.2f} -> {synth.fmax_mhz:.2f} MHz"
    old, new = best["synth"].worst_slack_ns, synth.worst_slack_ns
    if new > old + a.min_gain_ns:
        return True, f"slack {old:.4f} -> {new:.4f} ns"
    tns = tns_gain(a, best, synth)
    if tns is not None:
        gain, floor, text = tns
        if gain > a.tns_min_gain and new >= floor:
            return True, f"{text}; slack {old:.4f} -> {new:.4f} ns (sàn {floor:.4f})"
    why = (f"slack không tốt hơn: {new:.4f} ns so với {old:.4f} ns "
           f"của {best['name']} (đường găng {synth.crit_start} -> {synth.crit_end})")
    if tns is not None:
        why += f"; {tns[2]} (cần > {a.tns_min_gain:.0%}, slack >= {tns[1]:.4f})"
    return False, why


def judge_chia(a, d: Path, base: dict, best: dict, r: dict, gate) -> dict:
    """--chia-mode decision once the tile passed judge(): functional gate, cycle cost within
    --max-ipc-loss of the baseline, Fmax / cycle cost better than the best by --min-perf-gain.
    A functional failure sets r["functional_fail"] (the text for the debug agent)."""
    res = ironlaw(a, base["gate"], gate)
    write(d / "gate_compare.txt", "\n".join(res["lines"]))
    r["isa_pass"] = not any(f.startswith("ISA") for f in res["fails"])
    if res["fails"]:
        r["reason"] = "gate không đạt (chức năng): " + "; ".join(res["fails"][:10])
        logs = "\n\n".join(f"===== {n}\n{t[-2500:]}" for n, t in list(gate.fail_logs.items())[:6])
        r["functional_fail"] = "\n".join(res["fails"][:40]) + ("\n\n" + logs if logs else "")
        return r
    cyc, synth = res["cyc"], r["synth"]
    new_perf = synth.fmax_mhz / cyc
    gain = new_perf / perf(best) - 1
    r.update(cyc=cyc, bench_cycles_match=all(l.endswith("(+0.000 %)") for l in res["lines"][:-1]),
             ipc_loss_pct=f"{(cyc - 1) * 100:.3f}", perf_gain_pct=f"{gain * 100:.3f}")
    text = (f"Fmax {best['synth'].fmax_mhz:.2f} -> {synth.fmax_mhz:.2f} MHz, cycle {(cyc - 1) * 100:+.3f} % "
            f"so với baseline, hiệu năng {perf(best):.2f} -> {new_perf:.2f} ({gain:+.2%})")
    if cyc > 1 + a.max_ipc_loss:
        r["reason"] = f"tốn quá nhiều cycle: {text}; cho phép tối đa +{a.max_ipc_loss:.1%}"
    elif gain <= a.min_perf_gain:
        r["reason"] = f"hiệu năng không tăng đủ: {text}; cần > {a.min_perf_gain:+.1%}"
    else:
        r["accepted"], r["reason"] = True, text
    return r


def tns_gain(a, best: dict, synth):
    """--tns-min-gain: (relative TNS gain vs best, worst-slack floor, text), or None when off
    or TNS is not negative. The floor is the best worst slack accepted so far minus --wns-tol,
    so rounds accepted on TNS cannot let worst slack drift down step by step."""
    old, new = best["synth"].tns_ns, synth.tns_ns
    if a.tns_min_gain <= 0 or old is None or new is None or old >= 0:
        return None
    gain = (new - old) / -old
    floor = best.get("peak_wns", best["synth"].worst_slack_ns) - a.wns_tol
    return gain, floor, f"TNS {old:.2f} -> {new:.2f} ns ({gain:+.1%})"


# Patches of rejected rounds, normalized (repeat_key) -> "round i: reason". lab-c3: Claude
# re-applied the round-1 patch (A/B better, whole tile worse) in rounds 2-4 word for word, and
# the loop measured it three more times (~13 min each).
TRIED: dict[str, str] = {}


def repeat_key(patch: str) -> str:
    return "\n".join(l for l in patch.splitlines() if not l.startswith("index ")).strip()


def repeat_note(k: int, seen: str, patch: str) -> str:
    """Note of a session that follows one which re-made a patch already rejected."""
    return "\n".join([
        f"## This session: a different edit (session {k + 1})", "",
        f"Session {k} made exactly the patch that was already measured and rejected ({seen}). The loop "
        "did not measure it again and put the tree back at the current best. Measuring the same patch "
        "gives the same numbers: make a **different** change, either to another part of the target or a "
        "different restructuring.", "", "The rejected patch:", "", "```diff", patch[-6000:], "```"])


def new_round(i: int) -> dict:
    return {"accepted": False, "reason": "", "synth": None, "rtl": None, "patch": "",
            "isa_pass": "", "bench_cycles_match": "", "build_fixes": 0}


def run_round(a, i: int, out: Path, llm, fix_llm, elfs, snap, base, best, history) -> dict:
    """One round (one agent call). Returns {"accepted", "reason", "synth", "rtl", "patch", ...}."""
    d = out / "runs" / f"iter{i}"
    r = new_round(i)

    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    get(N.stage_for_agent.chia_remote(stage_files(best, history)))
    prompt = render_prompt(a, i, best, history, chia_target(best) if a.chia_mode else "")
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
        # --chia-mode: a session that gives up without any edit (chia-fmax-r1 round 5: one turn,
        # 7 s, no synth_ab) gets a new session told so, at most --empty-retries times
        for k in range(1, (a.empty_retries if a.chia_mode else 0) + 1):
            if reason or get(N.collect_diff.chia_remote(snap["rocket_head"], snap["outside"]))["patch"] != best["patch"]:
                break
            print(f"round {i}: agent không sửa gì, gọi phiên mới (lần {k}/{a.empty_retries})", flush=True)
            rprompt = prompt + EMPTY_RETRY.format(answer=last_response(d)[-1500:])
            write(d / f"retry{k}_prompt.md", rprompt)
            reason = call_agent(a, llm, rprompt, f"{tag}_retry{k}", d, f"retry{k}", best, a.ab_tool)
    r["agent_min"] = (time.time() - t_agent) / 60

    diff = after_agent(out, d, i, snap, best, r, reason)
    if diff is None:
        return r
    art, diff = build_with_fixes(a, i, out, d, fix_llm, snap, best, r, diff)
    if art is None:
        return r
    rtl = tile_rtl(art, r)
    if rtl is None:
        return r
    r = gate_and_judge(a, d, art, rtl, elfs, base, best, r)
    k = 0
    while r.get("functional_fail") and k < a.debug_retries:
        # --chia-mode, as debug_failure in CHIA's timing_opt: the edit builds but fails a test
        k += 1
        r["debug_fixes"] = k
        fails = r.pop("functional_fail")
        print(f"round {i}: test lỗi, gọi agent debug (lần {k}/{a.debug_retries}): {r['reason'][:200]}", flush=True)
        dprompt = render_debug_prompt(a, i, k, fails, r["patch"], last_response(d))
        write(d / f"debug{k}_prompt.md", dprompt)
        debug_llm = make_llm(a, a.fix_model or a.model, SYSTEM_MESSAGE_DEBUG, a.fix_timeout, None)
        reason = call_agent(a, debug_llm, dprompt, f"agent_{a.run_name}_iter{i}_debug{k}_llm", d, f"debug{k}",
                            best, a.ab_tool)
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
        r = gate_and_judge(a, d / f"debug{k}", art, rtl, elfs, base, best, r)
    r.pop("functional_fail", None)
    return r


def chia_target(best: dict) -> str:
    """--chia-mode: the worst path located in the Verilog and the Chisel (as for --loop-ab), a
    starting point for the agent, which still picks what to work on."""
    target = N.pick_target(best["rtl"], best["synth"])
    target["report"] = first_path(best["synth"].paths)
    target["ends"], target["sources"] = N.path_sources(best["rtl"], best["synth"], best["patch"])
    classes = get(N.find_scala_classes.chia_remote([m for _, m in target["chain"]]))
    return target_text(target, classes)


# ---------------------------------------------------------------------------
# --loop-ab rounds: the loop picks the target and runs the A/B, the agent edits
# ---------------------------------------------------------------------------

def decision_path(rtl, target: dict) -> str:
    """Instance whose endpoints decide the A/B: the instance where the best's worst path
    ends, when it lies inside the target (a black box -> its parent); else the target.
    lab-c5: target fpuOpt (divSqrt_2 -> regfile_ext) = the whole A/B module, whose own worst
    path (inside divSqrt, -0.73 ns) is not the tile's (-1.13 ns, ending in regfile_ext), so
    the module delta could not see an edit of the tile's path."""
    path, end = target["path"], target["end_path"]
    paths = N.instance_paths(rtl)
    while end and paths.get(end) in rtl.blackboxes:
        end = end.rpartition(".")[0]
    return end if path and end.startswith(path + ".") else path


def target_text(target: dict, classes: dict) -> str:
    lines = [f"- Worst path of the current best: `{target['crit']}`",
             f"- It starts in instance `{target['start_path'] or N.TOP}` and ends in "
             f"`{target['end_path'] or N.TOP}`.",
             f"- **Target: instance `{target['path'] or N.TOP}` = module `{target['module']}`** "
             "(the smallest instance holding both ends).",
             *([f"- **The A/B is decided by the worst slack of the endpoints inside `{target['decide'] or N.TOP}`**"
                + (" (where the worst path ends), not by the worst path of the whole synthesized module."
                   if target["decide"] != target["path"] else ".")] if "decide" in target else []),
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
        CONFIG_DESC=N.CONFIG, PERIOD_NS=a.period_ns, BEST_NAME=best["name"],
        BEST_SLACK=fmt(s.worst_slack_ns, 4), BEST_FMAX=fmt(s.fmax_mhz, 2),
        CRIT=f"{s.crit_start} -> {s.crit_end}", ENDPOINTS=s.families, STAGE_DIR=N.STAGE_DIR,
        TARGET=target_text(target, classes), SESSION_NOTE=note, BUDGET_SOFT=a.edit_budget[0],
        BUDGET_READONLY=a.edit_budget[1], BUDGET_HARD=a.edit_budget[2],
        HISTORY="\n\n".join(history) or "(chưa có vòng nào trước)", **tool_text(a))


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


def revert_note(k: int, ab_note: str, response: str) -> str:
    """Note of a session that follows one which undid the round's edit and made no new one."""
    lines = [f"## This session: a different edit (session {k + 1})", "",
             f"Session {k} undid the round's edit and made no new one, so the tree is back at the current best. "
             "Undoing is not an answer: make a **different** restructuring of the same target now. Do not "
             "repeat the undone edit."]
    if response.strip():
        lines += ["", "Its answer:", "", "```", response.strip()[-3000:], "```"]
    if ab_note:
        lines += ["", "The undone edit and its A/B:", "", ab_note.split("\n", 1)[1].strip()]
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
        "restructuring of the same target in this same session. Do not end the session with the edit undone "
        "and nothing new: that wastes the session.",
        "", "Your edits of this round so far:", "", "```diff", rdiff[-8000:], "```"])


def tile_note(k: int, ab_text: str, last: str, best: dict, tile, ab_dir: str, rdiff: str) -> str:
    """Note of a session that follows one whose edit won its A/B but lost on the whole tile."""
    b = best["synth"]
    return "\n".join([
        f"## This session: the whole tile rejected your edit (session {k + 1})", "",
        "Your edit won the A/B of the target:", "", "```", ab_text, "```", "",
        "but the round is judged on the full RocketTile synth, and that rejected it:", "",
        f"- with your edit: worst slack {tile.worst_slack_ns:.4f} ns, TNS {tile.tns_ns}, worst path "
        f"`{tile.crit_start} -> {tile.crit_end}`",
        f"- current best ({best['name']}): worst slack {b.worst_slack_ns:.4f} ns, TNS {b.tns_ns}, worst path "
        f"`{b.crit_start} -> {b.crit_end}`",
        f"- loop: {last}", "",
        "The A/B synthesizes the module alone; the tile is flattened and ABC maps all of it at once, so the "
        "same logic can come out slower there. "
        f"`{ab_dir}/tile_paths.rpt` has the tile's worst paths with your edit, `{ab_dir}/tile_families.txt` "
        f"its endpoint families; `{ab_dir}/round.diff` your edits of this round.", "",
        "If the tile's worst path runs through logic you changed, shorten it further: the tile's numbers are "
        "what count, keep the logic depth low (fewer levels, not just a different form). If it is elsewhere, "
        "check in the families whether your target got better at all in the tile. This exact patch will not "
        "be measured again: change it or undo it and try a different restructuring.",
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
    target["decide"] = decision_path(best["rtl"], target)
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
        if diff is None and k < a.ab_refines and r["patch"] == best["patch"] and not violation:
            # the session undid the round's edit and made no new one (lab-c1, session 2): the
            # next session starts again from the best, with the losing A/B of the undone edit
            print(f"round {i}: phiên sửa {k + 1} hoàn tác bản sửa và không sửa mới", flush=True)
            resp = d / f"edit{k}_response.md"
            note = revert_note(k + 1, ab_note, resp.read_text() if resp.exists() else "")
            r["reason"] = ""
            continue
        if diff is None:
            if k:
                r["reason"] += f" (A/B trước đó: {last})"
            return r
        if repeat_key(r["patch"]) in TRIED:
            note = repeat_session(a, i, k, snap, best, r)
            if note is None:
                return r
            continue
        art, diff = build_with_fixes(a, i, out, d, fix_llm, snap, best, r, diff, f"_e{k}")
        if art is None:
            return r
        if repeat_key(r["patch"]) in TRIED:          # the build fix made it the rejected patch
            note = repeat_session(a, i, k, snap, best, r)
            if note is None:
                return r
            continue
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
                            parent_cache, target["decide"])
            write(d / f"ab{k}.txt", ab["text"])
            print(f"round {i}, phiên {k + 1}: {ab['text']}", flush=True)
            r["ab_delta_ns"] = "" if ab["delta"] is None else ab["delta"]
            last = (f"{ab['module']}: delta {ab['delta']:+.3f} ns" if ab["delta"] is not None
                    else f"{ab['module']}: synth lỗi ({ab['c'].get('error') or ab['p'].get('error')})")
            if ab["delta"] is not None and ab["delta"] > a.ab_min_gain:
                r["ab_result"] = last
                r = gate_and_judge(a, d, art, rtl, elfs, base, best, r,
                                   synth=ab["child"] if ab["whole"] else None)
                if not r.pop("tile_reject", False) or k >= a.ab_refines:
                    return r
                # lab-c3/c4: the target won its A/B but the whole tile (flattened, one ABC run)
                # rejected it; the next session gets the tile's numbers instead of ending the round
                tile = r["synth"]
                TRIED.setdefault(repeat_key(r["patch"]), f"vòng {i} phiên {k + 1}: {r['reason'][:400]}")
                r["tile_rejects"] = r.get("tile_rejects", 0) + 1
                print(f"round {i}, phiên {k + 1}: A/B thắng nhưng cả tile bị loại ({r['reason'][:300]}); "
                      "sang phiên sau", flush=True)
                write(d / f"edit{k}_tile.txt", r["reason"])
                last = f"{last}, cả tile: {r['reason'][:200]}"
                r.update(reason="", synth=None, rtl=None, ab_result="")
                stage = {**ab["files"], "round.diff": rdiff, "tile_paths.rpt": tile.paths or "",
                         "tile_families.txt": tile.families or "",
                         "child_verilog.sv": AB.module_texts(rtl).get(ab["module"], "")}
                get(N.stage_for_agent.chia_remote(stage, ab_dir))
                note = ab_note = tile_note(k + 1, ab["text"], last, best, tile, ab_dir, rdiff)
                continue
            ab_text = ab["text"]
            stage = {**ab["files"], "round.diff": rdiff,
                     "child_verilog.sv": AB.module_texts(rtl).get(ab["module"], "")}
        if mods:     # lab-c4: a session went back to an earlier session's losing edit
            TRIED.setdefault(repeat_key(r["patch"]), f"vòng {i} phiên {k + 1}: A/B {last}")
        if k < a.ab_refines:
            get(N.stage_for_agent.chia_remote(stage, ab_dir))
            note = ab_note = session_note(k + 1, ab_text, ab_dir, rdiff)
    r["reason"] = f"A/B không tốt hơn {a.ab_min_gain} ns sau {a.ab_refines + 1} phiên sửa (lần cuối: {last})"
    return r


def repeat_session(a, i: int, k: int, snap, best, r) -> str | None:
    """A session re-made a rejected patch: back to the best, and the note of the next
    session (None, r["reason"] set, after the last one)."""
    seen = TRIED[repeat_key(r["patch"])]
    print(f"round {i}: phiên sửa {k + 1} lặp lại patch đã loại ({seen[:120]}), không đo lại", flush=True)
    r["repeats"] = r.get("repeats", 0) + 1
    patch = r["patch"]
    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    r["patch"] = best["patch"]
    if k >= a.ab_refines:
        r["reason"] = f"phiên sửa {k + 1} lặp lại patch đã loại ({seen[:300]}); hết {a.ab_refines + 1} phiên"
        return None
    return repeat_note(k + 1, seen, patch)


def last_response(d: Path) -> str:
    """Summary of the round's last agent answer (agent_response.md, retryN_ or editN_response.md)."""
    files = sorted(d.glob("edit*_response.md"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    files = files or sorted(d.glob("retry*_response.md"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
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
    if r.get("ipc_loss_pct") != "" and r.get("ipc_loss_pct") is not None:
        lines.append(f"Cycle so với baseline: {r['ipc_loss_pct']} %; hiệu năng so với bản tốt nhất: {r['perf_gain_pct']} %")
    if r.get("debug_fixes"):
        lines.append(f"Test lỗi, agent debug {r['debug_fixes']} lần.")
    if r.get("build_fixes"):
        lines.append(f"Build lỗi, agent sửa lỗi build {r['build_fixes']} lần.")
    if resp_summary:
        lines.append(f"Tóm tắt của agent: {resp_summary[:800]}")
    patch = r.get("round_diff") or r["patch"]
    if not r["accepted"] and r.get("ab_result") and s is not None and s.success:
        lines.append("A/B của mục tiêu tốt hơn nhưng cả RocketTile bị loại. Patch này đã đo rồi: nộp lại y "
                     "nguyên sẽ bị loop bỏ qua, không đo lại. Hãy sửa khác (chỗ khác của mục tiêu, hoặc cách khác).")
    if not r["accepted"] and patch:
        lines.append("Patch đã bị loại (đã revert, đừng lặp lại y nguyên):\n```diff\n" + patch[:4000] + "\n```")
    return "\n".join(lines)


def make_llm(a, model: str, system_message: str, timeout: int, steps: int | None):
    """The agent of --agent: SalvageOpenCodeLLM, or SalvageClaudeLLM (--effort; `steps` becomes
    --max-turns steps + 10, a safety net behind BudgetBashTool's HARD limit)."""
    if a.agent == "claude":
        return N.SalvageClaudeLLM(model=model, system_message=system_message, timeout_seconds=timeout,
                                  retries=1, effort=a.effort, max_turns=steps + 10 if steps else None)
    oc_args = ["--print-logs", "--log-level", a.opencode_logs] if a.opencode_logs else None
    return N.SalvageOpenCodeLLM(model=model, system_message=system_message, timeout_seconds=timeout, retries=1,
                                builtin_tools=a.builtin_tools, extra_cli_args=oc_args, max_steps=steps)


def tool_text(a) -> dict:
    """How the prompts name the shell tool (each CLI names MCP tools its own way) and what they
    say about the CLI's own tools."""
    ab = {f"AB_{k.upper()}": ("mcp__synth_ab__" if a.agent == "claude" else "synth_ab_") + m
          for k, m in (("rebuild", "rebuild_verilog"), ("list", "list_modules"), ("start", "start_ab"),
                       ("result", "ab_result"))}
    if a.agent == "claude":
        return {"BASH_TOOL": "mcp__chipyard_bash__chipyard_bash_run_command",
                "OTHER_TOOLS": " You have no other tool.", **ab}
    return {**ab, "BASH_TOOL": "chipyard_bash_chipyard_bash_run_command",
            "OTHER_TOOLS": " opencode's own `bash`, `read`, `edit`, `glob`, ... (if you see them) run in another "
                           "container that has no Chipyard: nothing they read or write is the design."}


def run(a, out: Path):
    csv_path = out / "results.csv"
    elfs = get(N.load_test_elfs.chia_remote(tuple(a.extra_bench)))
    print(f"ELF: {len(elfs['isa'])} ISA tests, benchmarks {list(elfs['bench'])}", flush=True)
    snap = get(N.tree_snapshot.chia_remote())
    write(out / "runs" / "tree_snapshot.json", json.dumps(snap, indent=2))

    base = ensure_baseline(a, out, elfs, snap)
    if a.baseline_only:
        return

    llm = make_llm(a, a.model, SYSTEM_MESSAGE_CHIA if a.chia_mode else SYSTEM_MESSAGE_AB if a.ab_tool else SYSTEM_MESSAGE,
                   a.edit_timeout if a.loop_ab else a.llm_timeout, (a.edit_steps or None) if a.loop_ab else None)
    fix_llm = make_llm(a, a.fix_model or a.model, SYSTEM_MESSAGE_FIX, a.fix_timeout, None)
    best = {"name": "baseline", "patch": "", "synth": base["synth"], "rtl": base["rtl"]}
    if a.start_patch:
        best = ensure_start(a, out, snap, base, elfs)
    start = top = best          # top: the accepted point with the best worst slack (Fmax)
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
                              "ab_delta_ns": r.get("ab_delta_ns", ""), "ab_sessions": r.get("ab_sessions", ""),
                              "ipc_loss_pct": r.get("ipc_loss_pct", ""), "perf_gain_pct": r.get("perf_gain_pct", "")})
        print(f"round {i}: {'ACCEPT' if r['accepted'] else 'REJECT'} ({r['minutes']:.1f} min) - "
              f"{r['reason'][:400]}", flush=True)
        if not r["accepted"] and r.get("synth") is not None and r["patch"] and r["patch"] != best["patch"]:
            TRIED.setdefault(repeat_key(r["patch"]), f"vòng {i}: {r['reason'][:400]}")
        if r["accepted"]:
            peak = max(best.get("peak_wns", best["synth"].worst_slack_ns), r["synth"].worst_slack_ns)
            best = {"name": f"iter{i}", "patch": r["patch"], "synth": r["synth"], "rtl": r["rtl"],
                    "peak_wns": peak, **({"cyc": r["cyc"]} if "cyc" in r else {})}
            print_synth(f"new best iter{i}", r["synth"])
            if r["synth"].worst_slack_ns > top["synth"].worst_slack_ns:
                top = best
        history.append(history_entry(i, r, resp_summary))

    # best.patch: the last accepted point (rounds accepted on TNS may lower worst slack);
    # best_wns.patch: the accepted point with the best worst slack, i.e. the Fmax to report
    write(out / "patches" / "best.patch", best["patch"])
    write(out / "patches" / "best_wns.patch", top["patch"])
    write(out / "best.json", json.dumps({"best": best["name"], **synth_row(best["synth"]),
                                         **({"cycle_cost": round(best.get("cyc", 1.0), 5),
                                             "perf": round(perf(best), 3)} if a.chia_mode else {}),
                                         "best_wns": {"name": top["name"], **synth_row(top["synth"])}}, indent=2))
    get(N.reset_rocket.chia_remote(snap["rocket_head"], best["patch"]))
    print(f"\nbest: {best['name']} | " + json.dumps(synth_row(best["synth"]), ensure_ascii=False))
    if top is not best:
        print(f"best_wns: {top['name']} | " + json.dumps(synth_row(top["synth"]), ensure_ascii=False))
    if a.equiv:
        # against the start point (--start-patch) or the baseline
        for p in ([best] if top is best else [best, top]):
            if p is not start:
                run_equiv(a, out, start["rtl"], p["rtl"], p["name"], start["name"],
                          "equiv" if p is best else "equiv_best_wns")


# ---------------------------------------------------------------------------
# equivalence: best patch vs baseline
# ---------------------------------------------------------------------------

def rtl_sources(rtl) -> dict:
    """All module texts of a TileRTL, SRAM models included (named bb_*)."""
    return {**rtl.files, **{f"bb_{m}.v": t for m, t in rtl.blackboxes.items()}}


def run_equiv(a, out: Path, base_rtl, best_rtl, label: str, ref: str = "baseline", sub: str = "equiv") -> dict:
    d = out / "runs" / sub
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
