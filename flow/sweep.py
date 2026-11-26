"""sweep.py - quét bước FPGA của baseline (không agent): Fmax của từng biến thể, rồi công suất theo hoạt động thật ở một
clock chung với nhiều giá trị của một opt của project (fpga.sweep_opt, ví dụ tỉ lệ gửi phép của demo ALU). Mỗi lần build
là một task CHIA (flow.tasks_fpga.fpga_build) trên khối vivado.

Chạy từ GỐC repo, cluster có khối vivado (hoặc: python flow/run.py <project> sweep fmax ...):
  chia job submit --working-dir . -- python -m flow.sweep --project <project> fmax  [--variants a,b] [--start 10]
      [--res 0.1] [--max-runs 7]
  chia job submit --working-dir . -- python -m flow.sweep --project <project> power --period 5 [--variants a,b]
      [--values 10,30,70,100]

fmax: với mỗi biến thể, build ở --start ns; đạt timing thì thử chu kỳ bằng đường găng (chu kỳ − WNS), không đạt thì
      chia đôi khoảng giữa chu kỳ đạt nhỏ nhất và chu kỳ trượt lớn nhất, tới khi hai chu kỳ cách nhau ≤ --res ns.
      Fmax = 1000 / chu kỳ đạt nhỏ nhất. Không đo công suất (nhanh hơn). Các biến thể chạy song song nếu khối vivado có
      nhiều slot.
power: mỗi biến thể × mỗi giá trị của fpga.sweep_opt build ở --period ns, đo công suất SAIF.

Kết quả: <WORK_DIR>/<group>/<project>/sweep-<mode>-<thời điểm>/
    <biến thể>-T<chu kỳ>[-<opt><giá trị>]/   bản chép nguồn của từng lần build (file lớn bị xoá, trừ khi --keep)
    results/sweep.json, sweep.md            bảng cho báo cáo
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

from flow.project import Project
from flow.tasks_fpga import fpga_build

MIN_PERIOD = 2.2          # BUFG của Artix-7 -1 khoảng 464 MHz (2,155 ns): không thử nhanh hơn


def _prep(prj: Project, sweep: Path, variant: str, period: float, opts: dict, tag: str) -> tuple[Path, dict]:
    d = sweep / tag
    prj.copy_sources(d, ignore=("build", "out", "reports", "xsim*", ".Xil"))
    return d, prj.prepare_fpga(d, period, variant, opts)


def _slim(d: Path) -> None:
    f = d / "fpga"
    for p in [f / "out" / "post_route.dcp", f / "out" / "funcsim.v", f / "out" / "fpga_top.bit"]:
        p.unlink(missing_ok=True)
    for p in [f / "xsim_pw", f / ".Xil"]:
        shutil.rmtree(p, ignore_errors=True)


def _row(r: dict) -> dict:
    keys = ("ok", "timing_met", "clock_period_ns", "clock_mhz", "wns_ns", "whs_ns", "fmax_est_mhz", "lut", "ff",
            "bufg", "power_dynamic_w", "power_inst_w", "power_total_w", "power_dynamic_vl_w", "power_confidence",
            "saif_nets_matched_pct", "saif_ok", "warnings", "warning_ids", "seconds", "power_seconds")
    return {k: r.get(k) for k in keys}


def _snap(x: float, res: float, up: bool) -> float:
    q = x / res
    return round((math.ceil(q - 1e-9) if up else math.floor(q + 1e-9)) * res, 3)


def run_fmax(args, prj: Project, sweep: Path) -> dict:
    variants = args.variants
    state = {v: {"runs": [], "pass": None, "fail": None, "next": args.start, "done": False} for v in variants}
    while True:
        todo = [v for v in variants if not state[v]["done"]]
        if not todo:
            break
        refs = {}
        for v in todo:
            T = state[v]["next"]
            d, _ = _prep(prj, sweep, v, T, {}, f"{v}-T{T:g}")
            refs[v] = (T, d, fpga_build.chia_remote(str(d), prj.cfg, power_cycles=0))
        for v, (T, d, ref) in refs.items():
            r = get(ref)
            st = state[v]
            st["runs"].append({"period_ns": T, "dir": str(d), **_row(r)})
            if not args.keep:
                _slim(d)
            met = bool(r.get("ok")) and bool(r.get("timing_met"))
            print(f"[fmax {prj.name}] {v:8s} T={T:6.3f} ns ({1000 / T:6.1f} MHz)  WNS={r.get('wns_ns')}  WHS="
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
        out[v] = {"note": prj.cfg["fpga"]["variants"][v].get("note", ""), "pass_period_ns": st["pass"], "fail_period_ns": st["fail"],
                  "fmax_mhz": round(1000 / st["pass"], 1) if st["pass"] else None, "at_fmax": best,
                  "runs": st["runs"]}
    return out


def run_power(args, prj: Project, sweep: Path) -> dict:
    key = prj.cfg["fpga"].get("sweep_opt")
    refs = []
    for v in args.variants:
        for val in args.values or [None]:
            opts = {key: val} if key and val is not None else {}
            tag = f"{v}-T{args.period:g}" + (f"-{key}{val:g}" if opts else "")
            d, info = _prep(prj, sweep, v, args.period, opts, tag)
            refs.append((v, val, d, info, fpga_build.chia_remote(str(d), prj.cfg, power_cycles=args.power_cycles)))
    out = {v: {"note": prj.cfg["fpga"]["variants"][v].get("note", ""), "opt": key, "by_value": {}} for v in args.variants}
    for v, val, d, info, ref in refs:
        r = get(ref)
        out[v]["by_value"]["-" if val is None else f"{val:g}"] = {"dir": str(d), "info": {k: x for k, x in info.items()
                                                                                         if k != "synth_args"}, **_row(r)}
        if not args.keep:
            _slim(d)
        mw = lambda w: f"{w * 1000:7.3f}" if isinstance(w, (int, float)) else "      ?"
        print(f"[power {prj.name}] {v:8s} {key}={val}  timing {'đạt' if r.get('timing_met') else 'KHÔNG'}  "
              f"P động {mw(r.get('power_dynamic_w'))} mW  instance {mw(r.get('power_inst_w'))} mW  "
              f"(vector-less {mw(r.get('power_dynamic_vl_w'))} mW, tin cậy {r.get('power_confidence')}, "
              f"net khớp {r.get('saif_nets_matched_pct')} %)", flush=True)
        if not r.get("ok"):
            print(r.get("log_tail", "")[-1500:], flush=True)
    return out


def table_md(mode: str, res: dict, args, prj: Project) -> str:
    mw = lambda w: f"{w * 1000:.3f}" if isinstance(w, (int, float)) else "-"
    part = prj.cfg["fpga"]["part"]
    if mode == "fmax":
        lines = [f"# {prj.name}: FPGA Fmax ({part}, default tool settings)\n",
                 "| Variant | Fmax (MHz) | Pass / fail period (ns) | LUT | FF | BUFG | Runs (ns: WNS) |",
                 "|---|---|---|---|---|---|---|"]
        for v, x in res.items():
            a = x["at_fmax"] or {}
            runs = ", ".join(f"{r['period_ns']:g}: {r['wns_ns']}" for r in x["runs"])
            lines.append(f"| {v} ({x['note']}) | {x['fmax_mhz'] or '-'} | {x['pass_period_ns']} / "
                         f"{x['fail_period_ns']} | {a.get('lut', '-')} | {a.get('ff', '-')} | {a.get('bufg', '-')} | "
                         f"{runs} |")
        return "\n".join(lines) + "\n"
    inst = prj.cfg["fpga"].get("power_instance") or "instance"
    key = prj.cfg["fpga"].get("sweep_opt") or "-"
    lines = [f"# {prj.name}: FPGA power at {1000 / args.period:.4g} MHz ({args.period:g} ns), SAIF\n",
             f"| Variant | {key} | Timing | WNS (ns) | Dyn. power SAIF (mW) | {inst} SAIF (mW) | "
             "Dyn. power vector-less (mW) | LUT | FF | BUFG |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for v, x in res.items():
        for val, r in x["by_value"].items():
            lines.append(f"| {v} | {val} | {'met' if r['timing_met'] else 'NOT met'} | {r['wns_ns']} | "
                         f"{mw(r['power_dynamic_w'])} | {mw(r['power_inst_w'])} | {mw(r['power_dynamic_vl_w'])} | "
                         f"{r['lut']} | {r['ff']} | {r['bufg']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["fmax", "power"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")))
    ap.add_argument("--variants", help="mặc định: mọi biến thể của project")
    ap.add_argument("--start", type=float, default=10.0, help="fmax: chu kỳ bắt đầu (ns)")
    ap.add_argument("--res", type=float, default=0.1, help="fmax: độ phân giải chu kỳ (ns)")
    ap.add_argument("--max-runs", type=int, default=7, help="fmax: số lần build tối đa mỗi biến thể")
    ap.add_argument("--period", type=float, help="power: chu kỳ chung (ns)")
    ap.add_argument("--values", default="10,30,70,100", help="power: các giá trị của fpga.sweep_opt của project")
    ap.add_argument("--power-cycles", type=int, default=20000)
    ap.add_argument("--keep", action="store_true", help="giữ bitstream, checkpoint, netlist của từng lần build")
    args = ap.parse_args()
    prj = Project(args.project)
    variants = prj.cfg["fpga"]["variants"]
    args.variants = [v.strip() for v in (args.variants or ",".join(variants)).split(",") if v.strip()]
    for v in args.variants:
        if v not in variants:
            raise SystemExit(f"LỖI: biến thể {v!r} không có; chọn trong {', '.join(variants)}")
    args.values = [float(x) for x in args.values.split(",") if x.strip()] if prj.cfg["fpga"].get("sweep_opt") else []
    if args.mode == "power" and not args.period:
        raise SystemExit("LỖI: power cần --period <ns> (chọn từ kết quả fmax)")

    sweep = prj.run_root(args.work_dir) / time.strftime(f"sweep-{args.mode}-%Y%m%d-%H%M%S")
    (sweep / "results").mkdir(parents=True)
    ray.init(address="auto", ignore_reinit_error=True)
    if not ray.cluster_resources().get("vivado"):
        raise SystemExit("LỖI: cluster không có khối vivado: `python scripts/chia-up-blocks.py --fpga --asic`")
    start_collector(log_dir=str(sweep / "profile"))
    print(f"[sweep {prj.name}] {args.mode}: {sweep}", flush=True)
    t0 = time.time()
    res = run_fmax(args, prj, sweep) if args.mode == "fmax" else run_power(args, prj, sweep)
    stop_collector()
    rep = {"project": prj.name, "mode": args.mode, "dir": str(sweep), "seconds": round(time.time() - t0, 1),
           "args": {k: v for k, v in vars(args).items() if k != "work_dir"}, "result": res}
    (sweep / "results" / "sweep.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    md = table_md(args.mode, res, args, prj)
    (sweep / "results" / "sweep.md").write_text(md)
    print("\n" + md, flush=True)
    if args.mode == "fmax":
        fm = [x["fmax_mhz"] for x in res.values() if x["fmax_mhz"]]
        if fm:
            print(f"[sweep {prj.name}] Fmax thấp nhất trong các biến thể: {min(fm)} MHz", flush=True)
    bad = [v for v, x in res.items() if (x.get("fmax_mhz") is None if args.mode == "fmax"
                                         else not all(r["ok"] for r in x["by_value"].values()))]
    print(f"[sweep {prj.name}] bảng: {sweep / 'results' / 'sweep.md'}", flush=True)
    print(f"KẾT QUẢ: {'OK' if not bad else 'LỖI ở ' + ', '.join(bad)}", flush=True)
    raise SystemExit(0 if not bad else 1)


if __name__ == "__main__":
    main()
