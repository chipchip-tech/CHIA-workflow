// fcov_NoCG_alu.sv - functional coverage CỐ ĐỊNH của bước Sim (demo NoCG). Agent không được sửa file này, và
// testbench không được tham chiếu tới nó (agent.py kiểm tra).
//
// Gắn vào DUT bằng `bind` (chỉ Verilator; SIM=iverilog bỏ qua file này). Mỗi bin là một tình huống của đặc tả cần
// được thử ít nhất một lần; lấy mẫu ở cạnh lên của clk (đọc giá trị thanh ghi trước cạnh) và ở cạnh xuống của rst_n.
// Cuối mô phỏng in:
//   FCOV: hit=<số bin đã gặp> total=<tổng số bin>
//   FCOV_MISS: <tên các bin chưa gặp, cách nhau bằng dấu phẩy>
// Luật nhận của agent ở bước Sim dựa trên số này (agent.py, judge).
/* verilator lint_off DECLFILENAME */
/* verilator lint_off UNUSEDSIGNAL */
module fcov_alu #(parameter W = 8, parameter CG = 0) (
    input wire         clk,
    input wire         rst_n,
    input wire         en,
    input wire         en_q,
    input wire         valid,
    input wire [2:0]   op_q,
    input wire [W-1:0] a_q,
    input wire [W-1:0] b_q,
    input wire [W-1:0] r,
    input wire         c
);
    localparam N_BASE = 56;
    localparam N = N_BASE + (CG ? 2 : 0);
    reg [N-1:0] hit = {N{1'b0}};            // bin lấy mẫu theo clk
    reg [2:0]   hit_rst = 3'b0;              // bin 52..54, lấy mẫu theo rst_n
    reg [1:0]   hit_cg = 2'b0;               // bin 56, 57 (chỉ CG), lấy mẫu theo en

    // Lịch sử điều khiển, cập nhật ở cạnh lên.
    reg     en_d1 = 1'b0, en_d2 = 1'b0, rst_d = 1'b0;
    integer run_en = 0, run_idle = 0;
    reg [W-1:0] a_prev = 0, b_prev = 0;
    integer n_rst = 0, n_rst_seen = 0;   // số lần rst_n xuống (bin 51 bỏ qua chu kỳ có reset)

    wire sa = a_q[W-1], sb = b_q[W-1];
    wire slt_res = r[0];

    integer k;
    always @(posedge clk) begin
        if (rst_n) begin
            if (en_q) begin
                // A. kết quả 0 / khác 0 cho từng phép (bin 0..15)
                if (r == 0) hit[{3'd0, op_q}] <= 1'b1;
                else        hit[6'd8 + {3'd0, op_q}] <= 1'b1;
                // B. carry của ADD/SUB (16..19)
                if (op_q == 3'd0) hit[c ? 16 : 17] <= 1'b1;
                if (op_q == 3'd1) hit[c ? 18 : 19] <= 1'b1;
                // C. độ dịch 0..7 của SHL (20..27), SHR (28..35); b[7:3] != 0 (36, 37)
                if (op_q == 3'd5) begin hit[6'd20 + {3'd0, b_q[2:0]}] <= 1'b1; if (b_q[W-1:3] != 0) hit[36] <= 1'b1; end
                if (op_q == 3'd6) begin hit[6'd28 + {3'd0, b_q[2:0]}] <= 1'b1; if (b_q[W-1:3] != 0) hit[37] <= 1'b1; end
                // D. SLT có dấu (38..42)
                if (op_q == 3'd7) begin
                    if (sa && !sb)                 hit[38] <= 1'b1;   // âm < dương: 1
                    if (!sa && sb)                 hit[39] <= 1'b1;   // dương, âm: 0
                    if (sa == sb && slt_res)       hit[40] <= 1'b1;   // cùng dấu, nhỏ hơn
                    if (sa == sb && !slt_res && a_q != b_q) hit[41] <= 1'b1;   // cùng dấu, lớn hơn
                    if (a_q == b_q)                hit[42] <= 1'b1;   // bằng nhau
                end
                // E. tràn có dấu (43..46)
                if (op_q == 3'd0 && sa == sb && r[W-1] != sa) hit[sa ? 44 : 43] <= 1'b1;
                if (op_q == 3'd1 && sa != sb && r[W-1] != sa) hit[sa ? 46 : 45] <= 1'b1;
            end
            // F. mẫu en (47..51)
            if (en && en_d1)                  hit[47] <= 1'b1;   // hai phép liền nhau
            if (en && run_en >= 7)            hit[48] <= 1'b1;   // en = 1 ít nhất 8 chu kỳ liền
            if (en && !en_d1 && en_d2)        hit[49] <= 1'b1;   // 1, 0, 1
            if (en && run_idle >= 16)         hit[50] <= 1'b1;   // phép đầu sau ít nhất 16 chu kỳ rảnh
            if (!en_d1 && rst_d && n_rst == n_rst_seen && (a_q != a_prev || b_q != b_prev))
                hit[51] <= 1'b1;   // kiểm tra âm: luôn 0 nếu DUT đúng
            // G4. phép ngay chu kỳ đầu sau khi nhả reset (55)
            if (en && !rst_d)                 hit[55] <= 1'b1;
            run_en   <= en ? run_en + 1 : 0;
            run_idle <= en ? 0 : run_idle + 1;
        end else begin
            run_en <= 0; run_idle <= 0;
        end
        en_d1 <= rst_n & en; en_d2 <= en_d1; rst_d <= rst_n;
        a_prev <= a_q; b_prev <= b_q; n_rst_seen <= n_rst;
    end

    // G. reset bất đồng bộ giữa chừng (52..54)
    always @(negedge rst_n) begin
        if (en_q)  hit_rst[0] <= 1'b1;            // đang có kết quả trong pipeline
        if (valid) hit_rst[1] <= 1'b1;            // ngay lúc output vừa hợp lệ
        if (clk)   hit_rst[2] <= 1'b1;            // giữa hai cạnh (pha clk = 1)
        n_rst <= n_rst + 1;
    end

    // H. chỉ demo CG: en đổi trong pha clk = 1 (latch của ICG phải giữ en, không tạo xung clock giả) (56, 57)
    generate if (CG) begin : g_cg
        always @(en) if (rst_n && clk) hit_cg[en ? 0 : 1] <= 1'b1;   // 0: en lên, 1: en xuống
    end endgenerate

    // Bin 51 là kiểm tra âm (thanh ghi vào đổi khi rảnh): không tính vào hit, chỉ báo lỗi nếu gặp.
    function automatic string bin_name(input integer i);
        string ops[8];
        ops = '{"ADD", "SUB", "AND", "OR", "XOR", "SHL", "SHR", "SLT"};
        if (i < 8)   return $sformatf("%s_zero", ops[i]);
        if (i < 16)  return $sformatf("%s_nonzero", ops[i - 8]);
        case (i)
            16: return "ADD_carry"; 17: return "ADD_nocarry"; 18: return "SUB_borrow"; 19: return "SUB_noborrow";
            36: return "SHL_b_high_bits"; 37: return "SHR_b_high_bits";
            38: return "SLT_neg_pos"; 39: return "SLT_pos_neg"; 40: return "SLT_same_sign_less";
            41: return "SLT_same_sign_greater"; 42: return "SLT_equal";
            43: return "ADD_ovf_pos"; 44: return "ADD_ovf_neg"; 45: return "SUB_ovf_pos"; 46: return "SUB_ovf_neg";
            47: return "en_back_to_back"; 48: return "en_run_8"; 49: return "en_1_0_1"; 50: return "en_after_idle_16";
            51: return "inputs_regs_change_while_idle";
            52: return "reset_while_en_q"; 53: return "reset_while_valid"; 54: return "reset_mid_cycle";
            55: return "en_first_cycle_after_reset";
            56: return "en_rise_clk_high"; 57: return "en_fall_clk_high";
            default: ;
        endcase
        if (i < 28) return $sformatf("SHL_by_%0d", i - 20);
        return $sformatf("SHR_by_%0d", i - 28);
    endfunction

    final begin : report
        integer n_hit;
        string miss;
        reg [N-1:0] all;
        all = hit;
        all[54:52] = hit_rst;
        if (CG) all[N-1:N-2] = hit_cg;
        n_hit = 0; miss = "";
        for (k = 0; k < N; k = k + 1) begin
            if (k == 51) continue;
            if (all[k]) n_hit = n_hit + 1;
            else miss = {miss, (miss == "") ? "" : ",", bin_name(k)};
        end
        $display("FCOV: hit=%0d total=%0d", n_hit, N - 1);
        $display("FCOV_MISS: %s", (miss == "") ? "-" : miss);
        if (hit[51]) $display("FCOV_ERROR: input registers changed while idle");
    end
endmodule

bind NoCG_alu fcov_alu #(.W(8), .CG(0)) u_fcov (
    .clk(clk), .rst_n(rst_n), .en(en), .en_q(en_q), .valid(valid),
    .op_q(op_q), .a_q(a_q), .b_q(b_q), .r(r), .c(c));
