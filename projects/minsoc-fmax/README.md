# minsoc-fmax: tối ưu timing cả SoC `MinimalSoCConfig`

Mở 10/11/2026, nhánh `project/minsoc-fmax` (tách từ `setup`). Bắt đầu từ baseline upstream
`chipyard.MinimalSoCConfig` (không dùng lại patch của rocket-fmax/soc-fmax). Đầu ra: patch Chisel đạt WNS ≥ 0.

- **FPGA (ưu tiên):** cả SoC trên Arty A7-100T (`xc7a100tcsg324-1`), WNS post-route ≥ 0 ở 10 → 6,667 → 5 ns.
- **ASIC (nếu còn thời gian):** cả ChipTop, 5 ns, WNS ≥ 0 (PDK/flow đề xuất trước khi làm).

Mọi build (sim, Verilog, bitstream) chạy trên fork `~/tools/RISCVTutorial` (Chipyard 1.10.0). Agent
(Claude Code, opus, effort medium, tối đa 5 vòng/đợt) sửa Chisel; session chỉ viết loop, tool, prompt.

## Pha

| Pha | Lệnh | Tiêu chí vào | Tiêu chí ra | Trạng thái |
|---|---|---|---|---|
| Simulate | `minsoc_fmax.py --phase sim` | Bước 0 OK | Gate pass ở baseline (test fail sẵn ghi rõ, loại khỏi gate), cycle baseline đo thật | chưa viết |
| FPGA | `minsoc_fmax.py --phase fpga --period-ns 10\|6.667\|5` | Sim xong, baseline bitstream 10 ns, nhiễu P&R đo 2 lần | WNS post-route ≥ 0 ở period, gate pass, cycle ≤ +3 %, không vượt tài nguyên xc7a100t | chưa viết |
| ASIC | `minsoc_fmax.py --phase asic` | Sau FPGA, PDK/corner đã duyệt (mặc định sky130hd tt_025C_1v80, Yosys + OpenROAD) | WNS ≥ 0 ở 5 ns, gate pass | chưa làm |

**Gate** (như soc-fmax): riscv-tests ISA (`rv64*-p-*`) + 8 benchmark (dhrystone, median, multiply, qsort,
towers, vvadd, mm, spmv), Verilator cả SoC, chạy trên khối `chipyard` (cần môi trường của fork). Test fail sẵn
ở baseline (soc-fmax: `rv64ui-p-ma_data`) được ghi và loại khỏi gate.
**Ngân sách:** trung bình nhân cycle của 8 benchmark ≤ +3 % so với baseline.
**Nhận/loại FPGA:** theo WNS post-route; ngưỡng nhận > nhiễu đo ở Mốc 1(c).

Thay đổi chỉ cho FPGA nằm trong `WithMinSocFpgaTweaks` (`scala/MinSocArty100TConfigs.scala`), không lẫn vào
`MinSocFmaxConfig` dùng cho sim/ASIC. Agent được sửa `generators/{rocket-chip, rocket-chip-inclusive-cache,
testchipip, chipyard/src}`; không sửa `fpga/`, `fpga-shells`.

Config FPGA = `WithArty100TTweaks(freqMHz)` + `MinSocFmaxConfig`, **giữ L2 128 KiB** (khác `RocketArty100TConfig`
dùng `WithBroadcastManager`, không L2).

## Tài nguyên

- Cluster: `python scripts/chia-up-blocks.py --fpga` (khối chipyard, verilator, claude, vivado 1 slot). Không yaml riêng.
- Một Vivado một lúc (23 GB RAM). Không chạy song song với `chipyard-dev` hay job khác cùng sửa fork.
- Cache `~/chia_cache_minsoc`, namespace Ray `minsoc_fmax`, tag = hash patch + pha + period. Kết quả ở
  `~/minsoc-fmax/<run>/` (`results.csv`, `history.md`, `best.patch`, `summary.md`).
- Fork phải sạch (`git status` của fork và submodule), trừ các file Config do `install.sh` chép vào.

## Bước 0: cài Config và kiểm môi trường (chưa chạy thật)

```bash
cd ~/CHIA-workflow && git fetch origin && git checkout project/minsoc-fmax && git pull
```

```bash
scripts/install-chipyard-config.sh && projects/minsoc-fmax/install.sh
```

Cần thấy `MIXIN_OK`, `INSTALL_DONE` và một dòng `class WithArty100TTweaks(... freqMHz ...)`.

```bash
conda activate chia_env && python scripts/chia-up-blocks.py --fpga
```

```bash
cd ~/CHIA-workflow/projects/minsoc-fmax && chia job submit --working-dir . -- python probe.py
```

Cần thấy `KẾT QUẢ: OK`. Gửi lại toàn bộ log (đặc biệt `board_part`, `@@PART`, `fpga_shells_arty100`, `bench`,
`isa_count`, `dirty`).

## File

| File | Nội dung |
|---|---|
| `scala/MinSocFmaxConfig.scala` | `chipyard.MinSocFmaxConfig` = `MinimalSoCConfig` (điểm gắn mixin) |
| `scala/MinSocArty100TConfigs.scala` | `MinSocArty100TF{100,150,200}Config`, `WithMinSocFpgaTweaks` |
| `install.sh` | Chép 2 file trên vào fork |
| `probe.py` | Bước 0: kiểm fork, gate, Vivado/board/part, không build |
| (Mốc 1) `minsoc_nodes.py`, `vivado_tcl.py`, `minsoc_fmax.py` | Gate, bitstream, driver |
| (Mốc 2) `prompts/*.md` | Prompt agent |
