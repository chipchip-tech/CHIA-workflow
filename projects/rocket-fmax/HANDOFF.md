# Bàn giao: rocket-fmax, theo example `timing_opt` (bắt đầu lại 29/10/2026, mốc Vivado post-route từ 30/10/2026)

File này dành cho session Claude Code tiếp quản project. Đọc cùng `CLAUDE.md` (gốc repo) và `README.md`
(thư mục này).

## Trạng thái

- Ngày 29/10/2026 người dùng quyết định **chạy lại từ đầu**. Mọi kết quả trước đó (m1–m4, config audit, `lab-*`,
  `chia-1/2`) bị bỏ, cả trên repo lẫn trên máy người dùng. Code config audit (`config_audit.py`, `measure_soc.py`,
  `area_check.py`, `--fragments`) đã gỡ. Lịch sử cũ chỉ còn trong git, trước commit `ab43d94`.
- Hướng duy nhất: theo `examples/timing_opt` của CHIA (trước 30/10 là `--chia-mode`; nay là chế độ duy nhất),
  Claude Code Sonnet effort medium, chu kỳ 10 ns, mất tối đa 3 % cycle, `--extra-bench mm spmv`, không equiv.
  r1–r3b nhận khi Fmax Yosys / chi phí cycle tăng > 1 %; từ Bước 1 nhận theo Fmax post-route Vivado, ngưỡng 2 %.
- Baseline 10 ns đã đo thật (47,13 MHz, đường găng `core.div`, gate pass trừ `rv64ui-p-ma_data`) và nằm trong
  cache Ray của máy người dùng (nay dùng `baseline_rtl2`, `baseline_gate_b2_mm_spmv`; `baseline_synth_v2_p10` của Yosys
  không còn dùng).
- Đợt 1 `chia-fmax-r1` (29/10/2026) xong: 4/5 vòng nhận, 47,13 → 81,91 MHz, cycle +0,405 %. Vòng 5 dừng sau
  0,3 phút: Sonnet bỏ cuộc sau 1 lượt, không gọi tool. Đã sửa prompt và thêm `--empty-retries 1`. Kết quả ở
  `~/rocket-fmax-v2/chia-fmax-r1/` trên máy người dùng.
- Đợt 2 `chia-fmax-r2` (29/10/2026) xong: 1/5 vòng nhận, 81,91 → 84,37 MHz, cycle vẫn +0,405 %. Vòng 2–3 ra
  netlist trùng iter1 (WNS/TNS trùng tới 4 chữ số); vòng 4–5 agent không sửa gì (retry ở vòng 5 chỉ thử 1 sửa).
  Đường găng còn nhiều họ sát nhau (`dfma`, `sfma`, `core.mem_reg_wdata`, `core.div`), và synth cả tile (flatten,
  một lần ABC) dao động 1–2 ns khi sửa một khối khác. Kết quả ở `~/rocket-fmax-v2/chia-fmax-r2/`.
- Đợt 3 `chia-fmax-r3b` (30/10/2026) xong, có Vivado song song: 1/5 vòng nhận (vòng 3), 84,37 → 90,57 MHz Yosys,
  cycle +0,405 %, đường găng Yosys về `core.div`. Vivado: start và iter3 cùng 67,53 MHz (đường găng
  `dcache.s2_data -> core.mem_reg_wdata`), iter4 bị Yosys loại thì Vivado 68,73 MHz. Vòng 1, 2, 5 agent không sửa gì
  dù retry đã liệt kê các sửa đổi đã đo (commit `680e7f8`; chưa đọc `retry*_prompt.md` để xác nhận). Hai thước đo
  lệch nhau; chưa quyết hướng B (B1 chấm A/B theo họ endpoint, B2 thêm Vivado vào A/B) và đích ASIC hay FPGA.

- **30/10/2026: đổi mốc.** Mốc 1 = Fmax > 100 MHz **post-route Vivado** trên Arty A7-100T (`xc7a100tcsg324-1`,
  speed grade -1); mốc 2 = > 100 MHz post-route ORFS sky130. Phỏng vấn người dùng chốt: nhận/loại chỉ theo Vivado
  post-route (Yosys ghi để đối chiếu); SRAM dùng model hành vi → BRAM; giữ cycle +3 %; giữ mục tiêu 100 MHz và đánh giá
  tiến độ từng đợt (Chipyard Arty100T chạy Rocket ở 50 MHz, nên kẹt nhiều đợt thì bàn lại mục tiêu/giới hạn cycle);
  `synth_ab` đổi sang Vivado `synth_design`; chống nhiễu P&R bằng vài chiến lược song song, lấy tốt nhất; đợt đầu
  xuất phát từ `best.patch` của r3b. `--vivado-accept` (commit `e2065fd`) không dùng nữa, lệnh `fmax-r4` với nó đã bỏ.
- Bước 0 xong. `viv-impl0`/`viv-impl1` lỗi `[Synth 8-439] module 'split_rockettile_dcache_tag_array_ext' not found`:
  model `*_ext` gọi các module con `split_*_ext`; sửa bằng `TileRTL.sram_models`, tag RTL `rtl2`. **`viv-impl2`
  (30/10) chạy được** (README, mục "Đo post-route lần đầu"): post-route baseline 58,67 MHz, start (r3b) 61,06 MHz;
  48 BRAM; mỗi chiến lược 5,5–11,6 phút; `default` không lần nào tốt nhất; ba chiến lược chênh tới 5 MHz trên cùng
  netlist; 2 lần song song gần 10 GB RAM → người dùng chọn 1 Vivado một lúc. Đường găng post-route:
  `dcache.s2_data`/`s2_req_addr`/`s2_req_size` → `core.mem_reg_wdata`, kế đến `fpuOpt.sfma` → `fpuOpt.regfile`.
- **Bước 1 + dọn dẹp (30/10, mới kiểm bằng mock, chưa chạy thật).** Người dùng duyệt: mỗi vòng `explore` +
  `extratiming` lần lượt, song song với gate; ngưỡng nhận `--min-perf-gain 0.02`; `synth_ab` dùng Vivado
  `synth_design`; prompt nêu đường găng post-route và đặc thù FPGA; bỏ `--vivado-accept`; dọn cấu hình cho đỡ tốn
  tài nguyên. Đã làm:
  - Chỉ còn một chế độ (theo `timing_opt`, agent Claude Code); gỡ `--chia-mode` (luôn bật), `--agent opencode`,
    `--loop-ab`, `--ab-tool`, `--bypass-llm`, `--equiv*`, `--vivado-accept`, `--vivado-impl-probe` (thay bằng
    `--baseline-only`), Yosys/OpenSTA (`synth_sta`, `--synth-flow`), `equiv.py`, `probe_tools.py`,
    `docker/EdaDockerfile`, 3 prompt cũ. `prompts/improve_timing_chia.md` → `prompts/improve_timing.md` (viết lại
    cho Artix-7, post-route).
  - `cluster.yaml` còn 4 worker (bỏ `hello_opencode`, `rocket_eda`). Người dùng phải `docker rm -f` hai container cũ.
  - Mặc định theo project: `--period-ns 10 --vivado-part xc7a100tcsg324-1 --extra-bench mm spmv --exclude-isa
    rv64ui-p-ma_data --out-dir ~/rocket-fmax-v2`.
  - Cache (`exp.yaml`): `extract_tile_rtl`, `run_gate`, `vivado_impl` với tag `baseline_.*`. Trước đây `vivado_impl` và
    `vivado_synth` **không hề được cache** (không có trong `exp.yaml`), nên `viv-impl2` sẽ không HIT; tag mới
    `vivimpl4` (thêm danh sách endpoint). RTL (`rtl2`) và gate (`_gate_b2_mm_spmv`) của baseline và start vẫn HIT.
  - Vivado chạy trong process group riêng (`start_new_session`): gate fail hay cycle quá giới hạn thì loop
    `chia_cancel` place + route và không còn Vivado sót (đã thử với Vivado giả).
  - `synth_ab` (`ab_tool.py`): `vivado_synth` thay `synth_sta`, SRAM là model (sub-block lấy đủ `split_*_ext`),
    sub-block chiếm 0,5 slot `vivado` (2 cùng lúc), cả tile 1 slot; parent của cả tile là `synth_design` trong lần
    place + route của bản tốt nhất.
  - Định vị đường găng cho prompt: tên Vivado (`dcache/s2_data_reg[13]/C`) → `vivado_nodes.dotted` → `pick_target`,
    `path_sources` (dòng Chisel theo `@[...]`).

## Việc tiếp theo

1. Người dùng chép file, `docker rm -f` hai worker cũ, `chia up`, rồi chạy đợt `fmax-r4` (lệnh ở README, mục
   "Chạy"; `--start-patch` = `best.patch` của r3b). Đầu job: start place + route chạy lại (~23 phút, `vivimpl4`
   miss), RTL/gate HIT.
2. Đọc log r4: `synth_ab` bản Vivado có chạy không (thời gian, RAM khi 2 sub-block cùng lúc), agent có đọc được
   `route_paths.rpt`, thời gian mỗi vòng, huỷ place + route khi gate fail có để lại Vivado không (`docker exec
   chia-vivado-$USER-0 pgrep -fa vivado`). Sửa loop theo log.
3. Ghi kết quả từng đợt vào bảng "Kết quả" của `README.md`. Đợt sau dùng `--start-patch` từ `best.patch` của đợt
   trước. Kẹt nhiều đợt thì bàn lại mục tiêu/giới hạn cycle với người dùng.

## Quy ước làm việc

Xem đầy đủ trong `CLAUDE.md`.

- Trả lời bằng tiếng Việt.
- Claude không chạy lệnh trên máy người dùng: đưa lệnh cho người dùng chạy, rồi đọc log họ gửi lại.
- **Claude không tự viết patch Chisel.** Mọi chỉnh sửa Chisel do loop (agent) làm.
- `~/CHIA-workflow` trên máy người dùng không phải git clone: người dùng chép file bằng tay. Sau mỗi lần đưa code,
  đưa `sha256sum <file> | cut -c1-12` để họ đối chiếu.
- Làm việc trên nhánh `project/rocket-fmax`. Không tạo PR, không rebase hay force-push, không ghi tên model vào commit.
- Liệt kê file cho người dùng duyệt trước khi tạo hoặc xoá file tài liệu.
- Cập nhật `README.md` và `CLAUDE.md` sau mỗi đợt chạy. Ghi rõ phần nào đã kiểm chứng, phần nào chưa.
- Mã nguồn CHIA (chialoops 2.0.1) để đối chiếu: repo `ucb-bar/chia`, `examples/timing_opt`.
