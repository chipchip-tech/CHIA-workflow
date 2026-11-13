# minsoc-fmax pha ASIC bước A: STA không tải dây trên netlist Yosys (chạy bằng `openroad -exit`).
# Biến môi trường: MINSOC_TECH_LEF, MINSOC_SC_LEF, MINSOC_LIB, MINSOC_NETLIST, MINSOC_STUBS, MINSOC_TOP, MINSOC_SDC.
read_lef $::env(MINSOC_TECH_LEF)
read_lef $::env(MINSOC_SC_LEF)
read_liberty $::env(MINSOC_LIB)
read_verilog $::env(MINSOC_NETLIST)
read_verilog $::env(MINSOC_STUBS)
link_design $::env(MINSOC_TOP)
read_sdc $::env(MINSOC_SDC)
# MINSOC_EXCLUDE: mẫu tên cell, cách nhau dấu phẩy (vd *fpuOpt*). Path bắt đầu/kết thúc ở đó bị bỏ khỏi báo cáo.
if {[info exists ::env(MINSOC_EXCLUDE)] && $::env(MINSOC_EXCLUDE) ne ""} {
  foreach pat [split $::env(MINSOC_EXCLUDE) ","] {
    set cs [get_cells -hierarchical -quiet $pat]
    puts "MINSOC_EXCLUDE $pat: [llength $cs] cell"
    if {[llength $cs]} { set_false_path -from $cs; set_false_path -to $cs }
  }
}
puts "MINSOC_RAW_WNS [sta::worst_slack -max]"
# Netlist Yosys chưa chèn buffer (fanout tới 512): sửa fanout/slew trước khi đo, như bước đầu của P&R.
repair_design
puts "MINSOC_BEGIN_SUMMARY"
report_worst_slack -max -digits 3
report_tns -digits 3
puts "MINSOC_CLOCKS [llength [all_clocks]] [get_full_name [all_clocks]]"
puts "MINSOC_END_SUMMARY"
puts "MINSOC_BEGIN_ENDPOINTS"
report_checks -path_delay max -group_path_count 3000 -endpoint_path_count 1 -format end -digits 3
puts "MINSOC_END_ENDPOINTS"
# Nhiều path (1 path/endpoint) để Python chọn path xấu nhất của từng họ register.
report_checks -path_delay max -group_path_count 400 -endpoint_path_count 1 -fields {fanout} -digits 3
