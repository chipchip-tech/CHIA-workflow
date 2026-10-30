# rocket-fmax: agent tối ưu Fmax của Rocket theo example `timing_opt` của CHIA

Mục tiêu: **Fmax post-route của `RocketTile` > 100 MHz**, chu kỳ 10 ns, mất tối đa 3 % cycle so với RocketConfig gốc.

Mốc (đổi ngày 30/10/2026):

| Mốc | Thước đo | Trạng thái |
|---|---|---|
| 1 (hiện tại) | Vivado **post-route**, `xc7a100tcsg324-1` (Arty A7-100T, -1), out-of-context, SRAM thành BRAM | Bước 0 xong (`viv-impl2`: baseline 58,67, start 61,06 MHz). Bước 1 (loop nhận theo post-route) đã viết, mới kiểm bằng mock |
| 2 | ORFS sky130 post-route | Chưa bắt đầu |

Loop chạy theo `examples/timing_opt` của CHIA. Agent là Claude Code (Sonnet, effort medium). Ba đợt đầu (r1, r2, r3b)
nhận/loại theo Yosys + OpenSTA sky130hd ở mức synth (bảng "Kết quả"). Từ 30/10/2026 (Bước 1) chỉ còn Vivado: nhận/loại
theo Fmax post-route, `synth_ab` của agent đo bằng Vivado `synth_design`. Code Yosys/OpenSTA, worker `eda`, opencode,
`--loop-ab`, kiểm tương đương đã gỡ (còn trong git, trước commit dọn dẹp ngày 30/10/2026). Lịch sử trước khi bắt đầu
lại có trong git, trước commit `ab43d94`.

## Một vòng

```
reset rocket-chip về bản tốt nhất
  ──► 1 phiên agent dài (--llm-timeout): đọc báo cáo post-route, sửa Chisel bằng chipyard_bash,
      tự A/B một module bằng synth_ab (make verilog + Vivado synth_design, start/poll)
  ──► kiểm phạm vi (chỉ src/main/scala của rocket-chip, không file *Config*)
  ──► build RocketConfig (lỗi compile → agent sửa build, --build-fixes)
  ──► Verilog RocketTile trùng một bản đã đo (bản tốt nhất, vòng trước) → loại ngay
  ──► song song: gate (riscv-tests rv64ui/um/ua/uf/ud-p-* + 8 benchmark, Verilator ×4, ~19 phút)
               và Vivado place + route RocketTile (explore, extratiming lần lượt, 25–27 phút)
        test fail → huỷ place + route, agent debug (--debug-retries), build + đo lại
        cycle > 1 + --max-ipc-loss → huỷ place + route, loại
  ──► nhận nếu: mọi test pass, minstret mọi benchmark = baseline, chi phí cycle ≤ 1 + --max-ipc-loss,
                Fmax post-route / chi phí cycle > bản tốt nhất × (1 + --min-perf-gain)
      loại thì: revert về bản tốt nhất, đưa lý do vào prompt vòng sau
```

Chi phí cycle là trung bình nhân của `mcycle / mcycle baseline` trên các benchmark, tính so với RocketConfig gốc
(cộng dồn qua các vòng). Một vòng đầy đủ 33–37 phút ở `fmax-r4` (agent + build ~10, place + route 25–27).

`--min-perf-gain` mặc định 2 %: hai chiến lược place + route trên cùng netlist chênh tới ~8 % (`viv-impl2`), nên
ngưỡng 1 % cũ nằm trong nhiễu.

## Đối chiếu với example `timing_opt`

| | Example (BOOM) | Ở đây (Rocket) |
|---|---|---|
| Điểm xuất phát | `seed_flow`: RTL chưa sửa | `--start-patch` (đợt trước), hoặc RocketConfig gốc |
| Mỗi lần chạy | 1 phiên agent → 1 nhánh con | 1 phiên agent mỗi vòng, `--iters` vòng liền |
| Chọn bản tiếp | Cây nhánh SQLite, người chọn bản cha | Tuyến tính, tự nhận/loại |
| Model | Opus, effort max, 60 phút | Sonnet, effort medium (gói Pro), `--llm-timeout` |
| A/B module | `timing_experiment` (Genus, start/poll) | `synth_ab` (Vivado `synth_design`, start/poll) |
| Đo cả tile | Genus | Vivado place + route, tốt nhất của các chiến lược |
| Gate | Verilator suite, `debug_failure` tối đa 3 lần | riscv-tests + benchmark, agent debug `--debug-retries` |
| Mất IPC | Prompt ironlaw: tuỳ ý nếu thắng | Tối đa `--max-ipc-loss` (3 %) |
| Prompt | `improve_timing_ironlaw.md` | `prompts/improve_timing.md` (Rocket + Artix-7) |
| Kiểm tương đương | Không | Không (cycle được đổi) |

## Đo timing (Vivado, `vivado_nodes.py`)

- `RocketTile` out-of-context, `create_clock` 10 ns trên `clock`, I/O delay 0, clock coi như đến từ BUFG
  (`HD.CLK_SRC BUFGCTRL_X0Y0`). Fmax = 1000 / (chu kỳ − worst slack).
- SRAM `*_ext` là model hành vi Chipyard sinh cho mô phỏng, kèm các module con `split_*_ext` mà chúng gọi
  (`rtl.sram_models`), để Vivado suy ra BRAM/LUTRAM. `EICG_wrapper` được thay bằng dây nối thẳng.
- `vivado_impl`: `synth_design` → checkpoint → mỗi chiến lược `--vivado-impl-strategies` (mặc định `explore`,
  `extratiming`; `default` không lần nào tốt nhất ở `viv-impl2`) chạy `opt_design` → `place_design` →
  `phys_opt_design` → `route_design` (→ `phys_opt_design` nếu còn âm), tối đa `--vivado-impl-parallel` lần cùng
  lúc (mặc định 1), mỗi lần `--vivado-impl-threads` luồng. Kết quả là chiến lược có worst slack post-route tốt nhất.
- `vivado_synth`: chỉ `synth_design` (dùng cho `synth_ab`: sub-block ~1 phút, cả tile 2–3 phút).
- Báo cáo: `report_timing -input_pins` 10 đường xấu nhất, 500 endpoint xấu nhất (`endpoints.txt`, gom theo thanh ghi
  thành `families`), utilization. Vivado chạy trong process group riêng, nên huỷ task (gate fail) là tắt luôn Vivado.
- Verilog lấy từ chính lần build sim (`collect_generated_src=True`).

## File

| File | Việc |
|---|---|
| `rocket_fmax.py` | Driver: baseline, điểm xuất phát, các vòng, nhận/loại, ghi kết quả, profiler |
| `fmax_nodes.py` | Các `@ChiaFunction`: chipyard (ELF, git, kiểm phạm vi, đọc Chisel), `extract_tile_rtl`, `run_gate`; lớp `SalvageClaudeLLM`; định vị đường găng trong Verilog/Chisel |
| `vivado_nodes.py` | `vivado_synth`, `vivado_impl`, đọc báo cáo Vivado |
| `ab_tool.py` | Tool MCP `synth_ab`: `rebuild_verilog`, `list_modules`, `start_ab`, `ab_result` |
| `prompts/improve_timing.md` | Prompt phiên tối ưu (Artix-7, post-route) |
| `prompts/fix_failure.md` | Prompt agent debug khi test fail |
| `prompts/fix_build.md` | Prompt agent sửa lỗi build |
| `exp.yaml` | Cache và bypass (RTL, gate, place + route của baseline/start) |
| `cluster.yaml` | 4 worker: `chipyard`, `verilator_run` ×4, `claude_creds`, `vivado` |
| `docker/VivadoDockerfile` | Image `chia-vivado`: chia-cacti + thư viện Vivado cần; Vivado cài trên host, mount vào |

## Chuẩn bị (một lần)

Vivado ML Standard (miễn phí, cần tài khoản AMD) cài trên host, chỉ chọn họ Artix-7 (đã cài 2025.1 ở `~/tools/Xilinx`):

```bash
# tải "AMD Unified Installer ... Linux Self Extracting Web Installer" (.bin) từ trang download Vivado của AMD
chmod +x FPGAs_AdaptiveSoCs_Unified_*_Lin64.bin
./FPGAs_AdaptiveSoCs_Unified_*_Lin64.bin --keep --noexec --target ~/xinstall
cd ~/xinstall
./xsetup -b AuthTokenGen                  # email + mật khẩu AMD
./xsetup -b ConfigGen                     # chọn Vivado, rồi Vivado ML Standard
# sửa ~/.Xilinx/install_config.txt: Destination=/var/home/<user>/tools/Xilinx;
#   trong Modules=: Artix-7:1, mọi họ chip khác :0 (bớt dung lượng)
#   CreateProgramGroupShortcuts=0, CreateDesktopShortcuts=0, CreateFileAssociation=0 (không cần,
#   và tránh lỗi "Program group entry ... already exists" khi cài lại)
mkdir -p ~/tools/Xilinx                   # phải thuộc $USER (không tạo bằng sudo), nếu không: "Cannot write to ..."
./xsetup -a XilinxEULA,3rdPartyEULA -b Install -c ~/.Xilinx/install_config.txt
ls ~/tools/Xilinx/*/Vivado/bin/vivado ~/tools/Xilinx/Vivado/*/bin/vivado 2>/dev/null   # đã cài: 2025.1/Vivado/bin/vivado
```

Image, cluster (4 worker), bỏ hai worker cũ (opencode, Yosys `eda`):

```bash
conda activate chia_env
cd ~/CHIA-workflow/projects/rocket-fmax/docker
docker build -f VivadoDockerfile -t chia-vivado:latest .
docker rm -f chia-opencode-$USER-0 chia-eda-$USER-0 2>/dev/null   # worker cũ, không còn trong cluster.yaml
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia up ~/CHIA-workflow/projects/rocket-fmax/cluster.yaml -y
chia status        # chipyard 1, verilator_run 4, claude_creds 1, vivado 1
docker exec chia-vivado-$USER-0 bash -lc 'v=$(ls /tools/Xilinx/*/Vivado/bin/vivado /tools/Xilinx/Vivado/*/bin/vivado 2>/dev/null | tail -1); $v -version'
```

Vivado nằm ở chỗ khác thì đặt biến môi trường `VIVADO` trong container. Worker `hello_claude` mount `~/.claude` của
host: đăng nhập `claude` trên host trước (gói Pro được).

## Chạy

Luôn submit **từ thư mục này**: cache lưu dataclass của `fmax_nodes`/`vivado_nodes`, nên lúc đọc lại phải import
được các module này.

```bash
cd ~/CHIA-workflow/projects/rocket-fmax
chia job submit --no-wait --submission-id fmax-r5 --working-dir . -- \
  python rocket_fmax.py --start-patch ~/rocket-fmax-v2/fmax-r4/patches/best.patch --run-name fmax-r5
chia job logs -f fmax-r5          # dừng: chia job stop fmax-r5
```

Mặc định đã là cấu hình của project: `--period-ns 10 --vivado-part xc7a100tcsg324-1 --extra-bench mm spmv
--exclude-isa rv64ui-p-ma_data --model sonnet --effort medium --iters 5 --max-ipc-loss 0.03 --min-perf-gain 0.02
--vivado-impl-strategies explore extratiming --vivado-impl-parallel 1 --out-dir ~/rocket-fmax-v2`.

- Mỗi lần chạy dùng `--run-name` mới: câu trả lời agent được cache theo `agent_<run-name>_iter<i>_*`.
- Đợt sau tiếp từ bản tốt nhất của đợt trước: `--start-patch ~/rocket-fmax-v2/<run>/patches/best.patch`. Điểm xuất
  phát được build, gate và place + route một lần, cache theo `baseline_start_<hash>_rtl2`, `..._gate_b2_mm_spmv`,
  `..._vivimpl4_<part>_p10_<chiến lược>`. Không có `--start-patch` thì baseline là điểm xuất phát (place + route
  cache theo `baseline_vivimpl4_...`).
- `--baseline-only`: không chạy vòng nào, chỉ place + route baseline (và `--start-patch` nếu có).
- Gói Pro: một phiên dài tốn nhiều token, nên chạy theo đợt khoảng 5 vòng. Gặp "You've hit your limit" thì loop
  chờ tới giờ reset rồi gọi lại (`--quota-retries`, bỏ nếu xa hơn `--quota-max-wait`).
- Tuỳ chọn khác: `--llm-timeout 1800`, `--debug-retries 2`, `--empty-retries 1` (phiên mới khi agent không sửa gì),
  `--build-fixes 2`, `--fix-timeout 900`, `--vivado-threads 8`, `--vivado-impl-threads 4`, `--make-jobs 16`,
  `--build-timeout 3600`, `--cache-dir ~/chia_cache`.
- `rv64ui-p-ma_data` fail ngay trên baseline (Rocket không hỗ trợ truy cập lệch địa chỉ), nên mặc định bị loại khỏi gate.

## Đo post-route lần đầu (Bước 0, `viv-impl2`)

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

Cache hiện dùng: `baseline_rtl2`, `baseline_gate_b2_mm_spmv` (cache chỉ phân biệt theo tag). Fmax ở bảng này là Yosys + OpenSTA sky130hd (đã gỡ).

## Kết quả

Các đợt r1–r3b nhận/loại theo Yosys + OpenSTA sky130hd (mức synth, đã gỡ). Từ `fmax-r4` nhận/loại theo Fmax
post-route Vivado (Artix-7 `xc7a100tcsg324-1`, hiệu năng = Fmax post-route / chi phí cycle, ngưỡng 2 %).

| Đợt | Vòng nhận | Fmax | Chi phí cycle | Ghi chú |
|---|---|---|---|---|
| baseline | | 47,13 MHz | 1,000 | đường găng `core.div` |
| `chia-fmax-r1` (29/10/2026) | 4/5 | 47,13 → 59,80 → 72,94 → 79,42 → **81,91 MHz** | +0,405 % | hiệu năng +73,1 %; diện tích +1,5 %; vòng 5 agent không sửa gì (0,3 phút) |
| `chia-fmax-r2` (29/10/2026) | 1/5 | 81,91 → **84,37 MHz** | +0,405 % | hiệu năng 81,58 → 84,03 (+3,0 %); diện tích +1,5 % so với baseline; vòng 2–5 loại |
| `chia-fmax-r3b` (30/10/2026) | 1/5 | 84,37 → **90,57 MHz** | +0,405 % | hiệu năng 84,03 → 90,20 (+7,35 %); diện tích −0,1 % so với baseline; Vivado không đổi (67,53 MHz) |
| `fmax-r4` (30/10/2026, **post-route Vivado**) | 1/5 | 61,06 → **63,61 MHz** | +0,405 % | hiệu năng 60,82 → 63,36 (+4,18 %); baseline post-route 58,67 MHz; LUT 29 484, 48 BRAM, 30 DSP |

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

Nhận xét: vòng đầy đủ 33–37 phút (agent + build ~10 phút, place + route 25–27 phút); post-route lần đầu không còn
dính `dcache` → `core.mem_reg_wdata`, đường găng chuyển sang frontend (icache tag hit → fetch queue); 2000 endpoint
âm, TNS ~ −10 000 ns, nên còn nhiều họ đường cách mục tiêu ~5,7 ns.

Yosys và Vivado không cùng xu hướng (lý do đổi mốc sang Vivado post-route): vòng 3 tăng 7,35 % theo Yosys nhưng Vivado giữ nguyên 67,53 MHz (đường găng
Vivado vẫn `dcache.s2_data` → `core.mem_reg_wdata` từ baseline). Vòng 4 bị Yosys loại nhưng Vivado lại tăng
(68,73 MHz, LUT +3,7 %, DSP 30 → 17).

## Đầu ra: `<out-dir>/<run>/`

```
results.csv            iter, accepted, fmax_mhz (post-route), slack_ns, tns_ns, strategy, synth_fmax_mhz, critical_path,
                       luts, brams, dsps, cycle_pct, perf, perf_gain_pct, isa_pass, reason, elapsed_min, impl_min
vivado_impl.csv        mỗi điểm/vòng: một dòng synth + một dòng mỗi chiến lược (slack sau place, sau route, LUT, BRAM, ...)
patches/iter<N>.patch  git diff trong generators/rocket-chip so với commit gốc (gộp cả bản tốt nhất trước đó)
patches/best.patch     bản tốt nhất (Fmax post-route / chi phí cycle); best.json (ghi khi job chạy hết)
runs/baseline/         gate.json, cycles.csv, build.log, rtl_info.json
runs/start/, runs/iter<N>/
                       prompt.md, agent_response.md, agent_transcript.txt, agent_ab_experiments.jsonl,
                       build.log, gate.json, gate_compare.txt, decision.json,
                       impl_<synth|chiến lược>/vivado_{paths,timing_summary,utilization,log_tail}.*, vivado_families.txt,
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
  - `--vivado-impl-probe` của code trước khi dọn (`viv-impl2`, 30/10/2026): số liệu ở mục "Đo post-route lần đầu".
  - Baseline 10 ns với `--extra-bench mm spmv` (`fmax-chia-2`, 29/10/2026), các đợt r1–r3b (bảng "Kết quả"): gate,
    nhận/loại theo chi phí cycle, agent debug, `--empty-retries`, `--start-patch`, `SalvageClaudeLLM`, sửa build,
    `synth_ab` (bản Yosys) đều đã chạy.
  - Vivado 2025.1 ML Standard ở `~/tools/Xilinx`, image `chia-vivado`, worker `rocket_vivado`: synth và place + route
    chạy được. Bộ nhớ: synth đỉnh ~9,5 GB; 2 place + route song song gần 10 GB.
  - Bước 1 (`fmax-r4`, 30/10/2026): loop nhận/loại theo post-route chạy hết 5 vòng; place + route điểm xuất phát
    và mỗi vòng; huỷ place + route khi cycle quá giới hạn (vòng 2), sau đó không còn Vivado sót; `synth_ab` bản Vivado
    (`rebuild_verilog` 55–79 s, A/B cả tile ~2,5 phút); agent đọc đường găng post-route. Chưa đo: RAM khi 2 sub-block
    synth cùng lúc (r4 agent chỉ A/B cả tile).
- **Mới kiểm bằng mock:**
  - Gate fail → huỷ place + route → agent debug; `--baseline-only` với `--start-patch`; huỷ task giữa place + route
    với Vivado giả thì không còn tiến trình Vivado nào.
  - Bỏ qua vòng có Verilog `RocketTile` trùng một bản đã đo (sau `fmax-r4`).
  - `synth_ab` so slack từng họ endpoint của parent và child (sau `fmax-r4`).
- Tính đúng chức năng chỉ dựa vào riscv-tests và 8 benchmark (như example), không kiểm tương đương.
