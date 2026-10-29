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
- Chưa vòng `--chia-mode` nào chạy thật; phần vòng mới kiểm bằng mock.

## Việc tiếp theo

1. Người dùng chạy đợt 1 bằng lệnh ở mục "Chạy" của `README.md` (`--out-dir ~/rocket-fmax-v2 --run-name v2-r1`).
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
