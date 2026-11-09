# Spec: Config SoC mẫu tối ưu timing (`TimingSoCConfig`)

- Ngày: 09/11/2026
- Nhánh: mixin và config trên `setup`; công cụ đo và các lần đo trên `project/timing-soc`
- Trạng thái: **đã duyệt; phần `setup` đã viết (09/11/2026), chưa build**. Hướng dẫn: `docs/chipyard-timing-soc.md`.
- Liên quan: [minimal-soc-config-spec.md](minimal-soc-config-spec.md) (nền đầy đủ chức năng),
  `projects/rocket-fmax/REPORT.md` trên nhánh `project/rocket-fmax` (số liệu đường găng).

## 1. Mục tiêu

Có một config SoC mẫu để project đặt khối của mình lên. Khi lên FPGA hoặc ASIC, **phần SoC nền (core +
uncore) không được là thứ giới hạn timing**, để timing đo được phản ánh khối của project.

`MinimalSoCConfig` giữ nguyên làm nền đầy đủ chức năng (FPU, MMU, L2). `TimingSoCConfig` là config riêng,
đánh đổi chức năng và hiệu năng để lấy timing.

**Không làm:** sửa RTL Rocket hay L2 (đó là việc của `project/rocket-fmax`), tách clock domain của tile
(async/rational crossing; xem mục 8), tối ưu riêng cho một board FPGA.

## 2. Các quyết định đã chốt (phỏng vấn 09/11/2026)

| # | Câu hỏi | Người dùng chọn |
|---|---|---|
| 1 | Được hy sinh gì | Bỏ FPU; bỏ MMU (không Linux); bỏ L2; giảm hiệu năng core (giữ đủ lệnh) |
| 2 | Đo trên nền nào | Cả FPGA và ASIC. Đạt khi đường găng không nằm trong `RocketTile` và Fmax cao hơn `MinimalSoCConfig` |
| 3 | Hướng | B: mixin riêng lẻ, đo theo bậc thang, gỡ mixin không có tác dụng |
| 4 | Công cụ đo đặt ở đâu | Project riêng `project/timing-soc`; kết luận đưa về `setup` |

## 3. Căn cứ: đường găng đã đo (`project/rocket-fmax`)

Đo bằng Yosys + OpenSTA sky130hd (tile và cả SoC) và Vivado post-route Arty A7-100T (cả SoC):

| Họ đường găng | Nằm ở | Số liệu |
|---|---|---|
| Bộ chia `core.div` (`divisor` → `remainder`) | Tile | Baseline tile 47 MHz |
| FPU FMA `fpuOpt.dfma`/`sfma`, `fpuOpt.ifpu` | Tile | Chặn cả SoC ở baseline: 54,8 MHz (synth) |
| Load → store data (`dcache.s2_req_addr` → `core.mem_reg_wdata`) | Tile | |
| L2 `InclusiveCache` (`sinkA.putbuffer`, MSHR) | Uncore | Chặn SoC sau khi tile đã nhanh: 63,8 MHz (synth) |
| (FPGA) cả SoC, `RocketArty100TConfig` (vốn không có L2) | | 55,5 MHz; tile tối ưu RTL: 59,9 MHz |

Số synth cả SoC của `rocket-fmax` chưa thật tin cậy (thiếu `repair_design`, xem REPORT mục 7). Spec này dùng
chúng để **chọn ứng viên**, không dùng làm mục tiêu tuyệt đối.

## 4. Mixin (trên `setup`, file `chipyard/TimingMixins.scala`, package `chipyard`)

Đã đối chiếu với rocket-chip commit `47f7b71` (Chipyard 1.10.0). Mixin nào có sẵn thì dùng, chỉ viết
mixin mới khi không có. Các mixin mới chỉ sửa `RocketTileAttachParams` trong `TilesLocated(InSubsystem)`,
theo cùng mẫu với `WithoutFPU` của rocket-chip.

| Mixin | Có sẵn? | Thay đổi | Nhắm vào họ | Giá phải trả |
|---|---|---|---|---|
| `freechips.rocketchip.subsystem.WithoutFPU` | Có | `fpu = None` | FMA, int↔fp | Mất F/D; binary phải là `rv64imac`/`lp64` |
| `WithRocketMulDiv(mulUnroll = 1, earlyOut = false)` | **Mới** | `MulDivParams(mulUnroll, divUnroll = 1, mulEarlyOut = earlyOut, divEarlyOut = earlyOut)` | `core.div` | `mul` tới 64 chu kỳ khi `mulUnroll = 1` |
| `chipyard.config.WithBroadcastManager` | Có | Thay L2 bằng broadcast hub | L2 `InclusiveCache` | Mọi miss L1 đi thẳng DRAM |
| `WithNoRocketVM` | **Mới** | `useVM = false`, `nL2TLBEntries = 0` | TLB, PTW, L2 TLB 1024 entry (do `AbstractConfig` 1.10 bật) | Không Linux, không chế độ S; chỉ test `-p-` |
| `WithNoRocketBTB` | **Mới** | `btb = None` | Dự đoán rẽ nhánh | Phạt rẽ nhánh mỗi lần nhảy |
| `WithRocketSlowLoad` | **Mới** | `fastLoadWord = false` | Load → dùng/store data | Thêm 1 chu kỳ load-use |
| `WithL1ICacheWays(1)`, `WithL1DCacheWays(1)` | Có | L1 direct-mapped | So tag nhiều way | Conflict miss tăng |

Giữ nguyên (không có trong danh sách hy sinh): A, C, PMP 8 vùng, Debug, UART, serial-TL. `WithRocketMulDiv`
giữ đủ lệnh M.

### Thứ tự trong config

Mixin đứng trước trong chuỗi `++` thắng, nên `WithBroadcastManager` phải đứng trước `MinimalSoCConfig`
(trong đó có `WithInclusiveCache`). Mixin sửa tile phải đứng **trước** (bên trái) dòng tạo core, vì chúng
sửa danh sách tile đã có.

```scala
class TimingSoCConfig extends Config(
  // Chỉ giữ những mixin còn lại sau khi đo (mục 6). Bản đầu có đủ:
  new chipyard.WithRocketSlowLoad ++
  new freechips.rocketchip.subsystem.WithL1ICacheWays(1) ++
  new freechips.rocketchip.subsystem.WithL1DCacheWays(1) ++
  new chipyard.WithNoRocketBTB ++
  new chipyard.WithNoRocketVM ++
  new chipyard.WithRocketMulDiv(mulUnroll = 1, earlyOut = false) ++
  new freechips.rocketchip.subsystem.WithoutFPU ++
  new chipyard.config.WithBroadcastManager ++
  new chipyard.MinimalSoCConfig)
```

## 5. File trên `setup` (chờ duyệt)

| File | Trạng thái | Nội dung |
|---|---|---|
| `chipyard/TimingMixins.scala` | mới | 4 mixin mới ở mục 4 |
| `chipyard/TimingSoCConfig.scala` | mới | `TimingSoCConfig` |
| `docs/chipyard-timing-soc.md` | mới | Chọn Minimal hay Timing, bảng mixin + giá phải trả, cài/build/test, kết quả đo (điền sau mục 6) |
| `scripts/install-chipyard-config.sh` | sửa | Thêm kiểm mixin `WithoutFPU`, `WithBroadcastManager`, `WithL1DCacheWays` |
| `README.md`, `CLAUDE.md` | sửa | Một dòng mỗi file |

Script cài đã copy `chipyard/*.scala`, nên hai file mới tự được cài.

## 6. Đo trên `project/timing-soc` (tách từ `setup` sau khi phần 5 xong)

### Bậc thang

Mỗi bậc là một lớp config trong project, thêm đúng một mixin so với bậc trước:

| Bậc | Config | Thêm |
|---|---|---|
| L0 | `MinimalSoCConfig` | (gốc) |
| L1 | `LadderL1Config` | `WithoutFPU` |
| L2 | `LadderL2Config` | `WithRocketMulDiv(1, false)` |
| L3 | `LadderL3Config` | `WithBroadcastManager` |
| L4 | `LadderL4Config` | `WithNoRocketVM` |
| L5 | `LadderL5Config` | `WithNoRocketBTB` |
| L6 | `LadderL6Config` | `WithRocketSlowLoad` |
| L7 | `LadderL7Config` | `WithL1ICacheWays(1)` + `WithL1DCacheWays(1)` |

Thứ tự theo mức đường găng đã đo (mục 3): họ chặn lớn nhất bỏ trước.

### Công cụ

- **ASIC** (`projects/timing-soc/config_synth.py`): sửa từ `soc_synth.py` của `rocket-fmax`. Nhận **danh sách
  tên config** thay vì patch RTL. Giữ flow v3 (Yosys phân cấp, sky130hd, `abc -D`, OpenSTA, SRAM giả). Thêm
  `repair_design` nếu làm được với khối asic. Mỗi bậc ghi: Fmax, worst slack, **họ đường găng và nó nằm trong
  `RocketTile` hay không**, diện tích. Chạy trên khối `asic` + `chipyard` của `docs/cluster-blocks.md`.
- **FPGA** (`projects/timing-soc/config_bitstream.py`): sửa từ `soc_bitstream.py`. Chỉ đo **L0 và config
  cuối**, vì mỗi bitstream mất 1–2 giờ. Cần một lớp `TimingSoCArty100TConfig` (`WithArty100TTweaks` +
  `TimingSoCConfig`) đặt trong `fpga/src` của fork; với L0 thì dùng `RocketArty100TConfig` có sẵn (lưu ý: vốn
  không có L2). Đo ở chế độ ép tần số (như `soc-asic6-f70`) để lấy Fmax thật.

### Quy tắc gỡ mixin

Một mixin bị gỡ khỏi `TimingSoCConfig` nếu bậc của nó **cùng lúc** thoả hai điều:
- Fmax ASIC thay đổi dưới 2 % so với bậc trước;
- họ đường găng không đổi.

Ngoại lệ: không gỡ mixin của một bậc mà đường găng ở bậc trước nằm trong chính khối mixin đó nhắm tới. Lý do
là bậc đó có thể chỉ chuyển đường găng sang chỗ khác cùng mức. Kết quả và lý do gỡ ghi vào
`projects/timing-soc/REPORT.md`, rồi sửa `TimingSoCConfig` trên `setup` và `git merge origin/setup` vào nhánh project.

## 7. Tiêu chí hoàn thành

**Chức năng** (Verilator, trên `TimingSoCConfig` cuối cùng):

1. Build xong.
2. Các test này đều `EXIT=0`:
   - `rv64ui-p-simple`, `rv64ui-p-add`;
   - `rv64um-p-mul`, `rv64um-p-div` (bộ nhân/chia sau khi chỉnh);
   - `rv64ua-p-amoadd_d` (atomics qua broadcast hub);
   - `rv64uc-p-rvc`.

   Chỉ dùng test `-p-`: không còn VM nên `-v-` không chạy được.
3. `hello` build với `-march=rv64imac -mabi=lp64` in ra lời chào. Nếu toolchain không có multilib `lp64`, ghi
   lại và chỉ dùng các test ở bước 2. **Kết quả 09/11/2026:** không có multilib `lp64`, bước này bỏ qua.
4. Không còn module `InclusiveCache`, `FPU`, `PTW`, `BTB` trong `generated-src`.

**Timing:**

5. ASIC: đủ 8 bậc có số liệu. Ở config cuối, đường găng **không nằm trong `RocketTile`**, và Fmax cao hơn L0.
6. FPGA: config cuối có bitstream. Fmax post-route cao hơn L0, và đường găng không nằm trong `RocketTile`
   (đọc từ báo cáo timing của Vivado).

Nếu ở config cuối đường găng vẫn nằm trong tile, ghi lại họ đường găng, rồi cân nhắc mục 8 trong spec sau.

## 8. Ngoài phạm vi / để sau

- Tách clock tile bằng `WithAsynchronousRocketTiles`/`WithRationalRocketTiles`, nếu tile vẫn chặn sau bậc L7.
- Tối ưu broadcast hub, bus, hoặc uncore. Đó là chỗ chặn tiếp theo, dự kiến sau khi tile thôi chặn.
- Giảm PMP, bỏ Debug, bỏ A/C: không nằm trong danh sách hy sinh đã chọn.
- Đưa `TimingSoCConfig` vào `blocks_smoke.py`.
