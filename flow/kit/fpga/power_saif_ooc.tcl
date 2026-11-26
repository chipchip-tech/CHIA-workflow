# power_saif.tcl (bản out-of-context của kit) - công suất theo hoạt động thật VÀ mô phỏng gate-level của khối đã route.
# Cố định, agent không sửa. flow (tasks_fpga.fpga_build) gọi sau build.tcl:
#   cd <run_dir>/fpga && vivado -mode batch -nojournal -log out/power.log -source power_saif.tcl -tclargs <chu kỳ ns> <số chu kỳ>
# 1. netlist chức năng sau route (out/funcsim.v);
# 2. xsim chạy CHÍNH testbench của bước Sim (sim_cfg.tcl: tb_top, dut_inst, tb_files, inc_dirs; define GLS) trên netlist
#    đó tới $finish, ghi SAIF của instance DUT (xsim_pw/act.saif) và log (xsim_pw/xsim.log: flow đọc PASS/FAIL làm GLS);
# 3. read_saif vào bản đã route, report_power (mW) -> reports/power_saif.rpt.
# Số chu kỳ không dùng (testbench tự quyết thời lượng); giữ tham số để cùng lời gọi với bản có vỏ board.
source sim_cfg.tcl
set here [pwd]

open_checkpoint out/post_route.dcp
write_verilog -mode funcsim -force out/funcsim.v

file delete -force xsim_pw
file mkdir xsim_pw
set fh [open xsim_pw/saif.tcl w]
puts $fh "open_saif act.saif"
puts $fh "set objs \[get_objects -quiet -r /$tb_top/$dut_inst/*\]"
puts $fh "if {\[llength \$objs\] == 0} { puts \"SAIF_SCOPE: không thấy /$tb_top/$dut_inst\"; set objs \[get_objects -quiet -r /*\] }"
puts $fh "puts \"SAIF_OBJECTS: \[llength \$objs\]\""
puts $fh "log_saif \$objs"
puts $fh "run -all"
puts $fh "close_saif"
puts $fh "quit"
close $fh

set incs {}
foreach d $inc_dirs { lappend incs -i $d }
cd xsim_pw
exec xvlog -nolog -sv -d GLS {*}$incs ../out/funcsim.v {*}$tb_files $::env(XILINX_VIVADO)/data/verilog/src/glbl.v >& xvlog.log
exec xelab -nolog -debug typical --timescale 1ns/1ps -L unisims_ver -s tbp $tb_top glbl >& xelab.log
# -onfinish stop: $finish của testbench dừng mô phỏng nhưng vẫn chạy tiếp close_saif.
catch {exec xsim -nolog -onfinish stop tbp -tclbatch saif.tcl >& xsim.log}
cd $here
set saif_ok 0
if {[file exists xsim_pw/act.saif]} {
    set fh [open xsim_pw/act.saif]; set saif [read $fh]; close $fh
    set saif_ok [expr {[regexp {\(DURATION\s+([0-9]+)\)} $saif -> dur] && $dur > 0}]
}
if {!$saif_ok} {
    set fh [open xsim_pw/xsim.log]; set log [read $fh]; close $fh
    puts "SAIF_MISSING: xsim không ghi được hoạt động vào xsim_pw/act.saif. Cuối xsim.log:\n[string range $log end-3000 end]"
    exit 1
}
read_saif -strip_path $tb_top/$dut_inst xsim_pw/act.saif
set_units -power mW
report_power -hierarchical_depth 3 -file reports/power_saif.rpt
puts "POWER_DONE"
