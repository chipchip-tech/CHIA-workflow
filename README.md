# CHIA-workflow

Ghi chú, config và script để chạy [CHIA](https://github.com/ucb-bar/chia) (framework co-design phần cứng/phần mềm dùng agent AI, UC Berkeley) cùng Chipyard trên Bazzite.

Bối cảnh cho session Claude Code mới: [CLAUDE.md](CLAUDE.md).

## Tài liệu

- [docs/setup-bazzite.md](docs/setup-bazzite.md): cài đặt trên Bazzite (Docker qua bazzite-dx, SSH, conda/`chia_env`, image CHIA, Hello World, agent AI, Chipyard native trong distrobox).
- [docs/usage-local.md](docs/usage-local.md): dùng CHIA trên một máy: bật/tắt cluster, chạy job, viết loop (`@ChiaFunction`, resources), LLM và `ChiaTool`, các node có sẵn, cache/bypass, profiling, loop mẫu.
- [docs/usage-remote.md](docs/usage-remote.md): dùng thêm worker trên máy remote: chuẩn bị máy, firewall, SSH, `cluster-lan.yaml`, ghim và rải task qua nhiều máy, Tailscale/`tailnet:`.

## Config

- [configs/cluster-local.yaml](configs/cluster-local.yaml): cluster một máy (head + chipyard + verilator + opencode). Đã chạy thật hết 4 bước Hello World trên Bazzite.
- [configs/cluster-lan.yaml](configs/cluster-lan.yaml): head + một máy remote (chipyard và verilator trên cả hai máy).

## Script

- [scripts/check-bazzite-env.sh](scripts/check-bazzite-env.sh): chạy trên head, kiểm tra máy đã sẵn sàng chạy `chia up` chưa (chỉ đọc).
- [scripts/prepare-remote-worker.sh](scripts/prepare-remote-worker.sh): chạy trên máy remote để cài sshd, Docker, rsync và mở firewall cho head (hỏi trước mỗi bước).
- [scripts/check-remote-worker.sh](scripts/check-remote-worker.sh): chạy trên head, kiểm tra máy remote qua SSH (chỉ đọc).

## Ví dụ

- [examples/rtl_agent_loop.py](examples/rtl_agent_loop.py): loop agent sửa RTL → build → mô phỏng song song → đưa lỗi vào prompt vòng sau.
