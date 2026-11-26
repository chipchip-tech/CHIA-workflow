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
- Chạy thật trên cluster 26/11/2026 (`baseline-20261126-213109`, `KẾT QUẢ: OK`): Sim PASS, lint 12 cảnh báo, line 25/35, toggle 3007/3438, branch 44/60; FPGA out-of-context 100 MHz: LUT 1592, FF 473, WNS 1,292 / WHS 0,217 ns (Fmax ước lượng 114,8 MHz), methodology 0 critical (TIMING-18 mức Warning: chưa có độ trễ I/O), SAIF khớp 66 % net, P động 23,4 mW, GLS trên netlist PASS; ASIC sky130hd 10 ns: Yosys 0.68 đọc được SV, 48 859 µm² cell (util 44,7 %), 5 464 cell, 479 flop, setup/hold WS 2,626/0,474 ns (tt), 2,088/0,430 ns (OCV ±10 %), P 9,96 mW, DRC router/KLayout 0, antenna 0, LVS khớp, GLS PASS; route 605 s; kiểm tra sau finish song song 41,7 s (lần lượt 72,4 s).
  Agent `--rounds 1` chạy thật 26/11/2026 (`agent-20261126-215007`, `KẾT QUẢ: OK`): sim-r1 NHẬN (agent thêm kích thích vào tb: line 25 → 34/35, toggle 3007 → 3383, branch 44 → 57/60); fpga-r1 LOẠI (sửa `build.tcl`: WNS 1,292 → 0,71 ns, P động 23,4 → 30,5 mW); asic-r1 LOẠI (sửa `config.mk`: die −15 % nhưng cell 48 859 → 51 204 µm², P 9,96 → 9,80 mW; route 1 211 s). GLS ASIC của vòng ASIC dùng tb mới của sim-r1 và PASS.
