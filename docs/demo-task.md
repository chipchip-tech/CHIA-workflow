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

Demo 1 gồm **hai demo riêng**, mỗi demo một thư mục, có file RTL, testbench, script và (sau này) constraint,
config, kết quả riêng. Hai demo không dùng chung file nào.

| Demo | Thư mục | RTL | Mô tả |
|---|---|---|---|
| **NoCG** | `projects/demo-alu/NoCG/` | `rtl/NoCG_alu.v` | Thanh ghi dùng clock enable thông thường (mux hồi tiếp) |
| **CG** | `projects/demo-alu/CG/` | `rtl/CG_alu.v` + `rtl/icg_sim.v` | Clock của thanh ghi vào/ra đi qua module `icg` |

**Đầu vào:** RTL **đưa sẵn** (không do agent viết), để baseline có đầu vào cố định. Cả hai là ALU 8 bit, 8 phép
tính (ADD, SUB, AND, OR, XOR, SHL, SHR, SLT): thanh ghi vào → ALU tổ hợp → thanh ghi ra, có `en`, cờ `zero`,
`carry`, `valid`, cùng giao diện cổng.

Demo CG có module `icg` là wrapper nhỏ, mỗi nơi một bản (bản sim: `icg_sim.v`):

  | Nơi chạy | Hiện thực ICG |
  |---|---|
  | Sim | Mô hình hành vi (latch + AND) |
  | FPGA | Baseline dùng **nguyên** `icg_sim.v` (Vivado ra latch + LUT trên đường clock, có cảnh báo gated clock). Đổi sang `BUFGCE` là việc của agent ở pha 2 |
  | ASIC | Cell `sky130_fd_sc_hd__dlclkp` của sky130hd |

**Flow:** CHIA chạy **nối tiếp** Sim → FPGA → ASIC. Bước sau chỉ chạy khi bước trước đạt gate.

```
<demo>/rtl/*.v                ─► 1. Sim (verilator) ── PASS ──► 2. FPGA (vivado) ── OK ──► 3. ASIC (ORFS sky130hd)
                                   testbench, chạy test          synth, P&R, .bit          synth → floorplan → place
                                                                                             → CTS → route → .gds
```

**Ba pha** (chạy riêng cho từng demo):

```
Pha 1: BASELINE (không có agent)
  *_alu.v ─► Sim ─► FPGA ─► ASIC      flow cơ bản, thiết lập mặc định; lưu mọi số liệu và báo cáo
                 │
                 ▼
Pha 2: AGENT (Claude trong CHIA)
  đọc báo cáo baseline từng bước ─► đánh giá ─► đề xuất thay đổi (RTL, constraint, config)
  ─► CHIA chạy lại ─► qua gate thì giữ, không thì loại
                 │
                 ▼
Pha 3: SO SÁNH
  bảng baseline / agent theo từng bước (Sim, FPGA, ASIC), mỗi demo một bảng
```

**Từng bước:**

| | Sim | FPGA | ASIC |
|---|---|---|---|
| Các bước | Verilator build (khối chipyard) → chạy test (khối verilator) | Vivado synth → P&R → bitstream, qua vỏ `fpga/fpga_top.v` | Yosys → floorplan → place → CTS → route → GDS |
| Gate (đúng) | Output khớp mô hình tham chiếu (`RESULT: PASS`) | Có `.bit`, WNS ≥ 0 | Có `.gds`, timing đạt, không lỗi |
| Sản phẩm (đủ) | Log `PASS` + waveform | `.bit` + báo cáo tài nguyên | `.gds` + ảnh layout + báo cáo diện tích |
| Thước đo | Đúng/sai; số cycle hoặc độ phủ test nếu có | LUT, FF, WNS/Fmax, công suất | Diện tích, số cell, WNS/Fmax, công suất |
| Khối cluster | `verilator` | `vivado` | `asic` |

**Vỏ FPGA** (`<demo>/fpga/fpga_top.v`): ALU có khoảng 30 cổng nên vỏ tự sinh kích thích (LFSR, rảnh 70 %) và tự kiểm tra
(MISR so với chữ ký tính bằng `golden_sig.py`), ra ngoài chỉ có `clk`, nút reset, 4 LED (nhịp, PASS, FAIL, đã xong khung
đầu). Đổi board chỉ sửa `arty_a7_100t.xdc`. Chưa có board thì dùng part `xc7a100tcsg324-1`, clock 100 MHz.

Agent Claude chạy ở khối `claude`. Ngoài đánh giá và tối ưu, agent có thể viết những thứ phụ của từng bước
(testbench, `.xdc` và top cho board, `config.mk` cho ORFS) và sửa khi một bước fail.

**Quy tắc so sánh công bằng:**
- Cùng file RTL gốc, cùng phiên bản công cụ, cùng board/part và PDK, cùng chu kỳ clock mục tiêu (hoặc ghi rõ nếu
  agent được phép đổi).
- Mọi thay đổi của agent phải qua lại gate Sim. Sai chức năng thì loại.
- Chạy baseline **hai lần** để đo nhiễu (P&R thường dao động khoảng 1–2 %). Chỉ tính là cải thiện khi vượt mức nhiễu.
- Mỗi demo (NoCG, CG) có baseline và phần agent riêng.
- "Không cải thiện" vẫn là kết quả hợp lệ và vẫn ghi vào slide. ALU nhỏ nên có thể ít chỗ để tối ưu.

**Slide:**
- Mở đầu: cluster CHIA và các khối.
- 3 slide chính: **Sim**, **FPGA**, **ASIC**. Cùng một bố cục: sơ đồ flow; agent đánh giá gì và đã đổi gì; sản phẩm
  cuối (`PASS`, `.bit`, ảnh GDS); bảng baseline / agent của từng demo; cuối cùng có thể đặt NoCG và CG cạnh nhau.
- Kết: bước tiếp theo (Demo 2).

**Rủi ro cần kiểm tra sớm:**
- CTS của OpenROAD có xử lý được cell ICG trên cây clock không. Nếu không, làm demo NoCG trước để chứng minh flow,
  demo CG xử lý sau.
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
3. **RTL cho Demo 1:** đã duyệt. Bước Sim mỗi demo ở `projects/demo-alu/<NoCG|CG>/sim/` (`tb_<module>.v`, `run_sim.sh`).
   Baseline bước Sim qua CHIA (`baseline.py`) đã chạy thật 20/11/2026, cả hai demo `RESULT: PASS`. Bước FPGA **đã chạy
   thật 21/11/2026** (Vivado 2025.1 ở khối vivado, khoảng 64 s mỗi demo, cả hai `KẾT QUẢ: OK`):

   | Demo | LUT | FF | BUFG | WNS (ns) | WHS (ns) | Công suất tổng / động (W) | Cảnh báo |
   |---|---|---|---|---|---|---|---|
   | NoCG | 138 | 139 | 1 | 4,579 | 0,024 | 0,099 / 0,002 | 1 |
   | CG | 140 | 141 | 1 | 1,502 | 0,052 | 0,102 / 0,005 | 4 |

   Số liệu tính cả vỏ `fpga_top` (LFSR, MISR, bộ đếm). CG trên FPGA **kém hơn** NoCG: clock đi qua latch + LUT nên WNS
   giảm khoảng 3 ns và công suất động tăng. Đây là chỗ cho agent ở pha 2 (đổi sang `BUFGCE`).
   Bước ASIC **đã chạy thật 21/11/2026** (ORFS sky130hd ở khối asic, `asic/config.mk` gần mặc định, clock 10 ns; CG dùng
   `asic/icg_sky130.v` = `sky130_fd_sc_hd__dlclkp_1`). Cả ba bước qua gate, hai demo `KẾT QUẢ: OK`, khoảng 2,5 phút mỗi demo:

   | Demo | Diện tích (µm²) | Util | Cell | Flop | ICG | Buffer clock | Setup / hold WS (ns) | Công suất (mW) | DRC | Route (s) |
   |---|---|---|---|---|---|---|---|---|---|---|
   | NoCG | 3078 | 44,8 % | 356 | 31 | 0 | 9 | 6,815 / 0,493 | 0,368 | 0 | 35 |
   | CG | 2975 | 46,9 % | 306 | 31 | 2 | 18 | 6,957 / 0,406 | 0,324 | 0 | 113 |

   Trên ASIC, CG **tốt hơn** NoCG (ngược với FPGA): bỏ mux hồi tiếp nên ít hơn 50 cell, diện tích giảm 3 %, công suất giảm
   12 %, đổi lại buffer clock tăng gấp đôi. CTS của OpenROAD xử lý được cell ICG (rủi ro ở trên đã gỡ). Công suất là ước
   lượng của OpenSTA với hoạt động mặc định, không phải từ VCD. Thời gian route CG dài gấp 3 lần, chưa rõ lý do (cần chạy
   lại để xem có phải nhiễu).
   Baseline chạy lần hai (21/11/2026) ra **đúng từng số** ở cả ba bước, kể cả thời gian route (CG vẫn 113 s): công cụ
   tất định, nhiễu bằng 0, nên ngưỡng so sánh ở pha 2 chỉ bỏ qua sai khác làm tròn.
   **Pha 2** (`agent.py`, mỗi demo một bản). Mỗi bước tối đa `--rounds` vòng (mặc định
   2): Claude Code ở khối `claude` đọc báo cáo của bản tốt nhất, sửa file được phép ngay trong thư mục vòng, CHIA đo lại
   trên các khối như baseline. Phạm vi sửa: Sim chỉ `sim/tb_*.v` (thêm kiểm tra, nhận khi số lần kiểm tra tăng); FPGA chỉ
   `fpga/` (`build.tcl`, `sources.f`, file `.v` mới riêng cho FPGA; gate thêm `tb_fpga_top` trong xsim có UNISIM); ASIC
   chỉ `asic/config.mk`. `rtl/`, `.xdc`, `.sdc` cố định; sửa ngoài phạm vi bị hoàn lại. Nhận vòng khi không kém ở chỉ số
   nào và tốt hơn ở ít nhất một. Kết quả: `results/compare.md` (bảng baseline / agent cho slide). Đã thử trong sandbox với
   Claude, Vivado, ORFS giả (Verilator thật): nhận/loại đúng, sửa `rtl/` bị hoàn lại. `fpga/build.tcl` giờ đọc danh sách
   file từ `fpga/sources.f` (cùng tập file như trước). `projects/demo-alu/clean_runs.py` dọn các lần chạy cũ trong
   `WORK_DIR`.
   **Chạy thử thật NoCG `--rounds 1` (21/11/2026, `KẾT QUẢ: OK`):** Claude ở khối `claude`, Verilator/Vivado/xsim/ORFS
   thật, `build.tcl` đọc `sources.f` chạy được.
   - Sim NHẬN: thêm kiểm tra độ trễ đúng 2 cạnh, giá trị biên, dịch với `b[7:3]≠0`, reset giữa chừng; checked 6272 → 6888.
   - FPGA LOẠI: agent ánh xạ tay ALU lên LUT6_2 + CARRY4 (`fpga/NoCG_alu.v`) và thêm directive place/route; đo thật kém
     hơn (WNS 4,579 → 4,373, LUT+FF 277 → 309). Gate chặn đúng, bản baseline giữ nguyên.
   - ASIC NHẬN: `ABC_AREA=1`, `CORE_UTILIZATION` 40 → 55, `PLACE_DENSITY` 0,65 → 0,75: diện tích cell −1,2 %, die −27,5 %,
     công suất −2,2 %, setup WS 6,815 → 7,025 ns, DRC 0.
   - Lỗi lộ ra và đã sửa: (1) bước FPGA/ASIC lấy báo cáo từ thư mục vòng sim được nhận (không có báo cáo FPGA/ASIC), nên
     agent ASIC chỉ thấy `metrics.json`; giờ mỗi bước lấy báo cáo từ baseline hoặc vòng được nhận của chính bước đó;
     (2) câu gate Sim trong prompt ghi ngược ("accepted if checked does not increase"), agent tự nhận ra; đã sửa.

   **Chạy chính `--rounds 2`, cả hai demo (21/11/2026, `KẾT QUẢ: OK`, không có `CẢNH BÁO: context`):** NoCG
   `agent-20261121-141430`, CG `agent-20261121-143619`. Vòng được nhận ở cả hai: `sim-r1`, `sim-r2`, `asic-r1`, `asic-r2`.

   | | NoCG baseline → agent | CG baseline → agent |
   |---|---|---|
   | Sim: số lần kiểm tra (PASS) | 6272 → 542 046 | 6272 → 1 509 090 |
   | FPGA | cả 2 vòng LOẠI, giữ baseline | cả 2 vòng LOẠI, giữ baseline |
   | ASIC: diện tích cell (µm²) | 3077,95 → 3021,65 (−1,8 %) | 2975,35 → 2835,22 (−4,7 %) |
   | ASIC: diện tích die (µm²) | 7534,24 → 4641,70 (−38,4 %) | 6925,57 → 5504,90 (−20,5 %) |
   | ASIC: công suất (mW) | 0,368 → 0,361 (−1,8 %) | 0,324 → 0,302 (−6,8 %) |
   | ASIC: setup / hold WS (ns) | 6,815 → 7,034 / 0,493 → 0,481 | 6,957 → 7,042 / 0,406 → 0,404 |
   | ASIC: số cell, DRC | 356 → 468, 0 | 306 → 386, 0 |

   - Sim: vòng 1 thêm quét op × a × biên b, mẫu `en`, reset giữa chừng, độ trễ valid; vòng 2 vét cạn ADD/SUB/SLT 256×256 và
     đếm các kiểm tra giữ giá trị. CG thêm đếm cạnh `gclk_in`/`gclk_out` (không xung giả). VCD mặc định lên khoảng 90 MB.
   - ASIC: NoCG `ABC_AREA=1`, rồi nén `CORE_UTILIZATION` 40 → 55 → 65, `PLACE_DENSITY` 0,65 → 0,75 → 0,85. CG `ABC_AREA=1`, rồi
     `CORE_UTILIZATION` 40 → 50 và `DONT_BUFFER_PORTS=1`. Số cell tăng vì ABC theo diện tích dùng nhiều cell nhỏ hơn. NoCG r2
     được nhận dù công suất +0,4 % (trong ngưỡng 1 % của luật ASIC).
   - FPGA, 4 vòng LOẠI theo luật Pareto chặt (không chỉ số nào kém hơn; LUT+FF chỉ cho lệch 0,5 ô):
     - NoCG r1 (retiming + ánh xạ tay LUT6_2/CARRY4 + directive): WNS 4,579 → 4,94 nhưng P động 0,002 → 0,005, LUT+FF 277 → 302.
     - NoCG r2 (dùng chung bộ cộng ADD/SUB/SLT): LUT+FF 277 → 262 nhưng WNS 4,579 → 4,212.
     - CG r1 (`icg_fpga.v` là AND khi tổng hợp + `-gated_clock_conversion auto`): WNS 1,502 → 4,499, P động 0,005 → 0,002, nhưng
       LUT+FF 281 → 284 (+3 ô, +1,1 %). Vòng đáng nhận nhất về kỹ thuật nhưng bị luật chặn.
     - CG r2 (`CG_alu_fpga.v` dùng CE, không latch, retiming): WNS 1,502 → 4,436 nhưng LUT+FF 281 → 312.
   - Quyết định (21/11/2026): **giữ nguyên luật FPGA**; slide trình bày CG r1 như một đánh đổi bị luật chặn.

   **Trang trạng thái `status.html` (21/11/2026, chưa chạy thật):** `status_page.py` (giống hệt ở `NoCG/` và `CG/`).
   `baseline.py` và `agent.py` ghi `<run_dir>/status.html` sau mỗi sự kiện: ba ô Sim → FPGA → ASIC, task CHIA đang chạy
   trên khối nào, bảng baseline / tốt nhất, thẻ từng vòng (NHẬN/LOẠI, lý do, phần tóm tắt của agent), ảnh layout baseline
   và bản tốt nhất. Trang tự làm mới 3 giây khi chưa xong; mở bằng trình duyệt (`file://`), không cần server. Dựng lại từ
   lần chạy đã xong: `python status_page.py <run_dir>`. Đã thử trong sandbox với dữ liệu giả (render, chụp Chromium);
   chưa thử với job thật.
   Giao diện tối. Mở `file://` trên máy người dùng không được: `xdg-open` trong PATH là bản distrobox (`You must run
   distrobox-host-exec inside a container!`), và job chạy dưới Ray job server nên không tự mở trình duyệt được. Vì vậy
   có `projects/demo-alu/run_demo.py`: chọn run_id, phục vụ `<WORK_DIR>/demo-alu` ở `http://127.0.0.1:8765` (trang chờ tự
   làm mới khi job chưa ghi trang), mở trình duyệt (`/usr/bin/xdg-open`, `gio open`, Firefox Flatpak), rồi `chia job submit`.
   `run_demo.py show <run_dir>` xem lại lần chạy cũ. Đã thử trong sandbox (server, trang chờ, lệnh submit với `chia` giả);
   chưa thử trên máy người dùng.

   **Củng cố Demo 1 (từ 24/11/2026).** Kế hoạch đã chốt với người dùng, mục tiêu là kết quả đủ và đúng quy trình:
   (1) LVS cho ASIC; (2) Sim đo coverage, luật nhận đổi sang coverage; (3) FPGA: hai demo cùng một tần số, bản CG cho
   FPGA dùng BUFGCE/CE (file riêng trong `fpga/`), bảng Fmax, công suất từ SAIF chỉ tính phần ALU, quét tỉ lệ rảnh;
   (4) chạy lại baseline + agent. Hoãn: công suất ASIC từ VCD, MMCM trên board.
   **LVS, chạy thật 25/11/2026:** task `asic_lvs` sau `finish` chạy `make lvs` của ORFS (bản trên máy: KLayout 0.30.12,
   deck Ruby `sky130hd.lylvs`). Gate ASIC thêm "LVS khớp", ở cả baseline và agent. `baseline.py --stages asic`: NoCG và
   CG đều `lvs: match` (log `Congratulations! Netlists match.`), khoảng 2,4 s; mọi số khác trùng từng số với lần 21/11.
   Thử cố ý làm sai (25/11/2026, chạy thật): đổi chỗ dây CLK và D của flop `a_q[0]` trong `6_final_concat.cdl` thì LVS báo `ERROR : Netlists don't match`, nên gate chặn được
   lỗi thật (lần thử đầu không sửa được dòng nào vì CDL viết một instance trên nhiều dòng `+`). Chưa: chạy agent ASIC với
   gate LVS.
