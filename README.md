# CHIA-workflow

Ghi chú, config và script để chạy [CHIA](https://github.com/ucb-bar/chia) (framework co-design phần cứng/phần mềm dùng agent AI, UC Berkeley) cùng Chipyard trên Bazzite.

Bối cảnh cho session Claude Code mới: [CLAUDE.md](CLAUDE.md).

## Nhánh

- `main`: bản ổn định, hạn chế đụng.
- `setup`: tài liệu, config, script dùng chung. Mọi sửa đổi chung làm ở đây.
- `project/<tên>`: mỗi project một nhánh, tách từ `setup`, code nằm trong `projects/<tên>/`.
  Hiện có `project/ascon-secureboot`, `project/rocket-fmax`. Mỗi project theo khung pha trong
  [docs/workflow-phases.md](docs/workflow-phases.md).

Chi tiết xem mục "Quy ước nhánh" trong [CLAUDE.md](CLAUDE.md).

## Bắt đầu nhanh

Cài máy theo [docs/setup-bazzite.md](docs/setup-bazzite.md) (mục 1–4), rồi dùng **cluster theo khối**
([docs/cluster-blocks.md](docs/cluster-blocks.md)): đây là cách chạy chính cho mọi project, đã kiểm chứng cả 5 khối
(08–09/11/2026). `configs/cluster-local.yaml` (Chipyard có sẵn trong image) giữ lại cho Hello World.

## Tài liệu

- [docs/setup-bazzite.md](docs/setup-bazzite.md): cài đặt trên Bazzite (Docker qua bazzite-dx, SSH, conda/`chia_env`, image CHIA, Hello World, agent AI, Chipyard native trong distrobox).
- [docs/usage-local.md](docs/usage-local.md): dùng CHIA trên một máy: bật/tắt cluster, chạy job, viết loop (`@ChiaFunction`, resources), LLM và `ChiaTool`, các node có sẵn, cache/bypass, profiling, loop mẫu.
- [docs/usage-remote.md](docs/usage-remote.md): dùng thêm worker trên máy remote: chuẩn bị máy, firewall, SSH, `cluster-lan.yaml`, ghim và rải task qua nhiều máy, Tailscale/`tailnet:`.
- [docs/workflow-phases.md](docs/workflow-phases.md): khung chung cho mọi project: Simulate (bắt buộc) rồi rẽ sang FPGA và/hoặc ASIC; mục tiêu, tiêu chí vào/ra, quy tắc chung của từng pha.
- [docs/cluster-blocks.md](docs/cluster-blocks.md): cluster theo khối bật/tắt được: fork Chipyard mount từ host (RISCVTutorial), Verilator, Claude Code, Vivado (FPGA), OpenROAD-flow-scripts (ASIC); dựng fork, bật cluster, lỗi đã gặp.
- [docs/chipyard-minimal-soc.md](docs/chipyard-minimal-soc.md): SoC nền tối giản `MinimalSoCConfig` (1 Rocket, L2 128 KiB) cho mọi project: cài vào fork, build, kiểm tra, kế thừa. Spec: [docs/minimal-soc-config-spec.md](docs/minimal-soc-config-spec.md).
- [docs/prompt-template.md](docs/prompt-template.md): template prompt để mở session Claude Code mới cho một project, kèm bước phỏng vấn để dựng prompt.

## Config

- [configs/cluster-local.yaml](configs/cluster-local.yaml): cluster một máy (head + chipyard + verilator + opencode). Đã chạy thật hết 4 bước Hello World trên Bazzite.
- [configs/cluster-blocks.template.yaml](configs/cluster-blocks.template.yaml): template cluster theo khối (chipyard, verilator, claude bật; vivado, asic tắt). **Cách chạy chính.** Không `chia up` thẳng, dùng `scripts/chia-up-blocks.py`.
- [configs/cluster-lan.yaml](configs/cluster-lan.yaml): head + một máy remote (chipyard và verilator trên cả hai máy).

## Script

- [scripts/check-bazzite-env.sh](scripts/check-bazzite-env.sh): chạy trên head, kiểm tra máy đã sẵn sàng chạy `chia up` chưa (chỉ đọc).
- [scripts/prepare-remote-worker.sh](scripts/prepare-remote-worker.sh): chạy trên máy remote để cài sshd, Docker, rsync và mở firewall cho head (hỏi trước mỗi bước).
- [scripts/check-remote-worker.sh](scripts/check-remote-worker.sh): chạy trên head, kiểm tra máy remote qua SSH (chỉ đọc).
- [scripts/setup-chipyard-fork.sh](scripts/setup-chipyard-fork.sh): clone (tuỳ chọn) và dựng một fork Chipyard trong container `chia-chisel-build`; `--verify` build và chạy thử RocketConfig.
- [scripts/setup-orfs.sh](scripts/setup-orfs.sh): chép OpenROAD-flow-scripts từ image `chia-orfs` ra `~/tools` để sửa tay; `--verify` chạy flow mẫu nangate45/gcd.
- [scripts/chia-up-blocks.py](scripts/chia-up-blocks.py): sinh `cluster.gen.yaml` từ template theo khối (`--fpga`, `--asic`, `--no-claude`) và `chia up`.

- [scripts/install-chipyard-config.sh](scripts/install-chipyard-config.sh): copy `chipyard/*.scala` (Config dùng chung, ví dụ `MinimalSoCConfig`) vào fork Chipyard, in `diff` và `sha256sum`, kiểm tra mixin.

## Chipyard

- [chipyard/MinimalSoCConfig.scala](chipyard/MinimalSoCConfig.scala): SoC nền tối giản cho mọi project.

## Docker

- [docker/orfs-ray.Dockerfile](docker/orfs-ray.Dockerfile): image `chia-orfs` cho khối asic (OpenROAD-flow-scripts + Ray).

## Ví dụ

- [examples/rtl_agent_loop.py](examples/rtl_agent_loop.py): loop agent sửa RTL → build → mô phỏng song song → đưa lỗi vào prompt vòng sau.
- [examples/blocks_smoke.py](examples/blocks_smoke.py): kiểm tra các khối đang bật của cluster theo khối (môi trường fork, build Verilator, `vivado -version`, `openroad -version`).
