# HANDOFF: soc-fmax

Đọc file này trước khi làm tiếp.

## Trạng thái (06/11/2026)

- Nhánh `project/soc-fmax`, tách từ `setup`.
- Bước 1 đã viết xong (`soc_synth.py`) nhưng **chưa chạy**. Lần chạy đầu tiên dự kiến là `soc-fmax-1`: baseline và
  `asic-7`, chèn buffer bằng `repair_design`, SRAM giả 0 và 2 ns.
- Khi có kết quả, cần trả lời ba câu:
  1. OpenROAD có chạy được không? Xem cột `buffer` và mục lỗi OpenROAD trong `summary.md`.
  2. Đường xấu nhất còn là đường reset hay synchronizer không? Nếu còn, phải sửa constraint.
  3. Sau khi chèn buffer, vùng nào chặn (tile, uncore, L2), và SRAM có nằm trên đường găng không (cột `đường có SRAM`)?

## Bối cảnh từ rocket-fmax

- Tile `asic-7` đạt 140,01 MHz khi synth riêng tile (6 ns). Patch nằm ở `~/rocket-fmax-v2/asic-7/patches/best.patch`
  trên máy người dùng. Patch này **chưa được kiểm chức năng ngoài gate**.
- `soc-synth-5` (code cũ):
  - Fmax SoC: baseline 54,81 MHz, `asic-7` 63,80 MHz.
  - Đường găng: `resetSynchronizer ... sync_0_reg` → `serial_tl ... async sink ... sync_0_reg`.
  - Các cột theo vùng trống vì phần đọc `report_checks -format summary` không khớp format.
  - Số SRAM 0, 1,5 và 3 ns giống hệt nhau.
- Quy ước: Claude không viết Chisel. Mọi sửa RTL do agent trong loop làm (bước 3).

## Việc tiếp theo

1. Người dùng chạy `soc-fmax-1` (lệnh trong `README.md`) rồi gửi lại `summary.md`, `soc_synth.csv` và một file
   `sta_*.txt` nếu có lỗi.
2. Sửa script theo kết quả, cho đến khi đường găng là đường logic thật.
3. Bước 2: SRAM OpenRAM. Trước khi làm, liệt kê các macro sky130 có sẵn cùng timing của chúng, và cách map các
   memory của `RocketConfig` (L1 I/D, tag, L2 data/directory) sang các macro đó.
