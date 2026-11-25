## arty_a7_100t.xdc - chân của fpga_top trên Digilent Arty A7-100T (xc7a100tcsg324-1).
## Đổi board: sửa PACKAGE_PIN (và chu kỳ clock nếu board không phải 100 MHz), giữ nguyên tên cổng.

## Clock (chân E3, dao động 100 MHz trên board). Chu kỳ ràng buộc nằm ở clock.xdc (baseline.py ghi theo --fpga-period).
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports clk]

## Nút RESET (ck_rst, mức thấp)
set_property -dict {PACKAGE_PIN C2 IOSTANDARD LVCMOS33} [get_ports rst_n_btn]

## LED xanh LD4..LD7
set_property -dict {PACKAGE_PIN H5  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN J5  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T9  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## Nút bấm và LED là tín hiệu chậm, không cần ràng buộc timing I/O.
set_false_path -from [get_ports rst_n_btn]
set_false_path -to   [get_ports {led[*]}]

## Cấu hình bank (tránh cảnh báo DRC CFGBVS khi ghi bitstream)
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
