"""alu_project.py - phần chung của khai báo project cho hai demo ALU (NoCG, CG): mô tả cho agent và hàm móc FPGA.

Mỗi demo có project.py riêng, import file này rồi thêm phần khác nhau (DUT, biến thể FPGA, mô tả clock gating).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

EN_THR_DEFAULT = 38                     # fpga_top: tỉ lệ gửi phép = EN_THR/128 (38 ~ 30 %), EXP_SIG mặc định ứng với nó


def en_thr(en_pct: float) -> int:
    return EN_THR_DEFAULT if round(en_pct) == 30 else max(0, min(128, round(en_pct * 128 / 100)))


def fpga_synth_args(fpga_dir: Path, opts: dict) -> tuple[list[str], dict]:
    """opts["en_pct"]: tỉ lệ chu kỳ có phép gửi tới ALU trong fpga_top (LFSR). Khác 30 % thì thêm generic EN_THR và
    EXP_SIG (chữ ký mong đợi tính bằng fpga/golden_sig.py)."""
    thr = en_thr(float(opts.get("en_pct", 30)))
    if thr == EN_THR_DEFAULT:
        return [], {"en_thr": thr}
    spec = importlib.util.spec_from_file_location("golden_sig", Path(fpga_dir) / "golden_sig.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    sig, _ = g.golden(1048576, 0xACE12025, thr)
    return ["-generic", f"EN_THR=8'd{thr}", "-generic", f"EXP_SIG=32'h{sig:08X}"], {"en_thr": thr}


def base(dut: str) -> dict:
    return {
        "group": "demo-alu",
        "dut": dut,
        "steps": ["sim", "fpga", "asic"],
        "sim": {"plusargs": {"seed": 1, "cycles": 20000, "en_pct": 30}, "fcov_module": "fcov_alu"},
        "fpga": {"part": "xc7a100tcsg324-1", "board": "Arty A7-100T", "top": "fpga_top", "period_ns": 10.0,
                 "power_instance": "u_alu", "opts": {"en_pct": 30}, "sweep_opt": "en_pct",
                 "fixed": ["golden_sig.py"]},
        "asic": {"platform": "sky130hd"},
        "agent": {
            "description": "an 8-bit ALU with two pipeline stages (input registers, combinational ALU, output "
                           "registers; ops ADD SUB AND OR XOR SHL SHR SLT).",
            "sim_hints": "each op with edge operands, zero/carry flags, signed SLT edges, every shift amount, `en` "
                         "patterns such as back-to-back and long idle, outputs holding while idle, reset behaviour, ...",
            "fpga_wrapper": "The ALU is wrapped in fpga/fpga_top.v (LFSR stimulus sending an operation in {en_thr}/128 "
                            "of the cycles, MISR self-check, LEDs).",
        },
    }
