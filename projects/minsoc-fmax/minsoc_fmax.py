"""minsoc-fmax: driver. Mốc 1: --baseline-only. Mốc 2: vòng agent (--phase fpga, không --baseline-only).

  --phase sim    build Verilator MinSocFmaxConfig trên fork, chạy gate (ISA rv64*-p-* + 8 benchmark, lặp benchmark
                 với seed 7), ghi test fail sẵn (known_fail.txt) và cycle baseline (gate.json).
  --phase fpga   elaborate MinSocArty100TF<f>Config, Vivado: synth 1 lần + --repeat lần place/route (đo nhiễu),
                 bitstream ở lần đầu. Ghi WNS/TNS/Fmax/LUT/FF/BRAM/DSP, top 10 path, ngưỡng nhận đề xuất.

  cd ~/CHIA-workflow/projects/minsoc-fmax
  chia job submit --working-dir . -- python minsoc_fmax.py --phase sim  --baseline-only --run m1-sim
  chia job submit --working-dir . -- python minsoc_fmax.py --phase fpga --baseline-only --run m1-fpga10 --period-ns 10
  chia job submit --working-dir . -- python minsoc_fmax.py --phase fpga --run m2-f100a --period-ns 10 --rounds 5

Vòng agent (Mốc 2), mỗi lượt: checkpoint fork -> agent Claude Code sửa Chisel (chỉ qua chipyard_bash) -> kiểm phạm vi
-> elaborate bản Arty (lỗi compile: agent sửa, tối đa --fix-tries) -> build Verilator MinSocFmaxFpgaConfig + gate
-> Vivado synth + place/route Explore và ExtraTimingOpt, lấy WNS tốt nhất -> nhận (commit nhánh minsoc-fmax/agent
của fork) nếu gate pass, geomean ≤ ngân sách và WNS tốt hơn best > --accept-ns; không thì revert.

Kết quả: ~/minsoc-fmax/<run>/ (summary.md, results.csv, gate.json / fpga.json, reports/). Cache ~/chia_cache_minsoc,
namespace Ray minsoc_fmax. Một Vivado một lúc; không chạy song song với job khác sửa fork.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
from pathlib import Path

import ray
from chia.base.ChiaFunction import get

import minsoc_nodes as N

HERE = Path(__file__).resolve().parent
EXP_YAML = HERE / "exp.yaml"


def args_():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=["sim", "fpga", "asic"], required=True)
    p.add_argument("--baseline-only", action="store_true", help="Mốc 1: chỉ đo baseline (bắt buộc ở Mốc 1)")
    p.add_argument("--run", required=True, help="tên run, kết quả ở <out-dir>/<run>")
    p.add_argument("--out-dir", default="~/minsoc-fmax")
    p.add_argument("--cache-dir", default="~/chia_cache_minsoc")
    p.add_argument("--flush-cache", action="store_true")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=7200)
    # sim
    p.add_argument("--repeat-seed", type=int, default=7)
    # fpga
    p.add_argument("--period-ns", type=float, default=10.0, choices=sorted(N.FPGA_CONFIGS))
    p.add_argument("--repeat", type=int, default=3,
                   help="số lần place/route từ cùng netlist synth (0,1: Explore; >=2: directive place khác)")
    p.add_argument("--fpga-mem", choices=["bram", "simdram"], default="bram",
                   help="bram: thay SimDRAM bằng fpga/SimDRAM_fpga.v; simdram: giữ nguyên flow fork (không tổng hợp được)")
    p.add_argument("--no-bitstream", action="store_true")
    # Mốc 2: vòng agent
    p.add_argument("--rounds", type=int, default=5, help="số lượt agent tối đa của batch")
    p.add_argument("--resume", action="store_true",
                   help="chạy tiếp batch --run đã dừng: đọc history.json, bỏ r0, đánh số tiếp (fork phải sạch)")
    p.add_argument("--baseline-run", default="m1-sim", help="run sim Mốc 1: known_fail.txt và geomean ngân sách")
    p.add_argument("--cycle-budget", type=float, default=None, help="mặc định geomean baseline × 1,03")
    p.add_argument("--accept-ns", type=float, default=0.5, help="ngưỡng nhận: WNS phải tốt hơn best quá số này")
    p.add_argument("--directives", default="Explore,ExtraTimingOpt", help="directive place_design mỗi lượt")
    p.add_argument("--model", default="opus")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--agent-timeout", type=int, default=1800, help="giây cho một lần gọi agent")
    p.add_argument("--fix-tries", type=int, default=2, help="số lần agent sửa lỗi compile trong một lượt")
    a = p.parse_args()
    if a.phase == "asic":
        p.error("pha asic chưa làm (đề xuất PDK/flow trước)")
    if not a.baseline_only and a.phase != "fpga":
        p.error("vòng agent chỉ có ở --phase fpga")
    a.out_dir = os.path.expanduser(a.out_dir)
    a.cache_dir = os.path.expanduser(a.cache_dir)
    return a


def write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def todict(x):
    return dataclasses.asdict(x) if dataclasses.is_dataclass(x) else x


def geomean(xs):
    xs = [x for x in xs if x]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else None


def append_csv(path: Path, row: dict):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def snapshot(out: Path) -> dict:
    st = get(N.fork_state.chia_remote())
    write(out / "fork_state.txt", f"fork {st['fork']}\nhash {st['hash']}\n{st['heads']}\ndirty {st['dirty']}\n")
    print(f"fork {st['fork']} hash={st['hash']} dirty={st['dirty']}", flush=True)
    if st["own_missing"]:
        raise SystemExit(f"thiếu Config trong fork: {st['own_missing']} (chạy install.sh)")
    if st["dirty"]:
        write(out / "fork.patch", st["patch"])
        raise SystemExit(f"baseline cần fork sạch, đang sửa: {st['dirty']} (xem {out}/fork.patch)")
    return st


# ---------------------------------------------------------------------------
# sim
# ---------------------------------------------------------------------------

def phase_sim(a, out: Path, st: dict):
    from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
    tag = f"baseline_gate_{st['hash']}"
    t0 = time.time()
    cb = ChiselBuildNode(st["fork"], N.SIM_CONFIG, target=BuildTarget.VERILATOR, make_jobs=a.make_jobs,
                         timeout_seconds=a.build_timeout, clean=False, bundle_runtime_libs=True, name="minsoc-sim")
    art = get(cb.build.chia_remote(cb))
    write(out / "build.log", (art.stdout or "")[-200000:] + "\n--- stderr\n" + (art.stderr or "")[-100000:])
    if not art.success:
        raise SystemExit(f"build {N.SIM_CONFIG} lỗi, xem {out}/build.log")
    print(f"build {N.SIM_CONFIG} ok ({(time.time() - t0) / 60:.1f} phút)", flush=True)
    elfs = get(N.load_test_elfs.chia_remote())
    print(f"gate: {len(elfs['isa'])} ISA + {len(elfs['bench'])} benchmark", flush=True)
    gate = get(N.run_gate.chia_remote(art, elfs, repeat_seed=a.repeat_seed, _chia_tag=tag))
    g = todict(gate)
    write(out / "gate.json", json.dumps(g, indent=2))
    known = sorted(n for n, ok in g["isa"].items() if not ok)
    write(out / "known_fail.txt", "\n".join(known) + ("\n" if known else ""))
    write(out / "sim_fail.log", "\n\n".join(f"===== {n}\n{t}" for n, t in g["fail_logs"].items()))
    problems = [f"benchmark fail: {n}" for n, b in g["bench"].items() if not b["passed"]]
    problems += [f"{n}: mcycle đổi khi đổi seed ({b['mcycle']} vs {g['bench_repeat'][n]['mcycle']})"
                 for n, b in g["bench"].items()
                 if n in g["bench_repeat"] and g["bench_repeat"][n]["mcycle"] != b["mcycle"]]
    gm = geomean([b["mcycle"] for b in g["bench"].values()])
    lines = [f"# {a.run} — Simulate baseline ({N.SIM_CONFIG})", "",
             f"- fork hash `{st['hash']}`, tag `{tag}`, gate {g['seconds'] / 60:.1f} phút",
             f"- ISA: {sum(g['isa'].values())}/{len(g['isa'])} pass; fail sẵn (loại khỏi gate): {known or 'không'}",
             f"- trung bình nhân mcycle 8 benchmark: **{gm:.1f}**; ngân sách +3 % → ≤ {gm * 1.03:.1f}" if gm else "- không có mcycle",
             "", "| benchmark | pass | mcycle | minstret | mcycle (seed %d) |" % a.repeat_seed, "|---|---|---|---|---|"]
    for n, b in g["bench"].items():
        r = g["bench_repeat"].get(n, {})
        lines.append(f"| {n} | {b['passed']} | {b['mcycle']} | {b['minstret']} | {r.get('mcycle')} |")
        append_csv(out / "results.csv", {"phase": "sim", "name": "baseline", "hash": st["hash"], "test": n,
                                         "passed": b["passed"], "mcycle": b["mcycle"], "minstret": b["minstret"]})
    if problems:
        lines += ["", "## VẤN ĐỀ", *[f"- {x}" for x in problems]]
    write(out / "summary.md", "\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    if problems:
        raise SystemExit("baseline sim chưa dùng được (xem summary.md, sim_fail.log)")


# ---------------------------------------------------------------------------
# fpga
# ---------------------------------------------------------------------------

def phase_fpga(a, out: Path, st: dict):
    cfg = N.FPGA_CONFIGS[a.period_ns]
    simdram_v = (HERE / "fpga" / "SimDRAM_fpga.v").read_text()
    extra_xdc = (HERE / "fpga" / "minsoc_reset.xdc").read_text()
    vh = hashlib.sha256((simdram_v + extra_xdc + (HERE / "vivado_tcl.py").read_text()).encode()).hexdigest()[:8]
    tag = f"baseline_fpga_{st['hash']}_p{a.period_ns:g}_{a.fpga_mem}_n{a.repeat}_v{vh}"
    from chia.base.cache import get_active_cache
    if not get(get_active_cache().has.chia_remote(tag)):
        e = get(N.elaborate_fpga.chia_remote(cfg, jobs=min(a.make_jobs, 8)))
        write(out / "elaborate.json", json.dumps(e, indent=2))
        print(f"elaborate {cfg}: ok={e['ok']} ({e['seconds'] / 60:.1f} phút, {e['tries']} lần)", flush=True)
        if not e["ok"]:
            raise SystemExit(f"elaborate lỗi:\n{e['tail']}")
    print(f"vivado {cfg} (tag {tag}): synth + {a.repeat} lần place/route ...", flush=True)
    v = get(N.vivado_impl.chia_remote(cfg, a.run, simdram_v, fpga_mem=a.fpga_mem, n_impl=a.repeat,
                                      write_bit=not a.no_bitstream, extra_xdc=extra_xdc, _chia_tag=tag))
    rep = out / "reports"
    for t, im in v.get("impls", {}).items():
        for k, txt in im.pop("reports", {}).items():
            write(rep / f"{k}_{t}.rpt", txt)
    write(out / "fpga.json", json.dumps(v, indent=2))
    lines = [f"# {a.run} — FPGA baseline {cfg} ({a.period_ns:g} ns, mem={a.fpga_mem})", "",
             f"- part `{v.get('part')}`, board `{v.get('board_part')}`, fork hash `{st['hash']}`, tag `{tag}`",
             f"- Vivado {v.get('seconds', 0) / 60:.1f} phút, ok={v.get('ok')}, lỗi={v.get('error')}",
             f"- bitstream: {v.get('bit') or ('LỖI ' + str(v.get('bit_fail')) if v.get('bit_fail') else 'không')}",
             f"- synth: WNS {v.get('synth', {}).get('wns')} ns, LUT {v.get('synth', {}).get('lut')}, "
             f"FF {v.get('synth', {}).get('ff')}, BRAM {v.get('synth', {}).get('bram')}, DSP {v.get('synth', {}).get('dsp')}",
             "", "| impl | place directive | WNS (ns) | TNS (ns) | WHS (ns) | clock SoC | Fmax (MHz) | LUT | FF | BRAM | DSP |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    wns = []
    for t, im in v.get("impls", {}).items():
        c = im.get("dut_clock") or {}
        fm = im.get("fmax_mhz")
        lines.append(f"| {t} | {im['directive']} | {im['wns']} | {im['tns']} | {im['whs']} | {c.get('clock')} "
                     f"{c.get('period')} ns | {fm and round(fm, 2)} | {im['lut']} | {im['ff']} | {im['bram']} | {im['dsp']} |")
        if im["wns"] is not None:
            wns.append(im["wns"])
        append_csv(out / "results.csv", {"phase": "fpga", "name": "baseline", "hash": st["hash"], "config": cfg,
                                         "period_ns": a.period_ns, "impl": t, "directive": im["directive"],
                                         "wns": im["wns"], "tns": im["tns"], "fmax_mhz": fm, "lut": im["lut"],
                                         "ff": im["ff"], "bram": im["bram"], "dsp": im["dsp"]})
    if wns:
        spread = max(wns) - min(wns)
        same = len(wns) > 1 and wns[0] == wns[1]
        lines += ["", f"**Nhiễu P&R:** WNS từ {min(wns)} đến {max(wns)} ns (chênh {spread:.3f} ns). "
                      f"impl0 = impl1 (cùng directive): {'có (tất định)' if same else 'KHÔNG'}.",
                  f"**Ngưỡng nhận đề xuất:** cải thiện WNS > {max(0.05, 2 * spread):.3f} ns (max(0,05; 2 × chênh))."]
    first = next(iter(v.get("impls", {}).values()), None)
    if first:
        lines += ["", "## Top 10 path (impl0)", "", "| # | slack | levels | delay | group | start | end |",
                  "|---|---|---|---|---|---|---|"]
        for i, p in enumerate(first["paths"]):
            lines.append(f"| {i} | {p['slack']} | {p['levels']} | {p['delay']} | {p['group']} | `{p['start']}` | `{p['end']}` |")
    if v.get("errors"):
        lines += ["", "## ERROR / CRITICAL WARNING (60 dòng đầu)", "```", *v["errors"], "```"]
    write(out / "summary.md", "\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    if not v.get("ok"):
        write(out / "vivado_tail.log", v.get("log_tail", ""))
        raise SystemExit(f"Vivado chưa xong, xem {out}/vivado_tail.log và {v.get('run_dir')}/vivado.log")


# ---------------------------------------------------------------------------
# Mốc 2: vòng agent
# ---------------------------------------------------------------------------

BASH_TOOL = "mcp__chipyard_bash__chipyard_bash_run_command"
LIMIT_RE = re.compile(r"hit your limit|RateLimitError|rate_limit|usage limit")


def fill(name: str, **kw) -> str:
    t = (HERE / "prompts" / name).read_text()
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


def vivado_hash() -> tuple[str, str, str]:
    simdram_v = (HERE / "fpga" / "SimDRAM_fpga.v").read_text()
    extra_xdc = (HERE / "fpga" / "minsoc_reset.xdc").read_text()
    vh = hashlib.sha256((simdram_v + extra_xdc + (HERE / "vivado_tcl.py").read_text()).encode()).hexdigest()[:8]
    return simdram_v, extra_xdc, vh


def evaluate(a, d: Path, st: dict, label: str, gate_cfg: dict, elaborated: bool) -> dict:
    """Gate (Verilator MinSocFmaxFpgaConfig) rồi Vivado nếu gate pass. Ghi vào thư mục d."""
    from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
    ev = {"label": label, "hash": st["hash"], "rev": st["rev"], "gate_ok": False, "problems": [], "wns": None}
    key = f"{st['hash']}_{st['rev']}"
    t0 = time.time()
    cb = ChiselBuildNode(st["fork"], N.SIM_FPGA_CONFIG, target=BuildTarget.VERILATOR, make_jobs=a.make_jobs,
                         timeout_seconds=a.build_timeout, clean=False, bundle_runtime_libs=True, name="minsoc-m2-sim")
    art = get(cb.build.chia_remote(cb))
    if not art.success:
        write(d / "sim_build.log", (art.stdout or "")[-100000:] + "\n--- stderr\n" + (art.stderr or "")[-50000:])
        ev["problems"].append(f"build Verilator {N.SIM_FPGA_CONFIG} lỗi (sim_build.log)")
        return ev
    print(f"  [{label}] build sim ok ({(time.time() - t0) / 60:.1f} phút), chạy gate ...", flush=True)
    g = todict(get(N.run_gate.chia_remote(art, gate_cfg["elfs"], repeat_seed=None, _chia_tag=f"m2_gate_{key}")))
    write(d / "gate.json", json.dumps({k: v for k, v in g.items() if k != "fail_logs"}, indent=2))
    if g["fail_logs"]:
        write(d / "sim_fail.log", "\n\n".join(f"===== {n}\n{t}" for n, t in g["fail_logs"].items()))
    new_fail = sorted(n for n, ok in g["isa"].items() if not ok and n not in gate_cfg["known"])
    bad_bench = sorted(n for n, b in g["bench"].items() if not b["passed"])
    gm = geomean([b["mcycle"] for b in g["bench"].values()])
    ev.update(geomean=gm, isa_new_fail=new_fail, bench_fail=bad_bench,
              mcycle={n: b["mcycle"] for n, b in g["bench"].items()})
    if new_fail:
        ev["problems"].append(f"ISA fail mới: {new_fail}")
    if bad_bench:
        ev["problems"].append(f"benchmark fail: {bad_bench}")
    if gm is None or gm > gate_cfg["budget"]:
        ev["problems"].append(f"geomean mcycle {gm and round(gm, 1)} > ngân sách {gate_cfg['budget']:.1f}")
    ev["gate_ok"] = not ev["problems"]
    print(f"  [{label}] gate ok={ev['gate_ok']} geomean={gm and round(gm, 1)} {ev['problems']}", flush=True)
    if not ev["gate_ok"]:
        return ev
    cfg = N.FPGA_CONFIGS[a.period_ns]
    simdram_v, extra_xdc, vh = vivado_hash()
    dirs = [x.strip() for x in a.directives.split(",") if x.strip()]
    tag = f"m2_fpga_{key}_p{a.period_ns:g}_{a.fpga_mem}_{'-'.join(dirs)}_v{vh}"
    from chia.base.cache import get_active_cache
    if not elaborated and not get(get_active_cache().has.chia_remote(tag)):
        e = get(N.elaborate_fpga.chia_remote(cfg, jobs=min(a.make_jobs, 8)))
        if not e["ok"]:
            ev["problems"].append("elaborate lỗi")
            write(d / "elaborate_tail.log", e["tail"])
            ev["gate_ok"] = False
            return ev
    t0 = time.time()
    print(f"  [{label}] vivado {cfg} ({', '.join(dirs)}) ...", flush=True)
    v = get(N.vivado_impl.chia_remote(cfg, f"{a.run}-{label}", simdram_v, fpga_mem=a.fpga_mem, n_impl=len(dirs),
                                      write_bit=False, extra_xdc=extra_xdc, directives=dirs, _chia_tag=tag))
    for t, im in v.get("impls", {}).items():
        for k, txt in im.pop("reports", {}).items():
            if k in ("timing", "paths", "util", "util_hier"):
                write(d / "reports" / f"{k}_{t}.rpt", txt)
    write(d / "fpga.json", json.dumps(v, indent=2))
    impls = {t: im for t, im in v.get("impls", {}).items() if im.get("wns") is not None}
    if not v.get("ok") or not impls:
        ev["problems"].append(f"Vivado lỗi: {v.get('error')} (xem {v.get('run_dir')}/vivado.log)")
        write(d / "vivado_tail.log", v.get("log_tail", ""))
        return ev
    bt, bi = max(impls.items(), key=lambda kv: kv[1]["wns"])
    ev.update(wns=bi["wns"], tns=bi["tns"], whs=bi["whs"], fmax=bi.get("fmax_mhz"), impl=bt,
              directive=bi["directive"], lut=bi["lut"], ff=bi["ff"], bram=bi["bram"], dsp=bi["dsp"],
              paths=bi["paths"], run_dir=v["run_dir"], vivado_min=round((time.time() - t0) / 60, 1),
              all_wns={t: im["wns"] for t, im in impls.items()})
    print(f"  [{label}] WNS {ev['wns']} ns ({bt} {ev['directive']}), Fmax {ev['fmax'] and round(ev['fmax'], 2)} MHz",
          flush=True)
    return ev


def make_llm(a):
    cls = N.make_llm_class()
    return cls(model=a.model, system_message=(HERE / "prompts" / "system.md").read_text(),
               timeout_seconds=a.agent_timeout, retries=1, effort=a.effort)


def call_agent(llm, prompt: str, d: Path, prefix: str, fork: str) -> str:
    """Một lần gọi agent với tool chipyard_bash. Trả về "" hoặc lý do lỗi."""
    from chia.base.tools.BashTool import BashTool
    get(N.read_text_claude.chia_remote(N.LLM_PARTIAL_FILE, True))
    write(d / f"{prefix}_prompt.md", prompt)
    tool = BashTool("chipyard_bash", fork, timeout_seconds=120, task_options={"resources": {"chipyard": 1}})
    resp, reason = None, ""
    try:
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=[tool]))
    except Exception as e:                                           # noqa: BLE001
        write(d / f"{prefix}_error.txt", traceback.format_exc())
        reason = f"LLM lỗi: {type(e).__name__}: {e}"[:500]
    finally:
        tool.stop()                      # trả slot chipyard trước khi build
    if resp is not None:
        write(d / f"{prefix}_response.md", resp.result or "")
        write(d / f"{prefix}_transcript.txt", resp.stream_result or "")
        if not resp.success:
            reason = reason or f"LLM không thành công (returncode {resp.returncode})"
    if resp is None or not resp.success:
        partial = get(N.read_text_claude.chia_remote(N.LLM_PARTIAL_FILE, True))
        if partial:
            write(d / f"{prefix}_transcript.txt", partial)
            if LIMIT_RE.search(partial[-3000:]):
                reason = "LIMIT: hết hạn mức Claude. " + reason
    return reason


def paths_table(paths: list) -> str:
    return "\n".join(f"| {p['slack']} | {p['levels']} | {p['delay']} | `{p['start']}` | `{p['end']}` |"
                     for p in paths[:10]) or "| (không có) | | | | |"


def history_md(history: list) -> str:
    if not history:
        return "(none yet)"
    out = []
    for h in history:
        out.append(f"### {h['label']}: {h['verdict']}\n- WNS {h.get('wns')} ns (best before: {h.get('best_before')} ns), "
                   f"geomean {h.get('geomean') and round(h['geomean'], 1)}\n- reason: {h['reason']}\n"
                   f"- files: {', '.join(h.get('files', [])) or '-'}\n- agent report:\n\n" +
                   "\n".join("  > " + x for x in (h.get("idea") or "").strip().splitlines()[:20]))
    return "\n\n".join(out)


def load_history(out: Path) -> list:
    """Lịch sử mọi batch Mốc 2 trước (cùng --out-dir): ~/minsoc-fmax/*/history.json."""
    hist = []
    for p in sorted(out.parent.glob("*/history.json"), key=lambda x: x.stat().st_mtime):
        if p.parent != out:
            hist += json.loads(p.read_text())
    return hist


def write_loop_summary(a, out: Path, start: dict, best: dict, rounds: list):
    lines = [f"# {a.run} — Mốc 2, vòng agent ({a.period_ns:g} ns, {N.FPGA_CONFIGS[a.period_ns]})", "",
             f"- model {a.model}, effort {a.effort}, tối đa {a.rounds} lượt, ngưỡng nhận {a.accept_ns} ns, "
             f"directive {a.directives}",
             f"- điểm xuất phát: WNS {start.get('wns')} ns, Fmax {start.get('fmax') and round(start['fmax'], 2)} MHz, "
             f"geomean {start.get('geomean') and round(start['geomean'], 1)}",
             f"- best hiện tại: **WNS {best.get('wns')} ns, Fmax {best.get('fmax') and round(best['fmax'], 2)} MHz** "
             f"({best.get('label')}), geomean {best.get('geomean') and round(best['geomean'], 1)}",
             "", "| lượt | kết quả | WNS (ns) | Fmax (MHz) | geomean | LUT | FF | lý do | phút | Vivado (phút) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rounds:
        lines.append(f"| {r['label']} | {r['verdict']} | {r.get('wns')} | {r.get('fmax') and round(r['fmax'], 2)} | "
                     f"{r.get('geomean') and round(r['geomean'], 1)} | {r.get('lut')} | {r.get('ff')} | "
                     f"{r['reason']} | {r.get('minutes')} | {r.get('vivado_min')} |")
    if best.get("paths"):
        lines += ["", f"## Top 10 path của best ({best.get('label')})", "", "| slack | levels | delay | start | end |",
                  "|---|---|---|---|---|", paths_table(best["paths"])]
    write(out / "summary.md", "\n".join(lines) + "\n")


def resume_state(out: Path, st: dict):
    """--resume: dựng lại (start, best, rounds) từ r0-start/eval.json, history.json và eval.json của lượt NHẬN cuối.
    --rounds vẫn là tổng số lượt của batch, nên lượt kế tiếp là r<len(rounds)+1>."""
    if st["dirty"]:
        raise SystemExit(f"--resume cần fork sạch (job bị dừng giữa lượt?), đang sửa: {st['dirty']}")
    f0 = out / "r0-start" / "eval.json"
    if not f0.exists():
        raise SystemExit(f"--resume: thiếu {f0}, chạy lại không có --resume")
    start = json.loads(f0.read_text())
    hp = out / "history.json"
    rounds = json.loads(hp.read_text()) if hp.exists() else []
    best = dict(start, label="r0")
    for r in rounds:
        if r.get("verdict") == "NHẬN":
            best = dict(json.loads((out / r["label"] / "eval.json").read_text()), label=r["label"])
    return start, best, rounds


def phase_loop(a, out: Path, st: dict):
    base = Path(a.out_dir) / a.baseline_run
    if not (base / "known_fail.txt").exists() or not (base / "gate.json").exists():
        raise SystemExit(f"thiếu {base}/known_fail.txt hoặc gate.json (run sim Mốc 1)")
    known = set((base / "known_fail.txt").read_text().split())
    bg = json.loads((base / "gate.json").read_text())
    base_gm = geomean([b["mcycle"] for b in bg["bench"].values()])
    budget = a.cycle_budget or base_gm * 1.03
    gate_cfg = {"known": known, "budget": budget, "elfs": get(N.load_test_elfs.chia_remote())}
    print(f"vòng agent: {len(known)} test ISA loại, geomean baseline {base_gm:.1f}, ngân sách {budget:.1f}", flush=True)
    write(out / "fork_heads_start.txt", st["heads"])

    if a.resume:
        start, best, rounds = resume_state(out, st)
        print(f"resume {a.run}: {len(rounds)} lượt cũ, best {best['label']} WNS {best['wns']} ns", flush=True)
    else:
        start = evaluate(a, out / "r0-start", st, "r0", gate_cfg, elaborated=False)
        write(out / "r0-start" / "eval.json", json.dumps(start, indent=2))
        if not start["gate_ok"] or start["wns"] is None:
            raise SystemExit(f"điểm xuất phát không dùng được: {start['problems']}")
        best, rounds = dict(start, label="r0"), []
    history = load_history(out)
    llm = make_llm(a)
    write_loop_summary(a, out, start, best, rounds)
    for i in range(len(rounds) + 1, a.rounds + 1):
        if best["wns"] >= 0:
            print(f"đạt WNS ≥ 0 ở {a.period_ns:g} ns, dừng", flush=True)
            break
        label = f"r{i}"
        d = out / label
        t0 = time.time()
        print(f"\n=== {label}: best WNS {best['wns']} ns ===", flush=True)
        ck = get(N.fork_checkpoint.chia_remote())
        rpt = Path(best["run_dir"]) / "obj"
        prompt = fill("round.md", ROUND=i, RUN=a.run, PERIOD=f"{a.period_ns:g}", BEST_WNS=best["wns"],
                      BEST_FMAX=best["fmax"] and round(best["fmax"], 2), BEST_TNS=best["tns"], DIRECTIVES=a.directives,
                      LUT=best["lut"], FF=best["ff"], BRAM=best["bram"], DSP=best["dsp"],
                      GEOMEAN=round(best["geomean"], 1), BUDGET=round(budget, 1), ACCEPT=a.accept_ns,
                      PATHS=paths_table(best["paths"]), PATHS_RPT=rpt / f"paths_{best['impl']}.rpt",
                      TIMING_RPT=rpt / f"timing_{best['impl']}.rpt", UTIL_HIER_RPT=rpt / f"util_hier_{best['impl']}.rpt",
                      HISTORY=history_md(history + rounds))
        rec = {"label": label, "run": a.run, "best_before": best["wns"], "verdict": "loại", "reason": "", "files": []}
        reason = call_agent(llm, prompt, d, "agent", st["fork"])
        rec["idea"] = (d / "agent_response.md").read_text()[:3000] if (d / "agent_response.md").exists() else ""
        ev, sc = None, None
        if not reason:
            sc = get(N.fork_scope.chia_remote(ck))
            rec["files"] = sc["inside"] + sc["outside"]
            if not sc["changed"]:
                reason = "agent không sửa gì"
            elif sc["outside"]:
                reason = f"sửa ngoài phạm vi: {sc['outside']}"
        if not reason:
            write(out / "patches" / f"{label}.patch", get(N.fork_state.chia_remote())["patch"])
            cfg = N.FPGA_CONFIGS[a.period_ns]
            for k in range(a.fix_tries + 1):
                e = get(N.elaborate_fpga.chia_remote(cfg, jobs=min(a.make_jobs, 8)))
                write(d / f"elaborate{k}.json", json.dumps(e, indent=2))
                if e["ok"]:
                    break
                if k == a.fix_tries:
                    reason = f"lỗi compile sau {a.fix_tries} lần sửa"
                    break
                print(f"  {label}: lỗi compile, agent sửa lần {k + 1}", flush=True)
                fp = fill("fix_build.md", ROUND=i, TRY=k + 1, MAX=a.fix_tries, CONFIG=cfg, TAIL=e["tail"], LOG=e["log"])
                r2 = call_agent(llm, fp, d, f"fix{k + 1}", st["fork"])
                sc = get(N.fork_scope.chia_remote(ck))
                if r2 or sc["outside"]:
                    reason = r2 or f"sửa ngoài phạm vi: {sc['outside']}"
                    break
            rec["files"] = sc["inside"] + sc["outside"]
        if not reason:
            st2 = get(N.fork_state.chia_remote())
            write(out / "patches" / f"{label}.patch", st2["patch"])
            ev = evaluate(a, d, st2, label, gate_cfg, elaborated=True)
            write(d / "eval.json", json.dumps(ev, indent=2))
            rec.update({k: ev.get(k) for k in ("wns", "tns", "fmax", "geomean", "lut", "ff", "bram", "dsp", "impl",
                                               "directive", "all_wns")})
            if ev["problems"]:
                reason = "; ".join(ev["problems"])
            elif ev["wns"] <= best["wns"] + a.accept_ns:
                reason = f"WNS {ev['wns']} không tốt hơn {best['wns']} quá {a.accept_ns} ns"
        if not reason:
            c = get(N.fork_commit.chia_remote(f"minsoc-fmax {a.run} {label}: WNS {best['wns']} -> {ev['wns']} ns", ck))
            rec.update(verdict="NHẬN", reason=f"WNS +{ev['wns'] - best['wns']:.3f} ns", commits=c)
            best = dict(ev, label=label)
        else:
            rv = get(N.fork_revert.chia_remote(ck))
            write(d / "revert.json", json.dumps(rv, indent=2))
            rec["reason"] = reason
            if not rv["ok"]:
                rounds.append(rec)
                write(out / "history.json", json.dumps(rounds, indent=2))
                raise SystemExit(f"{label}: revert fork không sạch ({rv['left']}), dừng. Xem {d}/revert.json")
        rec["minutes"] = round((time.time() - t0) / 60, 1)
        if ev and ev.get("vivado_min") is not None:
            rec["vivado_min"] = ev["vivado_min"]
        rounds.append(rec)
        print(f"  {label}: {rec['verdict']} — {rec['reason']} ({rec['minutes']} phút)", flush=True)
        write(out / "history.json", json.dumps(rounds, indent=2))
        append_csv(out / "results.csv", {"round": label, "verdict": rec["verdict"], "wns": rec.get("wns"),
                                         "fmax_mhz": rec.get("fmax"), "geomean": rec.get("geomean"),
                                         "lut": rec.get("lut"), "ff": rec.get("ff"), "reason": rec["reason"]})
        write_loop_summary(a, out, start, best, rounds)
        if reason.startswith("LIMIT"):
            print("hết hạn mức Claude, dừng batch", flush=True)
            break
    write_loop_summary(a, out, start, best, rounds)
    print((out / "summary.md").read_text(), flush=True)


def main():
    a = args_()
    out = Path(a.out_dir) / a.run
    out.mkdir(parents=True, exist_ok=True)
    write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run}: kết quả ở {out}", flush=True)
    from chia.base.bypass import Bypass
    from chia.base.cache import start_cache, stop_cache
    ray.init(address="auto", namespace="minsoc_fmax", ignore_reinit_error=True)
    res = ray.cluster_resources()
    need = ["chipyard"] + (["verilator_run"] if a.phase == "sim" else ["vivado"])
    if not a.baseline_only:
        need += ["verilator_run", "claude_creds"]
    miss = [r for r in need if res.get(r, 0) < 1]
    if miss:
        raise SystemExit(f"cluster thiếu khối {miss} (python scripts/chia-up-blocks.py --fpga)")
    cache = start_cache(size=4, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(EXP_YAML))
    if a.flush_cache:
        get(cache.flush.chia_remote())
    bypass = Bypass(yaml_path=str(EXP_YAML))
    for fn in ("run_gate", "vivado_impl"):  # tag baseline_* và m2_*
        bypass.set_provider(fn, N.cache_provider)
        bypass.set_cond(fn, N.cache_hit_cond)
    try:
        st = snapshot(out)
        if not a.baseline_only:
            phase_loop(a, out, st)
        else:
            (phase_sim if a.phase == "sim" else phase_fpga)(a, out, st)
    finally:
        stop_cache()
    print(f"XONG: {out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
