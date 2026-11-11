# minsoc-fmax pha ASIC bước A: STA không tải dây trên netlist Yosys (chạy bằng `openroad -exit`).
# Biến môi trường: MINSOC_TECH_LEF, MINSOC_SC_LEF, MINSOC_LIB, MINSOC_NETLIST, MINSOC_STUBS, MINSOC_TOP, MINSOC_SDC.
read_lef $::env(MINSOC_TECH_LEF)
read_lef $::env(MINSOC_SC_LEF)
read_liberty $::env(MINSOC_LIB)
read_verilog $::env(MINSOC_NETLIST)
read_verilog $::env(MINSOC_STUBS)
link_design $::env(MINSOC_TOP)
read_sdc $::env(MINSOC_SDC)
puts "MINSOC_RAW_WNS [sta::worst_slack -max]"
# Netlist Yosys chưa chèn buffer (fanout tới 512): sửa fanout/slew trước khi đo, như bước đầu của P&R.
repair_design
puts "MINSOC_BEGIN_SUMMARY"
report_worst_slack -max -digits 3
report_tns -digits 3
puts "MINSOC_CLOCKS [llength [all_clocks]] [get_full_name [all_clocks]]"
puts "MINSOC_END_SUMMARY"
report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 -fields {fanout} -digits 3
