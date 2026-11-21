# sources.f - các file Verilog Vivado đọc (build.tcl) và mô phỏng xsim đọc (agent.py), đường dẫn tính từ fpga/.
# Thay một module bằng bản riêng cho FPGA: thêm file fpga/<tên>.v cùng tên module và cổng, rồi đổi dòng tương ứng.
../rtl/CG_alu.v
../rtl/icg_sim.v
fpga_top.v
