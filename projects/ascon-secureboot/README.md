# Ascon secure boot trên Rocket (RoCC) bằng CHIA

Dùng CHIA làm một vòng lặp, trong đó agent OpenCode thêm bộ tăng tốc RoCC bọc lõi Ascon
([rprimas/ascon-verilog](https://github.com/rprimas/ascon-verilog) @ `e1069549`, biến thể
v4: bus 64 bit, UROL=1) vào Rocket trong Chipyard. Một bootloader bare-metal băm firmware
4 KiB bằng Ascon-Hash256 (NIST SP 800-232), so với digest lưu sẵn, rồi in `BOOT OK` hoặc
`BOOT FAIL`.

Trạng thái: **M1 đạt** trên máy người dùng (23/10/2026). Đang làm **M2**. Các mốc ở cuối file.

## File

| File | Vai trò | Ai viết |
|---|---|---|
| `rtl/AsconCoreV4.sv` | 5 file RTL upstream gộp thành một (`define V4`, bỏ `include`) và wrapper `AsconCoreV4` có cổng `logic` thường | sinh bằng `scripts/prepare_rtl.py` |
| `sw/boot.c` | Ascon-Hash256 bằng C (mốc SW), đường RoCC qua `.insn`, đo `rdcycle`, verdict | session |
| `sw/image.h`, `sw/expected.json` | Image 4 KiB (seed `0x5EC0B007`) và digest tính bằng `ascon.py` | sinh bằng `scripts/gen_image.py` |
| `loop/ascon_loop.py` | Driver CHIA: cài RTL, compile, agent, thu cây, build, 2 test, `results.csv` | session |
| `loop/agent_prompt.md` | Prompt cho agent: đặc tả RoCC, cổng của BlackBox, quy tắc | session |
| `loop/exp.yaml` | Cấu hình cache và bypass (theo `_chia_tag`) | session |
| `scripts/selftest.sh`, `tb/*` | Tự kiểm ngoài CHIA: SW so với `ascon.py`, RTL trong Verilator | session |
| `tests/smoke_local.sh`, `tests/smoke_seed.py` | Kiểm orchestration bằng Ray thật với Chipyard giả | session |
| `out/` | Kết quả mỗi lần chạy (git bỏ qua, trừ `out/final/`) | loop |

Sinh lại file (không bắt buộc, vì file sinh ra đã được commit):

```bash
python3 scripts/prepare_rtl.py   # clone ascon-verilog@e1069549 -> rtl/AsconCoreV4.sv (in sha256)
python3 scripts/gen_image.py     # -> sw/image.h, sw/expected.json
```

## Giao diện RoCC (custom0, `funct3 = {xd,xs1,xs2}`)

| funct7 | Lệnh | funct3 | Ý nghĩa |
|---|---|---|---|
| 0 | `ASCON_INIT` | 000 | Reset core 1 chu kỳ, xoá buffer digest, phát xung `mode=M_HASH256` 1 chu kỳ |
| 1 | `ASCON_ABSORB` | 011 | `rs1` = 8 byte little-endian, `rs2 = nbytes \| (last<<8)`. `nbytes` từ 1 đến 8; được nhỏ hơn 8 chỉ khi `last=1`. Không trả `rd`; backpressure bằng `cmd.ready` |
| 2 | `ASCON_SQUEEZE` | 110 | `rd` <- word digest thứ `rs1` (0..3), chờ tới khi word đó có |

Wrapper RoCC giữ `bdo_ready=1` và chép 4 word digest vào buffer. Lý do: core xuất digest
tuần tự và chỉ về IDLE khi đã lấy đủ 4 word. Ràng buộc: message dài ít nhất 1 byte. Chi
tiết hành vi của core nằm trong `loop/agent_prompt.md`.

## Đã kiểm chứng và chưa kiểm chứng

**Đã chạy trong sandbox của session** (Ubuntu 24.04, Verilator 5.020, gcc x86,
riscv64-unknown-elf-gcc 13.2 với picolibc):
- `prepare_rtl.py` cho cùng sha256 `8b48b4dd…8865`, dù clone mới hay dùng bản checkout sẵn.
  `verilator --lint-only` không có lỗi (chỉ cảnh báo `UNOPTFLAT`, giống upstream).
- `ascon.py` cho Ascon-Hash256("") = `0b3be585…92b2`, khớp KAT của SP 800-232.
- `scripts/selftest.sh`: pass hết.
  - `boot.c` biên dịch native với `-DSW_ONLY`: `boot_good` in `BOOT OK`, `boot_tamper`
    in `BOOT FAIL`, digest đúng như `expected.json`.
  - SW khớp `ascon.py` ở 39/39 độ dài.
  - Core trong Verilator, điều khiển đúng giao diện trên, khớp `ascon.py` ở 152/152
    hash (có reset hoặc không, có stall ngẫu nhiên hoặc không). Hash 1024 byte mất 1730
    chu kỳ (bằng bảng V4 của upstream); 4 KiB mất 6722 chu kỳ core.
- `boot.c` biên dịch được cho RV64. Ba lệnh `.insn` mã hoá đúng: `0x0000000b`; `funct7=1`
  `funct3=3`; `funct7=2` `funct3=6`.

**Đã chạy trên máy người dùng (M1, job `ascon-m1`):**
- `install_rtl` ghi `AsconCoreV4.sv` vào container với `sha_ok=True`. Verilator của image
  là **5.022** (conda-forge), lint `rc=0`. Chipyard nhánh `chia_artifact` @ `40c7ddb6`.
- Compile với `htif_nano.specs` chạy được (ELF 41752 byte). Cache và bypass chạy đúng:
  compile và build `RocketConfig` được phát lại từ cache của một lần chạy trước (build 0 s).
- Agent giả: `BashTool` bật rồi `stop()`. `collect_tree` thu được `AsconCoreV4.sv`.
- `RocketConfig` với `-DSW_ONLY`: `boot_good` in `BOOT OK`, `boot_tamper` in `BOOT FAIL`.
  Digest khớp `ascon.py`. **CYCLES_SW = 498030 / 498075** cho 4 KiB (khoảng 122
  chu kỳ/byte).

**Chưa chạy:**
- Agent thật, `AsconRocketConfig`, đường RoCC (M2, M3).
- `tests/smoke_local.sh`.

## Chạy trên máy local (Bazzite)

### 0. Lấy code

```bash
cd ~/CHIA-workflow && git fetch origin && git checkout temp && git pull origin temp
cd ~/CHIA-workflow/projects/ascon-secureboot
```

### 1. (Tuỳ chọn) Tự kiểm không cần cluster

Cần `gcc`, `git`, `python3` trên host. Bước Verilator tự bỏ qua nếu không có `verilator`.

```bash
scripts/selftest.sh
```

### 2. (Tuỳ chọn) Smoke test orchestration, khi cluster đang **tắt**

Script tự bật một Ray head riêng, và cuối cùng chạy `ray stop --force`. Vì vậy phải
`chia down` trước; nếu Ray đang chạy, script sẽ từ chối chạy. Cần `gcc` và `git` trên host.

```bash
conda activate chia_env
chia down ~/CHIA-workflow/configs/cluster-local.yaml -y
PY=python tests/smoke_local.sh        # in "smoke: all scenarios passed" nếu ổn
```

Script kiểm 5 kịch bản: (A) M1 pass; (B) chạy lại cùng `--run-id` thì không build lại;
(D) lỗi build được đưa vào prompt vòng sau; (C) phát lại một lượt agent từ cache và khôi
phục cây; (E) sửa file trong submodule rocket-chip bị báo vi phạm.

### 3. Mốc M1 trên cluster thật

```bash
conda activate chia_env
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia up ~/CHIA-workflow/configs/cluster-local.yaml -y
chia status                    # cần đủ chipyard, verilator_run, opencode_creds

cd ~/CHIA-workflow/projects/ascon-secureboot
chia job submit --no-wait --submission-id ascon-m1 --working-dir loop -- \
  python ascon_loop.py --project-dir $PWD --out-dir $PWD/out/m1 --run-id m1 \
  --config RocketConfig --sw-only --fake-llm --iters 1
chia job logs -f ascon-m1      # Ctrl+C chỉ ngừng xem log; dừng job: chia job stop ascon-m1 --kill-tracked-pids
```

Kỳ vọng khi M1 chạy được:
1. Log có dòng `AsconCoreV4.sv sha256=8b48b4dd… sha_ok=True`, phiên bản Verilator của
   image, nhánh và commit của Chipyard, và `lint: rc=0 ok=True`.
2. `compiled boot_good …` và `compiled boot_tamper …`.
3. Ở `iteration 0`: agent `[fake-llm]`, `collected: … untracked [...AsconCoreV4.sv]`,
   `build RocketConfig: success=True`.
4. `boot_good: rc=0 boot=OK digest_match=1 passed=1` và
   `boot_tamper: rc=0 boot=FAIL digest_match=1 passed=1`, bảng `CYCLES_SW`, rồi
   `SUCCESS after 1 iteration(s)`.
5. Thư mục `out/m1/` có: `results.csv`, `install_rtl.json`, `iter0/` (gồm
   `chipyard.patch`, `files/…/AsconCoreV4.sv`, `build.log`, `sim_boot_*.log`,
   `prompt.md`, `summary.json`).

Kiểm cache: chạy lại đúng lệnh trên với `--submission-id ascon-m1b`. Lần này build và
compile phải được phát lại từ `~/chia_cache/ascon-secureboot`, xong trong vài phút thay vì
phải build lại.

Gửi lại cho session:

```bash
chia job logs ascon-m1 > ~/ascon-m1.log
cd ~/CHIA-workflow/projects/ascon-secureboot/out/m1
cat results.csv install_rtl.json iter0/summary.json
tail -30 iter0/sim_boot_good.log iter0/sim_boot_tamper.log
ls -R iter0 | head -40
```

Kèm `~/ascon-m1.log`. Nếu build lỗi, gửi thêm `tail -100 iter0/build.log`.

### 4. Mốc M2: agent thật, chạy tới khi `AsconRocketConfig` build pass

Không còn `--fake-llm` và `--sw-only`, nên test được biên dịch với đường RoCC. Cờ
`--stop-after-build` dừng loop ở vòng đầu tiên build pass. Test của vòng đó vẫn chạy và
được ghi lại, nên có số liệu M3 sớm. Mỗi vòng gồm: agent (tối đa 30 phút), build đầy đủ
(make clean), rồi 2 test. Tối đa 8 vòng, nên có thể chạy qua đêm.

```bash
conda activate chia_env
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia status || chia up ~/CHIA-workflow/configs/cluster-local.yaml -y
cd ~/CHIA-workflow/projects/ascon-secureboot
chia job submit --no-wait --submission-id ascon-m2 --working-dir loop -- \
  python ascon_loop.py --project-dir $PWD --out-dir $PWD/out/m2 --run-id m2 \
  --config AsconRocketConfig --iters 8 --stop-after-build
chia job logs -f ascon-m2
```

- Nếu job chết giữa chừng (mất điện, OOM, `chia down`), chạy lại **đúng lệnh trên** với
  `--submission-id` mới và giữ `--run-id m2`. Các vòng đã xong được phát lại từ cache:
  `agent (replayed ...)`, cây được khôi phục vào container, build không chạy lại.
- Nếu build bị OOM, thêm `--make-jobs 8`.
- Có thể xem agent đang sửa gì:
  `docker exec chia-chipyard-$USER-0 git -C /home/ray/chipyard status --short generators/chipyard`.

Gửi lại cho session:

```bash
chia job logs ascon-m2 > ~/ascon-m2.log
cd ~/CHIA-workflow/projects/ascon-secureboot/out/m2
cat results.csv; for d in iter*/; do echo "== $d"; cat $d/summary.json; done
cat iter*/agent_reply.md
L=$(ls -d iter* | sort -V | tail -1); cat $L/chipyard.patch; ls -R $L/files
tail -60 $L/build.log; tail -30 $L/sim_boot_*.log
```

Kèm `~/ascon-m2.log`. Nếu vòng cuối vẫn build lỗi, gửi thêm `cat $L/feedback.md`.

### Các tham số hay dùng

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `--config` | `AsconRocketConfig` | M1 dùng `RocketConfig` |
| `--iters` | 8 | Dừng sớm khi cả 2 test đạt |
| `--stop-after-build` | tắt | M2: dừng ở vòng đầu tiên build pass |
| `--sw-only` | tắt | Biên dịch test với `-DSW_ONLY` |
| `--fake-llm` | tắt | Agent trả câu mẫu (M1) |
| `--run-id` | bắt buộc | Không gian tên của cache. Dùng lại cùng id để tiếp tục hoặc phát lại; đổi id để chạy mới. `--no-replay` bỏ qua cache |
| `--make-jobs` | 32 | Giảm xuống 8 nếu bị OOM |
| `--timeout-cycles` | 20 000 000 | `+max-cycles` của simulator |
| `--llm-timeout` | 1800 | Mỗi lượt agent (`retries=1`) |
| `--cache-dir` | `~/chia_cache/ascon-secureboot` | Trên host |

## Mốc

- **M1** (đạt 23/10/2026): khung loop với LLM bypass, chạy trên `RocketConfig` với
  `-DSW_ONLY`, và thu được file về host.
- **M2**: bật agent thật, chạy tới khi `AsconRocketConfig` build pass. ← đang ở đây
- **M3**: bản RoCC: 2 test đạt, `DIGEST_HW == DIGEST_SW ==` `ascon.py`, có cycles.
- **M4**: `out/final/` (patch cuối, mọi file, `results.csv`, README áp patch và chạy lại).
