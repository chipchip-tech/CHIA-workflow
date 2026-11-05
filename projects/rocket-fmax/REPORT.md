# Báo cáo: tối ưu Fmax RocketTile bằng loop CHIA (Yosys + OpenSTA sky130hd)

Giai đoạn từ khi bắt đầu lại từ đầu (29/10/2026) tới 31/10/2026. Báo cáo chỉ lấy các số đo bằng Yosys + OpenSTA. Các
đợt `fmax-r4` và `fmax-r5` nhận hay loại vòng theo Vivado post-route, nên chỉ nhắc tới khi chúng để lại thay đổi
dùng tiếp.

## 1. Mục tiêu và thiết lập

- **Thiết kế:** `RocketTile` của Chipyard `RocketConfig`, gồm core Rocket, FPU, icache, dcache và PTW.
- **Mục tiêu:** Fmax > 100 MHz ở chu kỳ đặt 10 ns, trên thư viện `sky130_fd_sc_hd`.
- **Flow đo:**
  - Yosys `synth -flatten` → `dfflibmap` → `abc -D <period> -constr`, rồi OpenSTA.
  - Fmax = 1000 / (chu kỳ − worst slack).
  - SRAM (`*_ext`) là hộp đen, không tính timing.
- **Loop (theo example `timing_opt` của CHIA):** mỗi vòng là một phiên Claude Code (Sonnet, effort medium). Agent:
  - đọc báo cáo OpenSTA;
  - sửa Chisel trong `rocket-chip`;
  - tự A/B từng module bằng tool `synth_ab`;
  - được phép thêm tầng pipeline.

  Sau phiên agent, loop tự kiểm lại:
  1. Kiểm phạm vi sửa.
  2. Build `RocketConfig`. Nếu lỗi compile, một agent khác sửa build.
  3. Yosys + OpenSTA cả tile.
  4. Gate: riscv-tests `rv64ui/um/ua/uf/ud-p-*` cùng 8 benchmark (có `mm`, `spmv`), chạy trên Verilator. Nếu test
     fail, một agent debug được gọi.
- **Điều kiện nhận một vòng:**
  - mọi test pass;
  - `minstret` của mọi benchmark bằng baseline;
  - chi phí cycle (trung bình nhân `mcycle / mcycle baseline`) tăng ≤ 3 % so với baseline;
  - hiệu năng = Fmax / chi phí cycle tăng > 1 % so với bản tốt nhất.

## 2. Kết quả

| Đợt | Ngày | Vòng nhận | Fmax (MHz) | Worst slack (ns) | Chi phí cycle | Đường găng sau đợt |
|---|---|---|---|---|---|---|
| baseline | 29/10 | | 47,13 | | 1,000 | `core.div` |
| `chia-fmax-r1` | 29/10 | 4/5 | 47,13 → 59,80 → 72,94 → 79,42 → **81,91** | −2,21 | +0,405 % | `dcache.s2_req_addr` → `core.mem_reg_wdata` |
| `chia-fmax-r2` | 29/10 | 1/5 | → **84,37** | −1,85 | +0,405 % | `fpuOpt.dfma` (mulAdd) |
| `chia-fmax-r3b` | 30/10 | 1/5 | → **90,57** | −1,04 | +0,405 % | `core.div.divisor` → `core.div.remainder` |
| (`fmax-r4`, nhận theo Vivado) | 30/10 | 1/5 | — | — | +0,405 % | thêm sửa ALU, xem mục 3 |
| `fmax-y1` (dừng ở vòng 3) | 31/10 | 0/3 | start **92,36** | −0,827 | +0,405 % | `core.div.divisor[4]` → `core.div.remainder[128]` |
| `fmax-y2` (dừng ở vòng 1) | 01/11 | — | | | | Yosys treo > 30 phút trên bản sửa dang dở của agent |
| `fmax-y3` | 01/11 | 0/5 | 92,36 | −0,827 | +0,405 % | như trên |
| `fmax-y4` (dừng ở vòng 3) | 02/11 | 0/3 | 92,36 | −0,827 | +0,405 % | như trên; tốt nhất −0,786 ns (+0,4 %) |

Tổng cộng: **47,13 → 92,36 MHz (+96 %)**. Chi phí cycle chỉ tăng +0,405 %, nên hiệu năng (Fmax / chi phí cycle) đi
từ 47,13 lên 91,99 (+95 %). Mọi điểm được nhận đều pass riscv-tests và 8 benchmark. Diện tích ở `fmax-y1` là
1 067 543 µm². Còn thiếu khoảng 0,83 ns (8 %) để đạt 100 MHz.

### Chi tiết từng vòng

`chia-fmax-r1`: xuất phát từ RocketConfig gốc.

| Vòng | Kết quả | Fmax | Hiệu năng | Đường găng sau vòng |
|---|---|---|---|---|
| 1 | nhận | 59,80 | +26,9 % | `fpuOpt.dfma` (mulAdd) |
| 2 | nhận | 72,94 | +21,5 % (cycle +0,405 %) | `dcache.s2_req_addr` → `core.mem_reg_wdata` |
| 3 | nhận | 79,42 | +8,9 % | `fpuOpt.ifpu` |
| 4 | nhận | 81,91 | +3,1 % | `dcache.s2_req_addr` → `core.mem_reg_wdata` |
| 5 | loại | | | agent bỏ cuộc sau 1 lượt, không gọi tool |

`chia-fmax-r2`: xuất phát từ bản tốt nhất của r1.

| Vòng | Kết quả | Ghi chú |
|---|---|---|
| 1 | nhận, 84,37 MHz (+3,0 %) | đường găng chuyển sang `fpuOpt.dfma.in_in2` → `mulAddResult_pph_pipe_b` |
| 2–3 | loại | netlist trùng vòng 1 (WNS/TNS trùng tới 4 chữ số) |
| 4 | loại | agent không sửa gì |
| 5 | loại | phiên retry thử cắt `dfma` thành 4 lát; A/B cả tile −2,05 ns nên agent tự hoàn tác |

`chia-fmax-r3b`: xuất phát từ bản tốt nhất của r2.

| Vòng | Kết quả | Ghi chú |
|---|---|---|
| 3 | nhận, 90,57 MHz (+7,35 %) | đường găng về `core.div` |
| 4 | loại, 80,09 MHz | đường găng `dcache` → `core.mem_reg_wdata` |
| 1, 2, 5 | loại | agent không sửa gì |

`fmax-y1`: xuất phát từ `best.patch` của `fmax-r4` (r3b + sửa ALU), có cache build và gate.

| Vòng | Kết quả | Phút | Ghi chú |
|---|---|---|---|
| start | 92,36 MHz, slack −0,827 ns, TNS −682,5 ns | | gate pass |
| 1 | loại | 18,7 | agent không thay đổi gì |
| 2 | loại | 5,8 | agent không thay đổi gì |
| 3 | loại ở bước synth | 26,0 (synth 2,6) | 91,93 MHz < ngưỡng 92,91 MHz nên không chạy gate. TNS giảm mạnh (−682,5 → −56,7 ns), tức sửa của agent rút ngắn hầu hết đường của họ `core.div`, nhưng đường găng chuyển sang FPU `fpuOpt.sfma.in_in1` → `fpuOpt.dfma.fma.mulAddResult_p0` |

Đợt dừng tay sau vòng 3 vì sắp hết hạn mức tuần của gói Claude.

## 3. Những thay đổi agent đã làm

Mọi chỉnh sửa Chisel do agent trong loop tự viết và tự kiểm bằng `synth_ab`. Bảng dưới tóm tắt theo khối. Với r1–r3b, cột
"Hướng sửa" được **suy ra từ đường găng trước và sau mỗi vòng nhận**, chưa đối chiếu với diff. Cần kiểm lại bằng
`patches/best.patch` của từng đợt, trên máy chạy loop. Riêng dòng ALU được lấy từ log của agent.

| Khối | Đường găng trước | Hướng sửa | Đợt / vòng |
|---|---|---|---|
| Bộ chia `core.div` (MulDiv) | `divisor` → `remainder` (baseline 47 MHz) | rút ngắn đường cập nhật `remainder` của bộ chia lặp | r1 vòng 1, r3b vòng 3 |
| FPU FMA `fpuOpt.dfma`/`sfma` | mulAdd (nhân mảng + cộng) | tách thêm tầng thanh ghi trong `MulAddRecFN` (`mulAddResult_pph_pipe_b`), latency FMA theo sau | r1 vòng 2, r2 vòng 1 |
| Load → store data (`dcache.s2_req_addr` → `core.mem_reg_wdata`) | bypass/forwarding vào dữ liệu store | đổi cấu trúc bypass. Vòng này là nơi phát sinh chi phí cycle +0,405 % (thêm trễ) | r1 vòng 2 và 4 |
| FPU int↔fp `fpuOpt.ifpu` | chuyển đổi số nguyên ↔ số thực | rút ngắn logic chuyển đổi | r1 vòng 3 |
| ALU (đếm trailing zero) | 32–34 mức logic qua `PriorityEncoderOH - 1` | thay bằng prefix-OR. Đo `synth_design` được +1,909 ns. Đây là phần r4 thêm vào điểm xuất phát của `fmax-y1` | r4 vòng 1 |

Các hướng đã thử nhưng bị loại:
- `fastLoadWord = false` (−0,635 ns);
- cắt `dfma` 4 lát (−2,05 ns trên cả tile);
- các sửa tốn +1,9 % đến +4,1 % cycle ở r4/r5;
- r3b vòng 4 (đường găng về lại `dcache` → `core`).

## 4. Cải tiến loop trong giai đoạn này

- **Phiên retry khi agent không sửa gì** (`--empty-retries`). Prompt retry liệt kê những sửa đổi đã đo rồi hoàn tác.
- **Loại ngay** vòng có Verilog trùng một bản đã đo, và vòng có Fmax không thể vượt bản tốt nhất kể cả khi cycle
  không đổi. Vòng loại sớm không chạy gate, tiết kiệm khoảng 19 phút (`fmax-y1` vòng 3).
- **`synth_ab` báo slack theo họ endpoint** của cả parent và child. Worst slack một mình che mất cải thiện của họ khác
  (r4 vòng 4).
- **`--history-from`:** lịch sử các vòng đã loại được đưa sang đợt sau (r5 vòng 1 từng lặp lại một sửa đã bị loại).
- **Cache CHIA** cho build, gate và synth của baseline và điểm xuất phát. Một đợt mới không tốn lại khoảng 25 phút đó.

## 5. Nhận xét và hướng tiếp

- Ba rào cản còn lại sát nhau quanh −0,8 đến −0,9 ns:
  - bộ chia `core.div`;
  - FMA của FPU (`sfma` → `dfma` mulAdd);
  - store data `dcache` → `core.mem_reg_wdata`.

  Sửa một họ thường làm họ khác thành đường găng (`fmax-y1` vòng 3). Muốn qua 100 MHz cần một vòng xử lý cả hai họ
  `div` và FMA.
- Synth cả tile (flatten, một lần ABC) xê dịch 1–2 ns khi sửa một khối khác. Vì vậy nhận hay loại theo một lần synth
  vẫn có nhiễu ở mức vài phần trăm.
- Sonnet ở effort medium hay bỏ cuộc mà không sửa gì: 2/3 vòng ở `fmax-y1`, 3/5 vòng ở r3b.
- Bước tiếp, khi có lại hạn mức:

  ```bash
  python rocket_fmax.py --start-patch <best.patch fmax-r4> --history-from ~/rocket-fmax-v2/fmax-y1 --run-name fmax-y2
  ```

  Lệnh này để agent biết vòng 3 đã đẩy đường găng sang `sfma` → `dfma`. Khi Fmax vượt 100 MHz thì hạ `--period-ns`.

## 6. Sau `fmax-y1`: `fmax-y2` … `fmax-y4` (01–02/11/2026)

- `fmax-y2`: vòng 1 agent hết giờ giữa chừng, loop vẫn build bản sửa dang dở của nó (5 file) và Yosys chạy hơn 30 phút
  (bình thường 3–5 phút). Đã thêm giới hạn thời gian Yosys (`--synth-timeout`, mặc định 15 phút; `synth_ab` cũng vậy).
- `fmax-y3` (5 vòng) và `fmax-y4` (3 vòng): không vòng nào nhận. Agent đã thử: cây carry-save / Booth radix-4 cho
  `MulDiv`, CSA và tách 4 phần cho bộ nhân FMA, dời bộ cộng FMA ra sau thanh ghi (−16 ns), trễ thêm 1 chu kỳ cho kết
  quả `divSqrt`, giảm `mulUnroll` xuống 4. Tốt nhất: cây CSA cho `MulDiv` (y4 vòng 1), −0,827 → −0,786 ns (+0,4 %),
  dưới ngưỡng 1 %.
- Lỗi của loop đã sửa: ở vòng không có thay đổi, `history.md` ghi chính patch xuất phát vào mục "Patch đã bị loại",
  tức bảo agent vòng sau tránh bản đang tốt nhất (cả 5 vòng của y3).
- Dữ liệu cho thấy dao động của flow `v2` (flatten): cùng một RTL cho kết quả y hệt (patch `mulUnroll ≤ 4` đo 4 lần đều
  −0,9221 ns), nhưng sửa `MulDiv` làm họ `regfile_ext.Memory` (không bị sửa) đi từ −0,658 lên −0,080 ns ở một lần đo
  và xuống ở lần khác. Biên độ 0,2–0,6 ns lớn hơn ngưỡng nhận 1 % (~0,1 ns).

**Hướng tiếp (người dùng duyệt 02/11/2026):** tách pha theo `docs/workflow-phases.md`. Pha sim cho agent thêm tầng
pipeline và chỉ đo chi phí cycle, ra danh sách ứng viên; pha asic dùng synth phân cấp (`v3`) để A/B không bị nhiễu,
nhận thêm theo tổng slack các họ xấu nhất (điểm họ), và `synth_ab` không đo lại Verilog đã đo. Xem README.

## 7. Kết thúc project (05/11/2026)

Project dừng ở đây theo quyết định của người dùng. Bản cuối là `asic-7` (`~/rocket-fmax-v2/asic-7/patches/best.patch`,
sha `b93d772b45d8`, 2345 dòng, 12 file).

### Fmax đạt được khi synth (Yosys + OpenSTA sky130hd, chỉ `RocketTile`)

| Điểm | Flow | Chu kỳ đặt | Fmax (MHz) | Chi phí cycle | Ghi chú |
|---|---|---|---|---|---|
| baseline | `v2` | 10 ns | 47,13 | 1,000 | |
| `fmax-r4` | `v2` | 10 ns | 92,36 | +0,405 % | tốt nhất của flow phẳng |
| `v3-start` (= `fmax-r4`) | `v3` | 10 ns | 85,01 | +0,405 % | flow phân cấp, điểm xuất phát các đợt `asic-*` |
| `asic-4` | `v3` | 10 ns | 93,88 | +2,685 % | |
| `asic-6` | `v3` | 10 ns | **109,17** | −0,006 % | đạt mục tiêu > 100 MHz; qua `func_check` |
| `asic-7` | `v3` | 6 ns | **140,01** | +0,325 % | worst slack −1,143 ns, diện tích 1 248 905 µm² |

So với baseline 47,13 MHz, `asic-7` nhanh gấp khoảng **2,97 lần** ở mức synth. Hai con số này đo ở chu kỳ đặt khác nhau
(10 ns và 6 ns), và ABC chạy nhanh hơn khi bị ép chặt hơn: cùng RTL `asic-6` cho 109,17 MHz ở 10 ns và 113,53 MHz ở
6 ns. Phần tăng do agent làm trong `asic-7` là 113,53 → 140,01 MHz (+23,3 %).

Giới hạn của các số này:
- Chỉ là synth: không place-and-route, không RC dây, không chèn buffer cho net fanout lớn, SRAM là hộp đen.
- `asic-7` **chưa kiểm chức năng ngoài gate** (riscv-tests `rv64ui/um/ua/uf/ud` và 8 benchmark). Các rủi ro đã liệt kê
  trong HANDOFF mục 12 (C.EBREAK ở U-mode, RVC, Zbb, load MMIO, FPU) chưa được test.

### Vì sao không tiếp tục trên Vivado (FPGA)

- **Thay đổi của agent nhắm ASIC, không chuyển sang FPGA.** Out-of-context, `asic-6` cho 63,27 MHz, gần như bằng
  `fmax-r4` (63,61 MHz), dù ở synth ASIC tăng từ 92,36 lên 109,17 MHz. Các sửa kiểu carry-save, cây Dadda, mux one-hot
  thay cho phép cộng/nhân có sẵn làm mất carry chain và DSP của FPGA: tile `asic-6` dùng LUT +22–24 %, DSP 25 → 0.
- **Trong cả SoC trên Arty A7, lợi ích nhỏ.** `soc-asic6-f70` (Vivado post-route, 70 MHz): baseline 55,50 →
  `asic-6` 59,91 MHz (+7,9 %), trong khi synth ASIC của tile
  tăng từ 85,01 (`v3-start`) lên 109,17 MHz (+28 %). Đường găng chuyển sang `frontend/icache` → `frontend/fq`,
  tức là FPGA có đường găng riêng; muốn tăng tiếp phải tối ưu riêng cho FPGA, ngoài mục tiêu project.
- **Mỗi lần đo rất đắt.** Một điểm bitstream cả SoC mất khoảng 12–24 phút Vivado sau 1–2 giờ build, và kết quả P&R dao
  động cỡ 1 % (`fmax-r5`), lớn bằng ngưỡng nhận một vòng. Không hợp để chạy loop agent.

### Vì sao không tiếp tục với cả SoC (`soc_synth.py`)

`soc-synth-5` (05/11/2026, chạy thật, sau khi sửa clock cho mọi chân register/SRAM và bỏ qua cổng reset):

| Điểm | Fmax SoC (MHz) | Worst slack (6 ns) | Đường chặn | Diện tích (µm², không SRAM) |
|---|---|---|---|---|
| baseline | 54,81 | −12,244 | FPU `dfma` `mulAddResult_pipe_b` (trong tile) | 2 351 502 |
| `asic-7` | 63,80 (+16,4 %) | −9,675 | L2 `InclusiveCache` `sinkA.putbuffer`, `mshrs_6` | 2 433 389 (+3,5 %) |

- **Sau `asic-7`, tile không còn chặn SoC.** Mọi họ đường xấu nhất của `asic-7` nằm trong L2 (uncore), ngoài
  `RocketTile`. Tăng Fmax SoC tiếp phải tối ưu `InclusiveCache` và bus, tức là một project khác.
- **Số SoC chưa đủ tin để báo cáo như Fmax thật.** Yosys không chèn buffer cho net fanout lớn, và các đường L2 xấu
  nhất đều đi tới hàng nghìn bit (`putbuffer.data_ext.Memory`, 2863 bit), nên trễ bị tính quá. Thời gian truy cập
  SRAM giả (0 / 1,5 / 3 ns) không làm đổi kết quả. Script còn lỗi báo cáo: các cột slack theo vùng trống, "đường xấu
  nhất" ghi nhầm một đường reset vào `RESET_B`, OpenSTA vẫn kiểm recovery ở chân reset bất đồng bộ. Muốn có số tin
  được cần `repair_design` hoặc place-and-route bằng OpenROAD cho cả SoC, tốn công hơn nhiều so với giá trị thêm.
- Kết luận đủ cho câu hỏi ban đầu: tile nhanh hơn có làm SoC nhanh hơn (+16,4 % ở synth, +7,9 % trên FPGA với
  `asic-6`), nhưng giới hạn kế tiếp nằm ngoài tile.

### Việc còn bỏ ngỏ (nếu mở lại)

1. Chạy `func_check.py` cho `asic-7` trước khi dùng patch này ở bất cứ đâu.
2. Place-and-route `RocketTile` bằng OpenROAD (ORFS sky130hd) cho baseline / `asic-6` / `asic-7`.
3. Nếu quan tâm SoC: sửa báo cáo vùng trong `soc_synth.py`, thêm `repair_design`, rồi tối ưu L2.
