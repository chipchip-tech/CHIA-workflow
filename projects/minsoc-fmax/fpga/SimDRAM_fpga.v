// SimDRAM_fpga.v: thay SimDRAM (mô hình DRAM DPI-C, chỉ cho mô phỏng) khi tổng hợp FPGA.
//
// Cùng tên module, tham số và cổng với generators/testchipip/.../SimDRAM.v, nên Arty100THarness và
// ChipTop không đổi. Bên trong: một AXI4 slave đơn giản nối vào BRAM RAM_BYTES byte (suy luận BRAM,
// ghi theo byte). Địa chỉ cao bị bỏ qua (alias): vùng MEM_SIZE = 256 MiB lặp lại trên RAM_BYTES.
// Chỉ dùng cho FPGA (minsoc-fmax, vivado_tcl.py --fpga-mem bram). Không dùng cho sim hay ASIC.
//
// Mỗi lúc phục vụ một burst (ưu tiên ghi), burst INCR, 2 cycle mỗi beat đọc. Hiệu năng không quan
// trọng: mục đích là giữ L2 và đường refill trong netlist để đo timing của ChipTop.
module SimDRAM #(
    parameter ADDR_BITS = 32, DATA_BITS = 64, ID_BITS = 5,
              MEM_SIZE = 1000 * 1000 * 1000,
              LINE_SIZE = 64,
              WORD_SIZE = DATA_BITS/8,
              CLOCK_HZ = 100000,
              STRB_BITS = DATA_BITS/8,
              MEM_BASE = 0,
              RAM_BYTES = 131072)(
  input                  clock,
  input                  reset,
  output                 axi_aw_ready,
  input                  axi_aw_valid,
  input  [ADDR_BITS-1:0] axi_aw_bits_addr,
  input  [7:0]           axi_aw_bits_len,
  input  [2:0]           axi_aw_bits_size,
  input  [1:0]           axi_aw_bits_burst,
  input                  axi_aw_bits_lock,
  input  [3:0]           axi_aw_bits_cache,
  input  [2:0]           axi_aw_bits_prot,
  input  [3:0]           axi_aw_bits_qos,
  input  [ID_BITS-1:0]   axi_aw_bits_id,
  output                 axi_w_ready,
  input                  axi_w_valid,
  input  [DATA_BITS-1:0] axi_w_bits_data,
  input                  axi_w_bits_last,
  input  [STRB_BITS-1:0] axi_w_bits_strb,
  input                  axi_b_ready,
  output                 axi_b_valid,
  output [1:0]           axi_b_bits_resp,
  output [ID_BITS-1:0]   axi_b_bits_id,
  output                 axi_ar_ready,
  input                  axi_ar_valid,
  input  [ADDR_BITS-1:0] axi_ar_bits_addr,
  input  [7:0]           axi_ar_bits_len,
  input  [2:0]           axi_ar_bits_size,
  input  [1:0]           axi_ar_bits_burst,
  input                  axi_ar_bits_lock,
  input  [3:0]           axi_ar_bits_cache,
  input  [2:0]           axi_ar_bits_prot,
  input  [3:0]           axi_ar_bits_qos,
  input  [ID_BITS-1:0]   axi_ar_bits_id,
  input                  axi_r_ready,
  output                 axi_r_valid,
  output [1:0]           axi_r_bits_resp,
  output [DATA_BITS-1:0] axi_r_bits_data,
  output                 axi_r_bits_last,
  output [ID_BITS-1:0]   axi_r_bits_id
);
  localparam DEPTH = RAM_BYTES / STRB_BITS;
  localparam IDX   = $clog2(DEPTH);
  localparam LSB   = $clog2(STRB_BITS);

  localparam S_IDLE = 3'd0, S_WDATA = 3'd1, S_BRESP = 3'd2, S_RADDR = 3'd3, S_RDATA = 3'd4;
  reg [2:0]         state;
  reg [ID_BITS-1:0] id;
  reg [IDX-1:0]     idx;
  reg [7:0]         beats;    // số beat còn lại - 1

  (* ram_style = "block" *) reg [DATA_BITS-1:0] mem [0:DEPTH-1];
  reg [DATA_BITS-1:0] rdata;

  wire take_aw = state == S_IDLE && axi_aw_valid;
  wire take_ar = state == S_IDLE && !axi_aw_valid && axi_ar_valid;
  wire wbeat   = state == S_WDATA && axi_w_valid;
  wire rbeat   = state == S_RDATA && axi_r_ready;

  integer i;
  always @(posedge clock) begin
    if (wbeat)
      for (i = 0; i < STRB_BITS; i = i + 1)
        if (axi_w_bits_strb[i]) mem[idx][i*8 +: 8] <= axi_w_bits_data[i*8 +: 8];
    rdata <= mem[idx];
  end

  always @(posedge clock) begin
    if (reset) begin
      state <= S_IDLE;
    end else begin
      case (state)
        S_IDLE:
          if (take_aw) begin
            state <= S_WDATA; id <= axi_aw_bits_id; idx <= axi_aw_bits_addr[LSB +: IDX];
          end else if (take_ar) begin
            state <= S_RADDR; id <= axi_ar_bits_id; idx <= axi_ar_bits_addr[LSB +: IDX];
            beats <= axi_ar_bits_len;
          end
        S_WDATA:
          if (wbeat) begin
            idx <= idx + 1'b1;
            if (axi_w_bits_last) state <= S_BRESP;
          end
        S_BRESP:
          if (axi_b_ready) state <= S_IDLE;
        S_RADDR:
          state <= S_RDATA;          // rdata = mem[idx] sẵn ở cycle sau
        S_RDATA:
          if (rbeat) begin
            if (beats == 8'd0) state <= S_IDLE;
            else begin beats <= beats - 1'b1; idx <= idx + 1'b1; state <= S_RADDR; end
          end
        default: state <= S_IDLE;
      endcase
    end
  end

  assign axi_aw_ready    = state == S_IDLE;
  assign axi_ar_ready    = state == S_IDLE && !axi_aw_valid;
  assign axi_w_ready     = state == S_WDATA;
  assign axi_b_valid     = state == S_BRESP;
  assign axi_b_bits_resp = 2'b00;
  assign axi_b_bits_id   = id;
  assign axi_r_valid     = state == S_RDATA;
  assign axi_r_bits_resp = 2'b00;
  assign axi_r_bits_data = rdata;
  assign axi_r_bits_last = beats == 8'd0;
  assign axi_r_bits_id   = id;
endmodule
