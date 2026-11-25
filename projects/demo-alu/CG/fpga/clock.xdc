## clock.xdc - chu kỳ clock của bước FPGA. baseline.py ghi lại file này theo --fpga-period; agent không sửa được.
create_clock -name sys_clk -period 10.000 [get_ports clk]
