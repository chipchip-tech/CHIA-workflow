"""project.py - khai báo project cho flow/ (demo ALU có clock gating). Xem flow/README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import alu_project as A  # noqa: E402

fpga_synth_args = A.fpga_synth_args

PROJECT = A.base("CG_alu")
PROJECT["title"] = "8-bit ALU with clock gating (ICG on input and output registers)"
# Biến thể FPGA (rtl/ cố định): thay file trong sources.f và thêm tuỳ chọn synth_design.
PROJECT["fpga"]["variants"] = {
    "latch": {"replace": {}, "synth": [], "note": "icg_sim.v as is: latch + LUT on the clock path"},
    "bufgce": {"replace": {"../rtl/icg_sim.v": "icg_fpga_bufgce.v"}, "synth": [], "note": "ICG as BUFGCE"},
    "ce": {"replace": {"../rtl/icg_sim.v": "icg_fpga_ce.v"}, "synth": ["-gated_clock_conversion", "auto"],
           "note": "Vivado converts clock gating to clock enables"},
}
PROJECT["fpga"]["fixed"] = ["golden_sig.py", "icg_fpga_bufgce.v", "icg_fpga_ce.v"]
PROJECT["agent"]["variant"] = (
    "This variant (clock gating): the input/output registers are clocked by gated clocks from module `icg` "
    "(rtl/icg_sim.v, a behavioural latch + AND model of an integrated clock gating cell).")
