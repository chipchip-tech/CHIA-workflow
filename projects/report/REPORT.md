# Brief làm slide báo cáo: Demo 1 (ALU 8 bit, Sim → FPGA → ASIC với agent trong CHIA)

File này dành cho **session slide**. Đây là nguồn duy nhất session đó cần đọc để sửa hoặc làm slide: khán giả, quy ước,
bộ slide hiện có, số liệu đang dùng và cách build. Kết quả kỹ thuật đầy đủ nằm ở `../demo-alu/REPORT.md` và `docs/demo-task.md`,
do session làm việc cập nhật.

> Chuyển từ `projects/demo-alu/report/` sang `projects/report/` ngày 28/11/2026 (theo yêu cầu người dùng).

 Phạm vi và cách phối hợp

- Hai session cùng làm trên nhánh `demo`, chia theo thư mục:
  - **Session slide** chỉ sửa `projects/report/**`.
  - **Session làm việc** sửa mọi thứ khác (code, `../demo-alu/REPORT.md`, `docs/`, `CLAUDE.md`) và không đụng `report/`.
- Luôn `git pull` (hoặc `git fetch origin demo && git merge --no-edit FETCH_HEAD`) trước khi commit; không rebase, không force-push.
- Đồng bộ số liệu: khi session làm việc có kết quả mới, session slide đọc `git log` + `../demo-alu/REPORT.md` + `docs/demo-task.md`,
  sửa **mục 4 của file này trước**, rồi mới sửa slide. Ghi commit đã đồng bộ ở dòng "Đồng bộ tới".
- Người dùng không `git push` được; session slide commit + push lên `demo` và gửi file `.pptx` cho người dùng.

## 2. Khán giả và quy ước (chốt qua phỏng vấn 22–23/11/2026)

- **Người nghe:** ThS. Phạm Thế Hùng (giảng viên hướng dẫn); người trình bày: Phan Thành Nhân. Trình bày trực tiếp và gửi file sau.
- Thầy đã nắm thiết kế số, Verilog, flow FPGA/ASIC, clock gating. **Cần giải thích:** CHIA, Ray, Docker.
- Slide **tiếng Anh**, 4 file riêng. Tài liệu repo vẫn viết tiếng Việt.
- **Không dùng speaker notes**: mọi thứ trình bày phải nằm trên slide.
- Chữ nội dung **≥ 14 pt** (chỉ số trang và chân trang 10 pt).
- Mỗi slide một **dòng kết luận in đậm ở cuối** ("→ …").
- Mỗi bước **đúng 1 dòng lệnh chính** (ô "Run").
- Bố cục hai cột hiện tại, gọn; ít chữ, ít bảng; dữ liệu dùng biểu đồ cột + bảng nhỏ.
- Chỉ giữ vài tên file quan trọng: `config.mk`, `build.tcl`, testbench, `fpga_top`. Tên khác đổi thành mô tả
  ("shared run folder", "build task").
- Không có mục "lỗi đã gặp". Phần remote là **kế hoạch**, gắn nhãn "PLAN · NOT TESTED".
- **Bằng chứng gốc (chốt 27/11/2026):** số liệu chính vẫn ở biểu đồ/bảng; trên slide kết quả có ô "From …" trích nguyên
  văn 1–2 dòng từ log/report thật (Courier New, `H.evidence`), không chụp ảnh `.rpt`/`.log`. Ảnh chụp chỉ dùng cho thứ
  vốn là hình (layout, trang status, timeline). Đang dùng: dòng `RESULT:` Sim NoCG baseline, `FCOV: hit=57 total=57` CG
  sim-r1, câu chẩn đoán trong summary của agent CG fpga-r1, `Netlists match` + `RESULT:` GLS NoCG baseline; ASCON: câu "If it has a bug ... [FAIL] ..." trong summary
  sim-r1 (agent tự viết mô hình tham chiếu Ascon-AEAD128 + 10 corner case), `Netlists match` LVS baseline (12,4 s); FPGA ASCON: dòng bảng tóm tắt `methodology.rpt` (TIMING-18 Warning 120, không có rule Critical).
- Style: nền trắng, Times New Roman (code Courier New), thang xám, `LAYOUT_WIDE`.

- **Chốt 28/11/2026 (thay các quy ước style/cỡ chữ cũ ở trên):** slide vừa để trình bày vừa dùng như tài liệu làm lại từng
  bước (người không muốn đọc md). Style "Paper and Instruments" theo Impeccable: nền trắng, Calibri (lệnh/log/đường dẫn
  Courier New), nhãn mục mono chữ hoa; màu có nghĩa: vàng đậm `C98F00` chỉ cho chấm bước hiện tại (không làm màu chữ), xanh
  patina `1E6E6A` = Done when / kết quả nhận, đỏ son `B33A1E` chỉ cho If it fails; biểu đồ xám + patina; bảng chỉ kẻ ngang.
  Chữ nhỏ nhất 12 pt. Không eyebrow trên tiêu đề, không "→" ở dòng kết luận (dùng "->" trong chữ), dòng kết luận tuỳ slide.
- **Slide "Step N"** (`H.step`): dải tiến độ, Where, lệnh copy được (cả bề ngang), What it does, Done when (dòng log thật),
  Output hoặc "Other distros · not tested", If it fails. Lệnh lấy đúng từ `docs/reinstall.md`, `docs/setup-bazzite.md`,
  `flow/README.md`. Máy người khác có thể dùng distro khác: ghi yêu cầu chung, lệnh Bazzite đã kiểm chứng, lệnh Ubuntu/Fedora
  gắn "not tested". Vivado chỉ là một dòng trong Prerequisites.
- **Làm từng deck một** (Setup -> Sim -> FPGA -> ASIC), mỗi deck gửi duyệt trước khi sang deck sau; build riêng bằng `ONLY=setup`.

## 3. Bộ slide hiện có (`guides/`, sinh bởi `src/build_guides.js`)

| File | Slide |
|---|---|
| `0_Setup.pptx` (21, khung Step từ 28/11) | Cover · How CHIA Runs Work · Example task · Prerequisites · The Ten Steps · Step 1-7 · How the Tools Reach the Workers · Step 8-10 · Remote (PLAN, 2) · After Setup: Three Files · Proof: ASCON · Setup Status |
| `0_Setup.pptx` cũ (15) | Cover · How CHIA Runs Work · Example: One Simulation Task Through CHIA · Machine and Software Used · Setup Process · Steps 1-2 Host and Python · Tools Live on the Host, Workers Mount Them · Steps 3-4 Installing the Tools · Step 5 The Block Cluster · Bring-up and Smoke Test · Remote Workers: Architecture (PLAN) · Remote Workers: Planned Steps (PLAN) · Shared Flow: A New Design Needs Three Files · Proof: ASCON Through the Same Flow · Setup Status |
| `1_Sim.pptx` (8; slide ASCON đặt cuối, sau Takeaways) | Cover · Where Sim Fits · Running the Baseline Sim on CHIA · Agent: Improve the Testbench · NoCG vs CG: Same Test, Different Clocking · What the Agent Added · Takeaways and Limits |
| `2_FPGA.pptx` (9) | Cover · Where FPGA Fits · Running the FPGA Build on CHIA · Agent: Tune the FPGA Build · Baseline: Clock Gating Hurts on FPGA (3 biểu đồ) · Three Ways to Gate a Clock on FPGA · Agent Rounds: CG r1 Accepted · Second Design: ASCON, Out-of-Context · Takeaways and Limits |
| `3_ASIC.pptx` (9; slide ASCON đặt cuối, chừa chỗ ảnh layout) | Cover · Where ASIC Fits · Running the ASIC Flow on CHIA · Agent: Tune config.mk · Baseline: Clock Gating Pays Off on ASIC · What the Agent Changed (biểu đồ %) · Layouts, Same Scale · Takeaways and Limits |

Ảnh layout: `img/{NoCG,CG}_{baseline,agent}_layout.png`. `NoCG_agent` là của lần 21/11 (die 4 642 µm²), không khớp số chốt 26/11 (5 460 µm²) nên đã bỏ khỏi slide; `CG_agent` khớp (5 505 µm²). Cần xin người dùng ảnh NoCG agent 26/11 và layout ASCON.

- **ASCON tách khỏi ALU (chốt 27/11/2026):** các slide ALU của Sim/FPGA/ASIC chỉ có số ALU; ASCON nằm ở slide
  "Second Design · …" ở cuối mỗi deck, sau Takeaways.

## 4. Số liệu đang dùng trong slide

**Đồng bộ tới:** commit `48d80e4` (27/11/2026). Nguồn: `../demo-alu/REPORT.md` mục 7, 9, 10; `flow/README.md` mục "Hướng dẫn:
đưa một khối RTL mới vào flow". Số ALU chốt là bảng "Agent với đủ gate sign-off (chạy thật 26/11/2026)".

**Lệnh chạy chung (mọi project):** `python flow/run.py projects/<project> baseline` (hoặc `agent --rounds N`, `sweep`).
`run_demo.py` đã xoá.

**Baseline ALU** (Sim, ASIC: 10 ns; FPGA: 188,7 MHz = 5,3 ns, công suất động đo bằng SAIF sau P&R, gửi phép 30 %)

| Bước | Chỉ số | NoCG | CG |
|---|---|---|---|
| Sim | Kết quả; lint `-Wall` | PASS; 0 cảnh báo | PASS; 0 cảnh báo |
| | Bin functional | 51/55 (thiếu 4 bin reset) | 51/57 (thiếu 4 bin reset, 2 bin `en` khi clock cao) |
| | Line / toggle / branch | 15/16, 62/62, 8/8 | 14/15, 68/69, 8/10 |
| FPGA | WNS (ns) | 0,588 | 0 (latch mượn thời gian) |
| | Methodology critical | 0 | 2 (TIMING-14, LUT trên cây clock) |
| | P động SAIF (mW) | 5,575 | 6,602 |
| | LUT+FF | 280 | 283 |
| | GLS netlist sau route | PASS | PASS |
| ASIC | Cell / die (µm²) | 3 078 / 7 534 | 2 975 / 6 926 |
| | Setup / hold WS tt (ns) | 6,815 / 0,493 | 6,957 / 0,406 |
| | tt + OCV ±10 % (ns) | 6,477 / 0,447 | 6,789 / 0,350 |
| | Công suất (mW, OpenSTA mặc định) | 0,368 | 0,324 (−12 %) |
| | DRC router + KLayout, antenna, LVS, GLS | 0, 0, khớp, PASS | 0, 0, khớp, PASS |

**Fmax và cách gating trên FPGA** (CG, chỉ đổi file trong `fpga/`): NoCG 227,3 MHz; CG latch 128,2; bufgce 196,1; ce 232,6.
P động SAIF ở 188,7 MHz, gửi phép 10 / 30 / 70 / 100 %: NoCG 4,21 / 5,58 / 7,74 / 8,88; latch 6,19 / 6,60 / 9,17 / 10,42;
bufgce 8,50 / 9,36 / 10,25 / 11,67; ce 4,03 / 5,19 / 7,79 / 8,42 mW. Không cách nào tiết kiệm hơn NoCG; BUFGCE tốn thêm
3,64 mW ở mạng clock (96 % phần tăng), cả 139 flop chỉ 0,115 mW. ASIC thì ngược lại (CG −12 %).

**Agent ALU, đủ gate sign-off (26/11/2026, 2 vòng mỗi bước, cả hai `KẾT QUẢ: OK`)**

| | NoCG: baseline → agent | CG: baseline → agent |
|---|---|---|
| Sim: bin functional | 51/55 → 55/55 (r1) | 51/57 → 57/57, branch 8 → 10 (r1) |
| Sim: số phép so sánh | 6 272 → 6 277 | 6 272 → 6 305 |
| FPGA | 2 vòng loại, giữ baseline | r1 NHẬN, r2 loại |
| FPGA: WNS (ns) / methodology | 0,588 / 0 | 0 → 0,524 / 2 → 0 |
| FPGA: P động SAIF (mW) | 5,575 | 6,602 → 5,193 (−21,3 %) |
| FPGA: LUT+FF | 280 | 283 → 287 (+1,4 %) |
| ASIC: cell (µm²) | 3 077,95 → 3 042,92 (−1,1 %) | 2 975,35 → 2 865,25 (−3,7 %) |
| ASIC: die (µm²) | 7 534,24 → 5 459,73 (−27,5 %) | 6 925,57 → 5 504,90 (−20,5 %) |
| ASIC: công suất (mW) | 0,368 → 0,362 (−1,4 %) | 0,324 → 0,257 (−20,4 %) |
| Vòng nhận | sim-r1, asic-r1 | sim-r1, fpga-r1, asic-r1 |

- Sim: phủ đủ bin ngay r1 bằng khoảng 10–15 phép kiểm đặt đúng chỗ; r2 không còn gì để sửa.
- FPGA CG r1: agent chẩn đoán latch + TIMING-14, đổi `sources.f` sang biến thể `icg_fpga_ce.v` có sẵn, thêm
  `-gated_clock_conversion auto` và `Explore` vào `build.tcl`. r2 WNS tăng nhưng P động +12 %, loại.
- FPGA NoCG: r2 giảm 7 ô LUT+FF nhưng WNS 0,588 → 0,456, loại.
- ASIC: NoCG r2 cell −4,2 %, die −15 % nhưng P +1,5 %, loại; CG r2 die −16 % nhưng P +2,7 %, loại. Mọi vòng (kể cả bị
  loại) qua đủ gate sign-off; lý do loại chỉ là "kém hơn".
- Lần 25/11 CG nhận cả 2 vòng ASIC (cell −8,2 %, die −27,5 %, P −21,5 %): agent không tất định, công cụ thì tất định.

**Luật nhận (chốt trước khi chạy, 25/11):** Sim chỉ sửa tb; PASS, không coverage nào giảm, số bin tăng. FPGA ở 188,7 MHz,
sửa `build.tcl` + file riêng FPGA; WNS và P động SAIF không kém, LUT+FF kém ≤ 3 %; nếu bản tốt nhất chưa đạt sign-off thì
vòng đạt được nhận. ASIC chỉ sửa `config.mk`, luật 1 %, gate có LVS.

**Gate sign-off sau finish ASIC chạy song song:** DRC KLayout ‖ LVS ‖ STA ‖ GLS (ASCON: 41,7 s, lần lượt 72,4 s).

**ASCON** (`projects/ascon-core/`, lõi Ascon-AEAD128 giải mã + kiểm tag; chỉ RTL, tb, `project.py` khoảng 20 dòng)

| Bước | Baseline (chạy 2 lần trùng từng số) |
|---|---|
| Sim | PASS; lint 12 cảnh báo, 0 lỗi; line 25/35, toggle 3 007/3 438, branch 44/60 |
| FPGA out-of-context, Artix-7 100T, 100 MHz | LUT 1 592, FF 473; WNS 1,292 / WHS 0,217 ns (≈ 114,8 MHz); methodology 0; P động SAIF 23,4 mW; GLS (tb của Sim) PASS; không bitstream |
| ASIC sky130hd, 10 ns | cell 48 859 µm² (util 44,7 %), die 111 934 µm², 5 464 cell, 479 flop; setup/hold 2,626/0,474 (tt), 2,088/0,430 (OCV); P 9,96 mW; DRC 0, antenna 0, LVS khớp, GLS PASS |
| Thời gian | cả lần chạy 13,3 phút (route ≈ 10 phút) |

Agent `--rounds 1`: sim-r1 NHẬN (line 25 → 34/35, toggle → 3 383, branch → 57/60); fpga-r1 loại (WNS 1,292 → 0,71, P
23,4 → 30,5 mW); asic-r1 loại (die −15 %, P −1,6 %, cell +4,8 %). GLS của vòng ASIC chạy tb mới của agent, PASS.

**Mẫu flow `flow/`:** thiết kế mới cần RTL + tb + `project.py` (`flow/new.py` tạo khung); flow tự sinh `run_sim.sh`,
`config.mk`, SDC, `build.tcl` OOC, script SAIF và GLS. 5 quy ước tb: tự in PASS/FAIL (dòng `RESULT:` hoặc regex); chỉ đọc
cổng DUT; kiểu/hằng trong `.svh` riêng; reset ≥ 100 ns (GSR); một clock, SV mà Verilator 5.022 + Yosys 0.68 đọc được.
4 bước: tạo project, baseline, agent, dọn đĩa.

**Phạm vi / giới hạn (ghi đúng trên slide):** đủ các bước RTL → GDS và RTL → netlist FPGA với sign-off cơ bản; chưa phải
sign-off tape-out: STA một corner (tt + OCV thay ss/ff), GLS không SDF, chưa LEC, công suất ASIC chưa theo hoạt động
thật, một clock (chưa CDC/RDC). ALU nhỏ (~300 cell). Chưa nạp board; > 100 MHz chưa có MMCM. ASCON: tb có hướng, chưa có
mô hình tham chiếu; FPGA OOC chưa đặt I/O delay (TIMING-18). Setup chạy thật 14/11; remote/cloud chưa thử.

## 5. Build

```bash
# 1. pptxgenjs không cài sẵn toàn cục: cài vào scratchpad của session
cd <scratchpad> && npm install pptxgenjs
# 2. build 4 deck vào report/guides/
cd projects/report && NODE_PATH=<scratchpad>/node_modules node src/build_guides.js
# 3. vá lỗi <a:pPr> thừa của pptxgenjs (bắt buộc, từng file)
for f in guides/*.pptx; do python3 src/fix_pptx.py "$f"; done
# 4. kiểm tra: validate.py của skill pptx, rồi render xem bằng mắt
python3 <skill pptx>/scripts/office/validate.py guides/0_Setup.pptx
python3 <skill pptx>/scripts/office/soffice.py --headless --convert-to pdf --outdir <scratchpad> guides/0_Setup.pptx
pdftoppm -jpeg -r 60 <scratchpad>/0_Setup.pdf <scratchpad>/0_Setup && montage ...
```

- Chỉ kiểm được bằng bản render LibreOffice; chưa mở thử bằng PowerPoint.
- Lỗi đã gặp khi render: nhãn đè mũi tên, bảng đè dòng kết luận (dòng kết luận ở y 6.2–6.27), biểu đồ hiện nhãn
  "Series1" (dùng `catAxisHidden`), ô lệnh cách nội dung quá xa.
