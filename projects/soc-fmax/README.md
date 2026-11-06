# soc-fmax: Fmax của cả SoC Rocket

Project tiếp theo của `project/rocket-fmax`, mở ngày 06/11/2026. Project đó đưa `RocketTile` lên 140,01 MHz ở mức synth
(`asic-7`). Đo cả SoC (`DigitalTop`) thì chỉ tăng từ 54,81 lên 63,80 MHz, nhưng số SoC đó chưa tin được: đường găng là một
đường reset đi qua net không có buffer, và các cột theo vùng bị trống (xem `REPORT.md` mục 7 của nhánh `project/rocket-fmax`).

Mục tiêu:
1. Có một phép đo Fmax cả SoC đủ tin để dùng làm thước đo.
2. Đo với SRAM thật của sky130 (OpenRAM).
3. Nếu L2 hoặc uncore thật sự là chỗ chặn, chạy loop agent CHIA trên phần đó, như `rocket-fmax` đã làm với tile.

## Pha

| Bước | Nội dung | Trạng thái |
|---|---|---|
| 1 | Sửa phép đo (`soc_synth.py`): không tính reset bất đồng bộ và tầng đầu bộ đồng bộ, chèn buffer bằng `repair_design`, lấy slack theo vùng | Đã viết, **chưa chạy** |
| 2 | SRAM OpenRAM sky130 (MacroCompiler + `.lib`), so với SRAM giả, có thể thu nhỏ L2 | Chưa làm |
| 3 | Loop agent trên vùng chặn (L2/uncore), có gate chức năng cho bộ nhớ | Chưa làm |

Theo khung `docs/workflow-phases.md`, project này nằm ở nhánh **ASIC**. Pha **Simulate** dùng lại gate của `rocket-fmax`
cho đến bước 3.

## File

- `soc_synth.py`: build `RocketConfig` (baseline, cộng thêm các patch rocket-chip nếu có), Yosys `v3`, sau đó đo timing với
  từng giá trị thời gian truy cập SRAM. Xem docstring.
- `fmax_nodes.py`: chép nguyên từ `projects/rocket-fmax/fmax_nodes.py` (commit 2b796c9) vì `soc_synth.py` dùng các hàm build,
  Yosys và đọc kết quả STA trong đó. Không sửa ở đây.

## Chạy

Dùng lại cluster của `rocket-fmax`, không cần `chia up` lại nếu cluster đang chạy:
- `projects/rocket-fmax/cluster.yaml`, gồm các worker `chipyard`, `eda`, ...
- image `chia-eda-sky130` (`projects/rocket-fmax/docker/EdaDockerfile`).

Lần chạy đầu, task `eda` tải 2 file LEF sky130hd từ OpenROAD-flow-scripts về `/home/ray/pdk/sky130hd/lef/` và kiểm
sha256. Container cần truy cập được `raw.githubusercontent.com`.

```bash
cd ~/CHIA-workflow/projects/soc-fmax
chia job submit --no-wait --submission-id soc-fmax-1 --working-dir . -- \
  python soc_synth.py --run-name soc-fmax-1 \
    --patch asic-7=~/rocket-fmax-v2/asic-7/patches/best.patch
chia job logs -f soc-fmax-1
```

Kết quả nằm ở `~/soc-fmax/soc-fmax-1/` (`summary.md`, `soc_synth.csv`, `runs/<điểm>/sta_<access>.txt`).

Tuỳ chọn:
- `--buffer none`: chỉ chạy OpenSTA, không chèn buffer, như `rocket-fmax`.
- `--sram-access-ns 0,2`: thời gian truy cập SRAM giả, mỗi giá trị một lần đo.
- `--max-fanout 16`: fanout tối đa khi chạy `repair_design`.

## Kiểm chứng

- Đã kiểm: `py_compile`; dựng LEF giả, template Tcl và phần đọc kết quả (vùng, SRAM, Fmax trước và sau buffer, bảng tóm
  tắt) trên dữ liệu giả.
- **Chưa kiểm** (chưa có OpenROAD/OpenSTA trong sandbox):
  - OpenROAD đọc netlist phân cấp cùng LEF giả của SRAM;
  - `initialize_floorplan` và `repair_design` chạy được khi chưa có placement;
  - `find_timing_paths` và `get_property` trên bản OpenSTA trong image;
  - `set sta_recovery_removal_checks_enabled 0` và `all_registers -async_pins`.

  Nếu bước OpenROAD lỗi, script tự đo lại bằng OpenSTA không buffer, ghi cột `buffer` = `none (openroad failed)` và đưa lỗi
  vào `summary.md`.
