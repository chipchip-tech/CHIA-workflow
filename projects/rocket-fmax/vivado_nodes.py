"""Vivado synth_design timing of RocketTile, measured next to Yosys + OpenSTA.

Runs on the `vivado` worker (cluster.yaml: rocket_vivado, docker/VivadoDockerfile,
Vivado installed on the head and mounted at /tools/Xilinx). Only a second opinion:
the loop still accepts and rejects on the sky130 numbers; rocket_fmax.py writes
these results to vivado.csv (--vivado-part).

Same netlist inputs as synth_sta: the TileRTL of a build, SRAM *_ext modules as
black boxes (their pins are unconstrained), I/O with 0 delay against `clock`.
Out-of-context synthesis, so the part's I/O count does not matter.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from chia.base.ChiaFunction import ChiaFunction

RETRY_THREADS = 2
VIVADO_GLOBS = ["/tools/Xilinx/Vivado/*/bin/vivado", "/tools/Xilinx/*/Vivado/bin/vivado"]

CLOCK_XDC = """\
create_clock -name clk -period {period_ns} [get_ports clock]
set_input_delay 0 -clock clk [get_ports -filter {{DIRECTION == IN && NAME != clock}}]
set_output_delay 0 -clock clk [get_ports -filter {{DIRECTION == OUT}}]
"""

VIVADO_TCL = """\
set_param general.maxThreads {threads}
read_verilog -sv {files}
read_verilog {stubs}
read_xdc clock.xdc
synth_design -top {top} -part {part} -mode out_of_context
report_timing_summary -max_paths 10 -file timing_summary.rpt
report_timing -setup -max_paths 10 -nworst 1 -input_pins -file paths.rpt
report_utilization -file util.rpt
set p [get_timing_paths -setup -max_paths 1 -nworst 1]
puts "@@SLACK [get_property SLACK $p]"
puts "@@START [get_property STARTPOINT_PIN $p]"
puts "@@END [get_property ENDPOINT_PIN $p]"
set eps [get_timing_paths -setup -max_paths 2000 -nworst 1 -slack_lesser_than 0]
set tns 0.0
foreach e $eps {{ set tns [expr {{$tns + [get_property SLACK $e]}}] }}
puts "@@TNS2000 $tns"
puts "@@FAILING [llength $eps]"
"""


@dataclass
class VivadoResult:
    success: bool
    part: str
    period_ns: float
    worst_slack_ns: float | None = None
    fmax_mhz: float | None = None         # 1000 / (period - worst slack)
    tns_ns: float | None = None           # sum over the worst 2000 failing endpoints
    failing_endpoints: int | None = None  # capped at 2000
    crit_start: str = ""
    crit_end: str = ""
    luts: int | None = None
    ffs: int | None = None
    dsps: int | None = None
    paths: str = ""                       # report_timing, top 10
    summary: str = ""                     # report_timing_summary
    utilization: str = ""
    log_tail: str = ""
    version: str = ""
    seconds: float = 0.0
    threads: int = 0                      # maxThreads of the run that produced the result
    returncodes: list = field(default_factory=list)  # one per run; two when retried
    error: str = ""


def find_vivado() -> str:
    if os.environ.get("VIVADO"):
        return os.environ["VIVADO"]
    for g in VIVADO_GLOBS:
        hits = sorted(glob.glob(g))
        if hits:
            return hits[-1]
    return ""


def black_box_stubs(stubs: str) -> str:
    """Port-only modules of the SRAM macros, marked so Vivado keeps them as black
    boxes (an empty module would be synthesized: its outputs tied off and the logic
    they drive removed)."""
    return re.sub(r"^(\s*)module\b", r"\1(* black_box *) module", stubs, flags=re.M)


def _util(text: str, name: str) -> int | None:
    m = re.search(rf"^\|\s*{re.escape(name)}\*?\s*\|\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def parse_vivado(log: str, period_ns: float) -> dict:
    def val(tag):
        m = re.search(rf"^@@{tag} (.*)$", log, re.M)
        return m.group(1).strip() if m else ""

    def num(tag):
        try:
            return float(val(tag))
        except ValueError:
            return None

    slack = num("SLACK")
    fail = num("FAILING")
    return dict(worst_slack_ns=slack, tns_ns=num("TNS2000"),
                failing_endpoints=int(fail) if fail is not None else None,
                crit_start=val("START"), crit_end=val("END"),
                fmax_mhz=1000.0 / (period_ns - slack) if slack is not None and period_ns - slack > 0 else None)


@ChiaFunction(resources={"vivado": 1}, num_cpus=4, max_retries=0)
def vivado_synth(rtl, period_ns: float, part: str, threads: int = 8, timeout: int = 3 * 3600) -> VivadoResult:
    res = VivadoResult(success=False, part=part, period_ns=period_ns)
    vivado = find_vivado()
    if not vivado:
        res.error = f"vivado not found ({', '.join(VIVADO_GLOBS)}; or set VIVADO)"
        return res
    res.version = vivado
    work = tempfile.mkdtemp(prefix="rocket_fmax_vivado_")
    try:
        os.makedirs(os.path.join(work, "rtl"))
        for name, text in rtl.files.items():
            with open(os.path.join(work, "rtl", name), "w") as f:
                f.write(text)
        with open(os.path.join(work, "bb_stubs.v"), "w") as f:
            f.write(black_box_stubs(rtl.stubs) if rtl.stubs else "")
        with open(os.path.join(work, "clock.xdc"), "w") as f:
            f.write(CLOCK_XDC.format(period_ns=period_ns))
        t0 = time.time()
        # A run killed for memory (OOM: returncode 137/-9, no slack) is retried once with
        # RETRY_THREADS: fewer forked synth workers, lower peak memory.
        for nthr in [threads] + ([RETRY_THREADS] if threads > RETRY_THREADS else []):
            with open(os.path.join(work, "run.tcl"), "w") as f:
                f.write(VIVADO_TCL.format(threads=nthr, top=rtl.top, part=part,
                                          files=" ".join(f"rtl/{n}" for n in sorted(rtl.files)),
                                          stubs="bb_stubs.v"))
            try:
                p = subprocess.run([vivado, "-mode", "batch", "-nojournal", "-log", "vivado.log",
                                    "-source", "run.tcl"], cwd=work, capture_output=True, text=True,
                                   timeout=timeout)
                rc, out = p.returncode, p.stdout + p.stderr
            except subprocess.TimeoutExpired:
                rc, out = -1, ""
            res.returncodes.append(rc)
            if rc == -1 or "@@SLACK" in out:
                break
        res.threads = nthr
        res.seconds = time.time() - t0
        res.log_tail = out[-20000:]

        def read(name):
            path = os.path.join(work, name)
            return open(path, errors="replace").read() if os.path.exists(path) else ""

        res.paths, res.summary, res.utilization = read("paths.rpt"), read("timing_summary.rpt"), read("util.rpt")
        for k, v in parse_vivado(out, period_ns).items():
            setattr(res, k, v)
        res.luts = _util(res.utilization, "Slice LUTs") or _util(res.utilization, "CLB LUTs")
        res.ffs = _util(res.utilization, "Slice Registers") or _util(res.utilization, "CLB Registers")
        res.dsps = _util(res.utilization, "DSPs")
        if rc != 0 or res.worst_slack_ns is None:
            res.error = (f"vivado returncode {'/'.join(map(str, res.returncodes))}" + (" (timeout)" if rc == -1 else "")
                         + (" (killed: OOM?)" if rc in (137, -9) else "") + ", slack not found")
            return res
        res.success = True
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
