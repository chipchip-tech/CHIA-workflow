# Cổng reset của Arty100THarness: shell.xdc chỉ đặt BOARD_PIN {reset}, cần board_part mới ra chân.
# Project Vivado không đặt board part nên gán trực tiếp: nút RESET đỏ (ck_rst) = C2, LVCMOS33.
set_property -dict { PACKAGE_PIN C2 IOSTANDARD LVCMOS33 } [get_ports { reset }]
