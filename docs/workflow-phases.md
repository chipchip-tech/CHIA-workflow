# Khung chung cho project: Simulate → FPGA | ASIC

Mọi project phần cứng trong repo này đi theo cùng một khung. **Simulate** luôn là pha đầu
tiên và bắt buộc. Sau đó project rẽ sang **FPGA**, sang **ASIC**, hoặc cả hai. Hai nhánh
này chạy song song, không nối tiếp nhau.

```
                       ┌─► FPGA: tài nguyên + timing post-route trên một board cụ thể
Simulate (bắt buộc) ───┤
                       └─► ASIC: synth → P&R trên một PDK cụ thể
```

Mỗi pha dùng một agent riêng (loop CHIA riêng hoặc chế độ riêng của cùng loop), với prompt,
tool và thước đo riêng. Như vậy ngữ cảnh của mỗi phiên agent gọn hơn, và kết quả của từng
pha đọc được độc lập.

## Vì sao FPGA và ASIC là hai nhánh, không nối tiếp

Thứ tối ưu cho FPGA thường không chuyển sang ASIC, và ngược lại.

- Tài nguyên FPGA (LUT, FF, BRAM, DSP) không tương ứng với diện tích ASIC.
- Timing FPGA bị routing chi phối; timing ASIC bị độ sâu logic và cell chi phối.
- Bằng chứng trong repo (`project/rocket-fmax`, đợt r3b, 30/10/2026): Fmax đo bằng Yosys
  sky130 tăng lên 90,57 MHz, trong khi Vivado post-route trên Artix-7 đứng yên ở 67,53 MHz.

Vì vậy pha FPGA không phải bước "tối ưu trước rồi đưa sang ASIC". Cả hai nhánh cùng xuất
phát từ kết quả của pha Simulate.

## Pha 1: Simulate (bắt buộc)

**Mục tiêu**
- Chốt **spec**: chức năng bắt buộc, ISA hoặc giao diện, và ngân sách. Ví dụ: cycle được
  tăng tối đa bao nhiêu % so với baseline, diện tích hoặc tài nguyên tối đa.
- Chốt **bộ test hồi quy**: unit test, ISA test, benchmark. Bộ này là **gate** cho mọi
  thay đổi ở mọi pha sau.
- Tối ưu ở mức kiến trúc, đo bằng mô phỏng: IPC, số cycle, độ trễ các khối. Có thể khảo
  sát trước các thay đổi kiến trúc mà pha sau cần (ví dụ thêm tầng pipeline), để biết chi
  phí cycle của chúng trước khi tốn công synth.

Chức năng chỉ có đúng hoặc sai, không có gì để "tối ưu". "Tối ưu" ở pha này là tối ưu
kiến trúc trong khi giữ chức năng đúng.

**Công cụ CHIA thường dùng:** `ChiselBuildNode`, `VerilatorRunNode` (hoặc VCS, gem5),
fan-out benchmark song song, agent sửa RTL kèm agent debug khi test fail.

**Tiêu chí ra:** gate pass trên baseline (ghi rõ test nào fail sẵn từ baseline và bị loại),
có số cycle baseline đo thật, có patch tốt nhất của pha.

## Pha 2a: FPGA (tuỳ chọn)

**Mục tiêu:** tài nguyên và timing post-route trên **một board cụ thể** (ghi rõ part, ví
dụ `xc7a100tcsg324-1`), hoặc dùng FPGA để kiểm chứng: chạy phần mềm thật ở tốc độ cao,
boot OS, test dài mà mô phỏng không chạy nổi.

**Lưu ý**
- Nhận/loại theo **post-route**, không theo `synth_design`. Nhiễu giữa các lần P&R khoảng
  1–2 %, nên ngưỡng nhận phải lớn hơn mức này.
- Mỗi lần P&R mất nhiều phút, nên chạy một Vivado một lúc và tính trước thời gian mỗi vòng.
- Thay đổi chỉ dành cho FPGA (SRAM → BRAM, map vào DSP, primitive của hãng) phải bật/tắt
  được bằng config hoặc tham số, để không lẫn sang nhánh ASIC.

## Pha 2b: ASIC (tuỳ chọn)

**Mục tiêu:** timing, diện tích, công suất trên **một PDK cụ thể** (ghi rõ thư viện cell
và corner, ví dụ sky130hd `tt_025C_1v80`) ở một chu kỳ mục tiêu cố định.

**Các mức đo, từ rẻ tới đắt**
1. Synth (Yosys + OpenSTA): vài phút. Chỉ là ước lượng, chưa có placement và dây dẫn.
2. P&R (OpenROAD/ORFS): lâu hơn nhiều, nhưng là số đo thật.

Dùng mức 1 để lọc nhanh, mức 2 để xác nhận các bản tốt nhất.

**Lưu ý**
- Synth phẳng (`synth -flatten`) cả thiết kế lớn làm ABC "hỗn loạn": sửa một khối có thể
  làm slack của khối khác dao động ±0,2–0,6 ns (đã thấy ở rocket-fmax). Muốn A/B một thay
  đổi cục bộ thì synth phân cấp hoặc synth riêng module đó.
- Chu kỳ đặt cho ABC phải ở mức mục tiêu; chu kỳ rộng làm ABC dừng sớm và Fmax đo thấp.
- Thay đổi chỉ dành cho ASIC (macro SRAM, cell đặc biệt) cũng phải tắt được bằng config.

## Quy tắc chung cho mọi pha

- **Gate của pha Simulate chạy lại cho mọi thay đổi**, ở mọi pha. Thay đổi làm fail gate
  thì bị loại, hoặc giao cho agent debug.
- **Một ngân sách chung** (cycle, diện tích...) chốt ở pha Simulate và áp cho cả hai
  nhánh, để pha sau không phá thứ pha trước đã chốt.
- **Ngưỡng nhận lớn hơn nhiễu đo.** Đo nhiễu một lần ở đầu mỗi pha (chạy lại cùng RTL, hoặc
  đổi một chỗ không liên quan) rồi đặt ngưỡng theo đó.
- **Không đo lại thứ đã đo.** Lưu kết quả theo hash của RTL (hoặc của patch) và dùng lại.
- **Mỗi pha có sản phẩm bàn giao cố định:** baseline đo thật, `best.patch`, `results.csv`,
  `history.md` (các thay đổi đã thử, kể cả bị loại), và ghi chú tiêu chí vào/ra.
- **Claude (session) không tự viết patch RTL/Chisel** khi project dùng agent: mọi chỉnh sửa
  thiết kế do agent trong loop làm, session chỉ viết loop, tool và prompt.

## Ghi vào project

`projects/<tên>/README.md` phải có mục **Pha**: project đi những pha nào, pha hiện tại, board
hoặc PDK đã chọn, ngân sách, và tiêu chí vào/ra của từng pha. Mỗi pha nên có lệnh chạy riêng
(ví dụ `--phase sim`, `--phase asic`) hoặc script riêng.
