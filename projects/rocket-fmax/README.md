# rocket-fmax: agent tối ưu Fmax của Rocket theo example `timing_opt` của CHIA

Mục tiêu: **Fmax của `RocketTile` (Chipyard `RocketConfig`) > 100 MHz** ở chu kỳ 10 ns, đo bằng Yosys + OpenSTA trên
`sky130_fd_sc_hd` (phần synth của nhánh ASIC), chi phí cycle tối đa +3 % so với RocketConfig gốc. Tốt nhất hiện có:
**92,36 MHz** (flow `v2`, patch của `fmax-r4`, cycle +0,405 %). Ở flow `v3`: **93,88 MHz**, cycle +2,685 % (`asic-4`,
patch `~/rocket-fmax-v2/asic-4/patches/best.patch`, sha256 `b16798766e65…`), từ 85,01 → 87,84 (`asic-1`) → 91,81 (`asic-2`) → 93,17 (`asic-3`) → 93,88 MHz. Báo cáo các đợt Yosys: [REPORT.md](REPORT.md).

## Pha (theo `docs/workflow-phases.md`)

| Pha | Lệnh | Thước đo | Trạng thái |
|---|---|---|---|
| Simulate | `--phase sim` | gate (riscv-tests + 8 benchmark), chi phí cycle; agent thêm tầng pipeline, mỗi vòng qua gate thành một ứng viên (kèm synth sơ bộ cả tile, khối đã đổi) | `sim-1` xong (code cũ): 5 ứng viên, cả 5 trên FMA; `sim-2` xong (03/11/2026, code mới, chạy thật): 6 ứng viên ở 5 khối khác nhau, synth sơ bộ chạy đúng |
| ASIC, synth | `--phase asic` (mặc định), `--try-from <đợt sim>` | Yosys + OpenSTA sky130hd tt_025C_1v80, SRAM là hộp đen; gate như trên | r1–r4, y1–y4 đã chạy (flow `v2`); `asic-1` (`v3`, `--try-from sim-1`) chạy thật 03/11/2026: 85,01 → 87,84 MHz; `asic-2` (`--try-from sim-2 --iters 0`): 87,84 → 91,81 MHz; `asic-3` (5 vòng agent): 91,81 → 93,17 MHz; `asic-4`: → 93,88 MHz |
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
- **`asic-1` chạy thật (03/11/2026, `--try-from sim-1`, dừng tay ở vòng agent 1):** `--try-from` đo trước rồi thử theo
  nhóm, nhận theo điểm họ, bỏ gate khi ứng viên nằm trên gốc của nó: chạy đúng. Năm ứng viên thành một nhóm
  (`fpuOpt.dfma.fma`). Đo trên start (điểm họ −10,820): vòng 1, 2, 3 cùng 88,16 MHz (−1,343 ns), điểm họ −7,48 / −8,35 /
  −9,24; vòng 6 87,09 MHz, −8,53; vòng 5 87,84 MHz (−1,385 ns), điểm họ **−6,32** → nhận (không gate, 7,7 phút), bốn
  ứng viên kia BỎ QUA. Đường găng mới: `dcache.s2_req_addr → core.mem_reg_wdata` (load bypass). Vòng 5 thay
  `postMul` của cả ba FMA (h/s chỉ đổi module, không thêm tầng), nên khối đã đổi có cả `hfma`, `sfma`. Chênh Fmax
  giữa vòng 5 và vòng 1 (0,04 ns) nhỏ hơn mức dao động synth; điểm họ chọn vòng 5 vì nó gỡ nhiều họ FMA hơn.
- **`sim-2` chạy thật (03/11/2026, từ `cand_iter5.patch`, 6 vòng, mỗi vòng 19–20 phút gồm synth sơ bộ 1,6 phút):**
  bảng "Blocks already covered", synth sơ bộ, nhãn `TARGET`/`VARIANT OF` chạy đúng. Sáu vòng qua gate, ở năm khối khác
  nhau (sim-1 cả 5 ở một khối). Synth sơ bộ so với start (87,84 MHz, điểm họ −6,322):
  vòng 1 `core.div` chia mỗi bước chia làm 2 cycle (+0,522 %, 87,84, −6,57); vòng 2 `dcache` bản sao đảo của
  `s2_req.addr(2)` để chia fanout (+0,405 %, **88,79**, −5,92); vòng 3 `fpuOpt` bỏ enable của `ex_reg_inst/ctrl`
  (87,82, −6,93); vòng 4 `core` cho `LW` đi đường bypass chậm và lấy `data_raw` (+2,655 %, qsort +7,4 %, **91,81**,
  −5,52); vòng 5 `fpuOpt` thanh ghi trước `divSqrt` (87,84, −7,01); vòng 6 `dfmaLatency` 5 → 6 (+0,752 %, spmv +6,2 %,
  87,84, −7,27; agent tự ghi `VARIANT OF: round 5` và dẫn số synth sơ bộ của vòng 5). Vòng 2 thiếu `TARGET` vì agent
  viết dòng đó dính sau một câu (đã sửa cách đọc, xem dưới).
- **`asic-2` chạy thật (03/11/2026, `--try-from sim-2 --iters 0`, khoảng 1,4 giờ):** nhóm [4, 1] (`core` chứa
  `core.div`), [2], [3, 5, 6] (`fpuOpt`). Nhận vòng 4: 87,84 → **91,81 MHz** (−0,892 ns, TNS −297 → −114 ns), cycle
  +2,655 %, hiệu năng +2,23 %, không gate (11 phút). Vòng 1 BỎ QUA (cùng nhóm). Trên vòng 4: vòng 2 Fmax không đổi
  (điểm họ +0,13), vòng 3 91,38 MHz, vòng 5 không đổi → LOẠI (cần > +1 %); vòng 6 cycle +3,076 % → LOẠI (trần 3 %).
  Đường găng mới: `core.ex_reg_rs_lsb_1 → core.mem_reg_wdata` (bypass → ALU ở tầng ex). Ngân sách cycle còn 0,345 %.
- **Hai lỗi lộ ra ở `sim-2`/`asic-2`, đã sửa 03/11/2026 (chưa chạy thật):** (1) `--try-from` ghi "Patch đã bị loại" bằng
  patch cả cây (gồm cả patch start: ALU, AMOALU, `MulDiv`...), cắt ở 4000 ký tự nên mất phần của ứng viên và bảo agent
  sau đừng lặp lại chính điểm xuất phát; nay ghi `cand_iterN.delta.patch` (chỉ phần ứng viên). (2) Dòng `TARGET:` /
  `VARIANT OF:` nay đọc được ở bất kỳ đâu trên dòng (chữ hoa).
- **`asic-3` chạy thật (04/11/2026, `--start-patch asic-2/patches/best.patch --iters 5`, khoảng 1,6 giờ):** cycle giữ
  +2,655 % ở mọi vòng (chỉ sửa logic). Vòng 1 NHẬN: mux bypass `ex_rs` thành one-hot AND-OR (chọn sẵn ở decode,
  `RocketCore.scala`), 91,81 → **92,84 MHz**, hiệu năng +1,12 %; đường găng chuyển sang `core.div`. Vòng 2 NHẬN theo
  điểm họ (+1,376 ns, TNS −81 → −20 ns): `-result` trong `MulDiv` (`negated_remainder`) thành XOR với cây OR tiền tố,
  **93,17 MHz** (+0,35 %); đường găng chuyển sang FMA `mulAddResult_ppl → mulAddResult_r`. Vòng 3, 4: agent thử
  carry-save, carry-select, gộp `mC` vào `ppl`, tách cộng ba toán hạng thành module riêng; đều xấu hơn hoặc lời quá ít,
  agent tự revert. Vòng 5 LOẠI: cộng cuối của FMA trong module riêng `FmaFinalAdd2` (`+&` thường, ABC tối ưu riêng),
  họ `mulAddResult_r` −0,734 → −0,536 nhưng đường găng chuyển sang `core.div.remainder[3] → [129]`: 93,38 MHz
  (+0,24 %), điểm họ +0,124 < ngưỡng 0,15. Các họ còn lại sát nhau quanh −0,7 ns (FMA, `core.div.remainder`), nên
  sửa một họ không đủ +1 %.
- **Hai lỗi lộ ra ở `asic-3`, đã sửa 04/11/2026 (chưa chạy thật):** (1) Ở vòng agent thường (không chỉ `--try-from`),
  "Patch đã bị loại" vẫn là patch cả cây so với rocket head, nên lại hiện patch start (ALU, AMOALU, `MulDiv`) và mất phần
  của vòng; nay ghi phần sửa của vòng trên bản tốt nhất (`round_diff`, tính ngay sau lượt agent). (2) Diff trong "Các
  sửa đổi agent đã đo bằng synth_ab" bị cắt lấy đuôi nên bắt đầu giữa dòng, mất tên file; nay giữ đầu và đuôi, cắt
  giữa (`clip_mid`).
- **`asic-4` chạy thật (04/11/2026, `--start-patch asic-3/patches/best.patch --iters 5 --min-family-gain 0.1`):**
  **chạy bằng `rocket_fmax.py` cũ** (history vẫn nhãn cũ, diff `synth_ab` vẫn cắt đuôi), tức chưa có hai bản sửa của
  `asic-3`. Vòng 1 NHẬN theo điểm họ (+0,531 ns, Fmax 93,17 → 93,09, hiệu năng −0,08 %, worst slack −0,734 → −0,743 trong
  `--worst-tol`): cộng ba toán hạng cuối của FMA (nhánh tách nhân) thành carry-save + Kogge-Stone, và `ex_reg_inst` /
  `ex_reg_ctrl` của FPU thành `RegNext` (bỏ enable); đường găng về `core.ex_reg_rs_oh → mem_reg_wdata`. Vòng 2 LOẠI:
  `InlineInstance` cho `ALU`, 93,38 MHz (+0,32 %) nhưng điểm họ −0,044. Vòng 3 NHẬN theo điểm họ (+0,278): early-out của
  bộ chia tính `Log2`/trừ ở chu kỳ chia đầu vào thanh ghi `eoPos` (mỗi phép chia thêm 1 chu kỳ), **93,88 MHz**, cycle
  +2,655 → +2,685 %, hiệu năng +0,82 %; đường găng `core.div.resHi → remainder[63]`. Vòng 4, 5: tách fanout `resHi`,
  `s_neg_output` hai bước, nhân đôi bộ phủ định; đều xấu hơn, agent tự revert. Cả đợt: 93,17 → 93,88 MHz, hiệu năng
  90,76 → 91,42 (+0,73 %), điểm họ −3,798 → −2,989.
- **Sửa thêm sau `asic-4` (04/11/2026):** khi đọc `--history-from`, khối "Patch đã bị loại" nhãn cũ (cả cây so với
  rocket head) bị bỏ ở mọi vòng có mục "Các sửa đổi agent đã đo bằng synth_ab" (`asic-3` vòng 5, `asic-4` vòng 2), không
  chỉ vòng "agent không thay đổi gì". Đã thử trên hai file history đó: không còn khối cũ, ba mục `synth_ab` giữ nguyên.
- **Giới hạn đã biết của nhóm biến thể:** nhóm theo đường instance (trùng hoặc lồng nhau), nên `core` (sửa
  `RocketCore.scala`) gộp với `core.div` (sửa `Multiplier.scala`) dù hai patch không đụng nhau. Ở `asic-2` không mất gì
  (vòng 1 tự nó không tăng Fmax), nhưng về sau có thể cần xét thêm file nguồn bị sửa.
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
