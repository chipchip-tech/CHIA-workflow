// fpga_top.v - vỏ FPGA cho NoCG_alu (bước FPGA của demo NoCG, docs/demo-task.md).
//
// ALU có khoảng 30 cổng, nhiều hơn số chân dễ dùng trên board dev, nên vỏ này tự sinh kích thích và tự kiểm tra
// ngay trong FPGA; ra ngoài chỉ cần clk, nút reset và 4 LED. Đổi board chỉ phải sửa file .xdc.
//
// Mỗi khung N_CYCLES chu kỳ:
//   - LFSR Galois 32 bit đổi mỗi chu kỳ; gửi một phép (en=1) khi lfsr[6:0] < EN_THR (mặc định 38/128, khoảng 30 %,
//     rảnh 70 % như tb; 128 là luôn gửi),
//     op = lfsr[10:8], a = lfsr[23:16], b = lfsr[31:24]; 4 chu kỳ cuối khung không gửi để xả pipeline.
//   - Mỗi kết quả (valid=1) dồn vào MISR 32 bit: {carry, zero, y}.
//   - Cuối khung so chữ ký với EXP_SIG (tính bằng golden_sig.py), rồi nạp lại LFSR, MISR và chạy khung mới.
// LED:
//   led[0] nhấp nháy (khoảng 1,5 Hz ở 100 MHz): mạch đang chạy
//   led[1] PASS: đã xong ít nhất một khung và chưa khung nào sai
//   led[2] FAIL: có ít nhất một khung sai (giữ đến khi reset)
//   led[3] đã xong khung đầu tiên

module fpga_top #(
    parameter integer N_CYCLES = 1048576,       // khoảng 10,5 ms mỗi khung ở 100 MHz
    parameter [31:0]  SEED     = 32'hACE12025,
    parameter [31:0]  EXP_SIG  = 32'hEACBE87B,  // python3 golden_sig.py 1048576 ACE12025 38
    parameter [7:0]   EN_THR   = 8'd38          // tỉ lệ gửi phép = EN_THR/128 (đổi thì tính lại EXP_SIG)
) (
    input  wire       clk,          // chu kỳ theo clock.xdc (mặc định 10 ns)
    input  wire       rst_n_btn,    // nút reset, mức thấp
    output wire [3:0] led
);
    localparam [31:0] LFSR_POLY = 32'h00400007;
    localparam [31:0] MISR_POLY = 32'h04C11DB7;

    // Đồng bộ hoá reset: vào bất đồng bộ, nhả đồng bộ.
    reg [1:0] rst_sync;
    always @(posedge clk or negedge rst_n_btn)
        if (!rst_n_btn) rst_sync <= 2'b00;
        else            rst_sync <= {rst_sync[0], 1'b1};
    wire rst_n = rst_sync[1];

    reg  [31:0] cnt, lfsr, sig;
    reg  [6:0]  frames;
    reg         fail, done1;

    wire        issue = (cnt < N_CYCLES - 4);
    wire        en    = issue && ({1'b0, lfsr[6:0]} < EN_THR);
    wire [2:0]  op    = lfsr[10:8];
    wire [7:0]  a     = lfsr[23:16];
    wire [7:0]  b     = lfsr[31:24];

    wire [7:0]  y;
    wire        zero, carry, valid;

    NoCG_alu u_alu (.clk(clk), .rst_n(rst_n), .en(en), .op(op), .a(a), .b(b),
                    .y(y), .zero(zero), .carry(carry), .valid(valid));

    wire [31:0] lfsr_next = {lfsr[30:0], 1'b0} ^ (lfsr[31] ? LFSR_POLY : 32'd0);
    wire [31:0] sig_next  = {sig[30:0], 1'b0} ^ (sig[31] ? MISR_POLY : 32'd0) ^ {22'd0, carry, zero, y};

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            cnt <= 0; lfsr <= SEED; sig <= 0; frames <= 0; fail <= 1'b0; done1 <= 1'b0;
        end else if (cnt == N_CYCLES - 1) begin
            if (sig != EXP_SIG) fail <= 1'b1;
            done1  <= 1'b1;
            frames <= frames + 1'b1;
            cnt    <= 0;
            lfsr   <= SEED;
            sig    <= 0;
        end else begin
            cnt  <= cnt + 1;
            lfsr <= lfsr_next;
            if (valid) sig <= sig_next;
        end

    assign led = {done1, fail, done1 & ~fail, frames[6]};
endmodule
