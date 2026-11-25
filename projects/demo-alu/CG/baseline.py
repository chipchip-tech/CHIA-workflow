"""baseline.py - Pha 1 (baseline, KHÔNG có agent) của demo CG, do CHIA điều phối.

Chạy từ thư mục chứa file này (projects/demo-alu/CG), cluster theo khối đang bật:
  chia job submit --working-dir . -- python baseline.py [--stages sim,fpga,asic] [--seed 1] [--cycles 20000] [--en-pct 30]
      [--fpga-period 10] [--fpga-variant <tên>] [--fpga-en-pct 30] [--power-cycles 20000]
Bước fpga cần khối vivado, bước asic cần khối asic: `python scripts/chia-up-blocks.py --fpga --asic`.

Các bước (mỗi bước là một task CHIA, chạy trên khối có resource tương ứng):
  1. sim_build  khối chipyard   Verilator 5 của Chipyard build testbench (tb + RTL) thành binary Vtb
  2. sim_run    khối verilator  chạy Vtb; gate: dòng `RESULT: PASS`; ghi waveform VCD; số liệu: functional coverage
                                (dòng FCOV của sim/fcov_<DUT>.sv) và line/toggle/branch của DUT (build/coverage.dat)
  3. fpga       khối vivado     Vivado (thiết lập mặc định) build fpga/fpga_top.v cho xc7a100tcsg324-1 tới bitstream,
                                clock theo --fpga-period (fpga/clock.xdc), biến thể --fpga-variant; rồi công suất theo
                                hoạt động thật (power_saif.tcl: xsim netlist sau route, SAIF, report_power);
                                gate: có .bit, đúng clock, đo được công suất; WNS/WHS < 0 thì ghi "timing KHÔNG đạt"
                                nhưng không dừng (ở clock cao bản baseline có thể không đạt; agent phải đạt); số liệu: LUT, FF, BUFG, WNS/WHS, Fmax ước lượng,
                                công suất SAIF (tổng, động, u_alu) và vector-less, cảnh báo
  4. asic_*     khối asic       ORFS sky130hd theo asic/config.mk, mỗi bước make là một task CHIA:
                                synth → floorplan → place → cts → route → finish (GDS, ảnh layout bằng KLayout)
                                → lvs (`make lvs` của ORFS: KLayout so layout GDS với netlist CDL);
                                gate: có .gds, setup/hold slack >= 0, không lỗi DRC, LVS khớp; số liệu: diện tích,
                                số cell (flop, ICG, buffer clock), slack, công suất
  Mỗi bước chỉ chạy khi bước trước qua gate.

Mỗi lần chạy có một thư mục riêng trong thư mục làm việc chung (mount vào mọi khối, xem docs/cluster-blocks.md):
  <WORK_DIR>/demo-alu/CG/<run_id>/
      rtl/, sim/, fpga/, asic/  bản chép của project (đầu vào cố định của lần chạy)
      sim/build/                build.log, sim.log, wave.vcd, obj/Vtb
      fpga/out/, fpga/reports/  fpga_top.bit, post_route.dcp, vivado.log; util, timing, power, methodology, drc
      asic/orfs/                WORK_HOME của ORFS: logs/, reports/, results/ (6_final.gds, 6_lvs.lvsdb), objects/
      asic/layout.png           ảnh layout
      results/baseline.json     số liệu từng bước (đầu vào cho pha 2: agent)
      status.html               trang trạng thái (tự làm mới, mở bằng trình duyệt; xem status_page.py)
      profile/                  timeline của CHIA: ChiaProfileCollector.log (JSONL) và _profile.html (tự sinh)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.trace.profiler import get_profiler, start_collector, stop_collector

import status_page as S

DEMO = "CG"
DUT = "CG_alu"
RESULT_RE = re.compile(r"^RESULT: (PASS|FAIL) (.*)$", re.M)


def _sh(cmd: str, cwd: str, env: dict | None = None, timeout: int = 1800) -> tuple[int, str, float]:
    t0 = time.time()
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, p.stdout + p.stderr, round(time.time() - t0, 2)


@ChiaFunction(resources={"chipyard": 1})
def sim_build(run_dir: str) -> dict:
    """Build testbench bằng Verilator của Chipyard (worker chipyard đã nạp env.sh)."""
    sim = os.path.join(run_dir, "sim")
    _, ver, _ = _sh("verilator --version", sim)
    # Gate lint: RTL của DUT (rtl/*.v, cố định) phải sạch với -Wall (cảnh báo nào cũng là lỗi).
    rtl = " ".join(sorted(str(p) for p in Path(run_dir, "rtl").glob("*.v")))
    lrc, lout, _ = _sh(f"verilator --lint-only -Wall --top-module {DUT} {rtl}", sim)
    lint_msgs = re.findall(r"^%(?:Warning|Error)-?(\w*)", lout, re.M)
    lint_ok = lrc == 0 and not lint_msgs
    rc, out, sec = _sh("./run_sim.sh", sim, {"BUILD_ONLY": "1"})
    binary = os.path.join(sim, "build", "obj", "Vtb")
    ok = lint_ok and rc == 0 and os.path.isfile(binary)
    get_profiler().add_info({"step": "sim_build", "ok": ok, "seconds": sec})
    tail = out[-2000:] if lint_ok else "LINT (verilator --lint-only -Wall) không sạch:\n" + lout[-2000:]
    return {"ok": ok, "rc": rc, "seconds": sec, "verilator": ver.strip(), "host": os.uname().nodename,
            "lint_ok": lint_ok, "lint_warnings": len(lint_msgs), "lint_ids": sorted(set(lint_msgs)), "log_tail": tail}


def _num(pattern: str, text: str, cast=float):
    m = re.search(pattern, text, re.M)
    return cast(m.group(1)) if m else None


def _read(path: str) -> str:
    try:
        return open(path, errors="replace").read()
    except OSError:
        return ""


def _timing(text: str) -> dict:
    """Dòng số ngay dưới tiêu đề WNS(ns) của Design Timing Summary (như examples/fpga_smoke.py)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("WNS(ns)") and i + 2 < len(lines):
            v = lines[i + 2].split()
            try:
                return {"wns_ns": float(v[0]), "tns_ns": float(v[1]), "whs_ns": float(v[4]),
                        "ths_ns": float(v[5]), "hold_failing_endpoints": int(float(v[6]))}
            except (IndexError, ValueError):
                break
    return {}


# ---------------- FPGA (Vivado) ----------------
# Biến thể của bước FPGA: thay file trong sources.f và thêm tuỳ chọn synth_design. Chỉ dành cho FPGA (rtl/ cố định).
FPGA_VARIANTS = {
    "latch": {"replace": {}, "synth": [], "note": "icg_sim.v nguyên bản: latch + LUT trên đường clock"},
    "bufgce": {"replace": {"../rtl/icg_sim.v": "icg_fpga_bufgce.v"}, "synth": [],
               "note": "icg bằng BUFGCE"},
    "ce": {"replace": {"../rtl/icg_sim.v": "icg_fpga_ce.v"}, "synth": ["-gated_clock_conversion", "auto"],
           "note": "Vivado đổi clock gating thành clock enable"},
}
SAIF_MIN_MATCH_PCT = 50                              # dưới mức này coi như không đo được công suất SAIF
FPGA_EN_THR_DEFAULT = 38                              # fpga_top: tỉ lệ gửi phép = EN_THR/128 (38 ~ 30 %)


def en_thr(en_pct: float) -> int:
    return FPGA_EN_THR_DEFAULT if round(en_pct) == 30 else max(0, min(128, round(en_pct * 128 / 100)))


def prepare_fpga(run_dir: str, period_ns: float, variant: str, en_pct: float = 30) -> dict:
    """Ghi các file cố định của lần chạy vào <run_dir>/fpga: clock.xdc (chu kỳ), sources.f (biến thể),
    synth_opts.tcl (tuỳ chọn synth_design của biến thể, generic EN_THR/EXP_SIG khi đổi tỉ lệ gửi phép)."""
    if variant not in FPGA_VARIANTS:
        raise SystemExit(f"LỖI: biến thể FPGA {variant!r} không có; chọn trong {', '.join(FPGA_VARIANTS)}")
    v = FPGA_VARIANTS[variant]
    fpga = Path(run_dir, "fpga")
    (fpga / "clock.xdc").write_text(
        "## clock.xdc - chu kỳ clock của bước FPGA (baseline.py ghi theo --fpga-period; agent không sửa được).\n"
        f"create_clock -name sys_clk -period {period_ns:.3f} [get_ports clk]\n")
    srcs = (fpga / "sources.f").read_text().splitlines()
    srcs = [v["replace"].get(ln.strip(), ln) for ln in srcs]
    (fpga / "sources.f").write_text("\n".join(srcs) + "\n")
    args = list(v["synth"])
    thr = en_thr(en_pct)
    if thr != FPGA_EN_THR_DEFAULT:
        import importlib.util
        spec = importlib.util.spec_from_file_location("golden_sig", fpga / "golden_sig.py")
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        sig, _ = g.golden(1048576, 0xACE12025, thr)
        args += ["-generic", f"EN_THR=8'd{thr}", "-generic", f"EXP_SIG=32'h{sig:08X}"]
    (fpga / "synth_opts.tcl").write_text(
        f"# synth_opts.tcl - biến thể {variant} ({v['note']}), tỉ lệ gửi phép {thr}/128. baseline.py ghi, cố định.\n"
        "set synth_args {" + " ".join(args) + "}\n")
    return {"period_ns": period_ns, "variant": variant, "en_thr": thr, "synth_args": args}


def _period(fpga: str) -> float | None:
    m = re.search(r"-period\s+([\d.]+)", _read(os.path.join(fpga, "clock.xdc")))
    return float(m.group(1)) if m else None


def _power(text: str) -> dict:
    """report_power (đơn vị W hoặc mW): tổng, động, tĩnh, độ tin cậy, tỉ lệ net khớp SAIF, công suất của u_alu."""
    unit = {"W": 1.0, "mW": 1e-3, "uW": 1e-6}

    def val(label):
        m = re.search(rf"^\|\s*{label} \((W|mW|uW)\)\s*\|\s*([\d.]+)", text, re.M)
        return float(m.group(2)) * unit[m.group(1)] if m else None
    hu = re.search(r"^\|\s*Name\s*\|\s*Power \((W|mW|uW)\)\s*\|", text, re.M)
    alu = re.search(r"^\|\s+u_alu\s+\|\s*([\d.]+)", text, re.M)
    conf = re.search(r"^\|\s*Confidence Level\s*\|\s*(\w+)", text, re.M)
    match = re.search(r"^\|\s*Design Nets Matched\s*\|\s*([\d.]+)%", text, re.M)
    return {"total_w": val("Total On-Chip Power"), "dynamic_w": val("Dynamic"),
            "static_w": val("Device Static"),
            "alu_w": float(alu.group(1)) * unit[hu.group(1)] if alu and hu else None,
            "confidence": conf.group(1) if conf else None,
            "nets_matched_pct": float(match.group(1)) if match else None}


@ChiaFunction(resources={"vivado": 1})
def fpga_build(run_dir: str, timeout_s: int = 3600, power_cycles: int = 20000) -> dict:
    """Vivado batch theo fpga/build.tcl (worker vivado đã nạp settings64.sh), rồi power_saif.tcl: công suất theo
    hoạt động thật (xsim netlist sau route với tb_power.v, SAIF) khi power_cycles > 0.
    ok: có .bit, clock trong báo cáo timing đúng clock.xdc, đo được công suất SAIF. timing_met: WNS >= 0 và WHS >= 0
    (gate của agent; baseline ghi lại cả khi không đạt, vì ở clock cao bản baseline có thể không đạt timing)."""
    fpga = os.path.join(run_dir, "fpga")
    os.makedirs(os.path.join(fpga, "out"), exist_ok=True)
    _, ver, _ = _sh("vivado -version | head -1", fpga)
    rc, out, sec = _sh("vivado -mode batch -nojournal -log out/vivado.log -source build.tcl", fpga, timeout=timeout_s)
    rep = lambda name: _read(os.path.join(fpga, "reports", name))
    util, log, timing = rep("util.rpt"), _read(os.path.join(fpga, "out", "vivado.log")), rep("timing.rpt")
    vl = _power(rep("power.rpt"))
    bit = os.path.join(fpga, "out", "fpga_top.bit")
    period = _period(fpga)
    clk = re.search(r"^\s*sys_clk\s+\{[^}]*\}\s+([\d.]+)\s+([\d.]+)", timing, re.M)
    res = {
        "vivado": ver.strip(), "rc": rc, "seconds": sec, "host": os.uname().nodename,
        "bitstream": bit if os.path.isfile(bit) else None,
        **_timing(timing),
        "clock_period_ns": period, "clock_mhz": round(1000 / period, 2) if period else None,
        "clock_in_report_ns": float(clk.group(1)) if clk else None,
        "lut": _num(r"^\|\s*Slice LUTs\*?\s*\|\s*(\d+)", util, int),
        "ff": _num(r"^\|\s*Slice Registers\s*\|\s*(\d+)", util, int),
        "bufg": _num(r"^\|\s*BUFGCTRL\s*\|\s*(\d+)", util, int),
        # Ước lượng vector-less (cách đo cũ, chỉ để đối chiếu).
        "power_total_vl_w": vl["total_w"], "power_dynamic_vl_w": vl["dynamic_w"],
        "critical_warnings": len(re.findall(r"^CRITICAL WARNING:", log, re.M)),
        "warnings": len(re.findall(r"^WARNING:", log, re.M)),
        # Các mã cảnh báo duy nhất (ví dụ PDRC-153 gated clock): đầu vào cho agent ở pha 2.
        "warning_ids": sorted(set(re.findall(r"^(?:CRITICAL )?WARNING: \[([^\]]+)\]", log, re.M))),
        "reports": os.path.join(fpga, "reports"),
    }
    wns, whs = res.get("wns_ns"), res.get("whs_ns")
    res["fmax_est_mhz"] = round(1000 / (period - wns), 1) if period and wns is not None and period - wns > 0 else None
    res["timing_met"] = wns is not None and wns >= 0 and (whs is None or whs >= 0)
    clock_ok = period is not None and res["clock_in_report_ns"] is not None and \
        abs(res["clock_in_report_ns"] - period) < 1e-3
    tail = out[-3000:]
    power_ok = power_cycles <= 0
    if res["bitstream"] and power_cycles > 0:
        prc, pout, psec = _sh(f"vivado -mode batch -nojournal -log out/power.log -source power_saif.tcl "
                              f"-tclargs {period} {power_cycles}", fpga, timeout=timeout_s)
        p = _power(rep("power_saif.rpt"))
        res.update({"power_seconds": psec, "power_cycles": power_cycles, "power_total_w": p["total_w"],
                    "power_dynamic_w": p["dynamic_w"], "power_static_w": p["static_w"], "power_alu_w": p["alu_w"],
                    "power_confidence": p["confidence"], "saif_nets_matched_pct": p["nets_matched_pct"]})
        # SAIF chỉ có nghĩa khi phần lớn net khớp tên (lần chạy 100 MHz: 75 %); khớp ít thì Vivado tự ước lượng như
        # vector-less, số công suất không dùng được.
        res["saif_ok"] = (p["nets_matched_pct"] or 0) >= SAIF_MIN_MATCH_PCT
        power_ok = prc == 0 and p["dynamic_w"] is not None and res["saif_ok"]
        if not power_ok:
            tail += (f"\nSAIF khớp {p['nets_matched_pct']} % net (cần >= {SAIF_MIN_MATCH_PCT} %)" if not res["saif_ok"]
                     else "") + "\n--- power_saif.tcl ---\n" + pout[-3000:]
    # Methodology: luật mức Critical Warning/Error (ví dụ TIMING-14: LUT trên cây clock) phải bằng 0 ở bản agent nhận.
    meth = {}
    for rule, sev, n in re.findall(r"^\|\s*([A-Z]+-\d+)\s*\|\s*(Critical Warning|Error|Warning|Advisory)\s*\|[^|]*\|\s*(\d+)",
                                   rep("methodology.rpt"), re.M):
        meth[rule] = {"severity": sev, "count": int(n)}
    res["methodology"] = meth
    res["methodology_critical"] = sum(v["count"] for v in meth.values() if v["severity"] in ("Critical Warning", "Error"))
    res["signoff_met"] = res["timing_met"] and res["methodology_critical"] == 0
    # Mô phỏng gate-level: netlist sau route (out/funcsim.v, power_saif.tcl ghi) với tb_gls.v, chạy hết một khung và
    # kiểm LED PASS (MISR khớp chữ ký). Chỉ chạy khi có đo công suất (fpga_sweep fmax bỏ qua cả hai).
    gls_ok = power_cycles <= 0
    if power_ok and power_cycles > 0 and os.path.isfile(os.path.join(fpga, "out", "funcsim.v")):
        ps = int(round(period * 1000 / 2)) * 2
        grc, gout, gsec = _sh("rm -rf xsim_gls && mkdir xsim_gls && cd xsim_gls && "
                              f"xvlog -nolog -d TB_PERIOD_PS={ps} ../out/funcsim.v ../tb_gls.v "
                              "$XILINX_VIVADO/data/verilog/src/glbl.v && "
                              "xelab -nolog -L unisims_ver -s tbg tb_gls glbl && xsim -nolog tbg -R",
                              fpga, timeout=timeout_s)
        Path(fpga, "xsim_gls.log").write_text(gout)
        m = re.search(r"^RESULT: (PASS|FAIL) gls", gout, re.M)
        gls_ok = grc == 0 and bool(m) and m.group(1) == "PASS"
        res.update({"gls_ok": gls_ok, "gls_seconds": gsec, "gls_result": m.group(0) if m else None})
        if not gls_ok:
            tail += "\n--- mô phỏng gate-level (tb_gls.v) ---\n" + gout[-2000:]
    elif power_cycles > 0:
        res["gls_ok"] = False
    res["ok"] = rc == 0 and res["bitstream"] is not None and wns is not None and clock_ok and power_ok and gls_ok
    if not clock_ok:
        tail += f"\nclock trong timing.rpt ({res['clock_in_report_ns']}) khác clock.xdc ({period})"
    res["log_tail"] = tail
    get_profiler().add_info({"step": "fpga", "ok": res["ok"], "seconds": sec, "wns_ns": wns,
                             "lut": res["lut"], "ff": res["ff"]})
    return res


# ---------------- ASIC (ORFS) ----------------
ORFS_FLOW = "/OpenROAD-flow-scripts/flow"            # trong khối asic (ORFS của host mount vào đây)
ASIC_STAGES = ["synth", "floorplan", "place", "cts", "route", "finish", "drc", "lvs", "sta", "gls_prep"]
TT_LIB = f"{ORFS_FLOW}/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
STA_DERATE = 0.10                                    # corner OCV: delay ±10 % (thiếu .lib ss/ff thật)


def _orfs_paths(run_dir: str) -> dict:
    asic = os.path.join(run_dir, "asic")
    work = os.path.join(asic, "orfs")
    sub = os.path.join("sky130hd", DUT, "base")
    return {"asic": asic, "work": work, "config": os.path.join(asic, "config.mk"),
            **{k: os.path.join(work, k, sub) for k in ("logs", "reports", "results")}}


def _run_logged(cmd: list[str], cwd: str, timeout: int, log_path: str) -> tuple[int, float, str]:
    """Chạy trong process group riêng, log ra file; quá giờ thì giết cả nhóm (make, yosys, openroad)."""
    t0 = time.time()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            rc = -1
    return rc, round(time.time() - t0, 2), _read(log_path)[-3000:]


@ChiaFunction(resources={"asic": 1})
def asic_stage(run_dir: str, stage: str, timeout_s: int = 3600) -> dict:
    """Một bước `make <stage>` của ORFS (worker asic đã nạp env.sh của ORFS). WORK_HOME trỏ vào thư mục run, nên
    ORFS trên host không bị ghi. Bước finish còn đọc số liệu và vẽ ảnh layout."""
    P = _orfs_paths(run_dir)
    if stage in ("sta", "gls_prep"):                  # bước sign-off riêng của demo, không phải target make của ORFS
        t0 = time.time()
        res = (_sta_corners if stage == "sta" else _gls_prep)(P, timeout_s)
        res.update({"stage": stage, "seconds": round(time.time() - t0, 2), "host": os.uname().nodename})
        get_profiler().add_info({"step": f"asic_{stage}", "ok": res["ok"], "seconds": res["seconds"]})
        return res
    cmd = ["make", "-C", ORFS_FLOW, f"DESIGN_CONFIG={P['config']}", f"WORK_HOME={P['work']}", stage]
    rc, sec, tail = _run_logged(cmd, ORFS_FLOW, timeout_s, os.path.join(P["asic"], "make", f"{stage}.log"))
    res = {"stage": stage, "rc": rc, "seconds": sec, "host": os.uname().nodename, "ok": rc == 0, "log_tail": tail}
    if stage == "synth":
        _, ver, _ = _sh("yosys -V; openroad -version 2>/dev/null | head -1", ORFS_FLOW)
        res["tools"] = ver.strip()
        if rc == 0 and not os.path.isdir(P["results"]):
            res["ok"] = False
            res["log_tail"] = (f"make synth xong nhưng không có {P['results']}: ORFS này không nhận WORK_HOME? "
                               f"xem {ORFS_FLOW}/results/sky130hd/{DUT}\n") + tail
    if stage == "finish" and rc == 0:
        res.update(_asic_metrics(P))
        res["ok"] = res["gate_ok"]
    if stage == "drc":
        res.update(_drc_result(P))
        res["ok"] = rc == 0 and res["drc_klayout"] == 0
    if stage == "lvs":
        res.update(_lvs_result(P, tail))
        res["ok"] = rc == 0 and res["lvs"] == "match"
    get_profiler().add_info({"step": f"asic_{stage}", "ok": res["ok"], "seconds": sec})
    return res


def _asic_metrics(P: dict) -> dict:
    """Số liệu sau finish: metrics JSON của ORFS (logs/*.json), báo cáo 6_finish.rpt, netlist 6_final.v."""
    metrics = {}
    for f in sorted(Path(P["logs"]).glob("*.json")):
        try:
            metrics.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            pass
    fin = _read(os.path.join(P["reports"], "6_finish.rpt"))

    def m(keys, pattern=None):
        for k in keys:
            if isinstance(metrics.get(k), (int, float)):
                return metrics[k]
        return _num(pattern, fin) if pattern else None

    gds = os.path.join(P["results"], "6_final.gds")
    r = {
        "gds": gds if os.path.isfile(gds) else None,
        "setup_ws_ns": m(["finish__timing__setup__ws"], r"^worst slack max\s+(-?[\d.]+)"),
        "hold_ws_ns": m(["finish__timing__hold__ws"], r"^worst slack min\s+(-?[\d.]+)"),
        "setup_tns_ns": m(["finish__timing__setup__tns"], r"^tns(?: max)?\s+(-?[\d.]+)"),
        "area_um2": m(["finish__design__instance__area"], r"^Design area\s+([\d.]+)\s+u\^2"),
        "util_pct": m(["finish__design__instance__utilization"], r"^Design area\s+[\d.]+\s+u\^2\s+([\d.]+)%"),
        "core_area_um2": m(["finish__design__core__area", "floorplan__design__core__area"]),
        "die_area_um2": m(["finish__design__die__area", "floorplan__design__die__area"]),
        "stdcells": m(["finish__design__instance__count__stdcell"]),
        "power_total_w": m(["finish__power__total"], r"^Total\s+\S+\s+\S+\s+\S+\s+([\d.eE+-]+)"),
        "drc_errors": m(["detailedroute__route__drc_errors", "route__drc_errors"]),
        "antenna_nets": m(["detailedroute__antenna__violating__nets", "finish__antenna__violating__nets"]),
        "clock_period_ns": m(["finish__timing__clock__period"]),
    }
    if isinstance(r["util_pct"], float) and r["util_pct"] <= 1.0 and "finish__design__instance__utilization" in metrics:
        r["util_pct"] = round(100 * r["util_pct"], 2)           # metrics JSON ghi tỉ lệ 0..1
    # Đếm cell theo loại trong netlist cuối (không tính cell vật lý: tap, decap, fill).
    cells = {}
    for t in re.findall(r"^\s*sky130_fd_sc_hd__(\w+?)_\d+\s+\S+\s*\(", _read(os.path.join(P["results"], "6_final.v")), re.M):
        cells[t] = cells.get(t, 0) + 1
    phys = ("tapvpwrvgnd", "decap", "fill", "tap")
    logic = {k: v for k, v in cells.items() if not k.startswith(phys)}
    r["cells"] = dict(sorted(logic.items(), key=lambda kv: -kv[1]))
    r["n_flop"] = sum(v for k, v in logic.items() if re.match(r"(s?df|edf|sedf|dlx|dlr)", k))
    r["n_icg"] = sum(v for k, v in logic.items() if "dlclkp" in k)
    r["n_clkbuf"] = sum(v for k, v in logic.items() if k.startswith(("clkbuf", "clkinv", "clkdlybuf")))
    r["n_logic_cells"] = sum(logic.values())
    # Thời gian từng bước con của ORFS (dòng "Elapsed time" ở cuối mỗi log).
    steps = {}
    for f in sorted(Path(P["logs"]).glob("*.log")):
        mm = re.search(r"Elapsed time: (?:(\d+):)?(\d+):([\d.]+)", _read(str(f)))
        if mm:
            steps[f.stem] = round(int(mm.group(1) or 0) * 3600 + int(mm.group(2)) * 60 + float(mm.group(3)), 1)
    r["orfs_step_seconds"] = steps
    # Ảnh layout cho slide/video (không thuộc gate).
    png = os.path.join(P["asic"], "layout.png")
    if r["gds"]:
        lyp = next(iter(sorted(Path(ORFS_FLOW, "platforms", "sky130hd").glob("*.lyp"))), None)
        cmd = f"klayout -zz -r render_gds.py -rd gds={r['gds']} -rd png={png}" + (f" -rd lyp={lyp}" if lyp else "")
        rc, out, _ = _sh(cmd, P["asic"], timeout=600)
        r["layout_png"] = png if os.path.isfile(png) else None
        if not r["layout_png"]:
            r["layout_png_error"] = out[-1000:]
    r["reports"] = P["reports"]
    r["metrics_json"] = {k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}
    r["gate_ok"] = (r["gds"] is not None and r["setup_ws_ns"] is not None and r["setup_ws_ns"] >= 0
                    and (r["hold_ws_ns"] is None or r["hold_ws_ns"] >= 0) and not r["drc_errors"]
                    and r["antenna_nets"] == 0)       # antenna: metrics của ORFS phải có và bằng 0
    return r


def _sta_corners(P: dict, timeout_s: int) -> dict:
    """STA sau route ở nhiều corner bằng asic/signoff.tcl qua `make run` của ORFS: tt (thư viện của flow, phải khớp số
    của bước finish), tt + OCV ±STA_DERATE, và mọi .lib ss/ff có trong platforms/sky130hd/lib (corner thật, nếu có).
    Gate: setup và hold slack >= 0 ở mọi corner."""
    corners = [("tt", None, 0.0), (f"tt_ocv{int(STA_DERATE * 100)}", None, STA_DERATE)]
    for lib in sorted(Path(ORFS_FLOW, "platforms", "sky130hd", "lib").glob("sky130_fd_sc_hd__[sf][sf]_*.lib")):
        corners.append((lib.stem.replace("sky130_fd_sc_hd__", ""), str(lib), 0.0))
    out, logs, ok = {}, "", True
    for name, lib, derate in corners:
        cmd = ["make", "-C", ORFS_FLOW, f"DESIGN_CONFIG={P['config']}", f"WORK_HOME={P['work']}", "run",
               f"RUN_SCRIPT={os.path.join(P['asic'], 'signoff.tcl')}", f"RUN_LOG_NAME_STEM=sta_{name}"]
        if lib:
            cmd.append(f"LIB_FILES={lib}")
        os.environ["SIGNOFF_CORNER"], os.environ["SIGNOFF_DERATE"] = name, str(derate)
        rc, _, tail = _run_logged(cmd, ORFS_FLOW, timeout_s, os.path.join(P["asic"], "make", f"sta_{name}.log"))
        text = _read(os.path.join(P["asic"], "make", f"sta_{name}.log"))
        m = re.search(r"^SIGNOFF corner=\S+ parasitics=(\S+) derate=\S+ libs=\d+ setup_ws=(\S+) setup_tns=(\S+) "
                      r"hold_ws=(\S+) hold_tns=(\S+)", text, re.M)
        f = lambda x: None if x in (None, "NA") else float(x)
        c = {"lib": lib or "LIB_FILES của flow", "derate": derate, "rc": rc}
        if m:
            c.update({"parasitics": m.group(1), "setup_ws_ns": f(m.group(2)), "setup_tns_ns": f(m.group(3)),
                      "hold_ws_ns": f(m.group(4)), "hold_tns_ns": f(m.group(5))})
        if c.get("setup_ws_ns") is None:              # dự phòng: dòng in của report_worst_slack
            ws = re.findall(r"^worst slack (?:max )?(-?[\d.]+)", text, re.M)
            wh = re.findall(r"^worst slack min (-?[\d.]+)", text, re.M)
            c["setup_ws_ns"] = float(ws[0]) if ws else None
            c["hold_ws_ns"] = c.get("hold_ws_ns") or (float(wh[0]) if wh else None)
        c["ok"] = rc == 0 and c.get("setup_ws_ns") is not None and c["setup_ws_ns"] >= 0 and \
            c.get("hold_ws_ns") is not None and c["hold_ws_ns"] >= 0
        ok &= c["ok"]
        out[name] = c
        if not c["ok"]:
            logs += f"--- corner {name} ---\n" + tail[-1500:]
    worst = lambda k: min((c[k] for c in out.values() if c.get(k) is not None), default=None)
    return {"ok": ok, "sta_corners": out, "sta_setup_ws_min_ns": worst("setup_ws_ns"),
            "sta_hold_ws_min_ns": worst("hold_ws_ns"), "sta_real_corners": [n for n, l, _ in corners if l],
            "log_tail": logs}


def _gls_prep(P: dict, timeout_s: int) -> dict:
    """Chuẩn bị mô phỏng gate-level ASIC ở khối asic (có Yosys): model Verilog của cell sky130 từ .lib tt
    (asic/gls/build/cells.v; cell ICG dlclkp dùng asic/gls/cells_extra.v viết tay) và bản chép netlist 6_final.v."""
    b = os.path.join(P["asic"], "gls", "build")
    os.makedirs(b, exist_ok=True)
    net = os.path.join(P["results"], "6_final.v")
    rc, out, _ = _sh(f"yosys -q -p 'read_liberty -ignore_miss_func {TT_LIB}; write_verilog -noattr cells.v' && "
                     f"cp {net} 6_final.v", b, timeout=timeout_s)
    ok = rc == 0 and os.path.isfile(os.path.join(b, "cells.v")) and os.path.isfile(os.path.join(b, "6_final.v"))
    return {"ok": ok, "rc": rc, "log_tail": out[-2000:]}


@ChiaFunction(resources={"chipyard": 1})
def asic_gls(run_dir: str, plusargs: list[str], timeout_s: int = 1800) -> dict:
    """Mô phỏng gate-level của netlist ASIC sau route (6_final.v) bằng Verilator của khối chipyard, với testbench
    asic/gls/tb_gls_<DUT>.v (testbench baseline bỏ kiểm tra hộp trắng) và model cell từ _gls_prep. Gate: RESULT: PASS."""
    g = os.path.join(run_dir, "asic", "gls")
    b = os.path.join(g, "build")
    rc, out, sec = _sh(f"verilator --binary --timing -Wno-fatal -Wno-lint -Wno-style --top-module tb_{DUT} -Mdir obj "
                       f"-o Vgls ../tb_gls_{DUT}.v 6_final.v cells.v ../cells_extra.v > build.log 2>&1 && "
                       f"./obj/Vgls {' '.join(plusargs)} | tee sim.log", b, timeout=timeout_s)
    m = RESULT_RE.search(out)
    fields = dict(kv.split("=", 1) for kv in m.group(2).split()) if m else {}
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec, "host": os.uname().nodename,
           "gls_result": m.group(0) if m else None, "gls_checked": fields.get("checked"),
           "log_tail": out[-2000:] if m else _read(os.path.join(b, "build.log"))[-2000:]}
    get_profiler().add_info({"step": "asic_gls", "ok": res["ok"], "seconds": sec})
    return res


def _drc_result(P: dict) -> dict:
    """`make drc` của ORFS: KLayout chạy bộ luật DRC đầy đủ của sky130hd trên GDS cuối (khác số DRC của router sau đi
    dây). Kết quả reports/6_drc.lyrdb (XML, mỗi vi phạm một <item>), 6_drc_count.rpt (số vi phạm). Platform không có bộ
    luật thì ORFS ghi "DRC not supported" vào lyrdb."""
    lyrdb = os.path.join(P["reports"], "6_drc.lyrdb")
    db = _read(lyrdb)
    if "not supported" in db[:200]:
        return {"drc_klayout": None, "drc_status": "unsupported", "drc_lyrdb": lyrdb,
                "log_tail": f"ORFS này không có bộ luật DRC KLayout cho sky130hd (KLAYOUT_DRC_FILE rỗng): xem "
                            f"{ORFS_FLOW}/platforms/sky130hd/config.mk"}
    if not db:
        return {"drc_klayout": None, "drc_status": "missing", "drc_lyrdb": None}
    items = db.split("<items>", 1)[1] if "<items>" in db else ""
    rules = {}
    for r in re.findall(r"<category>'?([^<']+)'?</category>", items):
        rules[r] = rules.get(r, 0) + 1
    n = sum(rules.values())
    cnt = _read(os.path.join(P["reports"], "6_drc_count.rpt")).strip()
    if cnt.isdigit():
        n = int(cnt)
    return {"drc_klayout": n, "drc_status": "clean" if n == 0 else "violations",
            "drc_rules": dict(sorted(rules.items(), key=lambda kv: -kv[1])[:20]), "drc_lyrdb": lyrdb}


def _lvs_result(P: dict, make_tail: str) -> dict:
    """Kết quả `make lvs` của ORFS. Deck Python (klayout_lvs.py) in "Congratulations! Netlists match." hoặc
    "Netlists don't match" và luôn thoát 0, nên phải đọc chữ; deck Ruby cũ ghi trạng thái vào 6_lvs.lvsdb. Platform
    không có deck thì ORFS ghi "LVS not supported on this platform" vào lvsdb (không phải lỗi make)."""
    lvsdb = os.path.join(P["results"], "6_lvs.lvsdb")
    db = _read(lvsdb)
    log = _read(os.path.join(P["logs"], "6_lvs.log")) + _read(os.path.join(P["asic"], "make", "lvs.log"))
    if "not supported" in db[:200]:
        st = "unsupported"
    elif re.search(r"Netlists don'?t match|Netlists do not match", log):
        st = "mismatch"
    elif re.search(r"Netlists match", log):
        st = "match"
    else:
        # lvsdb (định dạng chữ của KLayout): dòng xref của mạch top, ví dụ " circuit(CG_alu CG_alu match".
        m = re.search(rf"circuit\({DUT} {DUT} (\w+)", db)
        st = {"match": "match", "nomatch": "mismatch", "mismatch": "mismatch"}.get(m.group(1), "unknown") if m \
            else "unknown"
    r = {"lvs": st, "lvsdb": lvsdb if os.path.isfile(lvsdb) else None,
         "lvs_log": os.path.join(P["logs"], "6_lvs.log")}
    if st == "unsupported":
        r["log_tail"] = ("ORFS này không có deck LVS cho sky130hd (KLAYOUT_LVS_FILE rỗng): xem "
                         f"{ORFS_FLOW}/platforms/sky130hd/config.mk\n") + make_tail
    return r


FCOV_RE = re.compile(r"^FCOV: hit=(\d+) total=(\d+)", re.M)
FCOV_MISS_RE = re.compile(r"^FCOV_MISS: (.*)$", re.M)


def _struct_cov(dat: str, dut_files: set[str]) -> dict:
    """coverage.dat của Verilator: mỗi điểm là `C '<khoá>' <số lần>`, khoá gồm các cặp \\x01<tên>\\x02<giá trị>
    (f: file, page: v_line/..., h: hierarchy). Chỉ đếm điểm trong file RTL của DUT. Trả {loại: [đã gặp, tổng]}."""
    out: dict[str, list[int]] = {}
    for key, n in re.findall(r"^C '(.*)' (\d+)$", dat, re.M):
        f = dict(re.findall(r"\x01([^\x02]+)\x02([^\x01]*)", key))
        if os.path.basename(f.get("f", "")) not in dut_files:
            continue
        kind = f.get("page", "?").split("/")[0].removeprefix("v_")
        c = out.setdefault(kind, [0, 0])
        c[0] += int(n) > 0
        c[1] += 1
    return out


@ChiaFunction(resources={"verilator_run": 1})
def sim_run(run_dir: str, plusargs: list[str]) -> dict:
    """Chạy binary đã build. Gate: dòng RESULT là PASS."""
    sim = os.path.join(run_dir, "sim")
    rc, out, sec = _sh("./run_sim.sh " + " ".join(plusargs), sim, {"SKIP_BUILD": "1"})
    m = RESULT_RE.search(out)
    fields = dict(kv.split("=", 1) for kv in m.group(2).split()) if m else {}
    vcd = os.path.join(sim, "build", "wave.vcd")
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec,
           "result": m.group(0) if m else None, **fields,
           "vcd": vcd if os.path.isfile(vcd) else None,
           "vcd_bytes": os.path.getsize(vcd) if os.path.isfile(vcd) else 0,
           "host": os.uname().nodename, "log_tail": out[-2000:]}
    fc, miss = FCOV_RE.search(out), FCOV_MISS_RE.search(out)
    res["fcov_hit"], res["fcov_total"] = (int(fc.group(1)), int(fc.group(2))) if fc else (None, None)
    res["fcov_miss"] = [] if not miss or miss.group(1).strip() == "-" else miss.group(1).strip().split(",")
    dut_files = {p.name for p in Path(run_dir, "rtl").glob("*.v")}
    cov = _struct_cov(_read(os.path.join(sim, "build", "coverage.dat")), dut_files)
    for kind in ("line", "toggle", "branch"):
        res[f"cov_{kind}"] = cov.get(kind, [None, None])[0]
        res[f"cov_{kind}_total"] = cov.get(kind, [None, None])[1]
    if "FCOV_ERROR" in out:                      # kiểm tra âm của fcov (thanh ghi vào đổi khi rảnh) cũng là lỗi
        res["ok"] = False
    get_profiler().add_info({"step": "sim_run", "ok": res["ok"], "seconds": sec})
    return res


def _sha256(paths: list[Path]) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")),
                    help="thư mục làm việc chung (WORK_DIR của cluster), mặc định ~/chia_demo")
    ap.add_argument("--run-id", default=time.strftime("baseline-%Y%m%d-%H%M%S"))
    ap.add_argument("--stages", default="sim,fpga,asic", help="các bước cần chạy, theo thứ tự (mặc định sim,fpga,asic)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cycles", type=int, default=20000)
    ap.add_argument("--en-pct", type=int, default=30)
    ap.add_argument("--fpga-period", type=float, default=10.0, help="chu kỳ clock của bước FPGA (ns), mặc định 10")
    ap.add_argument("--fpga-variant", default=next(iter(FPGA_VARIANTS)),
                    help="biến thể FPGA: " + ", ".join(f"{k} ({v['note']})" for k, v in FPGA_VARIANTS.items()))
    ap.add_argument("--fpga-en-pct", type=float, default=30, help="tỉ lệ gửi phép của fpga_top (%%), mặc định 30")
    ap.add_argument("--power-cycles", type=int, default=20000, help="số chu kỳ mô phỏng để ghi SAIF (0: không đo)")
    args = ap.parse_args()

    src = Path(__file__).resolve().parent            # bản chép --working-dir của project
    run_dir = Path(args.work_dir) / "demo-alu" / DEMO / args.run_id
    if run_dir.exists():
        raise SystemExit(f"LỖI: {run_dir} đã có, chọn --run-id khác")
    stages = [x.strip() for x in args.stages.split(",") if x.strip()]
    for sub in ("rtl", "sim", "fpga", "asic"):
        shutil.copytree(src / sub, run_dir / sub, ignore=shutil.ignore_patterns("build"))
    (run_dir / "results").mkdir()
    fpga_cfg = prepare_fpga(str(run_dir), args.fpga_period, args.fpga_variant, args.fpga_en_pct)

    ray.init(address="auto", ignore_reinit_error=True)
    for st, res_name, flag in (("fpga", "vivado", "--fpga"), ("asic", "asic", "--asic")):
        if st in stages and not ray.cluster_resources().get(res_name):
            raise SystemExit(f"LỖI: cluster không có khối {res_name}: bật bằng `python scripts/chia-up-blocks.py "
                             f"--fpga --asic`, hoặc bỏ {st} khỏi --stages")
    start_collector(log_dir=str(run_dir / "profile"))

    report = {"demo": DEMO, "dut": DUT, "phase": "baseline", "run_id": args.run_id, "run_dir": str(run_dir),
              "rtl_sha256": _sha256(sorted((run_dir / "rtl").glob("*.v"))),
              "params": {"seed": args.seed, "cycles": args.cycles, "en_pct": args.en_pct, "stages": stages,
                         "fpga": fpga_cfg, "power_cycles": args.power_cycles},
              "steps": {}, "stopped_at": None, "t0": time.time()}
    print(f"[baseline {DEMO}] run_dir = {run_dir}", flush=True)
    S.write(report)
    print(f"[baseline {DEMO}] trạng thái: {run_dir / 'status.html'}", flush=True)

    def done(step: str, res: dict) -> bool:
        report["steps"][step] = res
        report["live"] = None
        S.write(report)
        print(f"[baseline {DEMO}] {step:15s} {'OK  ' if res['ok'] else 'FAIL'} {res['seconds']:7.2f}s"
              f"  @ {res.get('host', '?')}", flush=True)
        if not res["ok"]:
            report["stopped_at"] = step
            print(res.get("log_tail", ""), flush=True)
        return res["ok"]

    # Mỗi bước chỉ chạy khi bước trước qua gate. get() rồi mới gọi bước sau (profiler bọc ObjectRef).
    ok = True
    if "sim" in stages:
        # 1-2. Sim: build ở khối chipyard, chạy ở khối verilator.
        S.set_live(report, task="sim_build")
        rb = get(sim_build.chia_remote(str(run_dir)))
        ok = done("sim_build", rb)
        print(f"[baseline {DEMO}] lint (verilator -Wall): {'sạch' if rb.get('lint_ok') else 'KHÔNG sạch'} "
              f"({rb.get('lint_warnings')} cảnh báo {rb.get('lint_ids') or ''})", flush=True)
        if ok:
            plus = [f"+seed={args.seed}", f"+cycles={args.cycles}", f"+en_pct={args.en_pct}"]
            S.set_live(report, task="sim_run")
            r = get(sim_run.chia_remote(str(run_dir), plus))
            ok = done("sim_run", r)
            if ok:
                print(f"[baseline {DEMO}] {r['result']}", flush=True)
                print(f"[baseline {DEMO}] coverage: functional {r['fcov_hit']}/{r['fcov_total']} bin, "
                      f"line {r['cov_line']}/{r['cov_line_total']}, toggle {r['cov_toggle']}/{r['cov_toggle_total']}, "
                      f"branch {r['cov_branch']}/{r['cov_branch_total']} (DUT); thiếu: {', '.join(r['fcov_miss']) or '-'}",
                      flush=True)
                print(f"[baseline {DEMO}] waveform: {r['vcd']} ({r['vcd_bytes']} byte)", flush=True)
    if ok and "fpga" in stages:
        # 3. FPGA: Vivado ở khối vivado.
        S.set_live(report, task="fpga")
        r = get(fpga_build.chia_remote(str(run_dir), power_cycles=args.power_cycles))
        ok = done("fpga", r)
        mw = lambda w: f"{w * 1000:.3f}" if isinstance(w, (int, float)) else "?"
        print(f"[baseline {DEMO}] fpga ({fpga_cfg['variant']}, {r['clock_mhz']} MHz, gửi phép {fpga_cfg['en_thr']}/128): "
              f"LUT={r['lut']} FF={r['ff']} BUFG={r['bufg']} WNS={r.get('wns_ns')} WHS={r.get('whs_ns')} ns "
              f"(Fmax ước lượng {r['fmax_est_mhz']} MHz, timing {'đạt' if r['timing_met'] else 'KHÔNG đạt'})  "
              f"cảnh báo={r['warnings']} (critical {r['critical_warnings']})", flush=True)
        print(f"[baseline {DEMO}] fpga công suất SAIF: động {mw(r.get('power_dynamic_w'))} mW, u_alu "
              f"{mw(r.get('power_alu_w'))} mW, tổng {mw(r.get('power_total_w'))} mW (độ tin cậy "
              f"{r.get('power_confidence')}, net khớp {r.get('saif_nets_matched_pct')} %); vector-less: động "
              f"{mw(r.get('power_dynamic_vl_w'))} mW", flush=True)
        print(f"[baseline {DEMO}] bitstream: {r['bitstream']}", flush=True)
        print(f"[baseline {DEMO}] fpga sign-off: methodology critical={r.get('methodology_critical')} "
              f"({', '.join(k for k, v in (r.get('methodology') or {}).items() if v['severity'] != 'Advisory') or '-'}), "
              f"mô phỏng gate-level: {r.get('gls_result')}", flush=True)
        if ok and not r["signoff_met"]:
            print(f"[baseline {DEMO}] CẢNH BÁO: FPGA chưa đạt sign-off (timing hoặc methodology) ở {r['clock_mhz']} MHz (ghi lại làm mốc; "
                  f"bitstream vẫn có nên bước ASIC vẫn chạy)", flush=True)
    if ok and "asic" in stages:
        # 4. ASIC: ORFS ở khối asic, từng bước make là một task (timeline thấy rõ từng bước).
        for stage in ASIC_STAGES:
            S.set_live(report, task=f"asic_{stage}")
            r = get(asic_stage.chia_remote(str(run_dir), stage))
            ok = done(f"asic_{stage}", r)
            if not ok:
                break
        if ok:                                        # mô phỏng gate-level netlist ASIC ở khối chipyard
            S.set_live(report, task="asic_gls")
            plus = [f"+seed={args.seed}", f"+cycles={args.cycles}", f"+en_pct={args.en_pct}"]
            ok = done("asic_gls", get(asic_gls.chia_remote(str(run_dir), plus)))
        if "asic_sta" in report["steps"]:
            for n, c in report["steps"]["asic_sta"].get("sta_corners", {}).items():
                print(f"[baseline {DEMO}] sta {n:10s} setup WS={c.get('setup_ws_ns')} hold WS={c.get('hold_ws_ns')} ns "
                      f"({c.get('parasitics')}, {'đạt' if c.get('ok') else 'KHÔNG đạt'})", flush=True)
        if "asic_gls" in report["steps"]:
            print(f"[baseline {DEMO}] mô phỏng gate-level ASIC: {report['steps']['asic_gls'].get('gls_result')}", flush=True)
        if "asic_drc" in report["steps"]:
            d = report["steps"]["asic_drc"]
            print(f"[baseline {DEMO}] drc (KLayout): {d.get('drc_status')} {d.get('drc_klayout')} vi phạm "
                  f"{d.get('drc_rules') or ''}", flush=True)
        if "asic_lvs" in report["steps"]:
            print(f"[baseline {DEMO}] lvs: {report['steps']['asic_lvs']['lvs']}", flush=True)
        r = report["steps"].get("asic_finish", {})
        if "gds" in r:
            print(f"[baseline {DEMO}] asic: diện tích {r['area_um2']} um2 (util {r['util_pct']} %), "
                  f"{r['n_logic_cells']} cell (flop {r['n_flop']}, ICG {r['n_icg']}, buffer clock {r['n_clkbuf']}), "
                  f"setup WS={r['setup_ws_ns']} hold WS={r['hold_ws_ns']} ns, P={r['power_total_w']} W, "
                  f"DRC={r['drc_errors']}, antenna={r.get('antenna_nets')}", flush=True)
            print(f"[baseline {DEMO}] gds: {r['gds']}", flush=True)
            print(f"[baseline {DEMO}] layout: {r.get('layout_png')}", flush=True)

    stop_collector()                                 # đợi sự kiện cuối ghi xong vào log
    prof = run_dir / "profile" / "ChiaProfileCollector.log"
    if prof.is_file():                               # timeline HTML cho video/slide (không bắt buộc)
        subprocess.run(["chia", "viz-profile", str(prof), "--format", "html"], capture_output=True)
        report["profile"] = str(prof.with_name("ChiaProfileCollector_profile.html"))

    out = run_dir / "results" / "baseline.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    S.write(report, final=True)
    ok = report["stopped_at"] is None
    print(f"[baseline {DEMO}] số liệu: {out}", flush=True)
    if report.get("profile"):
        print(f"[baseline {DEMO}] timeline: {report['profile']}", flush=True)
    print(f"KẾT QUẢ: {'OK' if ok else 'DỪNG ở ' + report['stopped_at']}", flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
