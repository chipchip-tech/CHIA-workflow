"""fpga_sweep.py - quét bước FPGA của baseline (không agent): Fmax của từng biến thể, rồi công suất theo hoạt động thật
ở một clock chung với nhiều tỉ lệ rảnh. Mỗi lần build là một task CHIA (baseline.fpga_build) trên khối vivado.

Chạy từ thư mục của demo (projects/demo-alu/NoCG hoặc CG), cluster có khối vivado:
  chia job submit --working-dir . -- python fpga_sweep.py fmax  [--variants a,b] [--start 10] [--res 0.1] [--max-runs 7]
  chia job submit --working-dir . -- python fpga_sweep.py power --period 5 [--variants a,b] [--en-pcts 10,30,70,100]

fmax: với mỗi biến thể, build ở --start ns; đạt timing thì thử chu kỳ bằng đường găng (chu kỳ − WNS), không đạt thì
      chia đôi khoảng giữa chu kỳ đạt nhỏ nhất và chu kỳ trượt lớn nhất, tới khi hai chu kỳ cách nhau ≤ --res ns.
      Fmax = 1000 / chu kỳ đạt nhỏ nhất. Không đo công suất (nhanh hơn). Các biến thể chạy song song nếu khối vivado có
      nhiều slot.
power: mỗi biến thể × mỗi tỉ lệ gửi phép (--en-pcts, % chu kỳ có en=1) build ở --period ns, đo công suất SAIF.

Kết quả: <WORK_DIR>/demo-alu/<demo>/sweep-<mode>-<thời điểm>/
    <biến thể>-T<chu kỳ>[-en<%>]/    rtl/, fpga/ của từng lần build (file lớn bị xoá, trừ khi --keep)
    results/sweep.json, sweep.md     bảng cho báo cáo
File này giống hệt nhau ở NoCG/ và CG/; danh sách biến thể nằm trong baseline.py (FPGA_VARIANTS).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
from pathlib import Path

import ray
from chia.base.ChiaFunction import get
from chia.trace.profiler import start_collector, stop_collector

import baseline as B

MIN_PERIOD = 2.2          # BUFG của Artix-7 -1 khoảng 464 MHz (2,155 ns): không thử nhanh hơn


def _prep(src: Path, sweep: Path, variant: str, period: float, en_pct: float, tag: str) -> Path:
    d = sweep / tag
    for sub in ("rtl", "fpga"):
        shutil.copytree(src / sub, d / sub, ignore=shutil.ignore_patterns("out", "reports", "xsim*", ".Xil"))
    B.prepare_fpga(str(d), period, variant, en_pct)
    return d


def _slim(d: Path) -> None:
    f = d / "fpga"
    for p in [f / "out" / "post_route.dcp", f / "out" / "funcsim.v", f / "out" / "fpga_top.bit"]:
        p.unlink(missing_ok=True)
    for p in [f / "xsim_pw", f / ".Xil"]:
        shutil.rmtree(p, ignore_errors=True)


def _row(r: dict) -> dict:
    keys = ("ok", "timing_met", "clock_period_ns", "clock_mhz", "wns_ns", "whs_ns", "fmax_est_mhz", "lut", "ff",
            "bufg", "power_dynamic_w", "power_alu_w", "power_total_w", "power_dynamic_vl_w", "power_confidence",
            "saif_nets_matched_pct", "warnings", "warning_ids", "seconds", "power_seconds")
    return {k: r.get(k) for k in keys}


def _snap(x: float, res: float, up: bool) -> float:
    q = x / res
    return round((math.ceil(q - 1e-9) if up else math.floor(q + 1e-9)) * res, 3)


def run_fmax(args, src: Path, sweep: Path) -> dict:
    variants = args.variants
    state = {v: {"runs": [], "pass": None, "fail": None, "next": args.start, "done": False} for v in variants}
    while True:
        todo = [v for v in variants if not state[v]["done"]]
        if not todo:
            break
        refs = {}
        for v in todo:
            T = state[v]["next"]
            d = _prep(src, sweep, v, T, 30, f"{v}-T{T:g}")
            refs[v] = (T, d, B.fpga_build.chia_remote(str(d), power_cycles=0))
        for v, (T, d, ref) in refs.items():
            r = get(ref)
            st = state[v]
            st["runs"].append({"period_ns": T, "dir": str(d), **_row(r)})
            if not args.keep:
                _slim(d)
            met = bool(r.get("ok")) and bool(r.get("timing_met"))
            print(f"[fmax {B.DEMO}] {v:8s} T={T:6.3f} ns ({1000 / T:6.1f} MHz)  WNS={r.get('wns_ns')}  WHS="
                  f"{r.get('whs_ns')}  {'ĐẠT' if met else 'trượt'}  LUT={r.get('lut')} FF={r.get('ff')} "
                  f"BUFG={r.get('bufg')}  {r.get('seconds')}s", flush=True)
            if not r.get("ok"):
                print(r.get("log_tail", "")[-1500:], flush=True)
                st["done"] = True
                continue
            if met:
                st["pass"] = T if st["pass"] is None else min(st["pass"], T)
            else:
                st["fail"] = T if st["fail"] is None else max(st["fail"], T)
            p, f, wns = st["pass"], st["fail"], r.get("wns_ns") or 0.0
            if p is not None and f is not None:
                nxt = _snap((p + f) / 2, args.res, up=True)
                if p - f <= args.res + 1e-9 or nxt >= p or nxt <= f:
                    st["done"] = True
            elif met:                                    # chưa có lần trượt: thử đúng bằng đường găng
                nxt = min(_snap(T - wns, args.res, up=False), _snap(T - args.res, args.res, up=False))
                if nxt < MIN_PERIOD:
                    nxt = MIN_PERIOD
                    st["done"] = T <= MIN_PERIOD
            else:                                        # chưa có lần đạt: nới ra bằng độ âm của WNS
                nxt = _snap(T - wns, args.res, up=True)
            if len(st["runs"]) >= args.max_runs:
                st["done"] = True
            st["next"] = nxt
    out = {}
    for v in variants:
        st = state[v]
        best = next((x for x in st["runs"] if x["period_ns"] == st["pass"] and x["timing_met"]), None)
        out[v] = {"note": B.FPGA_VARIANTS[v]["note"], "pass_period_ns": st["pass"], "fail_period_ns": st["fail"],
                  "fmax_mhz": round(1000 / st["pass"], 1) if st["pass"] else None, "at_fmax": best,
                  "runs": st["runs"]}
    return out


def run_power(args, src: Path, sweep: Path) -> dict:
    refs = []
    for v in args.variants:
        for pct in args.en_pcts:
            d = _prep(src, sweep, v, args.period, pct, f"{v}-T{args.period:g}-en{pct:g}")
            refs.append((v, pct, d, B.fpga_build.chia_remote(str(d), power_cycles=args.power_cycles)))
    out = {v: {"note": B.FPGA_VARIANTS[v]["note"], "by_en_pct": {}} for v in args.variants}
    for v, pct, d, ref in refs:
        r = get(ref)
        out[v]["by_en_pct"][f"{pct:g}"] = {"dir": str(d), "en_thr": B.en_thr(pct), **_row(r)}
        if not args.keep:
            _slim(d)
        mw = lambda w: f"{w * 1000:7.3f}" if isinstance(w, (int, float)) else "      ?"
        print(f"[power {B.DEMO}] {v:8s} en={pct:5.1f} %  timing {'đạt' if r.get('timing_met') else 'KHÔNG'}  "
              f"P động {mw(r.get('power_dynamic_w'))} mW  u_alu {mw(r.get('power_alu_w'))} mW  "
              f"(vector-less {mw(r.get('power_dynamic_vl_w'))} mW, tin cậy {r.get('power_confidence')}, "
              f"net khớp {r.get('saif_nets_matched_pct')} %)", flush=True)
        if not r.get("ok"):
            print(r.get("log_tail", "")[-1500:], flush=True)
    return out


def table_md(mode: str, res: dict, args) -> str:
    mw = lambda w: f"{w * 1000:.3f}" if isinstance(w, (int, float)) else "-"
    if mode == "fmax":
        lines = [f"# Demo {B.DEMO}: Fmax bước FPGA (xc7a100tcsg324-1, Vivado mặc định)\n",
                 "| Biến thể | Fmax (MHz) | Chu kỳ đạt / trượt (ns) | LUT | FF | BUFG | Các lần chạy (ns: WNS) |",
                 "|---|---|---|---|---|---|---|"]
        for v, x in res.items():
            a = x["at_fmax"] or {}
            runs = ", ".join(f"{r['period_ns']:g}: {r['wns_ns']}" for r in x["runs"])
            lines.append(f"| {v} ({x['note']}) | {x['fmax_mhz'] or '-'} | {x['pass_period_ns']} / "
                         f"{x['fail_period_ns']} | {a.get('lut', '-')} | {a.get('ff', '-')} | {a.get('bufg', '-')} | "
                         f"{runs} |")
        return "\n".join(lines) + "\n"
    lines = [f"# Demo {B.DEMO}: công suất bước FPGA ở {1000 / args.period:.4g} MHz ({args.period:g} ns), SAIF\n",
             "| Biến thể | Gửi phép (%) | Timing | WNS (ns) | P động SAIF (mW) | P u_alu SAIF (mW) | "
             "P động vector-less (mW) | LUT | FF | BUFG |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for v, x in res.items():
        for pct, r in x["by_en_pct"].items():
            lines.append(f"| {v} | {pct} | {'đạt' if r['timing_met'] else 'KHÔNG'} | {r['wns_ns']} | "
                         f"{mw(r['power_dynamic_w'])} | {mw(r['power_alu_w'])} | {mw(r['power_dynamic_vl_w'])} | "
                         f"{r['lut']} | {r['ff']} | {r['bufg']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["fmax", "power"])
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")))
    ap.add_argument("--variants", default=",".join(B.FPGA_VARIANTS),
                    help="mặc định mọi biến thể: " + ", ".join(B.FPGA_VARIANTS))
    ap.add_argument("--start", type=float, default=10.0, help="fmax: chu kỳ bắt đầu (ns)")
    ap.add_argument("--res", type=float, default=0.1, help="fmax: độ phân giải chu kỳ (ns)")
    ap.add_argument("--max-runs", type=int, default=7, help="fmax: số lần build tối đa mỗi biến thể")
    ap.add_argument("--period", type=float, help="power: chu kỳ chung (ns)")
    ap.add_argument("--en-pcts", default="10,30,70,100", help="power: các tỉ lệ gửi phép (%%)")
    ap.add_argument("--power-cycles", type=int, default=20000)
    ap.add_argument("--keep", action="store_true", help="giữ bitstream, checkpoint, netlist của từng lần build")
    args = ap.parse_args()
    args.variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in args.variants:
        if v not in B.FPGA_VARIANTS:
            raise SystemExit(f"LỖI: biến thể {v!r} không có; chọn trong {', '.join(B.FPGA_VARIANTS)}")
    args.en_pcts = [float(x) for x in args.en_pcts.split(",") if x.strip()]
    if args.mode == "power" and not args.period:
        raise SystemExit("LỖI: power cần --period <ns> (chọn từ kết quả fmax)")

    src = Path(__file__).resolve().parent
    sweep = Path(args.work_dir) / "demo-alu" / B.DEMO / time.strftime(f"sweep-{args.mode}-%Y%m%d-%H%M%S")
    (sweep / "results").mkdir(parents=True)
    ray.init(address="auto", ignore_reinit_error=True)
    if not ray.cluster_resources().get("vivado"):
        raise SystemExit("LỖI: cluster không có khối vivado: `python scripts/chia-up-blocks.py --fpga --asic`")
    start_collector(log_dir=str(sweep / "profile"))
    print(f"[sweep {B.DEMO}] {args.mode}: {sweep}", flush=True)
    t0 = time.time()
    res = run_fmax(args, src, sweep) if args.mode == "fmax" else run_power(args, src, sweep)
    stop_collector()
    rep = {"demo": B.DEMO, "mode": args.mode, "dir": str(sweep), "seconds": round(time.time() - t0, 1),
           "args": {k: v for k, v in vars(args).items() if k != "work_dir"}, "result": res}
    (sweep / "results" / "sweep.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    md = table_md(args.mode, res, args)
    (sweep / "results" / "sweep.md").write_text(md)
    print("\n" + md, flush=True)
    if args.mode == "fmax":
        fm = [x["fmax_mhz"] for x in res.values() if x["fmax_mhz"]]
        if fm:
            print(f"[sweep {B.DEMO}] Fmax thấp nhất trong các biến thể: {min(fm)} MHz", flush=True)
    bad = [v for v, x in res.items() if (x.get("fmax_mhz") is None if args.mode == "fmax"
                                         else not all(r["ok"] for r in x["by_en_pct"].values()))]
    print(f"[sweep {B.DEMO}] bảng: {sweep / 'results' / 'sweep.md'}", flush=True)
    print(f"KẾT QUẢ: {'OK' if not bad else 'LỖI ở ' + ', '.join(bad)}", flush=True)
    raise SystemExit(0 if not bad else 1)


if __name__ == "__main__":
    main()
