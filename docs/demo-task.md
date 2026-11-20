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

**Hình thức:** **video** kèm slide PPT báo cáo.

**Yêu cầu chung:**
1. **Show rõ quy trình của framework:** cluster, node và job, agent hành động, gate, vòng phản hồi, timeline.
2. **Mọi bước tạo, sửa, kiểm tra đều nhanh** để quay được video. Bước nào cũng không cần phức tạp, chỉ cần chứng
   minh flow **đúng và đủ**.
3. **Nhánh ASIC ra GDS**, có hình layout.

Làm theo thứ tự: **Demo 1 (ALU) trước**. Demo 2 (SoC) chỉ làm nếu còn thời gian, dùng lại loop, node và khối của
Demo 1.

### Demo 1: mạch ALU, có và không có clock gating (làm trước)

**Đầu vào:** file Verilog `alu.v` **đưa sẵn** (không do agent viết), để baseline có một đầu vào cố định.

- ALU 8 bit: thanh ghi vào → ALU tổ hợp (cộng, trừ, AND, OR, XOR) → thanh ghi ra, có tín hiệu `en`.
- Hai biến thể chọn bằng `define`:
  - **NoCG**: thanh ghi dùng clock enable thông thường (mux hồi tiếp).
  - **CG**: clock của thanh ghi đi qua một module ICG.
- Lõi ALU dùng chung. Riêng module ICG là wrapper nhỏ `icg.v`, mỗi nơi một bản:

  | Nơi chạy | Hiện thực ICG |
  |---|---|
  | Sim | Mô hình hành vi (latch + AND) |
  | FPGA | `BUFGCE` (trên FPGA không gate clock bằng logic: tạo skew, timing sai) |
  | ASIC | Cell `sky130_fd_sc_hd__dlclkp` của sky130hd |

**Flow:** CHIA chạy **nối tiếp** Sim → FPGA → ASIC. Bước sau chỉ chạy khi bước trước đạt gate.

```
alu.v (+ icg.v) ─► 1. Sim (verilator) ── PASS ──► 2. FPGA (vivado) ── OK ──► 3. ASIC (ORFS sky130hd)
                      testbench, chạy test          synth, P&R, .bit          synth → floorplan → place
                                                                              → CTS → route → .gds
```

**Ba pha:**

```
Pha 1: BASELINE (không có agent)
  alu.v ─► Sim ─► FPGA ─► ASIC        flow cơ bản, thiết lập mặc định; lưu mọi số liệu và báo cáo
                 │
                 ▼
Pha 2: AGENT (Claude trong CHIA)
  đọc báo cáo baseline từng bước ─► đánh giá ─► đề xuất thay đổi (RTL, constraint, config)
  ─► CHIA chạy lại ─► qua gate thì giữ, không thì loại
                 │
                 ▼
Pha 3: SO SÁNH
  bảng baseline / agent theo từng bước (Sim, FPGA, ASIC), cho cả NoCG và CG
```

**Từng bước:**

| | Sim | FPGA | ASIC |
|---|---|---|---|
| Các bước | Verilator build → chạy test | Vivado synth → P&R → bitstream | Yosys → floorplan → place → CTS → route → GDS |
| Gate (đúng) | Output khớp kết quả mong đợi, cả 2 biến thể | Có `.bit`, WNS ≥ 0 | Có `.gds`, timing đạt, không lỗi |
| Sản phẩm (đủ) | Log `PASS` + waveform | `.bit` + báo cáo tài nguyên | `.gds` + ảnh layout + báo cáo diện tích |
| Thước đo | Đúng/sai; số cycle hoặc độ phủ test nếu có | LUT, FF, WNS/Fmax, công suất | Diện tích, số cell, WNS/Fmax, công suất |
| Khối cluster | `verilator` | `vivado` | `asic` |

Agent Claude chạy ở khối `claude`. Ngoài đánh giá và tối ưu, agent có thể viết những thứ phụ của từng bước
(testbench, `.xdc` và top cho board, `config.mk` cho ORFS) và sửa khi một bước fail.

**Quy tắc so sánh công bằng:**
- Cùng `alu.v` gốc, cùng phiên bản công cụ, cùng board/part và PDK, cùng chu kỳ clock mục tiêu (hoặc ghi rõ nếu
  agent được phép đổi).
- Mọi thay đổi của agent phải qua lại gate Sim. Sai chức năng thì loại.
- Chạy baseline **hai lần** để đo nhiễu (P&R thường dao động khoảng 1–2 %). Chỉ tính là cải thiện khi vượt mức nhiễu.
- Mỗi biến thể (NoCG, CG) có baseline và phần agent riêng.
- "Không cải thiện" vẫn là kết quả hợp lệ và vẫn ghi vào slide. ALU nhỏ nên có thể ít chỗ để tối ưu.

**Slide:**
- Mở đầu: cluster CHIA và các khối.
- 3 slide chính: **Sim**, **FPGA**, **ASIC**. Cùng một bố cục: sơ đồ flow; agent đánh giá gì và đã đổi gì; sản phẩm
  cuối (`PASS`, `.bit`, ảnh GDS); bảng baseline / agent cho NoCG và CG.
- Kết: bước tiếp theo (Demo 2).

**Rủi ro cần kiểm tra sớm:**
- CTS của OpenROAD có xử lý được cell ICG trên cây clock không. Nếu không, ASIC chạy NoCG trước để chứng minh flow,
  CG xử lý sau.
- Loop "agent Claude ghi file rồi CHIA build/kiểm tra" chưa từng chạy: smoke test đầu tiên.
- Nếu chưa có board, nhánh FPGA dừng ở bitstream, vẫn đủ chứng minh flow.

### Demo 2: SoC (nếu còn thời gian)

Thay ALU bằng một SoC nhỏ, giữ nguyên loop, node và khối của Demo 1; chỉ đổi RTL, testbench và constraint.

- **Lõi đề xuất:** PicoRV32 (một file Verilog, rất nhỏ, từng dùng trên sky130 trong Caravel). Mini-SoC: PicoRV32,
  ROM chương trình 1–2 KB, RAM 256–512 B bằng flip-flop, UART, GPIO (LED, nút), timer.
- **Ứng dụng đề xuất:** Game of Life 16×16 real-time: UART in lưới mỗi nhịp timer, LED hiện số thế hệ.
- **Yêu cầu riêng:** SoC chạy real-time: sim RTL trước rồi nạp xuống FPGA, cùng phần mềm. Khác biệt giữa sim
  (chậm) và FPGA (thời gian thật) phải nhìn thấy được.
- **Nối từ Demo 1:** CPU rảnh giữa hai nhịp timer, nên clock gating CPU hoặc ngoại vi lúc chờ là ứng dụng tự nhiên
  (PicoRV32 không có WFI: thêm thanh ghi "sleep", timer đánh thức).

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
2. **Lõi CPU cho Demo 2:** đề xuất PicoRV32, chốt khi bắt đầu Demo 2.
3. **`alu.v` cho Demo 1:** người dùng đưa sẵn.
