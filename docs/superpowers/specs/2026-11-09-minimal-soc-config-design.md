# Spec: Config SoC tối giản cho Chipyard (`MinimalSoCConfig`)

- Ngày: 09/11/2026
- Nhánh: `setup` (dùng chung cho mọi project)
- Trạng thái: **chờ người dùng duyệt spec**. Chưa viết code.

## 1. Mục tiêu

Cần một SoC nền, nhỏ nhất có thể nhưng vẫn chạy được chương trình bare-metal trên
Verilator. Các project (`project/ascon-secureboot`, `project/rocket-fmax`, project mới)
kế thừa SoC này và tự gắn thêm khối của mình, thay vì mỗi project tự bắt đầu từ
`RocketConfig` với những phần không cần.

**Không làm trong spec này:** gọn mã nguồn hay submodule Chipyard, build lại image
Docker, xoá build cũ, cấu hình cho FPGA hay ASIC riêng. Những việc đó thuộc nhánh FPGA/ASIC
trong `docs/workflow-phases.md` và sẽ có spec riêng.

## 2. Các quyết định đã chốt (phỏng vấn ngày 09/11/2026)

| # | Câu hỏi | Người dùng chọn |
|---|---|---|
| 1 | "Dọn config" nghĩa là gì | Chỉ viết một lớp Config Scala tối giản |
| 2 | Đích dùng | Nền chung cho mọi project |
| 3 | Giữ những khối nào | Danh sách đề xuất (mục 3) |
| 4 | Đưa vào Chipyard bằng cách nào | Lưu file trong repo này, script copy vào Chipyard |

## 3. Nội dung SoC

Cơ sở là Chipyard nhánh `chia_artifact`, cùng nhánh với image CHIA (Ubuntu 22.04). Mình đã
đọc `AbstractConfig.scala` và `RocketConfigs.scala` của nhánh này.

**Giữ:**

| Khối | Thực hiện bằng |
|---|---|
| 1 Rocket core (RV64GC, có FPU) | `freechips.rocketchip.rocket.WithNHugeCores(1)` |
| L1 I$/D$ | Mặc định của core trên |
| L2 nhỏ: 1 bank, 4 way, 128 KiB | `freechips.rocketchip.subsystem.WithInclusiveCache(nBanks=1, nWays=4, capacityKB=128)` (mặc định là 512 KiB) |
| 1 kênh bộ nhớ AXI4 | `WithNMemoryChannels(1)`, có sẵn trong `AbstractConfig` |
| UART | `chipyard.config.WithUART`, có sẵn |
| Serial-TL / TSI (nạp chương trình trong sim) | `testchipip.serdes.WithSerialTL`, có sẵn |
| BootROM | `chipyard.config.WithBootROM`, có sẵn |
| CLINT, PLIC | Có sẵn trong `BaseConfig` |
| Debug (JTAG DTM) | Có sẵn |

**Bỏ:**

| Khối | Thực hiện bằng | Ghi chú |
|---|---|---|
| Scratchpad 64 KiB trên mbus | `testchipip.soc.WithNoScratchpads` | Đã có DRAM qua mbus |
| GPIO, SPI, I2C, PWM, block device, NIC | Không cần mixin | `AbstractConfig` vốn không thêm các khối này. IOBinder/HarnessBinder của chúng chỉ chạy khi khối có mặt |
| Accelerator (Gemmini, Saturn...), BOOM | Không cần mixin | Không được thêm vào |

**Phát hiện khi đọc mã:** `RocketConfig` gốc gần như đã tối giản. Khác biệt thực chất chỉ
là scratchpad và kích thước L2. Lợi ích chính của `MinimalSoCConfig` là có **một điểm gốc
ổn định, đặt tên rõ**, để các project kế thừa và để so sánh diện tích/timing.

**Để mặc định, không đụng tới:** custom boot pin, boot-addr-reg, clock tap, tần số các bus
(500 MHz). Bỏ chúng sẽ phải sửa HarnessBinder và bootROM, nên rủi ro cao mà lợi ít. Nếu
project ASIC cần thì sẽ xem lại.

### Bản nháp Config

```scala
package chipyard

import org.chipsalliance.cde.config.Config

// SoC nền cho các project CHIA-workflow. Xem docs/chipyard-minimal-soc.md.
class MinimalSoCConfig extends Config(
  new testchipip.soc.WithNoScratchpads ++
  new freechips.rocketchip.subsystem.WithInclusiveCache(nBanks = 1, nWays = 4, capacityKB = 128) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)
```

Project kế thừa như sau:
`class AsconSoCConfig extends Config(new WithAscon ++ new chipyard.MinimalSoCConfig)`.

**Rủi ro cần kiểm khi chạy thật:** `WithInclusiveCache` có thể được áp hai lần (một lần ở
đây, một lần trong `AbstractConfig`). Mixin đứng trước trong chuỗi `++` sẽ thắng. Nếu
elaborate báo lỗi hoặc L2 vẫn là 512 KiB, đổi sang mixin chỉ chỉnh kích thước (ví dụ
`WithL2Capacity`, nếu có trong nhánh này).

## 4. File dự kiến (chờ duyệt)

| File | Trạng thái | Nội dung |
|---|---|---|
| `chipyard/MinimalSoCConfig.scala` | mới | Config ở mục 3 |
| `scripts/install-chipyard-config.sh` | mới | Copy file `.scala` vào Chipyard |
| `docs/chipyard-minimal-soc.md` | mới | Mục đích, danh sách khối, lệnh build/chạy, cách kế thừa, trạng thái kiểm chứng |
| `CLAUDE.md` | sửa | Thêm một mục ngắn |
| `README.md` | sửa | Thêm một dòng link tới docs mới |

### Script `install-chipyard-config.sh`

- Cú pháp: `install-chipyard-config.sh <đường-dẫn-chipyard>`. Mặc định dùng
  `$CHIPYARD_DIR`; nếu chưa đặt thì báo lỗi, không đoán.
- Kiểm tra `<chipyard>/generators/chipyard/src/main/scala/config/` có tồn tại. Nếu
  không có thì báo lỗi rõ ràng và thoát với mã khác 0.
- Copy (không tạo symlink, để container mount vẫn đọc được) tất cả `chipyard/*.scala` vào
  thư mục đó. Nếu file đích đã có và khác nội dung thì in `diff` rồi ghi đè; nếu giống thì
  bỏ qua.
- In `sha256sum` của file đã copy, để người dùng gửi lại xác nhận đúng phiên bản (vì
  `~/CHIA-workflow` trên máy họ không phải bản git clone).
- Dùng được cả trên host và trong container
  (`docker exec ... bash install-chipyard-config.sh /root/chipyard`). Đường dẫn
  Chipyard trong image sẽ xác nhận khi chạy thật.

## 5. Tiêu chí hoàn thành (người dùng chạy trên máy, gửi log)

Claude không chạy được lệnh trên máy người dùng, nên mỗi bước dưới đây có lệnh cụ thể trong
`docs/chipyard-minimal-soc.md`:

1. **Cài:** script chạy xong, in đúng `sha256sum`.
2. **Elaborate + build:** trong `sims/verilator`, lệnh
   `make CONFIG=MinimalSoCConfig -j 16` kết thúc với mã 0.
3. **Chạy:** `make CONFIG=MinimalSoCConfig run-binary BINARY=<hello.riscv>` in ra lời
   chào qua UART và kết thúc pass.
4. **Đã bỏ được khối:** so sánh với `RocketConfig`:
   - `grep -c` module scratchpad trong `generated-src/*MinimalSoCConfig*/` cho ra 0.
   - L2 trong file `.dts` hoặc `*.memconf` của bản build là 128 KiB.
   - Không có module GPIO/SPI/I2C.
5. Cập nhật `CLAUDE.md` và docs, chuyển các mục trên sang "đã kiểm chứng".

Nếu bước 2 hoặc 3 lỗi, dùng skill systematic-debugging dựa trên log, sửa trên `setup`.

## 6. Ngoài phạm vi / để sau

- Biến thể FPGA (board cụ thể) và ASIC (bỏ phần chỉ có trong sim).
- Bỏ debug, boot pin, clock tap.
- Gọn image hoặc mã nguồn để build nhanh hơn.
- Đưa `MinimalSoCConfig` vào job CHIA (`ChiselBuildNode`): làm sau khi build tay đã chạy.
