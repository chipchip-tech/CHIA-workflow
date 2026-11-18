# soc-fmax: Fmax cả SoC `MinimalSoCConfig` trên sky130hd

Nhánh `project/soc-fmax-sky130` (mở 15/11/2026, tách từ `setup`). Khác project cũ cùng tên ở nhánh `project/soc-fmax`
(xong 06/11/2026, Chipyard 1.10/`chia_artifact`, RocketConfig, đo bằng synth): project này dùng Chipyard gốc 1.15.0,
`chipyard.MinimalSoCConfig`, và đo bằng ORFS **sau place**, tới CTS/route/GDS.

Báo cáo tóm tắt kiểu học thuật (phương pháp, kết quả Fmax, thảo luận): `report.md`, cập nhật sau mỗi mốc cùng README.

## Mục tiêu

Loop CHIA để agent (Claude Code, Opus, effort medium) tối ưu Fmax của toàn bộ SoC (`DigitalTop`: RocketTile, L2
InclusiveCache, bus, uncore), đo bằng OpenROAD-flow-scripts sky130hd sau global place. Mục tiêu cuối **200 MHz (5 ns)
có GDS**. Điểm xuất phát: `MinimalSoCConfig` gốc, không patch (không dùng lại patch `asic-6`/`asic-7`).

Đầu ra mỗi đợt (ở `~/soc-fmax-runs/<run>/`): baseline đo thật, `best.patch`, `results.csv`, `history.md`, báo cáo ORFS
và GDS của mỗi bậc đã đạt.

## Pha (`docs/workflow-phases.md`)

| | |
|---|---|
| Pha đi qua | **Simulate** (bắt buộc) → **ASIC**. Không làm FPGA. |
| Pha hiện tại | M1–M3 xong. M4 đang chạy: `m4-b` đạt bậc 10 ns sau place (**101,98 MHz**, WNS +0,194); `m4-c` (9 ns) không nhận vòng nào vì nhiễu place ở 9 ns quá lớn (±2,275 ns) → luật đo lặp (so trung vị 4 lần place); `m4-d` chạy thật: nhận r16, trung vị place **103,64 MHz** (9,649 ns) @ 9 ns; `m4-e` (A/B của agent, chạy thật lần đầu): nhận r21, trung vị **107,76 MHz** (9,280 ns), chưa đạt 9 ns |
| PDK / corner | sky130hd (`sky130_fd_sc_hd`), `tt_025C_1v80` |
| SRAM | fakeram (`scripts/fakeram_sky130.py`, `docs/fakeram-sky130.md`): access 1,5 ns, 3 µm²/bit, timing giả định. OpenRAM thật chỉ ở M6 |
| Bậc chu kỳ | 10 → 9 → 8 → 7 → 6 → 5 ns. Bản tốt nhất có WNS ≥ 0 sau place ở bậc hiện tại → bậc kế (`loop.py` tự xuống bậc, giữ variant ORFS của bậc đã đạt). CTS + route + GDS (M5) làm khi đạt 5 ns (người dùng chọn 16/11/2026) |
| Ngân sách (so với baseline `MinimalSoCConfig`) | chi phí cycle (trung bình nhân `mcycle / mcycle_baseline`) ≤ +3 %; diện tích logic (không SRAM) ≤ +15 % |

**Simulate.** Gate: riscv-tests `rv64ui/um/ua/uf/ud-p-*` + 8 benchmark (dhrystone, median, multiply, qsort, towers,
vvadd, mm, spmv), `minstret` của mọi benchmark bằng baseline; thêm `func_check.py` từ M2. Test ISA hỏng sẵn ở baseline
được ghi vào `known_fail.json` và không tính. Pha này cũng cho agent thêm tầng pipeline, chỉ đo cycle, ra
`candidates.json` (M3).
- Vào: Chipyard 1.15.0 dựng xong, `MinimalSoCConfig` cài (`scripts/install-chipyard-config.sh`).
- Ra: gate pass trên baseline, cycle baseline đo thật, danh sách ứng viên.

**ASIC.**
- Vào: baseline đo thật sau place ở 10 ns, ngưỡng nhiễu đã đo (M2).
- Nhận một vòng khi: gate pass, cycle ≤ +3 %, diện tích ≤ +15 %, và Fmax sau place hoặc điểm họ (tổng slack các họ
  endpoint xấu nhất) tăng đủ: từ 16/11/2026 so **trung vị 4 lần place** (đo lặp, xem mục func_check và luật nhận).
- Ra: 5 ns đạt WNS ≥ 0 sau place, có CTS + route + GDS; M6 thay fakeram bằng OpenRAM.

## Cách đo

| Mức | Công cụ | Dùng để |
|---|---|---|
| 1 | `make synth` + `make floorplan` của ORFS (Yosys phân cấp `SYNTH_HIERARCHICAL=1`, ABC ở chu kỳ mục tiêu; floorplan sửa timing) + OpenSTA với Liberty fakeram | chọn vòng cộng dồn trong đợt (theo floorplan) |
| 2 | `make place` (global place + `repair_design` + detailed place), RC ước lượng từ placement | nhận/loại bản cộng dồn cuối đợt |
| 3 | CTS + route + GDS (M5) | khi đạt một bậc |

- Fmax = 1000 / (chu kỳ − slack xấu nhất **không kể đường chạm cổng I/O**). Cổng I/O trễ 0 so với clock; đường I/O báo
  riêng (vùng `I/O`), không vào Fmax. WNS mọi đường cũng được ghi.
- Một clock lý tưởng `clk` trên mọi cổng `*clock` của `DigitalTop`. Cổng clock của miền bất đồng bộ (`*clock_in`,
  `*TCK`: `serial_tl_0_clock_in`, `debug_systemjtag_jtag_TCK`) có clock riêng `aux0/aux1` chu kỳ 5 × `clk`, khai
  `set_clock_groups -asynchronous` với `clk`; thiếu chúng thì 2115 thanh ghi (serial TL, JTAG) không có clock và không
  được tính. Reset bất đồng bộ, chân async của flop và tầng đầu
  bộ đồng bộ (`*sync_0*`) không tính (như soc-fmax cũ).
- `EICG_wrapper` (clock gate kiểu latch của rocket-chip) được thay bằng cell ICG `sky130_fd_sc_hd__dlclkp_4`.
- Slack theo vùng tính trên **mọi** endpoint (chân D của flop + chân vào dữ liệu của macro, bỏ chân clock của macro),
  không chỉ 5000 đường xấu nhất (`TOP_SLACK` trong `report.tcl`, `top_slack_<bước>.txt`).
- Vùng theo instance cấp một của `DigitalTop` (`soc_nodes.REGION_RULES`): `tile`, `L2`, `bus`, `uncore`, `I/O`.
  **Luật viết trước khi thấy build thật**: bước `rtl` in vùng gán cho từng instance để chỉnh.
- Không đo lại RTL đã đo: RTL hash (bỏ chú thích vị trí nguồn của firtool) nằm trong tag cache và tên variant ORFS.
- Giới hạn thời gian: build 3 giờ, synth 3 giờ, floorplan 2 giờ, place 3 giờ (`--*-timeout`); quá giờ thì giết cả nhóm
  tiến trình. Container asic giới hạn RAM bằng `ASIC_MEM` (dùng `20g`).

## func_check và luật nhận (M2)

- `func_check.py` chạy dhrystone, qsort, mm, spmv với `+verbose` và đọc vết commit của Rocket. Mỗi dòng in khi một lệnh
  rời pipeline: `C0: <cycle> [valid] pc=[..] W[rd=..][wen] R[..] R[..] inst=[..]`. Vết ứng viên được so với vết
  baseline từng lệnh một: `pc`, `inst`, `valid` phải giống hệt, và giá trị ghi phải giống khi cả hai bên có `wen=1`.
  Không so cycle.
- Những phần phụ thuộc thời gian thì không so:
  - lệnh trước `0x80000000`, tức bootrom chờ nạp ELF;
  - giá trị đọc từ bộ đếm (`mcycle`, `minstret`, `cycle`, `time`, `hpmcounter`). Giá trị này "nhiễm" (taint) và
    taint lan qua thanh ghi và bộ nhớ (địa chỉ = giá trị nguồn + imm). Gặp nhánh hoặc `jalr` có nguồn nhiễm thì
    dừng so;
  - từ lần ghi đầu tiên vào `tohost` (symbol trong ELF, tức printf hoặc exit qua HTIF) thì dừng.
- Vết baseline được nén và lưu trong cache (tag `ftrace_`). Ứng viên được so ngay trên worker verilator (tag
  `fcheck_`), không ghi vết ra đĩa. Bước `func` của baseline tự so baseline với chính nó ở seed 7: kết quả phải
  **ĐẠT**, nếu không thì `func_check` đang báo nhầm.
- `policy.py` nhận một vòng khi đủ cả các điều kiện sau:
  - gate pass (trừ test hỏng sẵn) và `minstret` bằng baseline;
  - `func_check` đạt;
  - chi phí cycle ≤ 1,03 và diện tích logic ≤ 1,15 × baseline;
  - so với **bản tốt nhất hiện tại** (cùng chu kỳ ràng buộc), theo chu kỳ đạt được sau place (1000 / Fmax = chu kỳ −
    WNS), **đo lặp** (phương án A, duyệt 16/11/2026, vì ở 9 ns place của cùng RTL dao động 1,5 ns):
    - một lần place chỉ để lọc: xấu đi quá ngưỡng nhiễu thì loại; tốt hơn **trung vị** của điểm so sánh (hoặc điểm họ
      tăng quá ngưỡng nhiễu) thì là "hứa hẹn", còn lại "trong nhiễu";
    - vòng hứa hẹn qua gate nhanh thì place thêm 3 lần với biến thể nhiễu (`PLACE_DENSITY` ±0,01, `CORE_UTILIZATION`
      +1); nhận (cộng dồn) khi **trung vị chu kỳ đạt được của 4 lần** tốt hơn trung vị của điểm so sánh ít nhất
      `--repeat-gain` (mặc định **0,367 ns**, mức dao động do đổi netlist đo ở baseline), hoặc trung vị điểm họ tăng
      quá ngưỡng nhiễu; không thì ghi `đo lặp`;
    - trung vị của bản tốt nhất lấy từ chính 4 lần đo nhiễu trên nó khi xuống bậc (hoặc từ `noise.json` khi đo trên
      nó); điểm chưa đo lặp dùng một lần đo; "bậc đạt" và kết thúc đợt sớm cũng theo WNS trung vị khi đã đo lặp.
- Trong đợt (pha asic) vòng được cộng dồn theo số sau place (hướng B) + gate nhanh; synth và floorplan không dùng để
  chọn. Cuối đợt các vòng cộng dồn qua luật nhận đầy đủ theo Fmax place giảm dần.
- Ngưỡng nhiễu lấy từ `soc_fmax.py --noise`. Baseline chạy lại 4 lần: một lần y hệt (chỉ đổi tên variant),
  `PLACE_DENSITY` ±0,01 và `CORE_UTILIZATION` +1, mỗi lần đi từ synth tới place. Mỗi khi xuống bậc, `loop.py` đo lại
  trên bản tốt nhất ở bậc mới (3 lần nhiễu, bỏ lần chạy y hệt vì ORFS tất định).
  Ngưỡng = max(1,5 × khoảng chênh, mức sàn). **Từ 16/11/2026 ngưỡng tính theo ns chu kỳ đạt được** (sàn 0,03 ns), không
  theo MHz: `noise.json` của baseline cho ±0,367 ns (bằng ±1,546 MHz ở 65 MHz, nhưng ±3,7 MHz ở 100 MHz). Điểm họ:
  sàn 0,2 ns.

## File

| File | Nội dung |
|---|---|
| `soc_fmax.py` | driver M1: các bước `rtl`, `gate`, `synth`, `floorplan`, `place` (`--until`), cache, bảng thời gian/RAM, `results.csv`, `summary.md` |
| `soc_nodes.py` | node chipyard (`tree_state` mọi submodule, `load_test_elfs`), verilator (`run_gate`), tách RTL `DigitalTop` (`SocRTL`), vùng, họ endpoint. Chép chọn lọc từ `rocket-fmax/fmax_nodes.py` |
| `orfs_nodes.py` | chuẩn bị design ORFS (host), `orfs_stage` trên khối asic (make + báo cáo, có timeout), đọc log `Elapsed`/`Peak memory` |
| `orfs/report.tcl` | báo cáo trên kết quả một bước: WNS/TNS, 5000 đường xấu nhất (mỗi endpoint một đường), slack theo instance cấp 1 trên mọi endpoint, đếm thanh ghi theo clock, nhóm thanh ghi không clock, diện tích logic/macro |
| `orfs/diag_floorplan.tcl` | chẩn đoán tay: macro (bbox, halo), blockage, và inst/pin/row trong một hộp (`SOCFMAX_BOX`) |
| `func_check.py` | vết commit (`+verbose`) của 4 benchmark: ghi vết baseline, so ứng viên (taint bộ đếm, dừng ở `tohost`) |
| `policy.py` | luật nhận/loại (place, ngưỡng theo ns chu kỳ đạt được), ngưỡng nhiễu (hàm thuần) |
| `loop.py` | đợt N vòng: áp đầu chuỗi → agent đề xuất patch → build (lỗi thì agent sửa, tối đa `--fix-retries`) → `--phase sim`: gate + func → ứng viên vào `candidates.json`; `--phase asic` (mặc định): synth → floorplan → so đầu chuỗi → gate nhanh → cộng dồn; cuối đợt place + gate đủ bản cộng dồn, hỏng thì lùi → hoàn nguyên cây; `history.md`, `history.json`, `best.patch`, `results.csv`, log agent ở `runs/r<N>/`. Cuối job dọn ORFS, chỉ giữ bản tốt nhất. `--bypass empty,comment,broken,file:<patch>` thay agent |
| `route_check.py` | hiệu chỉnh một lần: một patch qua place → `make cts` → `make route`, so Fmax/đường găng place với CTS (clock thật) và route (RC OpenRCX); không thuộc loop |
| `cal_5ns.py` | hiệu chỉnh một lần: baseline và bản tốt nhất của từng job qua synth → floorplan → place ở chu kỳ ép 5 ns, cộng 3 biến thể nhiễu cho bản cuối; không thuộc loop |
| `ab_tool.py` | công cụ A/B của agent (MCP `ab`, pha asic): `make verilog` bản sửa đang có trên khối chipyard (áp tạm rồi hoàn nguyên), ORFS synth + `2_1_floorplan` của một module (child vs parent) trên khối asic; xem mục **A/B của agent** |
| `patchutil.py` | `split_patch` (tách patch theo submodule), dùng chung cho `loop.py` và `ab_tool.py` |
| `agent.py` | agent Claude Code trên khối claude: chép `.scala` của các submodule được sửa từ mount Chipyard chỉ đọc sang `/tmp`, viết `TASK.md` + `context/` (báo cáo ORFS baseline, lịch sử, lỗi build), chạy `claude --print` (`ClaudeCodeLLM`, có timeout), diff bản chép ra patch |
| `memwatch.py` | RAM đỉnh từng container theo bước (`docker stats` mỗi 5 s, trên host) |
| `exp.yaml` | cache/bypass: `ChiselBuildNode.build`, `extract_soc_rtl`, `run_gate`, `run_func`, `orfs_stage`; bypass agent (`agent_bypass_*`) |

ORFS: design của mỗi variant ở `~/tools/OpenROAD-flow-scripts/flow/designs/sky130hd/soc_fmax/<variant>/`, kết quả ở
`flow/{results,logs,reports,objects}/sky130hd/soc_fmax/<variant>/`. Variant = `<RTL hash 12>_p<chu kỳ>_<FLOW_VERSION>_<fakeram>`.
Tag cache của bước ORFS thêm `REPORT_VERSION`: đổi riêng `report.tcl` thì chỉ chạy lại báo cáo (`make` không làm gì).
Design dir có `tapcell.tcl` riêng (`TAPCELL_TCL`): như tapcell của sky130hd nhưng cắt row trong halo macro 40 µm và bỏ
mảnh row ngắn hơn 80 µm (`-row_min_width`).

## Chạy M1

Cập nhật repo và cài config (cần thấy `GIỐNG` hoặc `MỚI`, rồi `MIXIN_OK`):

```bash
cd ~/CHIA-workflow && git fetch origin && git checkout project/soc-fmax-sky130 && git pull && scripts/install-chipyard-config.sh
```

Bật cluster (tắt cluster cũ trước nếu đang chạy: `chia down cluster.gen.yaml -y`):

```bash
conda activate chia_env && cd ~/CHIA-workflow && ASIC_MEM=20g python scripts/chia-up-blocks.py --asic --verilator-slots 4
```

Từng bước, cùng `--run-name` (bước đã xong đọc từ cache). Bước 1, build + tách `DigitalTop`:

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m1-rtl --working-dir . -- python soc_fmax.py --run-name m1 --until rtl
```

```bash
chia job logs -f m1-rtl
```

Sau đó lần lượt `--until gate` (`m1-gate`), `--until synth`, `--until floorplan`, `--until place` (đổi `--submission-id`
mỗi lần). Lần chạy cuối cùng (kiểm cache), chạy lại toàn bộ với tên mới: mọi bước phải in `cache HIT`:

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m1-cache --working-dir . -- python soc_fmax.py --run-name m1-cache
```

File theo bước (`orfs_<bước>.json`, `top_slack_*.txt`, `paths_*.rpt`, `noclock_*.txt`...) nằm ở
`~/soc-fmax-runs/<run>/runs/baseline/`.

Gửi lại: toàn bộ log của job, `~/soc-fmax-runs/m1/summary.md`, `results.csv`; nếu lỗi ở bước ORFS thì thêm đuôi
`flow/logs/sky130hd/soc_fmax/<variant>/*.log`.

Tuỳ chọn hay dùng: `--orfs-var PLACE_DENSITY=0.5` (hoặc `CORE_UTILIZATION=35`; tạo variant mới), `--make-jobs 16`,
`--fakeram-access-ns 1.5`, `--allow-dirty` (khi cây Chipyard có sửa tay), `--clean-build`.

## Chạy M2

Bước 1 tính vết baseline, rồi tự so baseline với chính nó ở seed khác (phải **ĐẠT**). Sau đó đo nhiễu bằng 4 lần
chạy baseline từ synth tới place, mỗi lần khoảng 14 phút, tổng khoảng 1 giờ. Build, gate và ORFS của baseline đọc từ
cache.

```bash
cd ~/CHIA-workflow && git pull && cd projects/soc-fmax && chia job submit --no-wait --submission-id m2-noise --working-dir . -- python soc_fmax.py --run-name m2-noise --noise
```

Bước 2 chạy 3 vòng loop với agent bị bypass:
- vòng 1: patch rỗng, hỏi lại 2 lần rồi ghi `rỗng`;
- vòng 2: chú thích cuối `RocketCore.scala`, build thật nhưng RTL hash không đổi, ra `RTL trùng`;
- vòng 3: dòng Scala sai, ra `build lỗi`.

Sau mỗi vòng cây Chipyard phải về đúng digest baseline.

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m2-loop --working-dir . -- python loop.py --run-name m2-loop --noise-json ~/soc-fmax-runs/m2-noise/noise.json --rounds 3 --bypass empty,comment,broken
```

Chạy tiếp một đợt cũ: `--history-from ~/soc-fmax-runs/<run>`. Cây Chipyard bị sửa trong lúc loop chạy, nên đừng build
tay trong `~/tools/chipyard` khi đang có job.

## Chạy M3 (agent thật, pha sim)

Agent (Claude Code, `opus`, effort `medium`, tối đa 30 phút mỗi phiên) đọc báo cáo ORFS baseline sau place và đề xuất
một thay đổi (thường là thêm tầng pipeline trên đường găng). Mỗi vòng: build → gate (14 phút) → func (9 phút); qua cả
hai và chi phí cycle ≤ +3 % thì thành **ứng viên** (`candidates.json`, `runs/r<N>/candidate.patch`). Ở pha này không
chạy ORFS, và vòng nào cũng đi từ baseline. Agent sửa trên bản chép, không build được, không được tạo/xoá file hay sửa
file chưa track (`MinimalSoCConfig.scala`). Build lỗi thì agent sửa tối đa 2 lần; gate/func hỏng thì agent chẩn đoán
(ghi vào `history.md`). Hết lượt dùng hoặc lỗi đăng nhập thì dừng đợt.

Khối claude cần mount Chipyard chỉ đọc (template trên `setup` từ 15/11/2026), nên phải dựng lại cluster:

```bash
cd ~/CHIA-workflow && git pull && python scripts/chia-up-blocks.py --down
```

```bash
ASIC_MEM=20g python scripts/chia-up-blocks.py --asic --verilator-slots 4
```

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m3-a --working-dir . -- python loop.py --phase sim --run-name m3-a --noise-json ~/soc-fmax-runs/m2-noise/noise.json --rounds 5
```

Tham số agent: `--agent-model`, `--agent-effort`, `--agent-timeout` (giây), `--agent-max-turns`, `--fix-retries`,
`--agent-keep-workspace` (giữ bản chép trong `/tmp/socfmax-agent/` của container claude để xem lại).

**Kết quả `m3-b` (chạy thật 15/11/2026, 5 vòng, khoảng 17–18 phút mỗi vòng, agent 1,2–2,7 phút):** 4 ứng viên, 1 hỏng
func. r1 retiming `dfma` (dời tầng thanh ghi thừa sau round lên giữa preMul và bộ nhân, giữ latency 4) trong
`FPU.scala`; r2, r4, r5 rút ngắn đường `mshrs → Scheduler request.ready → sinkA putbuffer` của L2 mà không đổi chu kỳ
(ghi sớm `ListBuffer`, `ready` riêng cho kênh A, so tag tính trước theo MSHR); r3 thêm `Queue(sinkA.io.req, 2)` → hỏng
func: lệch ở lệnh load đọc lại `mcycle` đã lưu (taint của `func_check` không theo giá trị bộ đếm qua bộ nhớ), nên mọi
patch đổi số chu kỳ trước vùng đo đều hỏng func dù đúng chức năng. Chưa có số ORFS cho các ứng viên.

## Chạy M4: pha asic, đợt cộng dồn đo bằng place (duyệt 16/11/2026, hướng B)

`loop.py` chỉ còn hai pha: `--phase sim` (M3, gate mọi vòng, không ORFS) và `--phase asic` (mặc định), luôn chạy theo
đợt cộng dồn:

1. Mỗi vòng: áp đầu chuỗi (bản tốt nhất + các vòng đã cộng dồn trong đợt) → agent sửa → build (lỗi thì agent sửa, tối
   đa 2 lần) → RTL trùng thì bỏ → ORFS synth → floorplan → **place** → so với đầu chuỗi (một lần đo để lọc) →
   hứa hẹn thì **gate nhanh** → **đo lặp** (3 lần place nhiễu, so trung vị, xem luật nhận) → tốt hơn đủ thì (`--quick-gate isa`: riscv-tests ISA; `full`: ISA +
   benchmark + ngân sách cycle; `none`) → **cộng dồn**, vòng sau xây trên đó. Agent nhận
   `context/r<N>_place.txt` của các vòng trước (và của bản tốt nhất cũ khi đi tiếp). Synth và floorplan chỉ ghi để
   tham khảo: ở `m4-a` floorplan bão hoà quanh 100 MHz ở 10 ns (sửa timing dừng khi slack ≥ 0), không phân biệt được vòng.
2. Cuối đợt, **chỉ ở đây chạy gate đủ** (gate + func + cycle ≤ +3 %): các vòng đã cộng dồn xếp theo Fmax place giảm
   dần (trong chuỗi Fmax tăng dần, nên đây là thứ tự lùi), lần lượt gate; bản đầu tiên qua → **nhận** (`best.patch`),
   các vòng trước nó trong chuỗi ghi `trong bản nhận`. Không agent chẩn đoán, không giới hạn số lần lùi.
3. Cuối job: `summary.md` tổng hợp job (bản tốt nhất đầu/cuối job so với baseline: Fmax, WNS, điểm họ, diện tích,
   cycle, đường găng, slack theo vùng; bảng các vòng; thứ tự gate cuối đợt; thời gian theo bước; RAM đỉnh; variant
   ORFS giữ lại). ORFS chỉ giữ variant của bản tốt nhất: job có bản tốt hơn thì xoá cả variant của bản tốt nhất cũ.
4. Đợt sau: `--history-from ~/soc-fmax-runs/<run>`: bản tốt nhất cũ được dựng lại từ cache (build + ORFS tới place) để
   agent có báo cáo place của nó.

5. **Bậc chu kỳ** (`--ladder 10,9,8,7,6,5`, duyệt 16/11/2026):
   - một vòng cộng dồn có WNS ≥ 0 sau place ở bậc hiện tại → **kết thúc đợt sớm** (gate đủ cuối đợt ngay);
   - bản được nhận có WNS ≥ 0 → **bậc đạt**: ghi vào `history.json`/`summary.md`, giữ variant ORFS của nó (cho M5),
     rồi `--on-met next` (mặc định) xuống bậc kế ngay trong job: đo lại bản tốt nhất ở bậc mới, đo nhiễu trên nó
     (3 lần place, khoảng 45 phút; `--rung-noise keep` để dùng lại ngưỡng cũ), các vòng còn lại chạy ở bậc mới.
     `--on-met stop`: dừng job;
   - đạt bậc cuối (5 ns) → dừng job, sang M5;
   - baseline luôn đo ở 10 ns (cache). Đi tiếp bằng `--history-from`: bậc bắt đầu là chu kỳ của bản tốt nhất cũ (hoặc
     `--period-ns`), ngưỡng nhiễu lấy từ lịch sử nếu cùng bậc; bản tốt nhất cũ đã đạt bậc đó thì xuống bậc ngay đầu job.

Ước lượng một đợt 5 vòng: 5 × (agent 2–5 + build + synth 4 + floorplan 1–2 + place 9,5 + gate nhanh) ≈ 2 giờ, cộng
khoảng 45 phút đo lặp cho mỗi vòng hứa hẹn (3 × synth + floorplan + place: biến thể nhiễu là variant ORFS khác nên synth
và floorplan chạy lại, chưa dùng chung được) và gate đủ khoảng 23 phút cho mỗi bản thử ở cuối đợt. Trong đợt có thể có tới 5 variant tới place (2,3 GB mỗi cái) trước khi dọn.

Đợt đầu (đã chạy, bản cũ của pha asic: lọc vòng bằng floorplan):

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m4-a --working-dir . -- python loop.py --run-name m4-a --noise-json ~/soc-fmax-runs/m2-noise/noise.json --rounds 5
```

Đợt kế, đi tiếp từ `m4-a` (đã chạy):

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m4-b --working-dir . -- python loop.py --run-name m4-b --noise-json ~/soc-fmax-runs/m2-noise/noise.json --history-from ~/soc-fmax-runs/m4-a --rounds 5
```

Đi tiếp từ `m4-b` (r8 đã đạt 10 ns → đầu job xuống 9 ns, đo r8 và nhiễu ở 9 ns, rồi 5 vòng):

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m4-c --working-dir . -- python loop.py --run-name m4-c --noise-json ~/soc-fmax-runs/m2-noise/noise.json --history-from ~/soc-fmax-runs/m4-b --rounds 5
```

Đi tiếp từ `m4-c` với luật đo lặp (9 ns; trung vị của r8 lấy từ 4 lần đo nhiễu của `m4-c`: 98,96 MHz, 10,105 ns):

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m4-d --working-dir . -- python loop.py --run-name m4-d --noise-json ~/soc-fmax-runs/m2-noise/noise.json --history-from ~/soc-fmax-runs/m4-c --rounds 5
```

Đi tiếp từ `m4-d` (9 ns, bản so sánh r16 với trung vị 103,64 MHz / 9,649 ns từ 4 lần đo của `m4-d`); từ 17/11/2026 mặc
định có A/B của agent (mục **A/B của agent**, `--ab-runs 0` để tắt):

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m4-e --working-dir . -- python loop.py --run-name m4-e --noise-json ~/soc-fmax-runs/m2-noise/noise.json --history-from ~/soc-fmax-runs/m4-d --rounds 5
```

Đi tiếp từ `m4-e`, **ép 5 ns, tắt A/B** (người dùng chọn 18/11/2026 sau `cal-5ns-4`): `--period-ns 5` bỏ qua các bậc 9 → 6
ns (không bão hoà, không mất ~1 giờ đo lại mỗi lần xuống bậc); bậc 5 ns là bậc cuối nên đạt thì dừng (M5). Bản so sánh r21
đo lại ở 5 ns và ngưỡng nhiễu mới (`--rung-noise measure`) lấy hết từ cache của `cal-5ns-4` (tag cache ORFS chỉ theo variant
và bước; cùng RTL, chu kỳ, biến ORFS → cùng variant): trung vị 4 lần 106,62 MHz / 9,379 ns. Luật nhận không đổi (trung vị 4
lần place tốt lên ≥ 0,367 ns). `--ab-runs 0`: A/B module không đoán được place ở mức 0,4 ns (`m4-e`), A/B cả SoC tới
floorplan xếp hạng sai (`cal-5ns-4`).

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id m4-f --working-dir . -- python loop.py --run-name m4-f --noise-json ~/soc-fmax-runs/m2-noise/noise.json --history-from ~/soc-fmax-runs/m4-e --period-ns 5 --ab-runs 0 --rounds 5
```

**Kết quả `m4-a` (chạy thật 16/11/2026, 10 ns, sau khi chạy lại từ đầu: `m2-noise` cho lại đúng baseline 65,423 MHz và
ngưỡng ±1,546):** cả đợt khoảng 1,5 giờ (vòng 8–17 phút, agent 1,6–4,6 phút, synth 4, floorplan 1,1–2,6, place 9,4
phút, RAM đỉnh 4,6 GB). Ngưỡng nhiễu floorplan tự tính ra mức sàn ±0,3 MHz / ±0,2.

| vòng | thay đổi | floorplan | kết quả |
|---|---|---|---|
| r1 | `FPU.scala`: retiming `dfma` (thanh ghi sau preMul, bỏ tầng ra rỗng, latency giữ 4) | 80,13 → 92,54 | cộng dồn |
| r2 | `BufferParams` flow depth 1 → `flowQueue1`; putbuffer L2 ghi sớm (`eagerWrite`) | → 100,14 (slack +0,014) | cộng dồn |
| r3 | `eagerQueue` cho TLBuffer depth 2 | 100,21 | trong nhiễu |
| r4 | L2 `ListBuffer` cổng `peek` theo MSHR, so tag song song với chọn MSHR | 100,14 | trong nhiễu |
| r5 | gộp r3 + r4 viết lại + `ToAXI4` dùng `flowQueue1Irrevocable` | 102,51 | cộng dồn |

Cuối đợt: r1+r2+r5 (RTL `9c7a2d547185`) **nhận ngay lần đầu**: place **86,860 MHz** (WNS −1,513, +21,4 MHz / +33 %),
điểm họ −4,162; gate + func đạt, cycle 1,0000, logic 2,289 mm² (+2,5 %). Đường găng place chuyển sang FPU `sfma`
(`in_in2 → mulAddResult_pipe_b`), L2 −0,420, bus +1,502. Synth vẫn 52,4–52,8 MHz (`tlMasterXbar → btb`), không dùng.

Nhận xét: ở 10 ns floorplan **bão hoà** quanh 100 MHz: bước sửa timing của floorplan dừng khi slack ≥ 0, nên sau r2 mọi
họ đường nằm sát slack 0 và r3, r4 (sửa đúng đường xấu nhất) đều ra "trong nhiễu"; trong khi place vẫn thiếu 1,5 ns. Agent
tự gộp hai thay đổi ở r5 để vượt ngưỡng. Bậc chu kỳ chưa đổi (place chưa có WNS ≥ 0); `loop.py` chưa tự xuống bậc.
Vì vậy đổi sang hướng B (mỗi vòng tới place), xem đầu mục.

**Kết quả `m4-b` (chạy thật 16/11/2026, hướng B, đi tiếp từ `m4-a`, 10 ns):** 2,1 giờ, 5 vòng (mỗi vòng 19–25 phút:
agent 1,9–2,3, synth 4,2, floorplan 1,1–2, place 9,1–9,3 phút), gate đủ cuối đợt 14,7 phút, RAM đỉnh 9,5 GB (bước
build). r5 dựng lại từ cache đúng như dự kiến (mọi bước `cache HIT`).

| vòng | thay đổi | floorplan | place | WNS place | kết quả |
|---|---|---|---|---|---|
| r6 | `FPU.scala`: `sfma` đăng ký riêng A·B và C, dời `+C` sang tầng postMul (`addAfterReg`) | 102,59 | 95,28 | −0,495 | cộng dồn |
| r7 | `IBuf`/`RocketCore`: `inst0_valid_buf` (Shannon theo `imem.valid`) trong `mem_wrong_npc` | 102,39 | 100,17 | +0,017 | cộng dồn |
| r8 | L2 `SourceD`: Shannon theo `bs_wadr.ready` cho khối bypass | 98,51 | **101,98** | **+0,194** | cộng dồn → **nhận** |
| r9 | `IntToFP`: mở rộng dấu trước thanh ghi vào | 98,51 | 95,37 | −0,485 (L2 `putbuffer/head`) | place kém hơn |
| r10 | L2 `ListBuffer`: ghi sớm `head`/`next` của putbuffer | 98,51 | 98,40 | −0,163 (`icache → fq`) | place kém hơn |

Cuối đợt: r5+r6+r7+r8 (RTL `2370465e8eb7`) **nhận ngay lần đầu**: place **101,979 MHz** (WNS **+0,194**, +15,1 MHz so
với r5, +36,6 MHz / +55,9 % so với baseline), cycle 1,0000, logic 2,284 mm² (+2,3 %). Slack vùng: tile 0,194 (FPU
`ifpu`), L2 0,207, bus 1,339, uncore 2,782. ORFS chỉ giữ variant `2370465e8eb7_p10_o4_fr1a1p5u3`.

**Kết quả `m4-c` (chạy thật 16/11/2026, đi tiếp từ `m4-b`):** 2,68 giờ, RAM đỉnh 8,9 GB (build). Đầu job tự xuống bậc
9 ns đúng như mock: đo lại r8 ở 9 ns ra **96,255 MHz** (WNS −1,389, L2 `requests/valid → mshrs_*/w_pprobeack`; cùng
RTL ở 10 ns là 101,979: place ở chu kỳ chặt hơn cho kết quả khác), rồi 3 lần nhiễu: density− 101,831, density+ 105,132,
util+1 90,674 MHz. Chu kỳ đạt được dao động 9,512..11,029 ns (khoảng 1,517) → ngưỡng **±2,275 ns** (1,5 × khoảng),
điểm họ ±20,1. Ba lần nhiễu chạy lại cả synth và floorplan (variant khác tên) dù hai bước này không đổi: phí khoảng 19 phút.

| vòng | thay đổi | place | WNS place | TNS | kết quả |
|---|---|---|---|---|---|
| r11 | L2: `ListBuffer` `peek_head`/`peek_all`, so tag với mọi entry | 103,78 | −0,636 (`dfma`) | −118 | trong nhiễu (+0,753 ns) |
| r12 | L2: `peekAll`, so tag sẵn cho head 3 list | 94,54 | −1,578 | −1467 | diện tích 2,584 mm² > 1,15× |
| r13 | L2: `headKey`, thanh ghi giữ tag phần tử đầu | 103,85 | −0,630 (`dfma`) | −25 | trong nhiễu (+0,759 ns) |
| r14 | r13 + `dfma` tích con ở preMul | 101,54 | −0,849 (L2 `keys`) | −225 | trong nhiễu (+0,540 ns) |
| r15 | L2: `keyOf` + Mem `nextKey` | **104,07** | −0,609 (`dfma`) | **−6** | trong nhiễu (+0,780 ns) |

Không vòng nào vượt ngưỡng nên không gate, bản tốt nhất giữ r8. Nhận xét: ba cách sửa L2 khác nhau (r11, r13, r15) đều
đưa đường găng sang `dfma` ở khoảng −0,61..−0,64 ns và TNS từ −1316 xuống −6..−118 (r8 qua 4 lần đo: −149..−2099),
tức là sửa L2 có tác dụng thật; nhưng Fmax place của chính r8 dao động 1,5 ns chỉ vì đổi density/util, lớn hơn mọi
cải thiện một vòng. Với luật hiện tại loop sẽ kẹt ở 9 ns. ORFS giữ `2370465e8eb7_p9_o4_fr1a1p5u3` và `..._p10_...`.

**Kết quả `m4-d` (chạy thật 17/11/2026, luật đo lặp lần đầu chạy thật, đi tiếp từ `m4-c`, 9 ns):** 4,46 giờ, 5 vòng,
RAM đỉnh 9,8 GB (build). Bản so sánh r8: trung vị 4 lần đo của `m4-c` 98,964 MHz (10,105 ns). Đo lặp tốn 135 phút
(synth 39 + floorplan 12 + place 84) trên tổng 268 phút: 3 vòng hứa hẹn × 45 phút như dự kiến.

| vòng | thay đổi | place (1 lần) | 4 lần đo (MHz) | trung vị | so với bản so sánh | kết quả |
|---|---|---|---|---|---|---|
| r16 | L2 `ListBuffer` thanh ghi `keys` + Mem `nextKey` (tag đầu hàng đợi, bỏ chuỗi đọc Mem); `dfma` tích con A×B[7 bit] ở preMul, cây CSA 3:2 viết tay | 101,87 | 101,87 / 105,47 / 101,68 / 109,63 | 103,64 (9,649 ns) | +0,456 ns ≥ 0,367 | **nhận** (gate đủ 13,7 phút) |
| r17 | L2: tính sẵn hazard `evictSafe` của `SourceD` cho từng MSHR, `Mux1H` 1 bit | 106,96 | 106,96 / 108,93 / 103,91 / 103,69 | 105,41 (9,487 ns) | +0,162 ns | không đủ |
| r18 | r17 + tách `IntToFP` 2 tầng (sau 1 lần sửa build) | 93,28 | | | −1,071 ns | trong nhiễu, không đo lặp |
| r19 | làm lại r18 | 108,02 | 108,02 / 102,33 / 108,97 / 99,52 | 105,10 (9,515 ns) | +0,134 ns | không đủ |
| r20 | r17 viết lại + putbuffer ghi sớm `head`/`next` | 103,29 | | | −0,032 ns | không hứa hẹn |

r16: WNS place −0,817 (một lần đo), logic 2,379 mm² (+6,6 %), cycle 1,0000; slack vùng L2 −0,817, tile −0,245, bus
+0,947, uncore +1,685. ORFS giữ `f0f3ab6946f1_p9_o4_fr1a1p5u3`. Tổng từ baseline: trung vị 103,64 MHz, +58 %.

Nhận xét:
- **Luật đo lặp chạy đúng** lần đầu trên máy thật: lọc một lần đo (r18, r20 không tốn đo lặp), nhận r16 nhờ trung vị,
  chặn r17/r19 (một lần đo +0,30/+0,39 ns nhưng trung vị chỉ +0,16/+0,13 ns).
- 4 lần place của cùng một RTL vẫn dao động 0,46–0,87 ns (r16: 9,12..9,84 ns). Một lần đo của r17/r19 rơi vào nửa tốt,
  đúng tình huống luật này được thiết kế để chặn.
- Ý `evictSafe` của r17 có vẻ có tác dụng nhỏ thật (cả r17, r19 đều +0,13..0,16 ns theo trung vị) nhưng dưới ngưỡng;
  agent lặp lại ý này 3 lần (r17, r19, r20).
- Từ r17, **floorplan đã đạt 9 ns** (111,12 MHz, WNS +0,001) nên lại bão hoà; khoảng cách còn lại (khoảng 0,5 ns) nằm ở
  place. Đường găng của các biến thể r16..r20 chuyển dần sang tile (FPU `ifpu`, `dfma` → regfile, `icache → fq`).
- Biến thể `util+1` của r16 cho 109,63 MHz (WNS −0,122), gần đạt 9 ns: tham số place giờ ảnh hưởng ngang một vòng RTL.

**Kết quả `m4-e` (chạy thật 17/11/2026, A/B của agent chạy thật lần đầu, đi tiếp từ `m4-d`, 9 ns):** 3,15 giờ, 5 vòng,
RAM đỉnh 10,5 GB. Agent 45,8 phút cho 5 phiên (8–10 phút/phiên, trước đây 2–5 phút), gồm 8 lần `make verilog` (2 lỗi
compile được agent tự sửa trong phiên) và 8 lần đo A/B. Chỉ 1 vòng hứa hẹn nên đo lặp chỉ tốn 45 phút.

| vòng | thay đổi | A/B @ 5 ns (module) | place (1 lần) | trung vị | so với r16 (9,649 ns) | kết quả |
|---|---|---|---|---|---|---|
| r21 | L2: `SourceD` tính sẵn so set/way cho từng MSHR (`hazard_safes`), `Scheduler` chỉ chọn 1 bit; s2 của `SourceD` nạp theo `s2_ready` | Scheduler +0,760 | 108,01 | 107,76 (108,01 / 108,36 / 107,51 / 104,81) | +0,369 ns ≥ 0,367 | **nhận** (gate đủ 13,2 phút) |
| r22 | `ShiftQueue` (`frontend/fq`): enable `!valid(i) \|\| deq.ready` | RocketTile +0,002 (họ `fq` rời top 25) | 106,42 | | −0,130 ns (so với r21) | không hứa hẹn |
| r23 | `IntToFP`: chọn kiểu + trị tuyệt đối dời trước thanh ghi vào | IntToFP +1,961, RocketTile 0 | 103,02 | | −0,427 ns | không hứa hẹn |
| r24 | `ListBuffer` ghi sớm con trỏ, `requests` `eagerWrite` | Scheduler 0 (2 lần) | 106,27 | | −0,130 ns | không hứa hẹn |
| r25 | gộp r22 + r23 | IntToFP +1,961, RocketTile +0,002 | 102,39 | | −0,487 ns | không hứa hẹn |

r21: WNS place −0,258 (một lần đo), logic 2,365 mm² (+5,9 %), cycle 1,0000; slack vùng tile −0,258, L2 +0,117, bus
+1,233, uncore +1,555. ORFS giữ `c5785e5bdb1e_p9_o4_fr1a1p5u3`. Tổng từ baseline: trung vị 107,76 MHz, +65 %.

Nhận xét:
- **A/B chạy thật được ngay lần đầu:** `make verilog` trên khối chipyard, `2_1_floorplan` có trong ORFS của image, không
  lỗi hạ tầng; cây Chipyard sạch sau job. Agent dùng tool đúng cách: bắt lỗi compile trong phiên (r21, r25), đo module mình
  sửa và cả `RocketTile`, tự ghi giới hạn của A/B (không thấy dây, không thấy cây fanout).
- r21 vượt ngưỡng sát nút (+0,369 so với 0,367 ns). Sau r21 đường găng sang tile (`icache → fq`, `ifpu`); L2 dương.
- **A/B module không đoán được place ở mức 0,4 ns:** r23 rút `IntToFP` 1,96 ns trong A/B nhưng place kém hơn 0,43 ns;
  r22/r25 không đổi đường xấu nhất của `RocketTile` (−2,91 ns @ 5 ns, đường `ex_reg_rs_msb` không ai sửa), nên A/B ở mức
  tile không thấy được họ `fq`/`ifpu` sát 0 ở 9 ns. Một lần place của 4 vòng sau đều trong khoảng dao động 0,46–0,87 ns
  của place, chưa nói được là xấu thật.
- Floorplan bão hoà ở 9 ns (111,12–111,23 MHz mọi vòng); Fmax ở bước synth của ORFS cố định 52,84 MHz mọi vòng (xem
  mục **Vì sao Fmax ở synth thấp**).

### Vì sao Fmax ở synth thấp (so với `rocket-fmax`, `soc-fmax` cũ)

`rocket-fmax` báo 140,01 MHz và `soc-fmax` cũ báo 107,70 MHz ở "synth", còn cột synth của project này là ~52 MHz. Ba con số
đo ba thứ khác nhau:

| | `rocket-fmax` `asic-7` | `soc-fmax` cũ `atom8` | project này, cột synth | project này, place |
|---|---|---|---|---|
| Thiết kế | chỉ `RocketTile` | cả `DigitalTop` | cả `DigitalTop` | cả `DigitalTop` |
| Flow | Yosys `v3` + OpenSTA, `abc -D` 6 ns | Yosys `v3` (+ Kogge-Stone) + global placement + `repair_design` | `1_synth` của ORFS | ORFS tới `3_place` |
| Buffer net fanout lớn | không | có (`repair_design`, fanout 16) | **không** | có |
| SRAM | hộp đen (không tính) | macro giả 0 ns | fakeram 1,5 ns | fakeram 1,5 ns |
| Dây | không | ước lượng từ global placement | không | ước lượng từ placement |
| Fmax | 140,01 | 107,70 @ 10 ns | 52,84 @ 9 ns | trung vị 107,76 @ 9 ns |

- Netlist ngay sau `1_synth` của ORFS chưa có buffer: đường găng `tlMasterXbar/beatsLeft → frontend/btb/table_ext/Memory[0]`
  (BTB là mảng flop, một net lái hàng nghìn chân) bị tính trễ rất lớn. Đường này giống nhau ở mọi vòng nên cột synth đứng
  yên 52,84 MHz; bước floorplan (`repair_timing`) và place (`repair_design`) xử lý được nó. Cột synth của ORFS vì thế
  không phải Fmax, chỉ dùng để kiểm synth chạy được.
- Số `rocket-fmax` chỉ là tile, không buffer, không dây, SRAM không tính, `abc` ép chặt 6 ns (cùng RTL `asic-6` cho 109,17
  MHz ở 10 ns, 113,53 MHz ở 6 ns). Khi đo cả SoC trong cùng project, baseline chỉ 54,81 MHz; Vivado thật trên FPGA chỉ +7,9 %.
- Số gần nhất để so là `soc-fmax` cũ (107,70 MHz): cả SoC, có buffer và ước lượng dây, nhưng SRAM 0 ns, không detailed
  placement, và dùng patch tile `asic-7` (2345 dòng) cộng L2 và atomics. Place hiện tại (107,76 MHz, SRAM 1,5 ns) đã ngang
  mức đó với patch nhỏ hơn nhiều.

Nhận xét:
- **Bậc 10 ns đã đạt sau place** (WNS ≥ 0). Từ đây place ở 10 ns cũng bão hoà như floorplan: các họ đường đều sát slack
  0, sửa thêm khó vượt ngưỡng.
- r9, r10 kém hơn r8 tới 0,36–0,68 ns ở **vùng không bị sửa** (L2 `putbuffer` khi sửa FPU; `icache → fq` khi sửa L2):
  đây là dao động của place khi đổi netlist, lớn hơn nhiễu đo ở baseline.
- Ngưỡng ±1,546 MHz đo ở 65 MHz tương đương ±0,367 ns chu kỳ; ở 100 MHz cùng 0,367 ns là khoảng ±3,7 MHz. Tính theo ns
  thì r8 hơn r7 chỉ 0,177 ns, tức **trong nhiễu**; r8 vẫn được nhận đúng luật hiện tại (ngưỡng theo MHz).
- Floorplan và place lệch nhau: r8 làm floorplan tụt (98,51, đường mới `sourceD/busy → s4_adjusted_opcode`) nhưng
  place tăng. Hướng B đo bằng place là đúng.
- Synth vẫn không dùng được (51,4–52,5 MHz).

## A/B của agent (duyệt 17/11/2026, theo `examples/timing_opt` của CHIA)

Trước đây agent nộp patch "mù": không build được, không đo được, nên mất vòng vì lỗi build (r18) hoặc lặp lại một ý
không có tác dụng (r17/r19/r20). Từ 17/11/2026, ở `--phase asic` (task đề xuất, không bypass), phiên agent có MCP server
`ab` (`ab_tool.py`, khung lấy từ `synth_ab` của `rocket-fmax` và `TimingExperimentTool` của CHIA):

| tool | việc |
|---|---|
| `rebuild_verilog` | diff bản chép của agent → áp tạm lên `~/tools/chipyard` trên khối chipyard → `make verilog` → hoàn nguyên; trả lỗi compile hoặc danh sách module đổi |
| `list_modules` | module dưới `DigitalTop`, đánh dấu module bị sửa |
| `start_ab(module)` + `ab_result` | ORFS synth + `2_1_floorplan` (bước đầu floorplan: `repair_timing` đổi cỡ cell, không chèn buffer, không đặt macro/PDN) của **một module**, bản sửa (child) và đầu chuỗi (parent), ở `--ab-period` (5 ns, đặt chặt để không bão hoà ở slack 0); chỉ đường thanh ghi→thanh ghi trong module; trả slack, Δ và bảng họ endpoint |

- Không dùng timing ngay sau synth: ở `soc-fmax` Fmax synth không đi theo place (r17 synth tệ nhất 44,7 MHz nhưng place tốt
  nhất), ở `rocket-fmax` A/B bằng synth cho 47 → 140 MHz nhưng FPGA thật chỉ +7,9 %.
- Giới hạn `--ab-runs` 3 lần đo mỗi phiên (đo lại đúng Verilog cũ hoặc lần lỗi không tính); `make verilog` không giới hạn.
  Bật A/B thì `--agent-timeout` mặc định 3600 giây (không bật: 1800). Effort giữ `medium` (giới hạn usage của gói Pro).
- **Luật nhận không đổi** (place + đo lặp). A/B chỉ là tín hiệu cho agent: không có dây giữa các khối, không có nhiễu place.
- An toàn cây Chipyard: patch tạm và process group của `make verilog` ghi vào `/tmp/socfmax_ab/applied.json` trên khối
  chipyard trước khi chạy; sau mỗi phiên agent, loop gọi `ab_idle` (chờ khối chipyard rảnh, giết `make` sót, `git apply -R`
  phần sót). Agent thoát khi `make verilog` đang chạy thì loop chờ nó xong (vài phút). Cuối vòng `check_tree` vẫn kiểm
  digest như cũ.
- Parent là RTL của đầu chuỗi, giữ trong bộ nhớ của loop (không có thì build lại, thường cache HIT). Kết quả ORFS của A/B
  ở variant `ab_*`, xoá cuối job (`--keep-orfs` để giữ). Nhật ký từng vòng: `runs/r<N>/ab.jsonl`, dòng "A/B của agent" trong
  `history.md`, số lần trong `summary.md`.
- Tắt: `--ab-runs 0`.

**Kiểm chứng (17/11/2026): mới mock trong sandbox** (Ray 2.54, `chialoops` 2.0.1, `claude` giả gọi tool qua MCP thật,
Chipyard giả có submodule git và `make verilog` giả có macro SRAM, ORFS giả): sửa → rebuild → `start_ab` ra Δ và bảng họ;
đo lại cùng Verilog thì trả kết quả cũ; module không đổi/không có bị từ chối; sửa sau rebuild thì báo Verilog cũ; lỗi
compile trả đúng dòng `[error]`; hết lượt thì từ chối; agent thoát khi `make verilog` đang chạy → `ab_idle` chờ xong và
cây sạch; `prune_ab` xoá thư mục `ab_*`; `config.mk` của module con có `DESIGN_NAME` = module, fakeram của macro trong
module. **Chưa chạy thật:** `make verilog` của Chipyard 1.15.0 trên khối chipyard (thời gian, thư mục `generated-src`), đích
`2_1_floorplan` trong bản ORFS của image (bản cũ không có thì tự chạy cả `floorplan`), thời gian ORFS của module con,
agent thật có dùng tool đúng cách không.

## Hiệu chỉnh ở 5 ns (`cal_5ns.py`, duyệt 18/11/2026)

Câu hỏi: nên ép cả loop ở 5 ns, và A/B của agent nên chạy ở đâu (một module như hiện tại, hay cả SoC tới floorplan @ 5 ns).
Ở 9 ns floorplan bão hoà (111,12 MHz mọi vòng); ở 5 ns slack luôn âm nên có thể phân biệt được các bản. Người dùng chọn chỉ
đo baseline và các bản tốt nhất để tiết kiệm thời gian.

- Điểm (`--runs m4-a,m4-c,m4-d,m4-e`): baseline, r5, r8, r16, r21 (bản tốt nhất của từng job, `best.patch` cộng dồn, kiểm RTL
  hash khớp `history.json`). Mỗi điểm: build/tách RTL (cache), ORFS synth → floorplan → place ở `--period-ns` (mặc định 5).
  Rồi 3 biến thể nhiễu cho bản cuối (density ±0,01, `util+1`; `--noise-variants 0` để tắt). Bước lỗi không dừng job.
- Mỗi điểm đo ở chu kỳ khác loop nên là variant ORFS mới, không phải đo lại cùng thiết lập. Cuối job chỉ giữ ORFS của bản cuối.
- `summary.md`: bảng synth/floorplan/place @ 5 ns cạnh số loop (9/10 ns); cải thiện giữa hai điểm liên tiếp theo chu kỳ đạt
  được (loop, floorplan, place); xếp hạng; nhiễu place @ 5 ns của bản cuối; thời gian và RAM.
- **Hạn chế:** các bản tốt nhất cách nhau ≥ 0,37 ns nên xếp hạng gần như chắc khớp; phép thử chính là cặp r16 → r21 (0,369 ns ở
  9 ns). Chưa trả lời được việc phân biệt hai ứng viên cách nhau 0,1–0,3 ns.
- Cách đọc kết quả: floorplan @ 5 ns giữ đúng thứ tự và tỉ lệ cải thiện như place → A/B cả SoC tới floorplan @ 5 ns; nhiễu
  place @ 5 ns nhỏ hơn ở 9 ns → chuyển loop sang ép 5 ns; không được cả hai → giữ nguyên, chuyển sang chỉnh floorplan/place.
- Ước lượng: 8 lần ORFS × ~15 phút (synth phải chạy lại vì `ABC_CLOCK_PERIOD` đổi) ≈ 2 giờ, nhiều hơn nếu ép 5 ns làm place lâu.

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id cal-5ns --working-dir . -- python cal_5ns.py --run-name cal-5ns
```

**Kiểm chứng (18/11/2026):** `py_compile` và `load_points`/`summary_md` trên `history.json` giả trong sandbox. Chạy thật lỗi
lần đầu (`KeyError: 'baseline'`: `history.json` thật không có khoá này; sửa: lấy hash từ `measured`, số loop từ dòng
place của `results.csv`). `cal-5ns-2` chết giữa chừng khi cluster dựng lại (mất mạng), `cal-5ns-4` chạy tiếp đúng từ cache.

**Kết quả `cal-5ns-4` (chạy thật 18/11/2026,** 0,85 giờ, phần chưa có cache; các điểm đo ở `cal-5ns-2` lấy từ cache CHIA,
cột `cached` = True, không đo lại). Chu kỳ đạt được (ns), một lần đo, trừ dòng trung vị:

| điểm | loop (bậc 10/9 ns) | floorplan @ 5 | place @ 5 | đường găng place @ 5 |
|---|---|---|---|---|
| baseline | 15,285 @ 10 | 12,480 | 14,997 | FPU `dfma` |
| r5 | 11,513 @ 10 | **8,643** | 10,582 | FPU `sfma` |
| r8 | 10,389 @ 9 | 10,151 | 10,051 | L2 `sinkA/putbuffer` |
| r16 | 9,649 @ 9 (trung vị 4) | 9,995 | **9,017** | FPU `ifpu` |
| r21 | 9,280 @ 9 (trung vị 4) | 8,781 | 9,241; trung vị 4 lần **9,379** | FPU `ifpu` |

- **Nhiễu place @ 5 ns (r21, 4 lần):** 9,209 / 9,241 / 9,518 / 9,840 ns, dao động 0,631 ns, ngang 9 ns (0,46–0,87 ns). Trung
  vị 9,379 ns, không tốt hơn trung vị ở 9 ns (9,280). Ép 5 ns **không giảm nhiễu và không làm place tốt lên**; thời gian
  (place 9,6–10,2 phút, synth 4,5, floorplan 1,3) và RAM đỉnh (4,36 GB) bằng ở 9/10 ns.
- **Floorplan @ 5 ns không xếp hạng đúng:** chỉ tách baseline khỏi các bản đã tối ưu. r5 floorplan nhanh nhất (8,64 ns) mà
  place chậm hơn r8, r16, r21; r16 → r21 floorplan +1,21 ns, place −0,22 ns (một lần). Từ r8 đường găng floorplan là chân
  enable `DE` của L2 `sourceD` (fan-out lớn chưa buffer; floorplan chưa `repair_design`, place mới làm). → **Bỏ A/B cả SoC
  tới floorplan.**
- **Place @ 5 ns giữ thứ tự** baseline < r5 < r8 < {r16, r21}; r16 (một lần, 9,017) và r21 (trung vị 9,379) cách nhau dưới
  nhiễu một lần đo, không phải ngược thứ tự.
- Đường găng place của bản tốt nhất giờ ở FPU `ifpu` (`in_pipe_b` → `io_out_pipe_b_data`), sau đó L2 (−3,3..−4,4) và `dfma`.
- place r21 chỉ 3,5 phút: `make` đi tiếp từ thư mục variant mà `cal-5ns-2` đang place dở (cùng variant, cùng RTL). Số khớp
  biến thể `density-` (9,209) nên coi là hợp lệ.

## Hiệu chỉnh place với route (`route_check.py`, duyệt 16/11/2026)

Câu hỏi: Fmax sau place (số loop dùng để chọn) có đáng tin không. Place chưa có cây clock (clock lý tưởng), chưa có dây
thật (RC ước lượng từ vị trí cell), chưa `repair_timing`; SRAM là fakeram; một corner `tt`. Hai chiều lệch bù trừ một
phần nên phải đo. Người dùng chọn: **sau job loop đang chạy, nếu bản tốt nhất có timing tốt (WNS ≥ 0 ở một bậc), chạy
thử một lần riêng cho đúng patch đó**, rồi loop chạy tiếp bình thường tới 5 ns. M5 (CTS + route + GDS cho mọi bậc) vẫn
để tới khi đạt 5 ns.

- `route_check.py --from-run <job>`: áp `best.patch` của job, build/RTL và ORFS tới place từ cache, rồi `make cts`,
  `make route` trên cùng variant (lỗi/quá giờ thì ghi lại, không dừng; giới hạn 3 giờ / 8 giờ). Không gate/func, không
  dọn ORFS. `--period-ns` để đo ở bậc khác chu kỳ của bản tốt nhất (vd r8 của `m4-b` ở 10 ns).
- `orfs/report.tcl`: sau `cts` và `route` dùng `set_propagated_clock`; sau `route` trích RC bằng OpenRCX (`RCX_RULES`
  của platform, như báo cáo cuối của ORFS), không có thì ước lượng theo placement và in `WARNING`. Báo cáo synth,
  floorplan, place không đổi (`REPORT_VERSION` giữ `r4`, cache cũ vẫn dùng).
- Kết quả: `~/soc-fmax-runs/<run>/summary.md`: bảng place / cts / route (Fmax, WNS, TNS, diện tích, đường găng),
  chênh so với place, slack theo vùng, họ endpoint, thời gian, RAM từng bước ORFS.
- Chạy khi không có job nào khác (một khối asic). RAM và thời gian route của thiết kế này chưa biết.

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id route-m4c --working-dir . -- python route_check.py --run-name route-m4c --from-run ~/soc-fmax-runs/m4-c
```

Bản tốt nhất của `m4-c` chưa đạt 9 ns (đúng như kết quả thật), nên kiểm r8 ở 10 ns, bản đã đạt timing (variant place
được `m4-c` giữ, không phải place lại):

```bash
cd ~/CHIA-workflow/projects/soc-fmax && chia job submit --no-wait --submission-id route-r8 --working-dir . -- python route_check.py --run-name route-r8 --from-run ~/soc-fmax-runs/m4-b --period-ns 10
```

**Kết quả `route-r8` (chạy thật 16/11/2026, r8 @ 10 ns, 22 phút):** place, floorplan, synth `cache HIT`.
- **CTS chạy được** (`make cts` 2,5 phút, RAM 2,15 GB): Fmax **100,658 MHz**, WNS **+0,065** (vẫn đạt 10 ns), TNS 0,
  logic 2,428 mm² (+6,3 % so với place: cây clock + sửa timing). So với place: −1,32 MHz, chu kỳ đạt được chậm hơn
  **0,129 ns**. Đường găng đổi từ `ifpu` (0,194 ở place) sang `dfma` `mulAddResult_pipe_b` (0,242 ở place → 0,065);
  L2 0,207 → 0,174; uncore 2,782 → 0,816 (clock skew sang các khối nhỏ). Lần đầu thấy đường I/O (`mem_axi4_0_aw_valid`
  +0,097) vì clock giờ là propagated.
- **Route lỗi** ở `5_1_grt` (global route) sau 19 phút (12 phút trong đó là pin access của detailed router), RAM
  3,88 GB: `GRT-0116 Global routing finished with congestion`. Tổng tài nguyên chỉ dùng 22 % (met1 27,6 %, met2 31,6 %,
  met3–5 dưới 15 %), nhưng tràn cục bộ tối đa 11 track mỗi ô lưới, tổng tràn 38 557, gần hết ở **met1/met2** (23 105 và
  15 053): nghẽn chân/ô cục bộ chứ không thiếu tài nguyên toàn die.
- `congestion_summary.py` (chạy thật): `congestion.rpt` bị cắt ở 20 000 ô (10 000 ngang + 10 000 dọc); **99 % là net của
  L2 `bankedStore`** (19 864), dồn ở góc trên bên trái die (x 0..2200, y 2000..4000 um), nơi có thể là 4 macro
  `cc_banks` (fakeram 4096×64, chưa đối chiếu toạ độ macro). Còn lại: 144 ô trên clock net, vài ô quanh dcache/icache.
  Chưa rõ baseline có nghẽn như vậy không. ORFS cài sẵn **không có biến `ALLOW_CONGESTION`** (grep `flow/scripts/`
  rỗng): `global_route.tcl` tìm cờ `-allow_congestion` trong `GLOBAL_ROUTE_ARGS`, nên `--allow-congestion` đã sửa sang
  `GLOBAL_ROUTE_ARGS=-congestion_report_iter_step 5 -verbose -allow_congestion` (giữ mặc định thấy trong log).
- Đã thêm (mới mock): `route_check.py --allow-congestion` (cờ `-allow_congestion` chỉ cho `make route`, không đổi variant,
  place/cts lấy từ cache; `summary.md` ghi tổng tràn global route và số vi phạm DRC cuối detailed route) và
  `orfs/congestion_summary.py` (đọc `congestion.rpt`: số ô tràn theo lớp, lưới 8×8 trên die, theo khối thiết kế).

**Kết quả `route-r8b` (chạy thật 16–17/11/2026, `--allow-congestion`, người dùng dừng tay sau khoảng 7 giờ):** global
route đi qua (không dừng ở GRT-0116) nhưng tràn y hệt `route-r8`: dùng 22,21 %, tối đa 11/11, tổng tràn **38 557**, dây
17,87 m. Detailed route: vi phạm theo vòng tối ưu 400 027 → 200 730 → 184 007 → 38 878 → 18 561 → 16 474 → 15 685 →
14 920 → 14 576 → 14 336 → 13 858 → **12 705** (vòng 11), đang ở vòng 12 khi dừng. Từ vòng 5 mỗi vòng chỉ giảm 2–8 %:
không về 0 trong giới hạn 8 giờ. Kết luận: route bị chặn bởi nghẽn met1/met2 quanh L2 `bankedStore` (floorplan, chân
fakeram), không phải thời gian chạy; chưa có Fmax sau route. Báo cáo DRC trung gian: `5_route_drc.rpt-5.rpt`, `-10.rpt`.
`congestion_summary.py` trên `5_route_drc.rpt-10.rpt` (12 605 vi phạm): Short 10 000 (báo cáo cắt ở 10 000), Metal
Spacing 2 323, Cut Spacing 266; theo lớp **met4 9 376, met5 2 715**, via4 282, met1–3 chỉ 232; 12 474 thuộc net L2
`bankedStore`, 2 590/2 481 là `VSS`/`VDD`; dồn ở góc dưới-trái của nửa trên die (cùng vùng nghẽn global route). Tức là
sau khi detailed route đẩy dây khỏi met1/met2, dây `bankedStore` lên met4/met5 và chập/sát với dải nguồn PDN (có thể
quanh chân fakeram `cc_banks` trên met4). Chưa đối chiếu toạ độ macro/chân.
**Người dùng quyết định (17/11/2026): tạm ngưng đo sau route**; loop tiếp tục tối ưu tới 5 ns theo place; sửa
floorplan/PDN/chân fakeram để vào M5.

## Chạy lại từ đầu (xoá cache và mọi lần chạy)

Xoá cache CHIA (`~/chia_cache/soc-fmax`), kết quả (`~/soc-fmax-runs`) và thư mục ORFS của project
(`flow/{designs,results,logs,reports,objects}/sky130hd/soc_fmax`), khi cluster đã tắt (actor cache giữ chỉ mục trong
bộ nhớ). Sau đó một job `soc_fmax.py --noise` làm lại baseline (build, gate, vết func, ORFS tới place) và đo nhiễu (4
lần place), khoảng 2 giờ; rồi mới chạy loop (`--noise-json` trỏ tới `noise.json` mới).

```bash
rm -rf ~/chia_cache/soc-fmax ~/soc-fmax-runs && for d in designs results logs reports objects; do rm -rf ~/tools/OpenROAD-flow-scripts/flow/$d/sky130hd/soc_fmax; done
```

## Trạng thái kiểm chứng

- **Mới kiểm bằng mock (15/11/2026, sandbox):** `py_compile`; tách RTL `DigitalTop` (lọc macro theo `mems.conf`, thay
  `EICG_wrapper`, hash không đổi khi chỉ dịch dòng Scala), SDC, chuẩn bị design (lần hai không ghi lại file nào), đọc báo
  cáo (vùng, đường găng không I/O, họ endpoint, diện tích), đọc log `Elapsed/Peak memory`; toàn bộ `soc_fmax.py` trên
  cluster Ray cục bộ với node giả: lần 1 chạy hết, lần 2 mọi bước `cache HIT`, bước lỗi không bị giữ trong cache.
- **M3 mới kiểm bằng mock (15/11/2026, sandbox; Ray 2.54, `chialoops` 2.0.1, `claude` giả in stream-json):**
  - `--phase sim` 3 vòng: ứng viên (cycle 1,01); build lỗi → agent sửa (patch thay thế đúng, so với cây trước patch
    hỏng) → gate hỏng → agent chẩn đoán; agent tạo file mới 3 lần → bị bỏ khỏi patch → `rỗng`. Cây sạch sau job.
  - `--agent-timeout 5` với `claude` treo: phiên bị giết, vòng ghi `agent lỗi: hết giờ`.
  - `--phase asic` 1 vòng được nhận: dọn ORFS giữ variant `r1`, xoá variant baseline.
  - Chưa chạy thật: mount Chipyard trong khối claude, `claude` thật (`--effort`, đăng nhập, timeout), chất lượng patch.
- **Chạy thật (15/11/2026, run `m1`):**
  - `--until rtl`: build `MinimalSoCConfig` (đã build sẵn nên 0,6 phút), `DigitalTop` 380 module, RTL hash `d2607fe708db`,
    không có `EICG_wrapper`. Instance cấp 1: `tile_prci_domain` (tile), `coh_wrapper` (L2), `sbus/mbus/cbus/pbus/fbus`
    (bus), còn lại uncore. Macro: `cc_banks_0_ext` 4096×64 ×4 (L2 data 128 KiB), `cc_dir_ext` 512×68, dcache data
    512×256 + tag 64×88, icache data 512×128 ×2 + tag 64×84 (10 instance). `sims/firesim` có `env.sh.backup` chưa track:
    bỏ qua (chỉ submodule `generators/`, `tools/` phải sạch).
  - `--until gate`: ISA 106/107 (`rv64ui-p-ma_data` hỏng sẵn, loại khỏi gate), 8 benchmark pass, `mcycle` giống hệt khi đổi
    seed; 14,1 phút trên 4 slot verilator, RAM đỉnh tổng 2,5 GB. Build/RTL đọc từ cache.

    | dhrystone | median | multiply | qsort | towers | vvadd | mm | spmv |
    |---|---|---|---|---|---|---|---|
    | 231 546 | 6 593 | 27 923 | 172 798 | 3 563 | 2 474 | 25 155 | 54 036 |
  - Log có một lỗi Ray `grpc ... port 10050 Address already in use` của một worker phụ (các container cùng `--net=host`
    tranh dải cổng worker của Ray); Ray tự thử lại, gate không bị ảnh hưởng. Lặp lại nhiều thì tách dải
    `--min-worker-port/--max-worker-port` theo khối (sửa trên `setup`).
  - `--until synth|floorplan|place` (variant `d2607fe708db_p10_o4_fr1a1p5u3`, 10 ns):

    | Bước | Thời gian | RAM đỉnh bước ORFS | Fmax | WNS | TNS | tile | L2 | bus | uncore | logic (cell) |
    |---|---|---|---|---|---|---|---|---|---|---|
    | synth | 3,8 phút | 0,84 GB | 52,39 MHz | −9,086 | −31 749 | −9,086 | −8,402 | −6,229 | +4,229 | 2,365 mm² (193 531) |
    | floorplan | 1,4 phút | 1,23 GB | 80,13 MHz | −2,480 | −2 460 | −2,480 | −2,126 | +0,014 | +4,855 | 2,221 mm² (187 475) |
    | place | 8,5 phút (3_3 gp 4,8) | 2,43 GB | **65,42 MHz** | **−5,285** | −10 163 | −5,285 | −2,096 | +1,176 | +1,916 | 2,233 mm² (180 223) |

    Macro 4,16 mm² (10), core 15,9 mm². Container asic đỉnh khoảng 3 GB, tổng các container 2,5 GB lúc báo cáo; xa
    ngưỡng dừng (3 giờ, 20 GB). Đường găng sau place: FPU `fpuOpt/dfma` `in_in1` → `mulAddRecFNToRaw_postMul_io_mulAddResult_pipe_b`
    (118 endpoint âm ở tile). L2: 6120 endpoint âm (`sinkA/putbuffer`, `sourceD/queue` từ `mshrs_*`). Floorplan chạy
    `repair_timing` nên Fmax của nó cao hơn place (chưa có dây thật).
  - Lần chạy `m1-cache` (tên run mới): mọi bước `cache HIT`, kể cả 3 bước ORFS (`make` 0,0 phút).
  - Lỗi đã gỡ khi chạy:
    - 2115 thanh ghi không có clock (serial TL, JTAG TCK): thêm clock phụ bất đồng bộ (xem **Cách đo**); sau sửa
      `registers_unclocked` = 0.
    - Floorplan lỗi `PDN-0179` (kênh met1 hẹp): tapcell của sky130hd đặt tapcell trên mảnh row giữa mép core và macro
      dcache, nằm trong halo; tăng `MACRO_PLACE_HALO` không đủ. Sửa bằng `tapcell.tcl` riêng (halo row 40 µm,
      `-row_min_width 80`), flow `o4`.
    - Slack theo vùng chỉ lấy từ 5000 đường xấu nhất nên mất vùng bus sau place: thêm `TOP_SLACK` trên mọi endpoint.
      Bản đầu gom cả chân clock của macro (`RW0_clk`), cho slack giả −52 ns ở synth/floorplan (`report_checks -to` báo
      `No paths found`); `r4` bỏ 10 chân đó, slack vùng khớp WNS ở cả 3 bước.
  - Chưa giải thích: số thanh ghi giảm từ 41 874 (synth) xuống 38 125 (floorplan), riêng miền aux từ 2115 xuống 1146.
    Có thể do floorplan của ORFS xoá logic chết; chưa kiểm.

- **M2 mới kiểm bằng mock (15/11/2026, sandbox; Ray 2.54 + chialoops cục bộ, node giả, cây git giả có submodule):**
  - `func_check` trên vết giả lập:
    - đạt khi chỉ cycle hoặc giá trị bộ đếm khác;
    - hỏng khi sai giá trị ghi, sai lệnh, thiếu lệnh;
    - dừng đúng ở `tohost` và ở nhánh nhiễm;
    - đọc được symbol `tohost` từ ELF.
  - `soc_fmax.py --noise`: 4 variant nhiễu, `noise.json`.
  - `loop.py` với 6 vòng, cho đủ các kết quả `RTL trùng`, `build lỗi`, `rỗng`, `gate`, `func`, `nhận`.
  - `--history-from` đi tiếp đúng: vòng sau thấy `best.patch`, RTL trùng vòng 6, patch xung đột ra `patch lỗi`.
  - Cây về sạch sau mọi vòng.
- **M2 chạy thật 15/11/2026 (`m2-noise`, chạy lại `m2-noise3` từ cache, mọi bước HIT):**
  - Vết `+verbose` của Chipyard 1.15.0 đọc được hết, không dòng lỗi. Ghi vết 4 benchmark mất 9,4 phút sim, so vết
    8,8 phút. dhrystone 215 377 lệnh, qsort 226 768, mm 207 806, spmv 71 910, đều dừng ở ghi `tohost`.
  - Lỗi lần đầu: Rocket in `W[r0=<rác>]` cho lệnh có rd = x0 (`csrw fcsr, zero` ở lệnh 40), giá trị khác giữa hai
    seed. Từ `CHECK_VERSION` `c2`: không so giá trị ghi khi rd = 0.
  - Baseline so với chính nó (seed 7): cả 4 benchmark đạt. Riêng **mm** dừng ở nhánh phụ thuộc bộ đếm tại lệnh
    35 678/207 806, nên func chỉ phủ khoảng 17 % mm; phần còn lại của mm chỉ có gate (kết quả đúng, minstret bằng).
  - Nhiễu sau place, 5 lần đo:

    | lần | Fmax (MHz) | điểm họ | logic (um2) |
    |---|---|---|---|
    | baseline | 65,423 | −13,300 | 2 232 856 |
    | rerun | 65,423 | −13,300 | 2 232 856 |
    | density 0,54 | 64,443 | −16,715 | 2 225 518 |
    | density 0,56 | 64,960 | −12,478 | 2 217 927 |
    | util 41 | 65,474 | −14,935 | 2 229 313 |

    ORFS tất định: chạy lại y hệt cho cùng số. Synth không đổi theo biến place (52,39 MHz cả 5 lần). Ngưỡng nhận:
    Fmax **±1,546 MHz** (khoảng 1,031 × 1,5), điểm họ **±6,356 ns**. Điểm họ nhiễu gần bằng nửa giá trị baseline,
    nên trên thực tế vòng chỉ được nhận nhờ Fmax.
- **M2 loop chạy thật 15/11/2026 (`m2-loop3`, bypass `empty,comment,broken`):**
  - Hai lần đầu (`m2-loop`, `m2-loop2`) dừng ở kiểm resource: `loop.py` đòi `claude`, khối claude cấp `claude_creds`
    (mock đặt tên sai). Sửa ở `422aca8`.
  - Baseline (build, RTL, gate, func, synth/floorplan/place) đọc hết từ cache.
  - Vòng 1: patch rỗng 3 lần, ra `rỗng`. Vòng 2: `git apply` vào submodule `rocket-chip` thật, build thật 1,3 phút
    (sbt biên dịch lại), RTL hash vẫn `d2607fe708db`, ra `RTL trùng`, không đo lại. Vòng 3: sbt báo
    `RocketCore.scala:1389:1: expected class or object definition` sau 0,2 phút, agent sửa build (bypass) không sửa,
  - Sau job, `generators/rocket-chip` sạch. Còn lại là trạng thái có sẵn từ trước, digest baseline đã tính cùng nó:
    `generators/gemmini` và `software/gemmini-rocc-tests` (`m`), `sims/firesim` (bỏ qua),
    `MinimalSoCConfig.scala` chưa track.
  - Dung lượng: mỗi variant ORFS tới place chiếm **2,3 GB** trong `flow/results` (5 variant nhiễu khoảng 11,5 GB), còn
    `runs/<vòng>` chỉ 2–14 MB. Cuối mỗi job chỉ giữ variant của bản tốt nhất (`prune_orfs`).
- **Pha asic cộng dồn mới kiểm bằng mock (15/11/2026, cả sau khi bỏ `--batch each/best`):** ngưỡng nhiễu floorplan đọc từ `results.csv`; 4 vòng agent giả:
  r1, r2, r4 cộng dồn, r3 hỏng gate nhanh; cuối đợt lùi r1+r2+r4 (hỏng func) → r1+r2 (hỏng func) → r1 nhận; cây sạch.
- **Hướng B mới kiểm bằng mock (16/11/2026, sandbox):** `m4b-1` 4 vòng: r1 cộng dồn, r2 hỏng gate nhanh, r3 + r4
  cộng dồn (place tăng dần); cuối đợt gate r4 (func hỏng) → r3 (func hỏng) → r1 nhận; dọn ORFS giữ mỗi r1.
  `m4b-2 --history-from m4b-1`: r1 dựng lại từ cache (build + synth/floorplan/place `cache HIT`), agent thấy
  `r1_place.txt`, r5 cộng dồn rồi nhận, dọn ORFS xoá cả variant r1 cũ; `summary.md` đúng ở cả hai; cây sạch sau job.
- **Bậc chu kỳ + ngưỡng ns mới kiểm bằng mock (16/11/2026, sandbox, Fmax place giả 95 MHz):** `m5-1` từ baseline:
  r1, r2 cộng dồn, r2 WNS +0,099 ở 10 ns → kết thúc đợt sớm, r2 nhận → bậc 10 đạt → đo r2 + 3 lần nhiễu ở 9 ns
  (`noise_p9.json`) → r3 cộng dồn, r4 hỏng gate nhanh, cuối đợt r3 hỏng func; dọn ORFS giữ r2 @ 10 ns và @ 9 ns.
  `m5-2 --history-from m5-1`: tự chạy ở 9 ns, ngưỡng lấy từ lịch sử, r2 dựng lại từ cache. `m5-3 --history-from m4b-2`
  (`history.json` kiểu cũ, không có bậc/nhiễu): r5 đạt 10 ns ngay đầu job → xuống 9 ns + đo nhiễu; `m5-4` cùng thế với
  `--on-met stop` dừng job, báo cách chạy tiếp.
- **Đo lặp (phương án A) mới kiểm bằng mock (16/11/2026, Fmax place giả 95 MHz, `noise.json` giả ±0,03 ns):**
  `m6-1`: r1 +3 MHz (0,33 ns) → hứa hẹn → gate nhanh → đo lặp 4 lần, trung vị 0,331 ns < 0,367 nhưng điểm họ tăng
  quá nhiễu → cộng dồn; r2 hỏng gate nhanh trước khi đo lặp; cuối đợt r1 nhận, mẫu đo lặp ghi vào `history.json`.
  `m6-2 --history-from m6-1 --repeat-gain 0.2`: trung vị của r1 lấy lại từ lịch sử; r3 trung vị +0,294 ns → cộng dồn,
  WNS trung vị +0,096 → kết thúc đợt sớm, nhận, bậc 10 đạt, xuống 9 ns + đo nhiễu (thành mẫu đo lặp của r3).
  `m6-3` từ bản sao `m6-2` bỏ mẫu đo lặp (như `history.json` của `m4-c`): trung vị r3 lấy từ ngưỡng nhiễu cùng bậc,
  r5 đo lặp rồi cộng dồn, cuối đợt hỏng func. Cột "trung vị đo lặp" trong `summary.md`/`history.md`, bước "(đo lặp)"
  trong bảng thời gian.
- **`route_check.py` mới kiểm bằng mock (16/11/2026):** từ `m5-1` (r2) ở 10 ns: place, floorplan, synth `cache HIT`,
  `cts` và `route` chạy, `summary.md` có bảng và chênh so với place, cây sạch sau job. Chưa chạy thật: `make cts` /
  `make route` của thiết kế này, OpenRCX trong `report.tcl`, RAM/thời gian.
- **Hướng B chạy thật 16/11/2026 (`m4-b`):** dựng lại bản tốt nhất cũ từ cache, mỗi vòng tới place, gate nhanh rồi
  cộng dồn, gate đủ cuối đợt (bản Fmax cao nhất qua ngay), `summary.md`, dọn ORFS (chỉ còn variant r8) đều chạy đúng.
  Chưa gặp trên máy thật: lùi sang bản kế khi gate đủ hỏng (mới có ở mock).
- Đường nhận vòng thật (gate/func/ORFS của ứng viên có RTL khác) chỉ có ở mock: session không viết thay đổi thiết
  kế, nên lần chạy thật đầu tiên của đường này là ở M3/M4.

## Mốc

| Mốc | Nội dung | Trạng thái |
|---|---|---|
| M1 | build → gate → fakeram → ORFS place 10 ns → Fmax/WNS, vùng, diện tích, thời gian/RAM, `results.csv`; lần hai từ cache | **xong 15/11/2026** (chạy thật, kể cả kiểm cache) |
| M2 | `func_check.py` vào gate, đo nhiễu place, chốt ngưỡng nhận, orchestration với bypass LLM | **xong 15/11/2026** (chạy thật: func_check, nhiễu, loop bypass 3 vòng) |
| M3 | pha sim có agent (tầng pipeline, chỉ đo cycle) → `candidates.json` | **xong 15/11/2026** (`m3-b` chạy thật: 4 ứng viên / 5 vòng) |
| M4 | pha asic có agent, đợt cộng dồn đo bằng place (hướng B), gate đủ cuối đợt theo Fmax, tự xuống bậc tới 5 ns | đang chạy: `m4-a` thật 16/11/2026 place 65,42 → 86,86 MHz; `m4-b` (hướng B, chạy thật) → **101,98 MHz, WNS +0,194: đạt bậc 10 ns**; `m4-c` (chạy thật): tự xuống 9 ns đúng, nhưng nhiễu place ở 9 ns ±2,275 ns chặn mọi vòng (r15 104,07 MHz, TNS −6, vẫn trong nhiễu); `m4-d` (luật đo lặp, chạy thật): nhận r16, trung vị place 103,64 MHz (+0,456 ns so với r8); `m4-e` (A/B của agent, chạy thật): nhận r21, trung vị 107,76 MHz (+0,369 ns), chưa đạt 9 ns |
| M5 | CTS + route + GDS, timing sau route: làm khi đạt 5 ns (variant ORFS bản tốt nhất của mỗi bậc đạt được giữ lại) | chưa |
| M6 | OpenRAM sky130 thật cho bản tốt nhất, GDS thật, so fakeram/OpenRAM | chưa |
