"""tasks_fpga.py - task CHIA của bước FPGA (Vivado, khối vivado).

fpga_sim    xsim fpga/tb_fpga_top.v trên đúng các file của sources.f (UNISIM). Chỉ agent dùng: bộ lọc sớm vài giây, chặn
            file .v agent viết sai trước khi tốn khoảng 1 phút build.
fpga_build  build.tcl tới bitstream; rồi power_saif.tcl (kit): netlist sau route + xsim tb_power.v, SAIF, report_power;
            rồi mô phỏng gate-level netlist sau route với tb_gls.v. ok: có .bit, clock đúng clock.xdc, đo được công suất
            SAIF, GLS PASS. timing_met / signoff_met (thêm methodology không Critical) là gate của agent; baseline chỉ
            ghi lại (ở clock cao bản baseline có thể chưa đạt).
Quy ước của project: fpga/build.tcl đọc sources.f, clock.xdc, synth_opts.tcl, ghi out/<top>.bit, out/post_route.dcp,
reports/{util,timing,power,methodology}.rpt; tb_power.v gọi top là `dut`; tb_gls.v in `RESULT: PASS gls`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from chia.base.ChiaFunction import ChiaFunction

from flow.common import judge_log, num, profile_info, read, sh, timed


def sources(fpga_dir: str) -> list[str]:
    lines = Path(fpga_dir, "sources.f").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def timing(text: str) -> dict:
    """Dòng số ngay dưới tiêu đề WNS(ns) của Design Timing Summary."""
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


def period_of(fpga: str) -> float | None:
    m = re.search(r"-period\s+([\d.]+)", read(os.path.join(fpga, "clock.xdc")))
    return float(m.group(1)) if m else None


def power(text: str, inst: str | None) -> dict:
    """report_power (W hoặc mW): tổng, động, tĩnh, độ tin cậy, tỉ lệ net khớp SAIF, công suất của một instance."""
    unit = {"W": 1.0, "mW": 1e-3, "uW": 1e-6}

    def val(label):
        m = re.search(rf"^\|\s*{label} \((W|mW|uW)\)\s*\|\s*([\d.]+)", text, re.M)
        return float(m.group(2)) * unit[m.group(1)] if m else None
    hu = re.search(r"^\|\s*Name\s*\|\s*Power \((W|mW|uW)\)\s*\|", text, re.M)
    im = re.search(rf"^\|\s+{re.escape(inst)}\s+\|\s*([\d.]+)", text, re.M) if inst else None
    conf = re.search(r"^\|\s*Confidence Level\s*\|\s*(\w+)", text, re.M)
    match = re.search(r"^\|\s*Design Nets Matched\s*\|\s*([\d.]+)%", text, re.M)
    return {"total_w": val("Total On-Chip Power"), "dynamic_w": val("Dynamic"), "static_w": val("Device Static"),
            "inst_w": float(im.group(1)) * unit[hu.group(1)] if im and hu else None,
            "confidence": conf.group(1) if conf else None,
            "nets_matched_pct": float(match.group(1)) if match else None}


@ChiaFunction(resources={"vivado": 1})
@timed
def fpga_sim(run_dir: str, cfg: dict) -> dict:
    """tb_fpga_top.v với đúng các file của sources.f; gate: RESULT: PASS fpga_top."""
    fpga = os.path.join(run_dir, "fpga")
    srcs = " ".join(sources(fpga))
    top = cfg["fpga"]["top"]
    cmd = (f"rm -rf xsim.dir && xvlog -nolog tb_{top}.v {srcs} $XILINX_VIVADO/data/verilog/src/glbl.v"
           f" && xelab -nolog --timescale 1ns/1ps -L unisims_ver -s tbsim tb_{top} glbl && xsim -nolog tbsim -R")
    rc, log, sec = sh(cmd, fpga, timeout=1200)
    Path(fpga, "xsim.log").write_text(log)
    m = re.search(rf"^RESULT: (PASS|FAIL) {top}", log, re.M)
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec,
           "result": m.group(0) if m else None, "log_tail": log[-3000:]}
    profile_info(step="fpga_sim", ok=res["ok"], seconds=sec)
    return res


@ChiaFunction(resources={"vivado": 1})
@timed
def fpga_build(run_dir: str, cfg: dict, timeout_s: int = 3600, power_cycles: int = 20000) -> dict:
    f = cfg["fpga"]
    ooc = f.get("mode") == "ooc"
    fpga = os.path.join(run_dir, "fpga")
    os.makedirs(os.path.join(fpga, "out"), exist_ok=True)
    _, ver, _ = sh("vivado -version | head -1", fpga)
    rc, out, sec = sh("vivado -mode batch -nojournal -log out/vivado.log -source build.tcl", fpga, timeout=timeout_s)
    rep = lambda name: read(os.path.join(fpga, "reports", name))
    util, log, tim = rep("util.rpt"), read(os.path.join(fpga, "out", "vivado.log")), rep("timing.rpt")
    vl = power(rep("power.rpt"), None)
    bit = os.path.join(fpga, "out", f"{f['top']}.bit")
    period = period_of(fpga)
    clk = re.search(r"^\s*sys_clk\s+\{[^}]*\}\s+([\d.]+)\s+([\d.]+)", tim, re.M)
    dcp = os.path.join(fpga, "out", "post_route.dcp")
    res = {
        "vivado": ver.strip(), "rc": rc, "seconds": sec, "mode": f.get("mode", "board"),
        "bitstream": bit if os.path.isfile(bit) else None, "routed": os.path.isfile(dcp),
        **timing(tim),
        "clock_period_ns": period, "clock_mhz": round(1000 / period, 2) if period else None,
        "clock_in_report_ns": float(clk.group(1)) if clk else None,
        "lut": num(r"^\|\s*Slice LUTs\*?\s*\|\s*(\d+)", util, int),
        "ff": num(r"^\|\s*Slice Registers\s*\|\s*(\d+)", util, int),
        "bufg": num(r"^\|\s*BUFGCTRL\s*\|\s*(\d+)", util, int),
        "power_total_vl_w": vl["total_w"], "power_dynamic_vl_w": vl["dynamic_w"],
        "critical_warnings": len(re.findall(r"^CRITICAL WARNING:", log, re.M)),
        "warnings": len(re.findall(r"^WARNING:", log, re.M)),
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
    if res["routed"] and power_cycles > 0:
        prc, pout, psec = sh(f"vivado -mode batch -nojournal -log out/power.log -source power_saif.tcl "
                             f"-tclargs {period} {power_cycles}", fpga, timeout=timeout_s)
        p = power(rep("power_saif.rpt"), f.get("power_instance"))
        res.update({"power_seconds": psec, "power_cycles": power_cycles, "power_total_w": p["total_w"],
                    "power_dynamic_w": p["dynamic_w"], "power_static_w": p["static_w"], "power_inst_w": p["inst_w"],
                    "power_confidence": p["confidence"], "saif_nets_matched_pct": p["nets_matched_pct"]})
        # SAIF chỉ có nghĩa khi phần lớn net khớp tên; khớp ít thì Vivado tự ước lượng như vector-less.
        res["saif_ok"] = (p["nets_matched_pct"] or 0) >= f["saif_min_match_pct"]
        power_ok = prc == 0 and p["dynamic_w"] is not None and res["saif_ok"]
        if not power_ok:
            tail += (f"\nSAIF khớp {p['nets_matched_pct']} % net (cần >= {f['saif_min_match_pct']} %)"
                     if not res["saif_ok"] else "") + "\n--- power_saif.tcl ---\n" + pout[-3000:]
    meth = {}
    for rule, sev, n in re.findall(r"^\|\s*([A-Z]+-\d+)\s*\|\s*(Critical Warning|Error|Warning|Advisory)\s*\|[^|]*\|\s*(\d+)",
                                   rep("methodology.rpt"), re.M):
        meth[rule] = {"severity": sev, "count": int(n)}
    res["methodology"] = meth
    res["methodology_critical"] = sum(v["count"] for v in meth.values() if v["severity"] in ("Critical Warning", "Error"))
    res["signoff_met"] = res["timing_met"] and res["methodology_critical"] == 0
    gls_ok = power_cycles <= 0
    if ooc and power_ok and power_cycles > 0:
        # Out-of-context: lần mô phỏng ghi SAIF chính là testbench của Sim chạy trên netlist sau route (GLS).
        glog = read(os.path.join(fpga, "xsim_pw", "xsim.log"))
        gls_ok, gres, _ = judge_log(glog, cfg)
        res.update({"gls_ok": gls_ok, "gls_result": gres})
        if not gls_ok:
            tail += "\n--- mô phỏng gate-level (testbench Sim trên netlist sau route) ---\n" + glog[-2000:]
    elif power_ok and power_cycles > 0 and os.path.isfile(os.path.join(fpga, "out", "funcsim.v")):
        ps = int(round(period * 1000 / 2)) * 2
        grc, gout, gsec = sh("rm -rf xsim_gls && mkdir xsim_gls && cd xsim_gls && "
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
    built = res["routed"] if ooc else res["bitstream"] is not None
    res["ok"] = rc == 0 and built and wns is not None and clock_ok and power_ok and gls_ok
    if not clock_ok:
        tail += f"\nclock trong timing.rpt ({res['clock_in_report_ns']}) khác clock.xdc ({period})"
    res["log_tail"] = tail
    profile_info(step="fpga", ok=res["ok"], seconds=sec, wns_ns=wns, lut=res["lut"], ff=res["ff"])
    return res
