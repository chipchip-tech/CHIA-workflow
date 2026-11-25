// icg_fpga_ce.v - module icg cho FPGA để Vivado đổi clock gating thành clock enable (biến thể FPGA "ce" của
// baseline.py, kèm `synth_design -gated_clock_conversion auto`). Thay ../rtl/icg_sim.v trong sources.f; rtl/ không đổi.
// Khi tổng hợp, icg là cổng AND: Vivado nhận ra clock gated và chuyển en sang chân CE của các flip-flop, mọi flop chạy
// clock gốc qua một BUFG. Khi mô phỏng (xsim, không có SYNTHESIS) dùng mô hình latch + AND như icg_sim.v, vì AND trần
// trong mô phỏng không trễ sẽ tạo xung clock giả khi en đổi trong pha clk = 1.
/* verilator lint_off DECLFILENAME */
module icg (
    input  wire clk,
    input  wire en,
    input  wire test_en,
    output wire gclk
);
`ifdef SYNTHESIS
    assign gclk = clk & (en | test_en);
`else
    reg en_lat;
    /* verilator lint_off LATCH */
    always @* if (!clk) en_lat = en | test_en;   // latch có chủ đích (như icg_sim.v)
    /* verilator lint_on LATCH */
    assign gclk = clk & en_lat;
`endif
endmodule
