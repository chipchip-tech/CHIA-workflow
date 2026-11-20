// NoCG_alu.v - ALU 8 bit, KHÔNG clock gating (Demo 1, docs/demo-task.md).
//
// Thanh ghi dùng clock enable (if (en) ...): tool tổng hợp thành mux hồi tiếp, mọi flop chạy clk liên tục.
//
// Pipeline 2 tầng:
//   chu kỳ t   : en=1 -> chốt a, b, op vào thanh ghi vào
//   chu kỳ t+1 : en_q=1 -> chốt kết quả ALU tổ hợp vào thanh ghi ra (y, zero, carry), valid=1
// Khi en=0 (en_q=0), thanh ghi tương ứng giữ nguyên giá trị.
//
// Mã op:
//   000 ADD  y = a + b        carry = bit nhớ
//   001 SUB  y = a - b        carry = 1 khi mượn (a < b, không dấu)
//   010 AND  011 OR  100 XOR
//   101 SHL  y = a << b[2:0]
//   110 SHR  y = a >> b[2:0]  (logic)
//   111 SLT  y = (a < b) có dấu ? 1 : 0
// zero = (y == 0). carry = 0 với các op ngoài ADD/SUB.
//
// NoCG_alu.v và CG_alu.v phải cho output giống hệt nhau ở mọi chu kỳ.

module NoCG_alu #(
    parameter W = 8
) (
    input  wire         clk,
    input  wire         rst_n,    // reset bất đồng bộ, mức thấp
    input  wire         en,       // 1: nhận phép tính mới ở chu kỳ này
    input  wire [2:0]   op,
    input  wire [W-1:0] a,
    input  wire [W-1:0] b,
    output reg  [W-1:0] y,
    output reg          zero,
    output reg          carry,
    output reg          valid     // 1: y/zero/carry vừa được cập nhật
);

    localparam OP_ADD = 3'b000, OP_SUB = 3'b001, OP_AND = 3'b010, OP_OR  = 3'b011,
               OP_XOR = 3'b100, OP_SHL = 3'b101, OP_SHR = 3'b110, OP_SLT = 3'b111;

    // en trễ một chu kỳ: điều khiển tầng ra. Luôn chạy clk gốc.
    reg en_q;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) en_q <= 1'b0;
        else        en_q <= en;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) valid <= 1'b0;
        else        valid <= en_q;

    // ---------------- Tầng vào ----------------
    reg [W-1:0] a_q, b_q;
    reg [2:0]   op_q;

    always @(posedge clk or negedge rst_n)
        if (!rst_n)  begin a_q <= 0; b_q <= 0; op_q <= 3'b0; end
        else if (en) begin a_q <= a; b_q <= b; op_q <= op;   end

    // ---------------- ALU tổ hợp ----------------
    reg [W-1:0] r;
    reg         c;
    always @* begin
        r = {W{1'b0}};
        c = 1'b0;
        case (op_q)
            OP_ADD: {c, r} = {1'b0, a_q} + {1'b0, b_q};
            OP_SUB: {c, r} = {1'b0, a_q} - {1'b0, b_q};
            OP_AND: r = a_q & b_q;
            OP_OR : r = a_q | b_q;
            OP_XOR: r = a_q ^ b_q;
            OP_SHL: r = a_q << b_q[2:0];
            OP_SHR: r = a_q >> b_q[2:0];
            OP_SLT: r = {{(W-1){1'b0}}, ($signed(a_q) < $signed(b_q))};
            default: r = {W{1'b0}};
        endcase
    end

    // ---------------- Tầng ra ----------------
    always @(posedge clk or negedge rst_n)
        if (!rst_n)    begin y <= 0; zero <= 1'b0; carry <= 1'b0; end
        else if (en_q) begin y <= r; zero <= (r == 0); carry <= c; end

endmodule
