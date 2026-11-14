# Thiết kế: thay fork RISCVTutorial bằng Chipyard gốc 1.15.0, dựng lại CHIA từ đầu

- Ngày: 14/11/2026
- Nhánh: `setup`
- Trạng thái: **bản thiết kế, chờ người dùng duyệt**. Chưa có gì được sửa hay chạy.

## 1. Mục đích và lý do

Bỏ fork RISCVTutorial (Chipyard 1.10.0) để **đồng bộ với Chipyard gốc** (`ucb-bar/chipyard`).
Lấy bản mới nhất hiện tại rồi **ghim cố định**; đồng bộ tiếp (lên bản mới hơn) là việc riêng, làm sau
khi có nhu cầu.

Nhân dịp đổi Chipyard, **dựng lại toàn bộ CHIA từ đầu** (như đợt 08/11/2026) với config mới, để tài
liệu cài đặt được kiểm chứng lại trên bản mới.

## 2. Mục tiêu, không làm, phản mục tiêu

**Mục tiêu**
1. Chipyard gốc, tag **`1.15.0`** (bản phát hành mới nhất, 15/10/2026), ở `~/tools/chipyard`, mã nguồn
   trên host sửa tay được, CHIA dùng qua khối `chipyard`/`vivado` như cũ.
2. `build-setup.sh` chạy **đủ 11 bước**, gồm FireSim (6, 7), FireMarshal (8, 9), CIRCT (10).
3. `MinimalSoCConfig` giữ đúng spec cũ (`docs/minimal-soc-config-spec.md`) nhưng elaborate được trên 1.15.0.
4. Một tài liệu cài lại từ đầu theo từng bước, mỗi bước có lệnh riêng và dấu hiệu "xong".
5. Mọi chỗ trong repo (nhánh `setup`) trỏ tới RISCVTutorial được cập nhật.

**Không làm lần này**
- Không sửa hai nhánh `project/ascon-secureboot`, `project/rocket-fmax` (viết cho 1.10; có thể không còn
  build được). Chỉ ghi chú vào `CLAUDE.md`.
- Không tự động đồng bộ lên bản Chipyard mới hơn; không theo `main`.
- Không chạy thử flow FireSim/FireMarshal (chỉ cài đặt), không chạy flow `fpga/`.
- Không kéo lại image `ghcr.io/ucb-bar/chia-*` (giữ image đang có).

**Phản mục tiêu** (thành thất bại dù "chạy được")
- Script lặng lẽ bỏ qua bước hỏng (ví dụ tự thêm `-s 6`) mà không báo.
- Lệnh xoá nằm trong script, dễ chạy nhầm lần hai.
- Ghi "đã kiểm chứng" cho thứ chưa chạy thật trên máy người dùng.

## 3. Ràng buộc và sự thật đã đối chiếu (mã nguồn tag 1.15.0)

- Claude không chạy lệnh trên máy người dùng: người dùng chạy, gửi log, Claude đọc.
- Bazzite: `/home` là symlink tới `/var/home`; mọi mount phải dùng `realpath`, cùng đường dẫn trên host và
  trong container (`env.sh`, conda env ghi đường dẫn tuyệt đối).
- Image `chia-chisel-build` được dựng từ nhánh `chia_artifact` (= 1.14.0 + vá CHIA). Toolchain của 1.15.0
  nằm trong `.conda-env` của thư mục chipyard nên không phụ thuộc Chipyard có sẵn trong image.
- `build-setup.sh` 1.15.0 tự tạo env `conda-lock` riêng (`.conda-lock-env`): **không còn cần**
  `pip install conda-lock==1.4.0` như 1.10. Còn cờ `-f` (bỏ câu hỏi "not on release") thì không cần vì
  ghim đúng tag release; builder kiểm lại khi viết script.
- Có thêm bước 10 (cài CIRCT, tải bản dựng sẵn từ GitHub). Không dùng `--use-lean-conda`.
- `.gitmodules` của 1.15.0 có ~60 submodule (gồm `spec2017`, `spec2026`, `firesim`, `firemarshal`...). Bước
  2 của `build-setup.sh` tự quyết submodule nào được init; "đủ 11 bước" nghĩa là theo mặc định của nó.
- Thay đổi API liên quan tới `MinimalSoCConfig`:

| Mục | 1.10.0 | 1.15.0 |
|---|---|---|
| Mixin core | `freechips.rocketchip.subsystem.WithNBigCores` | chuyển sang `freechips.rocketchip.rocket.WithNBigCores`; `RocketConfig` giờ dùng `WithNHugeCores(1)` (L1 64 set × 8 way, thêm Zba/Zbb/Zbs) |
| L2 | `subsystem.WithInclusiveCache(nWays, capacityKB)` + `WithNBanks` | vẫn ở `freechips.rocketchip.subsystem` (repo `rocket-chip-inclusive-cache`), `WithNBanks(n)` vẫn có |
| Scratchpad | `AbstractConfig` không có | `AbstractConfig` **thêm** `testchipip.soc.WithMbusScratchpad` 64 KiB tại `0x08000000` → phải bỏ bằng `testchipip.soc.WithNoScratchpads` (đang dùng trong `TinyRocketConfig`) |

## 4. Hướng làm (Approaches)

| Hướng | Mô tả | Đánh đổi |
|---|---|---|
| **A (đã chọn)** | Sửa script hiện có (đổi tên `setup-chipyard-fork.sh` → `setup-chipyard.sh`), thêm một tài liệu cài lại từng bước; lệnh xoá chỉ nằm trong tài liệu | Ít file mới, hợp cách làm "người dùng chạy, Claude đọc log", lỗi khoanh vùng theo bước; người dùng gõ nhiều lệnh hơn |
| B | Một `reinstall-all.sh` từ xoá tới kiểm tra | Một lệnh, nhưng chạy nhiều giờ, khó chạy tiếp khi hỏng giữa chừng, lệnh xoá dễ chạy nhầm; bước 6–9 chưa ai thử nên dễ hỏng giữa chừng |
| C | Giữ tên script, chỉ đổi mặc định | Ít sửa nhất, nhưng tên "fork" sai và mẹo của 1.10 lẫn với logic mới |

Người dùng chọn **A** (14/11/2026).

## 5. Quyết định đã chốt với người dùng

| # | Câu hỏi | Chọn |
|---|---|---|
| 1 | Đồng bộ với gì | Chipyard gốc mới nhất hiện tại, ghim cố định; đồng bộ tiếp để sau |
| 2 | Phiên bản | Tag `1.15.0` (đề xuất của Claude, người dùng đồng ý) |
| 3 | "Tải tất cả module/khối" | Chạy đủ 11 bước của `build-setup.sh` |
| 4 | "Xoá" | Dựng lại toàn bộ CHIA từ đầu với config mới, giữ Vivado |
| 5 | Image `ghcr.io/ucb-bar/chia-*` | Giữ, không kéo lại; chỉ xoá và build lại `chia-orfs` |
| 6 | Nhánh project | Không đụng; ghi chú trong `CLAUDE.md` |
| 7 | Bước 6–9 hỏng trong container | Script dừng và báo rõ bước nào; người dùng quyết định |
| 8 | Tổ chức | Hướng A |

## 6. Phạm vi xoá / giữ trên máy người dùng

**Xoá:** `~/tools/RISCVTutorial`, `~/tools/OpenROAD-flow-scripts`, env `chia_env`, `~/chia_cache`
(cả cache JVM), image `chia-orfs:latest` (và image `chia-*` tự build khác nếu có), container `chia-*` /
`chipyard-dev` còn sót. `~/CHIA-workflow` chuyển vào `~/archive/` rồi `git clone` lại nhánh `setup`.

**Giữ:** `~/tools/Xilinx/2025.1`, `~/.claude` (+ `~/.claude.json`), Miniforge, image
`ghcr.io/ucb-bar/chia-*`, thiết lập hệ thống (docker, sshd, linger, `.bashrc`, khoá SSH).

## 7. Thay đổi trong repo (nhánh `setup`)

| File | Việc |
|---|---|
| `scripts/setup-chipyard-fork.sh` | **Đổi tên** thành `scripts/setup-chipyard.sh` (`git mv`) và viết lại theo mục 8 |
| `scripts/install-chipyard-config.sh` | Mặc định `~/tools/chipyard`; danh sách mixin cần `grep` theo 1.15.0 (bỏ các mẫu cũ không còn dùng, thêm `WithNoScratchpads`) |
| `scripts/chia-up-blocks.py` | Mặc định `CHIPYARD_FORK` → `~/tools/chipyard`; thông báo lỗi trỏ tới `setup-chipyard.sh` |
| `configs/cluster-blocks.template.yaml` | Comment: đường dẫn mặc định, trạng thái khối "chưa kiểm chứng trên 1.15.0" |
| `chipyard/MinimalSoCConfig.scala` | Sửa theo mục 9 |
| `docs/reinstall.md` | **Tạo mới**: tài liệu cài lại từ đầu theo mục 10 |
| `docs/cluster-blocks.md` | Sơ đồ `~/tools`, mục 1 dựng Chipyard, mount tay `chipyard-dev`, bảng trạng thái; giữ bảng lỗi cũ, ghi rõ dòng nào chỉ áp dụng 1.10 |
| `docs/chipyard-minimal-soc.md`, `docs/minimal-soc-config-spec.md` | Cập nhật bảng mixin theo 1.15.0; ghi "kiểm chứng trên 1.10 (09/11), chưa trên 1.15.0" |
| `docs/setup-bazzite.md` | Mục 7 (Chipyard native) trỏ sang cách mới / `reinstall.md` nếu cần, không viết lại |
| `README.md` | Tên script, đường dẫn, link `docs/reinstall.md` |
| `CLAUDE.md` | Máy người dùng, bố cục `~/tools`, đã/chưa kiểm chứng, ghi chú nhánh project, việc tiếp theo |

Không xoá file tài liệu nào. Biến môi trường vẫn tên `CHIPYARD_FORK` (đổi tên sẽ làm hỏng nhánh project
đang dùng nó); chỉ đổi giá trị mặc định. *(Claude quyết định.)*

## 8. `scripts/setup-chipyard.sh`

```
scripts/setup-chipyard.sh [--tag TAG] [--verify] [CHIPYARD_DIR]
  CHIPYARD_DIR  mặc định $CHIPYARD_FORK, hoặc ~/tools/chipyard
  --tag         mặc định 1.15.0
  --verify      sau setup: build Verilator MinimalSoCConfig... (xem dưới)
```

- Chưa có thư mục: `git clone https://github.com/ucb-bar/chipyard.git` rồi `git checkout <tag>` (đứng
  trên tag, detached HEAD là chấp nhận được; hoặc tạo nhánh cục bộ `local-<tag>` để người dùng commit sửa
  tay. *Claude chọn: nhánh cục bộ `local-1.15.0`*).
- Đã có thư mục: không clone; in ra commit hiện tại và cảnh báo nếu không trùng tag (không tự checkout,
  tránh mất sửa tay).
- Chạy `build-setup.sh riscv-tools` **không có `-s`** trong container `chia-chisel-build` tạm (`--rm`,
  `--shm-size=10.24gb`, mount `realpath` cùng đường dẫn, UID host = UID `ray`).
- Log: `<dir>/setup.log`. Thành công khi thấy chuỗi hoàn tất của `build-setup.sh` 1.15.0 (builder kiểm
  chuỗi chính xác trong mã nguồn). Khi hỏng: in **số bước và tên bước cuối cùng** đã bắt đầu (đọc từ log),
  kèm lệnh chạy lại bỏ đúng bước đó bằng `-s N` cho người dùng tự quyết. Không tự thêm `-s`.
- Cho phép truyền thêm cờ cho `build-setup.sh` sau `--` (để người dùng tự bỏ bước khi đã quyết).
- Cache JVM: mount `~/chia_cache/jvm/{coursier,sbt,ivy2}` (như khối chipyard) và đặt `JAVA_TOOL_OPTIONS`
  timeout, vì bước 5/7 tiền biên dịch Scala qua sbt (lỗi treo coursier đã gặp). *(Claude quyết định.)*
- `--verify`: `source env.sh`, build Verilator `RocketConfig`, chạy `rv64ui-p-simple`, in `VERIFY_OK`.
  `MinimalSoCConfig` kiểm riêng ở bước sau (cần `install-chipyard-config.sh` trước).
- Script cần chạy trong `tmux` (vài giờ); ghi rõ ở đầu file.

## 9. `MinimalSoCConfig` trên 1.15.0

```scala
class MinimalSoCConfig extends Config(
  new testchipip.soc.WithNoScratchpads ++                                   // 1.15 AbstractConfig thêm scratchpad 64 KiB
  new freechips.rocketchip.subsystem.WithInclusiveCache(nWays = 4, capacityKB = 128) ++
  new freechips.rocketchip.subsystem.WithNBanks(1) ++
  new freechips.rocketchip.rocket.WithNBigCores(1) ++
  new chipyard.config.AbstractConfig)
```

- Core: giữ `WithNBigCores` (đúng mixin của spec cũ, chỉ đổi package) thay vì `WithNHugeCores` mà
  `RocketConfig` 1.15 dùng, để giữ "config như cũ". *(Claude quyết định; builder đối chiếu tham số L1 của
  `WithNBigCores` 1.15 với 1.10 và ghi lại nếu khác.)*
- Thứ tự `WithNoScratchpads` so với `WithMbusScratchpad` trong chuỗi `++`: builder kiểm theo cách
  `TinyRocketConfig` dùng.
- Tiêu chí kiểm (giống spec cũ, mục 5): build Verilator OK; `rv64ui-p-simple` và `hello.riscv` `EXIT=0`;
  L2 131072 byte; không có GPIO/SPI/I2C/PWM/**scratchpad** trong DTS/`*.json` sinh ra.
- Nếu `hello.riscv` (bare-metal) mặc định đặt code vào scratchpad trên 1.15: ghi lại, không tự sửa linker
  script; báo người dùng.

## 10. `docs/reinstall.md` (thứ tự bước)

Mỗi bước: một khối lệnh `bash` riêng, dấu hiệu xong, log cần gửi lại.

0. **Kiểm kê** (chỉ đọc): `docker ps -a`, `docker images | grep chia`, `conda env list`, `du -sh` các thư mục
   sẽ xoá, `df -h /var`. Người dùng đọc kết quả rồi mới qua bước 1.
1. **Dừng**: `chia down cluster.gen.yaml -y` (nếu còn chạy), `docker rm -f` container `chia-*`, `chipyard-dev`.
2. **Xoá** (danh sách mục 6). `~/CHIA-workflow` → `~/archive/CHIA-workflow-<ngày>`.
3. **Clone** `~/CHIA-workflow` nhánh `setup`; `check-bazzite-env.sh`.
4. **`chia_env`**: tạo lại, `conda env config vars set PYTHONNOUSERSITE=1`, cài `chialoops` 2.0.1; dấu hiệu:
   Python 3.10.19, Ray 2.54.0.
5. **Chipyard**: `scripts/setup-chipyard.sh --verify` (tmux, vài giờ) → `VERIFY_OK`.
6. **MinimalSoCConfig**: `install-chipyard-config.sh` → `MIXIN_OK`; build + chạy theo `chipyard-minimal-soc.md`.
7. **ORFS**: `docker build` `chia-orfs`, `setup-orfs.sh --verify` → `VERIFY_OK`.
8. **Cluster**: `chia-up-blocks.py --fpga --asic`, `blocks_smoke.py --clean` → `KẾT QUẢ: OK`; `chia down`.

## 11. Để builder quyết

- Chuỗi thành công / cách nhận diện bước đang chạy trong log `build-setup.sh` 1.15.0.
- Danh sách mẫu `grep` trong `install-chipyard-config.sh`.
- Câu chữ docs, vị trí chi tiết từng đoạn sửa.
- Các lệnh xoá cụ thể (an toàn: không dùng glob rộng trong `$HOME`, in ra trước khi xoá).

## 12. Kiểm chứng

Toàn bộ do người dùng chạy trên máy họ (mục 10). Trong sandbox Claude chỉ kiểm được: `bash -n`, `shellcheck`
(nếu có), `python -m py_compile`, `chia-up-blocks.py --dry-run` nếu chạy được không cần cluster, và đối
chiếu mixin với mã nguồn tag 1.15.0. Mọi mục trong `CLAUDE.md` ghi "chưa kiểm chứng trên 1.15.0" cho tới
khi có log.
