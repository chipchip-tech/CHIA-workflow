# config.mk - ORFS cho demo CG, sky130hd, RTL → GDS.
# Baseline: thiết lập gần mặc định (như designs/sky130hd/gcd của ORFS). Agent ở pha 2 được sửa file này.
# Module icg dùng bản ASIC icg_sky130.v (cell sky130_fd_sc_hd__dlclkp), KHÔNG dùng rtl/icg_sim.v.
# Đường dẫn tính theo vị trí file này, nên chép cả thư mục run đi đâu cũng chạy được.
DEMO_ASIC_DIR := $(dir $(DESIGN_CONFIG))

export PLATFORM         = sky130hd
export DESIGN_NAME      = CG_alu
export DESIGN_NICKNAME  = CG_alu
export VERILOG_FILES    = $(DEMO_ASIC_DIR)../rtl/CG_alu.v \
                          $(DEMO_ASIC_DIR)icg_sky130.v
export SDC_FILE         = $(DEMO_ASIC_DIR)constraint.sdc

export CORE_UTILIZATION = 40
export PLACE_DENSITY    = 0.65
