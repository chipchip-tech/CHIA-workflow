// ascon_pkg.svh - tham số, kiểu và hàm dùng chung của ascon_core (tách từ ascon_core.sv bản gộp để testbench
// `include được khi mô phỏng trên netlist; nội dung giữ nguyên).
`ifndef INCL_ASCON_PKG
`define INCL_ASCON_PKG


// Configuration 
// UROL: Number of Ascon-p rounds per clock cycle.
// CCW: Width of the data buses.
localparam logic [3:0] UROL = 1;
localparam unsigned CCW = 32;

localparam logic [3:0] W64 = CCW == 32 ? 4'd2 : 4'd1;   // Number of words in 64 bits
localparam logic [3:0] W128 = CCW == 32 ? 4'd4 : 4'd2;  // Number of words in 128 bits
localparam logic [3:0] W192 = CCW == 32 ? 4'd6 : 4'd3;  // Number of words in 192 bits

// Ascon parameters
localparam unsigned LANES = 5;
localparam unsigned ROUNDS_A = 12;
localparam unsigned ROUNDS_B = 8;

localparam logic [63:0] IV_AEAD = 64'h00001000808c0001;  // Ascon-AEAD128

// Interface data types
typedef enum logic [3:0] {
  D_INVALID = 0,
  D_NONCE   = 1,
  D_AD      = 2,
  D_MSG     = 3,
  D_TAG     = 4
} data_t;

// Generic Functions
// Pad input during ABS_AD, ABS_MSG (encryption):
// in:  [0x00, 0x00, 0x11, 0x22]
// val: [   0,    0,    1,    1]
// =>
// pad: [0x00, 0x01, 0x11, 0x22]
function automatic logic [CCW-1:0] pad;
  input logic [CCW-1:0] in;
  input logic [CCW/8-1:0] val;
  pad[7:0] = val[0] ? in[7:0] : 'd0;
  for (int i = 1; i < CCW / 8; i += 1) begin
    pad[i*8+:8] = val[i] ? in[i*8+:8] : val[i-1] ? 'd1 : 'd0;
  end
endfunction

// Pad input during ABS_MSG (decryption):
// in1:  [0x00, 0x11, 0x22, 0x33]
// in2:  [0x44, 0x55, 0x66, 0x77]
// val:  [   0,    0,    1,    1]
// =>
// pad2: [0x44, 0x54, 0x22, 0x33]
function automatic logic [CCW-1:0] pad2;
  input logic [CCW-1:0] in1;
  input logic [CCW-1:0] in2;
  input logic [CCW/8-1:0] val;
  pad2[7:0] = val[0] ? in1[7:0] : in2[7:0];
  for (int i = 1; i < CCW / 8; i += 1) begin
    pad2[i*8+:8] = val[i] ? in1[i*8+:8] : (val[i-1] ? 'd1 ^ in2[i*8+:8] : in2[i*8+:8]);
  end
endfunction

// Mask output during ABS_MSG:
// in1:  [0x00, 0x11, 0x22, 0x33]
// val:  [   0,    0,    1,    1]
// =>
// mask: [0x00, 0x00, 0x22, 0x33]
function automatic logic [CCW-1:0] mask;
  input logic [CCW-1:0] in1;
  input logic [CCW/8-1:0] val;
  for (int i = 0; i < CCW / 8; i += 1) begin
    mask[i*8+:8] = val[i] ? in1[i*8+:8] : 'd0;
  end
endfunction

`endif  // INCL_ASCON_PKG
