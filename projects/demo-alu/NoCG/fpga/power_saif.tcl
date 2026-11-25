# power_saif.tcl - công suất theo hoạt động thật của bản đã route (cố định, agent không sửa). baseline.py gọi sau build.tcl:
#   cd <run_dir>/fpga && vivado -mode batch -nojournal -log out/power.log -source power_saif.tcl -tclargs <chu kỳ ns> <số chu kỳ>
# 1. netlist chức năng sau route (out/funcsim.v); 2. xsim chạy tb_power.v trên netlist đó, ghi SAIF (xsim_pw/act.saif;
#    xelab cần -debug typical, thiếu thì open_saif báo "compiled without trace information");
# 3. read_saif vào bản đã route, report_power (đơn vị mW) -> reports/power_saif.rpt.
set period_ns [lindex $argv 0]
set cycles    [lindex $argv 1]
set ps [expr {int(round($period_ns * 1000.0 / 2.0)) * 2}]
# Thời gian chạy (ps): 200 ns GSR + 10 chu kỳ reset (như tb_power.v) + số chu kỳ cần đo.
set run_ps [expr {200000 + (10 + $cycles) * $ps}]
set here [pwd]

open_checkpoint out/post_route.dcp
write_verilog -mode funcsim -force out/funcsim.v

file delete -force xsim_pw
file mkdir xsim_pw
set fh [open xsim_pw/saif.tcl w]
puts $fh "open_saif act.saif"
puts $fh "log_saif \[get_objects -r /tb_power/dut/*\]"
puts $fh "run $run_ps ps"
puts $fh "close_saif"
puts $fh "quit"
close $fh

cd xsim_pw
exec xvlog -nolog ../out/funcsim.v ../tb_power.v $::env(XILINX_VIVADO)/data/verilog/src/glbl.v >& xvlog.log
exec xelab -nolog -debug typical -L unisims_ver -generic_top "PERIOD_PS=$ps" -generic_top "CYCLES=$cycles" -s tbp tb_power glbl >& xelab.log
catch {exec xsim -nolog tbp -tclbatch saif.tcl >& xsim.log}
cd $here
if {![file exists xsim_pw/act.saif] || [file size xsim_pw/act.saif] == 0} {
    set fh [open xsim_pw/xsim.log]; set log [read $fh]; close $fh
    puts "SAIF_MISSING: xsim không ghi xsim_pw/act.saif. Cuối xsim.log:\n[string range $log end-3000 end]"
    exit 1
}

read_saif -strip_path tb_power/dut xsim_pw/act.saif
set_units -power mW
report_power -hierarchical_depth 3 -file reports/power_saif.rpt
puts "POWER_DONE"
