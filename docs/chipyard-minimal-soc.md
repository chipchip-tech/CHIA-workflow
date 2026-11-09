# SoC nền tối giản: `MinimalSoCConfig`

SoC gốc cho mọi project, chạy trên fork RISCVTutorial (Chipyard 1.10.0) ở `~/tools/RISCVTutorial`.
Lý do và các quyết định: [minimal-soc-config-spec.md](minimal-soc-config-spec.md).

**Trạng thái:** bước 1 (cài) đã chạy thật trên fork (09/11/2026): `MIXIN_OK`, sha256 `2bf5c9d6…`.
Build, chạy test và kiểm tra khối chưa chạy.

## Nội dung

| Giữ | Bỏ (không có trong SoC) |
|---|---|
| 1 Rocket core RV64GC + FPU, L1 I/D | GPIO, SPI, I2C, PWM |
| L2 128 KiB (1 bank, 4 way; `RocketConfig` là 512 KiB) | Block device, NIC |
| 1 kênh bộ nhớ, UART, serial-TL/TSI, bootROM | Accelerator (Gemmini, Saturn...), BOOM |
| CLINT, PLIC, Debug (JTAG) | |

Mã nguồn: [`chipyard/MinimalSoCConfig.scala`](../chipyard/MinimalSoCConfig.scala) (lớp `chipyard.MinimalSoCConfig`).

## 1. Cài vào fork

Trên host (fork mặc định `~/tools/RISCVTutorial`; khác chỗ thì truyền đường dẫn hoặc đặt `CHIPYARD_FORK`):

```bash
cd ~/CHIA-workflow && git pull && scripts/install-chipyard-config.sh
```

Cần thấy `MỚI` (hoặc `GIỐNG`), một dòng `sha256sum` và `MIXIN_OK`. Gặp `CẢNH BÁO` thì fork đã đổi tên
mixin: gửi log lại để sửa `MinimalSoCConfig.scala`.

Sửa `chipyard/MinimalSoCConfig.scala` trong repo này rồi chạy lại script, **không** sửa thẳng bản trong fork
(lần cài sau sẽ ghi đè, script có in `diff` trước khi ghi).

## 2. Build và chạy thử

Dùng container `chipyard-dev` như [cluster-blocks.md](cluster-blocks.md) mục "Làm việc tay với fork"
(đừng build cùng lúc với khối `chipyard` của CHIA). Chạy trong `tmux`, lần đầu mất khá lâu vì phải elaborate:

Chưa có container thì tạo (báo trùng tên thì `docker start chipyard-dev`):

```bash
docker run -dit --name chipyard-dev --shm-size=10.24gb -v $(realpath ~/tools/RISCVTutorial):$(realpath ~/tools/RISCVTutorial):z -w $(realpath ~/tools/RISCVTutorial) ghcr.io/ucb-bar/chia-chisel-build:latest bash
```

Build:

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd sims/verilator && make -j16 CONFIG=MinimalSoCConfig 2>&1 | tee build-minimal.log'
```

Chạy test ISA (giống lần kiểm chứng `RocketConfig`), cần thấy kết thúc không lỗi:

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd sims/verilator && make CONFIG=MinimalSoCConfig run-binary BINARY=$RISCV/riscv64-unknown-elf/share/riscv-tests/isa/rv64ui-p-simple; echo EXIT=$?'
```

Chạy `hello` qua UART (`tests/` của Chipyard), cần thấy dòng chào in ra:

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd tests && make hello.riscv; cd ../sims/verilator && make CONFIG=MinimalSoCConfig run-binary BINARY=../../tests/hello.riscv; echo EXIT=$?'
```

## 3. Kiểm tra đã bỏ đúng khối

```bash
cd ~/tools/RISCVTutorial/sims/verilator/generated-src/chipyard.harness.TestHarness.MinimalSoCConfig && grep -o 'cache-size = <[0-9]*>' *.dts | sort | uniq -c; ls -R | grep -ciE 'gpio|spi|i2c|pwm|scratchpad'
```

Cần thấy L2 `cache-size = <131072>` (128 KiB; các dòng 16384 là L1) và số đếm thứ hai là `0`.

Gửi lại: dòng `sha256sum`, đuôi `build-minimal.log`, hai dòng `EXIT=`, và kết quả lệnh kiểm tra.

## 4. Kế thừa trong project

Trong nhánh `project/<tên>`, đặt config của project trong `projects/<tên>/` và kế thừa:

```scala
class MyProjectConfig extends Config(
  new WithMyBlock ++                // khối của project (RoCC, MMIO...)
  new chipyard.MinimalSoCConfig)
```

Mixin đứng trước trong chuỗi `++` thắng, nên project có thể đè lại L2 hay số core.
