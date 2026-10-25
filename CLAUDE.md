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
- User `dingus`, home `/var/home/dingus`. IP LAN từng là `192.168.1.3`, lần chạy M1 là
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

## Project đang làm: `projects/ascon-secureboot/` (RoCC Ascon-Hash256, secure boot)

Nhánh: `project/ascon-secureboot` (trước đây là `temp`).

Chi tiết, lệnh chạy và trạng thái kiểm chứng xem `projects/ascon-secureboot/README.md`.
- **M1 đạt** trên máy người dùng (job `ascon-m1`). RocketConfig với `-DSW_ONLY`: BOOT OK
  và BOOT FAIL đúng, digest khớp `ascon.py`, CYCLES_SW khoảng 498K cho 4 KiB. Verilator
  của image chipyard là 5.022. Cache và bypass (compile, build) phát lại đúng.
- **M2 và M3 đạt** cùng lúc ở vòng 0 của job `ascon-m2a` (`--run-id m2`). Agent thật xong
  trong 389 s, build 108 s. Đường RoCC: BOOT OK và BOOT FAIL đúng, DIGEST_HW = DIGEST_SW =
  `ascon.py`. CYCLES_ROCC khoảng 7020, CYCLES_SW khoảng 503700, nhanh hơn khoảng 71.7 lần.
- Đã review mã agent (`AsconRoCC.scala`): đúng đặc tả (chi tiết ở README project).
- **M4 đạt** (23/10/2026). Người dùng duyệt thêm test `boot_partial` (`-DPARTIAL`: 4093 byte,
  INIT giữa chừng, SQUEEZE 3..0). **Job `ascon-m3` đạt** (phát lại agent `m2` từ cache,
  build lại 82 s): cả 3 test pass, `boot_partial` CYCLES_ROCC 7027. `sw/image.h` và
  `expected.json` trong repo đã thêm digest `ac6c73b1…` (dựng tay theo output
  `gen_image.py` của người dùng, sha256 đã khớp). `make_final.sh out/m3` đã chạy, 18/18
  file pass `sha256sum -c`. `out/final/` trong repo đủ 18 file, khớp `SHA256SUMS` (7 file
  session dựng từ nội dung dán, 11 file người dùng tải lên qua GitHub web).
- `selftest.sh` **pass trên máy người dùng** (container `ubuntu:24.04`, cả `boot_partial`).
- `tests/smoke_local.sh` **pass cả 5 kịch bản** (container `python:3.10`), sau khi sửa E
  (lần đầu E sửa submodule trước baseline, nên bị bỏ qua đúng thiết kế).
- Áp patch bằng tay (`out/final/README.md`) **đã chạy**: build lại từ cây gốc, 3 test trùng
  từng chu kỳ với loop. Gọi simulator trực tiếp phải bọc `+max-cycles` trong
  `+permissive ... +permissive-off`.
- Project không còn phần nào chưa chạy.
- Người dùng yêu cầu: **không chạy gì trên session cloud**, chỉ tạo file và hướng dẫn.
  Muốn chạy test trên host Bazzite thì dùng container dùng một lần
  (`--security-opt label=disable`, mount thư mục project).
- Giao diện RoCC (custom0, `funct3={xd,xs1,xs2}`): INIT f7=0/000, ABSORB f7=1/011
  (`rs2=nbytes|last<<8`), SQUEEZE f7=2/110. Wrapper `AsconCoreV4` chỉ ép kiểu cổng enum.

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

## Việc tiếp theo (gợi ý)

1. Ascon secure boot đã xong M1–M4, mọi phần đều đã chạy trên máy người dùng.
2. Chạy thử `examples/rtl_agent_loop.py` trên cluster 1 máy và sửa theo log.
3. Xác nhận `PYTHONNOUSERSITE=1` cho `chia_env`.
4. Khi có máy thứ hai: làm theo `docs/usage-remote.md`, kiểm chứng `cluster-lan.yaml`.
5. Tuỳ chọn: worker Claude Code (`chia-claude-code`, mount `~/.claude`); thử các case
   study trong `~/work/chia/examples/` (`timing_opt`, `riscv_extensions`, `gem5_align`,...).
