"""minsoc-fmax: driver. Mốc 1 chỉ có --baseline-only (loop agent là Mốc 2).

  --phase sim    build Verilator MinSocFmaxConfig trên fork, chạy gate (ISA rv64*-p-* + 8 benchmark, lặp benchmark
                 với seed 7), ghi test fail sẵn (known_fail.txt) và cycle baseline (gate.json).
  --phase fpga   elaborate MinSocArty100TF<f>Config, Vivado: synth 1 lần + --repeat lần place/route (đo nhiễu),
                 bitstream ở lần đầu. Ghi WNS/TNS/Fmax/LUT/FF/BRAM/DSP, top 10 path, ngưỡng nhận đề xuất.

  cd ~/CHIA-workflow/projects/minsoc-fmax
  chia job submit --working-dir . -- python minsoc_fmax.py --phase sim  --baseline-only --run m1-sim
  chia job submit --working-dir . -- python minsoc_fmax.py --phase fpga --baseline-only --run m1-fpga10 --period-ns 10

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
import sys
import time
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
    a = p.parse_args()
    if a.phase == "asic":
        p.error("pha asic chưa làm (đề xuất PDK/flow trước)")
    if not a.baseline_only:
        p.error("Mốc 1 chỉ hỗ trợ --baseline-only; loop agent ở Mốc 2")
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
    vh = hashlib.sha256((simdram_v + (HERE / "vivado_tcl.py").read_text()).encode()).hexdigest()[:8]
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
                                      write_bit=not a.no_bitstream, _chia_tag=tag))
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
    miss = [r for r in need if res.get(r, 0) < 1]
    if miss:
        raise SystemExit(f"cluster thiếu khối {miss} (python scripts/chia-up-blocks.py --fpga)")
    cache = start_cache(size=4, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(EXP_YAML))
    if a.flush_cache:
        get(cache.flush.chia_remote())
    bypass = Bypass(yaml_path=str(EXP_YAML))
    for fn in ("run_gate", "vivado_impl"):
        bypass.set_provider(fn, N.cache_provider)
        bypass.set_cond(fn, N.cache_hit_cond)
    try:
        st = snapshot(out)
        (phase_sim if a.phase == "sim" else phase_fpga)(a, out, st)
    finally:
        stop_cache()
    print(f"XONG: {out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
