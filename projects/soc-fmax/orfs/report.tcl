# soc-fmax: báo cáo timing/diện tích trên kết quả một bước ORFS. Chạy qua `make ... run RUN_SCRIPT=report.tcl`
# (env của ORFS có sẵn) với SOCFMAX_DB (odb hoặc netlist .v), SOCFMAX_SDC, SOCFMAX_STAGE. Các mục in ra mở đầu
# bằng @@TÊN để orfs_nodes.analyze_report đọc.

set db $::env(SOCFMAX_DB)
set sdc $::env(SOCFMAX_SDC)
set stage $::env(SOCFMAX_STAGE)

set libs {}
foreach v {LIB_FILES ADDITIONAL_LIBS} {
  if {[info exists ::env($v)]} { foreach l $::env($v) { if {[lsearch -exact $libs $l] < 0} { lappend libs $l } } }
}
foreach l $libs { read_liberty $l }

if {[string match *.odb $db]} {
  read_db $db
} else {
  read_lef $::env(TECH_LEF)
  foreach l $::env(SC_LEF) { read_lef $l }
  if {[info exists ::env(ADDITIONAL_LEFS)]} { foreach l $::env(ADDITIONAL_LEFS) { read_lef $l } }
  read_verilog $db
  link_design $::env(DESIGN_NAME)
}
if {$sdc ne ""} { read_sdc $sdc } else { read_sdc $::env(SDC_FILE) }

if {[lsearch -exact {place cts} $stage] >= 0} {
  if {[file exists $::env(PLATFORM_DIR)/setRC.tcl]} { source $::env(PLATFORM_DIR)/setRC.tcl }
  estimate_parasitics -placement
}

puts "@@COUNTS"
if {[catch {puts "async_pins [llength [all_registers -async_pins]]"} e]} { puts "ERROR async_pins: $e" }
if {[catch {puts "sync0_cells [llength [get_cells -hierarchical -quiet *sync_0*]]"} e]} { puts "ERROR sync0: $e" }
if {[catch {
  set all [llength [all_registers -edge_triggered]]
  set clk [llength [all_registers -edge_triggered -clock clk]]
  puts "registers $all"
  puts "registers_on_clk $clk"
  puts "registers_unclocked [expr {$all - $clk}]"
} e]} { puts "ERROR registers: $e" }

puts "@@WORST"
report_worst_slack -max -digits 4
puts "@@TNS"
report_tns -digits 4

# Đường xấu nhất của từng endpoint (tối đa 5000): bắt đầu, kết thúc, slack. Python chia vùng và tính slack
# không kể I/O (Fmax).
puts "@@PATHS_LIST"
proc worst_paths {n} {
  if {[catch {find_timing_paths -path_delay max -group_path_count $n -endpoint_path_count 1 -sort_by_slack} ps]} {
    set ps [find_timing_paths -path_delay max -group_count $n -endpoint_count 1 -sort_by_slack]
  }
  return $ps
}
if {[catch {
  foreach pe [worst_paths 5000] {
    puts "[get_full_name [get_property $pe startpoint]] [get_full_name [get_property $pe endpoint]] [get_property $pe slack]"
  }
} err]} { puts "ERROR paths: $err" }

puts "@@ENDPOINTS"
if {[catch {report_checks -path_delay max -group_path_count 3000 -format end -digits 3} e]} {
  report_checks -path_delay max -group_count 3000 -format end -digits 3
}
puts "@@PATHS"
if {[catch {report_checks -path_delay max -group_path_count 5 -fields {slew cap input_pins fanout} -digits 3} e]} {
  report_checks -path_delay max -group_count 5 -fields {slew cap input_pins fanout} -digits 3
}

puts "@@AREA"
if {[catch {
  set block [ord::get_db_block]
  set dbu [[ord::get_db_tech] getDbUnitsPerMicron]
  set std 0.0; set mac 0.0; set nstd 0; set nmac 0
  foreach inst [$block getInsts] {
    set m [$inst getMaster]
    set a [expr {double([$m getWidth]) * [$m getHeight] / ($dbu * $dbu)}]
    if {[$m isBlock]} {
      set mac [expr {$mac + $a}]; incr nmac
    } elseif {![regexp {WELLTAP|ENDCAP|SPACER|ANTENNA} [$m getType]] && ![string match *tapvpwrvgnd* [$m getName]] && ![string match *fill* [$m getName]] && ![string match *decap* [$m getName]]} {
      set std [expr {$std + $a}]; incr nstd
    }
  }
  puts "std $std"
  puts "macro $mac"
  puts "nstd $nstd"
  puts "nmacro $nmac"
  set core [$block getCoreArea]
  puts "core [expr {double([$core dx]) * [$core dy] / ($dbu * $dbu)}]"
} e]} { puts "ERROR area: $e" }

puts "@@NOCLOCK"
if {[catch {check_setup -no_clock} e]} { puts "ERROR check_setup: $e" }

# Thanh ghi không nhận clock `clk`, gộp theo 3 cấp instance đầu; kèm net ở chân clock của một thanh ghi mẫu
# để biết nguồn clock (jtag TCK, clock chia, clock qua cổng gate...). Dòng: <nhóm> <số thanh ghi> <net mẫu>
puts "@@NOCLOCK_GROUPS"
if {[catch {
  array set onclk {}
  foreach c [all_registers -edge_triggered -clock clk] { set onclk([get_full_name $c]) 1 }
  array set grp {}
  array set smp {}
  foreach c [all_registers -edge_triggered] {
    set n [get_full_name $c]
    if {[info exists onclk($n)]} { continue }
    set parts [split $n /]
    set last [expr {[llength $parts] - 2}]
    if {$last > 2} { set last 2 }
    set k [expr {$last < 0 ? "<top>" : [join [lrange $parts 0 $last] /]}]
    if {[info exists grp($k)]} { incr grp($k) } else {
      set grp($k) 1
      set net "?"
      catch {
        foreach p [get_pins -of_objects $c] {
          if {[get_property $p direction] eq "input" && [get_property $p lib_pin_name] in {CLK CLK_N GATE GATE_N}} {
            set net [get_full_name [get_nets -of_objects $p]]
            break
          }
        }
      }
      set smp($k) $net
    }
  }
  foreach k [lsort [array names grp]] { puts "$k $grp($k) $smp($k)" }
} e]} { puts "ERROR noclock_groups: $e" }
puts "@@END"
exit
