# demo-alu: flow Sim → FPGA → ASIC có agent Claude, do CHIA điều phối

Demo 1 của nhánh `demo`. Đề bài, phạm vi và nhật ký chi tiết nằm trong [`docs/demo-task.md`](../../docs/demo-task.md);
báo cáo kết quả trong [`REPORT.md`](REPORT.md).

**Mục tiêu:** chứng minh có thể dùng [CHIA](https://github.com/ucb-bar/chia) để chạy một flow thiết kế vi mạch số mã
nguồn mở cho cả FPGA và ASIC, với agent Claude đánh giá và tối ưu từng bước:

- **Sim:** Verilator.
- **FPGA:** Vivado 2025.1, part `xc7a100tcsg324-1` (Arty A7-100T).
- **ASIC:** Yosys + OpenROAD (ORFS), sky130hd, từ RTL tới GDS.

Thiết kế là một ALU 8 bit, có hai biến thể, mỗi biến thể là một demo độc lập:

| Demo | Thư mục | Đặc điểm |
|---|---|---|
| **NoCG** | [`NoCG/`](NoCG) | Thanh ghi dùng clock enable thường (mux hồi tiếp) |
| **CG** | [`CG/`](CG) | Clock của thanh ghi vào/ra đi qua cell clock gating (`icg`) |

## Nhánh `demo`

- Tách từ `setup` (tài liệu, config, script chung cho mọi project). Sửa dùng chung làm trên `setup` rồi merge vào `demo`.
- Phần riêng của demo chỉ nằm trong `projects/demo-alu/`, `docs/demo-task.md`, và mục `demo` trong `CLAUDE.md`.
- Không rebase hay force-push.
- Demo 2 (SoC PicoRV32) là phần tuỳ chọn, chưa làm.

## Ba pha

```
Pha 1  baseline.py   RTL cố định ─► Sim ─► FPGA ─► ASIC        thiết lập mặc định, không agent; lưu mọi số liệu
Pha 2  agent.py      mỗi bước, tối đa N vòng:
                     Claude đọc báo cáo ─► sửa file được phép ─► CHIA đo lại trên công cụ thật ─► gate ─► nhận / loại
Pha 3  compare.md    bảng baseline / agent của từng bước (agent.py tự viết khi xong)
```

Mỗi bước là một task CHIA (`@ChiaFunction(resources=...)`), chạy trên khối có công cụ tương ứng của cluster
([`docs/cluster-blocks.md`](../../docs/cluster-blocks.md)):

| Task | Khối (resource) | Công cụ |
|---|---|---|
| `sim_build` | `chipyard` | Verilator 5.022 của Chipyard |
| `sim_run` | `verilator` (`verilator_run`) | chạy binary `Vtb` |
| `fpga_sim` (chỉ pha 2), `fpga_build` | `vivado` | xsim + UNISIM, Vivado synth → P&R → bitstream |
| `asic_stage` × 7 | `asic` | ORFS: synth, floorplan, place, cts, route, finish (GDS + ảnh layout), lvs (KLayout so GDS với netlist) |
| `agent_session` (pha 2) | `claude` (`claude_creds`) | Claude Code, chạy trong thư mục của vòng |

Gate và điều kiện nhận ở pha 2:

| Bước | Agent được sửa | Gate | Nhận khi |
|---|---|---|---|
| Sim | `sim/tb_<DUT>.v` | `RESULT: PASS` | số lần kiểm tra (`checked`) tăng |
| FPGA | `fpga/build.tcl`, `fpga/sources.f`, file `.v` mới trong `fpga/` | `tb_fpga_top` PASS trong xsim, có `.bit`, WNS ≥ 0 | không kém ở WNS, công suất động, LUT+FF và tốt hơn ở ít nhất một |
| ASIC | `asic/config.mk` (không đổi design, file nguồn, SDC) | có GDS, setup/hold slack ≥ 0, DRC = 0, LVS khớp | không kém quá 1 % ở diện tích cell, diện tích die, công suất và tốt hơn quá 1 % ở ít nhất một |

- `rtl/`, `.xdc` và `.sdc` cố định: tối ưu riêng cho FPGA không lan sang ASIC.
- Sửa ngoài phạm vi bị hoàn lại tự động.
- Mọi bước dùng cùng part, cùng PDK và cùng clock 10 ns như baseline.

## Cấu trúc

```
projects/demo-alu/
├── README.md, REPORT.md
├── run_demo.py        submit job + phục vụ status.html qua http://127.0.0.1 + tự mở trình duyệt
├── clean_runs.py      dọn các lần chạy cũ trong WORK_DIR (mặc định chỉ liệt kê)
├── report/            slide báo cáo (session slide): REPORT.md (brief), guides/*.pptx, src/build_guides.js, img/
└── NoCG/, CG/         mỗi demo một bộ riêng (CG là bản sed NoCG→CG của script, cộng phần ICG)
    ├── rtl/           <DUT>.v (CG thêm icg_sim.v); RTL đưa sẵn, không ai sửa
    ├── sim/           tb_<DUT>.v (testbench tự kiểm, mô hình tham chiếu), run_sim.sh
    ├── fpga/          fpga_top.v (LFSR + MISR tự kiểm, 4 LED), arty_a7_100t.xdc, build.tcl, sources.f,
    │                  tb_fpga_top.v, golden_sig.py
    ├── asic/          config.mk, constraint.sdc, render_gds.py (KLayout → PNG); CG thêm icg_sky130.v (dlclkp)
    ├── baseline.py    pha 1
    ├── agent.py       pha 2 (+ pha 3: results/compare.md)
    └── status_page.py trang trạng thái HTML (giống hệt nhau ở hai demo)
```

Kết quả mỗi lần chạy ghi vào `<WORK_DIR>/demo-alu/<demo>/<run_id>/` (`WORK_DIR` mặc định `~/chia_demo`, mount vào mọi
khối). Thư mục gồm bản chép nguồn, log, báo cáo, VCD, bitstream, GDS, `asic/layout.png`, `results/*.json`,
`results/compare.md`, `profile/` (timeline CHIA) và `status.html`. Ở pha 2, mỗi vòng có thư mục riêng `<step>-r<k>/`
(trong đó `agent/` chứa `TASK.md`, `context/`, `summary.md`, `changes.diff`).

## Chạy

Trên head, trong `chia_env`:

1. Bật đủ năm khối:
   ```bash
   conda activate chia_env && cd ~/CHIA-workflow && python scripts/chia-up-blocks.py --fpga --asic
   ```
2. Pha 1, baseline, khoảng 4 phút mỗi demo. Trình duyệt tự mở trang trạng thái:
   ```bash
   python ~/CHIA-workflow/projects/demo-alu/run_demo.py NoCG baseline
   ```
3. Pha 2 và 3, khoảng 20–25 phút mỗi demo. Bước này dùng baseline OK mới nhất làm mốc:
   ```bash
   python ~/CHIA-workflow/projects/demo-alu/run_demo.py NoCG agent --rounds 2
   ```
4. Xem lại một lần chạy đã xong:
   ```bash
   python ~/CHIA-workflow/projects/demo-alu/run_demo.py show ~/chia_demo/demo-alu/NoCG/<run_id>
   ```
5. Dọn đĩa. Lệnh này chỉ liệt kê; thêm `--yes` để xoá thật:
   ```bash
   python ~/CHIA-workflow/projects/demo-alu/clean_runs.py --keep-agent 1
   ```
6. Tắt cluster:
   ```bash
   python ~/CHIA-workflow/scripts/chia-up-blocks.py --down
   ```

Thay `NoCG` bằng `CG` cho demo còn lại. Không dùng `run_demo.py` cũng được: vào `NoCG/` hoặc `CG/` rồi chạy
```bash
chia job submit --working-dir . -- python baseline.py
```
(hoặc `agent.py --rounds 2`). Các tham số khác:

- `baseline.py`: `--stages sim,fpga,asic`, `--seed`, `--cycles`, `--en-pct`.
- `agent.py`: `--baseline <run_dir>`, `--steps`, `--rounds`, `--model`, `--agent-timeout`.

## Trạng thái kiểm chứng (21/11/2026)

| Phần | Trạng thái |
|---|---|
| Pha 1 baseline, cả hai demo, đủ Sim/FPGA/ASIC (ra GDS) | **Chạy thật**, chạy lại lần hai ra đúng từng số (công cụ tất định) |
| Pha 2 `agent.py --rounds 2`, cả hai demo | **Chạy thật**, `KẾT QUẢ: OK` |
| Pha 3 `compare.md` | **Chạy thật** (sinh bởi `agent.py`) |
| `status_page.py`: `python status_page.py <run_dir>` dựng lại trang từ lần chạy agent thật | Chạy thật (ra file `status.html`); chưa xem trang trên trình duyệt |
| `status_page.py` ghi trực tiếp trong lúc job chạy | Chỉ thử trong sandbox với dữ liệu giả |
| `run_demo.py` | Chỉ thử trong sandbox (server, trang chờ, `chia` giả); chưa chạy trên máy người dùng |
| `clean_runs.py` | Chạy thật (bản cũ). Luật giữ baseline được tham chiếu mới thử trên dữ liệu giả |
| Bước LVS (`asic_lvs`, thêm 24/11/2026) | Mới thử phần đọc kết quả với dữ liệu giả; chưa chạy thật |
| Nạp bitstream xuống board | Chưa (chưa có board) |
| Slide, video | Chưa làm |
