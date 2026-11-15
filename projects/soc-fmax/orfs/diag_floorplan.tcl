# soc-fmax: chẩn đoán floorplan/PDN. Chạy qua `make ... run RUN_SCRIPT=<file này>` với SOCFMAX_DB=<odb>,
# tuỳ chọn SOCFMAX_BOX="x0 y0 x1 y1" (um): in mọi thứ chạm vào hộp đó (ví dụ khe PDN-0178).
read_db $::env(SOCFMAX_DB)
set block [ord::get_db_block]
set dbu [$block getDefUnits]
proc um {v} { global dbu; return [format %.2f [expr {double($v) / $dbu}]] }
proc box {b} { return "([um [$b xMin]] [um [$b yMin]]) - ([um [$b xMax]] [um [$b yMax]])" }
foreach v {MACRO_PLACE_HALO MACRO_PLACE_CHANNEL CORE_UTILIZATION CORE_MARGIN PDN_TCL} {
  puts "env $v = [expr {[info exists ::env($v)] ? $::env($v) : "<không đặt>"}]"
}
puts "die  [box [$block getDieArea]]"
puts "core [box [$block getCoreArea]]"
puts "@@MACROS"
foreach i [$block getInsts] {
  if {[[$i getMaster] isBlock]} {
    puts "[$i getName] [[$i getMaster] getName] [$i getOrient] [$i getPlacementStatus] [box [$i getBBox]]"
    set h [$i getHalo]
    if {$h ne "NULL"} { puts "    halo [box $h]" }
  }
}
puts "@@BLOCKAGES"
foreach b [$block getBlockages] { puts "blockage [box [$b getBBox]]" }
foreach o [$block getObstructions] { puts "obstruction [[[$o getBBox] getTechLayer] getName] [box [$o getBBox]]" }
if {[info exists ::env(SOCFMAX_BOX)]} {
  lassign $::env(SOCFMAX_BOX) x0 y0 x1 y1
  foreach v {x0 y0 x1 y1} { set $v [expr {round([set $v] * $dbu)}] }
  puts "@@IN_BOX"
  set n 0
  foreach i [$block getInsts] {
    set b [$i getBBox]
    if {[$b xMax] > $x0 && [$b xMin] < $x1 && [$b yMax] > $y0 && [$b yMin] < $y1} {
      incr n
      if {$n <= 40} { puts "inst [$i getName] [[$i getMaster] getName] [box $b]" }
    }
  }
  puts "inst_count $n"
  foreach t [$block getBTerms] {
    foreach p [$t getBPins] {
      foreach b [$p getBoxes] {
        if {[$b xMax] > $x0 && [$b xMin] < $x1 && [$b yMax] > $y0 && [$b yMin] < $y1} {
          puts "pin [$t getName] [[$b getTechLayer] getName] [box $b]"
        }
      }
    }
  }
  foreach r [$block getRows] {
    set b [$r getBBox]
    if {[$b xMax] > $x0 && [$b xMin] < $x1 && [$b yMax] > $y0 && [$b yMin] < $y1} { puts "row [$r getName] [box $b]" }
  }
}
puts "@@END"
exit
