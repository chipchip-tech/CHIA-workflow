# Cài đặt CHIA + Chipyard trên Bazzite

Hướng dẫn này dựa trên tài liệu chính thức của CHIA
([repo ucb-bar/chia](https://github.com/ucb-bar/chia), thư mục `docs/`, cũng là
nội dung của <https://docs.chialoops.ai/en/latest/>), bài báo
[arXiv:2606.27350](https://arxiv.org/abs/2606.27350) và
[Chipyard](https://github.com/ucb-bar/chipyard). Phần riêng cho Bazzite được
viết lại vì Bazzite là hệ điều hành **immutable** (Fedora Atomic): không dùng
`dnf install` như Fedora thường, `/usr` là read-only, còn `$HOME` (`/var/home/<user>`)
và `/var` thì ghi được.

## 0. Cần hiểu trước: CHIA chạy tool ở đâu?

Một CHIA workflow gồm hai phần:

- **Loop**: chương trình Python (`@ChiaFunction`, `get()`, ...) chạy trên máy **head**.
- **Cluster**: mô tả trong `cluster.yaml`. Mỗi worker là một **Docker container**
  dựng từ image có sẵn của CHIA (`ghcr.io/ucb-bar/chia-*`), nối vào Ray head.

Chipyard, Verilator, RISC-V toolchain, OpenCode, Claude Code, ... đều đã nằm
**sẵn trong các image đó**. Vì vậy, để chạy CHIA, máy Bazzite chỉ cần có:

| Thành phần | Vai trò | Ghi chú cho Bazzite |
|---|---|---|
| Docker (lệnh `docker`) | CHIA gọi `docker pull/run/exec` qua SSH | Bazzite thường chỉ có Podman. Nên chuyển sang **bazzite-dx** (có docker-ce) |
| SSH server + key | `chia up` SSH vào chính `head_ip` (cả khi chỉ có 1 máy) | `sshd` mặc định tắt |
| conda + Python **3.10.19** | Môi trường `chia_env` chứa `chialoops` và Ray 2.54.0 | Cài Miniforge vào `$HOME` |
| `rsync`, `git` | `chia job submit` rsync thư mục làm việc lên cluster | Có sẵn trên Bazzite |

**Không cần cài Chipyard trên máy thật để chạy CHIA.** Chỉ cài Chipyard native
(mục 7) khi bạn muốn tự sửa hoặc build RTL bên ngoài CHIA.

Cấu hình nên có: CPU x86_64, **≥ 32 GB RAM** (build Verilator cho RocketConfig
khá tốn RAM), **≥ 150 GB trống** ở `/var` (image `chia-chisel-build` rất lớn,
docs đặt `pull_timeout: 7200`).

---

## 1. Chuyển sang bazzite-dx để có Docker (khuyến nghị)

Mã nguồn CHIA (`chia/cluster/docker.py`, `config.py`) luôn gọi lệnh `docker`
(`engine` cố định là `"docker"`). Các ví dụ cũng giả định Docker rootful, ví dụ
`--user $(id -u):$(id -g)` kèm `-v ~/.claude:/home/ray/.claude`. Image
[bazzite-dx](https://github.com/ublue-os/bazzite-dx) cài sẵn `docker-ce`,
`docker-buildx-plugin`, `docker-compose-plugin` và bật `docker.socket`.

Chọn đúng lệnh theo biến thể đang dùng. **Đừng** đổi giữa KDE và GNOME bằng rebase:

```bash
# KDE (mặc định)
brh rebase bazzite-dx:stable
# GNOME
brh rebase bazzite-dx-gnome:stable
# KDE + NVIDIA
brh rebase bazzite-dx-nvidia:stable
# GNOME + NVIDIA
brh rebase bazzite-dx-nvidia-gnome:stable

# Nếu máy chưa có lệnh brh:
# rpm-ostree rebase ostree-image-signed:docker://ghcr.io/ublue-os/bazzite-dx:stable
systemctl reboot
```

Sau khi reboot, thêm user vào group `docker`, rồi reboot hoặc đăng xuất và đăng nhập lại:

```bash
ujust dx-group
systemctl reboot
```

Kiểm tra:

```bash
docker run --rm hello-world
docker info | grep -i "storage driver\|root dir"
```

> Muốn quay lại bản cũ: `rpm-ostree rollback`, hoặc chọn deployment cũ ở menu boot.

### 1b. Phương án thay thế: giữ Bazzite gốc, dùng Podman rootless (thử nghiệm)

CHIA **không chính thức** hỗ trợ Podman. Bạn có thể giả lệnh `docker` bằng một
script, nhưng phải tự sửa một số option trong `cluster.yaml`:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/docker <<'EOF'
#!/usr/bin/env bash
exec podman "$@"
EOF
chmod +x ~/.local/bin/docker
# CHIA chạy lệnh qua `bash --login -c`, nên ~/.local/bin phải có trong PATH của login shell
grep -q '.local/bin' ~/.bash_profile || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bash_profile
```

Khi dùng Podman rootless:

- Thay `--user $(id -u):$(id -g)` bằng `--userns=keep-id`, nếu không file mount vào sẽ sai quyền.
- Thêm `:z` vào mọi bind mount vì Bazzite bật SELinux enforcing, ví dụ
  `-v ~/.claude:/home/ray/.claude:z`.
- Không dùng được `--dangerously-skip-permissions` của Claude Code khi container
  chạy như root. Ví dụ `examples/esp_accel_loop` có ghi chú về trường hợp này.

Nếu gặp lỗi khó hiểu, quay lại phương án 1 (bazzite-dx).

---

## 2. Bật SSH server và tạo key

`chia up` SSH (kèm `-A`, agent forwarding) tới `head_ip`, kể cả khi đó là chính máy bạn.

```bash
sudo systemctl enable --now sshd

# Tạo key nếu chưa có
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys

# Nạp key vào ssh-agent (CHIA dùng agent nếu không khai báo ssh_private_key)
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# Lấy IP của máy. Fedora có `hostname -I`; nếu không chạy được thì dùng dòng thứ hai
export THIS_MACHINE=$(hostname -I | awk '{print $1}')
# export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')

# Kiểm tra: SSH không mật khẩu, và login shell thấy được docker
ssh $USER@$THIS_MACHINE 'bash --login -c "command -v docker && docker ps"'
```

Cluster **nhiều máy**: mở port trên firewalld của từng máy, gồm Ray GCS `6379`,
dashboard `8265`, MCP tool server từ `8000` trở lên, và các port động của Ray.
Cách đơn giản nhất trong LAN tin cậy là thêm IP các máy vào zone `trusted`:

```bash
sudo firewall-cmd --permanent --zone=trusted --add-source=192.168.1.0/24
sudo firewall-cmd --reload
```

---

## 3. Cài conda (Miniforge) và môi trường `chia_env`

Trên Bazzite, cài conda vào `$HOME`, **không** cài vào `/usr` và **không** cài
trong distrobox. Lý do: `chia up` SSH vào máy thật nên phải thấy được `conda`.

```bash
cd ~
curl -LO "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
bash Miniforge3-Linux-x86_64.sh -b -p ~/miniforge3
~/miniforge3/bin/conda init bash
exec bash   # nạp lại shell

conda create -n chia_env python=3.10.19 -y
conda activate chia_env
pip install chialoops                      # bản ổn định từ PyPI
# pip install "chialoops[metrics]"         # thêm tensorboard/wandb nếu cần
chia --help
```

Muốn phát triển chính CHIA (bản editable):

```bash
git clone --recurse-submodules https://github.com/ucb-bar/chia.git ~/work/chia
pip install -e ~/work/chia
```

Phiên bản phải khớp: worker trong Docker dùng **Python 3.10.x và Ray 2.54.0**.
Nếu head lệch minor version, worker sẽ không join được cluster và cũng không báo lỗi.

### Sửa `~/.bashrc` cho shell non-interactive

CHIA chạy lệnh qua `bash --login -c` và `source ~/.bashrc`. Kiểm tra `~/.bashrc`
xem có đoạn sau không. Nếu có thì **xoá** nó, hoặc chuyển khối `conda initialize`
lên **trước** đoạn đó:

```bash
# If not running interactively, don't do anything
case $- in
     *i*) ;;
       *) return;;
esac
```

`.bashrc` mặc định của Fedora/Bazzite thường không có đoạn này, nhưng nên kiểm tra:

```bash
grep -n 'case \$- in' ~/.bashrc || echo "OK, không có"
ssh $USER@$THIS_MACHINE 'bash --login -c "source ~/.bashrc && conda activate chia_env && python -V && ray --version"'
# Kết quả mong đợi: Python 3.10.19 / ray, version 2.54.0
```

---

## 4. Pull trước các image cần dùng

`chia up` sẽ tự pull, nhưng image rất lớn nên pull trước sẽ dễ theo dõi hơn:

```bash
docker pull ghcr.io/ucb-bar/chia-chisel-build:latest   # Chipyard + RISC-V toolchain + Verilator (chia_artifact branch)
docker pull ghcr.io/ucb-bar/chia-verilator-run:latest  # chạy simulator Verilator
docker pull ghcr.io/ucb-bar/chia-opencode:latest       # agent OpenCode (có model miễn phí)
# Tuỳ case study:
# ghcr.io/ucb-bar/chia-claude-code  chia-circt  chia-gem5  chia-champsim
# ghcr.io/ucb-bar/chia-cacti  chia-riscv-cross  chia-firesim-build  chia-esp ...
```

---

## 5. Chạy thử Hello World (theo Quickstart)

Dùng `configs/cluster-local.yaml` trong repo này thay cho `cluster.yaml` và
`cluster-opencode.yaml` của ví dụ gốc. File này đã có đủ 3 worker (chipyard,
verilator, opencode) và hai bản sửa riêng cho Bazzite:

- **Ray head chạy trong `systemd-run --user --scope`.** systemd-logind tắt mọi tiến
  trình của một phiên SSH khi phiên đó đóng. Vì vậy, nếu không có bản sửa này, Ray head
  do `chia up` bật qua SSH chết ngay. CHIA vẫn in `Head node ready`, nhưng worker báo
  `Failed to connect to GCS at ...:6379`. Bản sửa cần chạy `loginctl enable-linger`
  **một lần**. Cách khác là tạo file `/etc/systemd/logind.conf.d/99-chia.conf` chứa
  `[Login]` và `KillUserProcesses=no`, rồi reboot.
- **`compatible_ips: ["${THIS_MACHINE}"]` có dấu nháy.** Ví dụ gốc
  `cluster-opencode.yaml` thiếu dấu nháy nên YAML báo lỗi
  `expected ',' or ']', but got '{'`.

```bash
loginctl enable-linger $USER                                  # một lần
git clone -b claude/zen-pasteur-gygy0m https://github.com/chipchip-tech/CHIA-workflow.git ~/CHIA-workflow
cp -r ~/work/chia/examples/hello-world ~/chia-hello-world
cd ~/chia-hello-world
conda activate chia_env
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')

chia up ~/CHIA-workflow/configs/cluster-local.yaml -y
ray status                                                    # 4 node: head + 3 worker
chia job submit --working-dir . -- python hello-world-s1.py   # Hello World (#1)
chia job submit --working-dir . -- python hello-world-s2.py   # build RocketConfig + Verilator: (#2)
chia job submit --working-dir . -- python hello-world-s3.py   # OpenCode trả lời: (#3)
chia job submit --working-dir . -- python hello-world-s4.py   # LLM sửa RTL qua BashTool: (#4)

chia down ~/CHIA-workflow/configs/cluster-local.yaml -y       # tắt cluster
```

Ray dashboard: <http://127.0.0.1:8265>. OpenCode dùng model miễn phí
`opencode/big-pickle`, không cần API key.

Ở step 4, log có thể hiện `Timeout on attempt 1/3`. Lý do là agent tự build thử và vượt
giới hạn 600 giây mỗi lượt. Lượt sau thấy message đã có sẵn nên thêm chữ "again". Đây
là hành vi đúng theo prompt của tutorial.

Bước tiếp theo:
- Viết loop của riêng bạn: [chia-usage.md](chia-usage.md).
- Thêm máy khác làm worker: [remote-worker.md](remote-worker.md).

---

## 6. (Tuỳ chọn) Agent AI: Claude Code, Codex, Gemini/Antigravity

Mỗi agent chạy trong container riêng (`chia-claude-code`, `chia-opencode`,
`chia-antigravity`, ...). Credentials **nằm trên host** và được mount vào container.

**Claude Code** (theo `examples/circt_issue_solver/cluster.yaml`):

```bash
curl -fsSL https://claude.ai/install.sh | bash   # cài vào ~/.local/bin, chạy được trên hệ immutable
claude                                           # đăng nhập một lần để tạo ~/.claude
cp ~/.claude.json ~/.claude/.claude.json         # ví dụ CHIA copy file này vào container
```

```yaml
docker:
    image: "ghcr.io/ucb-bar/chia-claude-code:latest"
    run_options:
        - "--user $(id -u):$(id -g)"
        - "-v ~/.claude:/home/ray/.claude"     # Podman rootless: thêm :z và đổi --user thành --userns=keep-id
        - "-v $SSH_AUTH_SOCK:/ssh-agent"
        - "-e SSH_AUTH_SOCK=/ssh-agent"
    run_setup_commands:
        - echo "user:x:$(id -u):$(id -g)::/home/ray:/bin/bash" >> /etc/passwd
        - cp /home/ray/.claude/.claude.json /home/ray/.claude.json
```

**Gemini / Vertex**: cài `gcloud` vào `$HOME` bằng
`curl https://sdk.cloud.google.com | bash`, rồi chạy
`gcloud auth application-default login`. Sau đó mount `~/.config/gcloud` hoặc
`~/.gemini` vào container theo `docs/user_guides/google_auth.rst`.

**GitHub** (case study giải issue CIRCT): `export GITHUB_TOKEN=...`.

**GPU** (`chia-vllm`, `chia-ollama`): nên dùng biến thể `bazzite-dx-nvidia`, rồi
kiểm tra bằng `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`.

---

## 7. (Tuỳ chọn) Cài Chipyard native trong distrobox

Chipyard chính thức được test trên Ubuntu/x86 Linux và cần nhiều gói `-dev` mà
Bazzite không cho cài trực tiếp. Hãy dùng **distrobox** với **Ubuntu 22.04**,
trùng với base image của CHIA. Đặt `--home` riêng để conda của Chipyard không
lẫn với `~/miniforge3` trên host:

```bash
distrobox create --name chipyard --image ubuntu:22.04 --home ~/distrobox/chipyard
distrobox enter chipyard
```

Trong box:

```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    build-essential git openssh-client autoconf automake autotools-dev curl wget \
    device-tree-compiler libmpc-dev libmpfr-dev libgmp-dev libusb-1.0-0-dev \
    libboost-regex-dev libboost-system-dev texinfo gperf patchutils bc zlib1g-dev \
    libexpat1-dev libtool pkg-config ca-certificates git-lfs

curl -LO "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
bash Miniforge3-Linux-x86_64.sh -b -p ~/miniforge3
~/miniforge3/bin/conda init bash && exec bash

# Dùng `chia_artifact` để giống image của CHIA, hoặc `main` để lấy bản Chipyard mới nhất
git clone -b chia_artifact https://github.com/ucb-bar/chipyard.git ~/chipyard
cd ~/chipyard
# Cài đầy đủ (lâu, tốn vài chục GB). Bỏ FireSim/FireMarshal cho nhanh: thêm -s 6 -s 7 -s 8 -s 9
./build-setup.sh riscv-tools -s 6 -s 7 -s 8 -s 9

conda deactivate        # Chipyard yêu cầu tắt base env trên distro kiểu RHEL/Fedora
source ./env.sh

# Kiểm tra: build và chạy RocketConfig trên Verilator
cd sims/verilator
make CONFIG=RocketConfig -j$(nproc)
make CONFIG=RocketConfig run-binary BINARY=$RISCV/riscv64-unknown-elf/share/riscv-tests/isa/rv64ui-p-simple
```

Lỗi thường gặp khi chạy `build-setup.sh`. Image `ChipyardDockerfile` của CHIA
đã có sẵn bản vá cho cả hai:

- **Lỗi `import sre_parse`** ở bước conda-lock: conda-forge kéo Python ≥ 3.13
  vào env conda-lock. Sửa `scripts/build-setup.sh`, thêm `"python<3.13"` vào
  dòng `conda create -y -p $CONDA_LOCK_ENV_PATH -c conda-forge conda-lock=...`.
- **Lỗi `sysroot_linux-64`** trên nhánh `main`: script tự đổi sysroot theo glibc
  2.35 của Ubuntu 22.04, nhưng conda-forge chỉ có 2.34 và 2.39. Ép giá trị
  `SYS_GLIBC` về `2.34`, hoặc dùng nhánh `chia_artifact`.
- Xung đột conda: chạy `conda update -n base --all` rồi chạy lại.

---

## 8. Checklist nhanh

Chạy `scripts/check-bazzite-env.sh` trong repo này. Script kiểm tra docker, linger,
sshd, SSH không mật khẩu, conda, `chia_env`, phiên bản Python/Ray, `rsync` và dung lượng `/var`.

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `'docker' not found in login-shell PATH` | Chưa dùng bazzite-dx, hoặc shim `~/.local/bin/docker` chưa có trong PATH của `bash --login` |
| `permission denied ... docker.sock` | Chưa chạy `ujust dx-group`, hoặc chưa đăng nhập lại |
| `Head node ready` nhưng worker báo `Failed to connect to GCS` | logind tắt Ray head khi phiên SSH đóng. Xem mục 5 (`enable-linger` + `systemd-run --user --scope`, đã có trong `configs/cluster-local.yaml`) |
| Worker không join cluster | Python/Ray trên head lệch với container (cần 3.10.x / 2.54.0) |
| `chia up` hỏi mật khẩu hoặc treo ở SSH | Chưa thêm key vào `authorized_keys`, `ssh-agent` chưa có key, hoặc `sshd` chưa bật |
| `Permission denied` trong file mount (`-v`) | SELinux: thêm `:z`. Podman: dùng `--userns=keep-id` |
| Pull image timeout | Tăng `pull_timeout`, hoặc `docker pull` trước (mục 4) |
| Port 8000/6379/8265 bị chiếm | Mỗi máy chỉ chạy được **một** head. Tắt cluster cũ bằng `chia down <yaml>` |
