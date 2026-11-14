# CLAUDE.md

Bối cảnh cho các session Claude Code làm việc trong repo này. Cập nhật file này khi
trạng thái thay đổi (thứ gì đã chạy được, việc gì xong).

## Mục đích

Ghi chú, config và script để chạy [CHIA](https://github.com/ucb-bar/chia) trên Bazzite.
CHIA là framework co-design phần cứng/phần mềm của UC Berkeley, dùng agent AI, chạy
trên Ray và Docker. Tài liệu chính thức nằm ở <https://docs.chialoops.ai/en/latest/>.
Từ sandbox của Claude Code thường không resolve được domain này, nên đọc thẳng
`docs/*.rst` trong repo `ucb-bar/chia`. Paper: arXiv:2606.27350.

## Máy của người dùng (head)

- OS: `ghcr.io/ublue-os/bazzite-dx-nvidia:stable`. Đã chạy `ujust dx-group`; `docker`
  dùng được không cần sudo.
- User `dingus`, home `/var/home/dingus`. IP LAN từng là `192.168.1.3`, có lần là
  `10.232.8.80`. `THIS_MACHINE` tự dò nên không phải sửa config.
- 20 CPU, 23 GB RAM (dưới mức 32 GB khuyến nghị nhưng vẫn build được RocketConfig với
  `make -j 32`), 1 GPU NVIDIA, khoảng 259 GB trống ở `/var`.
- sshd đã bật. SSH không mật khẩu vào chính máy đã chạy được. Đã chạy
  `loginctl enable-linger`.
- Miniforge ở `~/miniforge3`. Env `chia_env`: Python 3.10.19, Ray 2.54.0,
  `chialoops` 2.0.1 (cài từ PyPI).
- pip từng lấy `anyio`/`httpx`/`httpcore`/`h11` từ `~/.local` (user site). Đã khuyên chạy
  `conda env config vars set PYTHONNOUSERSITE=1 -n chia_env` rồi `pip install chialoops`
  lại. **Chưa xác nhận người dùng đã làm hay chưa.**
- Sau khi cài lại (08/11/2026): `~/CHIA-workflow` là bản `git clone` thật (nhánh `setup`), cập nhật
  bằng `git pull`. `chialoops` cài từ PyPI, không còn `~/work/chia`. Người dùng **không `git push`
  được** (GitHub đăng nhập bằng Google): muốn đưa file từ máy họ lên repo thì dùng GitHub web,
  `gh auth login`, hoặc họ dán nội dung để session dựng lại.
- Chưa có máy remote nào.
- Vivado 2025.1 cài ở `~/tools/Xilinx/2025.1` (bố cục `2025.1/Vivado/settings64.sh`; host in `v2025.1`).
- **08/11/2026: đã gỡ sạch CHIA** (`chia_env`, `~/work/chia`, `~/chia_cache`, image `chia-*`, fork cũ;
  `~/CHIA-workflow` cũ chuyển vào `~/archive/`) để cài lại từ đầu và kiểm chứng tài liệu. Giữ
  `~/tools/Xilinx`, `~/.claude`, Miniforge và thiết lập hệ thống (docker, sshd, linger, `.bashrc`).
  Cache của `rocket-fmax` đã mất.
- Bố cục: mọi công cụ trong `~/tools`: `chipyard/` (Chipyard gốc `ucb-bar/chipyard`, ghim tag **1.15.0**, nhánh
  cục bộ `local-1.15.0`), `OpenROAD-flow-scripts/` (chép từ image `chia-orfs`), `Xilinx/2025.1/`. Sửa tay trên host,
  build trong container (`docs/cluster-blocks.md`).
- **14/11/2026: quyết định bỏ fork RISCVTutorial (Chipyard 1.10.0), chuyển sang Chipyard gốc 1.15.0** để đồng bộ
  với bản gốc, rồi giữ cố định ở bản đó (đồng bộ tiếp là việc riêng). Dựng lại toàn bộ CHIA từ đầu theo
  `docs/reinstall.md` (giữ Vivado, `~/.claude`, Miniforge, image `ghcr.io/ucb-bar/chia-*`), `build-setup.sh` chạy
  **đủ 11 bước**; bước 6–9 (FireSim/FireMarshal) hỏng trong container thì ghi lỗi, chạy lại bỏ các bước đó, không
  mở thêm quyền container. Thiết kế: `docs/superpowers/specs/2026-11-14-chipyard-upstream-design.md`.
  **Người dùng chưa chạy `docs/reinstall.md`.** Cho tới khi có log, máy họ vẫn có thể còn bố cục cũ (`RISCVTutorial`).

## Đã kiểm chứng và chưa kiểm chứng

**Đã chạy thật trên máy người dùng:**
- `check-bazzite-env.sh`: pass hết, chỉ có WARN về RAM.
- `chia up` cluster 1 máy, `ray status` thấy đủ node.
- Hello World step 1–4. Step 4 cho ra `Hello World (#4) from instruction 1000! again`.
- Fork RISCVTutorial gắn vào CHIA (08/11/2026): dựng bằng tay trong container, build Verilator
  `RocketConfig` + chạy `rv64ui-p-simple` (`EXIT=0`), rồi `ChiselBuildNode` qua CHIA ra
  `SUCCESS: True` (với `clean=False`). Đây là khối `chipyard` của `docs/cluster-blocks.md`.
- Cài lại từ đầu (08/11/2026): `setup-chipyard-fork.sh --clone ... --verify` vào `~/tools/RISCVTutorial`,
  `chia-up-blocks.py` (chipyard + verilator + claude), `blocks_smoke.py --clean` ra
  `SUCCESS=True`, `KẾT QUẢ: OK` (sau 2 lỗi: `${VAR}` trong `worker_setup_commands`, `/home` → `/var/home`).
- `docker/orfs-ray.Dockerfile` build được (Ray trong Python 3.10 riêng `/opt/raypy`; `openroad/orfs` dùng 3.14),
  `setup-orfs.sh --verify` chép 1.44 GB ra `~/tools/OpenROAD-flow-scripts`, flow `nangate45/gcd` ra `VERIFY_OK`.
- `chia-up-blocks.py --asic` + `blocks_smoke.py --no-build`: khối asic thấy Yosys 0.68, `flow_writable: True`
  (`openroad -version` in `unknown` vì image build không có git sha, không phải lỗi).
- `--fpga --asic`: cả 5 khối lên cùng lúc, `blocks_smoke.py` ra `KẾT QUẢ: OK`. Khối vivado in
  `Vivado v2024.2` vì image `chia-chisel-build` có sẵn `/usr/local/bin/vivado`; đã sửa PATH. Sau đó lộ lỗi thật: `settings64.sh` ghi cứng `/var/home/...`, nên Xilinx
  phải mount đúng đường dẫn thật (`XILINX_DIR`). Chạy lại (09/11/2026): `which` ra
  `/var/home/dingus/tools/Xilinx/2025.1/Vivado/bin/vivado`, `vivado v2025.1`, `KẾT QUẢ: OK`.
- `MinimalSoCConfig` (`chipyard/`, `scripts/install-chipyard-config.sh`, `docs/chipyard-minimal-soc.md`,
  09/11/2026): SoC nền 1 Rocket + L2 128 KiB trên fork. Build Verilator, `rv64ui-p-simple` và `hello.riscv`
  `EXIT=0`, L2 131072 byte, không có GPIO/SPI/I2C/PWM/scratchpad. Project mới kế thừa config này.
- `chia down` không xoá được container asic (`scoped ray stop ... psutil`): `python3` trên PATH của image là
  `/opt/orfs-python` (3.14), không có `psutil`. Dockerfile đã cài `psutil`; kiểm chứng 09/11/2026:
  `ok=True`, container bị xoá sạch.

**Chưa chạy thử trên Chipyard 1.15.0** (14/11/2026, các mục "đã chạy thật" có fork ở trên là với 1.10.0):
- `scripts/setup-chipyard.sh` (đổi tên từ `setup-chipyard-fork.sh`), `docs/reinstall.md`, khối `chipyard`/`vivado`
  trên 1.15.0, `MinimalSoCConfig` bản 1.15.0. Trong sandbox mới kiểm: test bash của hai script
  (`tests/test_setup_chipyard.sh`, `tests/test_install_chipyard_config.sh`) và `install-chipyard-config.sh` in
  `MIXIN_OK` trên cây 1.15.0 thật (shallow).

**Chưa chạy thử** (viết từ docs và mã nguồn CHIA):
- `configs/cluster-lan.yaml`, `prepare-remote-worker.sh`, `check-remote-worker.sh`.
- `examples/rtl_agent_loop.py`: mới qua `py_compile`, API đã đối chiếu với mã nguồn CHIA 2.0.1.
- Phần linger trong `check-bazzite-env.sh` (thêm sau lần chạy script).
- Tailscale/`tailnet:`, phương án Podman rootless.
- Flow `fpga/` của Chipyard qua khối `vivado` (mới kiểm `vivado -version`).

## Các lỗi đã gặp trên Bazzite và cách sửa

1. **Ray head chết ngay sau `chia up`**, dù CHIA vẫn in `Head node ready`. Worker báo
   `Failed to connect to GCS at 192.168.1.3:6379`. Nguyên nhân: systemd-logind tắt mọi
   tiến trình của phiên SSH khi phiên đóng. Sửa: chạy `loginctl enable-linger $USER`, rồi
   thêm `systemd-run --user --scope --quiet` trước `ray start --head ...` trong
   `head_start_ray_commands`. Worker trong container thì không bị ảnh hưởng.
2. **YAML lỗi `expected ',' or ']', but got '{'`** ở `cluster-opencode.yaml` gốc vì
   `compatible_ips: [${THIS_MACHINE}]` thiếu dấu nháy. Sửa: gộp worker opencode vào
   `cluster.yaml` và viết `compatible_ips: ["${THIS_MACHINE}"]`.
3. **`OpenCodeLLM` báo `Timeout on attempt 1/3`**: `timeout_seconds` mặc định 600 giây
   cho mỗi lượt, mà agent tự build thử. Tăng `timeout_seconds`, hoặc dặn agent không build.
4. **`Ctrl+C` không dừng job**. Dùng `chia job stop <id>`.
5. Thêm `--node-ip-address=<IP>` và `--disable-usage-stats` vào lệnh khởi động Ray head
   để khỏi phụ thuộc IP tự dò và câu hỏi tương tác.

6. **Fork Chipyard 1.10 trong image CHIA** (riêng 1.10; Chipyard gốc 1.15.0 tự tạo env conda-lock và đứng
   trên tag release nên không cần `conda-lock==1.4.0` hay `-f`): thiếu `conda-lock` (cài `conda-lock==1.4.0`);
   `build-setup.sh` hỏi `y/n` mãi khi không có bàn phím vì fork không đứng trên tag release (dùng
   `-f`); `env.sh` của fork gọi `conda activate` nên phải `source .../anaconda3/etc/profile.d/conda.sh`
   trước. `/home` là symlink tới `/var/home`: fork ghi cứng đường dẫn thật, mount phải dùng `realpath`.
   `TutorialStarterConfig` cố ý không có core, build lỗi; thử bằng `RocketConfig`.
   Chipyard 1.10: `WithInclusiveCache` không có tham số `nBanks` (comment trong `TutorialConfigs.scala`
   ghi sai), dùng `WithNBanks(n)`. Thư mục build là `generated-src/chipyard.harness.TestHarness.<Config>`.
7. **sbt/coursier treo vô hạn khi build** (java 0% CPU, `.part` đứng yên): socket TCP chết tới Maven Central,
   coursier không có read timeout; cache coursier không mount nên mất sau mỗi `chia up`. Gỡ tạm: `sudo ss -K`
   socket của java trên host. Sửa gốc (12/11/2026, **đã kiểm chứng 13/11**: cache coursier 234 MB sau build, không treo): mount `JVM_CACHE` + `JAVA_TOOL_OPTIONS`
   timeout trong khối chipyard (`docs/cluster-blocks.md`).

## Ghi chú kỹ thuật về CHIA (đã đối chiếu mã nguồn)

- CHIA luôn gọi lệnh `docker` (`DockerConfig.engine` cố định là `"docker"`). Lệnh chạy
  qua `ssh -A ... bash --login`. Container chạy với `--net=host --init`.
- `chia up` SSH vào `head_ip` ngay cả khi chỉ có 1 máy.
- Ở chế độ `tailnet:`, CHIA tự thêm `--node-ip-address` vào lệnh khởi động head, nên
  không được thêm thủ công.
- `ChiselBuildNode.build` khi lỗi **không raise** mà trả `BuildArtifact(success=False)`.
- `BashTool(name, work_dir, timeout_seconds=120, task_options=...)`. Tool đang chạy giữ
  resource cho tới khi gọi `.stop()`.
- Image Chipyard của CHIA dùng nhánh `chia_artifact` của `ucb-bar/chipyard` (= 1.14.0 + vá CHIA), Ubuntu 22.04.
- Chipyard 1.15.0 so với 1.10.0 (đối chiếu mã nguồn 14/11/2026): `WithNBigCores` chuyển sang package
  `freechips.rocketchip.rocket` (tham số giữ nguyên), `RocketConfig` dùng `WithNHugeCores`; `AbstractConfig` thêm
  scratchpad 64 KiB (bỏ bằng `testchipip.soc.WithNoScratchpads`); `tests/` build bằng CMake
  (`tests/build/hello.riscv`); `build-setup.sh` có bước 10 cài CIRCT, bước 1 thoát nếu `.conda-env` đã có (chạy
  lại phải `-s 1` và `source env.sh`; `setup-chipyard.sh` in sẵn lệnh), env conda-lock không ghim Python (lỗi
  `sre_parse` có thể quay lại, xem `docs/setup-bazzite.md` mục 7).
- Worker docker: CHIA gộp `worker_env_commands`, `setup_commands`, `worker_setup_commands` và
  `worker_start_ray_commands` thành MỘT script chạy qua `docker exec -i ... bash --login`
  (`chia/cluster/node_setup.py`). Biến môi trường đặt ở `worker_setup_commands` đi vào Ray worker;
  ghi vào `~/.profile` thì không (shell đã đọc `.profile` trước).
- Tên container thực tế là `container_name` + hậu tố `-<chỉ số>` (ví dụ `chia-riscvtut-dingus-0`).
- `num_workers: 0` thì node bị bỏ qua: dùng để tắt khối. `${VAR}` KHÔNG được thay trong `worker_setup_commands` (kiểm chứng 08/11/2026: Ray worker có `RISCV=None`), chỉ thay trong `run_options`; `chia-up-blocks.py` tự thay biến của template. Kể cả khi được thay, nhưng
  kết quả vẫn là chuỗi, nên không dùng được cho `num_workers` (so sánh với số sẽ lỗi).
- `ChiselBuildNode(chipyard_path, config, ...)` chỉ `cd <path>/sims/verilator && make -j N CONFIG=...`
  với môi trường của worker; mặc định `clean=True` chạy `make clean` (xoá cả `chipyard.jar`).
  Không phụ thuộc gì riêng của nhánh `chia_artifact`, nên fork Chipyard 1.10 dùng được.

Kiểm chứng khi chạy thật (project `rocket-fmax`, 25/10/2026):
- Khi bật profiler (`start_collector`), ObjectRef trả về từ `chia_remote` mà truyền thẳng vào
  một task khác sẽ đến nơi dưới dạng lớp bọc `_ProfiledResult` (lỗi kiểu
  `'_ProfiledResult' object has no attribute ...`). Chỉ `get()` mới gỡ lớp bọc, nên phải `get()`
  rồi truyền giá trị.
- Cache (`_chia_tag`) chỉ phân biệt theo tag, không theo tên hàm hay tham số, và chỉ được ghi khi
  `get()` kết quả. Nên đưa các tham số vào tag (ví dụ `baseline_synth_v2_p21p2`).
- Actor cache là detached và lưu đối tượng Python bằng pickle. Vì vậy module định nghĩa
  dataclass trả về phải import được lúc đọc lại: luôn `chia job submit --working-dir .` từ
  đúng thư mục project.
- Script worker chạy với `set -e`, nhưng lệnh hỏng ở giữa chuỗi `a && b && c` KHÔNG làm dừng script:
  `ray start` vẫn chạy với môi trường thiếu, lỗi bị che. Dùng `;` hoặc kiểm tra kết quả bằng probe.
- Ray kiểm phiên bản Python của worker khớp head tới **bản vá** (3.10.19 ≠ 3.10.22 là lỗi).
- `DockerConfig.pull_before_run: false` để dùng image tự build tại máy.
- `chia job logs -f` có thể ngắt kết nối mà job vẫn chạy; dừng job bằng `chia job stop <id>`.

## Quy ước làm việc

- Trả lời người dùng bằng **tiếng Việt**.
- Theo **Quy ước nhánh** ở dưới. **Không tạo PR** trừ khi người dùng yêu cầu.
- Trước khi tạo, xoá hoặc tách file tài liệu, **liệt kê file hiện có và file dự định
  thay đổi để người dùng duyệt**.
- Claude không chạy được lệnh trên máy người dùng: đưa lệnh cho người dùng chạy, rồi đọc
  log họ gửi lại.
- Ghi rõ phần nào đã kiểm chứng và phần nào chưa. Khi một thứ chạy được hoặc gặp lỗi
  mới, cập nhật docs và file này.

## Quy ước nhánh

| Nhánh | Chứa gì | Ghi chú |
|---|---|---|
| `main` | Bản ổn định | **Hạn chế đụng.** Chỉ nhận merge từ `setup` qua PR, khi người dùng yêu cầu. |
| `setup` | Tài liệu, config, script chung, `CLAUDE.md` | Sửa phần dùng chung cho mọi project ở đây. |
| `project/<tên>` | Một project, code nằm trong `projects/<tên>/` | Tách từ `setup`. Tên viết thường, nối bằng `-`. |

- Mở project mới: `git checkout -b project/<tên> origin/setup`, rồi tạo `projects/<tên>/`
  kèm `README.md` (mục tiêu, lệnh chạy, trạng thái kiểm chứng, mục **Pha**).
- Mọi project phần cứng theo khung `docs/workflow-phases.md`: **Simulate** (bắt buộc, chốt
  gate và ngân sách) rồi rẽ sang **FPGA** và/hoặc **ASIC** (hai nhánh song song, thay đổi
  chỉ dành cho một nhánh phải tắt được bằng config).
- Trong nhánh project, chỉ sửa `projects/<tên>/`, và thêm một mục ngắn về project vào
  `CLAUDE.md`. Nếu phát hiện điều gì dùng chung (lỗi Bazzite mới, ghi chú CHIA), sửa trên
  `setup` rồi `git merge origin/setup` vào nhánh project. Không sửa docs chung ngay trong
  nhánh project.
- Không rebase hay force-push các nhánh `setup` và `project/*`.
- Session Claude Code được giao nhánh `claude/*` thì vẫn push lên nhánh đó, nhưng phải
  checkout đúng nhánh `setup` hoặc `project/<tên>` làm gốc. Khi được người dùng cho phép,
  push luôn lên nhánh đích.
- Project hiện có: `project/ascon-secureboot` (RoCC Ascon-Hash256, secure boot; xong M1–M4),
  `project/rocket-fmax` (tối ưu Fmax RocketTile; chi tiết trong `CLAUDE.md` của nhánh đó).
  Ngoài ra còn `project/minsoc-fmax` (Fmax `MinimalSoCConfig` FPGA/ASIC, đang chạy tới 13/11/2026; kết quả agent nằm
  trong fork, nhánh `minsoc-fmax/agent` của repo gốc + submodule `rocket-chip`, `sifive-cache`) và `project/soc-fmax`.
  **Tất cả viết cho Chipyard 1.10.0** (fork RISCVTutorial): sau khi chuyển sang 1.15.0 mã Scala của chúng có thể
  không build được (mixin đổi package, scratchpad). Chưa sửa; khi quay lại project nào thì `git merge origin/setup`
  và chuyển nó sang 1.15.0 trước.

## Việc tiếp theo (gợi ý)

1. **Người dùng chạy `docs/reinstall.md`** (bước 0–8), gửi log từng bước. Cập nhật docs và file này theo kết
   quả: chuyển mục sang "đã kiểm chứng trên 1.15.0", ghi lỗi mới (nhất là bước 6–9 của `build-setup.sh`).
2. Nếu bước 6–9 phải bỏ: việc riêng để làm FireSim/FireMarshal chạy được (dựa trên log thật).
3. Mở project mới theo **Quy ước nhánh**, dùng cluster theo khối (`docs/cluster-blocks.md`), config kế thừa
   `chipyard.MinimalSoCConfig` (nền mặc định cho mọi project).
4. Chuyển `project/ascon-secureboot`, `project/rocket-fmax` sang Chipyard 1.15.0 khi quay lại.
5. Lần đầu dùng khối vivado thật: chạy flow `fpga/` của Chipyard ra bitstream, ghi lỗi vào docs.
6. Chạy thử `examples/rtl_agent_loop.py` (đang trỏ `/home/ray/chipyard` của image; muốn dùng Chipyard trên host thì
   đổi sang `~/tools/chipyard` và resource `chipyard` của cluster theo khối).
7. Khi có máy thứ hai: làm theo `docs/usage-remote.md`, kiểm chứng `cluster-lan.yaml`.
