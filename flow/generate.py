"""generate.py - sinh các file mà project không tự viết, vào thư mục chạy (không ghi vào cây project).

Gọi từ Project.copy_sources. File nào project đã có thì giữ nguyên (demo ALU có đủ nên không sinh gì).
  sim/run_sim.sh        Verilator build testbench + RTL (coverage, sim_main.cpp của kit), chạy trong sim/build/
  asic/config.mk        ORFS: platform, DESIGN_NAME, VERILOG_FILES, include, SDC, CORE_UTILIZATION, PLACE_DENSITY
  asic/constraint.sdc   clock chính + độ trễ vào/ra (io_pct × chu kỳ), như designs/*/gcd của ORFS
  fpga/ (mode ooc)      sources.f (RTL), build.tcl (synth_design -mode out_of_context, place, route, báo cáo),
                        sim_cfg.tcl (testbench cho mô phỏng netlist sau route: SAIF + GLS)
"""
from __future__ import annotations

from pathlib import Path


def _write(path: Path, text: str, mode: int | None = None) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode:
        path.chmod(mode)


def rtl_files(cfg: dict) -> list[str]:
    """File RTL đọc vào công cụ (bỏ header .svh/.vh: chúng được `include)."""
    return [r for r in cfg["rtl"] if not r.endswith((".svh", ".vh"))]


def run_sim(cfg: dict) -> str:
    s = cfg["sim"]
    inc = " ".join(f"-I../{d}" for d in cfg["include_dirs"])
    files = " ".join(f"../{r}" for r in rtl_files(cfg))
    tb = Path(s["tb"]).name
    return f"""#!/usr/bin/env bash
# run_sim.sh - SINH BỞI flow/generate.py (project không có bản riêng). Build và chạy testbench bằng Verilator.
#   ./run_sim.sh [plusargs...]      BUILD_ONLY=1: chỉ build; SKIP_BUILD=1: chỉ chạy binary đã build
# Binary chạy trong build/ (file testbench ghi ra, ví dụ VCD, nằm ở đó); coverage ghi vào build/coverage.dat.
set -euo pipefail
cd "$(dirname "$0")"
B=build; mkdir -p "$B"
if [ "${{SKIP_BUILD:-0}}" != 1 ]; then
    verilator --cc --exe --build -j 4 --prefix Vtb --timing --trace --coverage -Wno-fatal -Wno-lint -Wno-style \\
        --timescale 1ns/1ps {inc} --top-module {s['tb_top']} -Mdir "$B/obj" -o Vtb \\
        {files} {tb} "$PWD/sim_main.cpp" > "$B/build.log" 2>&1 || {{ tail -30 "$B/build.log"; exit 1; }}
fi
[ "${{BUILD_ONLY:-0}}" = 1 ] && exit 0
rm -f "$B/coverage.dat"
cd "$B" && ./obj/Vtb "$@" +cov_file=coverage.dat | tee sim.log
"""


def asic_config(cfg: dict) -> str:
    a = cfg["asic"]
    files = " \\\n                          ".join(f"$(FLOW_PRJ_DIR)../{r}" for r in rtl_files(cfg))
    inc = " ".join(f"$(FLOW_PRJ_DIR)../{d}" for d in cfg["include_dirs"])
    extra = "".join(f"{x}\n" for x in a.get("config_extra", []))
    return f"""# config.mk - SINH BỞI flow/generate.py (project không có bản riêng). ORFS {a['platform']}, RTL → GDS.
# Agent ở pha 2 được sửa file này (không đổi PLATFORM, DESIGN_*, VERILOG_*, SDC_FILE).
FLOW_PRJ_DIR := $(dir $(DESIGN_CONFIG))

export PLATFORM             = {a['platform']}
export DESIGN_NAME          = {cfg['dut']}
export DESIGN_NICKNAME      = {cfg['dut']}
export VERILOG_FILES        = {files}
export VERILOG_INCLUDE_DIRS = {inc}
export SDC_FILE             = $(FLOW_PRJ_DIR)constraint.sdc

export CORE_UTILIZATION     = {a['utilization']}
export PLACE_DENSITY        = {a['place_density']}
{extra}"""


def asic_sdc(cfg: dict) -> str:
    clk, pct = cfg["clocks"][0], cfg["asic"]["io_pct"]
    return f"""# constraint.sdc - SINH BỞI flow/generate.py: clock chính và độ trễ vào/ra {pct:g} × chu kỳ (như gcd của ORFS).
current_design {cfg['dut']}
set clk_name  core_clock
set clk_port_name {clk['port']}
set clk_period {clk['period_ns']}
set clk_io_pct {pct}
set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port
set non_clock_inputs [lsearch -inline -all -not -exact [all_inputs] $clk_port]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
"""


def fpga_ooc(cfg: dict) -> dict[str, str]:
    f, s = cfg["fpga"], cfg["sim"]
    srcs = "\n".join(f"../{r}" for r in rtl_files(cfg))
    inc = " ".join(f"../{d}" for d in cfg["include_dirs"])
    build = f"""# build.tcl - SINH BỞI flow/generate.py: Vivado out-of-context cho khối {cfg['dut']} (không vỏ board, không
# bitstream). Agent ở pha 2 được sửa file này (giữ part, đọc clock.xdc và synth_opts.tcl).
#   cd <run_dir>/fpga && vivado -mode batch -nojournal -log out/vivado.log -source build.tcl
set part  {f['part']}
set top   {cfg['dut']}
file mkdir out reports
set fh [open sources.f]
set srcs {{}}
foreach ln [split [read $fh] "\\n"] {{ set ln [string trim $ln]; if {{$ln ne "" && [string index $ln 0] ne "#"}} {{ lappend srcs $ln }} }}
close $fh
read_verilog -sv $srcs
read_xdc clock.xdc
set synth_args {{}}
if {{[file exists synth_opts.tcl]}} {{ source synth_opts.tcl }}
synth_design -top $top -part $part -mode out_of_context -include_dirs {{{inc}}} {{*}}$synth_args
report_utilization -file reports/post_synth_util.rpt
opt_design
place_design
route_design
report_utilization     -file reports/util.rpt
report_utilization     -hierarchical -file reports/util_hier.rpt
report_timing_summary  -max_paths 10 -file reports/timing.rpt
report_clock_utilization -file reports/clock_util.rpt
report_power           -file reports/power.rpt
report_methodology     -file reports/methodology.rpt
report_drc             -file reports/drc.rpt
write_checkpoint -force out/post_route.dcp
puts "BUILD_DONE"
"""
    tbfiles = " ".join(f"../../{x}" for x in [s["tb"]])
    incs = " ".join(f"../../{d}" for d in cfg["include_dirs"])
    simcfg = f"""# sim_cfg.tcl - SINH BỞI flow/generate.py: testbench chạy trên netlist sau route (power_saif.tcl của kit).
set tb_top   {s['tb_top']}
set dut_inst {s['dut_instance']}
set tb_files {{{tbfiles}}}
set inc_dirs {{{incs}}}
"""
    return {"sources.f": "# sources.f - SINH BỞI flow/generate.py: RTL đọc vào Vivado (đường dẫn tính từ fpga/).\n" + srcs + "\n",
            "build.tcl": build, "sim_cfg.tcl": simcfg}


def fill(prj, dst: Path) -> None:
    cfg = prj.cfg
    if cfg["generated"]["run_sim"]:
        _write(dst / "sim" / "run_sim.sh", run_sim(cfg), 0o755)
    if "asic" in cfg["steps"] and cfg["generated"]["asic_config"]:
        _write(dst / "asic" / "config.mk", asic_config(cfg))
        _write(dst / "asic" / "constraint.sdc", asic_sdc(cfg))
    if "fpga" in cfg["steps"] and cfg["fpga"]["mode"] == "ooc":
        for name, text in fpga_ooc(cfg).items():
            _write(dst / "fpga" / name, text)
