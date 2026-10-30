"""Vivado timing of RocketTile: synth_design (vivado_synth, the agent's synth_ab A/B) and
place + route (vivado_impl, what the loop accepts or rejects on).

Runs on the `vivado` worker (cluster.yaml: rocket_vivado, docker/VivadoDockerfile, Vivado
installed on the head and mounted at /tools/Xilinx). Out-of-context on rtl.top, one clock
`clock` at a fixed period, I/O with 0 delay against it. The SRAM *_ext modules are their
behavioral models (rtl.sram_models: the *_ext texts Chipyard generates for simulation plus the
split_*_ext modules they instantiate), which Vivado maps to BRAM / LUTRAM.

Every Vivado process runs in its own session (process group), so chia_cancel of the task also
kills it and the synth workers it forks.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import signal
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

# Reports of the design in memory (after synth_design or route_design). endpoints.txt: the worst
# ENDPOINTS endpoints, one per line: "<pin> <slack>".
TIMING_TCL = """\
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
set fh [open endpoints.txt w]
foreach e [get_timing_paths -setup -max_paths {endpoints} -nworst 1] {{
    puts $fh "[get_property ENDPOINT_PIN $e] [get_property SLACK $e]"
}}
close $fh
"""
ENDPOINTS = 500

SYNTH_TCL = """\
set_param general.maxThreads {threads}
read_verilog -sv {files}
read_xdc clock.xdc
synth_design -top {top} -part {part} -mode out_of_context
{extra}""" + TIMING_TCL

# Place-and-route directives of one vivado_impl run each (all from the same synth checkpoint).
# viv-impl2 (30/10/2026): `default` was never the best, so the loop runs explore + extratiming.
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
    crit_start: str = ""                  # pins, e.g. dcache/s2_data_reg[13]/C
    crit_end: str = ""
    luts: int | None = None
    ffs: int | None = None
    dsps: int | None = None
    brams: float | None = None            # Block RAM tiles
    lutram: int | None = None             # LUT as memory
    paths: str = ""                       # report_timing -input_pins, top 10
    summary: str = ""                     # report_timing_summary (+ route status)
    utilization: str = ""
    log_tail: str = ""
    version: str = ""
    seconds: float = 0.0
    threads: int = 0                      # maxThreads of the run that produced the result
    returncodes: list = field(default_factory=list)  # one per run; two when retried
    error: str = ""
    endpoints: str = ""                   # worst ENDPOINTS endpoints: "<pin> <slack>" per line
    families: str = ""                    # endpoint_families(endpoints)


def find_vivado() -> str:
    if os.environ.get("VIVADO"):
        return os.environ["VIVADO"]
    for g in VIVADO_GLOBS:
        hits = sorted(glob.glob(g))
        if hits:
            return hits[-1]
    return ""


# ---------------------------------------------------------------------------
# names and reports (pure Python, also used by the loop and the A/B tool)
# ---------------------------------------------------------------------------

def cell_of(pin: str) -> str:
    """Register (cell) of a Vivado pin, bit index and replica suffix dropped:
    "dcache/s2_req_addr_reg[2]_replica_3/C" -> "dcache/s2_req_addr_reg"."""
    cell = pin.rsplit("/", 1)[0] if "/" in pin else pin
    cell = re.sub(r"_replica(_\d+)?$", "", cell)
    return re.sub(r"\[\d+\]", "", cell)


def dotted(pin: str) -> str:
    """A Vivado pin as the dotted instance name fmax_nodes.pick_target takes:
    "core/mem_reg_wdata_reg[6]/D" -> "core.mem_reg_wdata_reg"."""
    return cell_of(pin).replace("/", ".")


def endpoint_families(endpoints: str, n: int = 15) -> str:
    """Endpoints grouped by register (bit index -> [*], replicas merged): worst slack, number of
    endpoints, the worst one."""
    fam: dict[str, list] = {}
    for line in endpoints.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            slack = float(parts[1])
        except ValueError:
            continue
        pin = parts[0]
        cell = re.sub(r"_replica(_\d+)?$", "", pin.rsplit("/", 1)[0] if "/" in pin else pin)
        key = re.sub(r"\[\d+\]", "[*]", cell)
        f = fam.setdefault(key, [slack, 0, pin])
        f[1] += 1
        if slack < f[0]:
            f[0], f[2] = slack, pin
    rows = sorted(fam.items(), key=lambda kv: kv[1][0])[:n]
    out = [f"{'worst slack':>11}  {'bits':>4}  register (bit index -> [*]; worst endpoint)"]
    out += [f"{v[0]:11.3f}  {v[1]:4d}  {k}  ({v[2]})" for k, v in rows]
    return "\n".join(out)


def first_path(paths: str, limit: int = 9000) -> str:
    """The first path of a Vivado report_timing output (from its `Slack` line to the next)."""
    i = paths.find("Slack")
    if i < 0:
        return ""
    j = paths.find("\nSlack", i + 1)
    block = paths[i:j if j >= 0 else len(paths)].rstrip()
    return block if len(block) <= limit else block[:limit] + "\n... (truncated)"


def _util(text: str, name: str) -> int | None:
    m = re.search(rf"^\|\s*{re.escape(name)}\*?\s*\|\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def _util_f(text: str, name: str) -> float | None:
    m = re.search(rf"^\|\s*{re.escape(name)}\*?\s*\|\s*([\d.]+)", text, re.M)
    return float(m.group(1)) if m else None


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


# ---------------------------------------------------------------------------
# running Vivado
# ---------------------------------------------------------------------------

def _read(work: str, name: str) -> str:
    path = os.path.join(work, name)
    return open(path, errors="replace").read() if os.path.exists(path) else ""


def _start(vivado: str, d: str) -> subprocess.Popen:
    with open(os.path.join(d, "stdout.txt"), "w") as log:
        p = subprocess.Popen([vivado, "-mode", "batch", "-nojournal", "-log", "vivado.log", "-source", "run.tcl"],
                             cwd=d, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    p.t0, p.dir = time.time(), d
    return p


def _kill(p: subprocess.Popen):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    p.wait()


def _wait(p: subprocess.Popen, timeout: int) -> int:
    """Return code of a run; -1 after a timeout. Kills the run if the task is cancelled."""
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        return -1
    except BaseException:
        _kill(p)
        raise


def _result(d: str, rc: int, part: str, period_ns: float, vivado: str, threads: int, t0: float) -> VivadoResult:
    out = _read(d, "stdout.txt")
    r = VivadoResult(success=False, part=part, period_ns=period_ns, version=vivado, threads=threads,
                     returncodes=[rc], seconds=time.time() - t0, log_tail=out[-20000:],
                     paths=_read(d, "paths.rpt"), utilization=_read(d, "util.rpt"),
                     summary=(_read(d, "timing_summary.rpt") + "\n" + _read(d, "route_status.rpt")).strip(),
                     endpoints=_read(d, "endpoints.txt"))
    for k, v in parse_vivado(out, period_ns).items():
        setattr(r, k, v)
    r.luts = _util(r.utilization, "Slice LUTs") or _util(r.utilization, "CLB LUTs")
    r.ffs = _util(r.utilization, "Slice Registers") or _util(r.utilization, "CLB Registers")
    r.dsps = _util(r.utilization, "DSPs")
    r.brams = _util_f(r.utilization, "Block RAM Tile")
    r.lutram = _util(r.utilization, "LUT as Memory")
    r.families = endpoint_families(r.endpoints) if r.endpoints else ""
    if rc != 0 or r.worst_slack_ns is None:
        r.error = (f"vivado returncode {rc}" + (" (timeout)" if rc == -1 else "")
                   + (" (killed: OOM?)" if rc in (137, -9) else "") + ", slack not found")
    return r


def _write_inputs(work: str, rtl, period_ns: float) -> list[str]:
    """RTL, SRAM models and clock constraint into `work`; the Verilog files to read."""
    os.makedirs(os.path.join(work, "rtl"), exist_ok=True)
    for name, text in rtl.files.items():
        with open(os.path.join(work, "rtl", name), "w") as f:
            f.write(text)
    files = [f"rtl/{n}" for n in sorted(rtl.files)]
    if rtl.blackboxes:
        if not getattr(rtl, "sram_models", ""):
            raise ValueError("rtl has no sram_models (TileRTL cached before the field existed: bump its tag)")
        with open(os.path.join(work, "mems.v"), "w") as f:
            f.write(rtl.sram_models)
        files.append("mems.v")
    with open(os.path.join(work, "clock.xdc"), "w") as f:
        f.write(CLOCK_XDC.format(period_ns=period_ns))
    return files


def _synth(vivado: str, work: str, rtl, period_ns: float, part: str, threads: int, timeout: int,
           extra: str = "") -> VivadoResult:
    """synth_design in `work`; a run killed for memory (OOM: 137/-9, no slack) is retried once
    with RETRY_THREADS (fewer forked synth workers, lower peak memory)."""
    files = _write_inputs(work, rtl, period_ns)
    rcs = []
    for nthr in [threads] + ([RETRY_THREADS] if threads > RETRY_THREADS else []):
        with open(os.path.join(work, "run.tcl"), "w") as f:
            f.write(SYNTH_TCL.format(threads=nthr, top=rtl.top, part=part, files=" ".join(files),
                                     extra=extra, endpoints=ENDPOINTS))
        t0 = time.time()
        rc = _wait(_start(vivado, work), timeout)
        rcs.append(rc)
        res = _result(work, rc, part, period_ns, vivado, nthr, t0)
        if rc not in (137, -9) or res.worst_slack_ns is not None:
            break
    res.returncodes = rcs
    res.success = not res.error
    return res


@ChiaFunction(resources={"vivado": 1}, num_cpus=4, max_retries=0)
def vivado_synth(rtl, period_ns: float, part: str, threads: int = 8, timeout: int = 3 * 3600) -> VivadoResult:
    """synth_design of rtl.top (a sub-block or the whole tile): the A/B of synth_ab."""
    vivado = find_vivado()
    if not vivado:
        return VivadoResult(success=False, part=part, period_ns=period_ns,
                            error=f"vivado not found ({', '.join(VIVADO_GLOBS)}; or set VIVADO)")
    work = tempfile.mkdtemp(prefix="rocket_fmax_vivado_")
    try:
        return _synth(vivado, work, rtl, period_ns, part, threads, timeout)
    except ValueError as e:
        return VivadoResult(success=False, part=part, period_ns=period_ns, error=str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@dataclass
class VivadoImplResult:
    success: bool                         # synth ok and at least one strategy routed with a slack
    part: str
    period_ns: float
    synth: VivadoResult | None = None     # synth_design of the impl input
    runs: dict = field(default_factory=dict)   # {strategy: VivadoResult, post-route}
    place_slack: dict = field(default_factory=dict)  # {strategy: worst slack after place_design}
    best: str = ""                        # strategy with the best post-route worst slack
    error: str = ""

    @property
    def result(self) -> VivadoResult | None:
        return self.runs.get(self.best)


@ChiaFunction(resources={"vivado": 1}, num_cpus=4, max_retries=0)
def vivado_impl(rtl, period_ns: float, part: str, strategies=("explore", "extratiming"),
                threads: int = 8, impl_threads: int = 4, parallel: int = 1,
                timeout: int = 4 * 3600) -> VivadoImplResult:
    """synth_design, then one place + route per strategy, at most `parallel` at a time from the
    same checkpoint; a run killed for memory is redone alone afterwards. The best post-route
    worst slack is the result."""
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
        try:
            res.synth = syn = _synth(vivado, work, rtl, period_ns, part, threads, timeout,
                                     extra="write_checkpoint -force synth.dcp\n")
        except ValueError as e:
            res.error = str(e)
            return res
        if not os.path.exists(os.path.join(work, "synth.dcp")):
            syn.success = False
            res.error = syn.error = f"synth_design returncode {syn.returncodes}, no checkpoint"
            return res

        def start(name):
            d = os.path.join(work, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "run.tcl"), "w") as f:
                f.write(IMPL_TCL.format(threads=impl_threads, endpoints=ENDPOINTS, **IMPL_STRATEGIES[name]))
            return _start(vivado, d)

        def finish(name, p, rc):
            r = _result(p.dir, rc, part, period_ns, vivado, impl_threads, p.t0)
            m = re.search(r"^@@PLACE_SLACK (\S+)", _read(p.dir, "stdout.txt"), re.M)
            try:
                res.place_slack[name] = float(m.group(1)) if m else None
            except ValueError:
                res.place_slack[name] = None
            errs = re.search(r"# of nets with routing errors\.*\s*:\s*(\d+)", r.summary)
            if not r.error and errs and int(errs.group(1)) > 0:
                r.error = f"{errs.group(1)} nets with routing errors"
            r.success = not r.error
            return r

        def run_all(names, width):
            todo, running, done = list(names), {}, {}
            try:
                while todo or running:
                    while todo and len(running) < width:
                        n = todo.pop(0)
                        running[n] = start(n)
                    time.sleep(5)
                    for n, p in list(running.items()):
                        rc = p.poll()
                        if rc is None and time.time() - p.t0 > timeout:
                            _kill(p)
                            rc = -1
                        if rc is not None:
                            done[n] = finish(n, running.pop(n), rc)
            except BaseException:            # cancelled: no Vivado left behind
                for p in running.values():
                    _kill(p)
                raise
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
