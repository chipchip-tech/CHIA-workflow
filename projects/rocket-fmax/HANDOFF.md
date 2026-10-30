# Bàn giao: rocket-fmax, bắt đầu lại theo example `timing_opt` (29/10/2026)

File này dành cho session Claude Code tiếp quản project. Đọc cùng `CLAUDE.md` (gốc repo) và `README.md`
(thư mục này).

## Trạng thái

- Ngày 29/10/2026 người dùng quyết định **chạy lại từ đầu**. Mọi kết quả trước đó (m1–m4, config audit, `lab-*`,
  `chia-1/2`) bị bỏ, cả trên repo lẫn trên máy người dùng. Code config audit (`config_audit.py`, `measure_soc.py`,
  `area_check.py`, `--fragments`) đã gỡ. Lịch sử cũ chỉ còn trong git, trước commit `ab43d94`.
- Hướng duy nhất: `--chia-mode` (theo `examples/timing_opt` của CHIA), Claude Code Sonnet effort medium, chu kỳ
  10 ns, mất tối đa 3 % cycle, nhận khi Fmax / chi phí cycle tăng > 1 %, `--extra-bench mm spmv`, không equiv,
  xuất phát từ RocketConfig gốc.
- Baseline 10 ns đã đo thật (47,13 MHz, đường găng `core.div`, gate pass trừ `rv64ui-p-ma_data`) và nằm trong
  cache Ray của máy người dùng (`baseline_rtl`, `baseline_synth_v2_p10`, `baseline_gate_b2_mm_spmv`).
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
- Bước 0 (đang làm): `vivado_impl` + `--vivado-impl-probe` (README, mục "Post-route"), mới kiểm bằng mock. Cần đo
  thật: flow chạy được không (BRAM có được suy ra không), thời gian place + route, RAM khi 3 lần song song, độ dao
  động giữa các chiến lược, Fmax post-route của baseline và start.

## Việc tiếp theo

0. Đọc `vivado_impl.csv` của Bước 0, rồi viết Bước 1: loop nhận/loại theo post-route (`vivado_impl` của vòng chạy
   song song với gate), `synth_ab` dùng Vivado `synth_design`, prompt nêu đường găng post-route và đặc thù FPGA.

1. Sau đợt 2, người dùng chọn đo thêm timing bằng Vivado (song song, không dùng để nhận/loại; `synth_design`; Vivado ML
   Standard miễn phí, part Artix-7). Đã viết `vivado_nodes.py`, `--vivado-part`, `docker/VivadoDockerfile`, worker
   `rocket_vivado` (README, mục Vivado). Đã chạy thật baseline (`viv-base`): Vivado 63,82 MHz, 2,2 phút, đường găng
   `dcache.s2_data -> core.mem_reg_wdata` (Yosys: 47,13 MHz, `core.div`). Việc: đợt 3 với
   `--start-patch ~/rocket-fmax-v2/chia-fmax-r2/patches/best.patch --vivado-part xc7a200tfbg676-2`, rồi so xu hướng
   Yosys và Vivado qua các vòng trong `vivado.csv`.
   Flow Yosys `v4` (giữ ranh giới khối) đã thử viết rồi bỏ: người dùng chỉ giữ thay đổi cải thiện được cả Vivado lẫn
   Yosys; dùng số Vivado để đánh giá độ nhiễu của Yosys trước.
2. Đọc log: baseline phải HIT cache; xem phiên agent có gọi `synth_ab` trọn vẹn không, có vòng nào được nhận,
   agent debug có chạy khi test fail không. Sửa loop theo log.
3. Ghi kết quả từng đợt vào bảng "Kết quả" của `README.md`. Đợt sau dùng `--start-patch` từ `best.patch` của đợt trước.

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
