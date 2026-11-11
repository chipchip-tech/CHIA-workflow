# minsoc-fmax pha ASIC bước A. Chu kỳ lấy từ biến môi trường MINSOC_PERIOD_NS (ns, đơn vị của nangate45).
# Mọi cổng có tên *clock* (trừ jtag) gom thành MỘT clock: bước A chỉ hỏi "logic có chạy được 5 ns không".
set period $::env(MINSOC_PERIOD_NS)
set clk_ports {}
foreach p [get_ports -quiet *clock*] {
  set n [get_full_name $p]
  if {![string match -nocase *jtag* $n] && [get_property $p direction] == "input"} { lappend clk_ports $p }
}
if {[llength $clk_ports] == 0} { error "minsoc: không thấy cổng clock nào khớp *clock*" }
create_clock -name core_clock -period $period $clk_ports
set io [expr {0.2 * $period}]
set clk_names {}
foreach p $clk_ports { lappend clk_names [get_full_name $p] }
set non_clk {}
foreach p [all_inputs] { if {[lsearch -exact $clk_names [get_full_name $p]] < 0} { lappend non_clk $p } }
if {[llength $non_clk]} { set_input_delay $io -clock core_clock $non_clk }
set_output_delay $io -clock core_clock [all_outputs]
