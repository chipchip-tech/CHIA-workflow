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
- SRAM (`rockettile_{d,i}cache_{data,tag}_array*_ext`) là blackbox, chân của chúng **không
  được ràng buộc**. Vì vậy các đường SRAM→reg và reg→SRAM không được tính. `EICG_wrapper`
  (nếu có trong cây module) được thay bằng một dây nối thẳng.
- Verilog lấy từ chính lần build sim (`collect_generated_src=True`), nên bản đem synth đúng là
  bản đã mô phỏng.

## File

| File | Việc |
|---|---|
| `rocket_fmax.py` | Driver: baseline, các vòng, nhận/loại, ghi kết quả, profiler |
| `fmax_nodes.py` | Các `@ChiaFunction`: chipyard (ELF, git, kiểm phạm vi), `extract_tile_rtl`, `run_gate`, `synth_sta` |
| `prompts/improve_timing.md` | Prompt của agent (IPC-neutral, không build) |
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
`--min-gain-ns 0`, `--repeat-seed 7`, `--flush-cache`, `--out-dir ~/rocket-fmax-out`,
`--cache-dir ~/chia_cache`.

## Đầu ra: `~/rocket-fmax-out/<run>/`

```
results.csv            iter, accepted, wns_ns, tns_ns, worst_slack_ns, fmax_mhz, critical_path,
                       isa_pass, bench_cycles_match, reason, area_um2, elapsed_min
patches/iter<N>.patch  git diff trong generators/rocket-chip so với commit gốc (gộp cả bản tốt nhất trước đó)
patches/best.patch     bản tốt nhất (rỗng nếu không vòng nào được nhận)
runs/baseline/         timing_paths.rpt, timing_endpoints.rpt, timing_families.txt, sta.log, yosys.log, gate.json, cycles.csv, build.log
runs/iter<N>/          prompt.md, agent_response.md, agent_transcript.txt, diff_files.txt, build.log,
                       gate.json, gate_compare.txt, sim_fail.log, timing_*.rpt, decision.json
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
  - Họ endpoint đặt tên kiểu `mulAddResult_gHiR_36_reg` chưa được gom (bộ gom chỉ xử lý `[N]`).
