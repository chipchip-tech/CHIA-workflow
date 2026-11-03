# rocket-fmax: agent tối ưu Fmax của Rocket theo example `timing_opt` của CHIA

Mục tiêu: **Fmax của `RocketTile` (Chipyard `RocketConfig`) > 100 MHz** ở chu kỳ 10 ns, đo bằng Yosys + OpenSTA trên
`sky130_fd_sc_hd` (phần synth của nhánh ASIC), chi phí cycle tối đa +3 % so với RocketConfig gốc. Tốt nhất hiện có:
**92,36 MHz** (flow `v2`, patch của `fmax-r4`, cycle +0,405 %). Báo cáo các đợt Yosys: [REPORT.md](REPORT.md).

## Pha (theo `docs/workflow-phases.md`)

| Pha | Lệnh | Thước đo | Trạng thái |
|---|---|---|---|
| Simulate | `--phase sim` | gate (riscv-tests + 8 benchmark), chi phí cycle; agent thêm tầng pipeline, mỗi vòng qua gate thành một ứng viên (kèm synth sơ bộ cả tile, khối đã đổi) | `sim-1` xong (03/11/2026, code cũ): 5 ứng viên, cả 5 trên FMA; synth sơ bộ, nhóm biến thể **mới kiểm bằng mock** |
| ASIC, synth | `--phase asic` (mặc định), `--try-from <đợt sim>` | Yosys + OpenSTA sky130hd tt_025C_1v80, SRAM là hộp đen; gate như trên | r1–r4, y1–y4 đã chạy (flow `v2`); flow `v3` và nhận theo điểm họ **chưa chạy thật** |
| ASIC, post-route | (chưa có) | ORFS sky130 | Chưa bắt đầu |
| FPGA | (đã gỡ) | Vivado post-route, Arty A7-100T | **Tạm bỏ** ở 63,61 MHz (`fmax-r4`/`r5`); code ở commit `d2a1ac0` |

Ngân sách chung: chi phí cycle (trung bình nhân `mcycle / mcycle baseline` trên 8 benchmark) ≤ 1,03; mọi test pass
với `minstret` bằng baseline. `rv64ui-p-ma_data` fail ngay trên baseline nên bị loại khỏi gate.

## Một vòng

```
reset rocket-chip về bản tốt nhất (pha sim: luôn về điểm xuất phát)
  ──► 1 phiên agent Claude Code (--llm-timeout): sửa Chisel bằng chipyard_bash
        asic: tự A/B bằng synth_ab (make verilog + Yosys/OpenSTA một module hoặc cả tile)
        sim : chỉ rebuild_verilog (kiểm compile), không synth
  ──► kiểm phạm vi (chỉ src/main/scala của rocket-chip, không file *Config*)
  ──► build RocketConfig (lỗi compile → agent sửa build, --build-fixes)
  ──► Verilog RocketTile trùng bản đã đo → loại ngay
  ──► asic: Yosys + OpenSTA RocketTile; không thể qua tiêu chí nhận kể cả khi cycle không tăng → loại, không gate
  ──► gate (Verilator ×4, ~19 phút); test fail → agent debug (--debug-retries), build + đo lại
  ──► asic: nhận nếu chi phí cycle ≤ 1 + --max-ipc-loss VÀ một trong hai:
              Fmax / chi phí cycle > bản tốt nhất × (1 + --min-perf-gain)
              điểm họ tăng ≥ --min-family-gain và worst slack giảm ≤ --worst-tol
      sim : qua gate trong ngân sách cycle → lưu thành ứng viên (bản tốt nhất không đổi), rồi
            synth cả tile (sơ bộ, cache theo hash Verilog) và ghi khối đã đổi; trùng khối ứng viên trước = biến thể
```

**Chống lặp ở pha sim** (từ 03/11/2026, sau khi vòng 2 của `sim-1` làm lại ý của vòng 1 trên cùng FMA):
- *Khối đã đổi* = các instance sâu nhất có Verilog khác điểm xuất phát (`changed_leaf_paths`; đổi tên module con
  làm mọi module cha khác theo, nên chỉ giữ instance sâu nhất). Ứng viên đụng khối của ứng viên trước (trùng, hoặc
  instance này nằm trong instance kia) được ghi là **biến thể** của nó.
- Prompt sim có bảng "Blocks already covered": vòng, `TARGET`, khối đã đổi, cycle, synth sơ bộ. Agent chỉ được làm
  biến thể khi synth của ứng viên cũ cho thấy chưa rút ngắn được, và phải ghi `VARIANT OF: round N` ở dòng 2.
- `history.md` ghi dòng `TARGET` và khối đã đổi của mỗi vòng; tóm tắt agent lấy phần **đầu** câu trả lời (trước đây
  lấy giữa đoạn cuối nên mất dòng `TARGET`).
- `--no-sim-synth` tắt synth sơ bộ.

**Điểm họ** = tổng worst slack của `--family-k` (8) họ endpoint xấu nhất, mỗi họ chặn ở 0. Có nhiều họ sát nhau
(−0,48 đến −0,83 ns), nên Fmax chỉ tăng khi đã rút ngắn hết các họ đó; tiêu chí này cho phép nhận từng bước.

**Flow synth** (`--synth-flow`, tên nằm trong tag cache):
- `v2`: `synth -flatten`, một lần ABC cho cả tile. Sửa một khối làm họ khác dao động 0,2–0,6 ns (`fmax-y4`: sửa
  `MulDiv` làm `regfile_ext.Memory` nhảy từ −0,658 lên −0,080 ns), lớn hơn ngưỡng nhận 1 %.
- `v3` (mặc định từ 02/11/2026): synth phân cấp, ABC chạy riêng từng module, nên sửa một module không đụng netlist
  module khác. Không tối ưu qua cổng module, nên số `v3` không so trực tiếp với `v2`; đợt đầu với `v3` synth lại
  điểm xuất phát.

`synth_ab` nhớ Verilog đã đo (theo hash của module, flow, chu kỳ) trong đợt và trong các đợt `--history-from`: đo lại
một bản đã đo thì trả ngay kết quả cũ (`ALREADY MEASURED`).

## Chuẩn bị (một lần)

```bash
conda activate chia_env
cd ~/CHIA-workflow/projects/rocket-fmax/docker
docker image inspect chia-eda-sky130:latest >/dev/null 2>&1 || docker build -f EdaDockerfile -t chia-eda-sky130:latest .
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia up ~/CHIA-workflow/projects/rocket-fmax/cluster.yaml -y
chia status        # chipyard 1, verilator_run 4, claude_creds 1, eda 1
```

Worker `hello_claude` mount `~/.claude` của host: đăng nhập `claude` trên host trước.

## Chạy

Luôn submit **từ thư mục này** (cache lưu dataclass của `fmax_nodes`, lúc đọc lại phải import được). Mỗi lần chạy dùng
`--submission-id` và `--run-name` mới (câu trả lời agent được cache theo `agent_<run-name>_iter<i>_*`).

```bash
cd ~/CHIA-workflow/projects/rocket-fmax
START=~/rocket-fmax-v2/fmax-r4/patches/best.patch

# 0. Đo điểm xuất phát bằng flow v3 (không agent, ~5 phút; build và gate lấy từ cache)
chia job submit --no-wait --submission-id v3-start --working-dir . -- \
  python rocket_fmax.py --baseline-only --start-patch $START --run-name v3-start

# 1. Pha sim: agent thêm tầng pipeline, mỗi vòng qua gate thành ứng viên
chia job submit --no-wait --submission-id sim-1 --working-dir . -- \
  python rocket_fmax.py --phase sim --start-patch $START --run-name sim-1 --iters 6

# 2. Pha asic: thử từng ứng viên của sim-1 rồi tới các vòng agent
chia job submit --no-wait --submission-id asic-1 --working-dir . -- \
  python rocket_fmax.py --start-patch $START --try-from ~/rocket-fmax-v2/sim-1 \
  --history-from ~/rocket-fmax-v2/sim-1 --run-name asic-1 --iters 5

chia job logs -f <id>          # dừng: chia job stop <id>
```

Mặc định: `--phase asic --period-ns 10 --synth-flow v3 --extra-bench mm spmv --exclude-isa rv64ui-p-ma_data
--model sonnet --effort medium --iters 5 --max-ipc-loss 0.03 --min-perf-gain 0.01 --family-k 8 --min-family-gain 0.15
--worst-tol 0.02 --out-dir ~/rocket-fmax-v2`.

- Đợt sau tiếp từ bản tốt nhất: `--start-patch ~/rocket-fmax-v2/<run>/patches/best.patch --history-from
  ~/rocket-fmax-v2/<run>`. Điểm xuất phát được build, gate và synth một lần, cache theo `baseline_start_<hash>_*`.
- `--try-from <đợt sim>` (từ 03/11/2026):
  1. Mỗi ứng viên được áp lên điểm xuất phát, build (1–2 phút) và synth (cache theo hash Verilog, nên ứng viên đã
     synth ở pha sim không phải synth lại). Bảng kết quả ghi vào `history.md`.
  2. Gom ứng viên đụng cùng khối thành nhóm (tính lại từ Verilog, nên dùng được cả `candidates.json` cũ). Thử các
     nhóm theo điểm họ tốt nhất trước; trong nhóm thử ứng viên tốt nhất trước, chỉ thử ứng viên kế khi nó bị loại.
     Mỗi nhóm giữ tối đa một ứng viên; các biến thể còn lại ghi `BỎ QUA`.
  3. Tiêu chí nhận như một vòng. Khi bản tốt nhất vẫn là điểm xuất phát của đợt sim, không chạy lại gate (cycle lấy
     từ pha sim, cùng patch trên cùng gốc). Khi bản tốt nhất đã đổi: áp (merge 3 chiều), build, synth, gate.
- Khi Fmax vượt 100 MHz (slack dương ở 10 ns), hạ `--period-ns` (vd. 8); tag cache đổi theo.
- Gặp "You've hit your limit" thì loop chờ tới giờ reset rồi gọi lại (`--quota-retries`, `--quota-max-wait`).
- Tuỳ chọn khác: `--llm-timeout 1800`, `--debug-retries 2`, `--empty-retries 1`, `--build-fixes 2`, `--fix-timeout 900`,
  `--synth-timeout 900`, `--make-jobs 16`, `--build-timeout 3600`, `--cache-dir ~/chia_cache`.

## Baseline (RocketConfig gốc, 10 ns, đo thật 29/10/2026)

| Worst slack (`v2`) | Fmax | Gate |
|---|---|---|
| −11,2161 ns, trong `core.div` | 47,13 MHz | ISA 106/107 (trừ `ma_data`), 18,9 phút |

| Benchmark | dhrystone | median | multiply | qsort | towers | vvadd | mm | spmv |
|---|---|---|---|---|---|---|---|---|
| mcycle | 225 027 | 6 117 | 27 281 | 173 402 | 4 075 | 2 476 | 25 255 | 50 448 |

Cache đang dùng: `baseline_rtl2`, `baseline_gate_b2_mm_spmv`, `baseline_start_<hash>_rtl2`/`_gate_b2_mm_spmv`/
`_synth_<flow>_p10`.

## Kết quả

| Đợt | Thước đo nhận | Vòng nhận | Fmax | Cycle | Ghi chú |
|---|---|---|---|---|---|
| baseline | | | 47,13 MHz | +0 % | |
| `chia-fmax-r1` (29/10) | Yosys `v2` | 4/5 | → 81,91 MHz | +0,405 % | |
| `chia-fmax-r2` (29/10) | Yosys `v2` | 1/5 | → 84,37 MHz | +0,405 % | |
| `chia-fmax-r3b` (30/10) | Yosys `v2` | 1/5 | → 90,57 MHz | +0,405 % | Vivado đứng yên 67,53 MHz |
| `fmax-r4` (30/10) | Vivado post-route | 1/5 | post-route 61,06 → 63,61 MHz; Yosys `v2` 92,36 MHz | +0,405 % | thêm prefix-OR trong ALU |
| `fmax-r5` (30/10) | Vivado post-route | 0/5 | 63,61 MHz | +0,405 % | tốt nhất +1,11 %, ngang nhiễu P&R |
| `fmax-y1` (31/10) | Yosys `v2` | 0/3 | 92,36 MHz | +0,405 % | dừng ở vòng 3 (hạn mức) |
| `fmax-y2` (01/11) | Yosys `v2` | — | | | dừng ở vòng 1: Yosys treo > 30 phút → thêm `--synth-timeout` |
| `fmax-y3` (01/11) | Yosys `v2` | 0/5 | 92,36 MHz | +0,405 % | `history.md` ghi nhầm patch xuất phát là "bị loại" (đã sửa) |
| `fmax-y4` (02/11) | Yosys `v2` | 0/3 | 92,36 MHz | +0,405 % | tốt nhất +0,4 %; dao động của flow `v2` lớn hơn ngưỡng |

Chi tiết từng vòng của r1–r3b, `fmax-r4`, `fmax-r5` có trong git (README trước commit của ngày 02/11/2026) và REPORT.md.

## Đầu ra: `<out-dir>/<run>/`

```
results.csv            iter, accepted, fmax_mhz, slack_ns, tns_ns, family_score, area_um2, critical_path, cycle_pct,
                       perf, perf_gain_pct, isa_pass, reason, elapsed_min, synth_min
history.md             các vòng của đợt (lý do, sửa đã đo, patch bị loại / ứng viên), cho --history-from
patches/iter<N>.patch  git diff của rocket-chip so với commit gốc (gồm cả bản tốt nhất)
patches/best.patch     bản tốt nhất; best.json
patches/base.patch, patches/cand_iter<N>.patch, cand_iter<N>.delta.patch, candidates.json   (pha sim)
patches/cand_<sim-run>_<N>.patch                                                          (--try-from)
runs/baseline/, runs/start/, runs/iter<N>/, runs/cand_*/
                       prompt.md, agent_response.md, agent_transcript.txt, agent_ab_experiments.jsonl, build.log,
                       timing_*.rpt/txt, synth_stat.txt, yosys_log_tail.txt, sta.log, gate.json, gate_compare.txt,
                       decision.json, fix<k>_*, debug<k>_*
profile/               ChiaProfileCollector.log + timeline html
```

Áp lại một patch (diff so với commit rocket-chip của image, nhánh `chia_artifact`):

```bash
docker cp <out-dir>/<run>/patches/best.patch chia-chipyard-$USER-0:/tmp/best.patch
docker exec chia-chipyard-$USER-0 bash -lc 'cd /home/ray/chipyard/generators/rocket-chip && git checkout -- . && git apply /tmp/best.patch && git diff --stat'
```

## Trạng thái kiểm chứng

- **Chạy thật:** build, gate, flow `v2`, `synth_ab` (rebuild, A/B module và cả tile, bảng họ endpoint), agent sửa
  build và debug, `--empty-retries`, `--start-patch`, `--history-from`, `--synth-timeout`, loại Verilog trùng, loại
  trước gate. Build RocketConfig 1,3–1,7 phút, Yosys `v2` cả tile 3–5 phút, image có Yosys 0.38.
- **Mới kiểm bằng mock (02/11/2026):** `--phase sim`, `--try-from` (`apply_candidate`), nhận theo điểm họ, `synth_ab`
  nhớ Verilog đã đo, prompt `explore_pipeline.md`.
- **Mới kiểm bằng mock (03/11/2026):** khối đã đổi và biến thể, bảng "Blocks already covered", synth sơ bộ ở pha sim,
  `--try-from` đo trước rồi thử theo nhóm, bỏ gate khi ứng viên nằm trên đúng gốc của nó.
- **`sim-1` (xong 03/11/2026, code trước 03/11/2026, 6 vòng, khoảng 2,2 giờ):** 5 vòng qua gate (1, 2, 3, 5, 6), cycle
  +0,405 % (bằng điểm xuất phát), vòng 4 bị loại (`fastLoadWord = false`: qsort +7,6 %, tổng +3,11 %). Cả 5 ứng viên
  đều ở `fpuOpt.dfma.fma` và cùng tiêu tầng `Pipe` rảnh ở đầu ra FMA (tổng latency vẫn 5), nên loại trừ nhau:
  vòng 1 và 6 chia phép cộng 107 bit (họ `mulAddResult_r`), vòng 2 đặt thanh ghi trước bộ nhân (họ `ppl`/`pph`),
  vòng 3 tách bộ nhân thành 4 tích + CSA, vòng 5 chia `postMul` (họ `roundRawFNToRecFN_io_in_r`). Ba họ FMA này nằm
  trong 4 họ đầu của `v3-start`, nên một ứng viên chỉ gỡ được một họ. Agent vòng 6 tự ghi nhận "rounds 1, 2, 3 and 5
  all targeted the same FMA family": lời nhắc dạng văn bản không đủ, đúng lý do thêm chống lặp bằng code.
- **Flow `v3` chạy thật (`v3-start`, 03/11/2026):** tên đúng dạng `fpuOpt.dfma.fma.x[3]_reg` (không `/`, không `\`),
  bảng họ gom đúng. Điểm xuất phát (`fmax-r4`) ở `v3`: worst slack −1,763 ns, **85,01 MHz** (`v2`: 92,36 MHz). Các họ
  xấu nhất: FMA `mulAddResult_r`/`pph`/`ppl` (−1,76 … −1,63), `core.mem_reg_wdata` (−1,34), FMA
  `roundRawFNToRecFN_io_in_r_sig` (−1,30), `divSqrt` `rem_Z`/`sigX_Z` (−1,04/−1,02). `core.div.remainder` và
  `regfile_ext.Memory` (họ xấu nhất của `v2`) không còn trong 16 họ đầu.
- Tính đúng chức năng chỉ dựa vào riscv-tests và 8 benchmark (như example), không kiểm tương đương.
