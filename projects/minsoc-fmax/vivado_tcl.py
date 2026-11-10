"""Sinh tcl Vivado cho Arty100THarness và đọc kết quả. Không import Ray/CHIA (dùng được ở mọi khối).

Flow giống `make bitstream` của fork (fpga-shells/xilinx/common/tcl: init.tcl, synth.tcl, opt/place/route Explore),
khác ở 4 điểm:
  1. Part ép `xc7a100tcsg324-1` (fpga-shells ghi `xc7a100ticsg324-1L`); BOARD_PART chỉ đặt khi Vivado thấy board files.
  2. `--fpga-mem bram`: bỏ SimDRAM.v/.cc (DPI-C, không tổng hợp được), nạp `fpga/SimDRAM_fpga.v` (AXI4 -> BRAM).
  3. Synth một lần, implement nhiều lần từ `post_synth.dcp` (đo nhiễu P&R): biến thể 0 và 1 cùng directive Explore
     (kiểm tính tất định), biến thể >= 2 đổi directive của place_design.
  4. Không `exit 1` khi WNS < 0; in số đo dạng `@@KEY ...` để Python đọc.
"""
import re

PART = "xc7a100tcsg324-1"
BOARD = "arty_a7_100"
TOP = "Arty100THarness"
PLACE_VARIANTS = ["Explore", "Explore", "ExtraTimingOpt", "ExtraPostPlacementOpt", "WLDrivenBlockPlacement",
                  "AltSpreadLogic_high", "EarlyBlockPlacement"]
DROP_RE = re.compile(r"\.(cc|cpp|c|h|hpp|svh|vh|xdc)$")


def vsrc_list(sim_common_files: list, extra: list, fpga_mem: str) -> list:
    """Danh sách nguồn cho Vivado từ sim_files.common.f (như $(synth_list_f) của fpga/Makefile)."""
    out = []
    for f in sim_common_files:
        f = f.strip()
        if not f or f.startswith("#") or DROP_RE.search(f):
            continue
        if fpga_mem == "bram" and f.endswith("/SimDRAM.v"):
            continue
        out.append(f)
    return list(dict.fromkeys(extra + out))


def make_tcl(*, shells_tcl_dir: str, vsrcs_f: str, ip_tcls: list, run_dir: str, n_impl: int,
             write_bit: bool, board_repo: str = "", gen_xdcs: list = (), directives: list | None = None) -> str:
    """Script Vivado batch. Mọi đường dẫn tuyệt đối. Kết quả ghi vào run_dir/obj/.
    `directives`: directive place_design cho từng impl (mặc định PLACE_VARIANTS, n_impl cái đầu)."""
    variants = " ".join(directives or [PLACE_VARIANTS[i % len(PLACE_VARIANTS)] for i in range(n_impl)])
    return f"""# sinh bởi minsoc-fmax/vivado_tcl.py
set scriptdir {shells_tcl_dir}
set commondir [file dirname $scriptdir]
set board {BOARD}
set boarddir [file join [file dirname $commondir] $board]
set constraintsdir [file join $boarddir constraints]
set srcdir [file join $commondir vsrc]
set top {TOP}
set wrkdir {run_dir}/obj
set ipdir [file join $wrkdir ip]
set ip_vivado_tcls [list {" ".join(ip_tcls)}]
file mkdir $wrkdir
cd {run_dir}
{f"set_param board.repoPaths [list {board_repo}]" if board_repo else ""}
set_param messaging.defaultLimit 1000000

# --- prologue (thay prologue.tcl: ép part, BOARD_PART không bắt buộc) ---
source [file join $boarddir tcl board.tcl]
create_project -part {PART} -force $top
set bp [lindex [get_board_parts -quiet *arty-a7-100*] end]
if {{$bp ne ""}} {{ catch {{ set_property BOARD_PART $bp [current_project] }} }}
if {{[get_property PART [current_project]] ne "{PART}"}} {{ set_property PART {PART} [current_project] }}
puts "@@PART [get_property PART [current_project]]"
puts "@@BOARD_PART [get_property BOARD_PART [current_project]]"
set_property -dict [list TARGET_LANGUAGE {{Verilog}} DEFAULT_LIB {{xil_defaultlib}} IP_REPO_PATHS $ipdir] [current_project]
set fp [open {vsrcs_f} r]
set files [lsearch -not -exact -all -inline [split [read $fp] "\\n"] {{}}]
close $fp
add_files -norecurse -fileset [current_fileset] {{*}}$files
add_files -quiet -norecurse -fileset [get_filesets constrs_1] [lsort [glob -directory $constraintsdir -nocomplain {{*.tcl}}]]
add_files -quiet -norecurse -fileset [get_filesets constrs_1] [lsort [glob -directory $constraintsdir -nocomplain {{*.xdc}}]]
# xdc do elaborate sinh (shell.xdc: chân IO, clock, false path của harness)
foreach x [list {" ".join(gen_xdcs)}] {{ add_files -norecurse -fileset [get_filesets constrs_1] $x; puts "@@XDC $x" }}

# --- init + synth của fpga-shells (IP: clk_wiz, proc_sys_reset, ila, PLL harness) ---
source [file join $scriptdir init.tcl]
source [file join $scriptdir synth.tcl]
report_utilization -file $wrkdir/util_synth.rpt
report_timing_summary -max_paths 10 -file $wrkdir/timing_synth.rpt
puts "@@SYNTH_DONE"

proc minsoc_metrics {{tag}} {{
  upvar wrkdir wrkdir
  report_timing_summary -max_paths 10 -report_unconstrained -file $wrkdir/timing_$tag.rpt
  report_utilization -file $wrkdir/util_$tag.rpt
  report_utilization -hierarchical -hierarchical_depth 4 -file $wrkdir/util_hier_$tag.rpt
  report_timing -setup -max_paths 10 -nworst 1 -input_pins -file $wrkdir/paths_$tag.rpt
  report_clocks -file $wrkdir/clocks_$tag.rpt
  foreach c [get_clocks] {{
    set p [lindex [get_timing_paths -quiet -setup -to $c -max_paths 1] 0]
    if {{$p ne ""}} {{ puts "@@CLK $tag [get_property NAME $c] [get_property PERIOD $c] [get_property SLACK $p]" }}
  }}
  set i 0
  foreach p [get_timing_paths -setup -max_paths 10 -nworst 1] {{
    puts "@@PATH $tag $i [get_property SLACK $p] [get_property LOGIC_LEVELS $p] [get_property DATAPATH_DELAY $p] [get_property GROUP $p] [get_property STARTPOINT_PIN $p] [get_property ENDPOINT_PIN $p]"
    incr i
  }}
}}

set k 0
foreach dir {{{variants}}} {{
  set tag impl$k
  # mọi biến thể đều mở lại post_synth.dcp (impl0 = impl1 thì P&R tất định)
  if {{$k == 0}} {{ close_design }}
  open_checkpoint $wrkdir/post_synth.dcp
  opt_design -directive Explore
  place_design -directive $dir
  phys_opt_design -directive Explore
  power_opt_design
  route_design -directive Explore
  phys_opt_design -directive Explore
  write_checkpoint -force $wrkdir/post_route_$tag.dcp
  puts "@@IMPL $tag $dir"
  minsoc_metrics $tag
  if {{$k == 0 && {1 if write_bit else 0}}} {{
    report_drc -file $wrkdir/drc_$tag.rpt
    if {{[catch {{ write_bitstream -force $wrkdir/$top.bit }} err]}} {{ puts "@@BIT_FAIL $err" }} else {{ puts "@@BIT_OK $wrkdir/$top.bit" }}
  }}
  close_design
  incr k
}}
puts "@@ALL_DONE"
exit 0
"""


UTIL_KEYS = {"lut": r"Slice LUTs\*?", "ff": r"Slice Registers", "bram": r"Block RAM Tile", "dsp": r"DSPs"}


def parse_util(text: str) -> dict:
    out = {}
    for k, lab in UTIL_KEYS.items():
        m = re.search(r"^\|\s*" + lab + r"\s*\|\s*([\d.]+)\s*\|.*?\|\s*([\d.]+)\s*\|\s*$", text, re.M)
        if not m:   # bảng 7-series: | Site Type | Used | Fixed | Prohibited | Available | Util% |
            m = re.search(r"^\|\s*" + lab + r"\s*\|\s*([\d.]+)\s*\|", text, re.M)
        out[k] = float(m.group(1)) if m else None
    return out


def parse_wns_tns(timing_summary: str) -> dict:
    """Bảng 'Design Timing Summary': WNS TNS ... WHS THS ..."""
    m = re.search(r"WNS\(ns\)\s+TNS\(ns\).*?\n\s*-+.*?\n\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(\d+)\s+(\d+)\s+(-?[\d.]+)\s+(-?[\d.]+)",
                  timing_summary, re.S)
    if not m:
        return {"wns": None, "tns": None, "failing": None, "whs": None}
    return {"wns": float(m.group(1)), "tns": float(m.group(2)), "failing": int(m.group(3)),
            "whs": float(m.group(5))}


def parse_markers(log: str) -> dict:
    """Đọc các dòng @@ do make_tcl in ra."""
    r = {"part": None, "board_part": None, "synth_done": False, "all_done": False, "bit": None,
         "bit_fail": None, "impl": {}, "clk": {}, "paths": {}}
    for l in log.splitlines():
        if not l.startswith("@@"):
            continue
        f = l.split(" ", 1)
        key, rest = f[0], (f[1] if len(f) > 1 else "")
        if key == "@@PART":
            r["part"] = rest.strip()
        elif key == "@@BOARD_PART":
            r["board_part"] = rest.strip()
        elif key == "@@SYNTH_DONE":
            r["synth_done"] = True
        elif key == "@@ALL_DONE":
            r["all_done"] = True
        elif key == "@@BIT_OK":
            r["bit"] = rest.strip()
        elif key == "@@BIT_FAIL":
            r["bit_fail"] = rest.strip()[:500]
        elif key == "@@IMPL":
            tag, d = rest.split()
            r["impl"][tag] = d
        elif key == "@@CLK":
            tag, name, period, slack = rest.split()
            r["clk"].setdefault(tag, []).append({"clock": name, "period": float(period), "slack": float(slack)})
        elif key == "@@PATH":
            p = rest.split()
            tag = p[0]
            r["paths"].setdefault(tag, []).append({
                "slack": float(p[2]), "levels": int(p[3]), "delay": float(p[4]), "group": p[5],
                "start": p[6], "end": p[7] if len(p) > 7 else ""})
    return r


def dut_clock(clks: list) -> dict | None:
    """Clock của SoC: clock có chu kỳ nhỏ nhất trong các clock có path (JTAG/UART chậm hơn nhiều)."""
    c = [x for x in clks if x["period"] > 0]
    return min(c, key=lambda x: x["period"]) if c else None


def fmax_mhz(period_ns: float, slack_ns: float) -> float:
    return 1000.0 / (period_ns - slack_ns)
