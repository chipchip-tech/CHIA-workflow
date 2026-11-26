"""project.py - khai báo project cho flow/ (demo ALU không clock gating). Xem flow/README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import alu_project as A  # noqa: E402

fpga_synth_args = A.fpga_synth_args

PROJECT = A.base("NoCG_alu")
PROJECT["title"] = "8-bit ALU, registers with clock enable (no clock gating)"
PROJECT["fpga"]["variants"] = {
    "rtl": {"replace": {}, "synth": [], "note": "RTL as is (registers with clock enable)"},
}
PROJECT["agent"]["variant"] = "This variant: registers use ordinary clock enables (feedback mux), no clock gating."
