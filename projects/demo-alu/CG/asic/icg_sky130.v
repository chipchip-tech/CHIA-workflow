// icg_sky130.v - module icg cho ASIC: cell clock gating có sẵn của sky130hd (latch + AND, không glitch).
// Cùng cổng với rtl/icg_sim.v (bản mô phỏng). ORFS đọc liberty của sky130hd nên Yosys giữ nguyên cell này.
module icg (
    input  wire clk,
    input  wire en,
    input  wire test_en,   // ép mở clock khi test (scan); demo luôn nối 0
    output wire gclk
);
    sky130_fd_sc_hd__dlclkp_1 u_cg (.CLK(clk), .GATE(en | test_en), .GCLK(gclk));
endmodule
