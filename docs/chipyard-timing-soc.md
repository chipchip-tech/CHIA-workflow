# SoC mẫu tối ưu timing: `TimingSoCConfig`

Config mẫu để phần SoC nền (core + uncore) không giới hạn timing khi lên FPGA/ASIC. Lý do và quyết định:
[timing-soc-config-spec.md](timing-soc-config-spec.md).

**Trạng thái (09/11/2026):** build Verilator trên fork đã chạy thật; `rv64ui-p-simple`, `rv64ui-p-add`,
`rv64um-p-mul`, `rv64um-p-div`, `rv64ua-p-amoadd_d`, `rv64uc-p-rvc` đều `EXIT=0`. Chưa chạy `hello` `rv64imac`
và kiểm tra module (mục 3–4). Chưa đo timing (việc của `project/timing-soc`).

## Chọn config nào

| | `MinimalSoCConfig` | `TimingSoCConfig` |
|---|---|---|
| ISA | RV64GC | RV64IMAC |
| MMU / Linux | Có | Không (chỉ bare-metal, chế độ M/U) |
| L2 | 128 KiB `InclusiveCache` | Không (broadcast hub) |
| L1 I/D | 16 KiB, 4 way | 16 KiB, 1 way |
| Dự đoán rẽ nhánh | Có | Không |
| Nhân/chia | `mulUnroll = 8`, early-out | Lặp từng bit, không early-out |
| Dùng khi | Cần chức năng đầy đủ, chạy mô phỏng | Đo timing khối của project trên FPGA/ASIC |

## Mixin (`chipyard/TimingMixins.scala` và có sẵn)

| Mixin | Nhắm vào đường găng | Giá phải trả |
|---|---|---|
| `freechips.rocketchip.subsystem.WithoutFPU` | FPU FMA, int↔fp | Mất F/D, binary phải là `rv64imac`/`lp64` |
| `chipyard.WithRocketMulDiv(mulUnroll, earlyOut)` | Bộ chia `core.div` | `mul` chậm (tới 64 chu kỳ khi `mulUnroll = 1`) |
| `chipyard.config.WithBroadcastManager` | L2 `InclusiveCache` | Miss L1 đi thẳng DRAM |
| `chipyard.WithNoRocketVM` | TLB, PTW, L2 TLB 1024 entry | Không Linux |
| `chipyard.WithNoRocketBTB` | Dự đoán rẽ nhánh | Phạt mỗi lần nhảy |
| `chipyard.WithRocketSlowLoad` | Load → store data | +1 chu kỳ load-use |
| `WithL1ICacheWays(1)`, `WithL1DCacheWays(1)` | So tag nhiều way | Conflict miss tăng |

Project có thể tự ghép từng mixin lên `MinimalSoCConfig`. Mixin sửa tile phải đứng **trước** (bên trái)
config tạo core trong chuỗi `++`.

## 1. Cài

```bash
cd ~/CHIA-workflow && git pull && scripts/install-chipyard-config.sh
```

Cần thấy `MỚI` cho `TimingMixins.scala` và `TimingSoCConfig.scala`, kèm `MIXIN_OK`.

## 2. Build (trong `chipyard-dev`, xem [chipyard-minimal-soc.md](chipyard-minimal-soc.md) mục 2)

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd sims/verilator && make -j16 CONFIG=TimingSoCConfig 2>&1 | tee build-timing.log'
```

## 3. Test chức năng

Chỉ dùng test dạng `-p-` (không còn VM). Mỗi dòng in `<test> EXIT=<mã>`, cần tất cả là `0`:

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd sims/verilator && for t in rv64ui-p-simple rv64ui-p-add rv64um-p-mul rv64um-p-div rv64ua-p-amoadd_d rv64uc-p-rvc; do make CONFIG=TimingSoCConfig run-binary BINARY=$RISCV/riscv64-unknown-elf/share/riscv-tests/isa/$t >/dev/null 2>&1; echo "$t EXIT=$?"; done'
```

`hello` build không FPU (`rv64imac`/`lp64`). Nếu báo thiếu thư viện `lp64` thì toolchain không có multilib đó:
ghi lại, bỏ qua bước này.

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd tests && riscv64-unknown-elf-gcc -std=gnu99 -O2 -fno-common -fno-builtin-printf -march=rv64imac -mabi=lp64 -specs=htif_nano.specs -static hello.c -o hello-imac.riscv && cd ../sims/verilator && make CONFIG=TimingSoCConfig run-binary BINARY=../../tests/hello-imac.riscv; echo EXIT=$?'
```

## 4. Kiểm tra đã bỏ đúng khối

Mỗi tên in số file có chứa module đó. Cần tất cả là `0`:

```bash
cd ~/tools/RISCVTutorial/sims/verilator/generated-src/chipyard.harness.TestHarness.TimingSoCConfig/gen-collateral && for m in InclusiveCache FPU PTW BTB; do echo "$m $(grep -l "^module $m\b" *.sv *.v 2>/dev/null | wc -l)"; done
```

Gửi lại: output script cài, đuôi `build-timing.log` (hoặc lỗi đầu tiên), các dòng `EXIT=`, kết quả bước 4.
