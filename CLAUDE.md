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
- Đã clone: `~/work/chia` (repo CHIA), `~/chia-hello-world` (copy của
  `examples/hello-world`, trong đó `cluster.yaml` đã được sửa giống
  `configs/cluster-local.yaml`), `~/CHIA-workflow` (repo này).
  **Lưu ý (23/10/2026):** `git pull` trong `~/CHIA-workflow` báo `not a git repository`,
  tức thư mục này không phải bản git clone (có thể chép tay). Khi đưa code mới, phải kiểm
  người dùng đã thật sự lấy được bản mới chưa (ví dụ `grep` một dòng mới trong file).
  Người dùng **không `git push` được**: tài khoản GitHub đăng nhập bằng Google, không có
  mật khẩu. Muốn đưa file từ máy họ lên repo thì dùng GitHub web (Upload files), `gh auth
  login`, hoặc họ dán nội dung để session dựng lại và so `sha256sum`.
- Chưa có máy remote nào.

## Đã kiểm chứng và chưa kiểm chứng

**Đã chạy thật trên máy người dùng:**
- `check-bazzite-env.sh`: pass hết, chỉ có WARN về RAM.
- `chia up` cluster 1 máy, `ray status` thấy đủ node.
- Hello World step 1–4. Step 4 cho ra `Hello World (#4) from instruction 1000! again`.

**Chưa chạy thử** (viết từ docs và mã nguồn CHIA):
- `configs/cluster-lan.yaml`, `prepare-remote-worker.sh`, `check-remote-worker.sh`.
- `examples/rtl_agent_loop.py`: mới qua `py_compile`, API đã đối chiếu với mã nguồn CHIA 2.0.1.
- Phần linger trong `check-bazzite-env.sh` (thêm sau lần chạy script).
- Tailscale/`tailnet:`, worker Claude Code, phương án Podman rootless.

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

## Ghi chú kỹ thuật về CHIA (đã đối chiếu mã nguồn)

- CHIA luôn gọi lệnh `docker` (`DockerConfig.engine` cố định là `"docker"`). Lệnh chạy
  qua `ssh -A ... bash --login`. Container chạy với `--net=host --init`.
- `chia up` SSH vào `head_ip` ngay cả khi chỉ có 1 máy.
- Ở chế độ `tailnet:`, CHIA tự thêm `--node-ip-address` vào lệnh khởi động head, nên
  không được thêm thủ công.
- `ChiselBuildNode.build` khi lỗi **không raise** mà trả `BuildArtifact(success=False)`.
- `BashTool(name, work_dir, timeout_seconds=120, task_options=...)`. Tool đang chạy giữ
  resource cho tới khi gọi `.stop()`.
- Image Chipyard của CHIA dùng nhánh `chia_artifact` của `ucb-bar/chipyard`, Ubuntu 22.04.

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
  kèm `README.md` (mục tiêu, lệnh chạy, trạng thái kiểm chứng).
- Trong nhánh project, chỉ sửa `projects/<tên>/`, và thêm một mục ngắn về project vào
  `CLAUDE.md`. Nếu phát hiện điều gì dùng chung (lỗi Bazzite mới, ghi chú CHIA), sửa trên
  `setup` rồi `git merge origin/setup` vào nhánh project. Không sửa docs chung ngay trong
  nhánh project.
- Không rebase hay force-push các nhánh `setup` và `project/*`.
- Session Claude Code được giao nhánh `claude/*` thì vẫn push lên nhánh đó, nhưng phải
  checkout đúng nhánh `setup` hoặc `project/<tên>` làm gốc. Khi được người dùng cho phép,
  push luôn lên nhánh đích.
- Project hiện có: `project/ascon-secureboot` (RoCC Ascon-Hash256, secure boot; xong M1–M4).
  Nhánh `temp` cũ đã đổi tên thành nhánh này.
- `project/rocket-fmax`: **session mới đọc `projects/rocket-fmax/HANDOFF.md` trước** (bàn giao từ mốc m4;
  kết quả ở `projects/rocket-fmax/results/`). Sau m4 có thêm `--ab-tool` (agent synth A/B một module) và
  `--build-fixes` (agent sửa lỗi build), theo ví dụ `timing_opt` của CHIA. Hai phần này chưa chạy thật.
- `project/rocket-fmax` (`projects/rocket-fmax/`): loop agent tối ưu Fmax của RocketTile, giữ
  nguyên cycle (gate: riscv-tests + mcycle của benchmark phải khớp baseline; đo bằng Yosys +
  OpenSTA sky130hd, worker `eda` tự build). **Mốc 1 xong (25/10/2026)**: chốt luồng `v2`,
  chu kỳ cố định **21,2 ns**; baseline worst slack −1,00 ns (đường găng 22,2 ns, ~45 MHz),
  `~/rocket-fmax-out/m1-final`. **Mốc 2 xong (25/10/2026)**: 1 vòng bypass LLM (`m2a`, `m2b`),
  vòng bị loại đúng (slack bằng baseline), lần 2 cả 3 bước baseline HIT cache; một vòng
  ~13–14 phút (build 1,3 + gate ~12, synth chạy song song). **Mốc 3 xong (25/10/2026)**: 5 vòng agent thật (`m3`), nhận 4/5;
  slack −1,00 → +6,98 ns ở 21,2 ns, Fmax ~45,0 → ~70,3 MHz, area +1,4 %; tổng ~116 phút. Lợi
  chủ yếu do agent thay `+`/`-`/`*` bằng prefix adder/CSA viết tay (bù điểm yếu của Yosys/ABC),
  và vòng 4 dời một tầng prefix của FMA qua thanh ghi pipeline sẵn có. Gate chỉ gồm riscv-tests +
  6 benchmark; chưa kiểm equivalence hình thức cho các thay đổi FPU.
  **Kiểm tương đương `best.patch` (25/10/2026): PASS** (`--equiv-only`, `equiv.py`): `RocketALU`
  proven hình thức; `MulDiv` (unproven) và `FPU` (timeout) pass lockstep Verilator 10⁷ cycle.
  Chưa chạy thật: mốc phụ `config_audit.py`
  (agent chỉ đọc tìm khối thừa trong `RocketConfig`; loop tự ghi `RocketFmaxAudit.scala` trong
  `generators/chipyard` — ngoại lệ đã được người dùng duyệt — build, gate, area từng ứng viên). Audit có danh sách khối bảo vệ cho FPGA Arty A7-100T và ASIC (ORFS): debug/JTAG,
  UART, serial-TL, mem port, BootROM, CLINT/PLIC, boot-select, clock tap, clock/reset; chặn theo fragment và so cổng/module
  `ChipTop` với baseline; xuất `patches/chipyard-config.patch` (`RocketFmaxSoCConfig` + biến thể Arty). Lần chạy thật đầu (26/10/2026): Yosys area `ChipTop` lỗi `unexpected OP_CAST` ở `'{...}` (bảng tra Vec
  của firtool ngoài `RocketTile`); đã thêm `sv_packed_patterns` (đổi `'{`→`{`) cho luồng area, tag `audit_soc_area_a2_*`,
  và `area_check.py` để kiểm trong container `eda` ngoài CHIA. Image `eda` có Yosys `0.38+92` (litex-hub, bản dev sau 0.38; đọc được
  `wire [3:0][3:0]` nhưng không đọc `'{`). `area_check.py` chạy thật: 334 file, 126 pattern đổi, parse hết, area `ChipTop`
  (trừ `RocketTile` và SRAM) 1,18 mm², Yosys 149 s. Lần `fmax-audit-3`: bảng khối bảo vệ tìm đủ 10 khối; agent đề xuất 2 ứng viên (scratchpad,
  bus error devices) nhưng ghi `candidates.json` bằng `bash` có sẵn của opencode (chạy trong container opencode, không phải
  `chipyard_bash`) nên loop không thấy → prompt bắt dùng `chipyard_bash` + lặp JSON ở cuối câu trả lời; loop đọc dự phòng
  bên container opencode (`read_text_opencode`). Câu trả lời agent cache theo `audit_<run-name>_llm`: chạy lại phải đổi
  `--run-name`. **Audit chạy thật xong (26/10/2026, `fmax-audit-4`, `--candidates-file`, `~/rocket-fmax-out/audit2`)**:
  cả 2 ứng viên và bản gộp gate pass, mcycle giữ nguyên, khối bảo vệ còn đủ; bản gộp bớt 61 207 µm² (5,17 % area
  `ChipTop` ngoài tile) + 64 KiB SRAM scratchpad. Đã xuất `chipyard-config.patch`; kiểm tay: áp được, `RocketFmaxSoCConfig` elaborate qua. Bản Arty chưa elaborate được vì project `chipyard_fpga` của nhánh `chia_artifact` vốn không compile (6 harness FPGA thiếu `def wf_active: chisel3.Bool` của `HasHarnessInstantiators`; lỗi y hệt với `RocketArty100TConfig` gốc). Đo lại Fmax sau audit (`measure_soc.py`, chạy thật 26/10/2026, `~/rocket-fmax-out/soc-fmax`): best.patch + config audit cho slack +7,35 ns (13,85 ns, ~72,2 MHz) so với +6,98 ns (70,3 MHz) của best.patch; gate pass. RTL tile đổi (DCache/Frontend/ICache/PMAChecker/PTW, xbar size z4→z3); nhiễu synth cỡ 0,1 ns (base→cfg lệch 0,09 ns dù `core.div` không đổi). Đường găng của bản tốt nhất giờ ở `dcache` (store data → storegen). `rocket_fmax.py` có thêm `--fragments` (build `RocketFmaxCfg` = fragment ++ RocketConfig cho mọi vòng, file config ghi sau `collect_diff` rồi xoá) và `--start-patch` (xuất phát từ best.patch, cache `baseline_start_<hash>_*`, `--equiv` so với điểm xuất phát); chạy thật (`fmax-m4`, 26/10/2026): 5 vòng, nhận 2 (vòng 1, 5), slack +7,35 → +8,75 ns (72,2 → 80,3 MHz, area +0,4 %), đường găng chuyển sang FPU (`fpuOpt.dfma`); vòng 3 build lỗi, vòng 4 agent hết `--llm-timeout` 30 phút; equiv iter5 so với điểm xuất phát PASS (`AMOALU` proven, `FPU` lockstep 10⁷ cycle). Đã sửa: `build_errors` bỏ mã màu ANSI trước khi lọc `[error]`; tiêu đề equiv ghi đúng điểm so sánh.
  Bỏ error device: truy cập địa chỉ không ánh xạ có thể treo bus thay vì báo lỗi (gate không kiểm). Vivado/ORFS chưa
  chạy (người dùng: để sau, chỉ cần audit không bỏ khối hai luồng này cần). Claude không tự viết patch Chisel: mọi chỉnh sửa Chisel do loop
  (agent) làm. Đã kiểm (probe 25/10/2026): riscv-tests ở
  `$RISCV/riscv64-unknown-elf/share/riscv-tests` (107 test ISA -p), module `RocketTile` có cổng
  `clock`/`reset`, `chia-cacti` có `sta` (dùng `-group_count`, không phải `-group_path_count`),
  `make verilog CONFIG=RocketConfig` chỉ mất ~1 phút.
  Ghi chú CHIA dùng chung (đã chuyển lên `setup`, mục Ghi chú kỹ thuật về CHIA). Build baseline RocketConfig thật: 1,3–1,7 phút;
  gate 15,5 phút (4 slot); Yosys trên RocketTile 3,4 phút. `abc -D` không có `-constr` thì không
  chèn buffer, cho đường găng ~80 ns ảo → dùng `-constr` (luồng `v2`: 21,2 ns, đường găng trong `core.div`; `v3` dùng
  `+/choices/kogge-stone.v` nhưng file này chỉ có từ Yosys 0.40, image eda có 0.38 → đã bỏ). `rv64ui-p-ma_data` fail ở
  baseline (Rocket không hỗ trợ truy cập lệch địa chỉ).

## Việc tiếp theo (gợi ý)

1. Chạy thử `examples/rtl_agent_loop.py` trên cluster 1 máy và sửa theo log.
2. Xác nhận `PYTHONNOUSERSITE=1` cho `chia_env`.
3. Khi có máy thứ hai: làm theo `docs/usage-remote.md`, kiểm chứng `cluster-lan.yaml`.
4. Tuỳ chọn: worker Claude Code (`chia-claude-code`, mount `~/.claude`); thử các case
   study trong `~/work/chia/examples/` (`timing_opt`, `riscv_extensions`, `gem5_align`,...).
