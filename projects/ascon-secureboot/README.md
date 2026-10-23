# Ascon secure boot trên Rocket (RoCC) bằng CHIA

Dùng CHIA làm một vòng lặp, trong đó agent OpenCode thêm bộ tăng tốc RoCC bọc lõi Ascon
([rprimas/ascon-verilog](https://github.com/rprimas/ascon-verilog) @ `e1069549`, biến thể
v4: bus 64 bit, UROL=1) vào Rocket trong Chipyard. Một bootloader bare-metal băm firmware
4 KiB bằng Ascon-Hash256 (NIST SP 800-232), so với digest lưu sẵn, rồi in `BOOT OK` hoặc
`BOOT FAIL`.

Trạng thái: **M1–M4 đạt** trên máy người dùng (23/10/2026). Mọi phần đã chạy thật: loop với
agent thật, 3 test RoCC, selftest, smoke test, và áp patch bằng tay.

## Tóm tắt

### Kết quả cuối

Agent (OpenCode `opencode/big-pickle`) viết đúng ngay lượt đầu (389 s). Cả 3 test đạt trên
đường RoCC, với `DIGEST_HW == DIGEST_SW ==` digest của `ascon.py`.

| test | kiểm điều gì | boot | CYCLES_SW | CYCLES_ROCC | nhanh hơn |
|---|---|---|---|---|---|
| boot_good | firmware 4 KiB nguyên vẹn | OK | 503710 | 7021 | 71.7× |
| boot_tamper | lật 1 bit ở byte 1234 | FAIL | 503709 | 7018 | 71.8× |
| boot_partial | 4093 byte (word cuối 5 byte), INIT giữa chừng, SQUEEZE 3→0 | OK | 497135 | 7027 | 70.7× |

Phần mềm tốn khoảng 122 chu kỳ/byte, RoCC khoảng 1.7 chu kỳ/byte. Riêng lõi mất 6722 chu
kỳ cho 4 KiB, nên phần tốn thêm của RoCC chỉ khoảng 300 chu kỳ. Kết quả cuối, patch và cách
áp patch nằm ở `out/final/`.

### Ai tạo file nào

**Claude (session) viết**:
- **Mã nguồn:** `scripts/prepare_rtl.py`, `scripts/gen_image.py`, `sw/boot.c`.
- **Loop:** `loop/ascon_loop.py`, `loop/agent_prompt.md`, `loop/exp.yaml`.
- **Tự kiểm:** `scripts/selftest.sh`, `tb/*`.
- **Smoke test:** `tests/smoke_local.sh`, `tests/smoke_seed.py`.
- **Dựng kết quả cuối:** `scripts/make_final.sh`.
- **Tài liệu:** README này và `out/final/README.md`.

Các file sinh ra bằng script:
- `rtl/AsconCoreV4.sv` (`prepare_rtl.py`).
- `sw/image.h` và `sw/expected.json` (`gen_image.py`). Digest `boot_partial` được thêm theo
  output của `gen_image.py` chạy trên máy người dùng, sha256 khớp.

**Agent viết** (trong container, ở `generators/chipyard/`):
- `src/main/scala/example/AsconRoCC.scala` (mới): BlackBox `AsconCoreV4`, `AsconRoCC`,
  `AsconRoCCModuleImp`.
- `src/main/scala/config/AsconConfigs.scala` (mới): `AsconRocketConfig`.
- `src/main/scala/config/fragments/RoCCFragments.scala` (sửa): thêm `WithAsconRoCC`.

**Loop tạo** trong `out/<run>/` trên host:
- **Cấp lần chạy:**
  - `run_args.json`, `expected.json`;
  - `install_rtl.json`: sha256 của lõi, phiên bản Verilator, lint, commit Chipyard,
    baseline `git status`;
  - `results.csv`, `profile/`.
- **Mỗi vòng `iterN/`:**
  - `prompt.md`, `agent_reply.md`, `agent_transcript.txt`;
  - `chipyard.patch`, `full.diff`, `git_status.txt`;
  - `untracked.tar.gz`, `untracked.txt`, `files/`;
  - `build.log`, `sim_<test>.log` và `.out`, `summary.json`;
  - `feedback.md`, chỉ khi vòng đó chưa đạt.

Ngoài `out/<run>/`:
- Cache trên host ở `~/chia_cache/ascon-secureboot` giữ lượt agent, ảnh chụp cây, build và
  ELF.
- Trong container:
  - `AsconCoreV4.sv` được ghi vào `vsrc/`;
  - kết quả build nằm ở `sims/verilator/`;
  - mỗi lần chạy sim có thư mục `/home/ray/ascon-sim/<8 hex>`.

`out/final/` (18 file) do `make_final.sh out/m3` sinh ra. Trong repo, 7 file do session
dựng lại từ nội dung người dùng dán, 11 file do người dùng tải lên. Cả 18 file khớp
`SHA256SUMS`.

### Flow của loop

Driver `ascon_loop.py` chạy như một job Ray trên host. Mỗi bước được gửi tới worker có
resource ghi trong ngoặc vuông.

```
Chuẩn bị (1 lần mỗi job)
 0. Đọc rtl/, sw/, prompt, exp.yaml; kiểm expected.json có đủ digest cho mọi test
 1. Bật cache (ChiaCacheStore) và Bypass, đăng ký provider/cond cho từng hàm
 2. install_rtl            [chipyard]  ghi AsconCoreV4.sv, kiểm sha256, lint bằng Verilator,
                                        chụp git status làm baseline
 3. compile_program x3     [chipyard]  riscv64-gcc -specs=htif_nano.specs (+-DTAMPER/-DPARTIAL)
                                        tag compile/<test>/<sha của nguồn+cờ>; trùng tag thì phát lại

Vòng N (tối đa 8)
 4. Agent                  [opencode_creds + BashTool giữ chipyard]
      tag <run>/iterN/agent; nếu cache có cả agent lẫn collect thì phát lại, không gọi LLM;
      xong thì BashTool.stop() để nhả slot chipyard
 5. collect_tree           [chipyard]  git diff, các file untracked trong generators/chipyard, git status
      khi phát lại: restore_tree đưa cây đã lưu vào container
      ghi iterN/*; find_violations so status với baseline
 6. ChiselBuildNode.build  [chipyard]  make clean; make -j32 CONFIG=AsconRocketConfig
      chỉ phát lại khi cây là cây của lượt agent đã phát lại (tắt bằng --no-build-replay)
 7. VerilatorRunNode x3    [verilator_run]  chạy lần lượt boot_good, boot_tamper, boot_partial
      parse_run: BOOT OK/FAIL đúng kỳ vọng, DIGEST_SW == DIGEST_HW == expected, rc=0
 8. Ghi results.csv và summary.json
      cả 3 test đạt và không vi phạm -> SUCCESS, dừng
      ngược lại -> build_feedback (lỗi build, log test, vi phạm) -> feedback.md -> prompt vòng N+1
```

### Các lần chạy thật

| Job | Tham số | Diễn biến |
|---|---|---|
| `ascon-m1` | `RocketConfig`, `--sw-only`, `--fake-llm`, `--iters 1` | Agent giả không sửa gì. Build và compile được phát lại từ cache. 2 test phần mềm đạt (CYCLES_SW khoảng 498K) |
| `ascon-m2` | | Thất bại trước khi chạy: máy người dùng vẫn là `ascon_loop.py` cũ, chưa có `--stop-after-build` |
| `ascon-m2a` | `AsconRocketConfig`, `--run-id m2`, `--stop-after-build` | Vòng 0: agent thật 389 s, tạo 2 file và sửa 1 file, không có vi phạm. Build 108 s. 2 test RoCC đạt. SUCCESS sau 1 vòng (834 s) |
| `ascon-m3` | `--run-id m2`, `--no-build-replay`, thêm `boot_partial` | Compile lại vì `boot.c` đã đổi. Agent phát lại từ cache (2 s). `restore_tree` khôi phục cây (3 file). Build lại 82 s. 3 test đạt. SUCCESS |

Các lần chạy ngoài loop, trên máy người dùng:
- `make_final.sh out/m3`: dựng `out/final/`.
- `selftest.sh` trong `ubuntu:24.04`: pass.
- `smoke_local.sh` trong `python:3.10`: lần 1 pass 4/5; lần 2 pass 5/5 sau khi sửa kịch bản E.
- Áp patch và build bằng tay: 3 test trùng từng chu kỳ với loop.

### Giới hạn

- Giao diện không băm được thông điệp rỗng.
- Các lỗi lập trình làm CPU treo: ABSORB khi chưa INIT; SQUEEZE trước word cuối; `nbytes`
  ngoài 1..8.
- Agent mới chạy 1 lần với 1 model, nên chưa đủ để kết luận về độ ổn định của agent.

## File

| File | Vai trò | Ai viết |
|---|---|---|
| `rtl/AsconCoreV4.sv` | 5 file RTL upstream gộp thành một (`define V4`, bỏ `include`) và wrapper `AsconCoreV4` có cổng `logic` thường | sinh bằng `scripts/prepare_rtl.py` |
| `sw/boot.c` | Ascon-Hash256 bằng C (mốc SW), đường RoCC qua `.insn`, đo `rdcycle`, verdict | session |
| `sw/image.h`, `sw/expected.json` | Image 4 KiB (seed `0x5EC0B007`) và digest tính bằng `ascon.py` (cả digest của 4093 byte đầu cho `boot_partial`) | sinh bằng `scripts/gen_image.py` |
| `loop/ascon_loop.py` | Driver CHIA: cài RTL, compile, agent, thu cây, build, 3 test, `results.csv` | session |
| `loop/agent_prompt.md` | Prompt cho agent: đặc tả RoCC, cổng của BlackBox, quy tắc | session |
| `loop/exp.yaml` | Cấu hình cache và bypass (theo `_chia_tag`) | session |
| `scripts/selftest.sh`, `tb/*` | Tự kiểm ngoài CHIA: SW so với `ascon.py`, RTL trong Verilator | session |
| `tests/smoke_local.sh`, `tests/smoke_seed.py` | Kiểm orchestration bằng Ray thật với Chipyard giả | session |
| `scripts/make_final.sh` | Dựng `out/final/` từ một lần chạy (vòng đã pass), kèm `SHA256SUMS` | session |
| `out/` | Kết quả mỗi lần chạy (git bỏ qua, trừ `out/final/`) | loop |
| `out/final/README.md` | Kết quả cuối, cách áp patch và chạy lại | session |

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

**Đã chạy trên máy người dùng (M2 + M3, job `ascon-m2a`, `--run-id m2`):**
- Agent thật (`opencode/big-pickle`) xong trong 1 lượt (389 s), không vi phạm luật. Agent
  tạo `example/AsconRoCC.scala` và `config/AsconConfigs.scala`, sửa
  `config/fragments/RoCCFragments.scala` (thêm `WithAsconRoCC`).
- `AsconRocketConfig` build pass ngay vòng 0 (108 s, `rc=0`).
- Đường RoCC: `boot_good` in `BOOT OK`, `boot_tamper` in `BOOT FAIL`. `digest_match=1`
  nghĩa là cả `DIGEST_SW` và `DIGEST_HW` đều bằng digest của `ascon.py` trong
  `expected.json` (`parse_run` trong `ascon_loop.py`).
- **CYCLES_SW = 503710 / 503709, CYCLES_ROCC = 7021 / 7018**, tức nhanh hơn khoảng
  **71.7 lần**. CYCLES_ROCC gồm cả 512 lệnh ABSORB và 4 SQUEEZE. Lõi riêng mất 6722 chu
  kỳ, nên phần chi phí RoCC chỉ khoảng 300 chu kỳ.

**Review mã của agent** (đọc mã, đối chiếu `rtl/AsconCoreV4.sv`, không mô phỏng thêm):
đúng đặc tả. INIT gồm 1 chu kỳ `rst` rồi 1 chu kỳ `mode=3` với `bdi_eoi=0`. ABSORB chỉ
được nhận khi `bdi_ready=1`; ở chế độ hash, `bdi_ready` của lõi chỉ phụ thuộc trạng thái
FSM, nên không có vòng tổ hợp. SQUEEZE đọc bộ đệm 4 word theo chỉ số. Giới hạn ghi ở
`out/final/README.md`.

**Đã chạy trên máy người dùng (M4, job `ascon-m3`, `--run-id m2`, `--no-build-replay`):**
- `gen_image.py` cho digest `boot_partial` = `ac6c73b1…5b2c`, hai digest cũ giữ nguyên.
  `sw/image.h` và `sw/expected.json` trong repo được sửa theo đúng output đó.
- Lượt agent và cây được phát lại từ cache (2 s, `restored 3 untracked files`), build lại
  pass (82 s).
- Cả 3 test đạt: `boot_partial` in `BOOT OK`, `digest_match=1` (word cuối 5 byte, INIT
  giữa chừng, SQUEEZE 3..0), CYCLES_SW 497135, CYCLES_ROCC 7027.

**`make_final.sh out/m3` đã chạy** trên máy người dùng, và `sha256sum -c` pass cả 18 file.
`sw/image.h` và `sw/expected.json` trong repo khớp byte với bản người dùng sinh ra. Người
dùng không push được (tài khoản GitHub đăng nhập bằng Google, thư mục không phải git
clone). Session dựng lại 7 file từ nội dung dán, người dùng tải 11 file còn lại lên qua
GitHub web. Cả 18 file trong repo khớp `SHA256SUMS`.

**`scripts/selftest.sh` pass trên máy người dùng** (23/10/2026, trong container
`ubuntu:24.04`, Verilator 5.020). `boot_partial` (`-DSW_ONLY`) in `BOOT OK`, digest
`ac6c73b1…5b2c`. SW khớp `ascon.py` 39/39, RTL khớp 152/152.

**`tests/smoke_local.sh` pass cả 5 kịch bản trên máy người dùng** (23/10/2026, container
`python:3.10`, `chialoops` 2.0.1, Ray 2.54.0). Lần đầu E hỏng do lỗi của chính script:
nó sửa submodule **trước** khi loop chạy, nên thay đổi thuộc baseline của `install_rtl`
và bị bỏ qua đúng như thiết kế. E giờ seed một lượt agent sửa submodule
(`smoke_seed.py ... violation`).

**Áp patch bằng tay đã chạy** (23/10/2026): đưa Chipyard về gốc, `git apply`, build
`AsconRocketConfig` từ đầu, 3 test cho kết quả trùng từng chu kỳ với job `ascon-m3`.
Lệnh simulator cần `+permissive +max-cycles=... +permissive-off`.

## Chạy trên máy local (Bazzite)

### 0. Lấy code

```bash
cd ~/CHIA-workflow && git fetch origin && git checkout temp && git pull origin temp
cd ~/CHIA-workflow/projects/ascon-secureboot
```

### 1. (Tuỳ chọn) Tự kiểm không cần cluster

Bazzite có thể không có `gcc`/`verilator` trên host, nên chạy trong container dùng một
lần (đã chạy được). `label=disable` để SELinux không chặn thư mục mount.

```bash
docker run --rm --security-opt label=disable -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/w -w /w ubuntu:24.04 bash -c '
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      gcc g++ make git python3 verilator ca-certificates > /dev/null &&
    scripts/selftest.sh' 2>&1 | tee ~/ascon-selftest.log
```

Nếu host có `gcc`, `git`, `python3`, chạy thẳng `scripts/selftest.sh`. Bước Verilator tự
bỏ qua nếu không có `verilator`.

### 2. (Tuỳ chọn) Smoke test orchestration

Chạy trong container dùng một lần (đã chạy được, khoảng 5 phút sau khi có image). Ray
trong container nằm trong network riêng, nên không cần tắt cluster CHIA.

```bash
docker run --rm --security-opt label=disable --shm-size=2g -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/w -w /w python:3.10 bash -c '
    pip install -q --default-timeout=300 --retries 10 chialoops==2.0.1 &&
    tests/smoke_local.sh' 2>&1 | tee ~/ascon-smoke.log   # cuối: "smoke: all scenarios passed"
```

Chạy trên host bằng `chia_env` cũng được, nhưng script tự bật Ray head riêng và cuối cùng
chạy `ray stop --force`. Vì vậy phải `chia down` trước (nếu Ray đang chạy, script từ chối
chạy), và host cần có `gcc`, `make`, `git`.

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
chia job logs ascon-m2a > ~/ascon-m2.log   # đổi theo --submission-id đã dùng
cd ~/CHIA-workflow/projects/ascon-secureboot/out/m2
cat results.csv; for d in iter*/; do echo "== $d"; cat $d/summary.json; done
cat iter*/agent_reply.md
L=$(ls -d iter* | sort -V | tail -1); cat $L/chipyard.patch; ls -R $L/files
tail -60 $L/build.log; tail -30 $L/sim_boot_*.log
```

Kèm `~/ascon-m2.log`. Nếu vòng cuối vẫn build lỗi, gửi thêm `cat $L/feedback.md`.

### 5. Mốc M4: test `boot_partial` rồi dựng `out/final/`

Lấy code mới và kiểm tra đã có bản mới:

```bash
cd ~/CHIA-workflow && git pull origin temp
cd ~/CHIA-workflow/projects/ascon-secureboot
grep -c "boot_partial" loop/ascon_loop.py        # phải ra 2
```

Sinh lại `sw/image.h` và `sw/expected.json` để có digest của 4093 byte đầu. Script clone
ascon-verilog@e1069549 vào thư mục tạm, nên cần mạng.

```bash
python3 scripts/gen_image.py
grep -c partial_digest sw/image.h                # phải ra 1
```

Hai digest `boot_good` (`13da31e2…1534`) và `boot_tamper` (`b28ba17c…e8e3`) phải giữ
nguyên. Nếu host có `gcc`, chạy thêm `scripts/selftest.sh` để kiểm phần SW của
`boot_partial` (không cần cluster).

Chạy loop với `--run-id m2`. Lượt agent và cây của nó được phát lại từ cache, nên không
gọi LLM. `--no-build-replay` buộc build lại (khoảng 2 phút), vì simulator cũ mất nếu
container đã bị tạo lại. Kết quả ghi vào `out/m3`.

```bash
conda activate chia_env
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
chia status || chia up ~/CHIA-workflow/configs/cluster-local.yaml -y
chia job submit --no-wait --submission-id ascon-m3 --working-dir loop -- \
  python ascon_loop.py --project-dir $PWD --out-dir $PWD/out/m3 --run-id m2 \
  --config AsconRocketConfig --iters 8 --no-build-replay
chia job logs -f ascon-m3
```

Kỳ vọng:
1. `agent (replayed, ...)`, rồi các dòng `git checkout`, `git apply`, `restored 3 untracked files`.
2. `build AsconRocketConfig: success=True`.
3. Ba dòng `boot_good ... passed=1`, `boot_tamper ... passed=1`, `boot_partial ... passed=1`.
4. `SUCCESS after 1 iteration(s)`.

Nếu `boot_partial` hỏng, loop đưa lỗi cho agent thật ở vòng 1. Đó là hành vi đúng của
loop, có thể để chạy tiếp.

Dựng `out/final/` từ vòng đã pass, rồi kiểm checksum:

```bash
scripts/make_final.sh out/m3
(cd out/final && sha256sum -c SHA256SUMS)
```

Đưa kết quả vào repo, nếu `~/CHIA-workflow` là bản git clone có quyền push:

```bash
cd ~/CHIA-workflow && git pull origin temp
git add projects/ascon-secureboot/sw/image.h projects/ascon-secureboot/sw/expected.json \
        projects/ascon-secureboot/out/final
git commit -m "ascon-secureboot: out/final from run m3, image.h with partial digest"
git push origin temp
```

Gửi lại cho session (thay `ascon-m3` nếu đã dùng `--submission-id` khác):

```bash
chia job logs ascon-m3 | grep -v ToolServerActor > ~/ascon-m3.log; tail -40 ~/ascon-m3.log
cd ~/CHIA-workflow/projects/ascon-secureboot
cat sw/expected.json out/final/results.csv out/final/SOURCE.txt
cat out/final/sim_boot_partial.log
```

Nếu không push được, gửi thêm `cat out/final/SHA256SUMS`. Từ `expected.json` session
dựng lại được `image.h`, vì định dạng file sinh ra là cố định.

### Các tham số hay dùng

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| `--config` | `AsconRocketConfig` | M1 dùng `RocketConfig` |
| `--iters` | 8 | Dừng sớm khi cả 2 test đạt |
| `--stop-after-build` | tắt | M2: dừng ở vòng đầu tiên build pass |
| `--sw-only` | tắt | Biên dịch test với `-DSW_ONLY` |
| `--no-build-replay` | tắt | Luôn build lại, kể cả khi phát lại lượt agent từ cache |
| `--fake-llm` | tắt | Agent trả câu mẫu (M1) |
| `--run-id` | bắt buộc | Không gian tên của cache. Dùng lại cùng id để tiếp tục hoặc phát lại; đổi id để chạy mới. `--no-replay` bỏ qua cache |
| `--make-jobs` | 32 | Giảm xuống 8 nếu bị OOM |
| `--timeout-cycles` | 20 000 000 | `+max-cycles` của simulator |
| `--llm-timeout` | 1800 | Mỗi lượt agent (`retries=1`) |
| `--cache-dir` | `~/chia_cache/ascon-secureboot` | Trên host |

## Mốc

- **M1** (đạt 23/10/2026): khung loop với LLM bypass, chạy trên `RocketConfig` với
  `-DSW_ONLY`, và thu được file về host.
- **M2** (đạt 23/10/2026, job `ascon-m2a`): agent thật, `AsconRocketConfig` build pass ở
  vòng 0.
- **M3** (đạt cùng job): bản RoCC: 2 test đạt, `DIGEST_HW == DIGEST_SW ==` `ascon.py`,
  CYCLES_ROCC khoảng 7020 so với CYCLES_SW khoảng 503700.
- **M4**: test `boot_partial` (thêm theo yêu cầu), `out/final/` (patch cuối, mọi file,
  `results.csv`, README áp patch và chạy lại). **Đạt 23/10/2026.**
