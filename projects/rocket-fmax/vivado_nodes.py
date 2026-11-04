"""Chipyard's FPGA flow for the whole SoC (soc_bitstream.py): bitstream, timing and resources.

soc_bitstream runs on the `chipyard` worker: Chipyard's fpga/ Makefile (SUB_PROJECT=arty100t,
Arty A7-100T, fpga-shells scripts) elaborates the SoC with the rocket-chip patch applied, then
Vivado synthesizes, places, routes and writes the bitstream. Vivado is installed on the head
under ~/tools/Xilinx and mounted read-only at /tools/Xilinx (cluster.yaml); the Digilent board
files go to ~/tools/Xilinx/vivado-boards. A second Vivado run on post_route.dcp reports timing
per clock and the resources and timing of the RocketTile instance.

The chipyard image ships a stub `vivado` (FireSim) in /usr/local/bin: the Makefile gets a
wrapper put first on PATH, which sets the board repository and maxThreads, then sources the
script the Makefile gave. Every process runs in its own session (process group), so a timeout
or chia_cancel of the task kills Vivado too.

The report helpers (endpoint_families, compare_families, first_path, _util) come from the
Vivado loop of fmax-r4/r5 (commit d2a1ac0); its out-of-context nodes vivado_synth/vivado_impl
and the `vivado` worker were dropped on 04/11/2026 (last version: commit 2dd99af).
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from chia.base.ChiaFunction import ChiaFunction

import fmax_nodes as N

VIVADO_GLOBS = ["/tools/Xilinx/Vivado/*/bin/vivado", "/tools/Xilinx/*/Vivado/bin/vivado"]
BOARD_REPOS = ["/tools/Xilinx/vivado-boards/new/board_files"]   # git clone of Digilent/vivado-boards
FPGA_DIR = f"{N.CHIPYARD}/fpga"
SUB_PROJECT = "arty100t"
MODEL = "Arty100THarness"
CONFIG_PACKAGE = "chipyard.fpga.arty100t"
BOARD_GLOB = "*arty-a7-100*"
FREQ_SCALA = f"{FPGA_DIR}/src/main/scala/arty100t/ChiaFreqConfigs.scala"   # only while a run needs it
ENDPOINTS = 500
# chipyard chia_artifact added `def wf_active: Bool` to HasHarnessInstantiators (waveform window) but did
# not update the fpga/ harnesses, so chipyard_fpga does not compile. soc_bitstream adds the member to each
# harness for the run (after its `def success`) and restores the files afterwards.
FPGA_SCALA_GLOB = f"{FPGA_DIR}/src/main/scala/*/*.scala"
WF_ACTIVE_LINE = "def wf_active = false.B  // CHIA: chia_artifact HasHarnessInstantiators member, no waveform on FPGA"
REPORT_CAP = 3_000_000   # characters kept of each report

# vivado wrapper for the fpga Makefile: `-source X` becomes `-source pre.tcl`, which sources X.
WRAPPER_SH = """#!/bin/bash
args=()
while [ $# -gt 0 ]; do
  if [ "$1" = "-source" ]; then export CHIA_NEXT_TCL="$2"; args+=(-source "@PRE@"); shift 2
  else args+=("$1"); shift; fi
done
exec "@VIVADO@" "${args[@]}"
"""

PRE_TCL = """set_param general.maxThreads @THREADS@
if {"@REPO@" ne ""} { set_param board.repoPaths [list @REPO@] }
if {[llength [get_board_parts -quiet @BOARD@]] == 0} {
  puts "@@NO_BOARD board files @BOARD@ not found (board.repoPaths: @REPO@)"
  exit 3
}
source $::env(CHIA_NEXT_TCL)
"""

# On post_route.dcp. argv: dcp, tile module, number of tile endpoints to list.
# @@CLOCK <name> <period> <wns> <tns over the worst 5000 failing endpoints> <failing endpoints>
# @@TILE <instance>;  @@TILE_PATH <slack> <period> <clock> <start pin> <end pin>
METRICS_TCL = """set dcp [lindex $argv 0]
set tile_mod [lindex $argv 1]
set n_ep [lindex $argv 2]
open_checkpoint $dcp
report_utilization -file top_util.rpt
report_utilization -hierarchical -hierarchical_depth 6 -file hier_util.rpt
report_timing_summary -max_paths 10 -file timing_summary.rpt
report_route_status -file route_status.rpt
report_clocks -file clocks.rpt
foreach c [get_clocks] {
  set n [get_property NAME $c]
  set p [get_timing_paths -quiet -setup -group $n -max_paths 1 -nworst 1]
  if {[llength $p] == 0} continue
  set eps [get_timing_paths -quiet -setup -group $n -max_paths 5000 -nworst 1 -slack_lesser_than 0]
  set tns 0.0
  foreach e $eps { set tns [expr {$tns + [get_property SLACK $e]}] }
  puts "@@CLOCK $n [get_property PERIOD $c] [get_property SLACK $p] $tns [llength $eps]"
}
set tiles [get_cells -quiet -hier -filter "IS_PRIMITIVE == 0 && (ORIG_REF_NAME == $tile_mod || REF_NAME == $tile_mod)"]
if {[llength $tiles] == 0} {
  set tiles [get_cells -quiet -hier -filter {IS_PRIMITIVE == 0 && NAME =~ *element_reset_domain_rockettile}]
}
if {[llength $tiles] > 0} {
  set t [lindex $tiles 0]
  set tn [get_property NAME $t]
  puts "@@TILE $tn"
  report_utilization -cells $t -file tile_util.rpt
  set seq [get_cells -quiet -hier -filter "NAME =~ $tn/* && IS_SEQUENTIAL"]
  set p [get_timing_paths -quiet -setup -to $seq -max_paths 1 -nworst 1]
  if {[llength $p] > 0} {
    set ck [get_property ENDPOINT_CLOCK $p]
    set per ""
    catch { set per [get_property PERIOD [get_clocks $ck]] }
    puts "@@TILE_PATH [get_property SLACK $p] $per $ck [get_property STARTPOINT_PIN $p] [get_property ENDPOINT_PIN $p]"
    report_timing -setup -to $seq -max_paths 10 -nworst 1 -input_pins -file tile_paths.rpt
    set fh [open endpoints.txt w]
    foreach e [get_timing_paths -quiet -setup -to $seq -max_paths $n_ep -nworst 1] {
      puts $fh "[get_property ENDPOINT_PIN $e] [get_property SLACK $e]"
    }
    close $fh
  }
} else {
  puts "@@TILE none"
}
exit 0
"""

FREQ_CONFIG = """// Written by projects/rocket-fmax soc_bitstream.py for one run, removed after it.
package chipyard.fpga.arty100t

import org.chipsalliance.cde.config.Config

class {name} extends Config(
  new WithArty100TTweaks(freqMHz = {mhz}) ++
  new chipyard.config.WithBroadcastManager ++
  new chipyard.RocketConfig)
"""


def freq_config(mhz: int) -> tuple[str, str]:
    """(config name, Scala source) of RocketArty100TConfig at `mhz` instead of 50 MHz."""
    name = f"RocketArty100TF{mhz}Config"
    return name, FREQ_CONFIG.format(name=name, mhz=mhz)


def find_vivado() -> str:
    if os.environ.get("VIVADO"):
        return os.environ["VIVADO"]
    for g in VIVADO_GLOBS:
        hits = sorted(glob.glob(g))
        if hits:
            return hits[-1]
    return ""


def family_table(endpoints: str) -> dict:
    """{register (bit index -> [*], replicas merged): [worst slack, number of endpoints, worst pin]}."""
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
    return fam


def endpoint_families(endpoints: str, n: int = 15) -> str:
    """Endpoints grouped by register: worst slack, number of endpoints, the worst one."""
    rows = sorted(family_table(endpoints).items(), key=lambda kv: kv[1][0])[:n]
    out = [f"{'worst slack':>11}  {'bits':>4}  register (bit index -> [*]; worst endpoint)"]
    out += [f"{v[0]:11.3f}  {v[1]:4d}  {k}  ({v[2]})" for k, v in rows]
    return "\n".join(out)


def compare_families(parent_endpoints: str, child_endpoints: str, n: int = 12) -> str:
    """Worst slack per endpoint family on both sides, for the n worst families of either side.
    A family missing from one side's ENDPOINTS worst endpoints is shown as "> <that side's
    ENDPOINTS-th slack>" (it got better than every listed endpoint)."""
    pf, cf = family_table(parent_endpoints), family_table(child_endpoints)
    if not pf or not cf:
        return ""
    floor = {id(pf): max(v[0] for v in pf.values()), id(cf): max(v[0] for v in cf.values())}
    keys = sorted(set(pf) | set(cf), key=lambda k: min(pf.get(k, [9e9])[0], cf.get(k, [9e9])[0]))[:n]

    def cell(t, k):
        return f"{t[k][0]:8.3f}" if k in t else f"> {floor[id(t)]:.2f}".rjust(8)
    out = [f"{'parent':>8}  {'child':>8}  {'delta':>7}  register family"]
    for k in keys:
        delta = f"{cf[k][0] - pf[k][0]:+7.3f}" if k in pf and k in cf else " " * 7
        out.append(f"{cell(pf, k)}  {cell(cf, k)}  {delta}  {k}")
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


def _sub(text: str, **kw) -> str:
    for k, v in kw.items():
        text = text.replace(f"@{k}@", str(v))
    return text


def fix_fpga_harnesses(pattern: str = FPGA_SCALA_GLOB) -> dict:
    """Add `def wf_active` after `def success` in fpga harnesses that implement HasHarnessInstantiators
    without it. Returns {path: original text} for restore_files."""
    orig = {}
    for path in sorted(glob.glob(pattern)):
        with open(path) as f:
            text = f.read()
        if "HasHarnessInstantiators" not in text or "wf_active" in text:
            continue
        new = re.sub(r"^([ \t]*)(def success\b.*)$", lambda m: f"{m.group(1)}{m.group(2)}\n{m.group(1)}{WF_ACTIVE_LINE}",
                     text, flags=re.M)
        if new != text:
            orig[path] = text
            with open(path, "w") as f:
                f.write(new)
    return orig


def restore_files(orig: dict) -> None:
    for path, text in orig.items():
        with open(path, "w") as f:
            f.write(text)


def _run(cmd: str, cwd: str, log: str, timeout: int) -> int:
    """`cmd` under bash in its own session, output to `log`; -1 after a timeout."""
    with open(log, "w") as f:
        p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                             start_new_session=True)
    return _wait(p, timeout)


def _tail(path: str, n: int) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, errors="replace") as f:
        text = f.read()
    return text[-n:]


def _reports(d: str, names) -> dict:
    out = {}
    for name in names:
        path = os.path.join(d, name)
        if os.path.exists(path):
            with open(path, errors="replace") as f:
                out[name] = f.read()[:REPORT_CAP]
    return out


def parse_metrics(log: str) -> dict:
    """@@ lines of METRICS_TCL: clocks, tile instance, worst tile path."""
    clocks = []
    for m in re.finditer(r"^@@CLOCK (\S+) (\S+) (\S+) (\S+) (\d+)\s*$", log, re.M):
        try:
            clocks.append({"name": m.group(1), "period_ns": float(m.group(2)), "wns_ns": float(m.group(3)),
                           "tns_ns": float(m.group(4)), "failing": int(m.group(5))})
        except ValueError:
            continue
    t = re.search(r"^@@TILE (\S+)", log, re.M)
    tile = t.group(1) if t and t.group(1) != "none" else ""
    path = {}
    m = re.search(r"^@@TILE_PATH (\S+) (\S*) (\S+) (\S+) (\S+)\s*$", log, re.M)
    if m:
        try:
            path = {"slack_ns": float(m.group(1)), "period_ns": float(m.group(2)) if m.group(2) else None,
                    "clock": m.group(3), "start": m.group(4), "end": m.group(5)}
        except ValueError:
            path = {}
    return {"clocks": clocks, "tile": tile, "tile_path": path}


def util_numbers(text: str) -> dict:
    """LUT, LUTRAM, FF, BRAM tiles, DSP of a report_utilization table."""
    return {"luts": _util(text, "Slice LUTs"), "lutram": _util(text, "LUT as Memory"),
            "ffs": _util(text, "Slice Registers"), "brams": _util_f(text, "Block RAM Tile"),
            "dsps": _util(text, "DSPs")}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def soc_bitstream(rocket_head: str, patch: str, config: str, config_scala: str = "", threads: int = 8,
                  make_jobs: int = 8, timeout: int = 5 * 3600) -> dict:
    """Apply `patch` to rocket-chip, `make bitstream` of fpga/ (SUB_PROJECT=arty100t, CONFIG=`config`;
    `config_scala` is written to FREQ_SCALA for the run when not empty), then report post-route
    timing and resources. Leaves rocket-chip clean at `rocket_head`.

    Returns a dict (no dataclass: nothing to import when the head reads it back): ok (bitstream
    written), error, make_rc, metrics_rc, minutes, vivado, build_dir, make_log_tail, reports
    ({file: text}), clocks, tile, tile_path, util (whole chip), tile_util, endpoints, bit (bytes).
    """
    t0 = time.time()
    res = {"ok": False, "error": "", "make_rc": None, "metrics_rc": None, "minutes": 0.0, "vivado": "",
           "build_dir": "", "make_log_tail": "", "reports": {}, "clocks": [], "tile": "", "tile_path": {},
           "util": {}, "tile_util": {}, "endpoints": "", "bit": b""}
    vivado = find_vivado()
    if not vivado:
        res["error"] = (f"không thấy Vivado ({', '.join(VIVADO_GLOBS)}): cluster.yaml phải mount "
                        "~/tools/Xilinx vào container chipyard")
        return res
    res["vivado"] = vivado
    repo = next((r for r in BOARD_REPOS if os.path.isdir(r)), "")
    work = tempfile.mkdtemp(prefix="chia_soc_")
    harness_orig = {}
    try:
        status = N.reset_rocket_local(rocket_head, patch)
        harness_orig = fix_fpga_harnesses()
        if config_scala:
            with open(FREQ_SCALA, "w") as f:
                f.write(config_scala)
        pre = os.path.join(work, "pre.tcl")
        with open(pre, "w") as f:
            f.write(_sub(PRE_TCL, THREADS=threads, REPO=repo, BOARD=BOARD_GLOB))
        os.makedirs(os.path.join(work, "bin"))
        wrapper = os.path.join(work, "bin", "vivado")
        with open(wrapper, "w") as f:
            f.write(_sub(WRAPPER_SH, PRE=pre, VIVADO=vivado))
        os.chmod(wrapper, 0o755)
        make = (f"make SUB_PROJECT={SUB_PROJECT} CONFIG={config} CONFIG_PACKAGE={CONFIG_PACKAGE}")
        env = (f"source {N.CHIPYARD}/env.sh && export PATH={shlex.quote(os.path.join(work, 'bin'))}:$PATH "
               f"&& export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8")
        log = os.path.join(work, "make.log")
        with open(log, "w") as f:
            f.write(f"rocket-chip: {status.strip() or 'clean'}\n")
            f.write(f"fpga harness wf_active added: {sorted(os.path.relpath(p, FPGA_DIR) for p in harness_orig)}\n")
        rc = _run(f"{env} && {make} clean && {make} -j {make_jobs} bitstream >> {shlex.quote(log)} 2>&1",
                  FPGA_DIR, os.path.join(work, "make_outer.log"), timeout)
        res["make_rc"] = rc
        res["make_log_tail"] = _tail(log, 60000) + _tail(os.path.join(work, "make_outer.log"), 5000)
        build = glob.glob(f"{FPGA_DIR}/generated-src/*.{MODEL}.{config}")
        if not build:
            res["error"] = f"make returncode {rc}" + (" (timeout)" if rc == -1 else "") + ": không có thư mục build"
            return res
        build_dir = build[0]
        res["build_dir"] = build_dir
        obj = os.path.join(build_dir, "obj")
        bit = os.path.join(obj, f"{MODEL}.bit")
        if os.path.exists(bit):
            with open(bit, "rb") as f:
                res["bit"] = f.read()
        res["reports"] = {f"fpga_shells_{k}": v for k, v in
                          _reports(os.path.join(obj, "report"), ("timing.txt", "utilization.txt", "clocks.txt",
                                                                 "drc.txt")).items()}
        dcp = os.path.join(obj, "post_route.dcp")
        if os.path.exists(dcp):
            mdir = os.path.join(work, "metrics")
            os.makedirs(mdir)
            with open(os.path.join(mdir, "metrics.tcl"), "w") as f:
                f.write(METRICS_TCL)
            mrc = _run(f"export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 && {shlex.quote(wrapper)} -mode batch -nojournal "
                       f"-log vivado.log -source metrics.tcl -tclargs {shlex.quote(dcp)} {N.TOP} {ENDPOINTS}",
                       mdir, os.path.join(mdir, "stdout.txt"), 3600)
            res["metrics_rc"] = mrc
            out = _tail(os.path.join(mdir, "stdout.txt"), 10_000_000)
            res.update(parse_metrics(out))
            res["reports"].update(_reports(mdir, ("top_util.rpt", "hier_util.rpt", "timing_summary.rpt",
                                                  "route_status.rpt", "clocks.rpt", "tile_util.rpt",
                                                  "tile_paths.rpt")))
            res["reports"]["metrics_stdout.txt"] = out[-50000:]
            res["endpoints"] = _reports(mdir, ("endpoints.txt",)).get("endpoints.txt", "")
            res["util"] = util_numbers(res["reports"].get("top_util.rpt", ""))
            res["tile_util"] = util_numbers(res["reports"].get("tile_util.rpt", ""))
        res["ok"] = bool(res["bit"])
        if not res["ok"]:
            res["error"] = (f"make returncode {rc}" + (" (timeout)" if rc == -1 else "")
                            + (" (killed: OOM?)" if rc in (137, -9) else "")
                            + ("; chưa tới route" if not os.path.exists(dcp) else "") + ": không có bitstream")
            if "Failed to meet timing" in res["make_log_tail"]:
                res["error"] += "; route xong nhưng không đạt timing (fpga-shells không ghi bitstream; WNS vẫn đo)"
            if "Compilation failed" in res["make_log_tail"]:
                res["error"] += "; Scala không compile (xem [error] trong make_log_tail.txt)"
            if "@@NO_BOARD" in res["make_log_tail"]:
                res["error"] += f"; thiếu board files {BOARD_GLOB} (BOARD_REPOS: {BOARD_REPOS})"
        return res
    finally:
        res["minutes"] = (time.time() - t0) / 60
        if config_scala and os.path.exists(FREQ_SCALA):
            os.unlink(FREQ_SCALA)
        restore_files(harness_orig)
        N.reset_rocket_local(rocket_head, "")
        shutil.rmtree(work, ignore_errors=True)
