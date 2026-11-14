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
| `chipyard` | `chipyard` | `chia-chisel-build` + mount Chipyard | bật | ⏳ chưa kiểm chứng trên 1.15.0 (fork 1.10.0: `blocks_smoke.py --clean` OK, 08/11/2026) |
| `verilator` | `verilator_run` | `chia-verilator-run` | bật | ✅ |
| `claude` | `claude_creds` | `chia-claude-code` | bật (`--no-claude` để tắt) | ✅ (project rocket-fmax) |
| `vivado` | `vivado` | `chia-chisel-build` + mount Chipyard + `~/tools/Xilinx` | tắt (`--fpga`) | ⏳ chưa kiểm chứng trên 1.15.0; fork 1.10.0: `vivado v2025.1` của host chạy trong worker (09/11/2026); flow `fpga/` chưa thử |
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

Thêm `--fpga`, `--asic`, `--no-claude` tuỳ nhu cầu; `--dry-run` để chỉ xem config. Chipyard khác chỗ
mặc định (`~/tools/chipyard`) thì đặt `CHIPYARD_FORK=/đường/dẫn/tuyệt/đối`; ORFS khác chỗ thì `ORFS_DIR`. Script kiểm
trước: Chipyard có `env.sh`, Vivado có `settings64.sh`, image `chia-orfs` đã build, Claude đã đăng nhập.

Tắt cluster:

```bash
chia down cluster.gen.yaml -y
```

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
