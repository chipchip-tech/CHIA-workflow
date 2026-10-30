# rocket-fmax: agent tối ưu Fmax của Rocket theo example `timing_opt` của CHIA

Mục tiêu: **Fmax của `RocketTile` > 100 MHz**, chu kỳ 10 ns, mất tối đa 3 % cycle so với RocketConfig gốc.

Mốc (đổi ngày 30/10/2026):

| Mốc | Thước đo | Trạng thái |
|---|---|---|
| 1 (hiện tại) | Vivado **post-route**, `xc7a100tcsg324-1` (Arty A7-100T), out-of-context, SRAM thành BRAM | Bước 0: đo baseline và điểm xuất phát (`--vivado-impl-probe`), chưa chạy thật |
| 2 | ORFS sky130 post-route | Chưa bắt đầu |

Ba đợt đầu (r1, r2, r3b) nhận/loại theo Yosys + OpenSTA sky130hd ở mức synth (bảng "Kết quả"). Từ mốc 1, loop sẽ
nhận/loại theo Fmax post-route Vivado (lấy tốt nhất của vài chiến lược place/route), `synth_ab` của agent đo bằng
Vivado `synth_design`; Yosys chỉ ghi để đối chiếu. Phần sửa loop này chưa viết, chờ số đo của Bước 0.

Loop chạy theo `examples/timing_opt` của CHIA (`--chia-mode`). Agent là Claude Code (Sonnet, effort medium).
Bắt đầu lại từ đầu ngày 29/10/2026: xuất phát từ RocketConfig gốc, mọi kết quả trước đó bị bỏ. Lịch sử cũ có
trong git, trước commit `ab43d94`.

## Một vòng

```
reset rocket-chip về bản tốt nhất
  ──► 1 phiên agent dài (--llm-timeout): đọc báo cáo timing, sửa Chisel bằng chipyard_bash,
      tự A/B một module bằng synth_ab (make verilog + Yosys/OpenSTA, start/poll)
  ──► kiểm phạm vi (chỉ src/main/scala của rocket-chip, không file *Config*)
  ──► build RocketConfig (lỗi compile → agent sửa build, --build-fixes)
  ──► synth + STA cả RocketTile; Fmax không đủ để thắng kể cả khi không tốn cycle → loại, không gate
  ──► gate: riscv-tests rv64ui/um/ua/uf/ud-p-* + benchmark (Verilator ×4)
        test fail → agent debug (--debug-retries), build + đo lại
  ──► nhận nếu: mọi test pass, minstret mọi benchmark = baseline,
                chi phí cycle ≤ 1 + --max-ipc-loss, Fmax / chi phí cycle > bản tốt nhất × (1 + --min-perf-gain)
      loại thì: revert về bản tốt nhất, đưa lý do vào prompt vòng sau
```

Chi phí cycle là trung bình nhân của `mcycle / mcycle baseline` trên các benchmark, tính so với RocketConfig gốc
(cộng dồn qua các vòng).

## Đối chiếu với example `timing_opt`

| | Example (BOOM) | Ở đây (Rocket) |
|---|---|---|
| Điểm xuất phát | `seed_flow`: RTL chưa sửa | Baseline RocketConfig chưa sửa |
| Mỗi lần chạy | 1 phiên agent → 1 nhánh con | 1 phiên agent mỗi vòng, `--iters` vòng liền |
| Chọn bản tiếp | Cây nhánh SQLite, người chọn bản cha | Tuyến tính, tự nhận/loại. Rẽ nhánh: `--start-patch <iterN.patch>` |
| Model | Opus, effort max, 60 phút | Sonnet, effort medium (gói Pro), `--llm-timeout` |
| A/B module | `timing_experiment` (Genus, start/poll) | `synth_ab` (Yosys + OpenSTA, start/poll) |
| Đo cả tile | Genus | Yosys `abc -D -constr` + OpenSTA |
| Gate | Verilator suite, `debug_failure` tối đa 3 lần | riscv-tests + benchmark, agent debug `--debug-retries` |
| Mất IPC | Prompt mặc định: không; prompt ironlaw: tuỳ ý nếu thắng | Tối đa `--max-ipc-loss` (3 %) |
| Prompt | `improve_timing_ironlaw.md` | `prompts/improve_timing_chia.md` (chuyển sang Rocket + Yosys) |
| Kiểm tương đương | Không | Không (cycle được đổi) |

## Đo timing

Số đo ở mức synthesis, dùng để so tương đối giữa các vòng. Chỉ so các số đo cùng chu kỳ.

- Yosys (luồng `v2`): `synth -flatten`, `dfflibmap`, `abc -D <period> -constr` (driving cell `buf_1`, load 5 như
  ORFS), liberty `sky130_fd_sc_hd__tt_025C_1v80` (bỏ 36 cell `probe`/`lpflow`).
- OpenSTA: `create_clock` trên `clock`, I/O delay 0, `report_worst_slack`, `report_wns`, `report_tns`,
  `report_checks -group_count 10`.
- Fmax ≈ 1000 / (period − worst slack) MHz.
- **Chu kỳ đặt ở mục tiêu (10 ns).** `abc -D` là mục tiêu delay của ABC: đặt chu kỳ rộng thì ABC không ép đường
  găng và `dnsize` thu nhỏ cell, nên Fmax đo được thấp hơn.
- SRAM (`*_ext`) là blackbox, chân không được ràng buộc, nên đường SRAM→reg và reg→SRAM không được tính.
  `EICG_wrapper` được thay bằng dây nối thẳng.
- Verilog lấy từ chính lần build sim (`collect_generated_src=True`).

## File

| File | Việc |
|---|---|
| `rocket_fmax.py` | Driver: baseline, các vòng, nhận/loại, ghi kết quả, profiler |
| `fmax_nodes.py` | Các `@ChiaFunction`: chipyard (ELF, git, kiểm phạm vi), `extract_tile_rtl`, `run_gate`, `synth_sta`; lớp `SalvageClaudeLLM` |
| `vivado_nodes.py` | `vivado_synth`: Vivado `synth_design` (out-of-context) trên `RocketTile`, đo thêm cho `--vivado-part`; `vivado_impl`: post-route nhiều chiến lược |
| `ab_tool.py` | Tool MCP `synth_ab`: `rebuild_verilog`, `list_modules`, `start_ab`, `ab_result` |
| `prompts/improve_timing_chia.md` | Prompt phiên tối ưu |
| `prompts/fix_failure.md` | Prompt agent debug khi test fail |
| `prompts/fix_build.md` | Prompt agent sửa lỗi build |
| `probe_tools.py` | Kiểm nhanh agent gọi được `chipyard_bash` |
| `exp.yaml` | Cache và bypass |
| `cluster.yaml` | `configs/cluster-local.yaml` + 4 slot `verilator_run` + worker `eda` + `hello_claude` |
| `docker/EdaDockerfile` | Image `chia-eda-sky130`: chia-cacti + yosys + liberty sky130hd |
| `docker/VivadoDockerfile` | Image `chia-vivado`: chia-cacti + thư viện Vivado cần; Vivado cài trên host, mount vào |

Code còn giữ các chế độ cũ không dùng trong hướng này: `--loop-ab`, `--ab-tool`, `--bypass-llm`, `--agent opencode`
(cùng `prompts/improve_timing.md`, `improve_timing_ab.md`, `improve_timing_target.md`), `--equiv` / `--equiv-only`
(`equiv.py`). `--chia-mode` không dùng chung với `--loop-ab`, `--ab-tool`, `--bypass-llm`, `--equiv`.

## Chuẩn bị (một lần)

```bash
conda activate chia_env
cd ~/CHIA-workflow/projects/rocket-fmax/docker
docker build -f EdaDockerfile -t chia-eda-sky130:latest .

export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia up ~/CHIA-workflow/projects/rocket-fmax/cluster.yaml -y
chia status        # chipyard 1, verilator_run 4, eda 1, claude_creds 1
```

Worker `hello_claude` mount `~/.claude` của host: đăng nhập `claude` trên host trước (gói Pro được). Kiểm:

```bash
cd ~/CHIA-workflow/projects/rocket-fmax
chia job submit --working-dir . -- python probe_tools.py --agent claude --models sonnet --effort medium
# phải in answer_ok=True
```

## Vivado: đo timing thêm (tuỳ chọn, `--vivado-part`)

Mỗi điểm được synth bằng Yosys (baseline, điểm xuất phát, mọi vòng có synth) còn được chạy Vivado `synth_design`
out-of-context trên `RocketTile`, cùng chu kỳ, SRAM `*_ext` là black box, I/O trễ 0. Kết quả ghi vào
`<run>/vivado.csv` (Fmax/slack Vivado cạnh Fmax/slack Yosys, LUT, FF, DSP, đường găng) và
`runs/<vòng>/vivado_*.rpt`. Mặc định **không dùng để nhận/loại**: chỉ để đối chiếu, nhất là xem độ dao động của số Yosys.

`--vivado-accept TOL_NS` (cần `--chia-mode`, `--vivado-part`): vòng đã đạt theo Yosys + gate còn phải có worst slack
Vivado >= worst slack Vivado của bản tốt nhất − `TOL_NS` (`0` = không xấu đi, `-0.05` = phải tốt hơn ít nhất
0,05 ns), nếu không thì bị loại ("Vivado xấu đi: ..."). Vivado lỗi cũng làm vòng bị loại. Vivado của vòng chạy song
song với gate nên gần như không thêm thời gian. Prompt có thêm mục "Second check: Vivado" (ngưỡng, đường găng Vivado
của bản tốt nhất). `synth_ab` của agent vẫn chỉ dùng Yosys.
Worker `vivado` có 1 slot, các lần chạy xếp hàng; loop chỉ chờ Vivado ở cuối job. Baseline và điểm xuất phát được
cache (`baseline_vivado_<part>_p<chu kỳ>`, `baseline_start_<hash>_vivado_...`).

Cài Vivado ML Standard (miễn phí, cần tài khoản AMD) trên host, chỉ chọn họ Artix-7:

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

Image và worker:

```bash
cd ~/CHIA-workflow/projects/rocket-fmax/docker
docker build -f VivadoDockerfile -t chia-vivado:latest .
chia up ~/CHIA-workflow/projects/rocket-fmax/cluster.yaml -y      # thêm worker rocket_vivado
docker exec chia-vivado-$USER-0 bash -lc 'v=$(ls /tools/Xilinx/*/Vivado/bin/vivado /tools/Xilinx/Vivado/*/bin/vivado 2>/dev/null | tail -1); $v -version'
```

Thêm `--vivado-part xc7a200tfbg676-2` vào lệnh chạy (Artix-7 lớn nhất của bản miễn phí; RocketTile vừa).
Vivado nằm ở chỗ khác thì đặt biến môi trường `VIVADO` trong container.

### Post-route (`--vivado-impl-probe`, mốc 1, chạy thật 30/10/2026)

`vivado_impl` (`vivado_nodes.py`): `synth_design` out-of-context với SRAM `*_ext` là model hành vi Chipyard sinh cho
mô phỏng, kèm các module con `split_*_ext` mà chúng gọi (`rtl.sram_models`), để Vivado suy ra BRAM/LUTRAM, ghi checkpoint; rồi từ checkpoint đó chạy một lần
`opt_design` → `place_design` → `phys_opt_design` → `route_design` (→ `phys_opt_design` nếu còn âm) cho mỗi chiến lược
`--vivado-impl-strategies` (`default`, `explore`, `extratiming`; xem `IMPL_STRATEGIES`), tối đa
`--vivado-impl-parallel` lần cùng lúc (mặc định 1: 2 lần song song dùng gần 10 GB), mỗi lần `--vivado-impl-threads` luồng. Lần nào bị kill vì hết RAM (137/-9)
được chạy lại một mình. Clock coi như đến từ BUFG (`HD.CLK_SRC BUFGCTRL_X0Y0`). Kết quả là chiến lược có worst slack
post-route tốt nhất.

`--vivado-impl-probe` (cần `--vivado-part`) không chạy vòng nào: place + route baseline và `--start-patch`, ghi
`<run>/vivado_impl.csv` (mỗi điểm một dòng `synth` và một dòng mỗi chiến lược: slack sau place, slack/Fmax sau route,
LUT, LUTRAM, FF, BRAM, DSP, phút, đường găng) và `runs/<điểm>/impl_<chiến lược>/vivado_*.rpt`. Cache theo
`<prefix>_vivimpl3_<part>_p<chu kỳ>_<chiến lược>`; RTL của baseline/start theo `baseline_rtl2` / `baseline_start_<hash>_rtl2`. Với `--chia-mode` và cùng `--extra-bench`, gate của điểm xuất phát
chạy luôn và được cache cho đợt sau.

`viv-impl2` (30/10/2026, `xc7a100tcsg324-1`, 10 ns, 2 lần song song, RAM gần 10 GB): BRAM được suy ra (48 BRAM,
104–107 LUTRAM).

| Điểm | Yosys | Vivado synth | `default` | `explore` | `extratiming` | Đường găng post-route tốt nhất |
|---|---|---|---|---|---|---|
| baseline | 47,13 | 52,58 | 56,15 (5,5 ph) | 58,14 (11,6 ph) | **58,67** (10,9 ph) | `dcache.s2_req_addr` (replica) → `core.mem_reg_wdata` |
| start (r3b) | 90,57 | 56,19 | 56,13 (5,6 ph) | **61,06** (9,9 ph) | 58,89 (9,7 ph) | `dcache.s2_data` → `core.mem_reg_wdata` |

(MHz post-route, phút mỗi chiến lược; synth thêm ~2,4 phút.) Ba đợt Yosys (+92 %) chỉ cho +4,1 % post-route. Ba chiến
lược của cùng một netlist chênh tới 5 MHz (~8 %), nên best-of-3 vẫn nhiễu cỡ vài %. Các đường găng khác: `fpuOpt.sfma`
→ `fpuOpt.regfile` (baseline `explore`), `dcache.s2_req_size` → `core.mem_reg_wdata`. Mốc 1 cần slack từ −6,38 ns về 0
(chu kỳ đường găng giảm ~39 %).

## Chạy

Luôn submit **từ thư mục này**: cache lưu dataclass của `fmax_nodes`, nên lúc đọc lại phải import được module này.

```bash
cd ~/CHIA-workflow/projects/rocket-fmax
chia job submit --no-wait --submission-id fmax-v2-r1 --working-dir . -- \
  python rocket_fmax.py --chia-mode --agent claude --model sonnet --effort medium \
    --period-ns 10 --extra-bench mm spmv --exclude-isa rv64ui-p-ma_data \
    --max-ipc-loss 0.03 --min-perf-gain 0.01 \
    --iters 5 --out-dir ~/rocket-fmax-v2 --run-name v2-r1
chia job logs -f fmax-v2-r1          # dừng: chia job stop fmax-v2-r1
```

- Mỗi lần chạy dùng `--run-name` mới: câu trả lời agent được cache theo `agent_<run-name>_iter<i>_*`.
- Chạy đợt sau tiếp từ bản tốt nhất của đợt trước: thêm `--start-patch ~/rocket-fmax-v2/<run>/patches/best.patch`.
  Điểm xuất phát được build, synth và gate một lần (tag `baseline_start_<hash>_*`). File không tồn tại thì
  job dừng ngay lúc đọc tham số.
- Gói Pro: một phiên dài tốn nhiều token, nên chạy theo đợt khoảng 5 vòng. Gặp "You've hit your limit" thì loop
  chờ tới giờ reset rồi gọi lại (`--quota-retries`, bỏ nếu xa hơn `--quota-max-wait`).
- Tuỳ chọn khác: `--llm-timeout 1800` (example dùng 3600), `--debug-retries 2`, `--empty-retries 1` (phiên mới khi agent không sửa gì), `--build-fixes 2`,
  `--fix-timeout 900`, `--vivado-part <part>` (đo thêm bằng Vivado, xem trên), `--vivado-accept <tol ns>` (Vivado vào điều kiện nhận),
  `--vivado-impl-probe` (chỉ place + route baseline và điểm xuất phát), `--vivado-impl-strategies`, `--vivado-impl-parallel`, `--vivado-impl-threads`, `--make-jobs 16`, `--build-timeout 3600`, `--cache-dir ~/chia_cache`.
- `rv64ui-p-ma_data` fail ngay trên baseline (Rocket không hỗ trợ truy cập lệch địa chỉ), nên phải `--exclude-isa`.

## Baseline (RocketConfig gốc, 10 ns, đo thật 29/10/2026)

| Worst slack | Đường găng | Fmax | Gate |
|---|---|---|---|
| −11,2161 ns | 21,216 ns, trong `core.div` | 47,13 MHz | ISA 106/107 (trừ `ma_data`), 18,9 phút |

| Benchmark | dhrystone | median | multiply | qsort | towers | vvadd | mm | spmv |
|---|---|---|---|---|---|---|---|---|
| mcycle | 225 027 | 6 117 | 27 281 | 173 402 | 4 075 | 2 476 | 25 255 | 50 448 |

Cache: `baseline_rtl`, `baseline_synth_v2_p10`, `baseline_gate_b2_mm_spmv` (cache chỉ phân biệt theo tag).

## Kết quả

| Đợt | Vòng nhận | Fmax | Chi phí cycle | Ghi chú |
|---|---|---|---|---|
| baseline | | 47,13 MHz | 1,000 | đường găng `core.div` |
| `chia-fmax-r1` (29/10/2026) | 4/5 | 47,13 → 59,80 → 72,94 → 79,42 → **81,91 MHz** | +0,405 % | hiệu năng +73,1 %; diện tích +1,5 %; vòng 5 agent không sửa gì (0,3 phút) |
| `chia-fmax-r2` (29/10/2026) | 1/5 | 81,91 → **84,37 MHz** | +0,405 % | hiệu năng 81,58 → 84,03 (+3,0 %); diện tích +1,5 % so với baseline; vòng 2–5 loại |
| `chia-fmax-r3b` (30/10/2026) | 1/5 | 84,37 → **90,57 MHz** | +0,405 % | hiệu năng 84,03 → 90,20 (+7,35 %); diện tích −0,1 % so với baseline; Vivado không đổi (67,53 MHz) |

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

Yosys và Vivado không cùng xu hướng (vì vậy thêm `--vivado-accept`, mới kiểm bằng mock): vòng 3 tăng 7,35 % theo Yosys nhưng Vivado giữ nguyên 67,53 MHz (đường găng
Vivado vẫn `dcache.s2_data` → `core.mem_reg_wdata` từ baseline). Vòng 4 bị Yosys loại nhưng Vivado lại tăng
(68,73 MHz, LUT +3,7 %, DSP 30 → 17).

## Đầu ra: `<out-dir>/<run>/`

```
results.csv            iter, accepted, wns_ns, tns_ns, worst_slack_ns, fmax_mhz, critical_path, isa_pass,
                       ipc_loss_pct, perf_gain_pct, reason, area_um2, elapsed_min
patches/iter<N>.patch  git diff trong generators/rocket-chip so với commit gốc (gộp cả bản tốt nhất trước đó)
patches/best.patch     bản tốt nhất (Fmax / chi phí cycle); best_wns.patch: bản có worst slack tốt nhất
best.json              bản tốt nhất, cycle_cost, perf (best.*, best_wns.* chỉ ghi khi job chạy hết)
runs/baseline/         timing_*.rpt, sta.log, yosys.log, gate.json, cycles.csv, build.log
runs/iter<N>/          prompt.md, agent_response.md, agent_transcript.txt, agent_ab_experiments.jsonl,
                       build.log, timing_*.rpt, gate.json, gate_compare.txt, decision.json,
                       fix<k>_* (sửa build), debug<k>/ (agent debug)
profile/               ChiaProfileCollector.log + timeline html
```

Áp lại một patch (diff so với commit rocket-chip của image, nhánh `chia_artifact`):

```bash
docker cp <out-dir>/<run>/patches/best.patch chia-chipyard-$USER-0:/tmp/best.patch
docker exec chia-chipyard-$USER-0 bash -lc 'cd /home/ray/chipyard/generators/rocket-chip && git checkout -- . && git apply /tmp/best.patch && git diff --stat'
```

## Trạng thái kiểm chứng

- **Chạy thật:**
  - `--vivado-impl-probe` (`viv-impl2`, 30/10/2026): số liệu ở mục "Post-route".
  - Baseline 10 ns với `--extra-bench mm spmv` (`fmax-chia-2`, 29/10/2026); số liệu ở bảng trên.
  - Vòng `--chia-mode` (`chia-fmax-r1`, 29/10/2026): 4 vòng được nhận, gate và nhận/loại theo chi phí cycle chạy
    đúng; baseline HIT cache. Claude gọi `synth_ab` ở cả 4 vòng nhận (2–10 lần/vòng, có A/B cả `RocketTile`).
    Vòng 5: Sonnet bỏ cuộc sau 1 lượt, 7 giây, không gọi tool (prompt cũ cho phép "vòng trống"). Đã sửa prompt và
    thêm `--empty-retries` (phiên mới khi agent không sửa gì).
  - `chia-fmax-r2` (29/10/2026): `--start-patch` gate lại điểm xuất phát (81,91 MHz, khớp đợt 1); `--empty-retries`
    chạy thật ở vòng 5 (phiên retry có sửa và A/B, nhưng chỉ 1 sửa rồi bỏ). A/B cả `RocketTile` dao động mạnh: sửa
    `dfma` mà đường găng con nhảy sang `sfma` (−3,9 ns so với −1,85 ns), nên nhận/loại theo một lần synth có nhiễu
    lớn hơn ngưỡng 1 % (~0,12 ns).
  - Probe Claude Code (28/10/2026): Sonnet medium gọi được `chipyard_bash`.
  - Build, gate, synth/STA, `SalvageClaudeLLM`, sửa build: đã dùng ở các lần chạy trước khi bắt đầu lại.
  - Vivado (29/10/2026): Vivado 2025.1 ML Standard cài ở `~/tools/Xilinx`, image `chia-vivado`, worker `rocket_vivado`,
    `--baseline-only --vivado-part xc7a200tfbg676-2` chạy được: baseline 63,82 MHz (slack -5,670 ns, 2000+ endpoint
    fail), đường găng `dcache/s2_data_reg[34] -> core/mem_reg_wdata_reg[6]` (không phải `core.div` như Yosys),
    28 750 LUT, 13 214 FF, 25 DSP, 2,2 phút. Bộ nhớ đỉnh ~9,5 GB (tiến trình con của synth song song). Ở đợt 3 vài vòng
    bị bỏ qua vì hết RAM với `--memory=14g`, nên container nâng lên `--memory=18g --memory-swap=26g`, và Vivado bị kill
    (không ra slack) thì tự chạy lại 1 lần với 2 luồng (cột `error` của `vivado.csv` ghi "chạy lại ..."; chạy lại mới
    kiểm bằng mock).
- **Mới kiểm bằng mock:**
  - `--vivado-part` trong các vòng (ghi `vivado.csv` từng vòng, chờ Vivado ở cuối job, lỗi Vivado không làm dừng loop).
  - Huỷ gate khi synth tile thua, agent debug, gate điểm xuất phát (`--start-patch`).
  - Danh sách sửa đổi đã đo rồi hoàn tác (30/10/2026). Ở `chia-fmax-r3`, vòng 1–2 có 4 phiên đều tách bộ nhân
    dfma, A/B cả tile xấu đi nên tự hoàn tác. Prompt retry lúc đó chỉ nói "không sửa gì". Nay `synth_ab` ghi diff
    của mỗi sửa đổi (so với bản tốt nhất) vào log A/B. Prompt retry liệt kê từng sửa đổi kèm diff và số A/B, rồi
    yêu cầu chọn họ đường găng khác. `history.md` (lịch sử các vòng) của vòng bị loại cũng mang danh sách này.
    Mới kiểm bằng mock (`tried_edits`, `_round_diff` trên repo git giả).
- Tính đúng chức năng chỉ dựa vào riscv-tests và 8 benchmark (như example), không kiểm tương đương.
