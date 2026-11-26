# flow: Sim → FPGA → ASIC do CHIA điều phối, dùng chung cho mọi project

Thư viện điều phối, gate, vòng agent Claude và trang status, tách ra từ demo ALU (`projects/demo-alu/`) ngày
26/11/2026. Một project chỉ cần nguồn thiết kế theo các quy ước dưới đây và một file khai báo `project.py`.
Nhánh `demo` dùng và kiểm chứng trước; ổn định thì nhập sang `setup` cho các project sau.

## Project mới: tối thiểu cần gì

Một khối RTL chỉ cần **RTL + testbench tự kiểm + `project.py`**; mọi file còn lại flow tự sinh trong thư mục chạy
(`flow/generate.py`): `sim/run_sim.sh`, `asic/config.mk`, `asic/constraint.sdc`, và cho FPGA chế độ out-of-context
`fpga/build.tcl`, `sources.f`, `sim_cfg.tcl`. Tạo khung:

```bash
python flow/new.py projects/<tên> --top <module> --rtl a.sv pkg.svh --tb tb_<module>.sv --clock clk:10 --pass "REGEX" --fail "REGEX"
```

Rồi điền `agent.description`, `agent.sim_hints` trong `project.py` và chạy baseline. Ví dụ: `projects/ascon-core/`.
Testbench phải theo hai quy ước để GLS (mô phỏng netlist ASIC sau route và FPGA sau route) dùng lại được nó:
chỉ dùng **cổng** của DUT (không `u_dut.tín_hiệu_trong`; `new.py` cảnh báo khi thấy), và tự `include file khai báo
kiểu/hằng nó dùng (khai báo đặt trong file RTL sẽ không còn khi chạy trên netlist). Khi GLS, flow define `GLS`.
Testbench giữ reset ít nhất 100 ns rồi mới đưa kích thích: netlist Xilinx giữ mọi flop trong reset toàn cục (GSR,
`glbl.v` của Vivado, flow không sửa) 100 ns đầu, theo quy trình mô phỏng gate-level chuẩn của Vivado.
Project có vỏ board riêng (`fpga/build.tcl`, `fpga_top.v`, tb riêng) thì chạy chế độ `board` như demo ALU.
**SoC Chipyard: chưa hỗ trợ** (phần B, làm sau).

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
`WORK_DIR` qua `http://127.0.0.1:8765` và mở trang status. Kết quả: `<WORK_DIR>/<group>/<project>/<run_id>/`
(`projects/<tên>/` không có nhóm: `<WORK_DIR>/<tên>/<run_id>/`).

## Các file

| File | Việc |
|---|---|
| `project.py` | đọc `<project>/project.py`, gộp mặc định, chép nguồn + kit vào thư mục chạy, ghi `clock.xdc`/`sources.f`/`synth_opts.tcl` của FPGA |
| `orchestrate.py` | gửi task, đợi song song (`ray.wait`), ghi sự kiện cho trang status; ba nhánh `run_sim`, `run_fpga`, `run_asic` dùng chung cho baseline và agent |
| `tasks_sim.py`, `tasks_fpga.py`, `tasks_asic.py` | các task CHIA và cách đọc kết quả của công cụ |
| `baseline.py` | pha 1 |
| `agent.py` | pha 2 + `results/compare.md`; luật phạm vi sửa và luật nhận/loại |
| `status.py` | trang status (tiếng Anh): flow có gate, timeline Gantt theo khối, kết quả, log và file. Dùng chung cho mọi project: số ô flow và cột kết quả theo `steps` (1–3 bước), nhãn công cụ và dòng thông tin (DUT, clock, part, platform) lấy từ `project.py`, dòng ICG/công suất instance chỉ hiện khi project có, thêm dòng của `sim.metrics` |
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
| `rtl`, `include_dirs` | file RTL theo thứ tự đọc (mặc định `rtl/*.v`, `rtl/*.sv`; header `.svh/.vh` được `include từ `include_dirs`, mặc định `["rtl"]`) |
| `clocks` | `[{"port": "clk", "period_ns": 10}]`; clock đầu là clock chính (SDC, XDC, chu kỳ FPGA mặc định). Mới hỗ trợ một clock |
| `sim.kind` | `"verilator"` (testbench Verilog/SV tự kiểm). `"chipyard"` (SoC Chisel, chương trình RISC-V) **chưa làm** |
| `sim.tb`, `sim.tb_top`, `sim.dut_instance` | testbench (mặc định `sim/tb_<dut>.v/.sv`), module top (mặc định `tb_<dut>`), instance DUT (tự dò) |
| `sim.pass_patterns`, `sim.fail_patterns` | regex: mọi mẫu pass phải có, không mẫu fail nào được có trong log; không khai báo thì dùng dòng `RESULT: PASS` |
| `sim.lint` | `"errors"` (mặc định: lỗi lint mới chặn, cảnh báo ghi lại), `"strict"` (-Wall sạch, demo ALU), `False` |
| `sim.plusargs` | plusarg mặc định của testbench |
| `sim.fcov_module` | module functional coverage (tuỳ chọn); không có thì luật Sim của agent dùng line/toggle/branch |
| `sim.metrics` | số liệu riêng của project đọc từ log mô phỏng, hiện ở bảng Sim của trang status và `compare.md`: `[{"key", "label", "regex", "better", "scale"}]` (nhóm 1 của lần khớp cuối) hoặc `"count": True` (số lần khớp). Chỉ để xem, không ảnh hưởng gate hay luật agent |
| `fpga.mode` | `"board"` (có `fpga/build.tcl` + vỏ board, ra bitstream) hoặc `"ooc"` (mặc định khi không có: out-of-context, không bitstream; công suất SAIF và GLS chạy testbench Sim trên netlist sau route) |
| `fpga.part`, `fpga.board`, `fpga.top`, `fpga.period_ns` | part (bắt buộc khi bật fpga), tên board để hiển thị, top, chu kỳ mặc định (mặc định: chu kỳ clock chính) |
| `fpga.variants` | `{tên: {replace: {dòng sources.f: file thay}, synth: [tham số], note}}`; biến thể đầu là mặc định |
| `fpga.power_instance` | instance dưới top đọc công suất riêng |
| `fpga.opts`, `fpga.sweep_opt` | tham số riêng cho hàm móc; opt được `sweep power` quét |
| `fpga.fixed` | file `.v` trong `fpga/` agent không được sửa (ngoài `fpga_top.v`, `tb_*.v`) |
| `asic.platform`, `asic.cell_prefix`, `asic.tt_lib`, `asic.corner_glob`, `asic.ocv_derate` | platform ORFS, thư viện tt cho GLS, `.lib` corner thật (nếu có), derate OCV |
| `asic.fixed_vars` | biến `config.mk` agent không được đổi |
| `asic.utilization`, `asic.place_density`, `asic.io_pct`, `asic.config_extra` | cho `config.mk`/SDC tự sinh: CORE_UTILIZATION, PLACE_DENSITY, độ trễ vào/ra (tỉ lệ chu kỳ), dòng thêm (ví dụ `export SYNTH_HDL_FRONTEND = slang`) |
| `agent.description`, `agent.variant`, `agent.sim_hints`, `agent.fpga_wrapper` | đoạn mô tả đưa vào prompt (`fpga_wrapper` dùng được `{en_thr}`… của info) |

## Quy ước nguồn khi project tự viết file (chế độ board, như demo ALU)

- `rtl/*.v`: RTL cố định.
- `sim/run_sim.sh` nhận `BUILD_ONLY=1` / `SKIP_BUILD=1`, build ra `build/obj/Vtb` (`--prefix Vtb`, `sim_main.cpp` của
  kit), ghi `build/coverage.dat`; testbench `sim/tb_<dut>.v` in `RESULT: PASS|FAIL k=v ...` (có `checked=`, `errors=`);
  `sim/fcov_<dut>.sv` in `FCOV: hit=<n> total=<m>` và `FCOV_MISS: ...`.
- `fpga/build.tcl` đọc `sources.f`, `clock.xdc`, source `synth_opts.tcl`; ghi `out/<top>.bit`, `out/post_route.dcp`,
  `reports/{util,timing,power,methodology,clock_util}.rpt`. `tb_<top>.v` in `RESULT: PASS <top>`; `tb_power.v` đặt top
  tên `dut`, nhận macro `TB_PERIOD_PS`, `TB_CYCLES`; `tb_gls.v` in `RESULT: PASS gls`. Clock tên `sys_clk`, cổng `clk`.
- `asic/config.mk` cho ORFS (đường dẫn tính theo `$(dir $(DESIGN_CONFIG))`), `asic/gls/tb_gls_<dut>.v` (testbench chạy
  trên netlist, không kiểm tra hộp trắng).

## Trạng thái

- Chạy thật trên cluster 26/11/2026: baseline NoCG `baseline-20261126-195910`, CG `baseline-20261126-200321`, `KẾT QUẢ: OK`, mọi số (coverage, LUT/FF, WNS, SAIF, diện tích, STA, GLS) trùng từng số với baseline 25/11; 5 task kiểm tra sau finish chạy song song 5,2 s (cộng lần lượt 12,3–12,5 s), `make drc`/`lvs`/`run` cùng `WORK_HOME` không xung đột. Agent `--rounds 1`: NoCG `agent-20261126-200829` nhận sim-r1 (51 → 55 bin), asic-r1 (die 7 534 → 5 460 µm², trùng lần trước), loại fpga-r1; CG `agent-20261126-201926` nhận sim-r1 (51 → 57, branch 8 → 10), fpga-r1 (sign-off, WNS 0 → 0,524, methodology 2 → 0), asic-r1 (cell 2 975 → 2 930, die 6 926 → 5 018 µm², công suất −6,4 %).
- Trước đó thử trong sandbox (Ray cục bộ, FPGA/ASIC giả): trang status lúc lỗi, agent hoàn lại sửa ngoài phạm vi.
- 26/11/2026: khối RTL "cắm là chạy" (file tự sinh, `.sv` + header, PASS/FAIL theo regex, lint `errors`, FPGA
  out-of-context, GLS dùng testbench Sim, `flow/new.py`). Thử trong sandbox với `projects/ascon-core`: Sim chạy thật
  (Verilator 5.020, PASS, line 25/35, toggle 3007/3438, branch 44/60), FPGA/ASIC giả; demo ALU vẫn ra đúng số Sim cũ.
  ASCON chạy thật trên cluster 26/11/2026 (`baseline-20261126-213109`, `KẾT QUẢ: OK`): Sim PASS, lint 12 cảnh báo, line 25/35, toggle 3007/3438, branch 44/60; FPGA out-of-context 100 MHz: LUT 1592, FF 473, WNS 1,292 / WHS 0,217 ns (Fmax ước lượng 114,8 MHz), methodology 0 critical (TIMING-18 mức Warning: chưa có độ trễ I/O), SAIF khớp 66 % net, P động 23,4 mW, GLS trên netlist PASS; ASIC sky130hd 10 ns: Yosys 0.68 đọc được SV, 48 859 µm² cell (util 44,7 %), 5 464 cell, 479 flop, setup/hold WS 2,626/0,474 ns (tt), 2,088/0,430 ns (OCV ±10 %), P 9,96 mW, DRC router/KLayout 0, antenna 0, LVS khớp, GLS PASS; route 605 s; kiểm tra sau finish song song 41,7 s (lần lượt 72,4 s). Lỗi lộ ra và cách sửa: GLS FPGA treo vì testbench bắt
  đầu trước khi GSR của netlist Xilinx nhả (100 ns): testbench giữ reset 100 ns (quy trình chuẩn, không sửa `glbl.v`).
  Agent `--rounds 1` chạy thật 26/11/2026 (`agent-20261126-215007`, `KẾT QUẢ: OK`): sim-r1 NHẬN (agent thêm kích thích vào tb: line 25 → 34/35, toggle 3007 → 3383, branch 44 → 57/60); fpga-r1 LOẠI (sửa `build.tcl`: WNS 1,292 → 0,71 ns, P động 23,4 → 30,5 mW); asic-r1 LOẠI (sửa `config.mk`: die −15 % nhưng cell 48 859 → 51 204 µm², P 9,96 → 9,80 mW; route 1 211 s). GLS ASIC của vòng ASIC dùng tb mới của sim-r1 và PASS.
- Chưa có: nhiều clock, `sim.kind = "chipyard"` (SoC), `flow.sweep` trên cluster.
