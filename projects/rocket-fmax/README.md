# rocket-fmax: agent tối ưu Fmax của Rocket mà không đổi cycle

Loop CHIA cho agent rút ngắn critical path của `RocketTile` (Chipyard `RocketConfig`, gồm
core, L1 và FPU) mà **không đổi hành vi theo cycle**. Mỗi vòng:

```
reset rocket-chip về bản tốt nhất ──► agent (OpenCode big-pickle + chipyard_bash, 120 s/lệnh)
  ──► bash.stop() ──► kiểm phạm vi (chỉ src/main/scala, không file *Config*, không sửa ngoài rocket-chip)
  ──► build RocketConfig (Verilator + Verilog) ──┬─► gate: rv64ui/um/ua/uf/ud-p-* + 6 benchmark (verilator_run ×4)
                                                └─► Yosys + OpenSTA cho RocketTile (eda)
  ──► nhận nếu: build ok + mọi ISA pass + mcycle của mọi benchmark khớp đúng baseline + worst slack tốt hơn
      loại thì: revert về bản tốt nhất, đưa lý do vào prompt vòng sau
```

Synthesis chỉ được gửi đi khi build đã xong (build tốn 12–16 GB RAM). Nếu gate trượt thì
synthesis đang chạy bị huỷ.

## Đo timing

Đây là ước lượng chỉ ở mức synthesis, dùng để **so tương đối giữa các vòng**.

- Yosys: `synth -flatten`, `dfflibmap`, `abc -D <period> -constr` (driving cell `buf_1`, load 5 như
  ORFS; có `-constr` thì script mặc định của ABC thêm `buffer; upsize; dnsize`), liberty
  `sky130_fd_sc_hd__tt_025C_1v80`. `--synth-flow` chọn luồng (xem `SYNTH_FLOWS` trong
  `fmax_nodes.py`); tên luồng nằm trong tag cache của baseline:
  - `v2`: `synth -flatten` mặc định. Bộ cộng (`$lcu`) là Brent-Kung, bộ nhân là array multiplier
    của Yosys.
  - `v3` (đã bỏ): thêm `-booth` và `+/choices/kogge-stone.v`. File kogge-stone chỉ có từ Yosys 0.40,
    image eda có 0.38, nên Yosys lỗi (lần chạy thật 4).
- OpenSTA: `create_clock` trên cổng `clock`, I/O có delay 0, `report_worst_slack`, `report_wns`,
  `report_tns`, `report_checks -group_count 10`.
- Fmax ≈ 1000 / (period − worst slack) MHz. Chu kỳ clock cố định cho mọi vòng.
- **Chu kỳ phải đặt gần mục tiêu.** `abc -D` là mục tiêu delay của ABC: khi mạch đã nhanh hơn nhiều so với
  chu kỳ, ABC không ép đường găng ngắn thêm và `dnsize` thu nhỏ cell để giảm area. Đo lại ở 10 ns (`p10`) cho Fmax
  cao hơn rõ so với ở 21,2 ns (bản `lab-c2`: 80,3 → 88,4 MHz). Từ `lab-c2` trở đi, chạy với `--period-ns 10`
  (mục tiêu 100 MHz) và chỉ so các số đo cùng chu kỳ.
- SRAM (`rockettile_{d,i}cache_{data,tag}_array*_ext`) là blackbox, chân của chúng **không
  được ràng buộc**. Vì vậy các đường SRAM→reg và reg→SRAM không được tính. `EICG_wrapper`
  (nếu có trong cây module) được thay bằng một dây nối thẳng.
- Verilog lấy từ chính lần build sim (`collect_generated_src=True`), nên bản đem synth đúng là
  bản đã mô phỏng.

## File

| File | Việc |
|---|---|
| `rocket_fmax.py` | Driver: baseline, các vòng, nhận/loại, ghi kết quả, profiler; `--equiv`, `--equiv-only` |
| `fmax_nodes.py` | Các `@ChiaFunction`: chipyard (ELF, git, kiểm phạm vi), `extract_tile_rtl`, `run_gate`, `synth_sta`, `equiv_formal`, `equiv_sim`, `extract_soc_rtl`, `synth_area` |
| `equiv.py` | Tìm module đổi, đổi tên hai bản, sinh script Yosys `equiv_*` và testbench lockstep Verilator |
| `area_check.py` | Kiểm luồng area `ChipTop` ngay trong container `eda`, ngoài CHIA (parse từng file, rồi `synth_area`) |
| `config_audit.py` | Mốc phụ: agent tìm khối thừa trong `RocketConfig`, loop build và kiểm từng ứng viên |
| `measure_soc.py` | Đo lại Fmax `RocketTile` với config sau audit, có/không `best.patch` (4 điểm) |
| `ab_tool.py` | Tool MCP `synth_ab` (`--ab-tool`): agent elaborate bản sửa rồi synth A/B một module so với bản tốt nhất; `loop_ab`: cùng phép A/B do loop gọi (`--loop-ab`) |
| `prompts/improve_timing.md` | Prompt của agent (IPC-neutral, không build) |
| `prompts/improve_timing_ab.md` | Như trên, kèm hướng dẫn dùng `synth_ab` (dùng khi `--ab-tool`) |
| `prompts/improve_timing_target.md` | Prompt của phiên sửa ngắn (`--loop-ab`): mục tiêu do loop chọn, kết quả A/B của phiên trước |
| `prompts/fix_build.md` | Prompt của agent sửa lỗi build (`--build-fixes`) |
| `prompts/audit_config.md` | Prompt của agent kiểm config (chỉ đọc, ghi `candidates.json`) |
| `exp.yaml` | Cache và bypass |
| `cluster.yaml` | `configs/cluster-local.yaml` + 4 slot `verilator_run` + worker `eda` |
| `docker/EdaDockerfile` | Image `chia-eda-sky130`: chia-cacti + yosys + liberty sky130hd |

## Chuẩn bị (một lần)

```bash
conda activate chia_env
cd ~/CHIA-workflow/projects/rocket-fmax/docker
docker build -f EdaDockerfile -t chia-eda-sky130:latest .

export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia down ~/CHIA-workflow/configs/cluster-local.yaml -y      # verilator đổi từ 1 lên 4 slot nên phải tạo lại
chia up ~/CHIA-workflow/projects/rocket-fmax/cluster.yaml -y
chia status        # phải thấy chipyard 1, verilator_run 4, eda 1, opencode_creds 1
```

## Chạy

Luôn submit **từ thư mục này**: cache lưu dataclass của `fmax_nodes`, nên lúc đọc lại phải
import được module này.

```bash
cd ~/CHIA-workflow/projects/rocket-fmax
# Mốc 1: baseline. Lần đầu ở 10 ns để hiệu chỉnh, sau đó chạy lại ở chu kỳ đã chốt.
# Lần hai không build lại, vì rtl và gate đã có trong cache.
chia job submit --working-dir . -- python rocket_fmax.py --baseline-only --period-ns 10
chia job submit --working-dir . -- python rocket_fmax.py --baseline-only --period-ns <P>
# Mốc 2: bỏ qua LLM. Loop tự chèn một dòng comment (RTL không đổi, vòng phải bị loại),
# hoặc áp --patch-file <git diff trong rocket-chip>. Chạy 2 lần để thấy baseline đọc từ cache.
chia job submit --working-dir . -- python rocket_fmax.py --period-ns <P> --iters 1 --bypass-llm
# Mốc 3: agent thật, 5 vòng
chia job submit --no-wait --submission-id fmax-m3 --working-dir . -- python rocket_fmax.py --period-ns <P> --iters 5
chia job logs -f fmax-m3          # dừng: chia job stop fmax-m3 --kill-tracked-pids
```

Các tuỳ chọn khác: `--make-jobs 16`, `--build-timeout 3600`, `--llm-timeout 1800`,
`--min-gain-ns 0`, `--tns-min-gain 0` (> 0: nhận cả vòng mà worst slack không tăng nhưng TNS âm giảm hơn tỉ lệ
này, miễn worst slack không thấp hơn mức tốt nhất đã nhận quá `--wns-tol 0.1` ns); phiên sửa nộp lại patch của vòng đã loại thì không đo lại; ra thêm `patches/best_wns.patch` (điểm có worst slack tốt nhất), `--repeat-seed 7`, `--flush-cache`, `--out-dir ~/rocket-fmax-out`,
`--cache-dir ~/chia_cache`.

## Kiểm tương đương bản tốt nhất

Gate chỉ chạy riscv-tests và 6 benchmark. Bước này kiểm thêm từng module đã đổi, so với baseline:

```bash
# kết quả của một lần chạy đã xong (build lại bản có patch, ~2 phút, rồi kiểm):
chia job submit --no-wait --submission-id fmax-equiv --working-dir . -- \
    python rocket_fmax.py --equiv-only --patch-file ~/rocket-fmax-out/m3/patches/best.patch --run-name m3-equiv
# hoặc tự động ở cuối loop: thêm --equiv
```

- Đơn vị kiểm là các module đã đổi ở mức cao nhất (bỏ comment `// @[...]` trước khi so). Nếu cổng
  của module khác baseline thì lùi lên module cha.
- **Formal** (worker `eda`): Yosys `equiv_make`, `equiv_simple -seq 4`, `equiv_induct -seq 4`,
  giới hạn `--equiv-timeout` (mặc định 900 s) mỗi đơn vị. `proven` là đã chứng minh; `unproven` và
  `timeout` là chưa chứng minh được (thường gặp với bộ nhân), không có nghĩa là sai.
- **Lockstep** (worker `chipyard`, Verilator): bản gốc và bản mới nhận cùng input ngẫu nhiên (thêm
  các word toàn 0, toàn 1; reset ngẫu nhiên), so mọi output ở cả hai pha clock trong
  `--equiv-cycles` cycle (mặc định 10⁷). Kiểm cả input mà CPU không bao giờ đưa vào, nên chặt hơn
  thực tế. Kết luận PASS khi lockstep pass mọi đơn vị.
- Kết quả: `runs/equiv/equiv_report.md`, `units.json`, `<đơn vị>/{gold,gate,tb}.sv`, `formal.json`, `sim.json`.

## Mốc phụ: kiểm config tìm khối thừa

```bash
chia job submit --no-wait --submission-id fmax-audit --working-dir . -- \
    python config_audit.py --run-name audit --exclude-isa rv64ui-p-ma_data
```

1. Build `RocketConfig` gốc (rocket-chip reset về HEAD, **bỏ** patch Fmax), Yosys area của `ChipTop`
   theo từng khối, `RocketTile` và SRAM là blackbox (cache `audit_soc_rtl`, `audit_soc_area_a2_p<P>`;
   gate dùng lại `baseline_gate`).
2. Agent chỉ đọc: nguồn config, cây module, bảng area. Ghi `/home/ray/config_audit/candidates.json`.
   Loop kiểm agent không sửa gì trong cây chipyard (có thì revert).
3. Với mỗi ứng viên (tối đa `--max-candidates`, mặc định 8): **loop** ghi
   `generators/chipyard/src/main/scala/config/RocketFmaxAudit.scala` với
   `class RocketFmaxAudit<k> extends Config(<fragment> ++ new chipyard.RocketConfig)`, build, chạy gate
   ∥ area. Fragment chỉ được có dạng `new a.b.C` hoặc `new a.b.C(...)`. File này bị xoá sau mỗi build
   và ở cuối lần chạy; `RocketConfig` không bị sửa.
4. Gộp mọi ứng viên "bỏ được" vào `RocketFmaxAuditAll` và kiểm lần nữa (`--no-combine` để bỏ).
5. Xuất config cuối (bản gộp nếu pass, hoặc ứng viên duy nhất pass) thành
   `patches/chipyard-config.patch`: thêm `RocketFmaxSoCConfig`
   (`generators/chipyard/.../config/RocketFmaxSoC.scala`) và `RocketFmaxSoCArty100TConfig`
   (`fpga/src/main/scala/arty100t/`, = `WithArty100TTweaks ++ WithBroadcastManager ++ RocketFmaxSoCConfig`,
   như `RocketArty100TConfig`). Loop không áp patch này; áp tay bằng `git -C /home/ray/chipyard apply`.

**Yosys và `'{...}`.** firtool ghi các bảng tra nhỏ (Vec) ngoài `RocketTile` (L2 `Atomics`, `TLError`,
`TLMonitor`, serializer...) thành `wire [3:0][3:0] _GEN = '{4'hC, ...};`. Yosys trong image `eda` báo
`unexpected OP_CAST`. Luồng area đổi `'{` thành `{` (`fmax_nodes.sv_packed_patterns`; với mảng packed và
phần tử đúng độ rộng thì hai cách viết như nhau), báo lỗi nếu gặp `default:`/khoá hay số không độ rộng.
Luồng đo `RocketTile` (`v2`) không đổi. Kiểm nhanh ngoài CHIA trên bản build có sẵn:

```bash
C=chia-chipyard-$USER-0; E=$(docker ps --format '{{.Names}}' | grep -i eda | head -1)
D=/home/ray/chipyard/sims/verilator/generated-src/chipyard.harness.TestHarness.RocketConfig
rm -rf /tmp/ac && mkdir -p /tmp/ac/gen && docker cp $C:$D/gen-collateral /tmp/ac/gen/
docker cp $C:$D/chipyard.harness.TestHarness.RocketConfig.top.mems.conf /tmp/ac/gen/
cp area_check.py fmax_nodes.py equiv.py /tmp/ac/ && docker exec $E rm -rf /tmp/ac && docker cp /tmp/ac $E:/tmp/ac
docker exec $E bash -lc 'cd /tmp/ac && python area_check.py gen/gen-collateral'
```

**Khối bảo vệ cho FPGA (Arty A7-100T) và ASIC (OpenROAD flow scripts).** Hai luồng này chưa chạy,
nhưng audit không được bỏ khối mà chúng cần, dù harness Verilator không dùng. Danh sách
`PROTECTED` trong `config_audit.py` (dựa trên `WithArty100TTweaks` và `ChipLikeRocketConfig` của nhánh
`chia_artifact`): debug/JTAG, UART, serial-TL, mem port, BootROM, CLINT, PLIC, boot-select
(`custom_boot`, boot-addr reg), clock tap, clock/reset. Chặn hai lớp:

- trước build: fragment bỏ khối bảo vệ (`WithNoDebug`, `WithDMIDTM`, `WithNoUART`, `WithNoSerialTL*`,
  `WithNoMemPort`, `WithNMemoryChannels(0)`, `WithNoBootROM`, `WithNoCLINT`, `WithNoPLIC`,
  `WithNoCustomBootPin`, `WithNoBootAddrReg`, `WithNoClockTap`, `WithNoResetSynchronizers`,
  `WithNoClockGenerator*`) bị từ chối, không build;
- sau build: cổng `ChipTop` (`jtag_*`, `uart_*`, `serial_tl_*`, `axi4_mem_*`, `custom_boot`, `clock_tap`,
  `clock*`/`reset*`) và module (`TLDebugModule*`, `TLUART*`, `TLROM`/`BootROM`, `CLINT`, `TLPLIC`,
  `*Serdes*`, `*BootAddrReg*`, reset sync) phải còn đủ như baseline; mất thì bỏ qua gate.

`runs/baseline/protected_blocks.txt` ghi khối nào tìm thấy trong `ChipTop` baseline. Dòng `NOT FOUND` nghĩa
là regex tên module/cổng chưa khớp tên thật (chưa kiểm trên RTL thật), khối đó khi ấy chỉ được bảo vệ
bằng lớp fragment. Gate còn treo, ghi trong `audit.md`: Vivado (synth/timing 50 MHz, elaborate config
Arty) và ORFS; adder viết tay trong `best.patch` cần kiểm lại timing trên FPGA (carry chain cứng).

Kết luận mỗi ứng viên: *bỏ được* (gate pass, mcycle giữ nguyên), *chạy được nhưng đổi cycle*,
*cần cho harness/chức năng* (gate trượt), *bị chặn: khối bảo vệ FPGA/ASIC*, *cần cho FPGA/ASIC (đụng
khối bảo vệ)* hoặc *không build được*. Area không tính SRAM macro; phần
SRAM được báo bằng số bit (từ `.top.mems.conf`). Kết quả: `audit.md`, `audit.csv`,
`runs/agent/` (prompt, câu trả lời, `candidates.json`), `runs/cand<k>_<id>/`.

Sau mốc này rocket-chip ở trạng thái HEAD; muốn có lại bản Fmax tốt nhất thì áp `best.patch` như mục dưới.

### Đo lại Fmax sau audit (`measure_soc.py`)

```bash
chia job submit --no-wait --submission-id fmax-soc --working-dir . -- \
    python measure_soc.py --run-name soc-fmax --exclude-isa rv64ui-p-ma_data \
    --patch-file ~/rocket-fmax-out/m3/patches/best.patch
```

Fmax ở project này là Yosys + OpenSTA trên `RocketTile` (luồng `v2`, chu kỳ cố định 21,2 ns). Các khối bỏ
đi nằm ngoài tile, nhưng tile vẫn thấy bản đồ địa chỉ (TLB/PMA, kiểm địa chỉ hợp lệ) nên RTL của nó có thể
đổi. Script đo 4 điểm: `base` (RocketConfig, từ cache), `cfg` (fragment audit), `best` (`best.patch`),
`best_cfg` (cả hai, kèm gate so với baseline). Build chạy lần lượt (chung cây chipyard), synth/STA và gate
chạy song song. Báo cáo `soc_fmax.md`: slack, Fmax, area tile, sha RTL tile và module nào khác nhau. Mặc định
fragment là hai ứng viên của `audit2` (`--fragments` để đổi). Cuối lần chạy rocket-chip ở trạng thái đã áp
`best.patch`, file config tạm bị xoá.

Chạy thật (26/10/2026, `fmax-soc`, `~/rocket-fmax-out/soc-fmax`), 21,2 ns:

| Điểm | Worst slack | Đường găng | Fmax | Area tile (v2) | Đường găng đi qua |
|---|---|---|---|---|---|
| base | −1,001 ns | 22,201 ns | 45,04 MHz | 1 069 069 µm² | `core.div` |
| cfg | −0,911 ns | 22,111 ns | 45,23 MHz | 1 064 271 µm² | `core.div` |
| best | +6,980 ns | 14,220 ns | 70,32 MHz | 1 083 776 µm² | `dcache` (pstore1 → storegen) |
| best_cfg | +7,350 ns | 13,850 ns | 72,20 MHz | 1 078 378 µm² | `dcache` (s2_data → storegen) |

`base` và `best` khớp số của `m1-final`/`m3` (đo lặp lại được). Bỏ hai khối làm RTL tile đổi thật: `DCache`,
`Frontend`, `ICache`, `PMAChecker`, `PTW` (bản đồ địa chỉ ít vùng hơn) và master xbar của tile (`z4` → `z3`, trường
size hẹp lại); `MulDiv` không đổi. Gate `best_cfg` pass, mcycle giữ nguyên. Lưu ý: ở `base`→`cfg` đường găng nằm
trong `core.div` (RTL không đổi) mà slack vẫn lệch 0,09 ns, tức nhiễu của synth flatten + ABC cỡ 0,1 ns; mức lợi
0,37 ns (+2,7 % Fmax) của `best_cfg` lớn hơn nhiễu đó nhưng cùng bậc.

### Chạy tiếp vòng agent trên nền config audit (`--fragments`, `--start-patch`)

```bash
chia job submit --no-wait --submission-id fmax-m4 --working-dir . -- \
    python rocket_fmax.py --run-name m4 --period-ns 21.2 --iters 5 --exclude-isa rv64ui-p-ma_data --equiv \
    --start-patch ~/rocket-fmax-out/m3/patches/best.patch \
    --fragments "new testchipip.soc.WithNoScratchpads" "new chipyard.config.WithNoBusErrorDevices"
```

- `--fragments`: mỗi lần build của vòng (và điểm xuất phát), loop ghi class `RocketFmaxCfg` (= fragment ++
  `RocketConfig`) vào `RocketFmaxAudit.scala`, build, rồi xoá. File được ghi sau `collect_diff` nên kiểm phạm vi
  không thấy nó; agent vẫn chỉ được sửa rocket-chip. Prompt ghi config này thay cho `RocketConfig`.
- `--start-patch`: các vòng bắt đầu từ patch này (thay vì rocket-chip gốc). Điểm xuất phát được build + synth/STA
  một lần, cache theo `baseline_start_<hash>_*` (hash của patch và fragment); không chạy gate (đã gate bằng
  `measure_soc.py`). Gate các vòng vẫn so với `baseline_gate` (config audit không đổi mcycle).
- `best.patch` cuối là patch tích luỹ (gồm cả start patch). `--equiv` so bản tốt nhất với **điểm xuất phát**
  (cùng config), không so với baseline, vì config đổi RTL tile (bản đồ địa chỉ).
- Đã chạy trên mock (lần 2 điểm xuất phát HIT cache, file config tạm bị xoá).

**Chạy thật (`fmax-m4`, 26/10/2026, `~/rocket-fmax-out/m4`).** Lần chạy đầu dừng ở vòng 1 (agent hết
`--llm-timeout` 30 phút); chạy lại cùng `--run-name` nên `results.csv` có hai khối dòng, dưới đây là lần hai.

| Vòng | Nhận | Worst slack (ns) | Fmax (MHz) | Area tile (µm²) | Đường găng | Ghi chú |
|---|---|---|---|---|---|---|
| baseline | | −1,001 | 45,04 | 1 069 069 | `core.div` | RocketConfig gốc |
| start | | +7,350 | 72,20 | 1 078 378 | `dcache` store data → storegen | m3 best.patch + config audit |
| 1 | có | +8,158 | 76,68 | 1 088 086 | `fpuOpt.ifpu` | 14,8 phút |
| 2 | không | +7,761 | 74,41 | 1 078 451 | `fpuOpt.ifpu` | kém hơn vòng 1 |
| 3 | không | | | | | build lỗi: Scala, default param `isNaNx = xt.isNaN(x)` dùng tham số cùng danh sách (`FPU.scala:340`) |
| 4 | không | | | | | agent hết 30 phút |
| 5 | có | **+8,745** | **80,29** | 1 082 577 | `fpuOpt.dfma` (FMA) | 26,9 phút |

- So với điểm xuất phát: +1,40 ns slack, 72,2 → 80,3 MHz (+11 %), area +0,4 %. Mỗi bước nhận (+0,81 và +0,59 ns)
  lớn hơn nhiễu synth (~0,1 ns). So với RocketConfig gốc: 45,0 → 80,3 MHz.
- Kiểm tương đương iter5 so với điểm xuất phát: **PASS**. Module đổi `AMOALU`, `FPU`, `IntToFP`; `AMOALU`
  proven hình thức (195/195), `FPU` formal timeout (1870 s) nhưng lockstep Verilator 10⁷ cycle không lệch
  (`IntToFP` nằm trong đơn vị `FPU`). Gate (ISA + mcycle) pass ở cả hai vòng được nhận.
- Lỗi của loop phát hiện ở lần này (đã sửa): lý do "build lỗi" trong `results.csv`/prompt vòng sau chỉ có phần
  đầu log sbt vì `build_errors` lọc `[error]` mà không bỏ mã màu ANSI; tiêu đề báo cáo equiv ghi "so với
  baseline" dù so với điểm xuất phát.

### Tool A/B synth cho agent (`--ab-tool`)

Theo `TimingExperimentTool` của ví dụ `timing_opt` (BOOM) trong CHIA: thay vì sửa "mù" rồi chờ loop
build + gate + synth, agent tự kiểm từng ý tưởng. Tool `synth_ab` (`ab_tool.py`) chạy trên worker
chipyard cạnh `chipyard_bash` (hai tool chia slot `chipyard` 0,5 mỗi cái, nên build của loop chờ tới khi
cả hai dừng) và có 4 hàm:

| Hàm | Việc |
|---|---|
| `rebuild_verilog(max_wait_seconds)` | `make verilog CONFIG=<config của vòng>` trên bản sửa hiện tại (chạy nền; với `--fragments` tool tự ghi rồi xoá `RocketFmaxAudit.scala`); báo module nào đổi so với bản tốt nhất (bỏ qua comment `@[...]`) |
| `list_modules(pattern, only_changed)` | Module dưới `RocketTile`, số dòng, đánh dấu module bị đổi |
| `start_ab(module)` | `synth_sta` (cùng flow, cùng chu kỳ với loop) cho `module` của bản sửa và của bản tốt nhất, song song trên `eda` (0,5 slot mỗi cái; kết quả bản tốt nhất được nhớ theo module). `module=RocketTile`: synth cả tile (1 slot) và so với kết quả đầy đủ của bản tốt nhất, đúng con số loop dùng để nhận/loại |
| `ab_result(handle, max_wait_seconds)` | Slack hai bên, delta (dương = tốt hơn; ≤ 0,1 ns coi là nhiễu), họ endpoint xấu nhất; báo cáo đầy đủ ở `/home/ray/rocket_fmax/ab/<handle>/` |

Mọi lời gọi trả về trong ≤ 60 s (việc dài chạy nền, agent gọi lại để chờ). Tool từ chối synth khi mã nguồn
đã đổi sau lần `rebuild_verilog` cuối. Prompt `improve_timing_ab.md` yêu cầu chỉ nộp bản sửa có kết quả
`start_ab("RocketTile")` tốt hơn bản tốt nhất, và revert nếu không cải thiện được. Sau lượt agent, loop dừng
hai tool, gọi `ab_cleanup` (giết `make verilog` còn chạy, xoá file config) rồi mới `collect_diff`. Tiêu chí
nhận vòng không đổi (build, gate, synth đầy đủ của loop).

opencode ghép tên server vào trước tên hàm, nên agent thấy `synth_ab_rebuild_verilog`, ... và
`chipyard_bash_chipyard_bash_run_command`. Các tool sẵn có của opencode (`bash`, `read`, `edit`, ...) chạy trong
container opencode, nơi không có Chipyard: lần `fmax-ab1` agent gọi 142 lần `bash` sẵn có này để tìm workspace,
không gọi `synth_ab` lần nào và hết 45 phút. Cờ `--builtin-tools` chọn phần còn lại của chúng (class
`SalvageOpenCodeLLM` trong `fmax_nodes.py`): `on` (tất cả, như m3/m4; mặc định), `off` (không còn gì), `deny` (vẫn
khai báo nhưng quyền `bash`/`edit`/`webfetch` là deny), `readonly` (chỉ `read`/`grep`/`glob`/`list`/`lsp`). Prompt
dặn agent chỉ dùng `chipyard_bash`. Khi agent hết `--llm-timeout`, `SalvageOpenCodeLLM` vẫn giữ transcript
(`agent_transcript.txt`).

**Lần chạy thật `fmax-ab-t1` (27/10/2026, `--ab-tool`, tool sẵn có tắt, big-pickle): hỏng.** opencode không in
event nào trong 45 phút (`session None`, transcript rỗng); hai server MCP chỉ nhận lượt khởi tạo và
`tools/list`, không có lời gọi tool nào. **Nguyên nhân (`probe_tools.py`, chạy thật 27/10/2026):** khi tắt hết
tool sẵn có, server opencode trả ngay `AI_APICallError: Free usage limit reached` cho cả big-pickle và
deepseek-v4.1-flash-free, còn khi bật thì cùng model đó, chạy xen kẽ cách vài phút, gọi `chipyard_bash` đúng sau
8–9 s. Tức là không phải hết hạn mức thật: các model free dường như chỉ nhận request trông giống opencode
mặc định. Sau lỗi đó `opencode run` không thoát mà treo tới hết giờ. Đã sửa: bỏ mặc định `off`; log opencode
(INFO) luôn ra stderr, và khi dòng log cuối là `stream error` rồi im 60 s thì `SalvageOpenCodeLLM` giết
opencode, ghi đuôi stderr vào `agent_transcript.txt` và trả lượt hỏng thay vì chờ hết giờ. Probe lần 2
(27/10/2026, big-pickle): `deny` (15 s), `readonly` (8 s) và `on` đều gọi được `chipyard_bash`, nên lần chạy
`--ab-tool` tiếp theo dùng `--builtin-tools readonly`.

Chưa kiểm chứng trên lần chạy thật nào: tắt tool sẵn có (mới kiểm cấu hình sinh ra trên mock), và một vòng
agent dùng `synth_ab` từ đầu đến cuối. Dự kiến synth một sub-block mất 1–2 phút, `RocketTile` 3–4 phút
(Yosys trên tile của bản tốt nhất từng mất 9–13 phút ở m3, nên con số này có thể lớn hơn). Sub-block dùng
input/output delay 0, nên chỉ đo các đường nằm trong module. Synth của agent còn chạy khi agent kết thúc
sẽ chạy nốt, nên synth của vòng có thể phải chờ vài phút.

### Loop điều khiển A/B (`--loop-ab`)

**Lần chạy thật `fmax-ab-t2` (27/10/2026, `--ab-tool --builtin-tools readonly`, big-pickle, 1 vòng): bị loại vì
agent hết `--llm-timeout` 45 phút.** Lần này không có lỗi provider; agent chạy khoảng 123 bước, gần như toàn
`chipyard_bash` để đọc và sửa rải rác 4 file (`ALU`, `AMOALU`, `Multiplier`, `FPU`) trong khi đường găng ở
`fpuOpt.dfma`. Nó gọi `synth_ab_start_ab` khi chưa rebuild (bị từ chối), có lần gõ `synth_ab_list_modules` như
lệnh shell, và chỉ gọi `synth_ab_rebuild_verilog` khi còn khoảng 1 phút. `agent_ab_experiments.jsonl` rỗng: không
lần A/B nào xong. Diff của agent bị bỏ vì loop loại vòng ngay khi agent hết giờ (m4 vòng 4 cũng mất theo cách
này). Kết luận: model free không tự làm được phần thủ tục (chọn mục tiêu, rebuild, A/B, canh giờ).

`--loop-ab` chuyển phần thủ tục sang loop; agent chỉ sửa Chisel. Mỗi vòng:

1. **Loop chọn mục tiêu** (`pick_target` trong `fmax_nodes.py`): lấy hai đầu đường găng của bản tốt nhất, lần
   theo tên instance trong Verilog (`fpuOpt.dfma.in_in2[53]_reg` → instance `fpuOpt.dfma`), lấy instance nhỏ
   nhất chứa cả hai đầu và module của nó (vd. `FPUFMAPipe`), rồi tìm file Scala định nghĩa từng module trên
   đường xuống (`find_scala_classes`). Ghi vào `runs/iter<i>/target.json`.
2. **Phiên sửa ngắn** (`prompts/improve_timing_target.md`, `--edit-timeout` 900 s): chỉ có `chipyard_bash`, chỉ
   sửa cho mục tiêu, không build.
3. **Loop build** (lỗi compile thì agent sửa lỗi build như `--build-fixes`).
4. **Loop A/B** (`loop_ab` trong `ab_tool.py`): so module text với bản tốt nhất, lấy instance nhỏ nhất chứa mục
   tiêu và mọi module bị đổi (so theo đường dẫn instance, vì tên module có thể lệch hậu tố `_<n>` giữa hai bản),
   synth module đó của bản sửa và của bản tốt nhất (song song trên `eda`, kết quả bản tốt nhất nhớ trong vòng).
   Nếu instance đó là cả `RocketTile` thì synth cả tile, so với kết quả đầy đủ của bản tốt nhất, và dùng luôn
   kết quả này cho bước 6.
   Số quyết định là **worst slack của các endpoint nằm trong instance mục tiêu** (tìm trong 2000 endpoint tệ nhất
   của mỗi bên), không phải worst slack của cả module synth. Báo cáo vẫn in delta của cả module. Nếu một bên
   không có endpoint nào của mục tiêu thì dùng delta của module (lý do: xem `lab-c1` dưới đây).
5. A/B không tốt hơn `--ab-min-gain` (0,1 ns, cỡ nhiễu synth) → loop đưa báo cáo A/B (đường găng mới, Verilog
   của module, diff của vòng) vào một phiên sửa mới, tối đa `--ab-refines` (2) lần; hết lượt thì loại vòng mà
   **không chạy gate** (tiết kiệm ~13 phút). Bản sửa không làm Verilog đổi gì cũng được báo lại cho phiên sau.
6. A/B tốt hơn → gate và synth cả tile như cũ; tiêu chí nhận vòng không đổi.

Diff của riêng vòng (không gồm bản tốt nhất, `round_diff`, dùng index git tạm nên không đụng index thật) được
đưa vào prompt phiên sau và `history.md`, và đặt ở `/home/ray/rocket_fmax/ab<k>/round.diff` để agent tự
`git apply -R` khi muốn bỏ. Prompt cấm `git checkout`/`git restore`/`git stash` vì bản tốt nhất nằm trong working
tree, chưa commit.

**Áp dụng cho mọi chế độ:** agent hết giờ hoặc lỗi mà diff vẫn khác bản tốt nhất thì loop vẫn build, gate và
synth như bình thường. Lý do hỏng của agent được ghi thêm trong ngoặc vuông ở cột `reason` của `results.csv`.

`results.csv` có thêm cột `target`, `ab_delta_ns`, `ab_sessions`. Câu trả lời agent cache theo
`agent_<run-name>_iter<i>_edit<k>_llm`. `--loop-ab` không dùng chung với `--ab-tool`/`--bypass-llm`, và không dùng
`--llm-timeout`.

Đã kiểm trên mock (sandbox, 27/10/2026):
- chọn mục tiêu trên Verilog giả dạng FPU/`FPUFMAPipe`/`MulAddRecFNPipe`, gồm cả trường hợp module dùng chung
  và SRAM black box;
- 6 tình huống của vòng: thua A/B rồi thắng ở phiên 2 thì đi gate; thua hết lượt; sửa cả core thì A/B cả tile
  và dùng lại kết quả; agent hết giờ nhưng đã sửa; Verilog không đổi; chế độ cũ khi agent hết giờ có/không có
  diff;
- `round_diff` và `find_scala_classes` trên một repo git thật.

**Lần chạy thật `fmax-lab-t1` (27/10/2026, 1 vòng, big-pickle, readonly): bị loại.** Chọn mục tiêu đúng trên
Verilog thật (`fpuOpt.dfma` = `FPUFMAPipe_l4_f64`, chuỗi `RocketTile` → `FPU` → `FPUFMAPipe_l4_f64`), nhưng
không tìm ra file Scala của `FPUFMAPipe_l4_f64` (tên do `desiredName` đặt). Agent hết 15 phút mà chưa sửa gì nên
vòng bị loại ngay, không build. Đã sửa: `find_scala_classes` bỏ dần từng đoạn `_xxx` ở cuối tên cho tới khi tìm
thấy class (`FPUFMAPipe_l4_f64` → `FPUFMAPipe`); prompt có sẵn báo cáo OpenSTA của đường găng nhất.
Transcript: agent đi đúng chỗ (`MulAddRecFNPipe_l2_e11_s53` trong `tile/FPU.scala`, `postmul_regs`, cách chia các
tầng prefix quanh thanh ghi), nhưng 40 lần gọi tool vẫn chưa sửa, trong đó vài lần là heredoc rỗng/script nháp. Đã
thêm vào prompt: đọc tối đa khoảng 15 lần gọi tool rồi sửa; bản sửa chưa hoàn hảo vẫn được A/B và phản hồi; không
dùng tool làm nháp. Lần sau chạy với `--edit-timeout 1500`.

**Lần chạy thật `fmax-lab-t2` (28/10/2026, 1 vòng, `--edit-timeout 1500`): bị loại, agent vẫn không sửa.**
- Đã tìm được file Scala: `FPUFMAPipe_l4_f64` → `tile/FPU.scala`.
- Agent gọi 40 lần, đều là `chipyard_bash`, mỗi bước khoảng 23–25 s.
- Lệnh 1–24 đọc đúng chỗ. Đường găng kết thúc ở `mulAddResult_pHiR_15`, tức thanh ghi giữa bộ cộng prefix do
  chính loop viết ở m3/m4 (`loCut`, `gHiR`, `pHiR` trong `best.patch`). Agent đã tính thử các giá trị `loCut`
  khác nhau, và đó là hướng đúng.
- Lệnh 25–40 không làm gì: `python3 -c "# simulate\npass"` và heredoc rỗng.
- Transcript có 0 mục `[Thinking]`. Có vẻ model dùng tool call làm nháp, và khi định viết bản sửa thì nội dung ra
  rỗng.
- Hạn ~15 lần gọi tool ghi trong prompt bị bỏ qua. Hết 25 phút.

Đã sửa (loop ép chứ không chỉ dặn trong prompt):

1. **`BudgetBashTool`** (`fmax_nodes.py`): `chipyard_bash` của phiên sửa đếm số lệnh khi cây chưa có thay đổi
   nào của phiên. Thay đổi được nhận ra bằng hash của `git diff HEAD` cùng file mới.
   - Lệnh chỉ chứa script rỗng thì không chạy (`noop_command`). Script rỗng là `pass`, comment hoặc heredoc rỗng;
     với Python còn tính cả script không in gì và không ghi file nào.
   - Mặc định `--edit-budget 15 25 35`:
     - sau lệnh 15, mọi kết quả kèm câu nhắc ngân sách;
     - sau lệnh 25, lệnh không làm đổi file nào bị giấu output (script ghi ra `/tmp` rồi chạy ở lệnh sau vẫn được);
     - sau lệnh 35, không chạy gì nữa, agent được bảo trả lời bằng kế hoạch sửa.
   - Đã sửa thì hết giới hạn. Undo thì giới hạn quay lại.
   - Mỗi lệnh được ghi vào `edit<k>_budget.jsonl`.
   - Kèm `--edit-steps 45`: đặt `steps` của agent opencode, quá số bước đó model phải trả lời bằng chữ. Nếu bản
     opencode trong container không nhận khoá `steps` thì chạy với `--edit-steps 0`.
2. **Đưa sẵn mã Chisel vào prompt** (`path_sources`):
   - Lấy chú thích `@[file:line]` của thanh ghi đầu và cuối đường găng trong Verilog của bản tốt nhất, từ dòng khai
     báo `reg` và các dòng gán `<=`.
   - Trích ±40 dòng Scala quanh đó từ cây hiện tại, tối đa 300 dòng.
   - Đánh dấu `>` ở dòng được trỏ tới và `+` ở dòng do `best.patch` thêm vào.
   - Ghi vào `target.json` (`ends`, `sources`). File ngoài rocket-chip (hardfloat) chỉ được liệt kê.
3. **Phiên không sửa gì không kết thúc vòng.** Nếu còn lượt `--ab-refines`, phiên sau nhận:
   - danh sách lệnh của phiên trước, lấy từ `budget.jsonl`;
   - câu trả lời cuối của phiên trước, tức kế hoạch nếu có;
   - báo cáo A/B trước đó, nếu có.

   `decision.json` có thêm `noedit_sessions`.

Đã kiểm trên mock (28/10/2026):
- `noop_command` đúng trên các lệnh thật của `lab-t2`: 7 lệnh rỗng bị chặn, 8 lệnh có việc thật không bị chặn;
- logic ngân sách của `BudgetBashTool`, kể cả undo và quá giới hạn;
- trích nguồn có dấu `>`/`+` và gộp cửa sổ;
- 8 tình huống của vòng, trong đó có "phiên 1 không sửa, phiên 2 sửa và thắng" và "cả 3 phiên không sửa".

**Lần chạy thật `fmax-lab-t3` (28/10/2026, 1 vòng): bị loại sau 71 phút, cả 3 phiên đều không sửa.**
- Phần mới chạy đúng:
  - trích nguồn đúng `FPU.scala`: `in_in2` ở dòng 900–907, `mulAddResult_pHiR_15` ở dòng 844/854, có dấu `+`;
  - lệnh rỗng bị chặn (5 lệnh);
  - nhắc ngân sách và giấu output đúng mốc;
  - phiên sau nhận lệnh và kế hoạch của phiên trước;
  - opencode nhận khoá `steps`.
- Phiên 0 dùng 37 lệnh, phiên 1 dùng 25 (dừng do `--edit-steps`), phiên 2 dùng 44. Câu trả lời của phiên 0 là
  một kế hoạch đúng hướng: chèn một tầng thanh ghi giữa mạng prefix, giữ nguyên latency.
- **Lỗi của loop:** lệnh 36–37 ở phiên 0 chính là lệnh sửa (heredoc python ghi lại `FPU.scala`). Hai lệnh này đến
  sau mốc 35 nên bị trả "Session over" và không được chạy. Như vậy ngân sách đã chặn đúng cái nó muốn ép ra. Phiên 2
  cũng gọi thêm 9 lệnh sau "Session over".

Đã sửa:
- `edit_command`: nhận ra lệnh định ghi file Scala. Đó là `sed -i`, `perl -i`, `open(..., 'w')`, `write_text(`,
  `> x.scala` hoặc `tee x.scala` (kèm điều kiện lệnh có nhắc `.scala`), `git apply`, `patch -p`.
- Lệnh sửa luôn được chạy và luôn hiện output, kể cả sau mốc 35.
- Nếu lệnh sửa không làm đổi file (ví dụ `str.replace` không khớp, vốn không báo lỗi), loop nói rõ "file như cũ",
  rồi cho thêm 5 lệnh để kiểm lại dòng hiện tại và sửa lại.
- Lệnh sửa được ghi đầy đủ vào `budget.jsonl` (`"attempt": true`, các lệnh khác cắt ở 400 ký tự).
  `budget_attempt` đưa toàn văn lần thử sửa cuối, kèm việc nó không được chạy hay chạy mà không đổi gì, vào note
  của phiên sau, với yêu cầu chạy lại ngay ở lệnh đầu.
- Đã kiểm trên mock: thử sửa sau mốc 35 vẫn chạy; sửa hỏng thì được thêm 5 lệnh rồi mới dừng; note có toàn văn
  lệnh. Mock của các mục trên vẫn pass.

**Lần chạy thật `fmax-lab-c1` (28/10/2026, `--agent claude --model sonnet --effort medium`, 1 vòng): bị loại sau
7,5 phút, nhưng lộ ra một lỗi của loop.**
- Agent Claude làm rất gọn. Phiên 1 dùng 2 lệnh, 25 s: đọc 3 dòng rồi sửa `FPU.scala`, dời 2 tầng prefix
  của bộ cộng FMA qua thanh ghi `mulAddResult`. Build qua ngay, không cần agent sửa build.
- Mỗi phiên tốn khoảng 15k token cache creation, 15–25k cache read và 1–3k output. Đó là một phần nhỏ của hạn mức
  gói Pro.
- A/B kém hơn 0,877 ns. Các thanh ghi mới `mulAddResult_pR/gR` (72 và 97 bit) nằm ngay trên đường găng, nên đánh
  giá đúng là kém hơn.
- **Lỗi của loop:** bản sửa đổi `MulAddRecFNPipe`, module dùng chung cho `sfma` và `dfma`, nên A/B synth cả `FPU`.
  Khi synth riêng `FPU`, worst path là `divSqrt` (+8,444 ns), thấp hơn slack của `dfma` trong tile (+8,745 ns).
  Vì vậy worst slack của module không bao giờ tăng được, và mọi bản sửa `dfma` đều không thể thắng A/B. Đã sửa:
  `loop_ab` nhận `target` và quyết định theo worst slack của endpoint trong mục tiêu (bước 4 ở trên).
- **Lỗi thứ hai:** phiên 2 đọc báo cáo A/B, hoàn tác bản sửa bằng `git apply -R`, rồi dừng (2 lệnh, 18 s). Vòng bị
  loại với lý do "không thay đổi gì so với start" dù còn 1 phiên. Đã sửa: phiên hoàn tác mà không sửa mới thì loop
  chuyển sang phiên sau, kèm note "Session k undid..." và báo cáo A/B của bản đã hoàn tác. Prompt phiên refine dặn
  không kết thúc bằng hoàn tác. Hai sửa này mới kiểm bằng mock (case 9, 9b, 10, 10b).

**Lần chạy thật `fmax-lab-c2` (28/10/2026, Claude, 3 vòng, `--ab-refines 3`, chu kỳ 21,2 ns): nhận vòng 2.**
- A/B theo endpoint trong mục tiêu và phiên chạy tiếp sau hoàn tác hoạt động đúng trên máy thật. Vòng 1: 4 phiên
  đều thua A/B (`dfma` −0,65/−0,52/−0,04/−0,99 ns). Vòng 2: phiên 3 thắng (`dfma` +0,600 ns), gate pass, tile
  8,7454 → 8,7515 ns (80,33 MHz, area −1,7 %). Tile gần như không nhanh hơn vì đường găng chuyển sang `fpuOpt.ifpu`
  (`IntToFP`), vốn đã ở mức ~8,75 ns. Vòng 3 (mục tiêu `ifpu`): A/B riêng `IntToFP` +0,20 ns nhưng cả tile 8,41 ns → loại.
  Equiv iter2 so với điểm xuất phát: PASS (`FPU` lockstep 10⁷ cycle, formal timeout).
- Nhiều đường gần găng ngang nhau (`dfma`, `ifpu`, `divSqrt`): sửa một đường thì đường kế tiếp thành găng, nên
  worst slack không phản ánh tiến độ → thêm `--tns-min-gain` (đo ở chu kỳ dưới đường găng để TNS âm). Mới kiểm bằng mock.

**Đo lại ở 10 ns (`measure_soc.py --period-ns 10`, run `p10`, 28/10/2026):**

| Điểm | Worst slack (ns) | Đường găng (ns) | Fmax (MHz) | Đường găng |
|---|---|---|---|---|
| base | −11,216 | 21,216 | 47,13 | `core.div` |
| cfg | −11,096 | 21,096 | 47,40 | `core.div` |
| best (`lab-c2` iter2) | −1,602 | 11,602 | 86,19 | `fpuOpt.ifpu` |
| best_cfg | −1,307 | 11,307 | 88,44 | `fpuOpt.ifpu.in_pipe_b_in1[6]` → `io_out_pipe_b_data[28]` |

Gate `best_cfg` pass. Ở 21,2 ns cùng bản này chỉ đo được 80,3 MHz (xem mục Đo timing). Còn thiếu ~1,3 ns để đạt 100 MHz.

**Lần chạy thật `fmax-lab-c4` (29/10/2026, Claude, 5 vòng, 10 ns, `--tns-min-gain 0.05 --wns-tol 0.5`, từ `lab-c2` best): nhận 1/5.**
- Vòng 3 nhận (sửa `IntToFP`, A/B `ifpu` +0,259 ns): worst slack −1,3067 → −1,1281 ns, TNS −2430,5 → −1270,6 ns,
  **88,44 → 89,86 MHz**, area 1 064 761 µm². Đường găng mới: `fpuOpt.divSqrt_2...sExp_Z` → `fpuOpt.regfile_ext.Memory`.
- Vòng 2: A/B `ifpu` +0,411 ns nhưng cả tile cho worst slack −1,9516 ns ngay trong `ifpu` (dưới sàn −1,8067) → loại
  sau 12 phút gate. Đây là lần thứ hai A/B module thắng mà tile thua (cùng `lab-c3`).
- Vòng 1, 4, 5: A/B không thắng sau 4 phiên. Phiên 4 của vòng 1 và phiên 3 của vòng 5 quay về đúng bản sửa
  của phiên 1 (cùng delta), loop đo lại.
- Sửa (mới kiểm bằng mock):
  - synth cả tile chạy trước, song song với gate; tile loại thì huỷ gate.
  - A/B thắng mà tile loại thì sang phiên sửa sau, đưa số liệu và `tile_paths.rpt` của tile cho phiên đó, không kết thúc vòng.
  - Patch thua A/B cũng vào `TRIED`, nên phiên sau làm lại y nguyên thì không build lại.

**Lần chạy thật `fmax-lab-c3` (29/10/2026, Claude, 4 vòng, 10 ns, `--tns-min-gain 0.05`, từ `lab-c2` best): không nhận vòng nào.**
- Điểm xuất phát (synth lại ở 10 ns): worst slack −1,3067 ns, TNS −2430,5 ns, 88,44 MHz.
- Cả 4 vòng cho kết quả giống hệt nhau tới 4 chữ số: Claude nộp lại đúng patch `IntToFP` của vòng 1 (đọc từ lịch sử, vì
  A/B của nó tốt hơn), mỗi lần ~0,5 phút. A/B: `ifpu` +0,135 ns, cả `FPU` +0,341 ns. Gate pass.
- Cả tile: TNS −2430,5 → −1329,8 ns (+45,3 %), nhưng worst slack −1,3067 → −1,6807 ns, đường găng mới
  `dcache.s2_req_addr[2]` → `core.mem_reg_wdata[62]` nằm ngoài phần đã sửa → dưới sàn `--wns-tol 0.1` → loại.
  Synth `-flatten` cho ABC chạy chung cả tile, nên sửa FPU cũng đổi kết quả của đường không liên quan.
- Sửa: loop nhớ patch của các vòng đã loại (`TRIED`); phiên sửa nộp lại y nguyên một patch đó thì không build, không
  đo lại, tree về best và phiên sau được báo phải sửa khác. Lịch sử ghi rõ khi A/B thắng nhưng tile thua. Thêm
  `patches/best_wns.patch` và `best_wns` trong `best.json`: điểm đã nhận có worst slack tốt nhất (Fmax để báo cáo),
  vì vòng nhận theo TNS có thể làm worst slack giảm; `--equiv` kiểm cả điểm này nếu nó khác `best`. Mới kiểm bằng mock.

**Lần chạy thật `fmax-lab-t4` (28/10/2026, 1 vòng): agent đã sửa được file nhưng build lỗi, bị loại sau 28 phút.**
- Lệnh 36 của phiên 0 (heredoc python) ghi `FPU.scala` và có `EDITED`; các lệnh 37–45 kiểm `git diff`. Bản sửa
  của agent lần đầu tiên đi đến bước build trong `--loop-ab`.
- Build lỗi lúc elaborate: `IndexOutOfBoundsException: -1 is out of bounds (min 0, max 48)`. Cả 2 lần gọi agent sửa
  build đều trả returncode −1: transcript cho thấy provider báo `Free usage limit reached` (lần 1 sau 7 bước, lần 2
  gần như ngay). Loop đã giết opencode sau 60 s im lặng như thiết kế. Phiên sửa trước đó đã dùng 45 bước trong khoảng 20 phút.
- Stack frame chỉ ra `FPU.scala:870` (trong `mulAddResult`, gọi từ dòng 868/826), tức chỗ mạng prefix agent viết.
- Đã thêm `--quota-wait 900 --quota-retries 3`: khi transcript có `Free usage limit reached`, loop chờ rồi gọi lại agent
  (phần đã sửa vẫn ở trong cây; file của lần bị cắt giữ dưới tên `<prefix>_quotaN_*`, tag cache mới `_qN`). Mới kiểm bằng mock.
- **Lỗi của loop:** `build_errors` chỉ giữ dòng có `Exception`/`[error]`, nên bỏ mất các stack frame `at ...(FPU.scala:N)`.
  Agent sửa build vì thế không biết lỗi nằm ở dòng nào. Đã sửa: giữ tối đa 12 frame của mã `freechips`/`chipyard`/`testchipip`
  sau mỗi dòng exception.

### Agent sửa lỗi build (`--build-fixes`)

Theo `build_with_debug_retry` / `debug_failure` của ví dụ `timing_opt`: khi build của vòng lỗi (ví dụ m4 vòng 3,
lỗi Scala ở `FPU.scala:340`), loop không loại vòng ngay mà gọi một agent sửa lỗi (`prompts/fix_build.md`). Agent
này nhận lỗi build (đã bỏ mã màu), tóm tắt của agent vòng đó và diff hiện tại. Nó phải sửa lỗi mà giữ nguyên ý
tưởng của bản sửa, không được revert. Sau đó loop kiểm phạm vi lại và build lại. Mặc định làm tối đa
`--build-fixes 2` lần (`0` là hành vi cũ). Mỗi lần agent có `--fix-timeout` (900 s) và chạy model
`--fix-model` (mặc định bằng `--model`). Có `--ab-tool` thì agent sửa lỗi cũng có `synth_ab_rebuild_verilog`
để tự kiểm bản sửa compile được hay không. Câu trả lời được cache theo `agent_<run-name>_iter<i>_fix<k>_llm`.
File ghi ra: `runs/iter<i>/fix<k>_{prompt,response,transcript}.*` và `build_fix<k>.log`; `patches/iter<i>.patch`
là diff cuối cùng. Đã chạy trên mock: sửa được ở lần 1, hết số lần sửa, `--build-fixes 0`, có/không
`--ab-tool`. Chưa chạy thật.

### Chọn model của agent (`--model`, `--fix-model`)

`--model` nhận mọi id dạng `<provider>/<model>` mà opencode trong container opencode biết (mặc định
`opencode/big-pickle`). Xem danh sách model, kể cả model free của OpenCode Zen (thường là `big-pickle` và các
id có đuôi `-free`; danh sách thay đổi theo thời gian). CHIA thêm hậu tố vào tên container (ví dụ
`chia-chipyard-$USER-0`), nên phải dò tên:

```bash
O=$(docker ps --format '{{.Names}}' | grep -i opencode | head -1); echo "$O"
docker exec "$O" bash -lc 'opencode models opencode'
# thử nhanh một model:
docker exec "$O" bash -lc 'opencode run --model opencode/<id> "say hi"'
```

Model phải gọi được tool MCP (`chipyard_bash`, `synth_ab`).

### Agent Claude Code (`--agent claude`)

Thay opencode bằng Claude Code (`SalvageClaudeLLM`, kế thừa `ClaudeCodeLLM` của CHIA), thường dùng
`--model sonnet --effort medium`. Mặc định vẫn là `--agent opencode`. Cách hoạt động:

- **Tool.** `--tools ""` tắt mọi tool sẵn có của Claude (chúng chạy trong container claude, không có
  Chipyard). `--strict-mcp-config` chỉ nạp `chipyard_bash`. Prompt gọi tool bằng tên của Claude,
  `mcp__chipyard_bash__chipyard_bash_run_command`.
- **Giới hạn bước.** Phiên sửa có `--max-turns` = `--edit-steps` + 10. Chạm mốc này thì Claude dừng
  mà không trả lời, nên chặn chính vẫn là mốc HARD của `BudgetBashTool`. Agent sửa build không giới hạn
  lượt, chỉ có `--fix-timeout`.
- **Hết giờ.** `ClaudeCodeLLM` gốc ở chế độ stream chờ `claude` không giới hạn. Bản này giết tiến trình
  sau `timeout_seconds`. Event stream của lần chạy hết giờ, lỗi hoặc hết hạn mức được ghi vào
  `LLM_PARTIAL_FILE` trên worker claude, rồi loop chép về `<prefix>_transcript.txt`.
- **Hết hạn mức.** Khi gặp "You've hit your limit · resets ...", CHIA ném `RateLimitError`. Loop chờ tới
  giờ reset cộng 2 phút rồi gọi lại, với tag `_qN`, tối đa `--quota-retries` lần. Nếu giờ reset xa hơn
  `--quota-max-wait` (6 giờ, tức hạn mức tuần) thì bỏ, không chờ.
- **Chưa hỗ trợ** `--ab-tool` và `--bypass-llm`. `config_audit.py` vẫn chỉ dùng opencode.
- **Worker.** Node `hello_claude` trong `cluster.yaml` dùng resource `claude_creds` và mount `~/.claude`
  của host (đăng nhập `claude` trên host trước, gói Pro được).

Gói Pro dùng chung hạn mức với các phiên chat. Một phiên sửa 35–45 lượt gọi tool tốn khá nhiều token, nên
hạn mức 5 giờ có thể chỉ đủ vài phiên.

Kiểm nhanh trước khi chạy vòng:

```bash
chia job submit --working-dir . -- python probe_tools.py --agent claude --models sonnet --effort medium
```

Probe này phải in ra `answer_ok=True`. Phần này đã kiểm bằng mock (CLI `claude` giả: chạy xong, hết giờ,
hết hạn mức, chạm `--max-turns`). **Probe chạy thật (28/10/2026):** `claude-sonnet_effort-medium rc=0 6s
tool_in_stream=True answer_ok=True` (gói Pro, `claude` 2.1.311 trong image, node `hello_claude` lên được bằng
`chia up`). Vòng agent với Claude chưa chạy thật.

## Đầu ra: `~/rocket-fmax-out/<run>/`

```
results.csv            iter, accepted, wns_ns, tns_ns, worst_slack_ns, fmax_mhz, critical_path,
                       isa_pass, bench_cycles_match, reason, area_um2, elapsed_min,
                       target, ab_delta_ns, ab_sessions (--loop-ab)
patches/iter<N>.patch  git diff trong generators/rocket-chip so với commit gốc (gộp cả bản tốt nhất trước đó)
patches/best.patch     bản tốt nhất (rỗng nếu không vòng nào được nhận)
runs/baseline/         timing_paths.rpt, timing_endpoints.rpt, timing_families.txt, sta.log, yosys.log, gate.json, cycles.csv, build.log
runs/iter<N>/          prompt.md, agent_response.md, agent_transcript.txt, diff_files.txt, build.log,
                       gate.json, gate_compare.txt, sim_fail.log, timing_*.rpt, decision.json
                       (--ab-tool) agent_ab_experiments.jsonl: mỗi lần rebuild/A-B của agent; agent_ab_cleanup.txt
                       (build lỗi) fix<k>_prompt.md, fix<k>_response.md, fix<k>_transcript.txt, build_fix<k>.log
                       agent hết --llm-timeout: agent_transcript.txt vẫn có; agent_response.md rỗng
                       (--loop-ab) target.json; edit<k>_{prompt.md,response.md,transcript.txt,round.diff,budget.jsonl};
                       build_e<k>.log; ab<k>.txt (kết quả A/B); lỗi build: e<k>fix<j>_*, build_e<k>fix<j>.log
profile/               ChiaProfileCollector.log + timeline html
```

Cache baseline nằm ở `~/chia_cache`, mỗi hàm một tag: `baseline_rtl`, `baseline_gate`,
`baseline_synth_<flow>_p<period>` (cache chỉ phân biệt theo tag, không theo tên hàm).

`rv64ui-p-ma_data` fail ngay trên baseline (load/store lệch địa chỉ). Nếu xác nhận đúng như vậy,
chạy với `--exclude-isa rv64ui-p-ma_data`: test này bị loại khỏi gate, các test khác vẫn phải pass.

## Áp lại bản tốt nhất

Patch là diff so với commit rocket-chip của image (`70430823…`, nhánh `chia_artifact`):

```bash
docker cp ~/rocket-fmax-out/<run>/patches/best.patch chia-chipyard-$USER-0:/tmp/best.patch
docker exec chia-chipyard-$USER-0 bash -lc 'cd /home/ray/chipyard/generators/rocket-chip && git checkout -- . && git apply /tmp/best.patch && git diff --stat'
```

Cuối mỗi lần chạy, loop cũng để sẵn bản tốt nhất trong container chipyard.

## Trạng thái kiểm chứng

- **Đã kiểm trên máy người dùng** (probe 25/10/2026):
  - riscv-tests nằm ở `$RISCV/riscv64-unknown-elf/share/riscv-tests`, có 107 test ISA `-p` và đủ 6 benchmark.
  - Module `RocketTile` (`gen-collateral/RocketTile.sv`) có cổng `clock` và `reset`.
  - Có 4 SRAM `rockettile_*_ext` trong `.top.mems.conf`.
  - `chia-cacti` có `sta` và `openroad`, Ray 2.54.0. Tùy chọn của `report_checks` là `-group_count`.
- **Đã kiểm trong sandbox:**
  - Script Yosys chạy với yosys 0.70 trên một thiết kế thử kiểu firtool (blackbox SRAM, EICG, tên `_reg`).
  - Bộ lọc liberty bỏ 36 cell.
  - Driver chạy hết với CHIA/Ray giả và một chipyard giả: baseline, đọc lại từ cache, Mốc 2 bypass, và các trường hợp loại (lệch cycle, sửa config, sửa ngoài rocket-chip, build lỗi, không đổi gì, không nhanh hơn).
- **Chạy thật lần 1 (25/10/2026):**
  - Image eda build được: Yosys 0.38+92, `sta` có, liberty đúng sha256.
  - Cluster lên đủ 4 worker, `verilator_run` 4 slot.
  - ELF: 107 test ISA và 6 benchmark.
  - Build baseline mất 1,7 phút, gom được 471 file Verilog.
  - Lỗi: `'_ProfiledResult' object has no attribute 'generated_src_files'`. Khi profiler bật, ref
    của `chia_remote` truyền thẳng sang task khác sẽ đến nơi dưới dạng lớp bọc. Đã sửa bằng cách
    luôn `get()` giá trị rồi mới truyền.
- **Chạy thật lần 2 (25/10/2026), baseline ở 10 ns với luồng `v1` (chưa có `-constr`):**
  - Yosys mất 3,4 phút, STA 8 giây. Area 1.021.382 um².
  - Worst slack −70,7 ns, tức đường găng khoảng 80,6 ns, trên đường
    `frontend.fq.valid_0_reg -> core.ex_reg_rs_msb_0[34]_reg`.
  - Con số quá lớn vì ABC không chèn buffer: mọi cell đều là `_1`. Đã đổi sang `v2`.
  - Gate mất 15,5 phút: 106/107 test ISA pass, `rv64ui-p-ma_data` fail.
  - Benchmark: dhrystone 225027, median 6117, multiply 27281, qsort 173402, towers 4075,
    vvadd 2476 cycle. Đổi seed (`+verilator+seed+7`) số cycle vẫn y hệt. Tuy vậy không thấy dòng
    "seed" trong output, nên chưa chắc plusarg này có tác dụng.
- **Chạy thật lần 3 (25/10/2026), luồng `v2` ở 10 ns:**
  - Yosys 2,5 phút. Worst slack −11,22 ns, tức đường găng 21,2 ns, khoảng 47 MHz. Area 1.067.643 um².
  - Đường găng là `core.div.divisor[1]_reg -> core.div.remainder[65]_reg`: bộ trừ 65 bit trong
    MulDiv, synth thành chuỗi `maj3` ripple.
  - Kế tiếp là `div.count`, `dfma` postMul pipe_b (−10,48), `mem_reg_wdata` (−6,17), `sfma`,
    `ifpu`, `dcache.pstore2_storegen_data`.
  - Gate ở baseline: 106/107 pass, `ma_data` được loại bằng `--exclude-isa`.
- **Chạy thật lần 4 (25/10/2026), luồng `v3`:** Yosys lỗi sau 0,4 phút, vì `+/choices/kogge-stone.v`
  không có trong Yosys 0.38 (file xuất hiện từ tag `yosys-0.40`). Đã bỏ `v3`, chốt `v2`. (Ghi chú
  này từng kết luận chuỗi `maj3` không phải bộ cộng ripple vì `$lcu` mặc định là Brent-Kung. Mốc 3
  cho thấy kết luận đó sai: thay `-`/`+` bằng prefix adder viết tay rút ngắn hẳn đường găng, tức
  luồng Yosys + ABC này thực tế vẫn cho ra chuỗi carry dài.)
- **Chạy thật lần 5 (25/10/2026), baseline chính thức, Mốc 1 xong:** luồng `v2`, chu kỳ cố định
  **21,2 ns** (dùng cho mọi vòng). Worst slack −1,00 ns, TNS −106,5 ns, đường găng 22,2 ns
  (~45,0 MHz), area 1.069.069 um², Yosys 2,6 phút. Đường găng `core.div.divisor[1]_reg ->
  core.div.remainder[63]_reg` có khoảng 40 cell `maj3` nối tiếp qua chân C (chuỗi carry ripple).
  Ở 10 ns đường găng là 21,2 ns, ở 21,2 ns là 22,2 ns: `abc -D` map khác nhau theo chu kỳ, nên
  chỉ so slack giữa các lần chạy cùng chu kỳ. Ở 21,2 ns chỉ còn họ `core.div.*` vi phạm;
  `dfma` pipe_b có slack +0,21 ns.
- **Chạy thật lần 6–7 (25/10/2026), Mốc 2 xong:** `--iters 1 --bypass-llm`, chạy 2 lần (`m2a`, `m2b`).
  Vòng 1 thêm comment vào `ALU.scala`, build 1,3 phút, gate pass (cycle khớp), slack −1,0012 ns
  bằng baseline → bị loại với lý do "slack không tốt hơn", đúng như dự kiến. Một vòng mất 13,4–14,4
  phút. Lần 2: `baseline_rtl`, `baseline_gate`, `baseline_synth_v2_p21p2` đều HIT. Có
  `results.csv`, `patches/iter1.patch`, `best.patch`, `runs/iter1/*` và timeline html.
- **Chạy thật lần 8 (25/10/2026), Mốc 3 xong:** 5 vòng agent thật (`m3`), tổng ~116 phút.

  | Vòng | Kết quả | Worst slack (ns) | Fmax ~ (MHz) | Agent sửa |
  |---|---|---|---|---|
  | baseline | | −1,001 | 45,0 | |
  | 1 | nhận | 1,003 | 49,5 | `Multiplier.scala`: phép trừ của bộ chia → Kogge-Stone |
  | 2 | nhận | 4,501 | 59,9 | `FPU.scala`: `a*b +& c` của DFMA → cây CSA 3:2 + prefix adder |
  | 3 | loại | 3,963 | 58,0 | carry-select trong FMA + `AMOALU.scala`: chậm hơn vòng 2 |
  | 4 | nhận | 6,755 | 69,2 | `FPU.scala`: dời tầng prefix cuối qua thanh ghi `mulAddResult` có sẵn |
  | 5 | nhận | 6,980 | 70,3 | `ALU.scala`: bộ cộng → Kogge-Stone |

  - Mọi vòng: ISA pass, mcycle của 6 benchmark khớp đúng. Area 1.069.069 → 1.083.776 um² (+1,4 %).
  - Yosys tăng từ 2,6 lên 9–13 phút vì logic prefix lớn hơn.
  - Đường găng cuối: `dcache.pstore1_data -> dcache.pstore2_storegen_data_r_7` (AMOALU).
  - **Cách hiểu kết quả:** phần lớn mức tăng đến từ việc viết tay bộ cộng/nhân nhanh thay cho
    `+`/`-`/`*`. Công cụ thương mại (DesignWare) thường tự làm được việc này, nên mức tăng
    Fmax chỉ có nghĩa trong luồng Yosys + ABC này. Vòng 4 là thay đổi cấu trúc thật: dời logic qua
    thanh ghi pipeline có sẵn, không thêm tầng.
  - **Mức kiểm chứng:** gate chỉ gồm riscv-tests `-p` và 6 benchmark. Các thay đổi FPU (vòng 2, 4)
    mới được kiểm bằng `rv64uf/ud` (ít vector). Chưa có kiểm equivalence hình thức.
  - Họ endpoint đặt tên kiểu `mulAddResult_gHiR_36_reg` trước đây không được gom; nay gom cả `_<N>_reg`.
- **Chạy thật lần 9 (25/10/2026), kiểm tương đương `best.patch` của `m3` (`--equiv-only`, `m3-equiv`):
  PASS.** Module đổi: `FPU`, `MulAddRecFNPipe_l2_e{11_s53,8_s24,5_s11}`, `MulDiv`, `RocketALU`.
  `FPU` cũng đổi text nên các `MulAddRecFNPipe_*` được kiểm chung trong đơn vị `FPU`.

  | Đơn vị | Formal | Lockstep 10⁷ cycle |
  |---|---|---|
  | `RocketALU` | proven 1812/1812 (3 s) | pass |
  | `MulDiv` | unproven 675/741 (8 s) | pass |
  | `FPU` | timeout (900 s) | pass |

  Chỉ `RocketALU` được chứng minh hình thức; `MulDiv` và `FPU` dựa vào lockstep ngẫu nhiên ở mức
  module (FPU nhận lệnh ngẫu nhiên, nên FMA chỉ được kích ở một phần các cycle).
  Container chipyard có Verilator 5.022 (`.conda-env/bin`, sau khi `source env.sh`).
- **Mốc phụ (`config_audit.py`): chạy thật xong (26/10/2026, `fmax-audit-4`, `~/rocket-fmax-out/audit2`).**
  Bảng khối bảo vệ tìm đủ 10 khối trong `ChipTop` baseline (không có dòng `NOT FOUND`). Area `ChipTop`
  (trừ `RocketTile` và SRAM) 1 183 581 µm², SRAM 2 143 744 bit. Agent (`fmax-audit-3`) đề xuất 2 ứng viên;
  vì nó ghi `candidates.json` bằng `bash` của opencode nên lần đó loop không thấy, lần `fmax-audit-4` chạy lại
  với `--candidates-file` (file lấy từ container opencode):

  | Ứng viên | Fragment | Kết luận | Area bớt | SRAM bớt |
  |---|---|---|---|---|
  | `mbus_scratchpad` | `testchipip.soc.WithNoScratchpads` | bỏ được, mcycle giữ nguyên | 39 592 µm² (3,35 %) | 532 480 bit (64 KiB) |
  | `bus_error_devices` | `chipyard.config.WithNoBusErrorDevices` | bỏ được, mcycle giữ nguyên | 21 636 µm² (1,83 %) | 0 |
  | gộp | cả hai | bỏ được, mcycle giữ nguyên | 61 207 µm² (5,17 %) | 532 480 bit |

  Đã xuất `patches/chipyard-config.patch` (`RocketFmaxSoCConfig` + `RocketFmaxSoCArty100TConfig`). Kiểm tay
  (26/10/2026): patch `git apply` được; `make verilog CONFIG=RocketFmaxSoCConfig` trong `sims/verilator` qua.
  Bản Arty **chưa elaborate được vì image**: project `chipyard_fpga` của nhánh `chia_artifact` không compile
  (6 lỗi `needs to be abstract ... def wf_active: chisel3.Bool`, ở harness arty, arty100t, datastorm,
  nexysvideo, vc707, vcu118; `HasHarnessInstantiators` có thêm `wf_active` mà harness FPGA chưa cài). Lỗi y
  hệt khi không có patch (`RocketArty100TConfig` gốc), nên không do file sinh ra. Lưu ý: bỏ error device thì truy cập địa chỉ không ánh xạ có thể treo bus thay vì
  báo lỗi truy cập (gate không có test nào làm việc này). Vivado/ORFS chưa chạy.
  Trước đó (mock): chặn fragment, chặn build mất cổng JTAG, patch `git apply` được.
  - `equiv.py` với Yosys 0.70 (yowasp) và Verilator 5.020: chứng minh được bộ cộng viết lại
    (16/16 proven), bắt được lỗi cài vào (lockstep lệch), chứng minh cả trường hợp dời thanh ghi
    qua bộ cộng; phép nhân đảo thứ tự thì formal `timeout`, lockstep pass.
  - `--equiv-only` và `--equiv` chạy hết trên mock; patch mock đổi `*` thành `+` bị báo lệch.
  - `config_audit.py` chạy hết trên mock: ứng viên bỏ được, ứng viên làm hỏng harness (gate trượt),
    fragment sai dạng (bị bỏ qua), fragment không tồn tại (build lỗi); lần 2 baseline HIT cache.
  - Probe 25/10/2026: image eda có Yosys 0.38 nhưng không có Verilator; trong `bash -lc` của
    container chipyard không thấy `verilator` (nên `equiv_sim` source `env.sh` trước).
