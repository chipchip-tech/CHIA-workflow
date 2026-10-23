# Ascon-Hash256 RoCC cho Rocket: kết quả cuối (M4)

Đây là bộ tăng tốc RoCC Ascon-Hash256 do agent OpenCode (`opencode/big-pickle`) viết
trong loop CHIA. Mọi file khác trong thư mục này (ngoài README này) được sinh bằng
`scripts/make_final.sh` từ lần chạy loop ghi trong `SOURCE.txt`, và có checksum trong
`SHA256SUMS`.

- Agent viết đúng ở lượt đầu (`--run-id m2`, vòng 0, 389 s). Không có vi phạm luật.
- Lõi: [rprimas/ascon-verilog](https://github.com/rprimas/ascon-verilog) @ `e1069549`,
  biến thể V4 (bus 64 bit, UROL=1), gộp thành `AsconCoreV4.sv` (sha256 `8b48b4dd…8865`).
- Chipyard: nhánh `chia_artifact` @ `40c7ddb6` (image Chipyard của CHIA), Verilator 5.022.

## Kết quả

Firmware 4 KiB (seed `0x5EC0B007`). Digest kỳ vọng tính bằng `ascon.py` ở cùng commit
(`expected.json`). `digest_match=1` nghĩa là `DIGEST_SW == DIGEST_HW ==` digest kỳ vọng.

| test | boot | digest_match | CYCLES_SW | CYCLES_ROCC | nhanh hơn |
|---|---|---|---|---|---|
| boot_good | OK | 1 | 503710 | 7021 | 71.7× |
| boot_tamper | FAIL | 1 | 503709 | 7018 | 71.8× |
| boot_partial | _chờ lần chạy M4_ | | | | |

Số liệu hai dòng đầu lấy từ job `ascon-m2a`. Bảng đầy đủ nằm trong `results.csv`.

- `boot_tamper` lật bit 3 của byte 1234 trước khi băm, nên digest khác và bootloader
  in `BOOT FAIL`.
- `boot_partial` băm 4093 byte (word cuối chỉ có 5 byte). Trước đó nó bắt đầu một hash
  rồi bỏ dở sau 2 lần ABSORB bằng một INIT mới, và đọc digest theo thứ tự 3, 2, 1, 0.
- CYCLES_ROCC tính cả INIT, 512 lần ABSORB và 4 lần SQUEEZE. Riêng lõi mất 6722 chu kỳ
  (đo trong testbench), nên phần tốn thêm do RoCC chỉ khoảng 300 chu kỳ.

## File

| File | Nội dung |
|---|---|
| `chipyard.patch` | Sửa file có sẵn: `generators/chipyard/src/main/scala/config/fragments/RoCCFragments.scala` (thêm `WithAsconRoCC`, mặc định `custom0`) |
| `files/generators/chipyard/src/main/scala/example/AsconRoCC.scala` | BlackBox `AsconCoreV4`, `AsconRoCC` (LazyRoCC) và `AsconRoCCModuleImp` |
| `files/generators/chipyard/src/main/scala/config/AsconConfigs.scala` | `chipyard.AsconRocketConfig` = `WithAsconRoCC` + `WithNHugeCores(1)` + `AbstractConfig` |
| `files/generators/chipyard/src/main/resources/vsrc/AsconCoreV4.sv` | Lõi Ascon (giống `rtl/AsconCoreV4.sv`) |
| `untracked.tar.gz` | Các file trong `files/` ở dạng tar, đường dẫn tính từ gốc Chipyard |
| `full.diff` | Diff toàn cây so với HEAD, để tham khảo |
| `results.csv`, `summary.json` | Kết quả của lần chạy, và chi tiết vòng được chọn (có cả digest) |
| `sim_<test>.log` | Output của chương trình trong simulator |
| `prompt.md`, `agent_reply.md` | Prompt đưa cho agent ở vòng đó, và câu trả lời của agent |
| `expected.json`, `run_args.json`, `install_rtl.json`, `SOURCE.txt` | Digest kỳ vọng, tham số loop, thông tin container, nguồn |

Kiểm tra file nguyên vẹn: `cd out/final && sha256sum -c SHA256SUMS`.

## Giao diện RoCC (custom0, opcode `0x0B`, `funct3 = {xd,xs1,xs2}`)

| funct7 | lệnh | funct3 | toán hạng | ý nghĩa |
|---|---|---|---|---|
| 0 | INIT | 000 | | Reset lõi 1 chu kỳ, xoá bộ đệm digest, rồi `mode=3` 1 chu kỳ để bắt đầu hash |
| 1 | ABSORB | 011 | `rs1` = 8 byte (little-endian), `rs2 = nbytes \| last<<8` | Đưa 1 word vào lõi. `nbytes` từ 1 đến 8, chỉ nhỏ hơn 8 khi `last=1` |
| 2 | SQUEEZE | 110 | `rs1` = chỉ số 0..3 | `rd` ← word digest thứ `rs1`, chờ tới khi word đó có |

Trước lệnh RoCC đầu tiên phải bật `mstatus.XS` (`csrs mstatus, 3<<15`). Mã C mẫu nằm ở
`sw/boot.c` (`ascon_hash256_rocc`).

Giới hạn (đúng theo đặc tả, nhưng người dùng cần biết):
- Không băm được thông điệp rỗng, vì `nbytes` tối thiểu là 1.
- Các lỗi lập trình làm CPU treo:
  - ABSORB khi chưa INIT;
  - SQUEEZE trước word cuối;
  - `nbytes` bằng 0 hoặc lớn hơn 8 (word bị nuốt mất mà lõi không nhận).

## Áp patch và chạy lại bằng tay

**Chưa chạy thử.** Các lệnh dưới viết theo cách loop làm (`ascon_loop.py`). Chạy trong
container Chipyard của CHIA, khi cluster đang bật. Đặt `C` theo tên container
(`docker ps`).

```bash
# trên host
cd ~/CHIA-workflow/projects/ascon-secureboot
C=chia-chipyard-$USER-0
docker cp out/final "$C":/tmp/ascon-final
docker cp sw "$C":/tmp/ascon-sw
docker exec -it "$C" bash -l
```

```bash
# trong container
cd /home/ray/chipyard
source env.sh
# Bỏ qua 2 lệnh sau nếu cây đã có thay đổi của agent (container vừa chạy loop):
git apply /tmp/ascon-final/chipyard.patch
cp -r /tmp/ascon-final/files/generators/. generators/

cd sims/verilator
make -j 16 CONFIG=AsconRocketConfig CONFIG_PACKAGE=chipyard
CF="-O2 -mcmodel=medany -static -specs=htif_nano.specs -I /tmp/ascon-sw"
riscv64-unknown-elf-gcc $CF /tmp/ascon-sw/boot.c -o /tmp/boot_good.riscv
riscv64-unknown-elf-gcc $CF -DTAMPER /tmp/ascon-sw/boot.c -o /tmp/boot_tamper.riscv
riscv64-unknown-elf-gcc $CF -DPARTIAL /tmp/ascon-sw/boot.c -o /tmp/boot_partial.riscv
for t in good tamper partial; do
  ./simulator-chipyard.harness-AsconRocketConfig +max-cycles=20000000 /tmp/boot_$t.riscv
done
```

Kỳ vọng: lần lượt `BOOT OK`, `BOOT FAIL`, `BOOT OK`, mỗi lần có `DIGEST_HW` bằng
`DIGEST_SW`. Bản `-DSW_ONLY` chạy được cả trên `RocketConfig` gốc.

## Chạy lại bằng loop (phát lại từ cache)

Lượt agent của `--run-id m2` nằm trong cache trên host
(`~/chia_cache/ascon-secureboot`), nên chạy lại không gọi LLM. Loop khôi phục cây của
agent vào container, build lại (`--no-build-replay`, vì simulator cũ mất khi
`chia down`), rồi chạy cả 3 test. Xem `projects/ascon-secureboot/README.md`, mục 5.

Bỏ `--run-id m2` (đặt id mới) thì loop gọi agent thật từ đầu.
