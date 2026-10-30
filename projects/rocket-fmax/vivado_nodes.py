"""Vivado timing of RocketTile: synth_design (vivado_synth) and post-route (vivado_impl).

Runs on the `vivado` worker (cluster.yaml: rocket_vivado, docker/VivadoDockerfile,
Vivado installed on the head and mounted at /tools/Xilinx). Only a second opinion:
the loop still accepts and rejects on the sky130 numbers; rocket_fmax.py writes
these results to vivado.csv (--vivado-part).

Same netlist inputs as synth_sta: the TileRTL of a build, SRAM *_ext modules as
black boxes (their pins are unconstrained), I/O with 0 delay against `clock`.
Out-of-context synthesis, so the part's I/O count does not matter.

vivado_impl goes on to place and route (--vivado-impl-probe). Black boxes cannot be
placed, so there the SRAM *_ext modules are their behavioral models (rtl.blackboxes,
the text Chipyard generates for simulation), which Vivado maps to BRAM / LUTRAM.
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
read_verilog -sv {stubs}
read_xdc clock.xdc
synth_design -top {top} -part {part} -mode out_of_context
report_timing_summary -max_paths 10 -file timing_summary.rpt
report_timing -setup -max_paths 10 -nworst 1 -input_pins -file paths.rpt
report_utilization -file util.rpt
{extra}set p [get_timing_paths -setup -max_paths 1 -nworst 1]
puts "@@SLACK [get_property SLACK $p]"
puts "@@START [get_property STARTPOINT_PIN $p]"
puts "@@END [get_property ENDPOINT_PIN $p]"
set eps [get_timing_paths -setup -max_paths 2000 -nworst 1 -slack_lesser_than 0]
set tns 0.0
foreach e $eps {{ set tns [expr {{$tns + [get_property SLACK $e]}}] }}
puts "@@TNS2000 $tns"
puts "@@FAILING [llength $eps]"
"""


TIMING_TCL = VIVADO_TCL[VIVADO_TCL.index("report_timing_summary"):].replace("{extra}", "")

# Place-and-route directives of one vivado_impl run each (all from the same synth checkpoint).
IMPL_STRATEGIES = {
    "default": dict(opt="Default", place="Default", phys="Default", route="Default", post=""),
    "explore": dict(opt="Explore", place="Explore", phys="AggressiveExplore", route="AggressiveExplore",
                    post="AggressiveExplore"),
    "extratiming": dict(opt="Explore", place="ExtraTimingOpt", phys="AggressiveExplore", route="Explore",
                        post="AggressiveExplore"),
}

# The clock comes from a global buffer outside the OOC block (otherwise it is routed on
# general fabric, with a skew no real board has).
IMPL_TCL = """\
set_param general.maxThreads {threads}
open_checkpoint ../synth.dcp
set_property HD.CLK_SRC BUFGCTRL_X0Y0 [get_ports clock]
opt_design -directive {opt}
place_design -directive {place}
puts "@@PLACE_SLACK [get_property SLACK [get_timing_paths -setup -max_paths 1 -nworst 1]]"
phys_opt_design -directive {phys}
route_design -directive {route}
if {{"{post}" ne "" && [get_property SLACK [get_timing_paths -setup -max_paths 1 -nworst 1]] < 0}} {{
    phys_opt_design -directive {post}
}}
report_route_status -file route_status.rpt
""" + TIMING_TCL


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
    brams: float | None = None            # Block RAM tiles (vivado_impl)
    lutram: int | None = None             # LUT as memory (vivado_impl)
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


def _util_f(text: str, name: str) -> float | None:
    m = re.search(rf"^\|\s*{re.escape(name)}\*?\s*\|\s*([\d.]+)", text, re.M)
    return float(m.group(1)) if m else None


def _fill_util(res: VivadoResult):
    res.luts = _util(res.utilization, "Slice LUTs") or _util(res.utilization, "CLB LUTs")
    res.ffs = _util(res.utilization, "Slice Registers") or _util(res.utilization, "CLB Registers")
    res.dsps = _util(res.utilization, "DSPs")
    res.brams = _util_f(res.utilization, "Block RAM Tile")
    res.lutram = _util(res.utilization, "LUT as Memory")


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
                f.write(VIVADO_TCL.format(threads=nthr, top=rtl.top, part=part, extra="",
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
        _fill_util(res)
        if rc != 0 or res.worst_slack_ns is None:
            res.error = (f"vivado returncode {'/'.join(map(str, res.returncodes))}" + (" (timeout)" if rc == -1 else "")
                         + (" (killed: OOM?)" if rc in (137, -9) else "") + ", slack not found")
            return res
        res.success = True
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


@dataclass
class VivadoImplResult:
    success: bool                         # synth ok and at least one strategy routed with a slack
    part: str
    period_ns: float
    synth: VivadoResult | None = None     # synth_design of the impl input (behavioral SRAMs)
    runs: dict = field(default_factory=dict)   # {strategy: VivadoResult, post-route}
    place_slack: dict = field(default_factory=dict)  # {strategy: worst slack after place_design}
    best: str = ""                        # strategy with the best post-route worst slack
    error: str = ""

    @property
    def result(self) -> VivadoResult | None:
        return self.runs.get(self.best)


def _read(work: str, name: str) -> str:
    path = os.path.join(work, name)
    return open(path, errors="replace").read() if os.path.exists(path) else ""


def _impl_run(vivado: str, d: str, timeout: int) -> subprocess.Popen:
    log = open(os.path.join(d, "stdout.txt"), "w")
    p = subprocess.Popen([vivado, "-mode", "batch", "-nojournal", "-log", "vivado.log", "-source", "run.tcl"],
                         cwd=d, stdout=log, stderr=subprocess.STDOUT, text=True)
    p.t0, p.dir, p.timeout = time.time(), d, timeout
    return p


@ChiaFunction(resources={"vivado": 1}, num_cpus=4, max_retries=0)
def vivado_impl(rtl, period_ns: float, part: str, strategies=("default", "explore", "extratiming"),
                threads: int = 8, impl_threads: int = 4, parallel: int = 3,
                timeout: int = 4 * 3600) -> VivadoImplResult:
    """synth_design (SRAMs as behavioral models), then one place + route per strategy, at
    most `parallel` at a time from the same checkpoint; a run killed for memory is redone
    alone afterwards. The best post-route worst slack is the result."""
    res = VivadoImplResult(success=False, part=part, period_ns=period_ns)
    vivado = find_vivado()
    if not vivado:
        res.error = f"vivado not found ({', '.join(VIVADO_GLOBS)}; or set VIVADO)"
        return res
    unknown = [s for s in strategies if s not in IMPL_STRATEGIES]
    if unknown:
        res.error = f"unknown strategies {unknown} (have {sorted(IMPL_STRATEGIES)})"
        return res
    work = tempfile.mkdtemp(prefix="rocket_fmax_vivimpl_")
    try:
        os.makedirs(os.path.join(work, "rtl"))
        for name, text in rtl.files.items():
            with open(os.path.join(work, "rtl", name), "w") as f:
                f.write(text)
        with open(os.path.join(work, "mems.v"), "w") as f:
            f.write("\n\n".join(rtl.blackboxes.values()))
        with open(os.path.join(work, "clock.xdc"), "w") as f:
            f.write(CLOCK_XDC.format(period_ns=period_ns))
        with open(os.path.join(work, "run.tcl"), "w") as f:
            f.write(VIVADO_TCL.format(threads=threads, top=rtl.top, part=part, stubs="mems.v",
                                      files=" ".join(f"rtl/{n}" for n in sorted(rtl.files)),
                                      extra="write_checkpoint -force synth.dcp\n"))
        syn = VivadoResult(success=False, part=part, period_ns=period_ns, version=vivado, threads=threads)
        t0 = time.time()
        try:
            p = subprocess.run([vivado, "-mode", "batch", "-nojournal", "-log", "vivado.log", "-source", "run.tcl"],
                               cwd=work, capture_output=True, text=True, timeout=timeout)
            rc, out = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            rc, out = -1, ""
        syn.returncodes, syn.seconds, syn.log_tail = [rc], time.time() - t0, out[-20000:]
        syn.paths, syn.summary, syn.utilization = (_read(work, "paths.rpt"), _read(work, "timing_summary.rpt"),
                                                   _read(work, "util.rpt"))
        for k, v in parse_vivado(out, period_ns).items():
            setattr(syn, k, v)
        _fill_util(syn)
        res.synth = syn
        if rc != 0 or not os.path.exists(os.path.join(work, "synth.dcp")):
            syn.error = res.error = f"synth_design returncode {rc}, no checkpoint"
            return res
        syn.success = syn.worst_slack_ns is not None

        def start(name):
            d = os.path.join(work, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "run.tcl"), "w") as f:
                f.write(IMPL_TCL.format(threads=impl_threads, **IMPL_STRATEGIES[name]))
            return _impl_run(vivado, d, timeout)

        def finish(name, p, rc):
            d = p.dir
            out = _read(d, "stdout.txt")
            r = VivadoResult(success=False, part=part, period_ns=period_ns, version=vivado, threads=impl_threads,
                             returncodes=[rc], seconds=time.time() - p.t0, log_tail=out[-20000:],
                             paths=_read(d, "paths.rpt"), summary=_read(d, "timing_summary.rpt") + "\n"
                             + _read(d, "route_status.rpt"), utilization=_read(d, "util.rpt"))
            for k, v in parse_vivado(out, period_ns).items():
                setattr(r, k, v)
            _fill_util(r)
            m = re.search(r"^@@PLACE_SLACK (\S+)", out, re.M)
            try:
                res.place_slack[name] = float(m.group(1)) if m else None
            except ValueError:
                res.place_slack[name] = None
            errs = re.search(r"# of nets with routing errors\.*\s*:\s*(\d+)", r.summary)
            if rc != 0 or r.worst_slack_ns is None:
                r.error = (f"returncode {rc}" + (" (timeout)" if rc == -1 else "")
                           + (" (killed: OOM?)" if rc in (137, -9) else "") + ", slack not found")
            elif errs and int(errs.group(1)) > 0:
                r.error = f"{errs.group(1)} nets with routing errors"
            else:
                r.success = True
            return r

        def run_all(names, width):
            todo, running, done = list(names), {}, {}
            while todo or running:
                while todo and len(running) < width:
                    n = todo.pop(0)
                    running[n] = start(n)
                time.sleep(5)
                for n, p in list(running.items()):
                    rc = p.poll()
                    if rc is None and time.time() - p.t0 > p.timeout:
                        p.kill()
                        p.wait()
                        rc = -1
                    if rc is not None:
                        done[n] = finish(n, running.pop(n), rc)
            return done

        res.runs = run_all(list(strategies), max(1, parallel))
        oom = [n for n, r in res.runs.items() if r.returncodes[0] in (137, -9)]
        if oom and parallel > 1:
            for n, r in run_all(oom, 1).items():
                r.returncodes = res.runs[n].returncodes + r.returncodes
                res.runs[n] = r
        ok = {n: r for n, r in res.runs.items() if r.success}
        if not ok:
            res.error = "; ".join(f"{n}: {r.error}" for n, r in res.runs.items())
            return res
        res.best = max(ok, key=lambda n: ok[n].worst_slack_ns)
        res.success = True
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
