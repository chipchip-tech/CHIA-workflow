// tb_gls.v - mô phỏng gate-level của bản FPGA đã route (cố định, agent không sửa). baseline.py chạy trên netlist chức
// năng sau route (out/funcsim.v, do power_saif.tcl ghi) bằng xsim với thư viện UNISIM.
// fpga_top trong netlist giữ tham số đã tổng hợp (N_CYCLES = 1048576, EXP_SIG, EN_THR): chạy hết khung đầu rồi kiểm LED,
// tức là MISR của toàn bộ kết quả ALU trong khung phải khớp chữ ký của golden_sig.py.
// Chu kỳ clock đặt bằng macro (xvlog -d TB_PERIOD_PS=...), không bằng -generic_top (xem tb_power.v).
`ifndef TB_PERIOD_PS
`define TB_PERIOD_PS 10000
`endif
`timescale 1ps/1ps
module tb_gls;
    localparam integer PERIOD_PS = `TB_PERIOD_PS;
    localparam integer N_FRAME   = 1048576;
    reg clk = 1'b0, rst_n_btn = 1'b0;
    wire [3:0] led;
    integer k;
    fpga_top dut (.clk(clk), .rst_n_btn(rst_n_btn), .led(led));
    always #(PERIOD_PS / 2) clk = ~clk;
    initial begin
        #(200000 + 10 * PERIOD_PS);          // GSR của glbl giữ 100 ns đầu
        rst_n_btn = 1'b1;
        for (k = 0; k < N_FRAME + 1000 && !led[3]; k = k + 1) @(posedge clk);
        repeat (4) @(posedge clk);
        $display("gls: led=%b sau %0d chu kỳ (cần 1x10: xong khung, PASS, không FAIL)", led, k);
        if (led[3] === 1'b1 && led[2] === 1'b0 && led[1] === 1'b1) $display("RESULT: PASS gls");
        else                                                          $display("RESULT: FAIL gls");
        $finish;
    end
endmodule
