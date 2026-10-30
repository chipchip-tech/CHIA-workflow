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
- `project/rocket-fmax` (`projects/rocket-fmax/`): **session mới đọc `projects/rocket-fmax/HANDOFF.md` trước.**
  Loop CHIA tối ưu Fmax của `RocketTile` (Chipyard `RocketConfig`), mục tiêu > 100 MHz ở chu kỳ 10 ns
  (r1–r3b đo bằng Yosys + OpenSTA sky130hd, nay bằng Vivado post-route). **Bắt đầu lại từ đầu ngày 29/10/2026**: mọi kết
  quả trước bị bỏ (lịch sử chỉ còn trong git, trước commit `ab43d94`), code config audit đã gỡ. Hướng duy nhất là
  `--chia-mode` theo `examples/timing_opt` của CHIA: mỗi vòng một phiên Claude Code (Sonnet, effort medium, gói
  Pro) tự A/B bằng `synth_ab`, được thêm pipeline; nhận khi Fmax / chi phí cycle tăng > 1 % và cycle tăng ≤ 3 %
  so với baseline; gate riscv-tests + 8 benchmark (`--extra-bench mm spmv`), agent debug khi test fail; không
  equiv. Baseline 10 ns đo thật: 47,13 MHz, đường găng `core.div`, gate 18,9 phút. Đợt 1 `chia-fmax-r1` (29/10): 4/5
  vòng nhận, 81,91 MHz, cycle +0,405 %. Đợt 2 `chia-fmax-r2`: 1/5 vòng nhận, 84,37 MHz, cycle +0,405 %. Đợt 3 `chia-fmax-r3b` (30/10): 1/5 vòng nhận, 90,57 MHz Yosys, cycle +0,405 %; Vivado (đo song song, Artix-7) đứng yên 67,53 MHz. **Đổi mốc 30/10/2026**: mốc 1 = > 100 MHz post-route Vivado trên Arty A7-100T (`xc7a100tcsg324-1`), nhận/loại chỉ theo post-route, SRAM → BRAM, giữ cycle +3 %; mốc 2 = ORFS sky130 post-route. Bước 0 xong (`viv-impl2`): post-route baseline 58,67, start 61,06 MHz, 48 BRAM, mỗi chiến lược P&R 5–12 phút, chạy 1 Vivado một lúc. Bước 1 (30/10, mới kiểm bằng mock): loop nhận theo post-route (`explore` + `extratiming`, song song gate, ngưỡng 2 %), `synth_ab` bằng Vivado `synth_design`; đã gỡ Yosys/worker `eda`, opencode, `--loop-ab`, equiv; cluster còn 4 worker. Đợt `fmax-r4` (30/10, Bước 1 chạy thật): 1/5 vòng nhận, post-route 61,06 → 63,61 MHz, cycle +0,405 %; vòng 5 lặp y nguyên patch vòng 3 → loop nay loại ngay Verilog đã đo; A/B chỉ theo worst slack che mất cải thiện của họ khác → `synth_ab` nay so slack theo họ endpoint. Đợt `fmax-r5` (30/10): 0/5 vòng nhận (tốt nhất +1,11 %, ngang nhiễu P&R), vẫn 63,61 MHz; **Cuối 30/10 người dùng tạm bỏ mốc FPGA, quay lại Yosys + OpenSTA sky130hd (phần synth của mốc ASIC) và đẩy Fmax cao hơn**: code Vivado đã gỡ (còn ở commit `d2a1ac0`), loop synth trước gate, `--history-from`; mới kiểm bằng mock. Ghi nhớ kỹ thuật: build RocketConfig 1,3–1,7 phút, Yosys trên RocketTile ~3–4 phút; `abc -D` phải có
  `-constr` và chu kỳ đặt ở mục tiêu (chu kỳ rộng làm Fmax đo thấp hơn); `rv64ui-p-ma_data` fail ở baseline
  (`--exclude-isa`); image `eda` có Yosys 0.38. Claude không tự viết patch Chisel: mọi chỉnh sửa Chisel do loop
  (agent) làm.

## Việc tiếp theo (gợi ý)

1. Chạy thử `examples/rtl_agent_loop.py` trên cluster 1 máy và sửa theo log.
2. Xác nhận `PYTHONNOUSERSITE=1` cho `chia_env`.
3. Khi có máy thứ hai: làm theo `docs/usage-remote.md`, kiểm chứng `cluster-lan.yaml`.
4. Tuỳ chọn: worker Claude Code (`chia-claude-code`, mount `~/.claude`); thử các case
   study trong `~/work/chia/examples/` (`timing_opt`, `riscv_extensions`, `gem5_align`,...).
