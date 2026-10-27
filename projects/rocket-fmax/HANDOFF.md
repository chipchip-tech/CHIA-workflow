# Bàn giao: rocket-fmax, trạng thái sau mốc m4

File này dành cho session Claude Code tiếp quản project. Đọc cùng `CLAUDE.md` (gốc repo) và `README.md`
(thư mục này). Điểm xuất phát là **mốc m4**.

## Mục tiêu

Loop CHIA cho agent (opencode) sửa Chisel của rocket-chip để rút ngắn đường găng của `RocketTile` mà
**không đổi hành vi theo cycle**. Mỗi vòng:

1. build;
2. gate: riscv-tests `rv64ui/um/ua/uf/ud-p-*` pass và `mcycle` của 6 benchmark khớp đúng baseline;
3. Yosys (`v2`, `abc -D -constr`) + OpenSTA sky130hd tt, chu kỳ cố định **21,2 ns**;
4. nhận vòng nếu worst slack tốt hơn bản tốt nhất.

Fmax ≈ 1000 / (21,2 − slack) MHz. Đây là số đo mức synthesis, dùng để so tương đối giữa các vòng.

## Trạng thái hiện tại (bản tốt nhất)

| | Worst slack | Fmax | Area tile | Đường găng |
|---|---|---|---|---|
| RocketConfig gốc (baseline) | −1,001 ns | 45,04 MHz | 1 069 069 µm² | `core.div` |
| m3 `best.patch` | +6,980 ns | 70,32 MHz | 1 083 776 µm² | `dcache` |
| m3 `best.patch` + config audit (điểm xuất phát của m4) | +7,350 ns | 72,20 MHz | 1 078 378 µm² | `dcache` |
| **m4 `best.patch` + config audit** | **+8,745 ns** | **80,29 MHz** | 1 082 577 µm² | `fpuOpt.dfma` (FMA) |

- **Bản tốt nhất gồm hai phần:**
  - `results/m4/best.patch`: patch tích luỹ trong `generators/rocket-chip` (m3 cộng các vòng được nhận của m4);
  - hai fragment config: `new testchipip.soc.WithNoScratchpads` và `new chipyard.config.WithNoBusErrorDevices`.
- **Kiểm chứng:**
  - Gate pass, mcycle giữ nguyên.
  - Equiv của m4 so với điểm xuất phát của m4: PASS. `AMOALU` proven hình thức; `FPU`/`IntToFP` pass lockstep
    Verilator 10⁷ cycle (formal timeout).
  - Equiv của m3 so với baseline cũng PASS.
- **Patch config SoC** `results/audit2/chipyard-config.patch`: thêm `RocketFmaxSoCConfig` và biến thể Arty.
  - Áp tay được, `RocketFmaxSoCConfig` elaborate qua.
  - Biến thể Arty chưa elaborate được, vì `chipyard_fpga` của image vốn không compile.

## File kết quả (`results/`)

Bản sao người dùng upload từ `~/rocket-fmax-out/<run>/` trên máy họ. Kiểm bằng
`cd results && sha256sum -c SHA256SUMS`; đã khớp toàn bộ ngày 27/10/2026.

| File | Nội dung | sha256 |
|---|---|---|
| `m4/best.patch` | bản tốt nhất (rocket-chip) | `8e1789a206e67d12071c4090ab1c79c2a802ab14ecb5fe9cab41a0e8561dbb28` |
| `m4/results.csv` | các vòng m4 (2 khối dòng: lần chạy đầu dừng ở vòng 1, lần hai đủ 5 vòng) | `a39e2bd19a179aa9566cade3ac530878ce0f19a2681a532c0891455e878c563e` |
| `m4/best.json` | số đo bản tốt nhất | `1ffaf436cbff6e9438033ad040f1a5500cffef31eeca04de6c990093867014b9` |
| `m4/args.json` | tham số lần chạy m4 | `3a427ec5224674ad3900aa339d91ca727031ead5b0f4a1a968b27f6abd4353cf` |
| `m4/equiv_report.md` | equiv m4 | `5e76c19e7b1fb644431bb36d6605bce8eb79d5048f2498e25cd517406c6aa836` |
| `soc-fmax/soc_fmax.md` | đo 4 điểm base/cfg/best/best_cfg (m3 + audit) | `2321e027d0e2650c2bd67d9a3da3e973faf9a2f301d36e6296420177d763e7f0` |
| `soc-fmax/args.json` | tham số `measure_soc.py` | `57712bb6c7dbe9428035f002dc94982e8c5630a7d1988dcee25d5e4b4e3551a3` |
| `audit2/chipyard-config.patch` | config SoC sau audit | `fb6a8b9e1ec793e8594eeec8463d3ddb305a32370d2ae84b07cb041f0086a4d8` |
| `audit2/audit.md` | báo cáo audit | `9ef7e29788ac25ab857ced5ffa2fabf13cedf14e8a673d41711250af39c220d3` |

## Lịch sử ngắn

Chi tiết ở `README.md`.

| Mốc | Kết quả |
|---|---|
| m1 | Chốt luồng `v2`, chu kỳ 21,2 ns, baseline −1,00 ns (~45 MHz), đường găng `core.div` |
| m2 | Bypass LLM: loop, cache và nhận/loại chạy đúng |
| m3 | 5 vòng, nhận 4: +6,98 ns (~70,3 MHz). Lợi chính đến từ prefix adder/CSA viết tay thay `+`/`-`/`*` (bù điểm yếu của Yosys/ABC) và việc dời một tầng prefix của FMA qua thanh ghi có sẵn |
| audit | Agent chỉ đọc, tìm khối thừa trong config. Bỏ scratchpad và bus error device: area ngoài tile −5,17 %, gate pass |
| soc-fmax | m3 best + audit: +7,35 ns (72,2 MHz) |
| m4 | Từ m3 best + audit, 5 vòng: nhận vòng 1 (+8,158) và vòng 5 (**+8,745 ns, 80,29 MHz**). Vòng 2 kém hơn, vòng 3 build lỗi Scala, vòng 4 agent hết `--llm-timeout` 30 phút |

**Rút ra đến m4:**

- Đường găng dịch dần: `core.div` → `dcache` (store data) → `fpuOpt.ifpu` → `fpuOpt.dfma`.
- Nhiễu synth (flatten + ABC) cỡ 0,1 ns.
- 30 phút cho agent là không đủ ở một số vòng.

## Code

Code trong nhánh là đúng bản đã chạy m4 (`rocket_fmax.py`, `fmax_nodes.py`, `equiv.py`, `measure_soc.py`,
`config_audit.py`, `prompts/`). Các tùy chọn dùng ở m4: `--fragments`, `--start-patch`, `--equiv`.

## Chạy tiếp từ m4

Người dùng chạy từ `~/CHIA-workflow/projects/rocket-fmax`. File `~/rocket-fmax-out/m4/patches/best.patch` trên
máy họ trùng sha256 với `results/m4/best.patch`.

```bash
chia job submit --no-wait --submission-id fmax-<tên> --working-dir . -- python rocket_fmax.py \
    --period-ns 21.2 --iters 5 --exclude-isa rv64ui-p-ma_data --equiv \
    --fragments "new testchipip.soc.WithNoScratchpads" "new chipyard.config.WithNoBusErrorDevices" \
    --start-patch ~/rocket-fmax-out/m4/patches/best.patch --run-name <tên>
chia job logs -f fmax-<tên>        # dừng: chia job stop fmax-<tên>
```

- Mỗi lần chạy phải dùng `--run-name` mới, vì câu trả lời agent được cache theo `agent_<run-name>_iter<i>_llm`.
- Cân nhắc tăng `--llm-timeout` (mặc định 1800 s).

## Quy ước làm việc

Xem đầy đủ trong `CLAUDE.md`.

- Trả lời bằng tiếng Việt.
- Claude không chạy lệnh trên máy người dùng: đưa lệnh cho người dùng chạy, rồi đọc log họ gửi lại.
- **Claude không tự viết patch Chisel.** Mọi chỉnh sửa Chisel do loop (agent) làm.
- `~/CHIA-workflow` trên máy người dùng không phải bản git clone, và người dùng không `git push` được.
  - Đưa code bằng commit trên GitHub. Người dùng tải zip
    `https://github.com/chipchip-tech/CHIA-workflow/archive/<sha>.zip` bằng trình duyệt (repo private), chép file
    vào máy, rồi so `sha256sum`.
  - Muốn lấy file từ máy người dùng thì nhờ họ upload qua GitHub web hoặc dán nội dung.
- Làm việc trên nhánh `project/rocket-fmax`.
  - Không tạo PR.
  - Không rebase hay force-push.
  - Không ghi tên model vào commit.
- Liệt kê file cho người dùng duyệt trước khi tạo hoặc xoá file tài liệu.
- Cập nhật `README.md` và `CLAUDE.md` sau mỗi mốc hoặc phát hiện mới. Ghi rõ phần nào đã kiểm chứng, phần nào chưa.
- Vivado/ORFS: người dùng để sau. Adder viết tay trong `best.patch` cần kiểm lại trên FPGA (carry chain cứng).
- Mã nguồn CHIA (chialoops 2.0.1) để đối chiếu: repo `ucb-bar/chia`.
