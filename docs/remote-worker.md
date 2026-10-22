# Thêm máy remote làm worker cho CHIA

Hướng dẫn này giả định máy **head** (Bazzite) đã chạy được cluster một máy theo
[setup-bazzite.md](setup-bazzite.md), dùng `configs/cluster-local.yaml`. Ta sẽ thêm
một máy khác, gọi là **remote**, làm worker.

> Các template và script ở đây được viết từ tài liệu và mã nguồn CHIA
> (`docs/user_guides/cluster_config_reference.rst`, `chia/cluster/*.py`). Phần cluster
> một máy đã chạy thật trên Bazzite. Phần nhiều máy **chưa được chạy thử**.

## 0. Cách hoạt động

```
HEAD (Bazzite, 192.168.1.3)                    REMOTE (192.168.1.20)
 ├─ conda chia_env + Ray head (:6379)            ├─ sshd
 ├─ driver: chia job submit ...                  ├─ Docker
 ├─ container chipyard  {chipyard, cy_head}      ├─ container chipyard   {chipyard, cy_remote}
 ├─ container verilator {verilator_run}          ├─ container verilator-0 {verilator_run}
 └─ container opencode  {opencode_creds}         └─ container verilator-1 {verilator_run}
          │  ▲                                             │  ▲
          │  └──────── Ray: GCS, object transfer, ─────────┘  │
          └───────────  tool server MCP (:8000+)  ────────────┘
```

- `chia up` chạy trên head. Nó **SSH sang remote**, kéo image, tạo container
  (`--net=host`), rồi chạy `ray start --address=<HEAD_IP>:6379` bên trong container.
- **Máy remote không cần conda hay CHIA.** Chỉ cần sshd, Docker (dùng được không cần
  `sudo`), `rsync`, và mạng thông hai chiều với head.
- Driver (loop Python) vẫn chạy trên head. Ray tự gửi task sang remote dựa trên
  `resources`, và tự chuyển dữ liệu (ELF, simulator, log) giữa hai máy.
- Lỗi logind trên Bazzite (Ray bị tắt khi SSH đóng) **không ảnh hưởng worker**: worker
  chạy trong container qua `docker exec`, không thuộc phiên SSH. Chỉ Ray head cần `systemd-run`.

Chọn kiểu kết nối:

| Trường hợp | Cách làm |
|---|---|
| Hai máy cùng LAN (cùng router, cùng mạng lab) | Mục 1–5 bên dưới, dùng IP LAN |
| Hai máy khác mạng (nhà ↔ lab, có NAT) | Cài Tailscale ở cả hai máy (mục 6), rồi làm như LAN với IP `100.x.y.z` |
| Không có quyền root trên máy remote | Chế độ `tailnet:` có sẵn của CHIA (mục 7) |

---

## 1. Chuẩn bị máy remote

Chép `scripts/prepare-remote-worker.sh` sang máy remote rồi chạy. Script hỏi trước mỗi
bước và hỗ trợ Ubuntu/Debian, Fedora, Fedora Atomic (Bazzite):

```bash
# trên HEAD
scp scripts/prepare-remote-worker.sh <user>@192.168.1.20:
# trên REMOTE
bash prepare-remote-worker.sh 192.168.1.3        # IP của head
```

Script làm 5 việc. Bạn cũng có thể tự làm bằng tay:

1. Bật sshd: `sudo systemctl enable --now ssh` (Ubuntu) hoặc `sshd` (Fedora/Bazzite).
2. Cài Docker và thêm user vào group `docker`, rồi **đăng xuất và đăng nhập lại**.
   - Ubuntu: `sudo apt install docker.io && sudo usermod -aG docker $USER`.
   - Bazzite: rebase sang `bazzite-dx`, rồi `ujust dx-group` (giống bước 1 của setup-bazzite).
3. Cài `rsync` nếu chưa có.
4. Mở firewall cho IP của head (xem mục 2).
5. Pull trước `chia-chisel-build` và `chia-verilator-run`. Không bắt buộc, nhưng image rất lớn.

Phần cứng gợi ý cho remote: x86_64, ≥ 16 GB RAM cho mỗi worker Chipyard, ≥ 120 GB ổ trống.

## 2. Mạng và firewall (cả hai chiều)

Ray **không chỉ** dùng cổng 6379:
- Remote mở kết nối tới head: GCS `6379`, cùng các cổng ngẫu nhiên của raylet, object
  manager và worker (`10002–19999`).
- Head cũng mở kết nối ngược lại tới các cổng ngẫu nhiên trên remote.
- Tool server MCP (ví dụ `BashTool`) nghe ở cổng `8000+` trên máy chạy nó. Agent ở máy
  kia phải kết nối được tới cổng đó.

Cách đơn giản và an toàn trong mạng tin cậy: **mỗi máy tin tưởng IP của máy kia**.

```bash
# trên HEAD (Bazzite, firewalld)
sudo firewall-cmd --permanent --zone=trusted --add-source=192.168.1.20
sudo firewall-cmd --reload

# trên REMOTE: firewalld như trên với IP head, hoặc ufw:
sudo ufw allow from 192.168.1.3
```

> ⚠️ Ray không có đăng nhập: ai kết nối được tới các cổng Ray đều chạy được code trên
> cluster. **Không** mở các cổng này ra Internet, chỉ cho phép IP của máy trong cluster.

## 3. SSH không mật khẩu từ head sang remote

```bash
# trên HEAD
ssh-copy-id <user>@192.168.1.20
eval "$(ssh-agent -s)"; ssh-add            # CHIA dùng ssh -A
ssh <user>@192.168.1.20 'bash --login -c "docker ps"'   # không hỏi mật khẩu, không lỗi quyền
```

Nếu user trên remote khác user trên head, khai báo trong `auth.overrides` (template
`configs/cluster-lan.yaml` đã có sẵn, lấy từ biến `REMOTE_USER`).

## 4. Kiểm tra

```bash
# trên HEAD, trong repo này
bash scripts/check-remote-worker.sh <user>@192.168.1.20 192.168.1.3
```

Script kiểm tra: SSH, kiến trúc CPU, Docker không cần sudo, rsync, RAM và ổ đĩa, và (khi
cluster đang chạy) remote có tới được `HEAD:6379` không. Mọi mục `[FAIL]` phải sửa trước
khi `chia up`.

## 5. Bật cluster hai máy

```bash
conda activate chia_env
cd ~/CHIA-workflow
chia down configs/cluster-local.yaml -y 2>/dev/null   # tắt cluster một máy nếu đang chạy

export HEAD_IP=192.168.1.3
export REMOTE_IP=192.168.1.20
export REMOTE_USER=<user-trên-remote>
chia up configs/cluster-lan.yaml -y
ray status
```

`ray status` phải có **7 node** đang chạy (Active): Ray head, 3 container trên head và
3 container trên remote. Ở phần Resources phải thấy `chipyard: 2`, `cy_head: 1`,
`cy_remote: 1` và `verilator_run: 3`.

Chạy thử bằng hello-world: step 2 dùng resource `chipyard` và `verilator_run`, nên Ray
có thể xếp task sang remote. Mở dashboard <http://127.0.0.1:8265> → Cluster để xem
task chạy ở node nào.

```bash
cd ~/chia-hello-world
chia job submit --working-dir . -- python hello-world-s2.py
```

Thêm, bớt máy hoặc tăng số worker khi cluster đang chạy:

```bash
# sửa num_workers hoặc thêm node type mới trong configs/cluster-lan.yaml, rồi:
chia up --add configs/cluster-lan.yaml -y      # chỉ tạo worker còn thiếu, không đụng worker đang chạy
```

Tắt toàn bộ cluster (worker trên cả hai máy, rồi head):

```bash
chia down configs/cluster-lan.yaml -y
```

### Thêm máy thứ ba, thứ tư...

Sao chép khối `chipyard_remote` và `verilator_remote` trong `configs/cluster-lan.yaml`,
đổi `compatible_ips` sang IP mới, thêm entry vào `auth.overrides` nếu user khác. Nếu
nhiều máy giống nhau, có thể gộp vào một node type:

```yaml
    verilator_pool:
        resources: {"verilator_run": 1}
        num_workers: 6
        compatible_ips: ["192.168.1.20", "192.168.1.21", "192.168.1.22"]
        balance_level: worker          # chia đều 6 worker cho 3 máy
        docker: { ... }
```

## 6. Hai máy khác mạng: dùng Tailscale

Cách dễ nhất là đưa cả hai máy vào cùng một mạng Tailscale (kernel mode, cần sudo), rồi
coi như LAN:

```bash
# trên mỗi máy (Bazzite có sẵn tailscale; Ubuntu: curl -fsSL https://tailscale.com/install.sh | sh)
sudo systemctl enable --now tailscaled
sudo tailscale up
tailscale ip -4                                   # ví dụ head 100.101.1.1, remote 100.101.1.2

# trên mỗi máy dùng firewalld: tin tưởng interface tailscale0
sudo firewall-cmd --permanent --zone=trusted --add-interface=tailscale0 && sudo firewall-cmd --reload
```

Sau đó làm như mục 3–5, nhưng **dùng IP Tailscale**:

```bash
export HEAD_IP=$(tailscale ip -4)
export REMOTE_IP=100.101.1.2
chia up configs/cluster-lan.yaml -y
```

Đường truyền qua Internet chậm hơn LAN. Nên để các bước truyền nhiều dữ liệu
(build, rồi chạy simulator) chạy trên cùng một máy, xem
[chia-usage.md](chia-usage.md#ghim-tác-vụ-vào-cùng-một-worker).

## 7. Không có root: chế độ `tailnet:` của CHIA

CHIA có sẵn chế độ chạy qua Tailscale **userspace** (không cần root, không TUN). Nó
định tuyến toàn bộ traffic Ray qua một relay. Tham khảo
`~/work/chia/examples/tailscale/` (README, NETWORKING.md, `cluster.yaml`). Những điểm
cần lưu ý trên Bazzite:

- Bỏ `--node-ip-address=...` khỏi `head_start_ray_commands` vì CHIA tự thêm. Vẫn giữ
  `systemd-run --user --scope --quiet`.
- CHIA SSH qua SOCKS5 bằng `nc -X 5 -x ...` (OpenBSD netcat). Fedora/Bazzite dùng `nc`
  của nmap (`nc --version` in ra "Ncat"), nên phải đặt proxy riêng:
  ```yaml
  auth:
      ssh_user: ${USER}
      ssh_proxy_command: "ncat --proxy 127.0.0.1:1055 --proxy-type socks5 %h %p"
  ```
- Mọi worker phải là máy tailnet hoặc nằm chung máy với head. Không trộn được với
  worker LAN thường.

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `chia up` treo ở `Waiting for SSH to worker <REMOTE_IP>` | Chưa `ssh-copy-id`, `ssh-agent` chưa có key, sai `REMOTE_USER`, sshd remote chưa bật |
| `'docker' not found in login-shell PATH` trên remote | Docker chưa cài, hoặc chỉ có trong PATH của shell tương tác |
| `permission denied ... docker.sock` trên remote | User chưa vào group `docker`, hoặc chưa đăng nhập lại |
| Worker remote báo `Failed to connect to GCS at <HEAD_IP>:6379` | Firewall head chặn remote (mục 2), hoặc `HEAD_IP` là IP remote không tới được (ví dụ IP Docker bridge) |
| Node remote lúc có lúc không trong `ray status`, task treo khi lấy kết quả | Thiếu chiều head → remote: mở firewall trên remote cho IP head |
| Agent báo không kết nối được `http://<ip>:8000/...` | Firewall chặn cổng tool server giữa hai máy |
| LLM sửa RTL nhưng bản build không có thay đổi | Tool và build chạy ở hai container chipyard khác nhau. Ghim cùng một worker bằng `cy_head`/`cy_remote` |
| Pull image trên remote timeout | Tăng `pull_timeout`, hoặc chạy `docker pull` trước trên remote |
