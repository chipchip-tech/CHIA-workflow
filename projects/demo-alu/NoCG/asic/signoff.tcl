# signoff.tcl - STA một corner trên thiết kế đã route (cố định, agent không sửa). baseline.py (bước asic_sta) gọi qua ORFS:
#   make -C <ORFS>/flow DESIGN_CONFIG=... WORK_HOME=... run RUN_SCRIPT=<file này> RUN_LOG_NAME_STEM=sta_<corner> [LIB_FILES=<lib>]
# Biến môi trường: SIGNOFF_CORNER (tên), SIGNOFF_DERATE (ví dụ 0.10: delay +10 % khi kiểm setup, -10 % khi kiểm hold).
# LIB_FILES đổi thư viện cell (corner ss/ff nếu có .lib); parasitic đọc từ 6_final.spef (trích RC sau route).
source $::env(SCRIPTS_DIR)/load.tcl
# load_design đổi chữ ký giữa các bản ORFS: bản cũ (odb, sdc), bản mới chỉ (odb) và tự đọc SDC lưu trong ODB.
if {[llength [info args load_design]] >= 2} {
    load_design 6_final.odb 6_final.sdc
} else {
    load_design 6_final.odb
    if {[llength [all_clocks]] == 0} { read_sdc $::env(RESULTS_DIR)/6_final.sdc }
}
set_propagated_clock [all_clocks]
set spef $::env(RESULTS_DIR)/6_final.spef
set par none
if {[file exists $spef]} { read_spef $spef; set par spef }
set derate 0
if {[info exists ::env(SIGNOFF_DERATE)] && $::env(SIGNOFF_DERATE) ne ""} { set derate $::env(SIGNOFF_DERATE) }
if {$derate > 0} {
    set_timing_derate -late  [expr {1.0 + $derate}]
    set_timing_derate -early [expr {1.0 - $derate}]
}
proc val {cmd} { if {[catch {set v [uplevel 1 $cmd]}]} { return NA }; return $v }
puts "SIGNOFF corner=$::env(SIGNOFF_CORNER) parasitics=$par derate=$derate libs=[llength $::env(LIB_FILES)]\
 clocks=[llength [all_clocks]] setup_ws=[val {sta::worst_slack -max}] setup_tns=[val {sta::total_negative_slack -max}]\
 hold_ws=[val {sta::worst_slack -min}] hold_tns=[val {sta::total_negative_slack -min}]"
report_worst_slack -max -digits 4
report_worst_slack -min -digits 4
report_tns -digits 4
