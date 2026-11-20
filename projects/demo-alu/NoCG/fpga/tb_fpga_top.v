// tb_fpga_top.v - kiểm tra vỏ fpga_top.v trong mô phỏng trước khi đưa lên Vivado (khung ngắn cho nhanh).
//   iverilog -g2005 -o /tmp/t tb_fpga_top.v fpga_top.v ../rtl/*.v && vvp -n /tmp/t
// +bad: dùng EXP_SIG sai để kiểm tra đèn FAIL.
`timescale 1ns/1ps
module tb_fpga_top;
    localparam integer N = 4096;
    localparam [31:0]  GOOD = 32'hAC066D58;   // python3 golden_sig.py 4096 ACE12025
    reg clk = 1'b0, rst_n_btn = 1'b0;
    wire [3:0] led_good, led_bad;
    fpga_top #(.N_CYCLES(N), .EXP_SIG(GOOD))         u_good (.clk(clk), .rst_n_btn(rst_n_btn), .led(led_good));
    fpga_top #(.N_CYCLES(N), .EXP_SIG(GOOD ^ 32'h1)) u_bad  (.clk(clk), .rst_n_btn(rst_n_btn), .led(led_bad));
    always #5 clk = ~clk;
    initial begin
        #23 rst_n_btn = 1'b1;
        repeat (3 * N + 10) @(posedge clk);
        $display("good: led=%b (cần 1x10: done, không FAIL, PASS)   bad: led=%b (cần 11x0: done, FAIL, không PASS)",
                 led_good, led_bad);
        if (led_good[3:1] == 3'b101 && led_bad[3:1] == 3'b110) $display("RESULT: PASS fpga_top");
        else                                                   $display("RESULT: FAIL fpga_top");
        $finish;
    end
endmodule
