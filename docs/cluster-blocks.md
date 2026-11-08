# Cluster theo khối: fork Chipyard + Verilator + Vivado + ASIC

Cluster CHIA một máy gồm các khối bật/tắt được. Dùng chung cho mọi project từ nay về sau.
Mọi công cụ nằm trong `~/tools` trên host và được mount vào container, nên sửa tay bằng editor được:

```
~/tools/
├── RISCVTutorial/            fork Chipyard (khối chipyard, vivado); toolchain + build nằm trong đây
├── OpenROAD-flow-scripts/    ORFS chép từ image chia-orfs (khối asic)
└── Xilinx/2025.1/            Vivado (khối vivado, mount chỉ đọc)
```

| Khối | Resource | Image | Mặc định | Trạng thái |
|---|---|---|---|---|
| `chipyard` | `chipyard` | `chia-chisel-build` + mount fork | bật | ✅ cài lại từ đầu, `blocks_smoke.py --clean` OK (08/11/2026) |
| `verilator` | `verilator_run` | `chia-verilator-run` | bật | ✅ |
| `claude` | `claude_creds` | `chia-claude-code` | bật (`--no-claude` để tắt) | ✅ (project rocket-fmax) |
| `vivado` | `vivado` | `chia-chisel-build` + mount fork + `~/tools/Xilinx` | tắt (`--fpga`) | ❌ chưa chạy thử |
| `asic` | `asic` | `chia-orfs` tự build + mount `~/tools/OpenROAD-flow-scripts` | tắt (`--asic`) | ⚠️ image + `setup-orfs.sh --verify` OK; chưa thử qua CHIA |

File liên quan:

- `configs/cluster-blocks.template.yaml`: template, không `chia up` thẳng.
- `scripts/setup-chipyard-fork.sh`: dựng fork trong container.
- `scripts/chia-up-blocks.py`: sinh `cluster.gen.yaml` theo khối và `chia up`.
- `docker/orfs-ray.Dockerfile`: image cho khối asic.
- `scripts/setup-orfs.sh`: chép ORFS từ image ra `~/tools` để sửa tay.
- `examples/blocks_smoke.py`: kiểm tra các khối đang bật.

## 1. Dựng fork Chipyard

Ví dụ với [RISCVTutorial](https://github.com/uec-riscv-team/RISCVTutorial) (Chipyard 1.10.0). Chạy trong `tmux`, mất 30–90 phút:

```bash
cd ~/CHIA-workflow && scripts/setup-chipyard-fork.sh --clone https://github.com/uec-riscv-team/RISCVTutorial.git --verify ~/tools/RISCVTutorial
```

Script chạy `build-setup.sh` trong một container `chia-chisel-build` tạm (`--rm`), ghi
`.conda-env/`, `env.sh`, cache sbt vào thư mục fork. Thành công khi thấy `Setup complete!`
và (với `--verify`) `VERIFY_OK`. Log: `<fork>/setup.log`, `<fork>/sims/verilator/build-verify.log`.

Fork phải mount **đúng cùng đường dẫn** trên host và trong container: `env.sh` và conda env
ghi đường dẫn tuyệt đối. UID của user trên host (1000) trùng UID `ray` trong image nên không
cần `--user`.

### Làm việc tay với fork

Sửa mã trên host. Build/chạy trong một container dùng lâu dài:

```bash
docker run -dit --name chipyard-dev --shm-size=10.24gb -v $(realpath ~/tools/RISCVTutorial):$(realpath ~/tools/RISCVTutorial):z -w $(realpath ~/tools/RISCVTutorial) ghcr.io/ucb-bar/chia-chisel-build:latest bash
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

Thêm `--fpga`, `--asic`, `--no-claude` tuỳ nhu cầu; `--dry-run` để chỉ xem config. Fork khác
mặc định (`~/tools/RISCVTutorial`) thì đặt `CHIPYARD_FORK=/đường/dẫn/tuyệt/đối`; ORFS khác chỗ thì `ORFS_DIR`. Script kiểm
trước: fork có `env.sh`, Vivado có `settings64.sh`, image `chia-orfs` đã build, Claude đã đăng nhập.

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

Cần thấy `[chipyard] env` có `RISCV=<fork>/.conda-env/riscv-tools`, `SUCCESS=True`, và
`KẾT QUẢ: OK`. Thêm `--clean` để build lại từ đầu (xoá cả `chipyard.jar`).

## 4. Khối vivado (FPGA)

Vivado cài trên host ở `~/tools/Xilinx/<phiên bản>/Vivado` (bố cục từ 2025.1), mount chỉ đọc
vào `/tools/Xilinx`. Phiên bản lấy từ `VIVADO_VERSION` (mặc định `2025.1`). Khối dùng chung fork
với khối chipyard để chạy flow `fpga/` của Chipyard.

## 5. Khối asic (OpenROAD-flow-scripts)

```bash
cd ~/CHIA-workflow && docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker
```

Image cài thêm Ray 2.54.0 và `chialoops` 2.0.1 lên `openroad/orfs`. Ray đòi mọi node cùng phiên
bản Ray và Python với head (3.10), mà `openroad/orfs` mới dùng Python 3.14, nên Ray chạy trong một
Python 3.10 riêng ở `/opt/raypy`; `python3` của image giữ nguyên cho script ORFS.

Rồi chép ORFS ra host để sửa tay (cùng cách với fork Chipyard):

```bash
cd ~/CHIA-workflow && scripts/setup-orfs.sh --verify
```

Script `docker cp` cả `/OpenROAD-flow-scripts` của image ra `~/tools/OpenROAD-flow-scripts`
(chép từ image chứ không `git clone`, để script flow khớp đúng bản OpenROAD/Yosys đã build), rồi
`git init` thư mục `flow/` để theo dõi thay đổi tay. `--verify` chạy flow mẫu `nangate45/gcd` và
cần thấy `VERIFY_OK`. Khối asic mount thư mục này đè lên `/OpenROAD-flow-scripts` và chạy bằng UID
của host (image ORFS mặc định là root), nên kết quả trong `flow/results` sửa/xoá được trên host.

## Lỗi đã gặp

| Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|
| `conda-lock: command not found` | Chipyard 1.10 dựng env bằng `conda-lock`, image không có | `pip install conda-lock==1.4.0` vào conda base của container |
| `build-setup.sh` lặp câu hỏi `y/n` mãi | Fork không đứng trên tag release; chạy không có bàn phím | `build-setup.sh -f` |
| `TutorialStarterConfig` lỗi khi sinh `.fir` | Config tutorial cố ý không có core | Dùng `RocketConfig`, hoặc bỏ comment `WithNBigCores(1)` |
| `CondaError: Run 'conda init'` | `env.sh` gọi `conda activate` khi conda chưa nạp | `source /home/ray/anaconda3/etc/profile.d/conda.sh` trước |
| Worker có `RISCV=None`, `Did not find Verilator in PATH` | Ghi `env.sh` vào `~/.profile` vô ích: shell chạy `ray start` đã đọc `.profile` trước đó | `source env.sh` ngay trong `worker_setup_commands` (chạy cùng shell với `ray start`) |
| Worker có `RISCV=None` dù `cluster.gen.yaml` đúng | CHIA không thay `${CHIPYARD_FORK}` trong `worker_setup_commands` | `chia-up-blocks.py` tự thay biến trước khi ghi file (đã sửa) |
| `Not a conda environment: /var/home/.../.conda-env` | Bazzite: `/home` là symlink tới `/var/home`; fork dựng theo đường dẫn thật nhưng mount theo `/home/...` | `chia-up-blocks.py` dùng `realpath` (đã sửa). Mount tay thì dùng `$(realpath ~/tools/RISCVTutorial)` |
| Build `chia-orfs` lỗi `AssertionError: Python (3, 14)` | `openroad/orfs` mới dùng Python 3.14, head dùng 3.10 | Dockerfile cài Python 3.10 riêng ở `/opt/raypy` cho Ray (đã sửa) |
| `docker: command not found` | Gõ `docker` khi đang ở trong container | `exit` ra host |
