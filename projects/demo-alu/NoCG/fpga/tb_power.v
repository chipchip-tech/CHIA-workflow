// tb_power.v - kích thích cho đo công suất theo hoạt động thật (power_saif.tcl, cố định, agent không sửa).
// Mô phỏng netlist sau route (write_verilog -mode funcsim) của fpga_top: clock đúng chu kỳ ràng buộc, nhả reset rồi
// chạy CYCLES chu kỳ; LFSR trong fpga_top sinh phép với tỉ lệ EN_THR/128 như trên board. xsim ghi SAIF cho dut.
// Không gọi $finish: power_saif.tcl chạy đúng một khoảng thời gian rồi close_saif (xsim thoát ở $finish trước khi
// kịp ghi SAIF).
// Chu kỳ và số chu kỳ đặt bằng macro lúc biên dịch (xvlog -d TB_PERIOD_PS=... -d TB_CYCLES=...), KHÔNG bằng
// xelab -generic_top: đặt generic khác mặc định thì xsim đổi tên scope thành `tb_power(PERIOD_PS=5300)`, đường dẫn
// /tb_power/dut không còn và SAIF ghi rỗng (lỗi gặp khi chạy thật 25/11/2026 ở 5,3 ns).
`ifndef TB_PERIOD_PS
`define TB_PERIOD_PS 10000
`endif
`ifndef TB_CYCLES
`define TB_CYCLES 20000
`endif
`timescale 1ps/1ps
module tb_power;
    localparam integer PERIOD_PS = `TB_PERIOD_PS;
    localparam integer CYCLES    = `TB_CYCLES;
    reg clk = 1'b0, rst_n_btn = 1'b0;
    wire [3:0] led;
    fpga_top dut (.clk(clk), .rst_n_btn(rst_n_btn), .led(led));
    always #(PERIOD_PS / 2) clk = ~clk;
    initial begin
        #(200000 + 10 * PERIOD_PS);          // GSR của glbl giữ 100 ns đầu
        rst_n_btn = 1'b1;
    end
endmodule
