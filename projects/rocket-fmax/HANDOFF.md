# Bàn giao: rocket-fmax, trạng thái sau mốc m5

File này dành cho session Claude Code tiếp quản project. Đọc nó cùng `CLAUDE.md` (gốc repo) và
`README.md` (thư mục này). **Điểm xuất phát là mốc m5.** Mọi thứ chạy sau m5 (đo lại ở 12 ns `p12`,
tool A/B `fmax-ab1`, `fmax-ab2`) được bỏ qua theo yêu cầu người dùng (27/10/2026).

## Mục tiêu

Loop CHIA cho agent (opencode) sửa Chisel của rocket-chip để rút ngắn đường găng của `RocketTile`
mà **không đổi hành vi theo cycle**. Mỗi vòng:

1. build;
2. gate: riscv-tests `rv64ui/um/ua/uf/ud-p-*` pass, và `mcycle` của 6 benchmark khớp đúng baseline;
3. Yosys (`v2`, `abc -D -constr`) + OpenSTA sky130hd tt ở chu kỳ cố định **21,2 ns**;
4. nhận vòng nếu worst slack tốt hơn bản tốt nhất.

Fmax ≈ 1000 / (21,2 − slack) MHz. Đây là con số mức synthesis, dùng để so tương đối giữa các vòng.

## Trạng thái hiện tại (bản tốt nhất)

| | Worst slack | Fmax | Area tile | Đường găng |
|---|---|---|---|---|
| RocketConfig gốc (baseline) | −1,001 ns | 45,04 MHz | 1 069 069 µm² | `core.div` |
| **m4 `best.patch` + config audit** | **+8,745 ns** | **80,29 MHz** | 1 082 577 µm² | `fpuOpt.dfma` (FMA) |

- **Bản tốt nhất** gồm hai phần, cả hai đều phải có:
  - `m4/patches/best.patch`: patch tích luỹ trong `generators/rocket-chip`, gồm m3 cộng hai vòng nhận của m4;
  - hai fragment config: `new testchipip.soc.WithNoScratchpads` và `new chipyard.config.WithNoBusErrorDevices`.
- **Gate:** pass, mcycle giữ nguyên.
- **Equiv:** iter5 của m4 so với điểm xuất phát của m4 là PASS. `AMOALU` proven hình thức; `FPU` và `IntToFP` pass lockstep Verilator 10⁷ cycle (formal timeout). Trước đó, `best.patch` của m3 so với baseline cũng PASS.
- **m5** (5 vòng, xuất phát từ m4 best) **không nhận vòng nào**, nên bản tốt nhất vẫn là của m4.
- **Patch config SoC:** `audit2/patches/chipyard-config.patch` thêm `RocketFmaxSoCConfig` và biến thể Arty. Đã áp tay được và elaborate qua. Biến thể Arty chưa elaborate được vì `chipyard_fpga` của image vốn không compile.

## File kết quả

Bản sao các kết quả cần giữ nằm trong **`results/`** (người dùng upload qua GitHub web, commit `4782cfb`). Bản gốc
ở máy người dùng, `~/rocket-fmax-out/<run>/`. `results/SHA256SUMS` liệt kê sha256 của mọi file; đã kiểm ngày
27/10/2026 và khớp toàn bộ (`cd results && sha256sum -c SHA256SUMS`):

| File | sha256 |
|---|---|
| `m4/best.patch` | `8e1789a206e67d12071c4090ab1c79c2a802ab14ecb5fe9cab41a0e8561dbb28` |
| `m4/results.csv` | `a39e2bd19a179aa9566cade3ac530878ce0f19a2681a532c0891455e878c563e` |
| `m4/best.json` | `1ffaf436cbff6e9438033ad040f1a5500cffef31eeca04de6c990093867014b9` |
| `m4/args.json` | `3a427ec5224674ad3900aa339d91ca727031ead5b0f4a1a968b27f6abd4353cf` |
| `m4/equiv_report.md` | `5e76c19e7b1fb644431bb36d6605bce8eb79d5048f2498e25cd517406c6aa836` |
| `m5/results.csv` | `16e153d18fb430d7a5539a0b7bd711f3a9e84b88205f9f58c487659ee644c8d0` |
| `m5/agent_response.md` | `b06e90a73111e9dc316219e42c97604c033de890f7997e13bf81a1992d8f5ac5` |
| `m5/iter1.patch` | `9fe6fd7476692b92f040e6190c2bb6295c994617bb81c4e0ab4bea3d548133e1` |
| `m5/iter2.patch` | `d1f8331c9ebae516c6ffb88701b7fbcd18eaa18526c7c27b724bf380bdd541cf` |
| `m5/iter3.patch` | `8e1789a206e67d12071c4090ab1c79c2a802ab14ecb5fe9cab41a0e8561dbb28` (trùng `m4/best.patch`) |
| `soc-fmax/soc_fmax.md` | `2321e027d0e2650c2bd67d9a3da3e973faf9a2f301d36e6296420177d763e7f0` |
| `soc-fmax/args.json` | `57712bb6c7dbe9428035f002dc94982e8c5630a7d1988dcee25d5e4b4e3551a3` |
| `audit2/chipyard-config.patch` | `fb6a8b9e1ec793e8594eeec8463d3ddb305a32370d2ae84b07cb041f0086a4d8` |
| `audit2/audit.md` | `9ef7e29788ac25ab857ced5ffa2fabf13cedf14e8a673d41711250af39c220d3` |

**Lưu ý về `results/m5/`: đây không phải lần m5 ghi trong `README.md`.** Có hai lần chạy cùng `--run-name m5`,
và lần sau đã ghi đè thư mục `~/rocket-fmax-out/m5` của lần đầu.

- **Lần 1:** 5 vòng, kết quả người dùng dán lúc 03:04 UTC ngày 27/10/2026. Các số này được ghi trong
  `README.md` và ở bảng m5 dưới đây. Không còn file nào của lần này: `results.csv` và các patch từng vòng đều
  đã mất.
- **Lần 2:** chỉ có 3 vòng; chưa rõ vì sao người dùng chạy lại. Đây chính là `results/m5/`:

  | Vòng | Slack | Đường găng |
  |---|---|---|
  | 1 | +8,161 (76,69 MHz) | `core.div` |
  | 2 | +8,547 (79,03 MHz) | `fpuOpt.divSqrt` |
  | 3 | — | agent hết 45 phút; `iter3.patch` trùng `m4/best.patch`, tức không thay đổi gì |

  `agent_response.md` lấy từ `runs/iter1` của lần này.

Cả hai lần đều không nhận vòng nào, nên bản tốt nhất vẫn là m4 (+8,745 ns). `iter1.patch` và `iter2.patch` là
patch tích luỹ (m4 best cộng sửa của vòng đó) của lần 2, **không phải** vòng 1 +8,58 ns của lần 1.

## Lịch sử ngắn

Số liệu chi tiết ở `README.md`; tóm tắt các mốc ở `CLAUDE.md`.

| Mốc | Kết quả |
|---|---|
| m1 | Chốt luồng `v2`, chu kỳ 21,2 ns, baseline −1,00 ns (~45 MHz), đường găng `core.div` |
| m2 | Bypass LLM: loop, cache và nhận/loại chạy đúng |
| m3 | 5 vòng, nhận 4: +6,98 ns (~70,3 MHz). Lợi chính đến từ prefix adder/CSA viết tay thay `+`/`-`/`*` (bù điểm yếu của Yosys/ABC) và việc dời một tầng prefix của FMA qua thanh ghi có sẵn |
| audit | Agent tìm khối thừa trong config, chỉ đọc. Bỏ scratchpad và bus error device: area ngoài tile −5,17 %, gate pass |
| soc-fmax | m3 best + audit: +7,35 ns (72,2 MHz) |
| m4 | Từ m3 best + audit: nhận vòng 1 và 5, đạt **+8,745 ns (80,29 MHz)** |
| m5 | Từ m4 best, `--llm-timeout 2700`: không nhận vòng nào. Vòng 1 +8,58 (đường găng về `core.div`, kém 0,16 ns, cỡ nhiễu); các vòng 2, 4, 5 kém 0,8–1,4 ns; vòng 3 agent hết 45 phút |

**Đã học được:**
- Ở ~80 MHz, nhiều họ đường nằm sát nhau trong khoảng 0,2 ns: FMA, `div`, `ifpu`, `divSqrt`, store data của DCache. Sửa một họ thì họ khác lộ ra.
- Nhiễu của synth flatten + ABC cỡ 0,1 ns.
- Agent sửa "mù" (không được synth thử) thường làm đường găng dài thêm.

## Code

- **m5 chạy bằng code ở commit `30d6b98`**: không có thay đổi code nào giữa `30d6b98` và `64d5991`; commit `64d5991` chỉ ghi kết quả m5.
- **Các commit sau m5** (`e671148`, `c61d7f2`, `782e5f3`, `69f8ba0`) chưa được kiểm chứng trên lần chạy thật nào. Chúng thêm:
  - `--ab-tool`: tool MCP `synth_ab`, file `ab_tool.py`, prompt `improve_timing_ab.md`. Lần chạy thật đầu tiên agent không gọi tool lần nào.
  - `SalvageOpenCodeLLM`: giữ transcript khi agent hết giờ.
  - **Đổi mặc định:** tắt các tool sẵn có của opencode (`bash`, `read`, `edit`, …). Các tool này chạy trong container opencode, nơi không có Chipyard.

  Muốn chạy y như m5: thêm `--builtin-tools`, hoặc lấy code ở `64d5991`. Phần `--ab-tool` chỉ bật khi truyền cờ đó.
- Đã thử bằng mock trong sandbox: loop thường và tắt tool sẵn có (chỉ kiểm cấu hình sinh ra, chưa kiểm với opencode thật).

## Chạy tiếp từ m5

Lệnh cùng dạng m5, xuất phát từ m4 best (người dùng chạy, từ `~/CHIA-workflow/projects/rocket-fmax`).
Bản `best.patch` ở máy người dùng trùng với `results/m4/best.patch` (cùng sha256):

```bash
chia job submit --no-wait --submission-id fmax-m6 --working-dir . -- python rocket_fmax.py \
    --period-ns 21.2 --iters 5 --llm-timeout 2700 --exclude-isa rv64ui-p-ma_data --equiv \
    --fragments "new testchipip.soc.WithNoScratchpads" "new chipyard.config.WithNoBusErrorDevices" \
    --start-patch ~/rocket-fmax-out/m4/patches/best.patch --run-name m6
chia job logs -f fmax-m6        # dừng: chia job stop fmax-m6
```

Điểm xuất phát đã có trong cache (`baseline_start_<hash>_*`). Phải đổi `--run-name` mỗi lần, vì câu trả lời của
agent được cache theo `agent_<run-name>_iter<i>_llm`.

**Các hướng còn mở**, chưa làm và cần người dùng chọn:

1. Xem lại các vòng bị loại nhưng sát bản tốt nhất. Vòng 1 của lần m5 đầu (+8,58 ns, `core.div`) đã mất patch; vòng 2 của lần m5 sau (+8,547 ns, `divSqrt`) còn trong `results/m5/iter2.patch`. Có thể dùng làm điểm xuất phát rồi ghép thêm một sửa FMA.
2. Đổi model của agent. Hiện dùng `opencode/big-pickle`; ví dụ `timing_opt` (BOOM) của CHIA dùng Claude với effort cao.
3. Cho agent tự synth thử trước khi nộp (kiểu `timing_experiment` của ví dụ BOOM). Có code `--ab-tool` nhưng chưa chạy được thật.
4. "Iron-law": nới gate mcycle, chấp nhận mất ít IPC. **Cần người dùng đồng ý trước.**
5. Vivado/ORFS: người dùng để sau. Adder viết tay trong `best.patch` cần kiểm lại trên FPGA (carry chain cứng).

## Quy ước làm việc

Xem đầy đủ trong `CLAUDE.md`.

- Trả lời bằng tiếng Việt.
- Claude không chạy lệnh trên máy người dùng: đưa lệnh cho người dùng chạy, rồi đọc log họ gửi lại.
- **Claude không tự viết patch Chisel.** Mọi chỉnh sửa Chisel do loop (agent) làm.
- `~/CHIA-workflow` trên máy người dùng không phải bản git clone, và người dùng không `git push` được.
  - Đưa code bằng commit trên GitHub. Người dùng tải zip `https://github.com/chipchip-tech/CHIA-workflow/archive/<sha>.zip` bằng trình duyệt (repo private), chép file vào, rồi so `sha256sum`.
  - Muốn lấy file từ máy người dùng thì nhờ họ upload qua GitHub web hoặc dán nội dung.
- Commit vào nhánh `project/rocket-fmax`, push cả lên nhánh `claude/*` được giao: `git push origin HEAD:<nhánh>`.
  - Không tạo PR.
  - Không rebase hay force-push.
  - Không ghi tên model vào commit.
- Liệt kê file cho người dùng duyệt trước khi tạo hoặc xoá file tài liệu.
- Cập nhật `README.md` và `CLAUDE.md` sau mỗi mốc hoặc phát hiện mới. Ghi rõ phần nào đã kiểm chứng, phần nào chưa.
- Mã nguồn CHIA (chialoops 2.0.1) để đối chiếu: repo `ucb-bar/chia`, ví dụ `chia/models/opencode.py`, `chia/base/tools/`.
