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
| Simulate | `minsoc_fmax.py --phase sim` | Bước 0 OK | Gate pass ở baseline (test fail sẵn ghi rõ, loại khỏi gate), cycle baseline đo thật | Mốc 1 xong: 131/144 ISA pass (13 loại), geomean 23666, ngân sách ≤ 24376 |
| FPGA | `minsoc_fmax.py --phase fpga --period-ns 10\|6.667\|5` | Sim xong, baseline bitstream 10 ns, nhiễu P&R đo 2 lần | WNS post-route ≥ 0 ở period, gate pass, cycle ≤ +3 %, không vượt tài nguyên xc7a100t | Mốc 1 xong: WNS −9,326 ns ở 10 ns (≈ 51,7 MHz), ngưỡng nhận 0,8 ns |
| ASIC | `minsoc_fmax.py --phase asic` | Sau FPGA, PDK/corner đã duyệt (mặc định sky130hd tt_025C_1v80, Yosys + OpenROAD) | WNS ≥ 0 ở 5 ns, gate pass | Bước A (synth + STA, sky130hd) đã viết, chưa chạy thật |

**Gate** (như soc-fmax): riscv-tests ISA (`rv64*-p-*`) + 8 benchmark (dhrystone, median, multiply, qsort,
towers, vvadd, mm, spmv), Verilator cả SoC, chạy trên khối `chipyard` (cần môi trường của fork). Test fail sẵn
ở baseline (soc-fmax: `rv64ui-p-ma_data`) được ghi và loại khỏi gate.
**Ngân sách:** trung bình nhân cycle của 8 benchmark ≤ +3 % so với baseline.
**Nhận/loại FPGA:** theo WNS post-route; ngưỡng nhận > nhiễu đo ở Mốc 1(c).

Thay đổi chỉ cho FPGA nằm trong `WithMinSocFpgaTweaks` (`scala/MinSocArty100TConfigs.scala`), không lẫn vào
`MinSocFmaxConfig` dùng cho sim/ASIC. Agent được sửa `generators/{rocket-chip, sifive-cache,
testchipip, chipyard/src}`; không sửa `fpga/`, `fpga-shells`.

Config FPGA = `WithArty100TTweaks(freqMHz)` + `MinSocFmaxConfig`, **giữ L2 128 KiB** (khác `RocketArty100TConfig`
dùng `WithBroadcastManager`, không L2).

## Phát hiện ở Bước 0 (probe 10/11/2026, chạy thật)

- Fork sạch; 144 ISA test, đủ 8 benchmark trong `$RISCV/riscv64-unknown-elf/share/riscv-tests/`; Vivado 2025.1
  mở được `xc7a100tcsg324-1` (license OK); không cần vá `wf_active`.
- L2 (sifive `InclusiveCache`) ở `generators/sifive-cache`, không phải `rocket-chip-inclusive-cache`.
- `WithArty100TTweaks` của fork **không có tham số** (ghi cứng 50 MHz, `RocketArty100TConfig` dùng `TinyRocketConfig`),
  thêm GPIO/SPI/UART của harness, bỏ SerialTL. Tần số được đè bằng `WithMinSocFreqMHz`.
- fpga-shells đặt `part_fpga {xc7a100ticsg324-1L}`; project ép `xc7a100tcsg324-1` (chip thật của Arty) khi chạy
  bitstream (đè tạm `board.tcl`, khôi phục sau). Cần board files Digilent ở `~/tools/Xilinx/vivado-boards`.
- Elaborate `MinSocArty100TF100Config` chạy được (10/11/2026): 4 bus 100 MHz, L2 131072 byte, I$/D$ 16 KiB, UART/SPI/GPIO
  ở `0x6400xxxx`. Lần đầu build `fpga_platforms` phải tải thêm thư viện (hardfloat test, breeze, commons-lang3);
  Maven Central hay ngắt (`Connection reset`), file `.lock`/`.part` dở của coursier làm sbt treo: xoá chúng rồi chạy lại
  (`elaborate_fpga` tự làm, thử 3 lần). `cp: missing destination` và `Empty *.mems.conf` trong log là bình thường.
  Cache coursier nằm trong container (`/home/ray/.cache`), mất khi `chia down`.

## Cổng bộ nhớ trên FPGA

Harness Arty100T của fork (`Arty100TwoDDRHarnessImp`) **không có DDR/MIG**. Cổng `axi4_mem_0` (AXI4 64 bit, ID 4 bit)
của ChipTop được binder mặc định `WithBlackBoxSimMem` nối vào `SimDRAM` (DPI-C, 256 MiB ở `0x80000000`): chỉ dùng cho
mô phỏng, Vivado không tổng hợp được. Fork upstream cũng vậy.

Cách xử lý (duyệt 10/11/2026, phương án A): ở bước Vivado, `vivado_tcl.py` bỏ `SimDRAM.v/.cc` và nạp
`fpga/SimDRAM_fpga.v`: cùng tên module, tham số, cổng; bên trong là AXI4 slave nối BRAM 128 KiB (suy luận, ghi theo
byte, địa chỉ cao bị bỏ qua). **RTL ChipTop không đổi**, nên cái đo trên FPGA đúng là cái chạy gate và sau này ASIC.
Tắt bằng `--fpga-mem simdram` (flow gốc). Wrapper đã qua testbench iverilog (burst ghi/đọc, strobe, backpressure,
alias); chưa chạy trên board (Mốc 1 chỉ đo timing, không boot).

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

## Mốc 1: baseline (chạy thật 10/11/2026, xong)

**Kết quả** (run `m1-sim`, `m1-fpga10c`):
- Sim: 131/144 ISA pass, 13 test fail sẵn loại khỏi gate; geomean `mcycle` 8 benchmark = 23666, ngân sách +3 % ≤ 24376.
- FPGA 10 ns (Vivado 42 phút, bitstream OK): impl0 = impl1 = −9,326 ns (tất định), impl2 `ExtraTimingOpt` −9,025 ns.
  Fmax ≈ 51,7 MHz. LUT 39,7k, FF 19,3k, BRAM 88, DSP 24. WHS +0,035 ns.
- Path xấu nhất: L2 `inclusive_cache_bank_sched`, từ `c_mshr/w_grantlast_reg` sang `s_writeback`/`w_*` của các MSHR
  khác, 22–25 tầng logic.
- **Ngưỡng nhận: cải thiện WNS > 0,8 ns.** Chênh giữa directive là 0,301 ns (công thức cho 0,602 ns), nhưng chỉ
  thêm một file xdc gán chân reset (m1-fpga10b → 10c) đã làm WNS đổi 0,777 ns (−10,103 → −9,326). Nên lấy
  max(0,602; 0,777) ≈ 0,8 ns, so sánh theo WNS tốt nhất trong các impl.
- Lỗi đã sửa: `reset` không có chân (DRC NSTD-1/UCIO-1) vì `shell.xdc` chỉ đặt `BOARD_PIN` mà project không có
  board part. Thêm `fpga/minsoc_reset.xdc` (C2, LVCMOS33). CRITICAL WARNING về IP trùng và `arty-master.xdc`
  thiếu port là vô hại.


Cần cluster `--fpga` (chipyard, verilator, claude, vivado). Chạy **lần lượt**, không song song (cùng sửa fork).

```bash
cd ~/CHIA-workflow && git pull && projects/minsoc-fmax/install.sh
```

```bash
cd ~/CHIA-workflow/projects/minsoc-fmax && chia job submit --working-dir . -- python minsoc_fmax.py --phase sim --baseline-only --run m1-sim
```

```bash
cd ~/CHIA-workflow/projects/minsoc-fmax && chia job submit --working-dir . -- python minsoc_fmax.py --phase fpga --baseline-only --run m1-fpga10 --period-ns 10 --repeat 3
```

- Sim: build Verilator `MinSocFmaxConfig` (`bundle_runtime_libs=True` để binary chạy được ở khối verilator), gate ISA
  `rv64*-p-*` + 8 benchmark, benchmark chạy lại với seed 7. Ra `summary.md`, `gate.json`, `known_fail.txt` (test fail
  sẵn, loại khỏi gate), trung bình nhân `mcycle`.
- FPGA: elaborate, rồi Vivado synth một lần và place/route `--repeat` lần từ cùng `post_synth.dcp`: impl0, impl1 cùng
  directive Explore (kiểm tất định), impl2+ đổi directive của `place_design` (đo độ nhạy, dùng làm nhiễu). Bitstream ở
  impl0. Ra WNS/TNS/WHS, Fmax clock SoC, LUT/FF/BRAM/DSP, top 10 path, ngưỡng nhận đề xuất = max(0,05 ns; 2 × chênh WNS).
  Report ở `~/minsoc-fmax/<run>/reports/`, checkpoint ở `<fork>/fpga/generated-src/...<Config>/minsoc-vivado/<run>/obj/`.

## Mốc 2: vòng agent

**`m2-f100a` (10 ns, 5 lượt, chạy thật 10/11/2026):** WNS −9,025 → **−5,599 ns (64,1 MHz)**, nhận r1, r4; geomean
23666 không đổi. Path xấu nhất vẫn ở MSHR scheduler của L2 (`s_pprobe` → MSHR khác, `w_pprobeacklast` → địa chỉ BRAM
`bankedStore`), 16–19 tầng. r2 (+0,74 ns) và r5 (+0,61 ns) bị ngưỡng 0,8 ns loại. Directive thắng: ExtraTimingOpt 3/5,
Explore 2/5, chênh 0,1–0,5 ns, nên giữ cả hai. Mỗi lượt 50–60 phút.

**Đổi sau `m2-f100a` (chưa chạy thật):** ngưỡng nhận mặc định 0,5 ns; flow Vivado nhanh hơn (`maxThreads 8`, bỏ
`power_opt_design`, phys_opt trước route = Default), nên r0 của batch sau đo lại điểm xuất phát; `summary.md` thêm
cột phút Vivado. Không chạy gate song song với Vivado (23 GB RAM không đủ).

**Vivado nhanh (mặc định từ 11/11/2026, chưa chạy thật):** r0 luôn chạy FULL (flow cũ, `--directives`). Mỗi lượt agent:
1. NHANH: 1 directive (`--incr-directive`, mặc định ExtraTimingOpt), incremental từ `post_route_*.dcp` của best
   (`read_checkpoint -incremental -auto_incremental`; Vivado tự chạy thường khi dùng lại được ít). Sau place, nếu
   WNS < best − `--early-reject-margin` (1,5 ns) thì dừng, không route: `loại sớm`.
2. WNS nhanh < best + `--accept-ns` − `--confirm-slack` (0,5 − 0,3 ns): loại, không chạy FULL.
3. Ngược lại: chạy FULL (2 directive, không incremental), quyết nhận/loại theo số FULL. Lượt được nhận luôn đo bằng FULL.
`eval.json` có thêm `fast` (WNS nhanh, WNS sau place, incremental có dùng không) để kiểm biên sau mỗi batch.
`--no-incremental` quay về flow cũ. Đổi `vivado_tcl.py` làm cache Vivado cũ không trúng nữa (gate vẫn trúng).

**Chạy tiếp batch đã dừng (`--resume`, chưa chạy thật):** chỉ `chia job stop` sau khi log in `rN: NHẬN …` hoặc
`rN: loại …` (dừng giữa lượt thì fork bẩn, `--resume` từ chối). Submit lại đúng lệnh cũ, thêm `--resume`: bỏ r0, đọc
`history.json`, best = lượt NHẬN cuối, đánh số tiếp từ r(N+1); `--rounds` vẫn là tổng số lượt của batch.


Mục tiêu lần lượt 100 → 150 → 200 MHz (`--period-ns 10 | 6.667 | 5`), mỗi batch tối đa 5 lượt, dừng chờ người dùng
sau mỗi batch. Cần cluster `--fpga` (chipyard, verilator, claude, vivado) và run `m1-sim` (lấy `known_fail.txt`,
ngân sách geomean).

```bash
cd ~/CHIA-workflow && git pull && projects/minsoc-fmax/install.sh
```

```bash
cd ~/CHIA-workflow/projects/minsoc-fmax && chia job submit --working-dir . -- python minsoc_fmax.py --phase fpga --run m2-f100a --period-ns 10 --rounds 5
```

Mỗi lượt (khoảng 60–70 phút):
1. `fork_checkpoint`: HEAD fork + 3 submodule, file `.scala` chưa track, nội dung file Config.
2. Agent (Claude Code `--model opus --effort medium`, timeout 30 phút) chỉ có tool `chipyard_bash` trên fork; đọc top
   path, report của best, lịch sử mọi lượt trước (`~/minsoc-fmax/*/history.json`). Không được build, không dùng git.
3. `fork_scope`: chỉ cho sửa Scala của `rocket-chip`, `sifive-cache`, `testchipip` và `MinSocFmaxConfig.scala`.
4. Elaborate `MinSocArty100TF<f>Config`; lỗi compile thì agent sửa (tối đa 2 lần).
5. Build Verilator `MinSocFmaxFpgaConfig` (= `WithMinSocFpgaTweaks ++ MinSocFmaxConfig`, cùng RTL với bản Arty), gate:
   ISA trừ test fail sẵn, 8 benchmark, geomean ≤ 24376.
6. Vivado: synth + place/route `Explore` và `ExtraTimingOpt`, lấy WNS tốt nhất, không ghi bitstream.
7. Nhận nếu gate pass và WNS tốt hơn best > `--accept-ns` (0,8 ns tới `m2-f100a`, nay 0,5 ns): commit lên nhánh `minsoc-fmax/agent` của fork (từng submodule
   rồi fork gốc). Không thì `fork_revert`.

Lượt `r0` đo điểm xuất phát (commit hiện tại của fork), nên batch sau tiếp từ các lượt đã nhận.
Thay đổi chỉ dành cho FPGA phải có tham số mặc định giữ hành vi cũ, bật trong `chipyard.WithMinSocFpgaTweaks`.

Kết quả ở `~/minsoc-fmax/<run>/`: `summary.md`, `history.json`, `results.csv`, `patches/r<i>.patch`,
`r<i>/agent_prompt.md`, `agent_response.md`, `agent_transcript.txt`, `eval.json`, `reports/`.
Checkpoint Vivado mỗi lượt ở `<fork>/fpga/generated-src/...<Config>/minsoc-vivado/<run>-r<i>/` (vài trăm MB mỗi lượt,
xoá khi cần chỗ).

**Lưu ý:** đang có lượt được nhận thì không chạy lại `install.sh` (nó ghi đè `MinSocFmaxConfig.scala` mà agent đã sửa).
Muốn bỏ hết kết quả agent: checkout lại nhánh gốc trong fork và từng submodule (`base` / commit cũ trong `fork_heads_start.txt`).

## ASIC bước A: synth + STA ước lượng (chưa chạy thật)

Hỏi nhanh "200 MHz có khả thi không" trước khi đầu tư P&R. PDK mặc định **sky130hd** (tt_025C_1v80, PDK thật); `--asic-pdk nangate45` vẫn dùng được để so.

```bash
cd ~/CHIA-workflow/projects/minsoc-fmax && chia job submit --working-dir . -- python minsoc_fmax.py --phase asic --baseline-only --run a0-s130-5ns --asic-period-ns 5
```

Frontend HDL mặc định là slang (`SYNTH_HDL_FRONTEND=slang`): bộ đọc Verilog cũ của Yosys không hiểu cú pháp `'{...}` firtool sinh ra (lỗi `Assignment pattern` ở `TLAtomicAutomata.sv`). Dùng `--asic-frontend yosys` để quay lại bộ đọc cũ.

- Cần cluster có khối `asic` (`chia-up-blocks.py --asic`, thêm `--fpga` nếu muốn giữ vivado), fork sạch, không chạy cùng Vivado.
- Khối chipyard: build Verilator `MinSocFmaxConfig` (`clean=False`, có sẵn thì nhanh), lấy RTL ChipTop từ `<build>.top.f`
  (dự phòng: `gen-collateral` trừ harness), bỏ `*.top.mems.v`.
- Khối asic: stub `(* blackbox *)` cho các module SRAM, `make synth` của ORFS (`designs/<pdk>/minsoc_<run>/`),
  rồi `openroad sta.tcl` (`asic/sta.tcl`, `asic/constraint.sdc`: mọi cổng `*clock*` trừ jtag thành một clock, IO delay 20 %).
- Kết quả `~/minsoc-fmax/<run>/summary.md`: WNS/TNS, Fmax ≈ 1/(T − WNS), 10 path xấu nhất, cell, diện tích.
- **Lạc quan:** không tải dây (P&R thường xấu thêm 20–40 %), path vào/ra SRAM không tính. Không cache.
- Bước B (nếu A ổn): map SRAM sang macro (sky130: `sky130_sram_*` của ORFS; nangate45: `fakeram45`), P&R đầy đủ.

## File

| File | Nội dung |
|---|---|
| `scala/MinSocFmaxConfig.scala` | `chipyard.MinSocFmaxConfig` = `MinimalSoCConfig` (điểm gắn mixin), `WithMinSocFpgaTweaks`, `MinSocFmaxFpgaConfig` (gate Mốc 2) |
| `scala/MinSocArty100TConfigs.scala` | `MinSocArty100TF{100,150,200}Config` |
| `install.sh` | Chép 2 file trên vào fork |
| `probe.py` | Bước 0: kiểm fork, gate, Vivado/board/part, không build |
| `minsoc_nodes.py` | ChiaFunction: `fork_state`, `load_test_elfs`, `elaborate_fpga`, `run_gate`, `vivado_impl` |
| `vivado_tcl.py` | Sinh tcl Vivado (part ép `xc7a100tcsg324-1`, thay SimDRAM, synth 1 lần + N lần place/route), đọc report |
| `minsoc_fmax.py` | Driver: `--phase sim\|fpga --baseline-only --run <tên> [--period-ns] [--repeat] [--fpga-mem]`; không `--baseline-only` = vòng agent Mốc 2 |
| `fpga/minsoc_reset.xdc` | Gán chân `reset` = C2 (nạp sau `shell.xdc`) |
| `fpga/SimDRAM_fpga.v` | AXI4 → BRAM 128 KiB thay `SimDRAM` khi tổng hợp FPGA |
| `asic/constraint.sdc`, `asic/sta.tcl` | Pha ASIC bước A: SDC (chu kỳ qua `MINSOC_PERIOD_NS`) và STA OpenROAD |
| `exp.yaml` | Cache/bypass CHIA cho tag `baseline_*` |
| `prompts/system.md`, `round.md`, `fix_build.md` | Prompt agent Mốc 2 |
