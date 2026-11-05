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
  `project/rocket-fmax` (mục dưới).
- `project/rocket-fmax` (`projects/rocket-fmax/`): **session mới đọc `projects/rocket-fmax/HANDOFF.md` trước.**
  Loop CHIA (theo `examples/timing_opt`) tối ưu Fmax của `RocketTile` (`RocketConfig`), mục tiêu > 100 MHz ở 10 ns,
  đo bằng Yosys + OpenSTA sky130hd; cycle tối đa +3 % so với baseline. Theo khung pha: `--phase sim` (agent thêm tầng
  pipeline, chỉ đo cycle bằng gate, ra danh sách ứng viên) và `--phase asic` (agent tối ưu timing, `--try-patches` thử
  ứng viên). Tốt nhất đã đo: 92,36 MHz (`fmax-r4`, flow `v2` synth phẳng); các đợt `fmax-y1`..`y4` sau đó 0 vòng
  nhận. Từ 02/11/2026: flow `v3` (synth phân cấp, `v3-start` chạy thật: 85,01 MHz), nhận theo tổng slack các họ
  endpoint, `synth_ab` nhớ RTL đã đo. Từ 03/11/2026: pha sim ghi khối đã đổi, đánh dấu biến thể, synth sơ bộ (mock);
  `--try-from` thử theo nhóm biến thể (chạy thật: `asic-1` `v3` 85,01 → 87,84 MHz; `asic-2` → 91,81 MHz, cycle +2,655 %; `asic-3` 5 vòng agent → 93,17 MHz; `asic-4` → 93,88 MHz, cycle +2,685 %; `asic-5` 0 vòng nhận; `asic-6` agent Opus → 109,17 MHz, cycle −0,006 %, đạt mục tiêu ở mức synth; `asic-7` `--period-ns 6` → 140,01 MHz, cycle +0,325 %, chưa kiểm chức năng ngoài gate; `func_check.py` (kiểm chức năng mở rộng) và `soc_synth.py` (synth cả SoC, SRAM giả) viết 05/11/2026, chưa chạy). Vivado out-of-context của `asic-6` (`viv-asic6`, 04/11/2026): 63,27 MHz, gần như không đổi so với `fmax-r4` 63,61; sau đó bỏ worker `vivado`, chuyển sang bitstream cả SoC bằng `fpga/` của Chipyard (`soc_bitstream.py`, Vivado mount vào container chipyard; `soc-asic6` chạy thật ở 50 MHz: cả hai điểm có bitstream, WNS baseline 0,129 / `asic-6` 0,372 ns, `asic-6` tile LUT +23,8 %, DSP 25 → 0; `soc-asic6-f70` ở 70 MHz đo Fmax thật của SoC: baseline 55,50, `asic-6` 59,91 MHz, +7,9 %). Claude không tự viết patch Chisel: mọi chỉnh sửa Chisel do agent trong loop làm.

## Việc tiếp theo (gợi ý)

1. Chạy thử `examples/rtl_agent_loop.py` trên cluster 1 máy và sửa theo log.
2. Xác nhận `PYTHONNOUSERSITE=1` cho `chia_env`.
3. Khi có máy thứ hai: làm theo `docs/usage-remote.md`, kiểm chứng `cluster-lan.yaml`.
4. Tuỳ chọn: worker Claude Code (`chia-claude-code`, mount `~/.claude`); thử các case
   study trong `~/work/chia/examples/` (`timing_opt`, `riscv_extensions`, `gem5_align`,...).
