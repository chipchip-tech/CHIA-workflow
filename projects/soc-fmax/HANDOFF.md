# HANDOFF: soc-fmax

Đọc file này trước khi làm tiếp.

## Trạng thái (06/11/2026)

- Nhánh `project/soc-fmax`, tách từ `setup`.
- Bước 1 đã chạy (`soc-fmax-1`: baseline và `asic-7`, `repair_design`, SRAM giả 0 và 2 ns). Kết quả:

  | điểm | Fmax SoC | vùng chặn | tile | uncore |
  |---|---|---|---|---|
  | baseline | 54,81 MHz | tile (FMA: `dfma/in_in2` → `mulAddResult_pipe_b`) | -12,244 | -9,823 |
  | asic-7 | 63,80 MHz | uncore (L2) | ngoài top 5000 (> -8,87) | -9,675 |

  1. OpenROAD chạy được: đọc LEF, tạo floorplan, chạy `repair_design` không lỗi. Nhưng slack trước và sau buffer
     không đổi (chưa có placement nên không có dây, chỉ dùng wire load model).
  2. Đường găng là logic thật, không còn reset hay synchronizer: 787 chân async và 196 cell `sync_0` không tính.
  3. Với `asic-7`, L2 là chỗ chặn: `inclusive_cache_bank_sched/sourceC/busy_reg` → `sinkA/putbuffer/data_ext/Memory[*]`
     (khoảng 2880 bit, bộ nhớ bằng flop). Tiếp theo là `mshrs_6`, `putbuffer/head_ext` và `tail_ext`. Không có đường SRAM
     nào trong top 5000, nên SRAM 0 và 2 ns cho số giống hệt nhau.
- Hạn chế của script: slack theo vùng chỉ lấy trong 5000 đường xấu nhất, nên một vùng có thể không có số (tile của `asic-7`).
  Lỗi `find_timing_paths -group_path_count` (`STA-0403`) không ảnh hưởng: script tự chuyển sang `-group_count`.

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

1. (Tuỳ chọn) Sửa script để lấy worst slack riêng cho từng vùng (ví dụ `find_timing_paths -to` các register của tile).
2. Bước 2: SRAM OpenRAM. Trước khi làm, liệt kê các macro sky130 có sẵn cùng timing của chúng, và cách map các
   memory của `RocketConfig` (L1 I/D, tag, L2 data/directory) sang các macro đó.
3. Bước 3: loop agent trên L2 (`InclusiveCache`: đường `busy` → `putbuffer`), với gate chức năng cho bộ nhớ.
