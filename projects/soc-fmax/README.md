# soc-fmax: Fmax cả SoC `MinimalSoCConfig` trên sky130hd

Nhánh `project/soc-fmax-sky130` (mở 15/11/2026, tách từ `setup`). Khác project cũ cùng tên ở nhánh `project/soc-fmax`
(xong 06/11/2026, Chipyard 1.10/`chia_artifact`, RocketConfig, đo bằng synth): project này dùng Chipyard gốc 1.15.0,
`chipyard.MinimalSoCConfig`, và đo bằng ORFS **sau place**, tới CTS/route/GDS.

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
| Pha hiện tại | M1 xong (baseline đo thật sau place 10 ns: **65,42 MHz**). Kế tiếp M2 |
| PDK / corner | sky130hd (`sky130_fd_sc_hd`), `tt_025C_1v80` |
| SRAM | fakeram (`scripts/fakeram_sky130.py`, `docs/fakeram-sky130.md`): access 1,5 ns, 3 µm²/bit, timing giả định. OpenRAM thật chỉ ở M6 |
| Bậc chu kỳ | 10 → 9 → 8 → 7 → 6 → 5 ns. Bản tốt nhất có WNS ≥ 0 sau place ở bậc hiện tại → CTS + route + GDS cho bản đó → bậc kế |
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
  endpoint xấu nhất) tăng hơn ngưỡng nhiễu.
- Ra: 5 ns đạt WNS ≥ 0 sau place, có CTS + route + GDS; M6 thay fakeram bằng OpenRAM.

## Cách đo

| Mức | Công cụ | Dùng để |
|---|---|---|
| 1 | `make synth` của ORFS (Yosys phân cấp `SYNTH_HIERARCHICAL=1`, ABC ở chu kỳ mục tiêu) + OpenSTA với Liberty fakeram | lọc rẻ |
| 2 | `make place` (global place + `repair_design` + detailed place), RC ước lượng từ placement | nhận/loại vòng |
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

## File

| File | Nội dung |
|---|---|
| `soc_fmax.py` | driver M1: các bước `rtl`, `gate`, `synth`, `floorplan`, `place` (`--until`), cache, bảng thời gian/RAM, `results.csv`, `summary.md` |
| `soc_nodes.py` | node chipyard (`tree_state` mọi submodule, `load_test_elfs`), verilator (`run_gate`), tách RTL `DigitalTop` (`SocRTL`), vùng, họ endpoint. Chép chọn lọc từ `rocket-fmax/fmax_nodes.py` |
| `orfs_nodes.py` | chuẩn bị design ORFS (host), `orfs_stage` trên khối asic (make + báo cáo, có timeout), đọc log `Elapsed`/`Peak memory` |
| `orfs/report.tcl` | báo cáo trên kết quả một bước: WNS/TNS, 5000 đường xấu nhất (mỗi endpoint một đường), slack theo instance cấp 1 trên mọi endpoint, đếm thanh ghi theo clock, nhóm thanh ghi không clock, diện tích logic/macro |
| `orfs/diag_floorplan.tcl` | chẩn đoán tay: macro (bbox, halo), blockage, và inst/pin/row trong một hộp (`SOCFMAX_BOX`) |
| `memwatch.py` | RAM đỉnh từng container theo bước (`docker stats` mỗi 5 s, trên host) |
| `exp.yaml` | cache/bypass: `ChiselBuildNode.build`, `extract_soc_rtl`, `run_gate`, `orfs_stage` |

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

## Trạng thái kiểm chứng

- **Mới kiểm bằng mock (15/11/2026, sandbox):** `py_compile`; tách RTL `DigitalTop` (lọc macro theo `mems.conf`, thay
  `EICG_wrapper`, hash không đổi khi chỉ dịch dòng Scala), SDC, chuẩn bị design (lần hai không ghi lại file nào), đọc báo
  cáo (vùng, đường găng không I/O, họ endpoint, diện tích), đọc log `Elapsed/Peak memory`; toàn bộ `soc_fmax.py` trên
  cluster Ray cục bộ với node giả: lần 1 chạy hết, lần 2 mọi bước `cache HIT`, bước lỗi không bị giữ trong cache.
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

## Mốc

| Mốc | Nội dung | Trạng thái |
|---|---|---|
| M1 | build → gate → fakeram → ORFS place 10 ns → Fmax/WNS, vùng, diện tích, thời gian/RAM, `results.csv`; lần hai từ cache | **xong 15/11/2026** (chạy thật, kể cả kiểm cache) |
| M2 | `func_check.py` vào gate, đo nhiễu place, chốt ngưỡng nhận, orchestration với bypass LLM | chưa |
| M3 | pha sim có agent (tầng pipeline, chỉ đo cycle) → `candidates.json` | chưa |
| M4 | pha asic có agent, 5 vòng, nhận/loại theo place, tự xuống bậc | chưa |
| M5 | CTS + route + GDS mỗi bậc đạt, timing sau route | chưa |
| M6 | OpenRAM sky130 thật cho bản tốt nhất, GDS thật, so fakeram/OpenRAM | chưa |
