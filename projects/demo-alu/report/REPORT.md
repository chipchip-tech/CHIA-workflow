# Brief làm slide báo cáo: Demo 1 (ALU 8 bit, Sim → FPGA → ASIC với agent trong CHIA)

File này dành cho **session slide**. Đây là nguồn duy nhất session đó cần đọc để sửa hoặc làm slide: khán giả, quy ước,
bộ slide hiện có, số liệu đang dùng và cách build. Kết quả kỹ thuật đầy đủ nằm ở `../REPORT.md` và `docs/demo-task.md`,
do session làm việc cập nhật.

## 1. Phạm vi và cách phối hợp

- Hai session cùng làm trên nhánh `demo`, chia theo thư mục:
  - **Session slide** chỉ sửa `projects/demo-alu/report/**`.
  - **Session làm việc** sửa mọi thứ khác (code, `../REPORT.md`, `docs/`, `CLAUDE.md`) và không đụng `report/`.
- Luôn `git pull` (hoặc `git fetch origin demo && git merge --no-edit FETCH_HEAD`) trước khi commit; không rebase, không force-push.
- Đồng bộ số liệu: khi session làm việc có kết quả mới, session slide đọc `git log` + `../REPORT.md` + `docs/demo-task.md`,
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
- Style: nền trắng, Times New Roman (code Courier New), thang xám, `LAYOUT_WIDE`.

## 3. Bộ slide hiện có (`guides/`, sinh bởi `src/build_guides.js`)

| File | Slide |
|---|---|
| `0_Setup.pptx` (13) | Cover · How CHIA Runs Work · Example: One Simulation Task Through CHIA · Machine and Software Used · Setup Process · Steps 1-2 Host and Python · Tools Live on the Host, Workers Mount Them · Steps 3-4 Installing the Tools · Step 5 The Block Cluster · Bring-up and Smoke Test · Remote Workers: Architecture (PLAN) · Remote Workers: Planned Steps (PLAN) · Setup Status |
| `1_Sim.pptx` (7) | Cover · Where Sim Fits · Running the Baseline Sim on CHIA · Agent: Improve the Testbench · NoCG vs CG: Same Test, Different Clocking · What the Agent Added · Takeaways and Limits |
| `2_FPGA.pptx` (7) | Cover · Where FPGA Fits · Running the FPGA Build on CHIA · Agent: Tune the FPGA Build · Baseline: Clock Gating Hurts on FPGA (3 biểu đồ) · Four Agent Rounds, None Accepted · Takeaways and Limits |
| `3_ASIC.pptx` (8) | Cover · Where ASIC Fits · Running the ASIC Flow on CHIA · Agent: Tune config.mk · Baseline: Clock Gating Pays Off on ASIC · What the Agent Changed (biểu đồ %) · Layouts, Same Scale · Takeaways and Limits |

Ảnh layout: `img/{NoCG,CG}_{baseline,agent}_layout.png`.

## 4. Số liệu đang dùng trong slide

**Đồng bộ tới:** commit `6c08ec3` (lần chạy thật 20–21/11/2026).

**Baseline**

| Bước | Chỉ số | NoCG | CG |
|---|---|---|---|
| Sim | Kết quả, số lần kiểm tra | PASS, 6 272 | PASS, 6 272 |
| FPGA | LUT / FF | 138 / 139 | 140 / 141 |
| | WNS / WHS (ns) | 4.579 / 0.024 | 1.502 / 0.052 |
| | Công suất động (mW) | 2 | 5 |
| ASIC | Cell area (µm²), util | 3 078, 44.8 % | 2 975, 46.9 % |
| | Die area (µm²) | 7 534 | 6 926 |
| | Setup / hold WS (ns) | 6.815 / 0.493 | 6.957 / 0.406 |
| | Công suất (mW) | 0.368 | 0.324 |
| | DRC | 0 | 0 |

**Agent (2 vòng mỗi bước)**

| | NoCG | CG |
|---|---|---|
| Sim: số lần kiểm tra | 6 272 → 542 046 (×86) | 6 272 → 1 509 090 (×241); thêm kiểm tra glitch ICG |
| FPGA | 2 vòng đều loại (luật Pareto chặt) | 2 vòng đều loại; r1: WNS 1.50 → 4.50 ns, P động −60 %, LUT+FF +3 |
| ASIC die / cell / power | −38.4 % / −1.8 % / −1.8 % | −20.5 % / −4.7 % / −6.8 % |

Luật nhận: Sim chỉ sửa testbench, số kiểm tra phải tăng; FPGA sửa `build.tcl` + file riêng FPGA, Pareto chặt; ASIC
chỉ sửa `config.mk`, luật 1 %.

**Trạng thái kiểm chứng (ghi đúng trên slide):** setup trên máy thật 14/11/2026; demo chạy thật 20–21/11/2026;
remote/cloud **chưa thử**; chạy riêng `--stages`/`--steps` chưa thử; ASIC **chưa chạy LVS**; FPGA dừng ở bitstream (chưa nạp board).

## 5. Build

```bash
# 1. pptxgenjs không cài sẵn toàn cục: cài vào scratchpad của session
cd <scratchpad> && npm install pptxgenjs
# 2. build 4 deck vào report/guides/
cd projects/demo-alu/report && NODE_PATH=<scratchpad>/node_modules node src/build_guides.js
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
