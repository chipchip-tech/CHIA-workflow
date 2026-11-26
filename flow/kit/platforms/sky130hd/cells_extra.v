// cells_extra.v - model mô phỏng của các cell sky130_fd_sc_hd mà Yosys không chuyển được từ .lib (cell ICG dlclkp mô tả
// bằng statetable). Theo .lib: statetable ("CLK GATE","M0") = latch trong suốt khi CLK = 0, GCLK = CLK & M0. Cố định.
/* verilator lint_off DECLFILENAME */
/* verilator lint_off LATCH */
`define DLCLKP(name) \
module name (input CLK, input GATE, output GCLK); \
    reg M0; \
    always @* if (!CLK) M0 = GATE; \
    assign GCLK = CLK & M0; \
endmodule
`DLCLKP(sky130_fd_sc_hd__dlclkp_1)
`DLCLKP(sky130_fd_sc_hd__dlclkp_2)
`DLCLKP(sky130_fd_sc_hd__dlclkp_4)
/* verilator lint_on LATCH */
