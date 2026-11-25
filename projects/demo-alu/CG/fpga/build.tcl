# build.tcl - flow Vivado không dùng project cho fpga_top (bước FPGA, baseline: thiết lập mặc định).
#   cd <run_dir>/fpga && vivado -mode batch -nojournal -log out/vivado.log -source build.tcl
# Kết quả: out/fpga_top.bit, out/post_route.dcp, reports/*.rpt
set part  xc7a100tcsg324-1
set top   fpga_top
file mkdir out reports

# Danh sách file Verilog ở sources.f (bỏ dòng trống và dòng #), dùng chung với mô phỏng xsim của agent.py.
set fh [open sources.f]
set srcs {}
foreach ln [split [read $fh] "\n"] { set ln [string trim $ln]; if {$ln ne "" && [string index $ln 0] ne "#"} { lappend srcs $ln } }
close $fh
read_verilog $srcs
read_xdc arty_a7_100t.xdc
read_xdc clock.xdc

# synth_opts.tcl (baseline.py ghi, cố định trong lần chạy): biến thể FPGA (ví dụ -gated_clock_conversion) và generic của
# fpga_top (EN_THR, EXP_SIG). Không có file thì không thêm gì.
set synth_args {}
if {[file exists synth_opts.tcl]} { source synth_opts.tcl }
synth_design -top $top -part $part {*}$synth_args
report_utilization -file reports/post_synth_util.rpt

opt_design
place_design
route_design

report_utilization     -file reports/util.rpt
report_utilization     -hierarchical -file reports/util_hier.rpt
report_timing_summary  -max_paths 10 -file reports/timing.rpt
report_clock_utilization -file reports/clock_util.rpt
report_power           -file reports/power.rpt   ;# ước lượng vector-less (công suất theo SAIF: power_saif.tcl)
report_methodology     -file reports/methodology.rpt
report_drc             -file reports/drc.rpt

write_checkpoint -force out/post_route.dcp
write_bitstream  -force out/$top.bit
puts "BUILD_DONE"
