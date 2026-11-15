# SRAM giả (fakeram) cho sky130hd

`scripts/fakeram_sky130.py` sinh macro SRAM giả từ `*.mems.conf` của một build Chipyard, để ORFS
(sky130hd) chạy được synth → floorplan → place → CTS → route → GDS khi chưa có macro OpenRAM thật.
Timing và diện tích là **giả định**, giống nhau cho mọi lần đo: dùng để so các bản RTL với nhau, không
phải số sign-off.

**Trạng thái:** đã chạy thật qua ORFS sky130hd tới place (15/11/2026, project `soc-fmax-sky130`, 10 macro của
`MinimalSoCConfig`): Yosys đọc Liberty/blackbox, macro placement, PDN, OpenSTA đều chạy. Chưa qua CTS/route/GDS.

Hai điều gặp khi chạy:
- Chân clock của macro (`*_clk`) có `slack_max` trước CTS mà không phải endpoint dữ liệu (`report_checks -to` báo
  `No paths found`, OpenSTA cho −52 ns ở synth). Khi tính slack trên mọi chân vào của macro, bỏ các chân này.
- Tapcell mặc định của sky130hd (`tapcell -distance 14`) đặt tapcell lên mảnh row hẹp giữa mép core và macro, nằm
  trong halo, làm PDN lỗi `PDN-0179` (kênh met1 hẹp). Tăng `MACRO_PLACE_HALO` không sửa được; cần `TAPCELL_TCL` riêng
  với `-halo_width_x/-halo_width_y` bằng halo macro và `-row_min_width` (project dùng 40 µm và 80 µm).

## Macro nào được sinh

Chỉ các dòng của `mems.conf`: đó là các `SyncReadMem` mà firtool (`--repl-seq-mem`) đã thay bằng module
`<name>` (thường đuôi `_ext`). Các `Mem` đọc tổ hợp cũng có module `*_ext` trong Verilog nhưng chứa mảng
flop thật, không có trong `mems.conf`, nên vẫn là logic (giống khi tape-out thật). Project tự lọc macro nằm
dưới top đang đo (`--only`).

Tên chân theo quy ước của Chipyard, nên không cần wrapper:

| Cổng trong conf | Chân |
|---|---|
| `rw` / `mrw` | `RW<i>_addr`, `_en`, `_clk`, `_wmode`, `_wdata`, `_rdata` (+ `_wmask` nếu `mrw`) |
| `read` | `R<i>_addr`, `_en`, `_clk`, `_data` |
| `write` / `mwrite` | `W<i>_addr`, `_en`, `_clk`, `_data` (+ `_mask`) |

`--verilog <file>` kiểm chéo với header module trong RTL; lệch thì dừng.

## Mô hình

| Thứ | Giá trị mặc định | Tham số |
|---|---|---|
| clk → dữ liệu ra | 1,5 ns ở tải 0, cộng 1,5 ns/pF | `--access-ns`, `--out-res-ns-per-pf` |
| setup / hold mọi chân vào (so với clock của cổng đó) | 0,2 / 0,05 ns | `--setup-ns`, `--hold-ns` |
| điện dung chân vào / clock | 0,005 / 0,02 pF | `--in-cap-pf`, `--clk-cap-pf` |
| diện tích | số bit × 3 µm² (ô sky130 ~1,9 µm² + ngoại vi) | `--um2-per-bit` |
| hình dạng | vuông (`--aspect` = rộng/cao), cạnh ≥ 20 µm, cao bội số 2,72 µm | `--aspect`, `--min-side-um` |

Hình học (LEF):
- Chân tín hiệu: hình chữ nhật met3 (ngang trong sky130hd) 0,8 × 0,3 µm ở cạnh trái, cách 2 track (1,36 µm);
  không đủ chỗ thì chia sang cạnh phải; vẫn thiếu thì nới chiều cao.
- Nguồn: sọc `VPWR`/`VGND` met4 dọc toàn chiều cao, rộng 1,6 µm, xen kẽ mỗi 13,6 µm, để lưới macro của
  `pdn.tcl` sky130hd nối met4–met5.
- OBS: met1, met2, met4 phủ cả macro; met3 phủ trừ dải 1 µm sát hai cạnh có chân.

File sinh ra trong `--out`: `fakeram_sky130.lef`, `.lib` (một thư viện cho mọi macro, corner danh nghĩa
`tt_025C_1v80`), `.gds` (chỉ đường bao, lớp prBoundary 235/4, cho bước gộp GDS của ORFS),
`fakeram_sky130_bb.v` (blackbox), `fakeram.json` (tham số + kích thước từng macro; ghi vào báo cáo).
`VERSION` trong script đổi khi đổi mô hình; project đưa nó vào tag cache.

```bash
python3 scripts/fakeram_sky130.py --conf <build>/<...>.top.mems.conf --out /tmp/fr --access-ns 1.5
```

## Giới hạn

- Không phải OpenRAM: macro OpenRAM sky130 nhỏ (1–2 KiB) cỡ ~20 µm²/bit và timing chậm hơn nhiều. Số đo với
  fakeram và với OpenRAM có thể lệch xa, nhất là đường qua L2.
- Trễ ra không phụ thuộc slew vào; không có công suất; không kiểm min pulse width.
- Chân không đặt đúng lưới track sau khi placer xoay/lật macro; router xử lý được nhưng có thể chậm hơn.
