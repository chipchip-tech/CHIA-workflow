# Cluster theo khối: Chipyard + Verilator + Vivado + ASIC

Cluster CHIA một máy gồm các khối bật/tắt được. Dùng chung cho mọi project từ nay về sau.
Mọi công cụ nằm trong `~/tools` trên host và được mount vào container, nên sửa tay bằng editor được:

```
~/tools/
├── chipyard/                 Chipyard gốc, tag 1.15.0 (khối chipyard, vivado); toolchain + build nằm trong đây
├── OpenROAD-flow-scripts/    ORFS chép từ image chia-orfs (khối asic)
└── Xilinx/2025.1/            Vivado (khối vivado, mount chỉ đọc)
```

| Khối | Resource | Image | Mặc định | Trạng thái |
|---|---|---|---|---|
| `chipyard` | `chipyard` | `chia-chisel-build` + mount Chipyard | bật | ✅ 1.15.0: `blocks_smoke.py --clean` build `RocketConfig` `SUCCESS=True` (14/11/2026) |
| `verilator` | `verilator_run` | `chia-verilator-run` | bật | ✅ |
| `claude` | `claude_creds` | `chia-claude-code` + mount Chipyard **chỉ đọc** | bật (`--no-claude` để tắt) | ✅ (project rocket-fmax); mount Chipyard thêm 15/11/2026, chưa chạy thật |
| `vivado` | `vivado` | `chia-chisel-build` + mount Chipyard + `~/tools/Xilinx` | tắt (`--fpga`) | ✅ 1.15.0: `vivado v2025.1` trong worker; flow `fpga/` Arty A7-100T ra bitstream trong cùng image (mục 4, 14/11/2026) |
| `asic` | `asic` | `chia-orfs` tự build + mount `~/tools/OpenROAD-flow-scripts` | tắt (`--asic`) | ✅ `blocks_smoke.py` thấy openroad/yosys, `flow_writable` (08/11/2026) |

File liên quan:

- `configs/cluster-blocks.template.yaml`: template, không `chia up` thẳng.
- `scripts/setup-chipyard.sh`: clone Chipyard gốc (ghim tag) và dựng trong container.
- `docs/reinstall.md`: cài lại toàn bộ CHIA từ đầu theo từng bước.
- `scripts/chia-up-blocks.py`: sinh `cluster.gen.yaml` theo khối và `chia up`.
- `docker/orfs-ray.Dockerfile`: image cho khối asic.
- `scripts/setup-orfs.sh`: chép ORFS từ image ra `~/tools` để sửa tay.
- `examples/blocks_smoke.py`: kiểm tra các khối đang bật.

## 1. Dựng Chipyard

Chipyard gốc [`ucb-bar/chipyard`](https://github.com/ucb-bar/chipyard), ghim tag **1.15.0**, ở `~/tools/chipyard`.
Đủ 11 bước của `build-setup.sh` (gồm FireSim, FireMarshal) mất nhiều giờ (để máy chạy, hoặc dùng `tmux` nếu qua SSH). Cài từ đầu thì
làm theo [reinstall.md](reinstall.md); chỉ dựng Chipyard:

```bash
cd ~/CHIA-workflow && scripts/setup-chipyard.sh --verify
```

Script clone (nếu chưa có) và checkout tag thành nhánh cục bộ `local-1.15.0` (commit sửa tay được), rồi chạy
`build-setup.sh` trong một container `chia-chisel-build` tạm (`--rm`), ghi `.conda-env/`, `env.sh` vào thư mục
Chipyard và cache JVM vào `~/chia_cache/jvm`. Thành công khi thấy `Setup complete!` và (với `--verify`)
`VERIFY_OK`. Log: `<chipyard>/setup.log`, `<chipyard>/sims/verilator/build-verify.log`. Bước nào hỏng thì script
dừng, in số bước và lệnh chạy lại; không tự bỏ bước. Đồng bộ lên bản Chipyard mới hơn là việc riêng, chưa làm.

Chipyard phải mount **đúng cùng đường dẫn** trên host và trong container: `env.sh` và conda env
ghi đường dẫn tuyệt đối. UID của user trên host (1000) trùng UID `ray` trong image nên không
cần `--user`.

### Làm việc tay với Chipyard

Sửa mã trên host. Build/chạy trong một container dùng lâu dài:

```bash
docker run -dit --name chipyard-dev --shm-size=10.24gb -v $(realpath ~/tools/chipyard):$(realpath ~/tools/chipyard):z -w $(realpath ~/tools/chipyard) ghcr.io/ucb-bar/chia-chisel-build:latest bash
```

```bash
docker exec -it chipyard-dev bash -c 'source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && cd sims/verilator && make -j16 CONFIG=RocketConfig'
```

Đừng build trong `chipyard-dev` cùng lúc với khối `chipyard` của CHIA: hai bên ghi chung thư mục.

## 2. Bật cluster

```bash
conda activate chia_env && cd ~/CHIA-workflow
```

```bash
python scripts/chia-up-blocks.py
```

Thêm `--fpga`, `--asic`, `--no-claude` tuỳ nhu cầu; `--dry-run` để chỉ xem config. `--verilator-slots N` cho N sim
chạy song song trong container verilator (mặc định 1). `ASIC_MEM=20g` đổi giới hạn RAM của container asic (mặc định
`12g`; flow quá giới hạn thì container bị OOM-kill, không ảnh hưởng máy). Chipyard khác chỗ
mặc định (`~/tools/chipyard`) thì đặt `CHIPYARD_FORK=/đường/dẫn/tuyệt/đối`; ORFS khác chỗ thì `ORFS_DIR`.

**Thư mục làm việc chung `WORK_DIR`** (mặc định `~/chia_demo`, thêm 20/11/2026, **chưa chạy thật**): script tạo thư mục
nếu chưa có và mount nó **cùng đường dẫn thật** (`/var/home/...`) vào cả 5 khối, ghi được (khối `claude` cũng ghi được
ở đây, khác với Chipyard chỉ đọc). Loop ghi file lớn (VCD, bitstream, GDS, báo cáo) và file agent sửa vào đây, vì
`--working-dir` chỉ chép code vào mỗi container dưới dạng bản tạm. Đổi chỗ bằng `WORK_DIR=/đường/dẫn`. Script kiểm
trước: Chipyard có `env.sh`, Vivado có `settings64.sh`, image `chia-orfs` đã build, Claude đã đăng nhập.

Tắt cluster (từ `~/CHIA-workflow`):

```bash
python scripts/chia-up-blocks.py --down
```

Lệnh này đặt `THIS_MACHINE` như lúc bật rồi gọi `chia down cluster.gen.yaml -y`. Gọi thẳng `chia down` khi shell không có
`THIS_MACHINE` thì `head_ip` rỗng: CHIA in `Head:` trống, mọi `scoped ray stop` báo `is psutil available ...` và không
container nào bị gỡ (gặp 15/11/2026).

`cluster_name` là `default` như các config khác: mỗi lúc chỉ chạy một cluster.
Tên container thực tế có hậu tố `-0` (ví dụ `chia-chipyard-dingus-0`).

## 3. Kiểm tra

```bash
cd ~/CHIA-workflow/examples && chia job submit --working-dir . -- python blocks_smoke.py
```

Cần thấy `[chipyard] env` có `RISCV=<chipyard>/.conda-env/riscv-tools`, `SUCCESS=True`, và
`KẾT QUẢ: OK`. Thêm `--clean` để build lại từ đầu (xoá cả `chipyard.jar`).

## 4. Khối vivado (FPGA)

Vivado cài trên host ở `~/tools/Xilinx/<phiên bản>/Vivado` (bố cục từ 2025.1), mount chỉ đọc
đúng cùng đường dẫn thật (`settings64.sh` ghi cứng đường dẫn lúc cài). Thư mục khác thì đặt `XILINX_DIR`. Phiên bản lấy từ `VIVADO_VERSION` (mặc định `2025.1`). Khối dùng chung Chipyard
với khối chipyard để chạy flow `fpga/` của Chipyard.

### Flow `fpga/` ra bitstream (Arty A7-100T)

Đã chạy thật 14/11/2026 với `RocketArty100TConfig` có sẵn của Chipyard 1.15.0, trong container cùng image và
cùng mount với khối vivado: bitstream `Arty100THarness.bit` (3,8 MB), WNS +0,129 ns ở 50 MHz. Hold còn âm
nhẹ ở 4 endpoint (WHS −0,063 ns) với config gốc của upstream; flow vẫn ra bitstream (fpga-shells chỉ dừng khi
setup slack âm). Cần hai thứ:

1. **Board file Digilent** (fpga-shells gán board part `digilentinc.com:arty-a7-100:part0:1.1`; thiếu thì
   Vivado không mở được project). Cài một lần trên host:

   ```bash
   git clone --depth 1 https://github.com/Digilent/vivado-boards.git /tmp/vivado-boards && mkdir -p ~/tools/Xilinx/2025.1/Vivado/data/boards/board_files && cp -r /tmp/vivado-boards/new/board_files/arty-a7-100 ~/tools/Xilinx/2025.1/Vivado/data/boards/board_files/
   ```

   Kiểm tra (cần thấy `digilentinc.com:arty-a7-100:part0:1.1`):

   ```bash
   source ~/tools/Xilinx/2025.1/Vivado/settings64.sh && echo 'puts "BOARDS: [get_board_parts digilentinc.com:arty-a7-100:*]"' > /tmp/b.tcl && vivado -mode batch -nojournal -nolog -source /tmp/b.tcl | grep BOARDS
   ```

2. **Container phải có `--net=host`.** Thiếu nó thì Vivado sập ngay sau `Routing Is Done`
   (`realloc(): invalid old size`, `Abnormal program termination (6)`; stack trong `hs_err_pid*.log` đi qua
   `libXil_lmgr11` → `GetHostInfo` → `libudev udev_enumerate_scan_devices`: bước kiểm license lấy thông tin
   máy). Lỗi lặp lại y hệt khi chạy lại; thêm `--net=host` thì qua. Container của CHIA luôn có `--net=host`,
   nên chỉ cần nhớ khi chạy tay.

Chạy tay (sbt elaborate khoảng 10–20 phút, Vivado khoảng 30–60 phút; dùng chung cache JVM với khối chipyard):

```bash
CY=$(realpath ~/tools/chipyard); XD=$(realpath ~/tools/Xilinx); J=$(realpath ~/chia_cache/jvm); docker run --rm --net=host --shm-size=10.24gb --security-opt label=disable -v $CY:$CY -v $XD:$XD:ro -v $J/coursier:/home/ray/.cache/coursier -v $J/sbt:/home/ray/.sbt -v $J/ivy2:/home/ray/.ivy2 -e "JAVA_TOOL_OPTIONS=-Dsun.net.client.defaultConnectTimeout=30000 -Dsun.net.client.defaultReadTimeout=120000" -w $CY ghcr.io/ucb-bar/chia-chisel-build:latest bash -c "source /home/ray/anaconda3/etc/profile.d/conda.sh && source env.sh && source $XD/2025.1/Vivado/settings64.sh && export PATH=$XD/2025.1/Vivado/bin:\$PATH && cd fpga && make SUB_PROJECT=arty100t bitstream 2>&1 | tee build-arty100t.log | tail -40"
```

Kết quả nằm ở `fpga/generated-src/chipyard.fpga.arty100t.Arty100THarness.RocketArty100TConfig/obj/`
(`Arty100THarness.bit`, `report/timing.txt`, checkpoint `post_synth/post_opt/post_place/post_route.dcp`).
Trên 1.15.0 jar của `fpga/` là `.classpath_cache/chipyard_fpga.jar`, tách khỏi `chipyard.jar` của
`sims/verilator`, nên không còn lỗi `--top-module` như fork 1.10.0. Hàng chục `CRITICAL WARNING` ở
`arty-master.xdc` là chân của file constraint mẫu không được dùng, bỏ qua được.

Qua job CHIA (cách chính; khối vivado đã có `--net=host`, cache JVM và timeout coursier như khối chipyard):

```bash
cd ~/CHIA-workflow/examples && chia job submit --working-dir . -- python fpga_smoke.py --clean
```

`examples/fpga_smoke.py` chạy `make SUB_PROJECT=arty100t bitstream` trên worker `vivado`, `--clean` xoá thư mục
build và `.classpath_cache/chipyard_fpga.jar` để chạy lại từ sbt. Xong khi in `KẾT QUẢ: OK` (make thoát 0, có
`.bit` mới, đọc được WNS/WHS). Log đầy đủ ở `fpga/build-arty100t-<CONFIG>-smoke.log`.

**Đã chạy thật 14/11/2026** (cluster `chia-up-blocks.py --fpga`, job `--clean`): `rc: 0`, 11,1 phút, ra
`Arty100THarness.bit` 3 825 916 byte, WNS 0,129 ns, WHS −0,063 ns (4 endpoint, như lần chạy tay), `KẾT QUẢ: OK`;
`chia down` gỡ sạch 4 container và head.

Nếu Vivado hỏng sau place, chạy tiếp từ checkpoint thay vì làm lại từ đầu (route, bitstream, report):

```bash
CY=$(realpath ~/tools/chipyard); XD=$(realpath ~/tools/Xilinx); B=$CY/fpga/generated-src/chipyard.fpga.arty100t.Arty100THarness.RocketArty100TConfig; S=$CY/fpga/fpga-shells/xilinx/common/tcl; printf 'set top Arty100THarness\nset wrkdir [file join [pwd] obj]\nopen_checkpoint [file join $wrkdir post_place.dcp]\nsource %s/route.tcl\nsource %s/bitstream.tcl\nsource %s/report.tcl\n' $S $S $S > $B/resume-route.tcl; docker run --rm --net=host --shm-size=10.24gb --security-opt label=disable -v $CY:$CY -v $XD:$XD:ro -w $B ghcr.io/ucb-bar/chia-chisel-build:latest bash -c "source $XD/2025.1/Vivado/settings64.sh && export PATH=$XD/2025.1/Vivado/bin:\$PATH && vivado -nojournal -mode batch -source resume-route.tcl 2>&1 | tee resume-route.log | tail -30"
```

## 5. Khối asic (OpenROAD-flow-scripts)

```bash
cd ~/CHIA-workflow && docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker
```

Image cài thêm Ray 2.54.0 và `chialoops` 2.0.1 lên `openroad/orfs`. Ray đòi mọi node cùng phiên
bản Ray và Python với head (3.10), mà `openroad/orfs` mới dùng Python 3.14, nên Ray chạy trong một
Python 3.10 riêng ở `/opt/raypy`; `python3` của image giữ nguyên cho script ORFS.

Rồi chép ORFS ra host để sửa tay (cùng cách với Chipyard):

```bash
cd ~/CHIA-workflow && scripts/setup-orfs.sh --verify
```

Script `docker cp` cả `/OpenROAD-flow-scripts` của image ra `~/tools/OpenROAD-flow-scripts`
(chép từ image chứ không `git clone`, để script flow khớp đúng bản OpenROAD/Yosys đã build), rồi
`git init` thư mục `flow/` để theo dõi thay đổi tay. `--verify` chạy flow mẫu `nangate45/gcd` và
cần thấy `VERIFY_OK`. Khối asic mount thư mục này đè lên `/OpenROAD-flow-scripts` và chạy bằng UID
của host (image ORFS mặc định là root), nên kết quả trong `flow/results` sửa/xoá được trên host.

## Lỗi đã gặp

Các dòng ghi *(1.10)* gặp với fork RISCVTutorial (Chipyard 1.10.0, đã gỡ); 1.15.0 không cần cách sửa đó.

| Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|
| *(chưa gặp, có thể gặp ở 1.15.0)* `import sre_parse` ở bước 1 (conda-lock) | `build-setup.sh` 1.15.0 tạo env conda-lock không ghim Python; conda-forge có thể kéo Python ≥ 3.13 | Thêm `"python<3.13"` vào dòng `conda create -y -p "$CONDA_LOCK_ENV_PATH" ...` trong `scripts/build-setup.sh` của Chipyard, xoá `.conda-lock-env`, `.conda-env` rồi chạy lại (xem `docs/setup-bazzite.md` mục 7) |
| *(1.10)* `conda-lock: command not found` | Chipyard 1.10 dựng env bằng `conda-lock`, image không có | `pip install conda-lock==1.4.0` vào conda base của container |
| *(1.10)* `build-setup.sh` lặp câu hỏi `y/n` mãi | Fork không đứng trên tag release; chạy không có bàn phím | `build-setup.sh -f` |
| *(1.10)* `TutorialStarterConfig` lỗi khi sinh `.fir` | Config tutorial cố ý không có core | Dùng `RocketConfig`, hoặc bỏ comment `WithNBigCores(1)` |
| `CondaError: Run 'conda init'` | `env.sh` gọi `conda activate` khi conda chưa nạp | `source /home/ray/anaconda3/etc/profile.d/conda.sh` trước |
| Worker có `RISCV=None`, `Did not find Verilator in PATH` | Ghi `env.sh` vào `~/.profile` vô ích: shell chạy `ray start` đã đọc `.profile` trước đó | `source env.sh` ngay trong `worker_setup_commands` (chạy cùng shell với `ray start`) |
| Worker có `RISCV=None` dù `cluster.gen.yaml` đúng | CHIA không thay `${CHIPYARD_FORK}` trong `worker_setup_commands` | `chia-up-blocks.py` tự thay biến trước khi ghi file (đã sửa) |
| `Not a conda environment: /var/home/.../.conda-env` | Bazzite: `/home` là symlink tới `/var/home`; Chipyard dựng theo đường dẫn thật nhưng mount theo `/home/...` | `chia-up-blocks.py` dùng `realpath` (đã sửa). Mount tay thì dùng `$(realpath ~/tools/chipyard)` |
| Build `chia-orfs` lỗi `AssertionError: Python (3, 14)` | `openroad/orfs` mới dùng Python 3.14, head dùng 3.10 | Dockerfile cài Python 3.10 riêng ở `/opt/raypy` cho Ray (đã sửa) |
| `chia up --asic`: `Version mismatch ... Python: 3.10.22` | Ray so cả bản vá Python với head (3.10.19) | Dockerfile ghim `python=3.10.19` (đã sửa) |
| Khối vivado in `Vivado v2024.2` dù host là 2025.1 | Image `chia-chisel-build` có sẵn `/usr/local/bin/vivado` | Đặt `Vivado/bin` của host lên đầu PATH (đã sửa) |
| `settings64.sh: ... /var/home/dingus/tools/Xilinx/.../.settings64-Vitis_for_HLS.sh: No such file` | `settings64.sh` ghi cứng đường dẫn thật lúc cài; Xilinx bị mount ở `/tools/Xilinx` | Mount Xilinx đúng đường dẫn thật (`XILINX_DIR`, qua `realpath`) (đã sửa) |
| `chia down`: `Head:` trống, mọi khối báo `scoped ray stop ... could not run (is psutil available ...)`, không container nào bị gỡ | shell không có `THIS_MACHINE`, `head_ip` của `cluster.gen.yaml` thành rỗng | `python scripts/chia-up-blocks.py --down` (tự dò IP), hoặc `THIS_MACHINE=<IP> chia down cluster.gen.yaml -y` |
| `chia down`: `scoped ray stop in 'chia-asic-...' could not run (is psutil available ...)`, container asic không bị xoá | CHIA dừng Ray bằng `python3` của image (`/opt/orfs-python`, 3.14), không có `psutil` | Dockerfile cài `psutil` vào cả hai Python (đã sửa, kiểm chứng 09/11/2026) |
| `docker: command not found` | Gõ `docker` khi đang ở trong container | `exit` ra host |

### sbt/coursier treo khi build (khối chipyard)

- **Triệu chứng:** `ChiselBuildNode` đứng ở `make` hàng chục phút, `java` gần 0% CPU, file `.part` trong
  `~/.cache/coursier` không lớn thêm, trong khi `curl https://repo1.maven.org` vẫn trả 200 ngay.
- **Nguyên nhân:** coursier không có read timeout; một kết nối TCP tới Maven Central bị chết giữa chừng làm nó
  chờ mãi. Trước đây cache coursier nằm trong container (không mount) nên mất sau mỗi `chia up`, mỗi lần build
  đầu phải tải lại hết, dễ dính lỗi này.
- **Gỡ tạm từ host** (container chạy `--net=host`): tìm socket của java rồi cắt nó, coursier sẽ thử lại:
  ```bash
  sudo ss -tnp state established '( dport = :443 )' | grep java
  sudo ss -K state established dst '[::ffff:104.18.19.12]' dport = :443
  ```
  (thay IP bằng IP thấy ở lệnh đầu).
- **Sửa gốc (đã kiểm chứng 13/11/2026: cache coursier 234 MB sau lần build đầu, không treo):** template mount `${JVM_CACHE}` (mặc định `~/chia_cache/jvm`, `chia-up-blocks.py`
  tự tạo) vào `~/.cache/coursier`, `~/.sbt`, `~/.ivy2` của khối chipyard, và đặt `JAVA_TOOL_OPTIONS` với
  connect timeout 30 s, read timeout 120 s. Cần `chia down` rồi chạy lại `chia-up-blocks.py`; lần build đầu
  vẫn tải lại một lần.
