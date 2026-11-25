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
# Ghi hoạt động của mọi net dưới dut; không thấy dut thì ghi từ gốc (strip_path vẫn lấy đúng phần dưới tb_power/dut).
puts $fh "set objs \[get_objects -quiet -r /tb_power/dut/*\]"
puts $fh "if {\[llength \$objs\] == 0} { puts \"SAIF_SCOPE: không thấy /tb_power/dut, scope gốc: \[get_scopes /*\]\"; set objs \[get_objects -quiet -r /*\] }"
puts $fh "puts \"SAIF_OBJECTS: \[llength \$objs\]\""
puts $fh "log_saif \$objs"
puts $fh "run $run_ps ps"
puts $fh "close_saif"
puts $fh "quit"
close $fh

cd xsim_pw
# Chu kỳ truyền bằng macro (-d), không bằng -generic_top: generic khác mặc định làm xsim đổi tên scope gốc thành
# tb_power(PERIOD_PS=...), không còn /tb_power/dut (xem tb_power.v).
exec xvlog -nolog -d TB_PERIOD_PS=$ps -d TB_CYCLES=$cycles ../out/funcsim.v ../tb_power.v $::env(XILINX_VIVADO)/data/verilog/src/glbl.v >& xvlog.log
exec xelab -nolog -debug typical -L unisims_ver -s tbp tb_power glbl >& xelab.log
catch {exec xsim -nolog tbp -tclbatch saif.tcl >& xsim.log}
cd $here
set saif_ok 0
if {[file exists xsim_pw/act.saif]} {
    set fh [open xsim_pw/act.saif]; set saif [read $fh]; close $fh
    set saif_ok [expr {[regexp {\(DURATION\s+([0-9]+)\)} $saif -> dur] && $dur > 0}]
}
if {!$saif_ok} {
    set fh [open xsim_pw/xsim.log]; set log [read $fh]; close $fh
    puts "SAIF_MISSING: xsim không ghi được hoạt động vào xsim_pw/act.saif (thiếu file hoặc DURATION 0). Cuối xsim.log:\n[string range $log end-3000 end]"
    exit 1
}

read_saif -strip_path tb_power/dut xsim_pw/act.saif
set_units -power mW
report_power -hierarchical_depth 3 -file reports/power_saif.rpt
puts "POWER_DONE"
