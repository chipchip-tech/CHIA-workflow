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

Từ 26/11/2026 mọi script điều phối nằm ở thư viện chung [`flow/`](../../flow/README.md) (gốc repo); mỗi demo chỉ còn
nguồn thiết kế và một file khai báo `project.py`.

```
Pha 1  flow.baseline   RTL cố định ─► Sim ─► FPGA ─► ASIC        thiết lập mặc định, không agent; lưu mọi số liệu
Pha 2  flow.agent      mỗi bước, tối đa N vòng:
                       Claude đọc báo cáo ─► sửa file được phép ─► CHIA đo lại trên công cụ thật ─► gate ─► nhận / loại
Pha 3  compare.md      bảng baseline / agent của từng bước (flow.agent tự viết khi xong)
```

Mỗi việc là một task CHIA (`@ChiaFunction(resources=...)`), chạy trên khối có công cụ tương ứng của cluster
([`docs/cluster-blocks.md`](../../docs/cluster-blocks.md)):

| Task | Khối (resource) | Công cụ |
|---|---|---|
| `sim_build` | `chipyard` | lint `verilator --lint-only -Wall` trên RTL + Verilator 5.022 của Chipyard build testbench |
| `sim_run` | `verilator` (`verilator_run`) | chạy binary `Vtb`, coverage, VCD |
| `fpga_sim` (chỉ pha 2, lọc sớm), `fpga` | `vivado` | xsim + UNISIM; Vivado synth → P&R → bitstream, methodology, công suất SAIF, mô phỏng gate-level netlist sau route (`tb_gls.v`) |
| `asic_synth` … `asic_finish` (6 task, lần lượt) | `asic` | ORFS tới GDS + ảnh layout; finish chuẩn bị luôn model cell cho GLS (Yosys từ `.lib`) |
| `asic_drc`, `asic_lvs`, `asic_sta_<corner>` (**chạy cùng lúc** sau finish, mỗi task 0,25 slot `asic`) | `asic` | KLayout DRC, KLayout LVS, OpenSTA sau route (`flow/kit/asic/signoff.tcl`, corner tt và tt + OCV ±10 %, SPEF) |
| `asic_gls` (cùng lúc với ba loại trên) | `chipyard` | Verilator chạy testbench trên netlist `6_final.v` |
| `agent` (pha 2) | `claude` (`claude_creds`) | Claude Code, chạy trong thư mục của vòng |

Gate và điều kiện nhận ở pha 2:

| Bước | Agent được sửa | Gate | Nhận khi |
|---|---|---|---|
| Sim | `sim/tb_<DUT>.v` | `RESULT: PASS`, lint sạch | số bin functional coverage (`sim/fcov_<DUT>.sv`, cố định) tăng, line/toggle/branch của DUT không giảm (trước 25/11/2026: `checked` tăng) |
| FPGA | `fpga/build.tcl`, `fpga/sources.f`, file `.v` mới trong `fpga/` | `tb_fpga_top` PASS trong xsim, có `.bit`, đúng clock của baseline, WNS ≥ 0 và WHS ≥ 0, methodology không có Critical Warning, GLS netlist sau route PASS, SAIF khớp ≥ 50 % net | bản tốt nhất chưa đạt sign-off thì nhận ngay; còn lại không kém ở WNS và công suất động SAIF, LUT+FF kém ≤ 3 %, tốt hơn ở ít nhất một |
| ASIC | `asic/config.mk` (không đổi design, file nguồn, SDC) | có GDS, setup/hold slack ≥ 0, DRC router = 0, DRC KLayout = 0, antenna = 0, LVS khớp, STA sau route đạt ở mọi corner, GLS netlist PASS | không kém quá 1 % ở diện tích cell, diện tích die, công suất và tốt hơn quá 1 % ở ít nhất một |

- `rtl/`, `.xdc` và `.sdc` cố định: tối ưu riêng cho FPGA không lan sang ASIC.
- Sửa ngoài phạm vi bị hoàn lại tự động.
- Mọi bước dùng cùng part, cùng PDK và cùng clock như baseline (FPGA 5,3 ns từ 25/11/2026, ASIC 10 ns).

## Cấu trúc

```
flow/                  thư viện chung (điều phối, gate, agent, trang status); xem flow/README.md
└── kit/               file cố định dùng chung, flow chép vào thư mục chạy: sim_main.cpp, power_saif.tcl, signoff.tcl,
                       render_gds.py, cells_extra.v (sky130hd)
projects/demo-alu/
├── README.md, REPORT.md
├── common/alu_project.py   phần chung của hai khai báo (mô tả cho agent, hàm móc EN_THR/EXP_SIG của fpga_top)
├── report/            slide báo cáo (session slide): REPORT.md (brief), guides/*.pptx, src/build_guides.js, img/
└── NoCG/, CG/         mỗi demo một bộ nguồn riêng
    ├── project.py     khai báo cho flow/: DUT, plusargs, part, biến thể FPGA, platform, mô tả cho agent
    ├── rtl/           <DUT>.v (CG thêm icg_sim.v); RTL đưa sẵn, không ai sửa
    ├── sim/           tb_<DUT>.v (testbench tự kiểm, mô hình tham chiếu), run_sim.sh, fcov_<DUT>.sv (bin functional
    │                  coverage, bind vào DUT, cố định)
    ├── fpga/          fpga_top.v (LFSR + MISR tự kiểm, 4 LED), arty_a7_100t.xdc, build.tcl, sources.f, tb_fpga_top.v,
    │                  golden_sig.py, tb_power.v, tb_gls.v; clock.xdc do flow ghi lại; CG thêm icg_fpga_bufgce.v, icg_fpga_ce.v
    └── asic/          config.mk, constraint.sdc, gls/tb_gls_<DUT>.v; CG thêm icg_sky130.v (dlclkp)
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
2. Pha 1, baseline, khoảng 3–4 phút mỗi demo. Trình duyệt tự mở trang status:
   ```bash
   python ~/CHIA-workflow/flow/run.py projects/demo-alu/NoCG baseline --fpga-period 5.3
   ```
3. Pha 2 và 3, khoảng 15–20 phút mỗi demo. Bước này dùng baseline OK mới nhất làm mốc:
   ```bash
   python ~/CHIA-workflow/flow/run.py projects/demo-alu/NoCG agent --rounds 2
   ```
4. Xem lại một lần chạy đã xong:
   ```bash
   python ~/CHIA-workflow/flow/run.py show ~/chia_demo/demo-alu/NoCG/<run_id>
   ```
5. Dọn đĩa. Lệnh này chỉ liệt kê; thêm `--yes` để xoá thật:
   ```bash
   python ~/CHIA-workflow/flow/clean_runs.py --keep-agent 1
   ```
6. Tắt cluster:
   ```bash
   python ~/CHIA-workflow/scripts/chia-up-blocks.py --down
   ```

Thay `NoCG` bằng `CG` cho demo còn lại. Không dùng `run.py` cũng được: từ gốc repo chạy
`chia job submit --working-dir . -- python -m flow.baseline --project projects/demo-alu/NoCG` (hoặc `flow.agent`).
Các tham số khác:

- `flow.baseline`: `--stages sim,fpga,asic`, `--plusarg seed=7` (lặp được; mặc định seed=1, cycles=20000, en_pct=30),
  `--fpga-period <ns>` (mặc định 10), `--fpga-variant` (NoCG: `rtl`; CG: `latch` mặc định, `bufgce`, `ce`),
  `--fpga-opt en_pct=30` (tỉ lệ gửi phép của `fpga_top`), `--power-cycles`.
- `flow.sweep fmax` (dò Fmax từng biến thể), `flow.sweep power --period <ns> --values 10,30,70,100`
  (`run.py <project> sweep fmax` cũng được).
- `flow.agent`: `--baseline <run_dir>`, `--steps`, `--rounds`, `--model`, `--agent-timeout`. Baseline phải chạy bằng
  `flow/` (baseline của script cũ không có `cfg`, agent từ chối).

## Trạng thái kiểm chứng

| Phần | Trạng thái |
|---|---|
| Pha 1 baseline, cả hai demo, đủ Sim/FPGA/ASIC (ra GDS) | **Chạy thật**, chạy lại lần hai ra đúng từng số (công cụ tất định) |
| Pha 2 `--rounds 2`, cả hai demo (script cũ, trước `flow/`) | **Chạy thật**, `KẾT QUẢ: OK` (lần cuối 26/11/2026 với đủ gate sign-off: `agent-20261126-082319`, `agent-20261126-083923`) |
| Pha 3 `compare.md` | **Chạy thật** (script cũ) |
| **Thư viện `flow/` (26/11/2026)**: gộp code của NoCG/CG, `project.py`, DRC ‖ LVS ‖ STA ‖ GLS song song sau finish, trang status tiếng Anh (flow có gate, timeline theo khối, kết quả, log) | **Chạy thật trên cluster 26/11/2026: baseline NoCG `baseline-20261126-195910`, CG `baseline-20261126-200321`, `KẾT QUẢ: OK`, mọi số (coverage, LUT/FF, WNS, SAIF, diện tích, STA, GLS) trùng từng số với baseline 25/11; 5 task kiểm tra sau finish chạy song song 5,2 s (cộng lần lượt 12,3–12,5 s), `make drc`/`lvs`/`run` cùng `WORK_HOME` không xung đột. Agent `--rounds 1`: NoCG `agent-20261126-200829` nhận sim-r1 (51 → 55 bin), asic-r1 (die 7 534 → 5 460 µm², trùng lần trước), loại fpga-r1; CG `agent-20261126-201926` nhận sim-r1 (51 → 57, branch 8 → 10), fpga-r1 (sign-off, WNS 0 → 0,524, methodology 2 → 0), asic-r1 (cell 2 975 → 2 930, die 6 926 → 5 018 µm², công suất −6,4 %).** `flow.sweep` chưa chạy trên cluster |
| Script cũ `run_demo.py`, `status_page.py`, `clean_runs.py` | Chạy thật 25/11/2026; đã thay bằng `flow/run.py`, `flow/status.py`, `flow/clean_runs.py` |
| Bước LVS (`asic_lvs`, thêm 24/11/2026) | **Chạy thật 25/11/2026** (`baseline.py --stages asic`), cả hai demo LVS khớp. Thử cố ý làm sai (đổi dây CLK/D một flop trong CDL): LVS báo không khớp, đúng. Agent ASIC với gate LVS: chưa chạy |
| Coverage ở bước Sim (thêm 25/11/2026) | **Chạy thật 25/11/2026** (`baseline.py --stages sim`, Verilator 5.022 của Chipyard): NoCG 51/55 bin, CG 51/57 bin, trùng số trong sandbox. Agent với luật mới: chạy thật 25/11 (`REPORT.md` mục 9) |
| FPGA: clock chung, biến thể BUFGCE/CE, công suất SAIF, `fpga_sweep.py` (thêm 25/11/2026) | **Chạy thật 25/11/2026** `baseline.py --stages fpga` ở 100 MHz (NoCG, CG `bufgce`, CG `ce`) có SAIF; `fpga_sweep.py fmax` và `power --period 5.3` chạy thật cả hai demo |
| Gate sign-off (lint, DRC KLayout, antenna, STA corner, GLS FPGA và ASIC, methodology FPGA; thêm 25/11/2026) | **Chạy thật 25/11/2026** baseline đủ 3 bước cả hai demo (`baseline-20261125-230550`, `baseline-20261125-231121`), `KẾT QUẢ: OK`. Chỉ có corner tt (chưa có `.lib` ss/ff). Agent `--rounds 2` với các gate này: **chạy thật 26/11/2026**, cả hai `KẾT QUẢ: OK` (`REPORT.md` mục 9) |
| Nạp bitstream xuống board | Chưa (chưa có board) |
| Slide, video | Chưa làm |
