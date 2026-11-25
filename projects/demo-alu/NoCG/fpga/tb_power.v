// tb_power.v - kích thích cho đo công suất theo hoạt động thật (power_saif.tcl, cố định, agent không sửa).
// Mô phỏng netlist sau route (write_verilog -mode funcsim) của fpga_top: clock đúng chu kỳ ràng buộc, nhả reset rồi
// chạy CYCLES chu kỳ; LFSR trong fpga_top sinh phép với tỉ lệ EN_THR/128 như trên board. xsim ghi SAIF cho dut.
`timescale 1ps/1ps
module tb_power #(
    parameter integer PERIOD_PS = 10000,
    parameter integer CYCLES    = 20000
);
    reg clk = 1'b0, rst_n_btn = 1'b0;
    wire [3:0] led;
    fpga_top dut (.clk(clk), .rst_n_btn(rst_n_btn), .led(led));
    always #(PERIOD_PS / 2) clk = ~clk;
    initial begin
        #(200000 + 10 * PERIOD_PS);          // GSR của glbl giữ 100 ns đầu
        rst_n_btn = 1'b1;
        #(CYCLES * PERIOD_PS);
        $finish;
    end
endmodule
