# Dùng CHIA với worker trên máy remote

Hướng dẫn dùng CHIA khi máy **head** (Bazzite) có thêm một hay nhiều **máy remote** làm
worker. Tài liệu giả định bạn đã chạy được cluster một máy theo
[setup-bazzite.md](setup-bazzite.md). Cách viết node, LLM, tool, cache và profiling không
khác chế độ một máy, xem [usage-local.md](usage-local.md). File này chỉ nói những gì
**thêm vào hoặc khác đi** khi có nhiều máy.

> Template `configs/cluster-lan.yaml` và các script được viết từ tài liệu và mã nguồn
> CHIA (`docs/user_guides/cluster_config_reference.rst`, `chia/cluster/*.py`). Phần
> nhiều máy **chưa được chạy thử**.

Mục lục:
1. [Kiến trúc nhiều máy](#1-kiến-trúc-nhiều-máy)
2. [Chuẩn bị máy remote](#2-chuẩn-bị-máy-remote)
3. [Mạng và firewall (cả hai chiều)](#3-mạng-và-firewall-cả-hai-chiều)
4. [SSH không mật khẩu từ head sang remote](#4-ssh-không-mật-khẩu-từ-head-sang-remote)
5. [Kiểm tra máy remote](#5-kiểm-tra-máy-remote)
6. [Bật, tắt và mở rộng cluster](#6-bật-tắt-và-mở-rộng-cluster)
7. [Viết loop cho nhiều máy](#7-viết-loop-cho-nhiều-máy)
8. [Hai máy khác mạng: Tailscale](#8-hai-máy-khác-mạng-tailscale)
9. [Không có root: chế độ `tailnet:` của CHIA](#9-không-có-root-chế-độ-tailnet-của-chia)
10. [Lỗi hay gặp](#10-lỗi-hay-gặp)

---

## 1. Kiến trúc nhiều máy

```
HEAD (Bazzite, 192.168.1.3)                    REMOTE (192.168.1.20)
 ├─ conda chia_env + Ray head (:6379)            ├─ sshd
 ├─ driver: chia job submit ...                  ├─ Docker
 ├─ container chipyard  {chipyard, cy_head}      ├─ container chipyard    {chipyard, cy_remote}
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
  chạy trong container qua `docker exec`, không thuộc phiên SSH. Chỉ Ray head cần
  `systemd-run`, và `cluster-lan.yaml` đã có sẵn.

Chọn kiểu kết nối:

| Trường hợp | Cách làm |
|---|---|
| Hai máy cùng LAN (cùng router, cùng mạng lab) | Mục 2–6, dùng IP LAN |
| Hai máy khác mạng (nhà ↔ lab, có NAT) | Cài Tailscale ở cả hai máy (mục 8), rồi làm như LAN với IP `100.x.y.z` |
| Không có quyền root trên máy remote | Chế độ `tailnet:` có sẵn của CHIA (mục 9) |

## 2. Chuẩn bị máy remote

Chép `scripts/prepare-remote-worker.sh` sang máy remote rồi chạy. Script hỏi trước mỗi
bước và hỗ trợ Ubuntu/Debian, Fedora, Fedora Atomic (Bazzite):

```bash
# trên HEAD
scp ~/CHIA-workflow/scripts/prepare-remote-worker.sh <user>@192.168.1.20:
# trên REMOTE
bash prepare-remote-worker.sh 192.168.1.3        # IP của head
```

Script làm 5 việc. Bạn cũng có thể tự làm bằng tay:

1. Bật sshd: `sudo systemctl enable --now ssh` (Ubuntu) hoặc `sshd` (Fedora/Bazzite).
2. Cài Docker và thêm user vào group `docker`, rồi **đăng xuất và đăng nhập lại**.
   - Ubuntu: `sudo apt install docker.io && sudo usermod -aG docker $USER`.
   - Bazzite: rebase sang `bazzite-dx`, rồi `ujust dx-group` (giống bước 1 của setup-bazzite).
3. Cài `rsync` nếu chưa có.
4. Mở firewall cho IP của head (mục 3).
5. Pull trước `chia-chisel-build` và `chia-verilator-run`. Không bắt buộc, nhưng image rất lớn.

Phần cứng gợi ý cho remote: x86_64, ≥ 16 GB RAM cho mỗi worker Chipyard, ≥ 120 GB ổ trống.

## 3. Mạng và firewall (cả hai chiều)

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

## 4. SSH không mật khẩu từ head sang remote

```bash
# trên HEAD
ssh-copy-id <user>@192.168.1.20
eval "$(ssh-agent -s)"; ssh-add            # CHIA dùng ssh -A
ssh <user>@192.168.1.20 'bash --login -c "docker ps"'   # không hỏi mật khẩu, không lỗi quyền
```

Nếu user trên remote khác user trên head, khai báo trong `auth.overrides`. Template
`configs/cluster-lan.yaml` đã có sẵn phần này, lấy từ biến `REMOTE_USER`.

## 5. Kiểm tra máy remote

```bash
# trên HEAD
bash ~/CHIA-workflow/scripts/check-remote-worker.sh <user>@192.168.1.20 192.168.1.3
```

Script kiểm tra: SSH, kiến trúc CPU, Docker không cần sudo, rsync, RAM và ổ đĩa, và (khi
cluster đang chạy) remote có tới được `HEAD:6379` không. Mọi mục `[FAIL]` phải sửa trước
khi `chia up`. Chạy lại script sau khi cluster đã lên để kiểm tra chiều remote → head.

## 6. Bật, tắt và mở rộng cluster

```bash
conda activate chia_env
CL=~/CHIA-workflow/configs/cluster-lan.yaml
chia down ~/CHIA-workflow/configs/cluster-local.yaml -y 2>/dev/null   # mỗi máy chỉ chạy được một head

export HEAD_IP=192.168.1.3
export REMOTE_IP=192.168.1.20
export REMOTE_USER=<user-trên-remote>
chia up $CL -y
chia status
```

`chia status` phải có **7 node** đang chạy (Active): Ray head, 3 container trên head và
3 container trên remote. Ở phần Resources phải thấy `chipyard: 2`, `cy_head: 1`,
`cy_remote: 1` và `verilator_run: 3`.

Kiểm tra nhanh bằng hello-world. Step 2 dùng resource `chipyard` và `verilator_run`, nên
Ray có thể xếp task sang remote. Mở dashboard <http://127.0.0.1:8265> → Cluster để xem
task chạy ở node nào.

```bash
cd ~/chia-hello-world
chia job submit --working-dir . -- python hello-world-s2.py
```

**Mở rộng khi cluster đang chạy:** sửa yaml (tăng `num_workers`, thêm node type, thêm
IP), rồi chạy:

```bash
chia up --add $CL -y        # chỉ tạo worker còn thiếu, không đụng worker đang chạy
```

**Tắt** toàn bộ cluster (worker trên mọi máy, rồi head):

```bash
chia down $CL -y
```

### Thêm máy thứ ba, thứ tư...

Làm mục 2–5 cho máy mới. Sau đó sao chép khối `chipyard_remote` và `verilator_remote`
trong yaml, đổi `compatible_ips` sang IP mới và thêm entry vào `auth.overrides` nếu user
khác. Nếu nhiều máy giống nhau, có thể gộp vào một node type:

```yaml
    verilator_pool:
        resources: {"verilator_run": 1}
        num_workers: 6
        compatible_ips: ["192.168.1.20", "192.168.1.21", "192.168.1.22"]
        balance_level: worker          # chia đều 6 worker cho 3 máy
        docker: { ... }
```

Mỗi node type trên cùng một máy cần `container_name` khác nhau. CHIA tự thêm `-0`,
`-1`,... theo chỉ số worker.

## 7. Viết loop cho nhiều máy

Code loop **không phải đổi** khi thêm máy: Ray tự rải task theo resource. Có 3 điểm
cần chú ý.

### 7.1. Ghim tool và build vào cùng một worker

Agent sửa RTL qua `BashTool` trong container chipyard **A**. Nếu bước build sau đó rơi
vào container chipyard **B** (máy khác), bản build sẽ không có thay đổi. Vì vậy
`cluster-lan.yaml` gán cho mỗi container chipyard một resource riêng theo máy
(`cy_head`, `cy_remote`). Ghim cả tool lẫn build bằng resource đó:

```python
pin = {"chipyard": 1, "cy_remote": 0.01}
bash = BashTool("chipyard_bash", "/home/ray/chipyard", task_options={"resources": pin})
resp = get(llm.prompt.chia_remote(llm, prompt, tools=[bash]))
bash.stop()                                            # nhả slot chipyard
art_ref = cb.build.options(resources=pin).chia_remote(cb)
```

Hai điểm khác cũng phải để ý:
- Task không ghim (ví dụ `compile_program` với `{"chipyard": 1}`) có thể chạy ở bất kỳ
  container chipyard nào. Không sao, miễn là task đó trả về dữ liệu chứ không để lại
  file.
- Chạy song song N agent: mỗi agent ghim vào một container riêng (`cy_head`,
  `cy_remote`,...), mỗi container có nhánh sửa RTL riêng của nó.

### 7.2. Rải task để tận dụng mọi máy

```python
# 3 slot verilator_run (1 ở head, 2 ở remote) -> 3 simulator chạy cùng lúc
refs = [sim.run.chia_remote(sim, build_ref, elf, f"t{i}.riscv", "/home/ray")
        for i, elf in enumerate(elfs)]
results = get(refs)

# ép rải đều ra các node thay vì dồn vào node gần nhất
ref = run_synthesis.options(scheduling_strategy="SPREAD").chia_remote(design)
```

Khi so sánh nhiều cấu hình, mỗi máy có 1 chipyard nên 2 lần build chạy song song:

```python
builds = {cfg: ChiselBuildNode("/home/ray/chipyard", cfg) for cfg in ["RocketConfig", "TinyRocketConfig"]}
refs = {cfg: n.build.chia_remote(n) for cfg, n in builds.items()}   # Ray xếp mỗi build vào một container chipyard
```

### 7.3. Dữ liệu đi qua mạng

- Ray chuyển object giữa các máy (ví dụ `BuildArtifact` chứa simulator vài chục MB).
  Trong LAN thì nhanh. Qua Tailscale hay Internet thì nên giữ build và simulate trên
  cùng một máy, bằng cách ghim cả hai qua resource riêng. Ví dụ đổi verilator remote
  thành `{"verilator_run": 1, "vr_remote": 1}` rồi chạy
  `sim.run.options(resources={"verilator_run": 1, "vr_remote": 0.01})`.
- Truyền `ObjectRef` thẳng vào node sau thay vì `get()` về driver rồi gửi lại. Cách
  này tránh dữ liệu phải đi head → remote hai lần.
- File trên head (ví dụ dữ liệu bypass qua `data:`) được CHIA phục vụ qua actor trên
  head, không cần thư mục chia sẻ giữa các máy.

### 7.4. Loop mẫu trên cluster hai máy

```bash
cp ~/CHIA-workflow/examples/rtl_agent_loop.py ~/chia-hello-world/
cd ~/chia-hello-world
chia job submit --working-dir . -- python rtl_agent_loop.py --pin cy_remote   # sửa và build trên remote
chia job submit --working-dir . -- python rtl_agent_loop.py --pin cy_head     # sửa và build trên head
```

Simulator được chạy trên cả 3 worker `verilator_run`. Loop mẫu **chưa được chạy thử**.
Giải thích chi tiết từng bước: [usage-local.md mục 10](usage-local.md#10-loop-mẫu-agent-sửa-rtl-có-phản-hồi).

## 8. Hai máy khác mạng: Tailscale

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

Sau đó làm như mục 4–6, nhưng **dùng IP Tailscale**:

```bash
export HEAD_IP=$(tailscale ip -4)
export REMOTE_IP=100.101.1.2
chia up ~/CHIA-workflow/configs/cluster-lan.yaml -y
```

Đường truyền qua Internet chậm hơn LAN, xem mục 7.3.

## 9. Không có root: chế độ `tailnet:` của CHIA

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

## 10. Lỗi hay gặp

Các lỗi chung (resource, timeout của LLM, `Ctrl+C`,...) có trong
[usage-local.md mục 11](usage-local.md#11-lỗi-hay-gặp). Dưới đây là các lỗi riêng của
cluster nhiều máy:

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `chia up` treo ở `Waiting for SSH to worker <REMOTE_IP>` | Chưa `ssh-copy-id`, `ssh-agent` chưa có key, sai `REMOTE_USER`, hoặc sshd trên remote chưa bật |
| `'docker' not found in login-shell PATH` trên remote | Docker chưa cài, hoặc chỉ có trong PATH của shell tương tác |
| `permission denied ... docker.sock` trên remote | User chưa vào group `docker`, hoặc chưa đăng nhập lại |
| Worker remote báo `Failed to connect to GCS at <HEAD_IP>:6379` | Firewall head chặn remote (mục 3), hoặc `HEAD_IP` là IP mà remote không tới được (ví dụ IP Docker bridge) |
| Node remote lúc có lúc không trong `chia status`, task treo khi lấy kết quả | Thiếu chiều head → remote: mở firewall trên remote cho IP head |
| Agent báo không kết nối được `http://<ip>:8000/...` | Firewall chặn cổng tool server giữa hai máy |
| LLM sửa RTL nhưng bản build không có thay đổi | Tool và build chạy ở hai container chipyard khác nhau. Ghim bằng `cy_head`/`cy_remote` (mục 7.1) |
| Ray head không lên, cổng 6379 đang bị chiếm | Mỗi máy chỉ chạy được một head. `chia down` cluster cũ trước (ví dụ `cluster-local.yaml`) |
| Pull image trên remote timeout | Tăng `pull_timeout`, hoặc chạy `docker pull` trước trên remote |
| Dữ liệu chuyển chậm qua Tailscale | Ghim build và simulate trên cùng máy (mục 7.3) |
