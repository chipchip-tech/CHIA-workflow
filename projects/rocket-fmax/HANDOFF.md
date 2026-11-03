# Bàn giao: rocket-fmax

File này dành cho session Claude Code tiếp quản project. Đọc cùng `CLAUDE.md` (gốc repo), `docs/workflow-phases.md`
và `README.md` (thư mục này).

## Trạng thái (02/11/2026)

- Tốt nhất: **92,36 MHz** Yosys `v2` (patch `~/rocket-fmax-v2/fmax-r4/patches/best.patch` trên máy người dùng),
  cycle +0,405 %, worst slack −0,827 ns. Các họ đường găng sát nhau: `core.div.remainder` (−0,83), FMA
  `mulAddResult_r`/`ppl`/`pph` (−0,80 … −0,70), `fpuOpt.regfile_ext.Memory` (−0,66), `divSqrt` `rem_Z`/`sigX_Z`
  (−0,64/−0,58), `core.mem_reg_wdata` (−0,48).
- `fmax-y1`, `y3`, `y4` (12 vòng cộng lại) không nhận vòng nào. Nguyên nhân rút ra:
  1. Sửa cấu trúc (prefix adder, CSA, Booth) không thắng `+`/`*` của Yosys/ABC.
  2. Flow `v2` (flatten) dao động 0,2–0,6 ns khi sửa một khối khác, lớn hơn ngưỡng 1 %.
  3. Fmax chỉ tăng khi rút ngắn hết các họ sát nhau, mà mỗi vòng agent chỉ xử lý một họ.
  4. Agent đo lại cùng một sửa (`mulUnroll ≤ 4` đo 4 lần).
- 02/11/2026, người dùng duyệt khung pha (trên `setup`) và hai bước cho project, **đã code, mới kiểm bằng mock**:
  - Bước 1, pha sim (`--phase sim`, `prompts/explore_pipeline.md`): agent thêm tầng pipeline cho một họ, chỉ kiểm
    compile; loop chạy gate, đo cycle; qua gate thì lưu ứng viên (`candidates.json`). `--try-from` ở pha asic thử
    từng ứng viên trên bản tốt nhất (`N.apply_candidate`, `git apply -3`).
  - Bước 2, cách đo: flow `v3` phân cấp (mặc định), nhận thêm theo điểm họ (`--min-family-gain`, `--worst-tol`),
    `synth_ab` nhớ Verilog đã đo (`AB.child_key`, `load_memo`), ngưỡng nhiễu `synth_ab` theo flow.

## Việc tiếp theo

1. Xong (03/11/2026): `v3-start` cho tên đúng dạng, điểm xuất phát 85,01 MHz ở `v3` (−1,763 ns, họ FMA đứng đầu).
   Số `v3` thấp hơn `v2` vì không tối ưu qua cổng module (hằng số không lan vào module con); khi báo kết quả cuối,
   synth lại bản tốt nhất bằng `v2` (và sau này ORFS) để so với 92,36 MHz.
2. `sim-1` xong (code trước 03/11/2026): 5 ứng viên qua gate, cả 5 ở `fpuOpt.dfma.fma`, cùng tiêu tầng `Pipe` rảnh ở
   đầu ra FMA (xem README, mục kiểm chứng). Với code mới chúng thành một nhóm biến thể, `asic-1` giữ tối đa một. Đã thêm
   03/11/2026 (mới kiểm bằng mock): khối đã đổi + biến thể, bảng "Blocks already covered" trong prompt sim, sửa tóm tắt
   mất dòng `TARGET`, synth sơ bộ cả tile ở pha sim, `--try-from` đo hết rồi thử theo nhóm biến thể, bỏ gate khi ứng
   viên nằm trên gốc của nó. Ba họ FMA đứng đầu cần nhiều hơn một tầng: đợt sim sau có thể cho agent thử tăng
   `dfmaLatency` và đo cycle.
3. Chạy `asic-1 --try-from sim-1` bằng code mới. Đọc bảng ứng viên đo trên điểm xuất phát trong `history.md`, rồi lý do
   nhận/loại (cột `family_score`).
4. Sau đó: ASIC post-route (ORFS sky130) cho bản tốt nhất.

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
