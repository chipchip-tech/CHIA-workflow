# ascon-core: lõi Ascon-AEAD128 qua flow Sim → FPGA → ASIC

Project thử mẫu `flow/` với một khối RTL chưa từng chạy với CHIA (người dùng đưa 26/11/2026): lõi Ascon-AEAD128 chỉ
giải mã + kiểm tag, bus 32 bit, 1 vòng Ascon-p mỗi chu kỳ, reset `rst` tích cực thấp, clock 10 ns cho cả FPGA và ASIC.

Nguồn chỉ gồm `rtl/` và `sim/` cộng `project.py`; mọi file khác do flow sinh trong thư mục chạy. FPGA chạy
out-of-context (không vỏ board, không bitstream).

## Thay đổi so với file gốc

Logic không đổi. Hai sửa nhỏ để testbench chạy được trên netlist (GLS), người dùng duyệt 26/11/2026:
- tách phần khai báo chung (tham số, `data_t`, hàm `pad`/`pad2`/`mask`) ở đầu `ascon_core.sv` ra `rtl/ascon_pkg.svh`;
  `ascon_core.sv` và testbench `include file này;
- testbench: `wait_for_idle` chờ cổng `auth_valid` thay vì đọc `u_ascon_core.fsm_q`; `bdo_type` khai báo `logic [3:0]`
  (cổng của netlist là vector thường).
- testbench giữ reset 100 ns (trước là 20 ns) rồi mới nạp khóa: netlist Xilinx giữ mọi flop trong GSR 100 ns đầu
  (quy trình mô phỏng gate-level của Vivado). Lần chạy trước treo ở bước nạp khóa vì lý do này (đo thật 26/11/2026).

## Chạy

```bash
python ~/CHIA-workflow/flow/run.py projects/ascon-core baseline
```
```bash
python ~/CHIA-workflow/flow/run.py projects/ascon-core agent --rounds 1
```

## Trạng thái

- Sandbox (26/11/2026): Sim chạy thật bằng Verilator 5.020: PASS, line 25/35, toggle 3007/3438, branch 44/60; lint
  `-Wall` 12 cảnh báo (ghi lại, không chặn). Testbench đã sửa cũng PASS khi biên dịch như GLS (`-DGLS`, RTL sau tb).
- Chưa chạy trên cluster. Rủi ro: Yosys của ORFS đọc mảng packed nhiều chiều (`logic [UROL-1:0][63:0] ...`); nếu lỗi,
  thêm `"config_extra": ["export SYNTH_HDL_FRONTEND = slang"]` vào `asic` của `project.py` (nếu ORFS có plugin slang).
