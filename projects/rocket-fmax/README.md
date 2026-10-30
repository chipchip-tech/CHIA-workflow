# rocket-fmax: agent tối ưu Fmax của Rocket theo example `timing_opt` của CHIA

Mục tiêu hiện tại (30/10/2026): **đẩy Fmax của `RocketTile` đo bằng Yosys + OpenSTA (sky130_fd_sc_hd) càng cao
càng tốt** (phần synth của mốc ASIC), chu kỳ 10 ns, mất tối đa 3 % cycle so với RocketConfig gốc.

| Mốc | Thước đo | Trạng thái |
|---|---|---|
| ASIC, phần synth (hiện tại) | Yosys `synth -flatten` + `abc -D` + OpenSTA, sky130hd tt_025C_1v80, SRAM là hộp đen | r1–r3b: 47,13 → 90,57 MHz. Loop chạy lại theo Yosys từ 30/10/2026 (mới kiểm bằng mock) |
| ASIC post-route | ORFS sky130 | Chưa bắt đầu |
| FPGA | Vivado post-route, Arty A7-100T (`xc7a100tcsg324-1`) | **Tạm bỏ** (30/10/2026) ở 63,61 MHz sau `fmax-r4`/`fmax-r5`; code Vivado đã gỡ, còn trong git (commit `d2a1ac0`) |

Loop chạy theo `examples/timing_opt` của CHIA. Agent là Claude Code (Sonnet, effort medium). Các đợt r1–r3b
(29–30/10) nhận/loại theo Yosys; `fmax-r4`, `fmax-r5` (30/10) theo Vivado post-route (bảng "Kết quả"). Cuối ngày 30/10
người dùng quyết quay lại Yosys: loop giữ các phần thêm ở giai đoạn Vivado (bỏ qua Verilog đã đo, A/B so slack theo
họ endpoint, `--history-from`), thước đo và `synth_ab` dùng lại Yosys + OpenSTA. Lịch sử trước khi bắt đầu lại có
trong git, trước commit `ab43d94`.

## Một vòng

```
reset rocket-chip về bản tốt nhất
  ──► 1 phiên agent dài (--llm-timeout): đọc báo cáo OpenSTA, sửa Chisel bằng chipyard_bash,
      tự A/B một module bằng synth_ab (make verilog + Yosys/OpenSTA, start/poll)
  ──► kiểm phạm vi (chỉ src/main/scala của rocket-chip, không file *Config*)
  ──► build RocketConfig (lỗi compile → agent sửa build, --build-fixes)
  ──► Verilog RocketTile trùng một bản đã đo (bản tốt nhất, vòng trước) → loại ngay
  ──► Yosys + OpenSTA RocketTile (3–4 phút)
        Fmax ≤ hiệu năng bản tốt nhất × (1 + --min-perf-gain) → loại luôn, không chạy gate
        (kể cả cycle bằng baseline cũng không thắng được)
  ──► gate (riscv-tests rv64ui/um/ua/uf/ud-p-* + 8 benchmark, Verilator ×4, ~19 phút)
        test fail → agent debug (--debug-retries), build + đo lại
  ──► nhận nếu: mọi test pass, minstret mọi benchmark = baseline, chi phí cycle ≤ 1 + --max-ipc-loss,
                Fmax / chi phí cycle > bản tốt nhất × (1 + --min-perf-gain)
      loại thì: revert về bản tốt nhất, đưa lý do vào prompt vòng sau (và history.md của đợt)
```

Chi phí cycle là trung bình nhân của `mcycle / mcycle baseline` trên các benchmark, tính so với RocketConfig gốc
(cộng dồn qua các vòng). Ở r1–r3b một vòng đầy đủ 20–31 phút; vòng bị loại ở bước synth giờ không tốn thêm 19 phút gate.

`--min-perf-gain` mặc định 1 % (như r1–r3b). Yosys cho cùng kết quả trên cùng Verilog, nhưng synth cả tile (flatten,
một lần ABC) xê dịch 1–2 ns khi sửa một khối khác.

## Đối chiếu với example `timing_opt`

| | Example (BOOM) | Ở đây (Rocket) |
|---|---|---|
| Điểm xuất phát | `seed_flow`: RTL chưa sửa | `--start-patch` (đợt trước), hoặc RocketConfig gốc |
| Mỗi lần chạy | 1 phiên agent → 1 nhánh con | 1 phiên agent mỗi vòng, `--iters` vòng liền |
| Chọn bản tiếp | Cây nhánh SQLite, người chọn bản cha | Tuyến tính, tự nhận/loại |
| Model | Opus, effort max, 60 phút | Sonnet, effort medium (gói Pro), `--llm-timeout` |
| A/B module | `timing_experiment` (Genus, start/poll) | `synth_ab` (Yosys + OpenSTA, start/poll; bảng slack theo họ endpoint) |
| Đo cả tile | Genus | Yosys + OpenSTA sky130hd |
| Gate | Verilator suite, `debug_failure` tối đa 3 lần | riscv-tests + benchmark, agent debug `--debug-retries` |
| Mất IPC | Prompt ironlaw: tuỳ ý nếu thắng | Tối đa `--max-ipc-loss` (3 %) |
| Prompt | `improve_timing_ironlaw.md` | `prompts/improve_timing.md` (Rocket + sky130) |
| Kiểm tương đương | Không | Không (cycle được đổi) |

## Đo timing (`synth_sta` trong `fmax_nodes.py`)

- Yosys: `synth -flatten -top RocketTile` → `dfflibmap` → `abc -D <chu kỳ> -constr` (driver `buf_1`, load 5 như ORFS)
  trên `sky130_fd_sc_hd__tt_025C_1v80.lib` (bỏ các cell `probe`/`lpflow`). Chu kỳ phải đặt ở mức mục tiêu: chu kỳ rộng
  làm ABC dừng sớm và Fmax đo được thấp hơn.
- OpenSTA: `create_clock` 10 ns trên `clock`, I/O delay 0; SRAM `*_ext` là hộp đen (chân không được tính timing).
  `EICG_wrapper` được thay bằng dây nối thẳng. Fmax = 1000 / (chu kỳ − worst slack).
- Báo cáo: 10 đường xấu nhất (`timing_paths.rpt`), 2000 endpoint xấu nhất (`timing_endpoints.rpt`, gom theo thanh ghi
  thành `timing_families.txt`), diện tích (`synth_stat.txt`). Yosys chạy trong process group riêng.
- Verilog lấy từ chính lần build sim (`collect_generated_src=True`).

## File

| File | Việc |
|---|---|
| `rocket_fmax.py` | Driver: baseline, điểm xuất phát, các vòng, nhận/loại, ghi kết quả, profiler |
| `fmax_nodes.py` | Các `@ChiaFunction`: chipyard (ELF, git, kiểm phạm vi, đọc Chisel), `extract_tile_rtl`, `run_gate`, `synth_sta` (Yosys + OpenSTA); lớp `SalvageClaudeLLM`; định vị đường găng trong Verilog/Chisel |
| `ab_tool.py` | Tool MCP `synth_ab`: `rebuild_verilog`, `list_modules`, `start_ab`, `ab_result` |
| `prompts/improve_timing.md` | Prompt phiên tối ưu (sky130, Yosys + OpenSTA) |
| `prompts/fix_failure.md` | Prompt agent debug khi test fail |
| `prompts/fix_build.md` | Prompt agent sửa lỗi build |
| `exp.yaml` | Cache và bypass (RTL, gate, synth của baseline/start) |
| `cluster.yaml` | 4 worker: `chipyard`, `verilator_run` ×4, `claude_creds`, `eda` |
| `docker/EdaDockerfile` | Image `chia-eda-sky130`: chia-cacti (OpenSTA) + Yosys + liberty sky130hd (ghim commit, sha256) |

## Chuẩn bị (một lần)

Image `eda` (bỏ qua bước build nếu `docker image inspect chia-eda-sky130:latest` vẫn còn), cluster 4 worker, bỏ worker
Vivado:

```bash
conda activate chia_env
cd ~/CHIA-workflow/projects/rocket-fmax/docker
docker image inspect chia-eda-sky130:latest >/dev/null 2>&1 || docker build -f EdaDockerfile -t chia-eda-sky130:latest .
docker rm -f chia-vivado-$USER-0 chia-opencode-$USER-0 2>/dev/null   # worker cũ, không còn trong cluster.yaml
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia up ~/CHIA-workflow/projects/rocket-fmax/cluster.yaml -y
chia status        # chipyard 1, verilator_run 4, claude_creds 1, eda 1
docker exec chia-eda-$USER-0 bash -lc 'yosys -V && which sta && ls -l $SKY130HD_LIB'
```

Vivado ở `~/tools/Xilinx` và image `chia-vivado` không còn dùng; giữ hay xoá tuỳ người dùng (cài lại theo README ở
commit `d2a1ac0`). Worker `hello_claude` mount `~/.claude` của host: đăng nhập `claude` trên host trước (gói Pro được).

## Chạy

Luôn submit **từ thư mục này**: cache lưu dataclass của `fmax_nodes`, nên lúc đọc lại phải import được module này.

```bash
cd ~/CHIA-workflow/projects/rocket-fmax
chia job submit --no-wait --submission-id fmax-y1 --working-dir . -- \
  python rocket_fmax.py --start-patch ~/rocket-fmax-v2/fmax-r4/patches/best.patch --run-name fmax-y1
chia job logs -f fmax-y1          # dừng: chia job stop fmax-y1
```

Mặc định đã là cấu hình của project: `--period-ns 10 --synth-flow v2 --extra-bench mm spmv --exclude-isa
rv64ui-p-ma_data --model sonnet --effort medium --iters 5 --max-ipc-loss 0.03 --min-perf-gain 0.01
--out-dir ~/rocket-fmax-v2`.

- Mỗi lần chạy dùng `--run-name` mới: câu trả lời agent được cache theo `agent_<run-name>_iter<i>_*`.
- Đợt sau tiếp từ bản tốt nhất của đợt trước: `--start-patch ~/rocket-fmax-v2/<run>/patches/best.patch
  --history-from ~/rocket-fmax-v2/<run>` (agent đọc các vòng của đợt trước, đỡ thử lại sửa đã loại). Điểm xuất
  phát được build, gate và synth một lần, cache theo `baseline_start_<hash>_rtl2`, `..._gate_b2_mm_spmv`,
  `..._synth_v2_p10`. Không có `--start-patch` thì baseline là điểm xuất phát (synth cache theo
  `baseline_synth_v2_p10`, đã có từ r1).
- `--baseline-only`: không chạy vòng nào, chỉ synth baseline (và `--start-patch` nếu có).
- Khi Fmax vượt 100 MHz (slack dương ở 10 ns), hạ `--period-ns` (vd. 8) để ABC tối ưu tiếp; tag cache đổi theo.
- Gói Pro: một phiên dài tốn nhiều token, nên chạy theo đợt khoảng 5 vòng. Gặp "You've hit your limit" thì loop
  chờ tới giờ reset rồi gọi lại (`--quota-retries`, bỏ nếu xa hơn `--quota-max-wait`).
- Tuỳ chọn khác: `--llm-timeout 1800`, `--debug-retries 2`, `--empty-retries 1` (phiên mới khi agent không sửa gì),
  `--build-fixes 2`, `--fix-timeout 900`, `--make-jobs 16`, `--build-timeout 3600`, `--cache-dir ~/chia_cache`.
- `rv64ui-p-ma_data` fail ngay trên baseline (Rocket không hỗ trợ truy cập lệch địa chỉ), nên mặc định bị loại khỏi gate.

## Đo post-route lần đầu (Bước 0, `viv-impl2`; mốc FPGA, tạm bỏ)

`viv-impl2` (30/10/2026, `--vivado-impl-probe` của code cũ, `xc7a100tcsg324-1`, 10 ns, 3 chiến lược, 2 lần song song, RAM gần 10 GB): BRAM được suy ra (48 BRAM,
104–107 LUTRAM).

| Điểm | Yosys | Vivado synth | `default` | `explore` | `extratiming` | Đường găng post-route tốt nhất |
|---|---|---|---|---|---|---|
| baseline | 47,13 | 52,58 | 56,15 (5,5 ph) | 58,14 (11,6 ph) | **58,67** (10,9 ph) | `dcache.s2_req_addr` (replica) → `core.mem_reg_wdata` |
| start (r3b) | 90,57 | 56,19 | 56,13 (5,6 ph) | **61,06** (9,9 ph) | 58,89 (9,7 ph) | `dcache.s2_data` → `core.mem_reg_wdata` |

(MHz post-route, phút mỗi chiến lược; synth thêm ~2,4 phút.) Ba đợt Yosys (+92 %) chỉ cho +4,1 % post-route. Ba chiến
lược của cùng một netlist chênh tới 5 MHz (~8 %), nên best-of-3 vẫn nhiễu cỡ vài %. Các đường găng khác: `fpuOpt.sfma`
→ `fpuOpt.regfile` (baseline `explore`), `dcache.s2_req_size` → `core.mem_reg_wdata`. Mốc 1 cần slack từ −6,38 ns về 0
(chu kỳ đường găng giảm ~39 %).

## Baseline (RocketConfig gốc, 10 ns, đo thật 29/10/2026)

| Worst slack | Đường găng | Fmax | Gate |
|---|---|---|---|
| −11,2161 ns | 21,216 ns, trong `core.div` | 47,13 MHz | ISA 106/107 (trừ `ma_data`), 18,9 phút |

| Benchmark | dhrystone | median | multiply | qsort | towers | vvadd | mm | spmv |
|---|---|---|---|---|---|---|---|---|
| mcycle | 225 027 | 6 117 | 27 281 | 173 402 | 4 075 | 2 476 | 25 255 | 50 448 |

Cache hiện dùng: `baseline_rtl2`, `baseline_gate_b2_mm_spmv`, `baseline_synth_v2_p10` (cache chỉ phân biệt theo tag). Fmax ở bảng này là Yosys + OpenSTA sky130hd.

## Kết quả

Các đợt r1–r3b nhận/loại theo Yosys + OpenSTA sky130hd (mức synth). `fmax-r4`, `fmax-r5` nhận/loại theo Fmax
post-route Vivado (Artix-7 `xc7a100tcsg324-1`, hiệu năng = Fmax post-route / chi phí cycle, ngưỡng 2 %); mốc FPGA
tạm bỏ sau `fmax-r5`, các đợt tiếp theo quay lại Yosys.

| Đợt | Vòng nhận | Fmax | Chi phí cycle | Ghi chú |
|---|---|---|---|---|
| baseline | | 47,13 MHz | 1,000 | đường găng `core.div` |
| `chia-fmax-r1` (29/10/2026) | 4/5 | 47,13 → 59,80 → 72,94 → 79,42 → **81,91 MHz** | +0,405 % | hiệu năng +73,1 %; diện tích +1,5 %; vòng 5 agent không sửa gì (0,3 phút) |
| `chia-fmax-r2` (29/10/2026) | 1/5 | 81,91 → **84,37 MHz** | +0,405 % | hiệu năng 81,58 → 84,03 (+3,0 %); diện tích +1,5 % so với baseline; vòng 2–5 loại |
| `chia-fmax-r3b` (30/10/2026) | 1/5 | 84,37 → **90,57 MHz** | +0,405 % | hiệu năng 84,03 → 90,20 (+7,35 %); diện tích −0,1 % so với baseline; Vivado không đổi (67,53 MHz) |
| `fmax-r4` (30/10/2026, **post-route Vivado**) | 1/5 | 61,06 → **63,61 MHz** | +0,405 % | hiệu năng 60,82 → 63,36 (+4,18 %); baseline post-route 58,67 MHz; LUT 29 484, 48 BRAM, 30 DSP |
| `fmax-r5` (30/10/2026, post-route Vivado) | 0/5 | 63,61 (không đổi) | +0,405 % | vòng tốt nhất 64,32 MHz, hiệu năng +1,11 % (< ngưỡng 2 %); `synth_design` đứng yên 62,94 MHz ở mọi điểm |

Chi tiết `chia-fmax-r1` (gate riscv-tests pass ở mọi vòng nhận):

| Vòng | Fmax (MHz) | Worst slack (ns) | Cycle | Hiệu năng | Đường găng sau vòng | Phút |
|---|---|---|---|---|---|---|
| 1 | 59,80 | −6,72 | +0 % | +26,9 % | `fpuOpt.dfma` (mulAdd) | 20,3 |
| 2 | 72,94 | −3,71 | +0,405 % | +21,5 % | `dcache.s2_req_addr` → `core.mem_reg_wdata` | 22,8 |
| 3 | 79,42 | −2,59 | +0,405 % | +8,9 % | `fpuOpt.ifpu` | 21,1 |
| 4 | 81,91 | −2,21 | +0,405 % | +3,1 % | `dcache.s2_req_addr` → `core.mem_reg_wdata` | 28,9 |
| 5 | loại | | | | agent không thay đổi gì | 0,3 |

Chi tiết `chia-fmax-r2` (xuất phát từ `best.patch` của `chia-fmax-r1`, gate lại điểm xuất phát):

| Vòng | Fmax (MHz) | Worst slack (ns) | Cycle | Hiệu năng | Đường găng / lý do loại | Phút |
|---|---|---|---|---|---|---|
| 1 | 84,37 | −1,85 | +0,405 % | +3,0 % | `fpuOpt.dfma.in_in2` → `mulAddResult_pph_pipe_b` | 27,8 |
| 2 | loại | −1,85 | | | patch khác iter1 nhưng WNS/TNS trùng iter1 tới 4 chữ số | 9,8 |
| 3 | loại | −1,85 | | | như vòng 2 | 10,8 |
| 4 | loại | | | | agent không thay đổi gì | 9,6 |
| 5 | loại | | | | không sửa gì; phiên retry thử 1 sửa (dfma cắt 4 lát, A/B tile −2,05 ns), tự hoàn tác | 8,7 |

Chi tiết `chia-fmax-r3b` (xuất phát từ `best.patch` của `chia-fmax-r2`, `--empty-retries 2`, prompt retry liệt kê
các sửa đổi đã đo; `chia-fmax-r3` trước đó dừng tay sau 2 vòng không sửa). Cột Vivado đo song song, không dùng để nhận/loại:

| Vòng | Fmax Yosys (MHz) | Worst slack (ns) | Vivado (MHz) | Đường găng Yosys / lý do loại | Phút |
|---|---|---|---|---|---|
| start | 84,37 | −1,85 | 67,53 | `fpuOpt.dfma` | |
| 1 | loại | | | agent không thay đổi gì | 15,4 |
| 2 | loại | | | agent không thay đổi gì | 13,6 |
| 3 | 90,57 | −1,04 | 67,53 | `core.div.divisor` → `core.div.remainder` (gate pass, hiệu năng +7,35 %) | 31,0 |
| 4 | loại (80,09) | −2,49 | 68,73 | Fmax thấp hơn iter3; đường găng `dcache.s2_req_addr` → `core.mem_reg_wdata` | 13,7 |
| 5 | loại | | | agent không thay đổi gì so với iter3 | 14,9 |

Chi tiết `fmax-r4` (xuất phát từ `best.patch` của `chia-fmax-r3b`; Fmax post-route là chiến lược tốt hơn trong
`explore`/`extratiming`; `synth` = Fmax của `synth_design`):

| Vòng | Post-route (MHz) | synth (MHz) | Cycle | Hiệu năng | Đường găng post-route / lý do loại | Phút (P&R) |
|---|---|---|---|---|---|---|
| start | 61,06 (explore; extratiming 58,89) | 56,19 | +0,405 % | | `dcache.s2_data` → `core.mem_reg_wdata` | (20,7) |
| 1 | **63,61** (explore; extratiming 63,40) | 62,94 | +0,405 % | +4,18 % | `frontend.icache.s2_tag_hit_4` → `frontend.fq.elts_0_pc` (CE) | 34,2 (24,6) |
| 2 | loại | | +4,071 % | | tốn quá nhiều cycle (> +3 %), place + route bị huỷ | 26,9 |
| 3 | loại: 63,05 (extratiming; explore 62,60) | 63,57 | +1,907 % | −2,34 % | `frontend.icache.s2_tag_hit_5` → `core.ex_reg_rs_msb_0` (R); synth tốt hơn iter1 nhưng post-route kém hơn | 36,6 (27,3) |
| 4 | loại | | | | agent không thay đổi gì (cả phiên retry) | 12,4 |
| 5 | loại: 63,05 | 63,57 | +1,907 % | −2,34 % | patch trùng y nguyên vòng 3; nay loop bỏ qua Verilog đã đo | 32,9 (27,2) |

Vòng 1 (agent): 2 sửa, đo bằng `synth_ab` cả tile. `fastLoadWord = false` làm tệ đi (−0,635 ns, hoàn tác); đếm
trailing zero của ALU bằng prefix-OR thay `PriorityEncoderOH - 1` (đường 32–34 mức logic) được +1,909 ns synth
(−7,797 → −5,888 ns), giữ lại. Vòng 4: agent nhắm đúng đường post-route (icache → fq, 19 mức logic, qua FP decoder
vào hazard/stall), sửa `fp_hazard_targets`, nhưng A/B báo delta 0,000 vì đường FPU `divSqrt → regfile` là xấu nhất ở
cả hai bên; agent không so được họ `fq` của parent nên bỏ cuộc sau 1 sửa. Sau r4: `synth_ab` báo slack từng họ
endpoint ở cả parent và child.

Chi tiết `fmax-r5` (xuất phát từ `best.patch` của `fmax-r4`; điểm xuất phát chính là iter1 của r4):

| Vòng | Post-route (MHz) | synth (MHz) | Cycle | Hiệu năng | Đường găng post-route / lý do loại | Phút (P&R) |
|---|---|---|---|---|---|---|
| start | 63,61 (explore; extratiming 63,40) | 62,94 | +0,405 % | | `frontend.icache.s2_tag_hit_4` → `frontend.fq.elts_0_pc` (CE) | |
| 1 | loại | | +4,071 % | | tốn quá nhiều cycle (cùng mức +4,071 % với vòng 2 của r4), place + route bị huỷ | 22,3 |
| 2 | loại: 63,93 (extratiming; explore 62,65) | 62,94 | +0,405 % | +0,49 % | `core.div._prod_T_40__8` (DSP) → `core.div.remainder` | 27,7 (21,4) |
| 3 | loại: 64,30 (extratiming; explore 62,27) | 62,94 | +1,718 % | −0,23 % | như vòng 2 | 27,9 (22,0) |
| 4 | loại: 63,23 (explore; extratiming 63,07) | 62,94 | +0,405 % | −0,61 % | `frontend.fq.elts_3_data` → `core.ex_reg_btb_resp_entry` (CE) | 30,0 (23,3) |
| 5 | loại: 64,32 (extratiming; explore 63,30) | 62,94 | +0,404 % | +1,11 % | như vòng 2 | 28,5 (21,6) |

Nhận xét r5: worst slack `synth_design` −5,888 ns (họ FPU `divSqrt` → `regfile`) giống hệt ở mọi điểm, dù TNS synth
khác nhau (−9 892 … −10 314 ns), tức không vòng nào động tới họ này. Hai chiến lược trên cùng netlist chênh 0,3–3,2 %
(vòng 3: 62,27 và 64,30), các vòng có cycle gần như không đổi rơi trong 63,23–64,32 MHz: mức tăng lớn nhất (+1,11 %)
ngang mức nhiễu place + route. Đường găng post-route chuyển qua lại giữa `core.div` (DSP → `remainder`) và frontend
(`icache`/`fq`). Vòng 1 lặp lại một sửa tốn +4,071 % cycle đã bị loại ở r4, vì lịch sử không truyền qua đợt mới.
Không vòng nào trùng Verilog (guard không kích hoạt). Vòng 22–30 phút (place + route 21–23).

Nhận xét: vòng đầy đủ 33–37 phút (agent + build ~10 phút, place + route 25–27 phút); post-route lần đầu không còn
dính `dcache` → `core.mem_reg_wdata`, đường găng chuyển sang frontend (icache tag hit → fetch queue); 2000 endpoint
âm, TNS ~ −10 000 ns, nên còn nhiều họ đường cách mục tiêu ~5,7 ns.

Yosys và Vivado không cùng xu hướng (lý do đổi mốc sang Vivado post-route): vòng 3 tăng 7,35 % theo Yosys nhưng Vivado giữ nguyên 67,53 MHz (đường găng
Vivado vẫn `dcache.s2_data` → `core.mem_reg_wdata` từ baseline). Vòng 4 bị Yosys loại nhưng Vivado lại tăng
(68,73 MHz, LUT +3,7 %, DSP 30 → 17).

## Đầu ra: `<out-dir>/<run>/`

```
results.csv            iter, accepted, fmax_mhz, slack_ns, tns_ns, area_um2, critical_path, cycle_pct, perf,
                       perf_gain_pct, isa_pass, reason, elapsed_min, synth_min
history.md             các vòng của đợt (lý do loại, sửa đã đo, patch bị loại), cho --history-from của đợt sau
patches/iter<N>.patch  git diff trong generators/rocket-chip so với commit gốc (gộp cả bản tốt nhất trước đó)
patches/best.patch     bản tốt nhất (Fmax / chi phí cycle); best.json (ghi khi job chạy hết)
runs/baseline/         gate.json, cycles.csv, build.log, rtl_info.json (+ báo cáo synth khi baseline là điểm xuất phát)
runs/start/, runs/iter<N>/
                       prompt.md, agent_response.md, agent_transcript.txt, agent_ab_experiments.jsonl,
                       build.log, timing_paths.rpt, timing_endpoints.rpt, timing_families.txt, synth_stat.txt,
                       yosys_log_tail.txt, sta.log, gate.json, gate_compare.txt, decision.json,
                       fix<k>_* (sửa build), debug<k>_* và debug<k>/ (agent debug)
profile/               ChiaProfileCollector.log + timeline html
```

Áp lại một patch (diff so với commit rocket-chip của image, nhánh `chia_artifact`):

```bash
docker cp <out-dir>/<run>/patches/best.patch chia-chipyard-$USER-0:/tmp/best.patch
docker exec chia-chipyard-$USER-0 bash -lc 'cd /home/ray/chipyard/generators/rocket-chip && git checkout -- . && git apply /tmp/best.patch && git diff --stat'
```

## Trạng thái kiểm chứng

- **Chạy thật:**
  - Yosys + OpenSTA (image `chia-eda-sky130`, worker `eda`): baseline 10 ns với `--extra-bench mm spmv`
    (`fmax-chia-2`, 29/10/2026) và các đợt r1–r3b (bảng "Kết quả"): gate, nhận/loại theo chi phí cycle, agent debug,
    `--empty-retries`, `--start-patch`, `SalvageClaudeLLM`, sửa build, `synth_ab` bản Yosys đều đã chạy. Ghi nhớ: build
    RocketConfig 1,3–1,7 phút, Yosys cả tile ~3–4 phút, image có Yosys 0.38.
  - Giai đoạn Vivado (đã gỡ, 30/10/2026): `viv-impl2`, `fmax-r4`, `fmax-r5` (số liệu ở trên). Từ đó giữ lại: cấu trúc
    loop một chế độ, `synth_ab` chạy được trong phiên agent (`rebuild_verilog` 55–79 s), agent đọc đường găng đã định vị.
- **Mới kiểm bằng mock (code Yosys ngày 30/10/2026, chưa chạy thật):**
  - Synth trước gate, loại sớm vòng không thể thắng; gate fail → agent debug; `--baseline-only` với `--start-patch`;
    `--history-from`, `history.md`.
  - Bỏ qua vòng có Verilog `RocketTile` trùng một bản đã đo (có trong `fmax-r5` nhưng chưa vòng nào trùng).
  - `synth_ab` so slack từng họ endpoint của parent và child trên báo cáo OpenSTA.
  - Yosys trong process group riêng (huỷ task thì tắt Yosys).
- Tính đúng chức năng chỉ dựa vào riscv-tests và 8 benchmark (như example), không kiểm tương đương.
