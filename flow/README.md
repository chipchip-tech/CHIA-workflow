# flow: Sim → FPGA → ASIC do CHIA điều phối, dùng chung cho mọi project

Thư viện điều phối, gate, vòng agent Claude và trang status, tách ra từ demo ALU (`projects/demo-alu/`) ngày
26/11/2026. Một project chỉ cần nguồn thiết kế theo các quy ước dưới đây và một file khai báo `project.py`.
Nhánh `demo` dùng và kiểm chứng trước; ổn định thì nhập sang `setup` cho các project sau.

## Lệnh

Chạy trên head, trong `chia_env`, cluster theo khối đã bật (`python scripts/chia-up-blocks.py --fpga --asic`):

```bash
python flow/run.py <project> baseline [--fpga-period 5.3] [--stages sim,fpga,asic] [--plusarg k=v] [--fpga-opt k=v]
```
```bash
python flow/run.py <project> agent [--rounds 2] [--steps sim,fpga,asic] [--baseline <run_dir>]
```
```bash
python flow/run.py <project> sweep fmax|power [--period 5.3] [--values 10,30]
```
```bash
python flow/run.py show <run_dir>
```
```bash
python flow/clean_runs.py [--yes] [--only <group>/<project>]
```

`run.py` submit job từ gốc repo (`chia job submit --working-dir . -- python -m flow.<pha> --project <project>`), phục vụ
`WORK_DIR` qua `http://127.0.0.1:8765` và mở trang status. Kết quả: `<WORK_DIR>/<group>/<project>/<run_id>/`.

## Các file

| File | Việc |
|---|---|
| `project.py` | đọc `<project>/project.py`, gộp mặc định, chép nguồn + kit vào thư mục chạy, ghi `clock.xdc`/`sources.f`/`synth_opts.tcl` của FPGA |
| `orchestrate.py` | gửi task, đợi song song (`ray.wait`), ghi sự kiện cho trang status; ba nhánh `run_sim`, `run_fpga`, `run_asic` dùng chung cho baseline và agent |
| `tasks_sim.py`, `tasks_fpga.py`, `tasks_asic.py` | các task CHIA và cách đọc kết quả của công cụ |
| `baseline.py` | pha 1 |
| `agent.py` | pha 2 + `results/compare.md`; luật phạm vi sửa và luật nhận/loại |
| `status.py` | trang status (tiếng Anh): flow có gate, timeline Gantt theo khối, kết quả, log và file |
| `sweep.py` | quét FPGA: Fmax từng biến thể, công suất SAIF theo một opt của project |
| `run.py`, `clean_runs.py` | submit + mở trang; dọn `WORK_DIR` |
| `kit/` | file cố định chép vào thư mục chạy nếu project không có bản riêng: `sim/sim_main.cpp`, `fpga/power_saif.tcl`, `asic/signoff.tcl`, `asic/render_gds.py`, `platforms/<platform>/cells_extra.v` → `asic/gls/cells_extra.v` |

## Đồ thị task

```
sim_build (chipyard) ─► sim_run (verilator) ─► fpga [fpga_sim trước ở pha 2] (vivado)
  ─► asic_synth ─► floorplan ─► place ─► cts ─► route ─► finish (asic, lần lượt)
       └─► asic_drc ‖ asic_lvs ‖ asic_sta_<corner>… (asic, mỗi task cfg.asic.check_slots = 0,25 slot) ‖ asic_gls (chipyard)
```

Bước sau chỉ chạy khi bước trước qua gate. Ở baseline, FPGA chưa đạt sign-off (timing, methodology) chỉ là cảnh báo;
ở agent là gate. Mỗi task ghi `t_start`/`t_end` trên worker, nên timeline của trang status đúng cả khi chạy song song.

## Khai báo project (`<project>/project.py`)

Biến `PROJECT` (dict, gộp với `DEFAULTS` trong `flow/project.py`) và hàm móc tuỳ chọn
`fpga_synth_args(fpga_dir, opts) -> (list tham số synth_design, dict info)` (info đưa vào prompt agent, ví dụ `en_thr`).

| Trường | Ý nghĩa |
|---|---|
| `dut` | **bắt buộc**: module top của RTL |
| `name`, `group` | mặc định: tên thư mục project và thư mục cha |
| `title` | một dòng mô tả, hiện trên trang status |
| `steps` | `["sim", "fpga", "asic"]`; `sim` bắt buộc, `fpga`/`asic` tắt được |
| `sim.kind` | `"verilator"` (testbench Verilog tự kiểm). `"chipyard"` (SoC Chisel, chương trình RISC-V) **chưa làm** |
| `sim.plusargs` | plusarg mặc định của testbench |
| `sim.fcov_module` | module functional coverage; testbench của agent không được tham chiếu |
| `fpga.part`, `fpga.board`, `fpga.top`, `fpga.period_ns` | part (bắt buộc khi bật fpga), tên board để hiển thị, top, chu kỳ mặc định |
| `fpga.variants` | `{tên: {replace: {dòng sources.f: file thay}, synth: [tham số], note}}`; biến thể đầu là mặc định |
| `fpga.power_instance` | instance dưới top đọc công suất riêng |
| `fpga.opts`, `fpga.sweep_opt` | tham số riêng cho hàm móc; opt được `sweep power` quét |
| `fpga.fixed` | file `.v` trong `fpga/` agent không được sửa (ngoài `fpga_top.v`, `tb_*.v`) |
| `asic.platform`, `asic.cell_prefix`, `asic.tt_lib`, `asic.corner_glob`, `asic.ocv_derate` | platform ORFS, thư viện tt cho GLS, `.lib` corner thật (nếu có), derate OCV |
| `asic.fixed_vars` | biến `config.mk` agent không được đổi |
| `agent.description`, `agent.variant`, `agent.sim_hints`, `agent.fpga_wrapper` | đoạn mô tả đưa vào prompt (`fpga_wrapper` dùng được `{en_thr}`… của info) |

## Quy ước nguồn của project

- `rtl/*.v`: RTL cố định, lint `-Wall` phải sạch.
- `sim/run_sim.sh` nhận `BUILD_ONLY=1` / `SKIP_BUILD=1`, build ra `build/obj/Vtb` (`--prefix Vtb`, `sim_main.cpp` của
  kit), ghi `build/coverage.dat`; testbench `sim/tb_<dut>.v` in `RESULT: PASS|FAIL k=v ...` (có `checked=`, `errors=`);
  `sim/fcov_<dut>.sv` in `FCOV: hit=<n> total=<m>` và `FCOV_MISS: ...`.
- `fpga/build.tcl` đọc `sources.f`, `clock.xdc`, source `synth_opts.tcl`; ghi `out/<top>.bit`, `out/post_route.dcp`,
  `reports/{util,timing,power,methodology,clock_util}.rpt`. `tb_<top>.v` in `RESULT: PASS <top>`; `tb_power.v` đặt top
  tên `dut`, nhận macro `TB_PERIOD_PS`, `TB_CYCLES`; `tb_gls.v` in `RESULT: PASS gls`. Clock tên `sys_clk`, cổng `clk`.
- `asic/config.mk` cho ORFS (đường dẫn tính theo `$(dir $(DESIGN_CONFIG))`), `asic/gls/tb_gls_<dut>.v` (testbench chạy
  trên netlist, không kiểm tra hộp trắng).

## Trạng thái

- Ngày 26/11/2026 mới chỉ thử trong sandbox. Ray cục bộ có đủ các resource của cluster.
- Sim chạy thật bằng Verilator, cho đúng số của máy thật. FPGA và ASIC dùng task giả.
- Phần đã kiểm trong lần thử đó:
  - 5 task kiểm tra chạy song song sau `finish`;
  - trang status lúc đang chạy, lúc xong và lúc lỗi;
  - agent hoàn lại thay đổi ngoài phạm vi.
- Chưa chạy trên cluster thật.
