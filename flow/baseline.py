"""baseline.py - Pha 1 (baseline, KHÔNG có agent) của một project, do CHIA điều phối.

Chạy từ GỐC repo (job mang theo cả flow/ và project), cluster theo khối đang bật:
  chia job submit --working-dir . -- python -m flow.baseline --project projects/demo-alu/NoCG \\
      [--stages sim,fpga,asic] [--plusarg seed=1 ...] [--fpga-period 10] [--fpga-variant <tên>] [--fpga-opt k=v ...]
      [--power-cycles 20000]
Lệnh tiện hơn (submit + trang status trên trình duyệt): python flow/run.py <project> baseline [tham số như trên].

Các bước (mỗi việc là một task CHIA trên khối có resource tương ứng; bước sau chỉ chạy khi bước trước qua gate):
  Sim   sim_build (chipyard: lint + Verilator build) → sim_run (verilator: RESULT PASS, coverage, VCD)
  FPGA  fpga (vivado: build.tcl tới bitstream, công suất SAIF, methodology, GLS netlist sau route). WNS/WHS < 0 hay
        methodology có Critical thì ghi "chưa đạt sign-off" nhưng không dừng (ở clock cao baseline có thể chưa đạt;
        agent phải đạt)
  ASIC  asic_synth → floorplan → place → cts → route → finish (asic), rồi CÙNG LÚC: asic_drc, asic_lvs, asic_sta_<corner>
        (asic, mỗi task một phần slot) và asic_gls (chipyard)
Kết quả: <WORK_DIR>/<group>/<project>/<run_id>/ (bản chép nguồn rtl/ sim/ fpga/ asic/ và kết quả build,
results/baseline.json, status.html, profile/ timeline của CHIA).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import ray
from chia.trace.profiler import start_collector, stop_collector

from flow import status as ST
from flow.common import sha256
from flow.orchestrate import Runner, asic_merged, first_failed, run_asic, run_fpga, run_sim
from flow.project import Project, parse_kv

NEED = {"sim": [("chipyard", "chipyard"), ("verilator_run", "verilator")], "fpga": [("vivado", "vivado")],
        "asic": [("asic", "asic"), ("chipyard", "chipyard")]}


def check_cluster(steps: list[str], extra: list[tuple[str, str]] = ()) -> None:
    have = ray.cluster_resources()
    for res, blk in [x for s in steps for x in NEED[s]] + list(extra):
        if not have.get(res):
            raise SystemExit(f"LỖI: cluster không có khối {blk} (resource {res}): bật bằng "
                             "`python scripts/chia-up-blocks.py --fpga --asic`, hoặc bỏ bước đó")


def finish_profile(run_dir: Path, report: dict) -> None:
    stop_collector()                                 # đợi sự kiện cuối ghi xong vào log
    prof = run_dir / "profile" / "ChiaProfileCollector.log"
    if prof.is_file():                               # timeline HTML của CHIA (không bắt buộc)
        subprocess.run(["chia", "viz-profile", str(prof), "--format", "html"], capture_output=True)
        report["profile"] = str(prof.with_name("ChiaProfileCollector_profile.html"))


def task_logger(tag: str):
    def log(name: str, res: dict) -> None:
        print(f"{tag} {name:15s} {'OK  ' if res.get('ok') else 'FAIL'} {res.get('seconds') or 0:7.2f}s"
              f"  @ {res.get('host', '?')}", flush=True)
        if not res.get("ok"):
            print(res.get("log_tail", ""), flush=True)
    return log


def mw(w) -> str:
    return f"{w * 1000:.3f}" if isinstance(w, (int, float)) else "?"


def print_sim(tag: str, sub: dict) -> None:
    b = sub.get("sim_build", {})
    mode = b.get("lint_mode", "strict")
    state = ("sạch" if mode == "strict" else "không lỗi (cảnh báo chỉ ghi lại)") if b.get("lint_ok") else "KHÔNG đạt"
    print(f"{tag} lint (verilator -Wall, {mode}): {state}, {b.get('lint_warnings')} cảnh báo {b.get('lint_ids') or ''}",
          flush=True)
    r = sub.get("sim_run")
    if r and r.get("ok"):
        print(f"{tag} {r['result']}", flush=True)
        fc = f"functional {r['fcov_hit']}/{r['fcov_total']} bin, " if r.get("fcov_hit") is not None else ""
        print(f"{tag} coverage: {fc}line {r['cov_line']}/"
              f"{r['cov_line_total']}, toggle {r['cov_toggle']}/{r['cov_toggle_total']}, branch {r['cov_branch']}/"
              f"{r['cov_branch_total']} (DUT); thiếu: {', '.join(r['fcov_miss']) or '-'}", flush=True)
        print(f"{tag} waveform: {r['vcd']} ({r['vcd_bytes']} byte)", flush=True)


def print_fpga(tag: str, r: dict, fcfg: dict, inst: str | None) -> None:
    print(f"{tag} fpga ({fcfg['variant']}, {r.get('clock_mhz')} MHz, opts {fcfg.get('opts')}): LUT={r.get('lut')} "
          f"FF={r.get('ff')} BUFG={r.get('bufg')} WNS={r.get('wns_ns')} WHS={r.get('whs_ns')} ns (Fmax ước lượng "
          f"{r.get('fmax_est_mhz')} MHz, timing {'đạt' if r.get('timing_met') else 'KHÔNG đạt'})  cảnh báo="
          f"{r.get('warnings')} (critical {r.get('critical_warnings')})", flush=True)
    print(f"{tag} fpga công suất SAIF: động {mw(r.get('power_dynamic_w'))} mW, {inst or 'instance'} "
          f"{mw(r.get('power_inst_w'))} mW, tổng {mw(r.get('power_total_w'))} mW (độ tin cậy "
          f"{r.get('power_confidence')}, net khớp {r.get('saif_nets_matched_pct')} %); vector-less: động "
          f"{mw(r.get('power_dynamic_vl_w'))} mW", flush=True)
    print(f"{tag} bitstream: {r.get('bitstream')}", flush=True)
    meth = ", ".join(k for k, v in (r.get("methodology") or {}).items() if v["severity"] != "Advisory") or "-"
    print(f"{tag} fpga sign-off: methodology critical={r.get('methodology_critical')} ({meth}), mô phỏng gate-level: "
          f"{r.get('gls_result')}", flush=True)


def print_asic(tag: str, sub: dict) -> None:
    for k, c in sub.items():
        if k.startswith("asic_sta_"):
            print(f"{tag} sta {c.get('corner', k):10s} setup WS={c.get('setup_ws_ns')} hold WS={c.get('hold_ws_ns')} "
                  f"ns ({c.get('parasitics')}, {'đạt' if c.get('ok') else 'KHÔNG đạt'})", flush=True)
    if "asic_gls" in sub:
        print(f"{tag} mô phỏng gate-level ASIC: {sub['asic_gls'].get('gls_result')}", flush=True)
    if "asic_drc" in sub:
        d = sub["asic_drc"]
        print(f"{tag} drc (KLayout): {d.get('drc_status')} {d.get('drc_klayout')} vi phạm {d.get('drc_rules') or ''}",
              flush=True)
    if "asic_lvs" in sub:
        print(f"{tag} lvs: {sub['asic_lvs'].get('lvs')}", flush=True)
    r = sub.get("asic_finish", {})
    if "gds" in r:
        print(f"{tag} asic: diện tích {r['area_um2']} um2 (util {r['util_pct']} %), {r['n_logic_cells']} cell (flop "
              f"{r['n_flop']}, ICG {r['n_icg']}, buffer clock {r['n_clkbuf']}), setup WS={r['setup_ws_ns']} hold WS="
              f"{r['hold_ws_ns']} ns, P={r['power_total_w']} W, DRC={r['drc_errors']}, antenna={r.get('antenna_nets')}",
              flush=True)
        print(f"{tag} gds: {r['gds']}", flush=True)
        print(f"{tag} layout: {r.get('layout_png')}", flush=True)
    chk = [k for k in sub if k.startswith(("asic_drc", "asic_lvs", "asic_sta_", "asic_gls"))]
    if chk:
        t0 = min(sub[k].get("t_start", 0) for k in chk)
        t1 = max(sub[k].get("t_end", 0) for k in chk)
        tot = sum(sub[k].get("seconds") or 0 for k in chk)
        print(f"{tag} kiểm tra sau finish: {len(chk)} task song song, {t1 - t0:.1f} s (cộng lần lượt {tot:.1f} s)",
              flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="thư mục project (có project.py), tính từ gốc repo")
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")),
                    help="thư mục làm việc chung (WORK_DIR của cluster), mặc định ~/chia_demo")
    ap.add_argument("--run-id", default=time.strftime("baseline-%Y%m%d-%H%M%S"))
    ap.add_argument("--stages", help="các bước, theo thứ tự (mặc định: steps của project)")
    ap.add_argument("--plusarg", action="append", metavar="TÊN=GIÁ_TRỊ", help="đổi plusarg của testbench")
    ap.add_argument("--fpga-period", type=float, help="chu kỳ clock của bước FPGA (ns)")
    ap.add_argument("--fpga-variant", help="biến thể FPGA (mặc định: biến thể đầu tiên của project)")
    ap.add_argument("--fpga-opt", action="append", metavar="TÊN=GIÁ_TRỊ", help="tham số riêng của project cho FPGA")
    ap.add_argument("--power-cycles", type=int, default=20000, help="số chu kỳ mô phỏng để ghi SAIF (0: không đo)")
    args = ap.parse_args()

    prj = Project(args.project)
    cfg = prj.cfg
    tag = f"[baseline {prj.name}]"
    stages = [x.strip() for x in (args.stages or ",".join(prj.steps)).split(",") if x.strip()]
    if any(s not in prj.steps for s in stages):
        raise SystemExit(f"LỖI: --stages {stages} có bước project không bật ({prj.steps})")
    run_dir = prj.run_root(args.work_dir) / args.run_id
    if run_dir.exists():
        raise SystemExit(f"LỖI: {run_dir} đã có, chọn --run-id khác")
    prj.copy_sources(run_dir)
    (run_dir / "results").mkdir()
    fcfg = None
    if "fpga" in prj.steps:
        fcfg = prj.prepare_fpga(run_dir, args.fpga_period or cfg["fpga"]["period_ns"],
                                args.fpga_variant or next(iter(cfg["fpga"]["variants"])), parse_kv(args.fpga_opt))
    plusargs = {**cfg["sim"]["plusargs"], **parse_kv(args.plusarg)}
    plus = prj.plusargs(plusargs)

    ray.init(address="auto", ignore_reinit_error=True)
    check_cluster(stages)
    start_collector(log_dir=str(run_dir / "profile"))

    report = {"project": prj.name, "group": prj.group, "demo": prj.name, "dut": prj.dut, "phase": "baseline",
              "run_id": args.run_id, "run_dir": str(run_dir), "cfg": cfg,
              "rtl_sha256": sha256(sorted((run_dir / "rtl").glob("*.v"))),
              "params": {"plusargs": plusargs, "stages": stages, "fpga": fcfg, "power_cycles": args.power_cycles},
              "steps": {}, "stopped_at": None, "t0": time.time()}
    write = lambda final=False: ST.write(report, final)
    R = Runner(report, write, task_logger(tag))
    print(f"{tag} run_dir = {run_dir}", flush=True)
    write()
    print(f"{tag} trạng thái: {run_dir / 'status.html'}", flush=True)
    d = str(run_dir)

    def done(sub: dict) -> bool:
        report["steps"].update(sub)
        bad = first_failed(sub)
        if bad:
            report["stopped_at"] = bad
        write()
        return bad is None

    ok = True
    if "sim" in stages:
        sub = run_sim(R, d, cfg, plus)
        ok = done(sub)
        print_sim(tag, sub)
    if ok and "fpga" in stages:
        sub = run_fpga(R, d, cfg, args.power_cycles)
        ok = done(sub)
        print_fpga(tag, sub["fpga"], fcfg, cfg["fpga"].get("power_instance"))
        if ok and not sub["fpga"].get("signoff_met"):
            print(f"{tag} CẢNH BÁO: FPGA chưa đạt sign-off (timing hoặc methodology) ở {sub['fpga'].get('clock_mhz')} "
                  "MHz (ghi lại làm mốc; bitstream vẫn có nên bước ASIC vẫn chạy)", flush=True)
    if ok and "asic" in stages:
        sub = run_asic(R, d, cfg, plus)
        ok = done(sub)
        report["steps"]["asic"] = asic_merged(sub)
        print_asic(tag, sub)

    finish_profile(run_dir, report)
    out = run_dir / "results" / "baseline.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    write(True)
    print(f"{tag} số liệu: {out}", flush=True)
    if report.get("profile"):
        print(f"{tag} timeline CHIA: {report['profile']}", flush=True)
    print(f"KẾT QUẢ: {'OK' if report['stopped_at'] is None else 'DỪNG ở ' + report['stopped_at']}", flush=True)
    raise SystemExit(0 if report["stopped_at"] is None else 1)


if __name__ == "__main__":
    main()
