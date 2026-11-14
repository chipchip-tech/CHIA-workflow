# Cài lại CHIA từ đầu với Chipyard gốc 1.15.0

Gỡ fork RISCVTutorial (Chipyard 1.10.0) và mọi thứ CHIA đã dựng, rồi dựng lại với Chipyard gốc
`ucb-bar/chipyard` ghim tag **1.15.0** ở `~/tools/chipyard`, chạy đủ 11 bước của `build-setup.sh`.
Thiết kế: [superpowers/specs/2026-11-14-chipyard-upstream-design.md](superpowers/specs/2026-11-14-chipyard-upstream-design.md).

**Trạng thái: chưa chạy thật** (viết 14/11/2026). Làm lần lượt từng bước, gửi lại phần "Gửi lại" của mỗi
bước trước khi sang bước sau. Bước 2 xoá thật, không hoàn tác được.

| Xoá | Giữ |
|---|---|
| `~/tools/RISCVTutorial` (fork cũ) | `~/tools/Xilinx/2025.1` (Vivado) |
| `~/tools/OpenROAD-flow-scripts` (chép lại ở bước 7) | `~/.claude`, `~/.claude.json` (khối claude không phải đăng nhập lại) |
| env `chia_env` | Miniforge `~/miniforge3` |
| `~/chia_cache` (cả cache JVM; lần build đầu tải lại) | image `ghcr.io/ucb-bar/chia-*` (không kéo lại) |
| image tự build `chia-orfs:latest` | docker, sshd, linger, `~/.bashrc`, khoá SSH |
| `~/CHIA-workflow` → chuyển vào `~/archive/` rồi clone lại | |

## 0. Kiểm kê (chỉ đọc)

Xem có gì đang chạy, sẽ xoá bao nhiêu và còn bao nhiêu chỗ trống. Đủ 11 bước cần thêm vài chục GB.

```bash
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
```

```bash
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep -i chia
```

```bash
conda env list
```

```bash
du -sh ~/tools/RISCVTutorial ~/tools/OpenROAD-flow-scripts ~/chia_cache ~/CHIA-workflow 2>&1; df -h /var
```

Hai cây sắp xoá là nơi sửa tay (fork Chipyard nhánh `base`, `flow/` của ORFS được `git init` để theo dõi sửa
tay), và bạn không `git push` được, nên kiểm xem có commit hay thay đổi chỉ nằm ở đó không:

```bash
git -C ~/tools/RISCVTutorial status --short | head -20; git -C ~/tools/RISCVTutorial log --oneline --branches --not --remotes | head -20
```

```bash
git -C ~/tools/OpenROAD-flow-scripts/flow status --short | head -20; git -C ~/tools/OpenROAD-flow-scripts/flow log --oneline | head -20
```

Commit cục bộ có thể nằm cả trong **submodule** (agent của project fmax commit vào `rocket-chip`,
`sifive-cache`...), nên kiểm cả submodule:

```bash
cd ~/tools/RISCVTutorial && git submodule foreach --recursive -q 'n=$(git log --oneline --branches --not --remotes 2>/dev/null | wc -l); d=$(git status --short | wc -l); [ "$n$d" != "00" ] && echo "$sm_path: $n commit cục bộ, $d file thay đổi"; true'
```

Nếu có thứ cần giữ, lưu vào `~/archive` **trước bước 2**: bundle + patch cho repo gốc và từng submodule có
commit cục bộ, cùng SHA của mọi submodule (đổi danh sách submodule theo kết quả lệnh trên):

```bash
A=~/archive/RISCVTutorial-$(date +%Y%m%d) && mkdir -p $A && cd ~/tools/RISCVTutorial && git submodule status --recursive > $A/submodule-heads.txt && for r in . generators/rocket-chip generators/sifive-cache; do n=$([ "$r" = . ] && echo fork-root || echo "$r" | tr / _); git -C $r bundle create $A/$n.bundle HEAD --branches --not --remotes && git -C $r format-patch -q -o $A/$n-patches HEAD --branches --not --remotes && git -C $r diff > $A/$n-uncommitted.diff; done; ls -la $A
```

Kết quả sinh ra trong cây (checkpoint Vivado, design/report ORFS) thì chép thư mục ra `~/archive` bằng `cp -a`.

**Xong khi:** đọc hết kết quả, không có gì lạ (ví dụ container không thuộc CHIA mà tên bắt đầu `chia-`), và sửa tay
cần giữ đã được lưu. **Gửi lại:** toàn bộ kết quả các lệnh.

## 1. Dừng cluster và container

```bash
cd ~/CHIA-workflow && conda activate chia_env && chia down cluster.gen.yaml -y
```

(Báo lỗi vì không có cluster nào đang chạy thì bỏ qua.) Xoá các container còn sót, **liệt kê trước**:

```bash
docker ps -a --format '{{.Names}}' | grep -E '^(chia-|chipyard-dev$)'
```

Nếu danh sách đúng là của CHIA:

```bash
docker ps -a --format '{{.Names}}' | grep -E '^(chia-|chipyard-dev$)' | xargs -r docker rm -f
```

```bash
ray stop
```

**Xong khi:** lệnh liệt kê ở trên không in gì. **Gửi lại:** kết quả lệnh liệt kê trước và sau.

## 2. Xoá (không hoàn tác được)

```bash
conda deactivate; conda env remove -n chia_env -y
```

```bash
rm -rf ~/tools/RISCVTutorial
```

```bash
rm -rf ~/tools/OpenROAD-flow-scripts
```

```bash
rm -rf ~/chia_cache
```

```bash
docker rmi chia-orfs:latest
```

Image tự build khác (không có tiền tố `ghcr.io/`), nếu có, liệt kê rồi tự chọn xoá bằng `docker rmi <tên>`:

```bash
docker images --format '{{.Repository}}:{{.Tag}}' | grep -i chia | grep -v '^ghcr.io/'
```

Chuyển repo cũ vào archive (giữ lại để tra cứu):

```bash
mkdir -p ~/archive && mv ~/CHIA-workflow ~/archive/CHIA-workflow-$(date +%Y%m%d)
```

**Xong khi:** `ls ~/tools` chỉ còn `Xilinx`; `conda env list` không còn `chia_env`.
**Gửi lại:** `ls ~/tools`, `conda env list`, `docker images | grep -i chia`.

## 3. Clone repo và kiểm môi trường

```bash
git clone -b setup https://github.com/chipchip-tech/CHIA-workflow.git ~/CHIA-workflow
```

```bash
cd ~/CHIA-workflow && scripts/check-bazzite-env.sh
```

**Xong khi:** script pass hết (WARN về RAM là bình thường; FAIL về `chia_env` là bình thường, bước 4 tạo).
**Gửi lại:** đuôi kết quả `check-bazzite-env.sh`.

## 4. Tạo lại `chia_env`

`PYTHONNOUSERSITE=1` để pip không lấy gói từ `~/.local` (lỗi `anyio`/`httpx` từng gặp).

```bash
conda create -n chia_env python=3.10.19 -y
```

```bash
conda env config vars set PYTHONNOUSERSITE=1 -n chia_env
```

```bash
conda activate chia_env && pip install chialoops==2.0.1
```

```bash
python -V; ray --version; python -c "import site; print(site.ENABLE_USER_SITE)"; chia --help | head -3
```

**Xong khi:** `Python 3.10.19`, `ray, version 2.54.0`, `False`, và `chia --help` chạy được.
**Gửi lại:** kết quả lệnh cuối.

## 5. Dựng Chipyard 1.15.0 (nhiều giờ, chạy trong tmux)

```bash
tmux new -s chipyard
```

```bash
cd ~/CHIA-workflow && scripts/setup-chipyard.sh --verify
```

Script clone `ucb-bar/chipyard` vào `~/tools/chipyard`, checkout tag `1.15.0` thành nhánh `local-1.15.0`, rồi
chạy `build-setup.sh` **đủ 11 bước** trong container `chia-chisel-build` tạm. Log: `~/tools/chipyard/setup.log`.
Thoát tmux mà vẫn để chạy: `Ctrl+B` rồi `D`; vào lại: `tmux attach -t chipyard`.

**Nếu dừng ở bước 6–9** (FireSim/FireMarshal; có thể cần `sudo` hoặc `/dev/kvm` mà container không có):
script in số bước, tên bước và lệnh chạy lại. Lệnh đó bỏ luôn các bước đã xong (bước 1 của 1.15.0 không chạy
lại được khi `.conda-env` đã có) và tự `source env.sh`. Gửi đuôi `setup.log` để ghi lỗi vào docs, rồi chạy
đúng lệnh script in ra, thêm `--verify` (thư mục đã có nên không clone lại), ví dụ hỏng ở bước 8:

```bash
cd ~/CHIA-workflow && scripts/setup-chipyard.sh --verify ~/tools/chipyard -- -s 1 -s 2 -s 3 -s 4 -s 5 -s 6 -s 7 -s 8 -s 9
```

Hỏng ở bước khác (1–5, 10, 11) thì không bỏ qua: gửi log để sửa; script in lệnh chạy tiếp từ bước hỏng
(hỏng ở bước 1 thì phải xoá `.conda-lock-env`, `.conda-env` trước, script in sẵn lệnh).

**Xong khi:** thấy `VERIFY_OK` và `== Xong`.
**Gửi lại:** `tail -30 ~/tools/chipyard/setup.log`, `git -C ~/tools/chipyard log -1 --oneline`, dòng `VERIFY_OK`,
và (nếu có) các bước đã bỏ.

## 6. Cài và kiểm `MinimalSoCConfig`

```bash
cd ~/CHIA-workflow && scripts/install-chipyard-config.sh
```

**Xong khi:** `MIXIN_OK`. Rồi build và chạy theo [chipyard-minimal-soc.md](chipyard-minimal-soc.md) mục 2–3.
**Gửi lại:** như mục "Gửi lại" của tài liệu đó.

## 7. ORFS (khối asic)

```bash
cd ~/CHIA-workflow && docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker
```

```bash
cd ~/CHIA-workflow && scripts/setup-orfs.sh --verify
```

**Xong khi:** `VERIFY_OK`. **Gửi lại:** đuôi kết quả hai lệnh.

## 8. Bật cluster đủ khối và kiểm tra

```bash
cd ~/CHIA-workflow && conda activate chia_env && python scripts/chia-up-blocks.py --fpga --asic
```

```bash
cd ~/CHIA-workflow/examples && chia job submit --working-dir . -- python blocks_smoke.py --clean
```

Lần build đầu tải lại cache JVM nên lâu. Xong thì tắt:

```bash
cd ~/CHIA-workflow && chia down cluster.gen.yaml -y
```

**Xong khi:** `[chipyard] env` có `RISCV=/var/home/<user>/tools/chipyard/.conda-env/riscv-tools` (đường dẫn
thật), `SUCCESS=True`, `vivado v2025.1`, và `KẾT QUẢ: OK`.
**Gửi lại:** toàn bộ log job (`chia job logs <id>`).

Sau bước 8, Claude cập nhật `CLAUDE.md` và docs: chuyển các mục sang "đã kiểm chứng trên 1.15.0", ghi lỗi mới.
