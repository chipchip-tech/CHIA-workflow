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
| Sim | `sim/tb_<DUT>.v` | `RESULT: PASS` | `checked` tăng (lần chạy 21/11). Từ 25/11/2026 đổi sang coverage, xem mục 7 |
| FPGA | `fpga/build.tcl`, `fpga/sources.f`, file `.v` mới riêng cho FPGA | `tb_fpga_top` PASS (xsim, UNISIM), có `.bit`, WNS ≥ 0 | Pareto chặt: không kém ở WNS (±0,02 ns), công suất động (±0,5 mW), LUT+FF (±0,5 ô), tốt hơn ở ít nhất một |
| ASIC | `asic/config.mk` | có GDS, setup/hold slack ≥ 0, DRC = 0, LVS khớp (từ 25/11/2026) | không kém quá 1 % ở diện tích cell, diện tích die, công suất, tốt hơn quá 1 % ở ít nhất một |

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
| | LVS (thêm 25/11/2026) | khớp | khớp |

- Số liệu FPGA tính cả vỏ `fpga_top` (LFSR, MISR, bộ đếm).
- Thời gian: FPGA khoảng 64 s mỗi demo. ASIC khoảng 2,5 phút mỗi demo; riêng bước route của CG mất 113 s, của NoCG 35 s.
- Công suất ASIC là ước lượng của OpenSTA với hoạt động mặc định, không lấy từ VCD.
- LVS thêm sau, ngày 25/11/2026: task `asic_lvs` chạy `make lvs` của ORFS (KLayout 0.30.12, deck `sky130hd.lylvs`), so
  layout GDS với netlist CDL. Cả hai demo khớp, mất khoảng 2,4 s; mọi số khác của bước ASIC trùng từng số với lần chạy
  21/11. Thử cố ý làm sai: đổi chỗ dây CLK và D của flop `a_q[0]` trong `6_final_concat.cdl` thì LVS báo `ERROR : Netlists don't match`. Các vòng agent ASIC ở mục 5 chạy trước khi có LVS, nên chưa qua gate này.

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
   - Cập nhật 26/11 (luật mới, đủ gate sign-off): CG fpga-r1 được nhận, P động −21,3 %; xem mục 9.
3. **Gate và đo trên công cụ thật là phần không thể thiếu.** Dự đoán của agent không thay được đo thật: ở FPGA, cả bốn
   dự đoán về tài nguyên hoặc timing đều sai một phần. Vòng lặp "đề xuất → đo → nhận/loại" của CHIA giữ cho kết quả
   cuối không bao giờ kém baseline.
4. **FPGA và ASIC tách biệt.** Clock gating có lợi trên ASIC nhưng có hại trên FPGA. `rtl/` cố định, nên cách sửa riêng
   cho FPGA (`icg_fpga.v`, `CG_alu_fpga.v`) không ảnh hưởng nhánh ASIC.

## 7. Hạn chế

- **Thiết kế rất nhỏ.** ALU có khoảng 300 cell, nên còn ít chỗ để tối ưu, nhất là RTL thì cố định.
- ~~**`checked` đo số phép so sánh, không đo độ phủ.**~~ Đã khắc phục 25/11: đo coverage (mục 9).
- ~~**Luật FPGA chặt.**~~ Đã đổi 25/11, chốt trước khi chạy lại: LUT+FF được kém ≤ 3 % (mục 9).
- **Công suất ASIC vẫn là ước lượng** của OpenSTA với hoạt động mặc định, không lấy từ VCD. Công suất FPGA đã đo bằng
  SAIF từ 25/11 (mục 9).
- ~~**ASIC chưa chạy LVS.**~~ Đã thêm 25/11 (mục 9).
- **STA chỉ có corner tt.** Máy chỉ có `.lib` tt_025C_1v80, nên corner chậm/nhanh thay bằng tt + OCV ±10 %; chưa phải
  sign-off ss/ff thật (mục 9). Chưa có kiểm tra tương đương hình thức (LEC) và phân tích IR drop.
- **Fmax FPGA có sai số cỡ 0,1–0,2 ns**: kết quả của Vivado không đơn điệu theo chu kỳ, bản latch còn có time borrowing.
- **Clock > 100 MHz chưa chạy được trên board** vì chưa có MMCM; số ở 188,7 MHz là số của công cụ.
- **Mỗi bước chỉ 2 vòng, mỗi cấu hình chạy một lần.** Công cụ tất định, nhưng agent thì không: chạy lại có thể ra đề
  xuất khác.
- **Chưa nạp board.** Nhánh FPGA dừng ở bitstream vì chưa có board.
- **GLS chưa có SDF.** Mô phỏng netlist (FPGA và ASIC) là zero-delay: kiểm chức năng của netlist, chưa kiểm timing.
- **Một clock, chưa kiểm CDC/RDC.** FPGA out-of-context (ASCON) chưa đặt độ trễ I/O (Vivado báo TIMING-18, mức Warning).
- **Kiểm chứng chức năng ASCON còn mỏng:** testbench có hướng, một bộ vector, chưa có mô hình tham chiếu (mục 10).
- **Ngoài phạm vi:** DFT/scan, IR drop/EM, PDK thương mại và deck DRC của foundry.

## 8. Bước tiếp theo

- **Slide:** ba slide chính Sim, FPGA, ASIC, kèm slide mở đầu về cluster và slide kết.
- **Video:** quay `flow/run.py` với trang status và timeline CHIA (mục 10).
- **Demo 2 (tuỳ chọn):** mini-SoC PicoRV32, dùng lại loop, task và khối của Demo 1.

## 9. Củng cố ngày 25/11/2026

Mục tiêu: kết quả đủ và đúng quy trình. Mọi số dưới đây đo thật trên máy, bằng `baseline.py`, `fpga_sweep.py` và
`agent.py`. Mục 5 là kết quả của luật cũ (21/11); kết quả agent với luật mới ở cuối mục này.

### ASIC: thêm LVS

- Task mới `asic_lvs` chạy `make lvs` của ORFS sau `finish`. KLayout so layout GDS với netlist, khoảng 2,4 s.
- Gate ASIC thêm điều kiện "LVS khớp".
- Cả hai demo khớp; mọi số ASIC khác trùng từng số với baseline 21/11.
- Thử cố ý làm sai (đổi dây CLK và D của một flip-flop trong netlist): LVS báo không khớp, gate chặn đúng.

### Sim: đo coverage thật

- `sim/fcov_<DUT>.sv` liệt kê các tình huống cần thử (bin): kết quả 0/khác 0 của từng phép, carry, mọi độ dịch, biên SLT
  có dấu, tràn có dấu, mẫu `en`, reset giữa chừng; CG thêm `en` đổi khi clock đang ở mức cao. File cố định, agent không
  sửa được.
- Verilator đo thêm line/toggle/branch của riêng ALU.

| | Bin functional | Line | Toggle | Branch |
|---|---|---|---|---|
| NoCG | 51/55 (thiếu 4 bin reset) | 15/16 | 62/62 | 8/8 |
| CG | 51/57 (thiếu 4 bin reset, 2 bin `en` khi clock cao) | 14/15 | 68/69 | 8/10 |

- Luật Sim mới: PASS, không loại coverage nào giảm, số bin tăng. Thêm phép so sánh mà không phủ thêm tình huống thì không
  được nhận.

### FPGA: so CG và NoCG công bằng

Ba cách làm clock gating trên FPGA cho demo CG, đều chỉ nằm trong `fpga/` (RTL không đổi):

- **latch**: như baseline cũ, latch + LUT trên đường clock;
- **bufgce**: BUFGCE, bộ đệm clock có chân bật sẵn trong FPGA;
- **ce**: Vivado đổi gating thành chân enable của flip-flop.

**Fmax** (dò tự động, độ phân giải 0,1 ns):

| | NoCG | CG latch | CG bufgce | CG ce |
|---|---|---|---|---|
| Fmax | 227,3 MHz | 128,2 MHz | 196,1 MHz | 232,6 MHz |
| BUFG | 1 | 1 | 3 | 1 |

Bản latch trượt vì hold (clock qua LUT đến các flop lệch nhau). BUFGCE và CE lấy lại tốc độ.

**Công suất động** ở tần số chung 188,7 MHz (5,3 ns), đo bằng hoạt động thật: mô phỏng netlist sau P&R, ghi SAIF,
Vivado tính công suất từ đó (khớp 74–77 % net, độ tin cậy High). Đơn vị mW:

| Gửi phép | NoCG | CG latch | CG bufgce | CG ce |
|---|---|---|---|---|
| 10 % | 4,21 | 6,19 | 8,50 | 4,03 |
| 30 % | 5,58 | 6,60 | 9,36 | 5,19 |
| 70 % | 7,74 | 9,17 | 10,25 | 7,79 |
| 100 % | 8,88 | 10,42 | 11,67 | 8,42 |

- Công suất tăng theo hoạt động. Ước lượng vector-less cũ báo gần như cùng một số ở mọi mức, nên không dùng để so được.
- **Trên FPGA, không cách gating nào tiết kiệm hơn NoCG**, kể cả khi ALU rảnh 90 %. BUFGCE tốn thêm 2,8–4,3 mW vì mỗi
  BUFGCE là một mạng clock toàn cục mà chỉ nuôi khoảng 30 flop. CE ngang NoCG vì sau chuyển đổi cấu trúc gần như giống hệt.
- Bảng thành phần công suất (188,7 MHz, gửi phép 30 %) cho thấy lý do:

| Thành phần (mW) | NoCG | CG bufgce | Chênh |
|---|---|---|---|
| Clocks | 2,627 | 6,268 | +3,641 |
| Slice Logic | 1,358 | 1,344 | −0,014 |
| trong đó Register (139 flop) | 0,115 | 0,115 | 0 |
| Signals | 1,470 | 1,627 | +0,157 |
| Tổng động | 5,575 | 9,359 | +3,784 |

  96 % phần tốn thêm của BUFGCE nằm ở mạng clock. Cả 139 flop chỉ tốn 0,115 mW, nên phần clock gating có thể tiết kiệm
  ở 28 flop của ALU nhỏ hơn rất nhiều so với chi phí hai mạng clock toàn cục mới. Trên FPGA, gating chỉ đáng khi tắt được
  cả một khối lớn.
- Trên ASIC thì ngược lại: CG giảm 12 % công suất (mục 4). Cây clock do CTS dựng vừa đủ cho thiết kế, cell ICG cắt cả
  nhánh cây phía sau, và CG bỏ được mux hồi tiếp. Lưu ý: số ASIC vẫn là ước lượng của OpenSTA với hoạt động mặc định.

### Gate sign-off bổ sung (chạy thật 25/11/2026)

Câu hỏi "flow đã đúng chuẩn chưa" dẫn tới bốn nhóm kiểm tra mới. Baseline đủ 3 bước ở 188,7 MHz
(NoCG `baseline-20261125-230550`, CG `baseline-20261125-231121`), cả hai `KẾT QUẢ: OK`:

| Kiểm tra | Công cụ | NoCG | CG |
|---|---|---|---|
| Lint RTL | `verilator --lint-only -Wall` | 0 cảnh báo | 0 cảnh báo |
| FPGA methodology | `report_methodology` | 0 critical | **2 critical (TIMING-14)** |
| FPGA mô phỏng gate-level | xsim trên netlist sau route, 1 khung | PASS | PASS |
| ASIC DRC sign-off | KLayout, `sky130hd.lydrc` | 0 vi phạm | 0 vi phạm |
| ASIC antenna | detailed route | 0 net | 0 net |
| ASIC LVS | KLayout | khớp | khớp |
| ASIC STA tt (SPEF) setup / hold WS (ns) | OpenSTA, `asic/signoff.tcl` | 6,815 / 0,493 | 6,957 / 0,406 |
| ASIC STA tt + OCV ±10 % setup / hold WS (ns) | như trên, `set_timing_derate` | 6,477 / 0,447 | 6,789 / 0,350 |
| ASIC mô phỏng gate-level | Verilator, `6_final.v` + model cell từ `.lib` | PASS (6 272/6 272) | PASS (6 272/6 272) |

- Mọi số khác (coverage, LUT/FF, WNS, công suất SAIF, diện tích, công suất ASIC) trùng từng số với baseline trước đó.
- **CG latch trên FPGA chưa đạt sign-off:** TIMING-14 báo LUT nằm trên cây clock, đúng chỗ yếu mà agent FPGA đã tự sửa ở
  vòng CG r1 (xem dưới). Baseline vẫn ghi lại làm mốc; từ giờ agent phải đạt methodology mới được nhận.
- STA corner tt khớp số ORFS báo ở bước finish (6,815 ns), nghĩa là `signoff.tcl` đọc đúng thiết kế và SDC.
- GLS ASIC chạy cùng testbench với bước Sim (bỏ phần kiểm tra hộp trắng vì netlist đổi tên tín hiệu). Trong sandbox, cố
  ý bỏ latch trong model ICG thì testbench báo lỗi, nên GLS bắt được lỗi clock gating.
- Lỗi lộ ra khi chạy và đã sửa: ORFS trên máy là bản mới, `load_design` chỉ nhận file `.odb`; gate STA giờ đòi có ít nhất
  một clock để tránh "đạt" giả khi SDC không được đọc.

### Luật nhận đổi cho lần chạy lại agent

- Sim: theo coverage (ở trên).
- FPGA: clock chung 188,7 MHz; WNS và công suất động SAIF không được kém, **LUT+FF được kém tối đa 3 %** (trước là ±0,5
  ô); nếu bản tốt nhất chưa đạt timing thì vòng nào đạt sẽ được nhận. Đổi vì luật cũ loại cả đánh đổi tốt như CG r1.
- ASIC: như cũ, thêm gate LVS.
- Luật chốt trước khi chạy lại, không đổi sau khi thấy kết quả. Đây là số đo cho thấy vì sao tối ưu của FPGA không được mang
  sang ASIC.

### Agent với luật mới (chạy lại 25/11/2026)

Baseline mốc: NoCG `baseline-20261125-214405`, CG `baseline-20261125-214709` (FPGA 188,7 MHz). Lần agent đầu
(`agent-20261125-215351`, `agent-20261125-220808`) cho Sim và ASIC hợp lệ; bước FPGA của lần đó chạy nhầm ở 10 ns nên
đã bỏ, chạy lại riêng (`agent-20261125-222617`, `agent-20261125-223205`) sau khi sửa lỗi. Mọi vòng ASIC qua gate có LVS.

| | NoCG: baseline → agent | CG: baseline → agent |
|---|---|---|
| **Sim**: bin functional | 51/55 → **55/55** (r1) | 51/57 → **57/57**, branch 8 → 10 (r1) |
| **Sim**: số phép so sánh | 6 272 → 6 281 | 6 272 → 6 287 |
| **FPGA** 188,7 MHz | 2 vòng LOẠI, giữ baseline | r1 NHẬN |
| FPGA: WNS (ns) | 0,588 | 0 → 0,462 |
| FPGA: P động SAIF (mW) | 5,575 | 6,602 → 5,174 (**−21,6 %**) |
| FPGA: LUT+FF | 280 | 283 → 286 (+1,1 %) |
| **ASIC**: diện tích cell (µm²) | 3 077,95 → 3 042,92 (−1,1 %) | 2 975,35 → 2 730,12 (**−8,2 %**) |
| **ASIC**: diện tích die (µm²) | 7 534,24 → 5 459,73 (−27,5 %) | 6 925,57 → 5 017,60 (−27,5 %) |
| **ASIC**: công suất (mW) | 0,368 → 0,362 (−1,4 %) | 0,324 → 0,254 (**−21,5 %**) |
| Vòng được nhận | sim-r1, asic-r1 | sim-r1, fpga-r1, asic-r1, asic-r2 |

- **Sim:** agent đủ mọi bin ngay vòng 1 bằng khoảng 10–15 phép kiểm tra đặt đúng chỗ (reset giữa chừng, `en` đổi khi
  clock cao). Vòng 2 agent không sửa gì vì không còn bin nào. Luật cũ khiến agent thêm 0,5–1,5 triệu phép so sánh.
- **FPGA CG r1:** agent đọc báo cáo thật và chẩn đoán đúng: WNS = 0 là do Vivado mượn thời gian qua latch (đường găng
  chỉ có nửa chu kỳ, 2,65 ns), có 2 cảnh báo TIMING-14 (LUT trên cây clock), riêng clock tốn 3,28 mW. Agent sửa:
  - `sources.f`: thay `icg_sim.v` bằng `icg_fpga_ce.v`, biến thể FPGA viết tay có sẵn trong thư mục (agent được dùng, không
    được sửa);
  - `build.tcl`: thêm `-gated_clock_conversion auto`, và directive `Explore` cho place/route.

  Vivado đổi clock gating thành clock enable: hết latch và LUT trên đường clock. Số đo gần trùng biến thể `ce` viết tay
  (5,19 mW). Agent dự đoán công suất "giảm nhẹ hoặc giữ nguyên", đo thật giảm 21,6 %. Luật cũ đã loại đúng kiểu đánh đổi
  này hôm 21/11 (+3 ô LUT+FF).
  r2 (`CG_alu_fpga.v` + `build.tcl`) giảm 14 ô nhưng P động tăng lên 5,446 mW nên bị loại.
- **FPGA NoCG:** r1 (`NoCG_alu_fpga.v`) giảm 15 ô LUT+FF nhưng WNS 0,588 → 0,303 ns, bị loại; r2 (`build.tcl`) không tốt
  hơn chỉ số nào. NoCG ở 188,7 MHz gần như đã tối ưu.
- **ASIC:** NoCG r2 bị loại vì công suất +5,8 % dù diện tích cell −3 %. CG r2 được nhận: diện tích cell và công suất
  cùng giảm.
- **Lỗi lộ ra khi chạy và đã sửa:** vòng đầu của agent chép cây project (`clock.xdc` mặc định 10 ns) thay cho bản đã chuẩn
  bị như baseline (5,3 ns), và gate chỉ so clock với chính vòng đó. Giờ agent dựng nguồn theo tham số của baseline và gate
  so chu kỳ với baseline.

### Agent với đủ gate sign-off (chạy thật 26/11/2026)

Lần chạy cuối, một mạch liền cho mỗi demo, mọi vòng phải qua toàn bộ gate ở trên (lint, methodology, GLS FPGA và ASIC,
DRC KLayout, antenna, LVS, STA hai corner). Baseline mốc: NoCG `baseline-20261125-230550`, CG `baseline-20261125-231121`.
Lần agent: NoCG `agent-20261126-082319` (khoảng 16 phút), CG `agent-20261126-083923`. Cả hai `KẾT QUẢ: OK`.

| | NoCG: baseline → agent | CG: baseline → agent |
|---|---|---|
| **Sim**: bin functional | 51/55 → **55/55** (r1) | 51/57 → **57/57**, branch 8 → 10 (r1) |
| **Sim**: số phép so sánh | 6 272 → 6 277 | 6 272 → 6 305 |
| **FPGA** 188,7 MHz | 2 vòng LOẠI, giữ baseline | r1 NHẬN, r2 LOẠI |
| FPGA: WNS (ns) | 0,588 | 0 → 0,524 |
| FPGA: methodology critical | 0 | 2 (TIMING-14) → **0** |
| FPGA: P động SAIF (mW) | 5,575 | 6,602 → 5,193 (**−21,3 %**) |
| FPGA: LUT+FF | 280 | 283 → 287 (+1,4 %) |
| **ASIC**: diện tích cell (µm²) | 3 077,95 → 3 042,92 (−1,1 %) | 2 975,35 → 2 865,25 (−3,7 %) |
| **ASIC**: diện tích die (µm²) | 7 534,24 → 5 459,73 (−27,5 %) | 6 925,57 → 5 504,90 (−20,5 %) |
| **ASIC**: công suất (mW) | 0,368 → 0,362 (−1,4 %) | 0,324 → 0,257 (**−20,4 %**) |
| Vòng được nhận | sim-r1, asic-r1 | sim-r1, fpga-r1, asic-r1 |

- **Sim:** như lần 25/11, agent phủ đủ bin ngay vòng 1, vòng 2 không còn gì để sửa.
- **FPGA CG r1** (`build.tcl`, `sources.f`) được nhận theo luật "bản tốt nhất chưa đạt sign-off": baseline latch có
  TIMING-14, vòng này hết critical và WNS dương. Nó cũng thắng theo luật thường (P động −21,3 %, LUT+FF +1,4 % < 3 %).
  Số đo gần trùng lần 25/11 (5,174 mW) và biến thể `ce` viết tay (5,19 mW). r2 (`build.tcl`) tăng WNS nhưng P động
  +12 %, bị loại.
- **FPGA NoCG:** r1 (`build.tcl`) gần như không đổi gì; r2 (`NoCG_alu_fpga.v`) giảm 7 ô LUT+FF nhưng WNS 0,588 →
  0,456 ns, bị loại. Giống lần 25/11.
- **ASIC NoCG:** r1 trùng từng số với lần 25/11. r2 giảm cell −4,2 % và die −15 % nhưng công suất +1,5 % (ngưỡng 1 %), bị loại.
- **ASIC CG:** r1 được nhận. r2 giảm die −16 % nhưng công suất +2,7 %, bị loại. Lần 25/11 CG nhận cả 2 vòng ASIC
  (cell −8,2 %, die −27,5 %); lần này agent đề xuất khác. Công cụ tất định, agent thì không.
- Mọi vòng ASIC, kể cả vòng bị loại, đều qua gate sign-off: lý do loại chỉ là "kém hơn", không có vòng nào hỏng gate.

## 10. Mẫu flow dùng chung: thêm một khối RTL thứ hai (26/11/2026)

Mục tiêu: một thiết kế mới chỉ cần đưa RTL và testbench vào là cả ba nhánh chạy được, không sửa mã điều phối. Code của
hai demo ALU gộp thành thư viện `flow/` ở gốc repo; mỗi project chỉ còn khai báo `project.py`. Lệnh chung cho mọi
project:

```bash
python flow/run.py projects/<project> baseline        # hoặc agent --rounds N, sweep
```

### Hai mức dùng mẫu

| | ALU NoCG / CG | ASCON (`projects/ascon-core/`) |
|---|---|---|
| Nguồn của project | RTL, tb, `project.py` + phần mở rộng: vỏ board Arty, tb GLS riêng, functional coverage, ba biến thể clock gating FPGA | Chỉ RTL (`ascon_core.sv`, `ascon_pkg.svh`), tb, `project.py` (khoảng 20 dòng, `flow/new.py` tạo khung) |
| Flow tự sinh | | `run_sim.sh`, `config.mk`, SDC, `build.tcl` Vivado out-of-context, script công suất SAIF và GLS |
| FPGA | Có vỏ board, ra bitstream | Out-of-context (không bitstream); GLS chạy chính tb của bước Sim trên netlist sau route |
| PASS/FAIL | Dòng `RESULT: PASS` | Regex khai báo trong `project.py` (`[PASS] ...`, không có `[FAIL]`) |
| Lint | `strict`: `-Wall` phải sạch | `errors`: chặn lỗi, ghi 12 cảnh báo |

ASCON là lõi Ascon-AEAD128 giải mã và kiểm tag (bus 32 bit, một vòng mỗi clock) của người dùng, chưa từng chạy qua CHIA.
Những gì phải sửa để đưa vào mẫu cũng chính là quy ước cho thiết kế sau:

- tb tự in kết quả nhận diện được: dòng `RESULT:` hoặc regex pass/fail;
- tb chỉ đọc cổng của DUT. tb gốc đọc `fsm_q` bên trong, nên GLS trên netlist sẽ hỏng; giờ tb chờ `auth_valid`;
- tb tự `include` kiểu và hằng nó dùng: tách `ascon_pkg.svh` khỏi file RTL, logic RTL giữ nguyên;
- giữ reset ≥ 100 ns trước kích thích đầu tiên. GSR của netlist Xilinx (`glbl.v`) khoá mọi flop trong 100 ns đầu; tb
  gốc nạp khoá từ khoảng 45 ns nên GLS FPGA treo. Sửa ở tb theo cách Vivado tự làm, không sửa `glbl.v`;
- một clock; SystemVerilog mà Verilator 5.022 và Yosys 0.68 đọc được.

### Kết quả ASCON (chạy thật trên cluster)

Baseline `baseline-20261126-213109`, chạy lại `baseline-20261126-223426` ra trùng từng số. `KẾT QUẢ: OK` cả hai lần.

| Bước | Kết quả |
|---|---|
| Sim | PASS; lint 12 cảnh báo, 0 lỗi; coverage line 25/35, toggle 3 007/3 438, branch 44/60 |
| FPGA Artix-7 100T, 100 MHz | LUT 1 592, FF 473; WNS 1,292 / WHS 0,217 ns (Fmax ước lượng 114,8 MHz); methodology 0 critical; SAIF khớp 66 % net, P động 23,4 mW; GLS PASS |
| ASIC sky130hd, 10 ns | Cell 48 859 µm² (util 44,7 %), die 111 934 µm², 5 464 cell, 479 flop; setup/hold WS 2,626/0,474 ns (tt), 2,088/0,430 ns (tt + OCV ±10 %); P 9,96 mW; DRC router và KLayout 0, antenna 0, LVS khớp; GLS PASS |
| Thời gian | Route khoảng 10 phút, cả lần chạy 13,3 phút; DRC ‖ LVS ‖ STA ‖ GLS song song 41,7 s (lần lượt 72,4 s) |

Agent `--rounds 1` (`agent-20261126-215007`, `KẾT QUẢ: OK`), cùng luật nhận/loại như ALU:

| Vòng | Agent sửa | Kết quả |
|---|---|---|
| sim-r1 | thêm kích thích vào tb | **NHẬN**: line 25 → 34/35, toggle 3 007 → 3 383, branch 44 → 57/60 |
| fpga-r1 | `build.tcl` | LOẠI: WNS 1,292 → 0,71 ns, P động 23,4 → 30,5 mW |
| asic-r1 | `config.mk` | LOẠI: die −15 %, P −1,6 %, nhưng cell 48 859 → 51 204 µm² (+4,8 %); route 1 211 s |

Vòng ASIC dùng tb mới của sim-r1 và GLS vẫn PASS: tb do agent viết chạy đúng trên netlist. Project ASCON không có
functional coverage, nên luật Sim tự chuyển sang line/toggle/branch.

### Trang status dùng chung

Trang status (`flow/status.py`) chỉ đọc `project.py` và kết quả chạy: số ô Flow và cột Results theo các bước bật (1–3),
nhãn công cụ và dòng thông tin (DUT, clock, part, platform) lấy từ khai báo, dòng không áp dụng tự ẩn (ICG, công suất
instance). Project tự thêm số liệu riêng qua `sim.metrics` (regex trên log mô phỏng, chỉ để xem; ASCON đếm dòng
`[PASS]`). Đã xem trên cluster với ASCON; trường hợp chỉ Sim và Sim + ASIC mới thử trong sandbox.

### Kết luận

- Cả ba thiết kế (ALU NoCG, ALU CG, ASCON) chạy trên cùng một bộ điều phối `flow/`. Thiết kế mới theo năm quy ước trên
  chỉ cần RTL, tb và `project.py`; muốn thêm board, coverage riêng hay biến thể thì khai báo thêm, không sửa `flow/`.
- Flow phủ đủ các bước RTL → GDS và RTL → netlist FPGA, mỗi bước có gate sign-off cơ bản: lint, tb tự kiểm, coverage,
  timing, methodology, DRC, antenna, LVS, STA sau route có SPEF, GLS. Kết quả tất định.
- Chưa phải sign-off tape-out: STA một corner, GLS không SDF, chưa có LEC, công suất ASIC chưa theo hoạt động thật, một
  clock (mục 7).
- Chưa làm: SoC Chipyard (`sim.kind = "chipyard"`), nhiều clock.

