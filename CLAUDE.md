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
- pip từng lấy `anyio`/`httpx`/`httpcore`/`h11` từ `~/.local` (user site). `chia_env` tạo lại 14/11/2026 với
  `PYTHONNOUSERSITE=1` (đã xác nhận: `site.ENABLE_USER_SITE` = `False`).
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
- **14/11/2026: bỏ fork RISCVTutorial (Chipyard 1.10.0), chuyển sang Chipyard gốc 1.15.0** và giữ cố định ở bản đó
  (đồng bộ tiếp là việc riêng). Dựng lại toàn bộ CHIA từ đầu theo `docs/reinstall.md`: **xong cả 8 bước**, IP LAN lúc
  đó `192.168.1.7`. `build-setup.sh` chạy **đủ 11 bước** (bước 9 FireMarshal cần `setup-chipyard.sh --privileged`).
  Mất 8 lần chạy bước 5; lỗi và cách gỡ ghi trong bước 5 của `reinstall.md`: mạng conda, hai container dựng cùng lúc
  (script giờ gắn nhãn và từ chối chạy trùng), `ibm/rocc-software` bị xoá (script trỏ sang bản sao `ucb-bar`), dừng
  giữa bước 6 để lại submodule FireSim tải dở hoặc chưa checkout (`git submodule status` không thấy), FireSim cũng in
  `Setup complete!` (script chỉ tin dòng đứng sau `BEGINNING STEP` cuối), FireMarshal `mount -o loop` cần
  `--privileged` + `mknod` loop0–7.
- Trước khi xoá fork đã lưu: `~/archive/RISCVTutorial-20261114/` (bundle + patch `minsoc-fmax`: repo gốc 16,
  `rocket-chip` 11, `sifive-cache` 8 commit; `submodule-heads.txt`; checkpoint Vivado `minsoc-vivado` 2.3 GB),
  `~/archive/ORFS-minsoc-20261114/` (`minsoc_*`, 2.8 GB), `~/CHIA-workflow` cũ ở `~/archive/CHIA-workflow-20261114`.
- Flow `fpga/` Arty A7-100T cần board file Digilent trong Vivado và `--net=host` (thiếu thì Vivado 2025.1 sập ở kiểm
  license qua libudev sau `Routing Is Done`; container của CHIA luôn có), xem `docs/cluster-blocks.md` mục 4.

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
- `python scripts/chia-up-blocks.py --down` (tự dò `THIS_MACHINE`; gọi thẳng `chia down` khi thiếu biến này thì `Head:` rỗng,
  mọi `scoped ray stop` báo `psutil` và không xoá gì): kiểm chứng 15/11/2026, 4 container + Ray head `ok=True`, `docker ps` rỗng.

**Đã chạy thật trên Chipyard 1.15.0** (14/11/2026; các mục có fork ở trên là với 1.10.0): toàn bộ `docs/reinstall.md`
(bước 0–8): `scripts/setup-chipyard.sh --verify` (`VERIFY_OK`; bước 8–9 của `build-setup.sh` chạy riêng sau với `--privileged`),
`install-chipyard-config.sh` + `MinimalSoCConfig` bản 1.15.0, ORFS, cluster theo khối đủ 5 khối (`KẾT QUẢ: OK`).
Sau đó: bước 9 FireMarshal (`--privileged`), flow `fpga/` Arty A7-100T ra bitstream (container tay cùng image/mount
với khối vivado, `--net=host`), rồi qua job CHIA: `examples/fpga_smoke.py --clean` trên khối `vivado` ra `rc: 0`,
11,1 phút, `.bit` 3 825 916 byte, WNS 0,129 / WHS −0,063 ns, `KẾT QUẢ: OK`; `chia down` sạch. Chưa có trên 1.15.0:
các project cũ.

**Đã chạy thật 15/11/2026** (project `soc-fmax-sky130`, M1): `scripts/fakeram_sky130.py` qua ORFS sky130hd tới place
(10 macro của `MinimalSoCConfig`: Yosys, macro placement, PDN, OpenSTA). Kênh hẹp giữa macro và mép core cần cắt row
trong halo khi tapcell (`PDN-0179`, xem `docs/fakeram-sky130.md`). `chia-up-blocks.py --verilator-slots 4` (4 sim song
song) và `ASIC_MEM=20g`.

**Chưa chạy thử** (viết từ docs và mã nguồn CHIA):
- `configs/cluster-lan.yaml`, `prepare-remote-worker.sh`, `check-remote-worker.sh`.
- `examples/rtl_agent_loop.py`: mới qua `py_compile`, API đã đối chiếu với mã nguồn CHIA 2.0.1.
- Phần linger trong `check-bazzite-env.sh` (thêm sau lần chạy script).
- Tailscale/`tailnet:`, phương án Podman rootless.

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
  trong fork, nhánh `minsoc-fmax/agent` của repo gốc + submodule `rocket-chip`, `sifive-cache`) và `project/soc-fmax`
  (project cũ, xong 06/11/2026, lịch sử git tách khỏi `setup`; giữ nguyên).
  **Tất cả viết cho Chipyard 1.10.0** (fork RISCVTutorial): sau khi chuyển sang 1.15.0 mã Scala của chúng có thể
  không build được (mixin đổi package, scratchpad). Chưa sửa; khi quay lại project nào thì `git merge origin/setup`
  và chuyển nó sang 1.15.0 trước.
- `project/soc-fmax-sky130` (mở 15/11/2026, Chipyard 1.15.0): loop agent tối ưu Fmax cả SoC `MinimalSoCConfig`
  (`DigitalTop`) trên sky130hd đo bằng ORFS sau place, fakeram, bậc chu kỳ 10 → 5 ns; chi tiết trong nhánh đó.

## Project trên nhánh này: soc-fmax (`projects/soc-fmax/`)

- Đọc `projects/soc-fmax/README.md` (mục tiêu, Pha, cách đo, mốc M1–M6). Người dùng duyệt 15/11/2026: tên nhánh
  `project/soc-fmax-sky130` (vì `project/soc-fmax` là project cũ), Chipyard 1.15.0 ở `~/tools/chipyard`, fakeram bằng
  script riêng (`setup`: `scripts/fakeram_sky130.py`), bộ lọc mức 1 là `make synth` của ORFS (không dùng script `v3`
  riêng; `v3` chỉ để dành cho `synth_ab` của agent ở M4).
- Trạng thái: **M1 xong 15/11/2026** (chạy thật): gate ISA 106/107 (`rv64ui-p-ma_data` hỏng sẵn) + 8 benchmark; ORFS
  sky130hd 10 ns sau place **65,42 MHz** (WNS −5,285, đường găng FPU `dfma`), logic 2,23 mm², place 8,5 phút, RAM bước
  ORFS đỉnh 2,4 GB; chạy lại toàn bộ từ cache. Sửa khi chạy: clock phụ bất đồng bộ cho serial TL/JTAG, `tapcell.tcl`
  riêng (PDN-0179), slack vùng trên mọi endpoint (bỏ chân clock macro). Chi tiết ở mục Trạng thái kiểm chứng của README.
  M2 (duyệt 15/11/2026 "như đề xuất"): `func_check.py` (vết commit `+verbose`, taint bộ đếm, dừng ở `tohost`),
  `policy.py`, `loop.py` (agent bypass, patch áp lên `~/tools/chipyard` rồi hoàn nguyên), `soc_fmax.py --noise`; chạy
  thật 15/11/2026: func_check đạt (sửa: bỏ so ghi vào x0, Rocket in rác), mm chỉ phủ 17 % (dừng ở nhánh phụ thuộc bộ
  đếm); nhiễu place Fmax 64,44..65,47 MHz, ORFS tất định, ngưỡng nhận **±1,546 MHz** / điểm họ ±6,356 ns. `m2-loop3` (bypass, 15/11/2026): rỗng / RTL trùng (build thật sau `git apply` vào `rocket-chip`) / build lỗi đúng như dự kiến, cây sạch sau job. **M2 xong.** Resource của khối claude là `claude_creds`. M3 (duyệt 15/11/2026 "như đề xuất" + chỉ giữ kết quả ORFS tốt nhất sau mỗi job): `agent.py` (Claude Code trên khối claude, mount Chipyard chỉ đọc, sửa bản chép rồi diff), `loop.py --phase sim` → `candidates.json`, sửa build tối đa 2 lần, `prune_orfs` cuối job. **M3 xong** (`m3-b` chạy thật 15/11/2026: 4 ứng viên / 5 vòng, FPU `dfma` retiming + 3 patch L2 Scheduler/ListBuffer; patch thêm Queue hỏng func vì so vết thấy giá trị `mcycle` đã lưu được load lại). M4 (duyệt 15/11/2026): pha asic đo ORFS mọi vòng, cuối đợt chỉ gate vòng đo tốt nhất, hỏng thì vòng kế (`--batch best`); rồi thay bằng đợt cộng dồn (duyệt 15/11/2026): mỗi vòng tới floorplan (ngưỡng nhiễu floorplan), gate nhanh ISA, cộng dồn trong đợt, cuối đợt place + gate đủ, hỏng thì lùi (`finish_chain`). Đã bỏ `--batch each/best` và bộ lọc synth: `loop.py` chỉ còn `--phase sim` và `--phase asic` (cộng dồn); mới chạy mock. Người dùng chọn chạy lại từ đầu (xoá `~/chia_cache/soc-fmax`, `~/soc-fmax-runs`, thư mục ORFS `soc_fmax`) rồi `m4-a`. **Chạy thật 16/11/2026:** `m2-noise` cho lại đúng baseline (65,423 MHz, ±1,546); `m4-a` (1,5 giờ) cộng dồn r1 `dfma` retiming + r2 hàng đợi flow/putbuffer L2 + r5 (TLBuffer `eagerQueue`, `ToAXI4`, peek L2), nhận ngay: place **86,86 MHz** (WNS −1,513, logic +2,5 %, cycle 1,0000), đường găng sang `sfma`. Floorplan bão hoà ~100 MHz ở 10 ns (sửa timing dừng ở slack 0) nên r3, r4 ra trong nhiễu; chưa có tự xuống bậc. Người dùng chọn **hướng B** (16/11/2026): mỗi vòng tới place (ngưỡng ±1,546, bỏ ngưỡng floorplan và `--fp-noise`, `--max-backtrack`), gate nhanh ISA rồi cộng dồn, cuối đợt gate đủ theo Fmax place giảm dần, bản đầu tiên qua thì nhận; `summary.md` mỗi job; ORFS chỉ giữ variant bản tốt nhất (xoá cả bản cũ khi có bản tốt hơn); **`m4-b` chạy thật 16/11/2026** (2,1 giờ): r6 `sfma`, r7 `IBuf`, r8 L2 `SourceD` cộng dồn, r9/r10 place kém hơn; nhận ngay r8: place **101,98 MHz, WNS +0,194 → đạt bậc 10 ns** (+55,9 % so với baseline, cycle 1,0000, logic +2,3 %). Place ở 10 ns giờ cũng bão hoà; dao động place khi đổi netlist (0,36–0,68 ns ở vùng không sửa) lớn hơn nhiễu baseline; ngưỡng ±1,546 MHz ≈ ±0,367 ns. Người dùng duyệt (16/11/2026): ngưỡng theo **ns chu kỳ đạt được** (±0,367 ns từ baseline), mỗi khi xuống bậc đo lại bản tốt nhất + 3 lần nhiễu ở bậc mới; vòng có WNS ≥ 0 → kết thúc đợt sớm, bản nhận đạt bậc → xuống bậc kế trong job (`--on-met next`, `stop` để dừng), giữ variant ORFS bậc đã đạt; M5 làm khi đạt 5 ns. Mới chạy mock, chờ `m4-c`. Độ tin của Fmax place: người dùng chọn (16/11/2026) chạy `route_check.py` một lần sau `m4-c` cho patch tốt nhất có timing tốt (place → CTS → route, `report.tcl` propagated clock + OpenRCX; mới mock), rồi loop tiếp tới 5 ns. **`m4-c` chạy thật 16/11/2026** (2,7 giờ): tự xuống 9 ns đúng; r8 ở 9 ns place 96,26 MHz, 3 lần nhiễu 90,67..105,13 MHz → ngưỡng ±2,275 ns chặn mọi vòng (r11/r13/r15 sửa L2 ra 103,8–104,1 MHz, TNS −6..−118 so với −1316, đường găng sang `dfma` −0,61; vẫn trong nhiễu); giữ r8, chưa gate. Người dùng chọn **phương án A** (16/11/2026): một lần place để lọc, vòng hứa hẹn (tốt hơn trung vị
bản so sánh) qua gate nhanh thì place thêm 3 biến thể nhiễu, nhận khi trung vị 4 lần tốt lên ≥ `--repeat-gain` 0,367 ns;
mới mock (`m6-1..3`), chờ `m4-d`; **`route-r8` chạy thật 16/11/2026:** CTS 2,5 phút, 100,66 MHz, WNS +0,065 (chậm hơn place 0,129 ns, đường găng sang `dfma`); route lỗi ở `5_1_grt` sau 19 phút: GRT-0116 tràn cục bộ met1/met2 (dùng 22 % tài nguyên, tổng tràn 38 557); nghẽn 99 % ở L2 `bankedStore` (góc trên trái, quanh `cc_banks`?); `--allow-congestion` dùng `GLOBAL_ROUTE_ARGS` có `-allow_congestion` (ORFS không có `ALLOW_CONGESTION`); **`route-r8b` chạy thật**: global route qua nhưng tràn y hệt, detailed route sau ~7 giờ còn 12 705 vi phạm (vòng 11, giảm 2–8 %/vòng), người dùng dừng tay; DRC còn lại chủ yếu Short met4/met5 giữa net `bankedStore` và `VDD`/`VSS` (PDN quanh `cc_banks`?). **Người dùng (17/11/2026): tạm ngưng đo sau route, tối ưu tiếp tới 5 ns**; sửa floorplan/PDN để M5. Mỗi variant ORFS tới place chiếm 2,3 GB `flow/results`. Patch giả sinh lúc chạy (không có thư mục `bypass/`).
- `projects/soc-fmax/report.md` (tạo 16/11/2026, người đọc: thầy/hội đồng): báo cáo học thuật 3–5 trang, trọng tâm kết quả Fmax
  và phương pháp loop agent; **cập nhật cùng README sau mỗi kết quả chạy thật**.
- Claude không viết Chisel: mọi sửa thiết kế do agent trong loop làm (từ M3).

## Việc tiếp theo (gợi ý)

1. (Xong 14/11/2026) `docs/reinstall.md` chạy hết trên Chipyard 1.15.0.
2. (Xong 14/11/2026) FireMarshal bước 9 (`--privileged`), flow `fpga/` ra bitstream Arty A7-100T (tay và qua job CHIA).
3. Mở project mới theo **Quy ước nhánh**, dùng cluster theo khối (`docs/cluster-blocks.md`), config kế thừa
   `chipyard.MinimalSoCConfig` (nền mặc định cho mọi project).
4. Chuyển `project/ascon-secureboot`, `project/rocket-fmax` sang Chipyard 1.15.0 khi quay lại.
5. Chạy thử `examples/rtl_agent_loop.py` (đang trỏ `/home/ray/chipyard` của image; muốn dùng Chipyard trên host thì
   đổi sang `~/tools/chipyard` và resource `chipyard` của cluster theo khối).
6. Khi có máy thứ hai: làm theo `docs/usage-remote.md`, kiểm chứng `cluster-lan.yaml`.
