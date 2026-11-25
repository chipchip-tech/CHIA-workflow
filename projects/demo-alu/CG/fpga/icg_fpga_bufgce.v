// icg_fpga_bufgce.v - module icg cho FPGA bằng BUFGCE (bộ đệm clock toàn cục có chân bật, sẵn trong Artix-7).
// Thay ../rtl/icg_sim.v trong sources.f (biến thể FPGA "bufgce" của baseline.py). Chỉ dùng cho FPGA, rtl/ không đổi.
// BUFGCE lấy mẫu CE đồng bộ với clock nên không tạo xung giả, tương đương latch + AND của ICG; clock gated đi trên
// mạng clock toàn cục (không qua LUT), nên không còn đường găng nửa chu kỳ của bản latch + LUT.
module icg (
    input  wire clk,
    input  wire en,
    input  wire test_en,
    output wire gclk
);
    BUFGCE u_bufgce (.I(clk), .CE(en | test_en), .O(gclk));
endmodule
