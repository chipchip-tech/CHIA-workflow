// icg_sim.v - mô hình hành vi của cell clock gating (ICG) cho mô phỏng.
// Latch trong suốt khi clk = 0 giữ en ổn định suốt pha clk = 1, nên gclk không có glitch.
// FPGA dùng BUFGCE, ASIC dùng sky130_fd_sc_hd__dlclkp (các bản icg_*.v riêng, viết sau).
/* verilator lint_off DECLFILENAME */
module icg (
    input  wire clk,
    input  wire en,
    input  wire test_en,   // ép mở clock khi test (scan); demo luôn nối 0
    output wire gclk
);
    reg en_lat;
    /* verilator lint_off LATCH */
    always @* if (!clk) en_lat = en | test_en;   // latch có chủ đích
    /* verilator lint_on LATCH */
    assign gclk = clk & en_lat;
endmodule
