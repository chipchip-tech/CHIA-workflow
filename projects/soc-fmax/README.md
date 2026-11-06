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
| 1 | Sửa phép đo (`soc_synth.py`): không tính reset bất đồng bộ và tầng đầu bộ đồng bộ, chèn buffer bằng `repair_design`, lấy slack theo vùng | Xong (`soc-fmax-1`): baseline 54,81 MHz (FMA chặn), `asic-7` 63,80 MHz (L2 chặn) |
| 2 | SRAM OpenRAM sky130 (MacroCompiler + `.lib`), so với SRAM giả, có thể thu nhỏ L2 | Chưa làm (làm sau bước 3) |
| 3 | Loop agent trên L2 (`l2/l2_fmax.py`), tile cố định ở `asic-7` | Đã viết, **chưa chạy** |

Theo khung `docs/workflow-phases.md`, project này nằm ở nhánh **ASIC**. Pha **Simulate** dùng lại gate của `rocket-fmax`
cho đến bước 3.

## File

- `soc_synth.py`: build `RocketConfig` (baseline, cộng thêm các patch rocket-chip nếu có), Yosys `v3`, sau đó đo timing với
  từng giá trị thời gian truy cập SRAM. Xem docstring.
- `fmax_nodes.py`: chép nguyên từ `projects/rocket-fmax/fmax_nodes.py` (commit 2b796c9) vì `soc_synth.py` dùng các hàm build,
  Yosys và đọc kết quả STA trong đó. Không sửa ở đây.

## Bước 3: loop L2 (`l2/`)

`l2/` là bản port của loop `rocket-fmax` (`rocket_fmax.py`, `fmax_nodes.py`, `ab_tool.py`, prompt; commit 2b796c9). Phần loop
giữ nguyên: agent sửa Chisel, tự đo bằng `synth_ab`, rồi loop build, synth, chạy gate và quyết định nhận hay bỏ.
Những chỗ khác:
- Agent chỉ được sửa `generators/rocket-chip-inclusive-cache/design/craft/inclusivecache/src/` (sifive `InclusiveCache`).
- Top synth là `InclusiveCache`, period mặc định 6 ns (như `soc-fmax-1`).
- `--tile-patch` (patch `asic-7`) được áp vào `rocket-chip` một lần trước snapshot, nên agent không được đụng `rocket-chip`.
  Baseline (gate, số cycle) là SoC có tile `asic-7` và L2 chưa sửa.
- Gate giống `rocket-fmax`: riscv-tests ISA và benchmark trên Verilator, chạy cả SoC, nên mọi miss L1 đều đi qua L2. Một cycle
  L2 thêm vào sẽ hiện ra ở `mcycle` (ngân sách +3 %).
- Agent mặc định là `--model opus --effort medium`. Cache riêng `~/chia_cache_l2`, namespace Ray `l2_fmax`, kết quả ở
  `~/soc-fmax-l2/<run>/`.
- Chưa port pha `sim` (`--phase sim`).

Kiểm trước (đường dẫn L2 trong image chipyard):
```bash
docker exec chia-chipyard-dingus-0 bash -lc 'ls /home/ray/chipyard/generators/rocket-chip-inclusive-cache/design/craft/inclusivecache/src/ && git -C /home/ray/chipyard/generators/rocket-chip-inclusive-cache status --short | head'
```

Chạy (không chạy song song với `soc_synth.py`: cả hai cùng sửa `rocket-chip` trong container chipyard):
```bash
cd ~/CHIA-workflow/projects/soc-fmax/l2
chia job submit --no-wait --submission-id l2-1 --working-dir . -- \
  python l2_fmax.py --run-name l2-1 --iters 5 --tile-patch $HOME/rocket-fmax-v2/asic-7/patches/best.patch
chia job logs -f l2-1
```

## Chạy bước 1

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

- `l2/`: mới qua `py_compile`. Chưa chạy.

- Đã kiểm: `py_compile`; dựng LEF giả, template Tcl và phần đọc kết quả (vùng, SRAM, Fmax trước và sau buffer, bảng tóm
  tắt) trên dữ liệu giả.
- **Chưa kiểm** (chưa có OpenROAD/OpenSTA trong sandbox):
  - OpenROAD đọc netlist phân cấp cùng LEF giả của SRAM;
  - `initialize_floorplan` và `repair_design` chạy được khi chưa có placement;
  - `find_timing_paths` và `get_property` trên bản OpenSTA trong image;
  - `set sta_recovery_removal_checks_enabled 0` và `all_registers -async_pins`.

  Nếu bước OpenROAD lỗi, script tự đo lại bằng OpenSTA không buffer, ghi cột `buffer` = `none (openroad failed)` và đưa lỗi
  vào `summary.md`.
