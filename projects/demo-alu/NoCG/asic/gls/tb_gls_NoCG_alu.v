// tb_gls_NoCG_alu.v - mô phỏng gate-level của netlist ASIC (6_final.v): bản sao testbench baseline sim/tb_NoCG_alu.v, bỏ kiểm tra
// hộp trắng (dut.a_q, ... không còn tên trong netlist). Cố định, agent không sửa. Gốc:
//
//
// Plusargs lúc chạy (đều có mặc định):
//   +seed=<n>     hạt giống bộ sinh ngẫu nhiên (xorshift32, giống nhau trên mọi simulator)   mặc định 1
//   +cycles=<n>   số chu kỳ của phần ngẫu nhiên                                              mặc định 20000
//   +en_pct=<n>   % chu kỳ có en=1 trong phần ngẫu nhiên (demo: 30, tức rảnh 70 %)          mặc định 30
//   +vcd=<file>   ghi waveform VCD (Verilator cần build với --trace)                        mặc định không ghi
//
// Kiểm tra:
//   1. Reset: sau reset y=0, zero=0, carry=0, valid=0.
//   2. Directed: mọi op với các giá trị biên (00, 01, 7F, 80, FF), mọi độ dịch 0..7, en liên tiếp.
//   3. Giữ giá trị: khi valid=0, y/zero/carry không đổi.
//      Thêm kiểm tra hộp trắng: khi en=0, thanh ghi vào (dut.a_q, b_q, op_q) không đổi. Lỗi này KHÔNG lộ ra
//      ở cổng ra (tầng ra chỉ chốt một chu kỳ sau khi nạp), nhưng làm ALU tổ hợp chuyển mạch vô ích khi rảnh,
//      tức là mất ý nghĩa của enable.
//   4. Ngẫu nhiên: op/a/b ngẫu nhiên, en theo en_pct.
// Mỗi kết quả (valid=1) so với mô hình tham chiếu trong testbench; số kết quả phải bằng số phép đã gửi.
//
// Dòng cuối luôn có dạng (để script/CHIA đọc):
//   RESULT: PASS|FAIL dut=<tên> seed=<n> cycles=<n> en_pct=<n> sent=<n> checked=<n> errors=<n>
`timescale 1ns/1ps
// Testbench dùng gán blocking trong khối always và reset điều khiển cả đồng bộ lẫn bất đồng bộ: có chủ đích.
/* verilator lint_off BLKSEQ */
/* verilator lint_off SYNCASYNCNET */

module tb_NoCG_alu;
    localparam W = 8;
    localparam QN = 16;               // độ sâu hàng đợi kết quả chờ (pipeline chỉ cần 2)

    reg          clk = 1'b0;
    reg          rst_n = 1'b0;
    reg          en = 1'b0;
    reg  [2:0]   op = 3'd0;
    reg  [W-1:0] a = 0, b = 0;
    wire [W-1:0] y;
    wire         zero, carry, valid;

    NoCG_alu dut (.clk(clk), .rst_n(rst_n), .en(en), .op(op), .a(a), .b(b),
                  .y(y), .zero(zero), .carry(carry), .valid(valid));

    always #5 clk = ~clk;             // 100 MHz danh nghĩa, chỉ để đọc waveform

    // ---------------- Mô hình tham chiếu ----------------
    // Trả về {carry, y}. zero suy ra từ y.
    function [W:0] ref_alu(input [2:0] o, input [W-1:0] x, input [W-1:0] z);
        begin
            case (o)
                3'd0: ref_alu = {1'b0, x} + {1'b0, z};
                3'd1: ref_alu = {1'b0, x} - {1'b0, z};
                3'd2: ref_alu = {1'b0, x & z};
                3'd3: ref_alu = {1'b0, x | z};
                3'd4: ref_alu = {1'b0, x ^ z};
                3'd5: ref_alu = {1'b0, x << z[2:0]};
                3'd6: ref_alu = {1'b0, x >> z[2:0]};
                default: ref_alu = {{W{1'b0}}, ($signed(x) < $signed(z))};
            endcase
        end
    endfunction

    // ---------------- Hàng đợi kết quả mong đợi ----------------
    reg [W:0]  q_exp [0:QN-1];
    reg [2:0]  q_op  [0:QN-1];
    integer    q_head = 0, q_tail = 0;
    integer    sent = 0, checked = 0, errors = 0, cyc = 0;

    // Ghi lại phép được nhận ở mỗi cạnh lên có en=1.
    always @(posedge clk) begin
        if (rst_n && en) begin
            q_exp[q_tail % QN] = ref_alu(op, a, b);
            q_op [q_tail % QN] = op;
            q_tail = q_tail + 1;
            sent   = sent + 1;
        end
    end

    // Kiểm tra ở cạnh xuống (output đã ổn định sau cạnh lên).
    reg [W-1:0] y_prev = 0;
    reg         z_prev = 0, c_prev = 0;
    reg [W:0]   e;
    always @(negedge clk) begin
        if (rst_n) begin
            cyc = cyc + 1;
            if (valid) begin
                if (q_head == q_tail) begin
                    errors = errors + 1;
                    if (errors <= 10) $display("ERROR t=%0t: valid=1 nhưng không có phép nào đang chờ", $time);
                end else begin
                    e = q_exp[q_head % QN];
                    if (y !== e[W-1:0] || carry !== e[W] || zero !== (e[W-1:0] == 0)) begin
                        errors = errors + 1;
                        if (errors <= 10)
                            $display("ERROR t=%0t: op=%0d y=%02h c=%b z=%b, mong đợi y=%02h c=%b z=%b",
                                     $time, q_op[q_head % QN], y, carry, zero,
                                     e[W-1:0], e[W], (e[W-1:0] == 0));
                    end
                    q_head  = q_head + 1;
                    checked = checked + 1;
                end
            end else if (y !== y_prev || zero !== z_prev || carry !== c_prev) begin
                errors = errors + 1;
                if (errors <= 10) $display("ERROR t=%0t: valid=0 nhưng output đổi", $time);
            end
            y_prev = y; z_prev = zero; c_prev = carry;
        end
    end


    // ---------------- Bộ sinh ngẫu nhiên xorshift32 ----------------
    reg [31:0] rng;
    function [31:0] xs32(input [31:0] s);
        reg [31:0] t;
        begin
            t = s ^ (s << 13);
            t = t ^ (t >> 17);
            xs32 = t ^ (t << 5);
        end
    endfunction

    // Đưa một phép vào ở cạnh xuống (cạnh lên kế tiếp DUT nhận).
    task send(input [2:0] o, input [W-1:0] x, input [W-1:0] z);
        begin
            @(negedge clk);
            en = 1'b1; op = o; a = x; b = z;
        end
    endtask

    task idle(input integer n);
        integer k;
        begin
            for (k = 0; k < n; k = k + 1) begin
                @(negedge clk);
                en = 1'b0; op = rng[2:0]; a = rng[15:8]; b = rng[23:16];   // đầu vào đổi khi rảnh
                rng = xs32(rng);
            end
        end
    endtask

    // ---------------- Kịch bản ----------------
    integer seed, cycles, en_pct, i, j, k;
    reg [1023:0] vcd_file;
    reg [W-1:0] edge_v [0:4];

    initial begin
        if (!$value$plusargs("seed=%d", seed))     seed = 1;
        if (!$value$plusargs("cycles=%d", cycles)) cycles = 20000;
        if (!$value$plusargs("en_pct=%d", en_pct)) en_pct = 30;
        if ($value$plusargs("vcd=%s", vcd_file)) begin
            $dumpfile(vcd_file);
            $dumpvars(0, tb_NoCG_alu);
        end
        rng = (seed == 0) ? 32'h1 : seed;
        edge_v[0] = 8'h00; edge_v[1] = 8'h01; edge_v[2] = 8'h7F; edge_v[3] = 8'h80; edge_v[4] = 8'hFF;

        // 1. Reset
        repeat (3) @(negedge clk);
        if (y !== 0 || zero !== 0 || carry !== 0 || valid !== 0) begin
            errors = errors + 1;
            $display("ERROR: giá trị sau reset sai: y=%02h z=%b c=%b v=%b", y, zero, carry, valid);
        end
        rst_n = 1'b1;
        idle(2);

        // 2. Directed: mọi op × giá trị biên, gửi liên tiếp (en=1 nhiều chu kỳ liền)
        for (k = 0; k < 8; k = k + 1)
            for (i = 0; i < 5; i = i + 1)
                for (j = 0; j < 5; j = j + 1)
                    send(k[2:0], edge_v[i], edge_v[j]);
        idle(3);
        // Mọi độ dịch 0..7 cho SHL/SHR, xen kẽ với chu kỳ rảnh
        for (i = 0; i < 8; i = i + 1) begin
            send(3'd5, 8'hA5, i[W-1:0]); idle(1);
            send(3'd6, 8'hA5, i[W-1:0]); idle(2);
        end

        // 3 + 4. Ngẫu nhiên theo en_pct (kiểm tra giữ giá trị chạy suốt ở khối always phía trên)
        for (i = 0; i < cycles; i = i + 1) begin
            @(negedge clk);
            rng = xs32(rng);
            en  = ((rng % 100) < en_pct);
            op  = rng[10:8];
            a   = rng[23:16];
            b   = rng[31:24];
        end
        idle(4);                       // xả pipeline

        if (q_head != q_tail) begin
            errors = errors + 1;
            $display("ERROR: còn %0d phép chưa có kết quả", q_tail - q_head);
        end
        $display("RESULT: %s dut=NoCG_alu seed=%0d cycles=%0d en_pct=%0d sent=%0d checked=%0d errors=%0d",
                 (errors == 0) ? "PASS" : "FAIL", seed, cyc, en_pct, sent, checked, errors);
        $finish;
    end
endmodule
