# Chipyard gốc 1.15.0 + dựng lại CHIA: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay fork RISCVTutorial bằng Chipyard gốc tag `1.15.0` trong script, config và tài liệu của nhánh `setup`, kèm tài liệu cài lại CHIA từ đầu theo từng bước.

**Architecture:** Hướng A của spec: đổi tên và viết lại script dựng Chipyard, chỉnh giá trị mặc định ở các script/template đang trỏ fork, sửa `MinimalSoCConfig` theo API 1.15.0, thêm `docs/reinstall.md`. Người dùng chạy trên máy họ; Claude chỉ kiểm tĩnh trong sandbox.

**Tech Stack:** bash, Python 3 (stdlib), Scala (Chipyard config), Markdown tiếng Việt.

**Spec:** `docs/superpowers/specs/2026-11-14-chipyard-upstream-design.md`

## Global Constraints

- Tag Chipyard: `1.15.0`; URL `https://github.com/ucb-bar/chipyard.git`; thư mục mặc định `~/tools/chipyard`; nhánh cục bộ `local-1.15.0`.
- Biến môi trường giữ tên `CHIPYARD_FORK` (chỉ đổi mặc định).
- Image dựng: `ghcr.io/ucb-bar/chia-chisel-build:latest`; không kéo lại image `ghcr.io/ucb-bar/chia-*`.
- `build-setup.sh riscv-tools` chạy **không có `-s`** mặc định; không bao giờ tự thêm `-s`.
- Không `--privileged`, không root, không image phái sinh cho bước 6–9.
- Mọi đường dẫn mount qua `realpath`, cùng đường dẫn host/container.
- Lệnh xoá chỉ nằm trong `docs/reinstall.md`, không trong script.
- Tài liệu tiếng Việt; mọi thứ chưa chạy trên máy người dùng ghi "chưa kiểm chứng trên 1.15.0".
- Commit lên `setup`, push `origin/setup`; không PR; không đụng nhánh `project/*`.

## Review Focus

1. `build-setup.sh` hỏng ở bước 6–9 → script in số bước, tên bước và lệnh chạy lại có `-s N`, thoát mã ≠ 0 (test `failure_report` ở Task 1).
2. Thư mục Chipyard đã tồn tại nhưng không ở tag `1.15.0` (người dùng đã sửa tay) → chỉ cảnh báo, không checkout/clone (test ở Task 1).
3. `~` trỏ qua symlink `/home` → `/var/home`: mount phải là đường dẫn thật (test `--dry-run` với symlink ở Task 1).
4. `chia-up-blocks.py` chạy khi `~/tools/chipyard/env.sh` chưa có → thông báo lỗi trỏ tới `setup-chipyard.sh` (Task 3).
5. `MinimalSoCConfig` tham chiếu mixin không có trong 1.15.0 → `install-chipyard-config.sh` báo CẢNH BÁO thay vì `MIXIN_OK` (Task 2, chạy trên cây 1.15.0 thật).

---

### Task 1: `scripts/setup-chipyard.sh`

**Files:**
- Rename: `scripts/setup-chipyard-fork.sh` → `scripts/setup-chipyard.sh` (`git mv`, rồi viết lại)
- Test: `tests/test_setup_chipyard.sh` (tạo mới; bash thuần, docker và git giả qua PATH)

**Interfaces:**
- Produces: CLI `scripts/setup-chipyard.sh [--tag TAG] [--verify] [--dry-run] [CHIPYARD_DIR] [-- <cờ thêm cho build-setup.sh>]`. Mặc định `TAG=1.15.0`, `CHIPYARD_DIR=${CHIPYARD_FORK:-$HOME/tools/chipyard}`. `--dry-run` in lệnh `git clone`/`docker run` sẽ chạy rồi thoát 0, không chạy gì. Thoát 0 + in `== Xong` khi log có `Setup complete!` (và `VERIFY_OK` nếu `--verify`).

- [ ] **Step 1: Viết test** `tests/test_setup_chipyard.sh` với các ca (mỗi ca dùng thư mục tạm, `docker`/`git` giả ghi argv vào file):
  - `dry_run_clone`: dir chưa có, `--dry-run` → stdout chứa `git clone https://github.com/ucb-bar/chipyard.git` và `1.15.0`, thoát 0, dir vẫn chưa có.
  - `realpath_mount`: `HOME` là symlink tới thư mục thật, dir có `build-setup.sh` → dòng `docker run` chứa `-v <đường dẫn thật>:<đường dẫn thật>`, không chứa đường dẫn symlink.
  - `no_skip_default`: lệnh build-setup trong `docker run` là `./build-setup.sh riscv-tools` và **không** có `-s`; với `-- -s 6 -s 7` thì có đúng `-s 6 -s 7`.
  - `existing_dir_other_ref`: dir có sẵn, git giả trả `describe` ≠ `1.15.0` → stdout có `CẢNH BÁO`, git giả không nhận lệnh `checkout`/`clone`.
  - `failure_report`: docker giả ghi vào `<dir>/setup.log` dòng ` ========== BEGINNING STEP 8: Setting up FireMarshal ==========` rồi thoát 1 → script thoát ≠ 0, stderr chứa `bước 8` , `Setting up FireMarshal` và `-s 8`.
  - `jvm_cache_mount`: dòng `docker run` có `-v` cho `coursier`, `.sbt`, `.ivy2` dưới `${JVM_CACHE:-$HOME/chia_cache/jvm}` và `-e JAVA_TOOL_OPTIONS=` chứa `defaultReadTimeout=120000`.
- [ ] **Step 2: Chạy** `bash tests/test_setup_chipyard.sh` → FAIL (script chưa có).
- [ ] **Step 3: Viết script.** Giữ khung của script cũ (header tiếng Việt, `set -euo pipefail`, `docker run --rm -i --shm-size=10.24gb`, UID host = `ray`). Khác cũ:
  - Clone + `git -C DIR checkout -b local-<TAG> <TAG>`; sau clone **không** init submodule (build-setup bước 2 lo).
  - Bỏ `pip install conda-lock` và `-f` (1.15.0 tự tạo `.conda-lock-env`, không còn câu hỏi release).
  - INNER: `source /home/ray/anaconda3/etc/profile.d/conda.sh && conda activate base; cd DIR; ./build-setup.sh riscv-tools <EXTRA> 2>&1 | tee setup.log`; thành công khi `grep -q "Setup complete!" setup.log`.
  - Khi hỏng: lấy dòng `BEGINNING STEP` cuối trong `setup.log`, in ra stderr số bước, tên bước, đường dẫn log, và lệnh chạy lại: `scripts/setup-chipyard.sh DIR -- -s N` (với N=6 hoặc 7 gợi ý `-s 6 -s 7`; 8 hoặc 9 gợi ý `-s 8 -s 9`, vì 7 phụ thuộc 6 và 9 phụ thuộc 8). Thoát mã của docker.
  - Mount cache JVM (`mkdir -p` trước) và `JAVA_TOOL_OPTIONS="-Dsun.net.client.defaultConnectTimeout=30000 -Dsun.net.client.defaultReadTimeout=120000"` (cùng giá trị template).
  - `--verify` giữ như cũ (`RocketConfig`, `rv64ui-p-simple`, `VERIFY_OK`).
  - Header ghi: chạy trong tmux, nhiều giờ, cần vài chục GB; "chưa chạy thật trên 1.15.0".
- [ ] **Step 4: Chạy** `bash tests/test_setup_chipyard.sh` → tất cả PASS; `bash -n scripts/setup-chipyard.sh`; `shellcheck` nếu có (không có thì ghi vào commit message).
- [ ] **Step 5: Commit** `feat(scripts): setup-chipyard.sh dựng Chipyard gốc 1.15.0, đủ 11 bước`.

### Task 2: `MinimalSoCConfig` và `install-chipyard-config.sh` theo 1.15.0

**Files:**
- Modify: `chipyard/MinimalSoCConfig.scala`
- Modify: `scripts/install-chipyard-config.sh` (mặc định dir, danh sách mẫu grep)
- Modify: `docs/chipyard-minimal-soc.md`, `docs/minimal-soc-config-spec.md` (bảng mixin, trạng thái)

**Interfaces:**
- Consumes: không.
- Produces: `class chipyard.MinimalSoCConfig` (tên không đổi, nhánh project kế thừa theo tên này).

- [ ] **Step 1: Dựng cây kiểm** trong scratchpad: shallow clone `ucb-bar/chipyard` tag `1.15.0` + chỉ các submodule `generators/rocket-chip`, `generators/rocket-chip-inclusive-cache`, `generators/testchipip` (`git submodule update --init --depth 1 <paths>`).
- [ ] **Step 2: Chạy script cũ** `scripts/install-chipyard-config.sh <cây kiểm>` → mong đợi CẢNH BÁO (mẫu cũ của 1.10 như `fastLoadWord`/`WithNBigCores` sai package…), ghi lại kết quả.
- [ ] **Step 3: Sửa `MinimalSoCConfig.scala`** đúng như spec mục 9 (thêm `testchipip.soc.WithNoScratchpads`, `freechips.rocketchip.rocket.WithNBigCores(1)`), comment đổi "Chipyard 1.15.0". Đặt `WithNoScratchpads` theo vị trí `TinyRocketConfig` 1.15.0 dùng (đầu chuỗi). So `WithNBigCores` 1.15.0 với 1.10.0 (L1 set/way, FPU, mulDiv) và ghi khác biệt vào comment + `docs/minimal-soc-config-spec.md`.
- [ ] **Step 4: Sửa `install-chipyard-config.sh`**: mặc định `~/tools/chipyard`; mẫu grep = đúng các lớp `MinimalSoCConfig` dùng: `class WithNoScratchpads`, `class WithInclusiveCache`, `class WithNBanks`, `class WithNBigCores`, `class AbstractConfig`. Bỏ dòng "Chưa chạy thật" cũ, ghi "kiểm trên cây 1.15.0 trong sandbox".
- [ ] **Step 5: Chạy** script trên cây kiểm → `MIXIN_OK`, file được copy vào `generators/chipyard/src/main/scala/config/`.
- [ ] **Step 6: Sửa 2 docs**: bảng mixin theo 1.15.0, ghi scratchpad 64 KiB của `AbstractConfig` 1.15 và cách bỏ, tiêu chí kiểm thêm "không có scratchpad"; trạng thái "kiểm chứng trên 1.10 (09/11), chưa trên 1.15.0". Ghi lưu ý `hello.riscv` có thể cần scratchpad (spec mục 9).
- [ ] **Step 7: Commit** `feat(chipyard): MinimalSoCConfig theo Chipyard 1.15.0`.

### Task 3: `chia-up-blocks.py` và template

**Files:**
- Modify: `scripts/chia-up-blocks.py:26,59,73`
- Modify: `configs/cluster-blocks.template.yaml` (comment đầu file, comment khối chipyard/vivado)

- [ ] **Step 1: Chạy trước** `CHIPYARD_FORK= HOME=<tmp rỗng> python3 scripts/chia-up-blocks.py --dry-run` → ghi lại thông báo hiện tại (trỏ `RISCVTutorial`, `setup-chipyard-fork.sh`).
- [ ] **Step 2: Sửa**: mặc định `Path.home()/"tools"/"chipyard"`; mọi chuỗi `setup-chipyard-fork.sh` → `setup-chipyard.sh`; "fork" trong thông báo → "Chipyard". Template: `CHIPYARD_FORK` mặc định `~/tools/chipyard`, bảng trạng thái khối chipyard/vivado "chưa kiểm chứng trên 1.15.0".
- [ ] **Step 3: Chạy lại** lệnh Step 1 → thông báo lỗi chứa `tools/chipyard/env.sh` và `setup-chipyard.sh`; `python3 -m py_compile scripts/chia-up-blocks.py`; `grep -rn RISCVTutorial scripts configs` → rỗng.
- [ ] **Step 4: Commit** `refactor: mặc định CHIPYARD_FORK = ~/tools/chipyard`.

### Task 4: `docs/reinstall.md`

**Files:**
- Create: `docs/reinstall.md`

- [ ] **Step 1: Viết** 9 bước 0–8 đúng spec mục 10, mỗi bước: mục đích một dòng, khối `bash` riêng mỗi lệnh, "Xong khi:", "Gửi lại:". Yêu cầu riêng:
  - Bước 0 chỉ đọc; bước 2 xoá từng đường dẫn tường minh (`rm -rf ~/tools/RISCVTutorial` …), không glob trong `$HOME`; xoá image bằng tên cụ thể `chia-orfs:latest` + liệt kê `docker images --format` lọc tên không có `ghcr.io/` để người dùng tự chọn; `mv ~/CHIA-workflow ~/archive/CHIA-workflow-$(date +%Y%m%d)`.
  - Bước 4: `conda create -n chia_env python=3.10.19`, `conda env config vars set PYTHONNOUSERSITE=1 -n chia_env`, kích hoạt lại, `pip install chialoops==2.0.1`; Xong khi `python -V` = 3.10.19 và `ray --version` = 2.54.0, `python -c "import site;print(site.ENABLE_USER_SITE)"` = `False`.
  - Bước 5: chạy trong tmux, nhánh hỏng ở 6–9 theo spec mục 10.
  - Ghi đầu file: giữ gì (spec mục 6), trạng thái "chưa chạy thật".
- [ ] **Step 2: Kiểm**: mọi script/đường dẫn nhắc trong file tồn tại trong repo (`grep -o 'scripts/[a-z-]*\.\(sh\|py\)' docs/reinstall.md | sort -u | xargs ls`).
- [ ] **Step 3: Commit** `docs: reinstall.md — cài lại CHIA từ đầu với Chipyard 1.15.0`.

### Task 5: Cập nhật docs chung, README, CLAUDE.md

**Files:**
- Modify: `docs/cluster-blocks.md`, `docs/setup-bazzite.md` (mục 7), `README.md`, `CLAUDE.md`

- [ ] **Step 1: `docs/cluster-blocks.md`**: sơ đồ `~/tools` (`chipyard/`), mục 1 dùng `setup-chipyard.sh` (link `reinstall.md`), lệnh `chipyard-dev` dùng `$(realpath ~/tools/chipyard)`, bảng trạng thái "chưa kiểm chứng trên 1.15.0"; bảng lỗi: thêm cột/ghi chú "chỉ 1.10" cho dòng `conda-lock`, `-f`, `TutorialStarterConfig`.
- [ ] **Step 2: `docs/setup-bazzite.md` mục 7**: thêm một câu trỏ tới `docs/reinstall.md` / `setup-chipyard.sh` là cách chính; không viết lại.
- [ ] **Step 3: `README.md`**: tên script, link `docs/reinstall.md`, mục Chipyard nói 1.15.0.
- [ ] **Step 4: `CLAUDE.md`**: mục Máy người dùng (bố cục `~/tools/chipyard`, kế hoạch dựng lại 14/11), Đã/Chưa kiểm chứng (các khối chuyển "chưa kiểm chứng trên 1.15.0"), lỗi số 6 ghi "riêng 1.10", Quy ước nhánh: hai nhánh project viết cho 1.10 có thể không build trên 1.15.0, Việc tiếp theo: chạy `docs/reinstall.md`.
- [ ] **Step 5: Kiểm**: `grep -rn "RISCVTutorial\|setup-chipyard-fork" --exclude-dir=.git --exclude-dir=superpowers .` → chỉ còn các dòng lịch sử có chủ đích (CLAUDE.md phần đã kiểm chứng, bảng lỗi), mỗi dòng còn lại đã được đọc qua.
- [ ] **Step 6: Commit + push** `docs: chuyển tài liệu sang Chipyard gốc 1.15.0`; `git push -u origin setup`.
