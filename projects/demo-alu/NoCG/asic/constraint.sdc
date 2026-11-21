# constraint.sdc - clock 10 ns (100 MHz, bằng clock của bước FPGA). I/O chiếm 20 % chu kỳ.
current_design NoCG_alu

set clk_name   core_clock
set clk_period 10.0
set clk_io_pct 0.2

create_clock -name $clk_name -period $clk_period [get_ports clk]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name [all_inputs -no_clocks]
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
