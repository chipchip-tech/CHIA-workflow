# Đề bài và demo: flow FPGA + ASIC với agent trong CHIA

Tài liệu chốt phạm vi trước khi thiết kế demo (20/11/2026). Chưa có gì trong file này được hiện thực hay
kiểm chứng; mọi con số thời gian là ước lượng.

## Phần 1. Đề bài (task)

**Đề bài gốc:** Sử dụng framework CHIA để chạy một flow thiết kế vi mạch số mã nguồn mở cho cả FPGA và ASIC.
Mục tiêu: tận dụng LLM agent trong CHIA để thiết kế và tối ưu từng bước trong quá trình xây dựng SoC, đi từ FPGA
xuống ASIC bằng OpenROAD.

| Điểm | Đã chốt |
|---|---|
| Công cụ | FPGA: **Vivado** được phép. ASIC: **Yosys + OpenROAD (ORFS)**. |
| PDK | **sky130hd**. Có thể đổi sang **nangate45** nếu chạy nhanh hơn rõ rệt. |
| Thứ tự FPGA → ASIC | FPGA là **prototype kiểm chứng chức năng**. RTL đã chạy đúng trên FPGA mới đưa xuống ASIC. Không mang tối ưu của FPGA sang ASIC (xem [workflow-phases.md](workflow-phases.md)). |
| Đối tượng | **Một SoC**: CPU, bộ nhớ, bus, ngoại vi. **Chưa chốt lõi CPU** (Rocket hay lõi nhỏ như PicoRV32 đều được). Tiêu chí: đủ nhỏ để mọi bước chạy nhanh. |
| Agent | **Claude**, chạy trong khối `claude` của cluster. |
| Vai trò agent | Linh hoạt: tạo mới hoặc sửa RTL, testbench, phần mềm, constraint, config, script. |
| Phạm vi agent | Có thể ở **tất cả các bước**. Mỗi bước có agent cần gate (đúng/sai) và thước đo so với baseline. |
| Điểm cuối ASIC | **RTL → GDS**: synth, floorplan, place, CTS, route, kèm báo cáo timing, diện tích, công suất. |
| Điều phối | Mọi bước là node hoặc job của CHIA trên cluster theo khối ([cluster-blocks.md](cluster-blocks.md)). |

```
Spec ─► RTL SoC ─► Sim RTL + phần mềm ─► FPGA (Vivado): bitstream, chạy trên board
                                               │ chức năng đã kiểm chứng
                                               ▼
                        ASIC (Yosys + OpenROAD, sky130hd): synth → floorplan → place → CTS → route → GDS

   Ở mỗi bước: agent Claude tạo hoặc sửa → gate → đo → giữ hoặc loại            (CHIA điều phối)
```

## Phần 2. Demo

**Mục đích:** chứng minh đề bài ở phần 1 chạy được. Demo không phải đề bài.

**Hình thức:** **video** kèm **một slide PPT** báo cáo.

**Yêu cầu:**
1. **SoC chạy real-time:** chạy sim RTL trước, sau đó nạp xuống FPGA chạy thử. Cùng phần mềm. Sự khác biệt giữa
   sim (chậm) và FPGA (thời gian thật) phải nhìn thấy được.
2. **Trực quan** trên cả sim lẫn FPGA.
3. **Show rõ quy trình của framework:** cluster, node và job, agent hành động, gate, vòng phản hồi, chạy song song,
   timeline.
4. **Mọi bước tạo, sửa, kiểm tra đều nhanh** để quay được video.
5. **Nhánh ASIC ra GDS**, có hình layout.

**Slide báo cáo:** flow, các bước có agent và việc agent đã làm, số liệu trước/sau, hình kết quả (sim, board,
layout).

## Phần 3. Hệ quả và ràng buộc

### Board FPGA

Board sẽ mượn, **chưa chắc là Arty A7-100T**. Phần riêng của board phải tách gọn để đổi được: file top FPGA, file
`.xdc` (chân, clock), PLL/clock, part number. SoC, phần mềm và nhánh ASIC không phụ thuộc board.

- Shell Arty của Chipyard (đã ra bitstream, [cluster-blocks.md](cluster-blocks.md) mục 4) chỉ dùng được nếu board
  đúng là Arty A7-100T.
- Board Xilinx khác: Vivado vẫn dùng được, cần board file hoặc part tương ứng.
- Board **không phải Xilinx** (Lattice, Intel): Vivado không dùng được, nhánh FPGA phải đổi công cụ. Kiểm tra ngay
  khi biết board.
- FPGA nhỏ (ví dụ Artix-7 35T) có thể không chứa nổi SoC Rocket.

### Lõi CPU

| | Rocket (Chipyard) | Lõi nhỏ (ví dụ PicoRV32) |
|---|---|---|
| Đã có trên máy | RTL, sim, bitstream Arty, ORFS tới place | Chưa có gì |
| Tốc độ mỗi bước | Chậm: Vivado khoảng 11 phút, ORFS cả SoC hàng giờ, route chưa thử | Nhanh: ước lượng vài phút mỗi bước, chưa đo |
| Phụ thuộc board | Shell Chipyard gắn với Arty | Tự viết top, dễ đổi board |
| Ra GDS trên máy 23 GB RAM | Rủi ro cao | Khả thi |

Lõi nhỏ viết bằng Verilog (như PicoRV32) nằm ngoài Chipyard: không dùng được `ChiselBuildNode`, shell Arty,
`uart_tsi`. Phải tự dựng top SoC, testbench Verilator, top FPGA và config ORFS; các khối cluster (`verilator`,
`vivado`, `asic`, `claude`) vẫn dùng lại được, node CHIA viết riêng. Bộ nhớ trong ASIC cần chọn: RAM bằng flip-flop
(giữ rất nhỏ) hoặc macro/fakeram ([fakeram-sky130.md](fakeram-sky130.md)).

### PDK

Với SoC nhỏ, ước lượng thời gian ORFS của sky130hd và nangate45 không chênh nhiều. sky130hd là PDK thật nên GDS có
giá trị hơn khi trình bày. Máy đã chạy cả hai (`nangate45/gcd` tới `VERIFY_OK`, sky130hd tới place). Chọn bằng cách
đo baseline trên cả hai.

### Agent Claude

Khối `claude` đã lên được trong cluster (`chia-up-blocks.py`), nhưng **chưa có loop nào cho Claude sửa file rồi CHIA
build/kiểm tra** trên Chipyard 1.15.0. Đây là mảnh cần smoke test đầu tiên.

## Còn mở

1. **Board cụ thể** (hãng, part). Có board mới chốt được nhánh FPGA.
2. **Lõi CPU:** chốt sau khi biết board và đo tốc độ.
