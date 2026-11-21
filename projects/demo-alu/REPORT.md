# Báo cáo Demo 1: ALU 8 bit qua Sim → FPGA → ASIC với agent Claude trong CHIA

Ngày chạy: 20–21/11/2026. Máy: một máy Bazzite (20 CPU, 23 GB RAM), cluster CHIA theo khối. Mọi số liệu dưới đây đo
bằng công cụ thật trên máy này. Cách chạy lại có trong [`README.md`](README.md); nhật ký chi tiết, kể cả các lỗi đã
gặp, ở [`docs/demo-task.md`](../../docs/demo-task.md).

## 1. Đề bài

> Sử dụng framework CHIA để chạy một flow thiết kế vi mạch số mã nguồn mở cho cả FPGA và ASIC. Mục tiêu: tận dụng LLM
> agent trong CHIA để thiết kế và tối ưu từng bước trong quá trình xây dựng SoC, đi từ FPGA xuống ASIC bằng OpenROAD.

Demo 1 chứng minh flow **đúng và đủ** trên một thiết kế nhỏ để mỗi bước chạy trong vài phút:

- **Đúng:** mỗi bước có gate kiểm tra.
- **Đủ:** ASIC đi tới GDS.

Demo 2 (SoC PicoRV32) là phần tuỳ chọn, chưa làm.

| Hạng mục | Lựa chọn |
|---|---|
| Thiết kế | ALU 8 bit, 8 phép (ADD, SUB, AND, OR, XOR, SHL, SHR, SLT), pipeline 2 tầng, cờ `zero`/`carry`/`valid` |
| Biến thể | **NoCG**: clock enable thường. **CG**: clock gating bằng module `icg` (sim: latch + AND; ASIC: `sky130_fd_sc_hd__dlclkp`) |
| Sim | Verilator 5.022, testbench tự kiểm so với mô hình tham chiếu |
| FPGA | Vivado 2025.1, `xc7a100tcsg324-1` (Arty A7-100T), 100 MHz. Vỏ `fpga_top` tự sinh kích thích (LFSR) và tự kiểm (MISR) |
| ASIC | Yosys 0.68 + OpenROAD (ORFS), sky130hd, clock 10 ns, RTL → GDS |
| Agent | Claude Code (model `opus`) chạy trong khối `claude` của cluster |

## 2. Kiến trúc: CHIA điều phối

Cluster gồm năm khối. Mỗi khối là một container Ray worker có đúng công cụ của một bước:

```
                  ┌────────── head (Ray, driver: baseline.py / agent.py) ──────────┐
                  │                                                                 │
   chipyard ── Verilator build      verilator ── chạy sim      vivado ── xsim, Vivado
   asic ── ORFS (6 bước make)       claude ── Claude Code (đọc báo cáo, sửa file)
                  │                                                                 │
                  └──────── WORK_DIR chung (~/chia_demo), mount vào mọi khối ───────┘
```

- **Task:** mỗi bước là một `@ChiaFunction(resources={...})`. Driver gọi `.chia_remote()` và Ray xếp task lên khối có
  resource tương ứng. Bước sau chỉ chạy khi bước trước qua gate.
- **Timeline:** profiler của CHIA ghi timeline của mọi task (`profile/ChiaProfileCollector_profile.html`).
- **Theo dõi trực tiếp:** trang `status.html` cho thấy đang ở bước nào và kết quả các bước trước.

## 3. Phương pháp

### Pha 1: baseline

RTL đưa sẵn, các thiết lập gần mặc định:

- `config.mk` như ví dụ `gcd` của ORFS: `CORE_UTILIZATION` 40, `PLACE_DENSITY` 0,65.
- `build.tcl` dùng directive mặc định của Vivado.

Chạy Sim → FPGA → ASIC, lưu mọi số liệu. Baseline được chạy hai lần và ra **đúng từng số** ở cả ba bước, nên công cụ
tất định, nhiễu bằng 0. Vì vậy ngưỡng so sánh ở pha 2 chỉ để bỏ qua sai khác làm tròn.

### Pha 2: agent

Với mỗi bước (Sim → FPGA → ASIC), tối đa 2 vòng. Mỗi vòng:

1. Chép bản tốt nhất hiện tại sang thư mục vòng.
2. Task trên khối `claude`: Claude đọc báo cáo của bản tốt nhất và lịch sử các vòng trước, rồi sửa các file được phép
   ngay trong thư mục vòng. Khối `claude` không có Verilator, Vivado hay ORFS, nên agent không tự build được.
3. CHIA hoàn lại mọi sửa ngoài phạm vi, kiểm luật (không đổi part, clock, design, file nguồn, SDC), rồi đo lại bước đó
   trên công cụ thật.
4. Gate, sau đó so với bản tốt nhất. Vòng được nhận thì bước sau xây trên bản đó.

| Bước | Phạm vi sửa | Gate | Luật nhận |
|---|---|---|---|
| Sim | `sim/tb_<DUT>.v` | `RESULT: PASS` | `checked` tăng |
| FPGA | `fpga/build.tcl`, `fpga/sources.f`, file `.v` mới riêng cho FPGA | `tb_fpga_top` PASS (xsim, UNISIM), có `.bit`, WNS ≥ 0 | Pareto chặt: không kém ở WNS (±0,02 ns), công suất động (±0,5 mW), LUT+FF (±0,5 ô), tốt hơn ở ít nhất một |
| ASIC | `asic/config.mk` | có GDS, setup/hold slack ≥ 0, DRC = 0 | không kém quá 1 % ở diện tích cell, diện tích die, công suất, tốt hơn quá 1 % ở ít nhất một |

`rtl/` chỉ đọc với agent. Tối ưu nào riêng cho FPGA phải nằm trong file mới ở `fpga/`, nên **không lan sang ASIC**.

### Pha 3: so sánh

`agent.py` sinh `results/compare.md`: bảng baseline / agent từng bước, kèm lý do nhận/loại và tóm tắt của agent ở mỗi
vòng.

## 4. Kết quả pha 1: baseline

| Bước | Chỉ số | NoCG | CG |
|---|---|---|---|
| Sim | Kết quả, số lần kiểm tra | PASS, 6 272 | PASS, 6 272 |
| FPGA | LUT / FF / BUFG | 138 / 139 / 1 | 140 / 141 / 1 |
| | WNS / WHS (ns) | 4,579 / 0,024 | 1,502 / 0,052 |
| | Công suất tổng / động (W) | 0,099 / 0,002 | 0,102 / 0,005 |
| | Cảnh báo Vivado | 1 | 4 |
| ASIC | Diện tích cell (µm²), util | 3 078, 44,8 % | 2 975, 46,9 % |
| | Diện tích die (µm²) | 7 534 | 6 926 |
| | Số cell (flop, ICG, buffer clock) | 356 (31, 0, 9) | 306 (31, 2, 18) |
| | Setup / hold WS (ns) | 6,815 / 0,493 | 6,957 / 0,406 |
| | Công suất (mW) | 0,368 | 0,324 |
| | DRC | 0 | 0 |

- Số liệu FPGA tính cả vỏ `fpga_top` (LFSR, MISR, bộ đếm).
- Thời gian: FPGA khoảng 64 s mỗi demo. ASIC khoảng 2,5 phút mỗi demo; riêng bước route của CG mất 113 s, của NoCG 35 s.
- Công suất ASIC là ước lượng của OpenSTA với hoạt động mặc định, không lấy từ VCD.

**Nhận xét:** clock gating cho kết quả **ngược nhau** trên hai nền tảng.

- **FPGA:** CG kém hơn. Clock đi qua latch + LUT nên đường găng chỉ còn nửa chu kỳ: WNS giảm 3 ns, công suất động tăng,
  Vivado báo cảnh báo gated clock.
- **ASIC:** CG tốt hơn. Bỏ được mux hồi tiếp nên ít hơn 50 cell, diện tích giảm 3 %, công suất giảm 12 %; đổi lại số
  buffer clock gấp đôi. CTS của OpenROAD xử lý đúng cell ICG.

Đây đúng là lý do tối ưu cho FPGA không được mang sang ASIC.

## 5. Kết quả pha 2 và 3: agent so với baseline

Lần chạy: NoCG `agent-20261121-141430`, CG `agent-20261121-143619`. Cả hai kết thúc với `KẾT QUẢ: OK`; các vòng được
nhận là `sim-r1`, `sim-r2`, `asic-r1`, `asic-r2`. NoCG chạy hết khoảng 22 phút. Tổng thời gian agent làm việc là 803 s
với NoCG và 550 s với CG; phần còn lại là CHIA đo lại.

| | NoCG: baseline → agent | CG: baseline → agent |
|---|---|---|
| **Sim**: số lần kiểm tra (vẫn PASS) | 6 272 → 542 046 (×86) | 6 272 → 1 509 090 (×241) |
| **FPGA** | 2 vòng đều LOẠI, giữ baseline | 2 vòng đều LOẠI, giữ baseline |
| **ASIC**: diện tích cell (µm²) | 3 077,95 → 3 021,65 (−1,8 %) | 2 975,35 → 2 835,22 (−4,7 %) |
| **ASIC**: diện tích die (µm²) | 7 534,24 → 4 641,70 (**−38,4 %**) | 6 925,57 → 5 504,90 (**−20,5 %**) |
| **ASIC**: util | 44,8 % → 73,9 % | 46,9 % → 58,1 % |
| **ASIC**: công suất (mW) | 0,368 → 0,361 (−1,8 %) | 0,324 → 0,302 (**−6,8 %**) |
| **ASIC**: setup / hold WS (ns) | 6,815 → 7,034 / 0,493 → 0,481 | 6,957 → 7,042 / 0,406 → 0,404 |
| **ASIC**: số cell, DRC | 356 → 468, 0 | 306 → 386, 0 |

### Sim: agent mở rộng testbench

Cả bốn vòng được nhận, và vẫn PASS trên Verilator thật.

- **Vòng 1:** agent tìm ra những gì testbench gốc chưa kiểm:
  - độ trễ `valid` đúng 2 chu kỳ;
  - giá trị biên quanh 7F/80 và FF/00;
  - phép dịch với `b[7:3] ≠ 0`;
  - mẫu `en` (liên tiếp, xen kẽ, rảnh dài, output phải giữ);
  - reset bất đồng bộ giữa chừng.
- **Vòng 2:** vét cạn ADD/SUB/SLT với mọi cặp 256 × 256, và đếm cả các kiểm tra giữ giá trị trước đó chạy mà không được
  tính.
- **Riêng CG:** agent tự thêm phép đếm cạnh của `gclk_in`/`gclk_out` mỗi chu kỳ: đúng 1 cạnh khi `en` = 1, 0 cạnh khi
  rảnh, kể cả khi `en` đổi trong pha clk = 1. Phép đếm này kiểm tra trực tiếp rằng latch ICG không tạo xung giả, một
  rủi ro đặc thù của clock gating.
- **Cái giá:** thời gian mô phỏng dài hơn và file VCD mặc định khoảng 90 MB. Thước đo `checked` chỉ đếm số phép so sánh,
  không đo độ phủ thật.

### FPGA: bốn đề xuất, không đề xuất nào thắng trên mọi chỉ số

| Vòng | Agent làm gì | Đo thật | Kết quả |
|---|---|---|---|
| NoCG r1 | Retiming (tính ALU trước tầng 1, lưu 9 bit kết quả thay cho 19 bit vào), ánh xạ tay LUT6_2/CARRY4, directive `ExtraTimingOpt`/`Explore` | WNS 4,579 → 4,94 ns; công suất động 2 → 5 mW; LUT+FF 277 → 302 | LOẠI |
| NoCG r2 | Giữ thanh ghi vào, ADD/SUB/SLT dùng chung một bộ cộng | LUT+FF 277 → 262; WNS 4,579 → 4,212 ns | LOẠI |
| CG r1 | `icg_fpga.v` là cổng AND khi tổng hợp + `-gated_clock_conversion auto` (Vivado đổi gating thành CE của FF); `build.tcl` tự dừng nếu còn latch hoặc LUT trên chân clock | WNS 1,502 → 4,499 ns; công suất động 5 → 2 mW; LUT+FF 281 → 284 | LOẠI |
| CG r2 | `CG_alu_fpga.v` dùng CE (không latch, không LUT trên cây clock) + retiming | WNS 1,502 → 4,436 ns; LUT+FF 281 → 312 | LOẠI |

- **Agent tự kiểm trước khi nộp.** Dù không có Vivado, agent tự viết mô hình Python và kiểm vét cạn 524 288 tổ hợp
  op × a × b, 0 lỗi. Ở CG r2, agent còn mô phỏng `fpga_top` theo chu kỳ và khớp chữ ký MISR `0xAC066D58`. Các dự đoán
  về tài nguyên thì sai: chỉ đo thật mới biết.
- **Agent học từ vòng trước.** Ở NoCG r2, agent đọc lý do vòng r1 bị loại (công suất động tăng vì ALU đảo trạng thái mỗi
  chu kỳ theo LFSR) và quay lại giữ thanh ghi vào.
- **Gate làm đúng việc.** Mỗi vòng đều có ít nhất một chỉ số kém hơn baseline, nên CHIA giữ bản baseline.
- **CG r1 là đánh đổi đáng chú ý nhất.** Agent xác định đúng vấn đề của baseline CG trên FPGA (latch + LUT trên đường
  clock, đường găng nửa chu kỳ) và sửa được: WNS gấp 3 lần, công suất động giảm 60 %. Đổi lại là 3 ô LUT+FF (+1,1 %).
  Theo luật Pareto chặt đã chốt từ trước, vòng này bị loại. Chúng tôi **giữ nguyên luật** thay vì nới ra sau khi đã thấy
  kết quả. Nếu ưu tiên timing, luật hợp lý sẽ là "WNS là chỉ số chính, tài nguyên được kém vài %". Khi đổi luật thì phải
  chạy lại.

### ASIC: agent điều chỉnh config của ORFS

| Vòng | NoCG | CG |
|---|---|---|
| r1 | `ABC_AREA=1` (timing dư nhiều nên tổng hợp theo diện tích), `CORE_UTILIZATION` 40 → 55, `PLACE_DENSITY` 0,65 → 0,75: die −27,5 % | Chỉ `ABC_AREA=1`, vì lần route trước phải lặp 6 vòng mới sạch DRC nên agent thận trọng: công suất −7,1 % |
| r2 | `CORE_UTILIZATION` 55 → 65, `PLACE_DENSITY` 0,75 → 0,85: die thêm −15 % | `CORE_UTILIZATION` 40 → 50, `DONT_BUFFER_PORTS=1` (bỏ khoảng 33 buffer ở cổng I/O): diện tích cell −4 %, die −19,5 % |

- **Gate và timing:** mọi vòng đều qua gate (GDS, slack ≥ 0, DRC = 0). Setup slack còn tăng nhẹ.
- **Số cell tăng 26–31 %** dù diện tích giảm: ABC theo diện tích dùng nhiều cell nhỏ hơn.
- **NoCG r2** được nhận dù công suất tăng 0,4 %, vì mức này nằm trong ngưỡng 1 % của luật.
- **Agent tự nhận ra giới hạn của bước:** ở NoCG, flip-flop và cây clock chiếm khoảng 70 % công suất, phần này do RTL quyết
  định, `config.mk` không đổi được.

## 6. Kết luận

1. **Flow đúng và đủ.** CHIA điều phối toàn bộ Sim (Verilator) → FPGA (Vivado, ra bitstream) → ASIC (Yosys + OpenROAD,
   ra GDS) trên năm khối của cluster. Mỗi bước có gate, và bước sau chỉ chạy khi bước trước qua.
2. **Agent tham gia được ở cả ba bước.** Claude đọc báo cáo thật của công cụ, đề xuất thay đổi có lý do, và CHIA đo
   lại trên công cụ thật để nhận hoặc loại.
   - Sim: số lần kiểm tra tăng 86–241 lần.
   - ASIC: die −20 đến −38 %, công suất CG −6,8 %.
   - FPGA: không vòng nào được nhận, nhưng CG r1 cho thấy agent tìm đúng vấn đề clock gating trên FPGA.
3. **Gate và đo trên công cụ thật là phần không thể thiếu.** Dự đoán của agent không thay được đo thật: ở FPGA, cả bốn
   dự đoán về tài nguyên hoặc timing đều sai một phần. Vòng lặp "đề xuất → đo → nhận/loại" của CHIA giữ cho kết quả
   cuối không bao giờ kém baseline.
4. **FPGA và ASIC tách biệt.** Clock gating có lợi trên ASIC nhưng có hại trên FPGA. `rtl/` cố định, nên cách sửa riêng
   cho FPGA (`icg_fpga.v`, `CG_alu_fpga.v`) không ảnh hưởng nhánh ASIC.

## 7. Hạn chế

- **Thiết kế rất nhỏ.** ALU có khoảng 300 cell, nên còn ít chỗ để tối ưu, nhất là RTL thì cố định.
- **`checked` đo số phép so sánh, không đo độ phủ.** Muốn đo thật thì cần coverage của Verilator.
- **Luật FPGA chặt.** Đánh đổi tốt như CG r1 vẫn bị loại. Chọn luật nhận (Pareto hay chỉ số chính có ngưỡng) là quyết
  định thiết kế, và phải chốt trước khi chạy.
- **Công suất chỉ là ước lượng.** Công suất ASIC tính với hoạt động mặc định của OpenSTA, không lấy từ VCD. Công suất
  FPGA là ước lượng vector-less của Vivado.
- **Mỗi bước chỉ 2 vòng, mỗi cấu hình chạy một lần.** Công cụ tất định, nhưng agent thì không: chạy lại có thể ra đề
  xuất khác.
- **Chưa nạp board.** Nhánh FPGA dừng ở bitstream vì chưa có board.

## 8. Bước tiếp theo

- **Slide:** ba slide chính Sim, FPGA, ASIC, kèm slide mở đầu về cluster và slide kết.
- **Video:** quay `run_demo.py` với trang trạng thái và timeline CHIA.
- **Demo 2 (tuỳ chọn):** mini-SoC PicoRV32, dùng lại loop, task và khối của Demo 1.
