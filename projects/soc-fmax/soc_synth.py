"""Synth + timing of the whole SoC (DigitalTop of RocketConfig), for the baseline and a few rocket-chip patches.

Project soc-fmax, step 1 (from rocket-fmax's soc_synth.py, commit 70de0bf). For each point:
  1. build RocketConfig with the patch, take the DigitalTop hierarchy (system bus, L2, peripherals, tile);
  2. Yosys, flow v3 (hierarchical), sky130hd, `abc -D` at --period-ns;
  3. timing, once per --sram-access-ns value, each SRAM (*_ext) a fake macro (generated Liberty: clock ->
     read data = the access time, setup --sram-setup-ns), one ideal clock on every clock port, register
     clock pin and SRAM clock pin. Changes from rocket-fmax (soc-synth-5 measured a reset path through
     unbuffered nets, and its per-region columns were empty):
       - asynchronous reset/set pins of the flops are not timed (recovery/removal off, false path to them);
       - the first stage of every synchronizer (*sync_0_reg*: reset synchronizers, AsyncQueue crossings)
         is not timed into (its input is asynchronous by design);
       - --buffer repair (default): OpenROAD reads the sky130hd LEF (downloaded once, sha256-pinned) and a
         generated LEF of the fake SRAMs, then `repair_design` buffers high-fanout nets (no placement, no
         wire RC: pin capacitances only, as before). The worst slack before the repair is kept;
         --buffer none: OpenSTA alone as in rocket-fmax;
       - the per-region worst paths come from find_timing_paths (start, end, slack printed by Tcl), not from
         parsing `report_checks -format summary`.
  4. regions: tile (both ends in RocketTile), uncore (both outside), tile<->uncore, I/O (a port), SRAM end.

Outputs in --out-dir/<run-name>/: summary.md, soc_synth.csv, runs/<point>/{point.patch, build.log (on
failure), result.json, sta_<access>.txt, stat.txt, yosys_log_tail.txt}. A point with a result.json is not
rerun (--force).

  cd ~/CHIA-workflow/projects/soc-fmax
  chia job submit --no-wait --submission-id soc-fmax-1 --working-dir . -- \\
    python soc_synth.py --run-name soc-fmax-1 \\
      --patch asic-7=~/rocket-fmax-v2/asic-7/patches/best.patch
  Tile + L2 (l2/ loop result), adding a point to the same run (points with a result.json are skipped):
    ... --patch asic-7=... --patch asic-7-l2=<tile patch> --l2-patch asic-7-l2=<l2 best.patch>
  Target 100 MHz: --period-ns 10 (STA clock, slack < 0 = misses 100 MHz), --abc-ns 6 (abc -D),
  --repair-timing (OpenROAD repair_timing -setup), --no-baseline; summary.md then groups the failing
  paths by startpoint register.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import ray

import fmax_nodes as N
from chia.base.ChiaFunction import ChiaFunction, get
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode

SOC_TOP = "DigitalTop"
REGIONS = ("tile", "uncore", "tile<->uncore", "I/O")
CSV_FIELDS = ["point", "access_ns", "period_ns", "buffer", "worst_slack_ns", "fmax_mhz", "worst_pre_ns", "fmax_pre_mhz",
              "worst_region", "tns_ns", *[f"{r}_slack_ns" for r in REGIONS], "sram_slack_ns", "n_async_pins",
              "n_sync_cells", "crit_start", "crit_end", "area_um2", "minutes"]

# sky130hd LEF from OpenROAD-flow-scripts, the same commit as the liberty of the eda image.
ORFS_COMMIT = "cca3a717920067d8f6e085ce886fe4ecdc876614"
LEFS = {
    "sky130_fd_sc_hd.tlef": "8e99b4e8b016db0713029ebcae6b2cc2aedd9c2c49682e2f23521dd0b1a2085e",
    "sky130_fd_sc_hd_merged.lef": "44180aaa0068b1fcb0789f128b090304c01ad48f511d3f7afa66c90b3e176ec5",
}
LEF_DIR = "/home/ray/pdk/sky130hd/lef"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--patch", action="append", default=[], metavar="NAME=PATH",
                   help="a rocket-chip patch (repeatable); the baseline is always the first point")
    p.add_argument("--l2-patch", action="append", default=[], metavar="NAME=PATH",
                   help="rocket-chip-inclusive-cache patch (from the l2/ loop) for point NAME, which must be a --patch")
    p.add_argument("--period-ns", type=float, default=6.0, help="clock of STA and repair_timing (asic-7 used 6)")
    p.add_argument("--abc-ns", type=float, default=None, help="abc -D target (default: --period-ns)")
    p.add_argument("--repair-timing", action="store_true",
                   help="after repair_design: OpenROAD repair_timing -setup against --period-ns (no RTL change)")
    p.add_argument("--what-if-false-from", action="append", default=[], metavar="CELL_PATTERN",
                   help="what-if only: set_false_path -from these cells (full path, e.g. cbus/atomics/cam_a_0_*), "
                        "to see the next paths once a known one is fixed")
    p.add_argument("--no-baseline", action="store_true", help="skip the baseline point")
    p.add_argument("--sram-access-ns", default="0,2",
                   help="comma-separated SRAM clock-to-read-data times, one timing run each (0 = ideal SRAM)")
    p.add_argument("--sram-setup-ns", type=float, default=0.2)
    p.add_argument("--buffer", choices=("repair", "none"), default="repair",
                   help="repair: OpenROAD repair_design before timing; none: OpenSTA alone (as rocket-fmax)")
    p.add_argument("--max-fanout", type=int, default=16, help="set_max_fanout for repair_design")
    p.add_argument("--top", default=SOC_TOP, help="module synthesized (DigitalTop; ChipTop adds IO cells and clocking)")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3 * 3600)
    p.add_argument("--yosys-timeout-hours", type=float, default=6.0)
    p.add_argument("--out-dir", default="~/soc-fmax")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    a.out_dir = os.path.expanduser(a.out_dir)
    a.abc_ns = a.abc_ns or a.period_ns
    a.access = [float(x) for x in a.sram_access_ns.split(",") if x.strip()]
    if not a.access:
        p.error("--sram-access-ns: at least one value")
    return a


def points(a) -> list[dict]:
    out = [] if a.no_baseline else [{"name": "baseline", "patch": "", "l2_patch": ""}]
    for spec in a.patch:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path or name == "baseline":
            raise SystemExit(f"--patch {spec!r}: expected NAME=PATH (NAME other than baseline)")
        text = Path(path).expanduser().read_text()
        if not text.strip():
            raise SystemExit(f"--patch {spec!r}: empty patch")
        out.append({"name": name, "patch": text, "l2_patch": ""})
    for spec in a.l2_patch:
        name, sep, path = spec.partition("=")
        pt = next((q for q in out if q["name"] == name and name != "baseline"), None)
        if not sep or not path or pt is None:
            raise SystemExit(f"--l2-patch {spec!r}: expected NAME=PATH with NAME given by a --patch")
        pt["l2_patch"] = Path(path).expanduser().read_text()
        if not pt["l2_patch"].strip():
            raise SystemExit(f"--l2-patch {spec!r}: empty patch")
    return out


# ---------------------------------------------------------------------------
# small helpers (from rocket-fmax's rocket_fmax.py)
# ---------------------------------------------------------------------------

def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else "")


def fmt(x, nd=3):
    return "" if x is None else f"{x:.{nd}f}"


def append_csv(path: Path, fields: list, row: dict):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def build(a, name: str):
    cb = ChiselBuildNode(N.CHIPYARD, N.CONFIG, target=BuildTarget.VERILATOR, make_jobs=a.make_jobs,
                         timeout_seconds=a.build_timeout, collect_generated_src=True, name=name)
    return get(cb.build.chia_remote(cb))


def build_errors(art) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", (art.stdout or "") + "\n" + (art.stderr or ""))
    lines = [l for l in text.splitlines() if re.search(r"\[error\]|error:|%Error|Exception|FAILED", l)]
    return "\n".join(lines[:120]) if lines else "\n".join(text.splitlines()[-60:])


def save_build(d: Path, art):
    write(d / "build.log", f"success={art.success} returncode={art.returncode}\n"
                           f"[stdout tail]\n{(art.stdout or '')[-200000:]}\n[stderr tail]\n{(art.stderr or '')[-100000:]}")


# ---------------------------------------------------------------------------
# fake SRAM Liberty and LEF
# ---------------------------------------------------------------------------

_PORT_RE = re.compile(r"\b(input|output)\s+(?:wire\s+|reg\s+|logic\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)")
_ANSI_RE = re.compile(r"(?:(input|output)\s+)?(?:wire\s+|reg\s+|logic\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)$")


def sram_ports(module_text: str) -> list[tuple[str, str, int]]:
    """[(direction, name, width)] of a *_ext module. ANSI header: a port without a direction keeps the
    direction and range of the one before (firtool writes `input RW0_en, RW0_clk, RW0_wmode,`)."""
    i, j = module_text.find("("), module_text.find(");")
    head = re.sub(r"//[^\n]*", "", module_text[i + 1:j]) if 0 <= i < j else ""
    out, d, w = [], None, 1
    for tok in head.split(","):
        m = _ANSI_RE.match(" ".join(tok.split()))
        if not m:
            continue
        if m.group(1):
            d = m.group(1)
            w = abs(int(m.group(2)) - int(m.group(3))) + 1 if m.group(2) else 1
        elif m.group(2):
            w = abs(int(m.group(2)) - int(m.group(3))) + 1
        if d:
            out.append((d, m.group(4), w))
    if out:
        return out
    seen = set()                                   # non-ANSI: input/output declarations in the body
    for d, msb, lsb, name in _PORT_RE.findall(module_text):
        if name not in seen:
            seen.add(name)
            out.append((d, name, abs(int(msb) - int(lsb)) + 1 if msb else 1))
    return out


def is_clock_pin(name: str) -> bool:
    return name.endswith("_clk") or name in ("clk", "clock", "CLK")


def fake_liberty(blackboxes: dict, access_ns: float, setup_ns: float) -> str:
    """Liberty of every SRAM black box: read data valid `access_ns` after the port's clock edge, every
    other input with `setup_ns` setup to it. A pin `X_<name>` belongs to the clock `X_clk` (RW0_, R0_, W0_)."""
    widths, cells = set(), []

    def timing_in(clk):
        return (f'      timing () {{ related_pin : "{clk}"; timing_type : setup_rising;\n'
                f'        rise_constraint (scalar) {{ values ("{setup_ns}"); }} fall_constraint (scalar) {{ values ("{setup_ns}"); }} }}\n'
                f'      timing () {{ related_pin : "{clk}"; timing_type : hold_rising;\n'
                f'        rise_constraint (scalar) {{ values ("0.02"); }} fall_constraint (scalar) {{ values ("0.02"); }} }}\n')

    def timing_out(clk):
        return (f'      timing () {{ related_pin : "{clk}"; timing_type : rising_edge;\n'
                f'        cell_rise (scalar) {{ values ("{access_ns}"); }} cell_fall (scalar) {{ values ("{access_ns}"); }}\n'
                f'        rise_transition (scalar) {{ values ("0.1"); }} fall_transition (scalar) {{ values ("0.1"); }} }}\n')

    for name, text in sorted(blackboxes.items()):
        ports = sram_ports(text)
        clocks = [p for _, p, _ in ports if is_clock_pin(p)]
        body = []
        for d, p, w in ports:
            if p in clocks:
                body.append(f"    pin ({p}) {{ direction : input; capacitance : 0.01; clock : true; }}\n")
                continue
            prefix = p.split("_", 1)[0]
            clk = f"{prefix}_clk" if f"{prefix}_clk" in clocks else (clocks[0] if clocks else None)
            t = "" if clk is None else (timing_in(clk) if d == "input" else timing_out(clk))
            attrs = "direction : input; capacitance : 0.005;" if d == "input" else "direction : output; max_capacitance : 0.5;"
            if w == 1:
                body.append(f"    pin ({p}) {{ {attrs}\n{t}    }}\n")
            else:
                widths.add(w)
                body.append(f"    bus ({p}) {{ bus_type : bus_{w}; {attrs}\n{t}    }}\n")
        cells.append(f"  cell ({name}) {{\n    area : 0;\n    dont_use : true;\n    dont_touch : true;\n"
                     + "".join(body) + "  }\n")
    types = "".join(f"  type (bus_{w}) {{ base_type : array; data_type : bit; bit_width : {w}; "
                    f"bit_from : {w - 1}; bit_to : 0; downto : true; }}\n" for w in sorted(widths))
    return ("library (rocket_fmax_fakeram) {\n  technology (cmos);\n  delay_model : table_lookup;\n"
            '  time_unit : "1ns";\n  voltage_unit : "1V";\n  current_unit : "1mA";\n'
            '  pulling_resistance_unit : "1kohm";\n  leakage_power_unit : "1nW";\n  capacitive_load_unit (1, pf);\n'
            "  input_threshold_pct_rise : 50;\n  input_threshold_pct_fall : 50;\n"
            "  output_threshold_pct_rise : 50;\n  output_threshold_pct_fall : 50;\n"
            "  slew_lower_threshold_pct_rise : 20;\n  slew_lower_threshold_pct_fall : 20;\n"
            "  slew_upper_threshold_pct_rise : 80;\n  slew_upper_threshold_pct_fall : 80;\n"
            "  nom_process : 1;\n  nom_voltage : 1.8;\n  nom_temperature : 25;\n  default_max_transition : 1.5;\n"
            + types + "".join(cells) + "}\n")




def fake_lef(blackboxes: dict) -> str:
    """LEF of every SRAM black box, only so that OpenROAD can link them: a block with one small met3
    pin rectangle per bit along its bottom edge. Size and pin places mean nothing (no placement)."""
    out = ['VERSION 5.8 ;\nBUSBITCHARS "[]" ;\nDIVIDERCHAR "/" ;\n']
    for name, text in sorted(blackboxes.items()):
        ports = sram_ports(text)
        bits = []
        for d, p, w in ports:
            use = "CLOCK" if is_clock_pin(p) else "SIGNAL"
            bits += [(d, p if w == 1 else f"{p}[{i}]", use) for i in range(w)]
        width = max(100.0, round(len(bits) * 0.46 + 1, 2))
        out.append(f"MACRO {name}\n  CLASS BLOCK ;\n  ORIGIN 0 0 ;\n  SIZE {width} BY 100 ;\n")
        for i, (d, b, use) in enumerate(bits):
            x = round(0.5 + i * 0.46, 2)
            out.append(f"  PIN {b}\n    DIRECTION {d.upper()} ;\n    USE {use} ;\n    PORT\n      LAYER met3 ;\n"
                       f"        RECT {x} 0 {x + 0.3:.2f} 0.3 ;\n    END\n  END {b}\n")
        out.append(f"END {name}\n")
    out.append("END LIBRARY\n")
    return "".join(out)


def ensure_lefs() -> list[str]:
    """The sky130hd LEFs, downloaded once into LEF_DIR and checked by sha256."""
    os.makedirs(LEF_DIR, exist_ok=True)
    paths = []
    for name, sha in LEFS.items():
        path = os.path.join(LEF_DIR, name)
        if not os.path.exists(path):
            url = (f"https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/{ORFS_COMMIT}"
                   f"/flow/platforms/sky130hd/lef/{name}")
            urllib.request.urlretrieve(url, path + ".part")
            os.replace(path + ".part", path)
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if got != sha:
            os.remove(path)
            raise RuntimeError(f"{name}: sha256 {got} != {sha}")
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# eda: Yosys, then OpenROAD (repair_design) or OpenSTA on the SoC
# ---------------------------------------------------------------------------

STA_SOC = """\
{read_lefs}catch {{set_hierarchy_separator .}}
read_liberty {lib}
read_liberty {fakelib}
read_verilog {netlist}
link_design {top}
{floorplan}set clks {{}}
set others {{}}
foreach p [all_inputs] {{
  set n [get_full_name $p]
  if {{([string match -nocase *clock $n] || $n eq "clk") && ![string match -nocase *reset* $n]}} {{ lappend clks $p }} else {{ lappend others $p }}
}}
puts "@@CLOCKS"
foreach p $clks {{ puts [get_full_name $p] }}
# Clock ports of DigitalTop reach the flops through PRCI clock nodes/gates: put the ideal clock on every
# register clock pin and every SRAM macro clock pin as well (one synchronous domain).
set ckpins [all_registers -edge_triggered -clock_pins]
foreach q [get_pins -hierarchical *_clk] {{
  if {{[catch {{get_property $q is_hierarchical}} h] == 0 && !$h}} {{ lappend ckpins $q }}
}}
puts "@@NCKPINS"
puts [llength $ckpins]
create_clock -name clk -period {period_ns} [concat $clks $ckpins]
if {{[llength $others]}} {{ set_input_delay 0 -clock clk $others }}
set_output_delay 0 -clock clk [all_outputs]
# Asynchronous resets: reset ports, recovery/removal checks and the async pins of the flops are not timed.
set rst [get_ports -quiet *reset*]
if {{[llength $rst]}} {{ set_false_path -from $rst }}
set sta_recovery_removal_checks_enabled 0
set apins [all_registers -async_pins]
puts "@@NASYNC"
puts [llength $apins]
if {{[llength $apins]}} {{ set_false_path -to $apins }}
# First stage of the synchronizers (reset synchronizers, AsyncQueue crossings): asynchronous input.
set sync [get_cells -hierarchical -quiet *sync_0_reg*]
puts "@@NSYNC"
puts [llength $sync]
if {{[llength $sync]}} {{ set_false_path -to $sync }}
{what_if}puts "@@WORST_PRE"
report_worst_slack -max -digits 4
{repair}puts "@@WORST"
report_worst_slack -max -digits 4
puts "@@TNS"
report_tns -digits 4
puts "@@REGIONS"
proc worst_paths {{n}} {{
  if {{[catch {{find_timing_paths -path_delay max -group_path_count $n -endpoint_path_count 1}} ps]}} {{
    set ps [find_timing_paths -path_delay max -group_count $n -endpoint_count 1]
  }}
  return $ps
}}
if {{[catch {{
  foreach pe [worst_paths 5000] {{
    puts "[get_full_name [get_property $pe startpoint]] [get_full_name [get_property $pe endpoint]] [get_property $pe slack]"
  }}
}} err]}} {{ puts "ERROR $err" }}
puts "@@ENDPOINTS"
report_checks -path_delay max -group_count 3000 -format end -digits 3
puts "@@PATHS"
report_checks -path_delay max -group_count 5 -fields {{slew cap input_pins fanout}} -digits 3
puts "@@NOCLOCK"
if {{[catch {{check_setup -no_clock}} err]}} {{ puts "ERROR $err" }}
puts "@@END"
"""

REPAIR = """\
puts "@@REPAIR"
set_max_fanout {max_fanout} [current_design]
if {{[catch {{repair_design}} err]}} {{ puts "ERROR repair_design: $err" }}
{repair_timing}"""

REPAIR_TIMING = """\
puts "@@WORST_DESIGN"
report_worst_slack -max -digits 4
if {[catch {repair_timing -setup -setup_margin 0 -repair_tns 100} err]} {
  puts "ERROR repair_timing: $err"
  if {[catch {repair_timing -setup} err]} { puts "ERROR repair_timing (no options): $err" }
}
"""

FLOORPLAN = """\
if {[catch {initialize_floorplan -utilization 20 -aspect_ratio 1 -core_space 10 -site unithd} err]} { puts "floorplan: $err" }
"""


@ChiaFunction(max_retries=0)
def extract_soc_rtl(artifact, top: str) -> N.TileRTL:
    return N.tile_rtl_from_sources(artifact.generated_src_files, top)


# Yosys 0.38's Verilog frontend accepts a size cast `8'(x)` but not `signed'(x)`, `unsigned'(x)` or a
# cast to a named type `T'(x)` (unexpected OP_CAST). Uncore files (e.g. InclusiveCache's Atomics.sv)
# use them; the tile alone never did. Rewrite them before Yosys reads the files.
_CAST_SIGN_RE = re.compile(r"\b(signed|unsigned)\s*'\s*\(")
_CAST_TYPE_RE = re.compile(r"\b(?!\d)([A-Za-z_]\w*)\s*'\s*\(")
_PACKED_PATTERN_RE = re.compile(
    r"(^[ \t]*(?:wire|logic|reg)\s+(?:\[[^\]]+\]\s*){2,}[A-Za-z_]\w*\s*=\s*)'\{", re.M)


def sv_casts_for_yosys(text: str) -> tuple[str, int]:
    """Return (text with signed'/unsigned'/T' casts rewritten, number rewritten). Size casts stay."""
    text, n1 = _CAST_SIGN_RE.subn(lambda m: f"${m.group(1)}(", text)
    text, n2 = _CAST_TYPE_RE.subn("(", text)
    # '{a, b, ...} on a packed multi-dim declaration (no unpacked dims) == concatenation {a, b, ...}
    text, n3 = _PACKED_PATTERN_RE.subn(lambda m: m.group(1) + "{", text)
    return text, n1 + n2 + n3


def _run_timing(work: str, tool: str, script: str, timeout: int) -> tuple[int, str]:
    with open(os.path.join(work, "sta.tcl"), "w") as f:
        f.write(script)
    cmd = (["openroad", "-no_init", "-no_splash", "-exit", "sta.tcl"] if tool == "openroad"
           else ["sta", "-no_init", "-no_splash", "-exit", "sta.tcl"])
    try:
        s = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout)
        return s.returncode, s.stdout + ("\nSTDERR:\n" + s.stderr[-5000:] if s.stderr else "")
    except subprocess.TimeoutExpired as e:
        tail = e.stdout if isinstance(e.stdout, str) else ""
        return -1, f"timeout after {timeout} s\n" + tail[-20000:]


def what_if_tcl(patterns) -> str:
    """set_false_path -from the given cells (what-if: pretend these paths are fixed)."""
    t = ""
    for pat in patterns:
        t += (f"set wi [concat [get_cells -quiet {{{pat}}}] [get_cells -hierarchical -quiet {{{pat}}}]]\n"
              f"puts \"@@WHATIF {pat} [llength $wi]\"\n"
              "if {[llength $wi]} { set_false_path -from $wi }\n")
    return t


@ChiaFunction(resources={"eda": 1}, num_cpus=2, max_retries=0)
def soc_synth_sta(rtl: N.TileRTL, period_ns: float, access: list, setup_ns: float, yosys_timeout: int,
                  buffer: str = "repair", max_fanout: int = 16, abc_ns: float | None = None,
                  repair_timing: bool = False, false_from: tuple = ()) -> dict:
    """Yosys v3 on rtl.top, then timing once per SRAM access time. Returns {"ok", "error", "area_um2",
    "stat", "yosys_log_tail", "yosys_minutes", "sta": {access: stdout}, "buffer": {access: what ran}}."""
    res = {"ok": False, "error": "", "sta": {}, "buffer": {}}
    lib_src = os.environ.get("SKY130HD_LIB", "/home/ray/pdk/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib")
    work = tempfile.mkdtemp(prefix="soc_fmax_")
    try:
        lib = "sky130hd_filtered.lib"
        filtered, _ = N.filter_liberty(open(lib_src).read())
        with open(os.path.join(work, lib), "w") as f:
            f.write(filtered)
        os.makedirs(os.path.join(work, "rtl"))
        casts = {}
        for name, text in rtl.files.items():
            text, n = sv_casts_for_yosys(text)
            if n:
                casts[name] = n
            with open(os.path.join(work, "rtl", name), "w") as f:
                f.write(text)
        res["casts_rewritten"] = casts
        bb = []
        for name, text in rtl.blackboxes.items():
            bb.append(f"bb_{name}.v")
            with open(os.path.join(work, bb[-1]), "w") as f:
                f.write(text + "\n")
        ys = N.YOSYS_SCRIPT.format(
            lib=lib, top=rtl.top, period_ps=int(round((abc_ns or period_ns) * 1000)), synth=N.SYNTH_FLOWS["v3"].format(top=rtl.top),
            read_blackboxes="\n".join(f"read_verilog -lib -sv {p}" for p in bb),
            files=" ".join(f"rtl/{n}" for n in sorted(rtl.files)), stat="stat.txt", netlist="netlist.v")
        with open(os.path.join(work, "synth.ys"), "w") as f:
            f.write(ys)
        with open(os.path.join(work, "abc.constr"), "w") as f:
            f.write(N.ABC_CONSTR)
        t0 = time.time()
        y = subprocess.Popen(["yosys", "-q", "-l", "yosys.log", "-s", "synth.ys"], cwd=work, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            yerr, _ = y.communicate(timeout=yosys_timeout)
            rc = y.returncode
        except subprocess.TimeoutExpired:
            N._kill(y)
            rc, yerr = -1, "timeout"
        except BaseException:
            N._kill(y)
            raise
        res["yosys_minutes"] = (time.time() - t0) / 60
        ylog = os.path.join(work, "yosys.log")
        res["yosys_log_tail"] = (open(ylog, errors="replace").read()[-20000:] if os.path.exists(ylog) else "") + (yerr or "")[-5000:]
        if rc != 0 or not os.path.exists(os.path.join(work, "netlist.v")):
            res["error"] = f"yosys returncode {rc}" + (" (killed: OOM?)" if rc in (137, -9) else "")
            for fn, ln in sorted(set(re.findall(r"(rtl/[\w.]+):(\d+): ERROR", res["yosys_log_tail"])))[:5]:
                try:
                    src = open(os.path.join(work, fn), errors="replace").read().splitlines()
                    res["error"] += f"\n{fn}:{ln}: " + src[int(ln) - 1].strip()[:300]
                except (OSError, IndexError):
                    pass
            return res
        res["stat"] = open(os.path.join(work, "stat.txt")).read()
        res["area_um2"] = N.parse_area(res["stat"])

        lefs, lef_error = [], ""
        if buffer == "repair":
            try:
                if not shutil.which("openroad"):
                    raise RuntimeError("openroad not on PATH")
                lefs = ensure_lefs()
                with open(os.path.join(work, "fakeram.lef"), "w") as f:
                    f.write(fake_lef(rtl.blackboxes))
            except Exception as e:                                        # noqa: BLE001
                lef_error = f"{type(e).__name__}: {e}"
        for acc in access:
            with open(os.path.join(work, "fakeram.lib"), "w") as f:
                f.write(fake_liberty(rtl.blackboxes, acc, setup_ns))
            common = dict(lib=lib, fakelib="fakeram.lib", netlist="netlist.v", top=rtl.top, period_ns=period_ns)
            out, used = "", "none"
            if buffer == "repair" and not lef_error:
                read_lefs = "".join(f"read_lef {p}\n" for p in lefs) + "read_lef fakeram.lef\n"
                rc, out = _run_timing(work, "openroad", STA_SOC.format(
                    read_lefs=read_lefs, floorplan=FLOORPLAN, repair=REPAIR.format(max_fanout=max_fanout, repair_timing=REPAIR_TIMING if repair_timing else ""),
                    what_if=what_if_tcl(false_from), **common), 12 * 3600)
                used = "repair+timing" if repair_timing else "repair"
                if rc != 0 or "@@END" not in out:
                    res.setdefault("repair_errors", {})[str(acc)] = f"openroad rc {rc}: " + out[-3000:]
                    out, used = "", "none (openroad failed)"
            elif buffer == "repair":
                used = f"none ({lef_error})"
            if not out:
                _, out = _run_timing(work, "sta", STA_SOC.format(read_lefs="", floorplan="", repair="", what_if=what_if_tcl(false_from), **common), 4 * 3600)
            res["sta"][str(acc)] = out
            res["buffer"][str(acc)] = used
        res["ok"] = True
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# head: regions of the worst paths
# ---------------------------------------------------------------------------

def norm(name: str) -> str:
    """Hierarchical name with "." only (OpenSTA with set_hierarchy_separator ., OpenROAD may print "/")."""
    return name.replace("\\", "").replace("/", ".")


def region(start: str, end: str, tile: str) -> str:
    def where(n):
        n = norm(n)
        if "." not in n:
            return "io"
        return "tile" if tile and n.startswith(tile + ".") else "uncore"
    s, e = where(start), where(end)
    if "io" in (s, e):
        return "I/O"
    return s if s == e else "tile<->uncore"


_REG_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+(-?[\d.]+(?:e[-+]?\d+)?)\s*$")


def _int(text: str) -> int | None:
    m = re.search(r"^\s*(\d+)\s*$", text, re.M)
    return int(m.group(1)) if m else None


def start_family(start: str) -> str:
    """Startpoint register with bit indices and the pin removed: a.b.x[3]_reg.Q -> a.b.x[*]_reg."""
    n = norm(start)
    n = re.sub(r"\.(Q|Q_N|CLK|[A-Z]\w*)$", "", n) if "." in n else n
    return re.sub(r"\[\d+\]", "[*]", n)


def analyze(sta_out: str, period_ns: float, tile: str, srams: set) -> dict:
    text = sta_out.replace("\\", "")
    pat = r"worst slack(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)"
    worst = N._num(pat, N._section(text, "WORST"))
    pre = N._num(pat, N._section(text, "WORST_PRE"))
    wdes = N._num(pat, N._section(text, "WORST_DESIGN"))
    tns = N._num(r"tns(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", N._section(text, "TNS"))
    srams_n = {norm(s) for s in srams}
    reg: dict = {}
    starts: dict = {}
    sram_worst = None
    n = 0
    for line in N._section(text, "REGIONS").splitlines():
        m = _REG_LINE_RE.match(line.strip())
        if not m:
            continue
        st, en, sl = m.group(1), m.group(2), float(m.group(3))
        n += 1
        r = region(st, en, tile)
        if sl < 0:
            k = start_family(st)
            g = starts.setdefault(k, {"count": 0, "slack": sl, "end": en, "regions": set()})
            g["count"] += 1
            g["regions"].add(r)
            if sl < g["slack"]:
                g["slack"], g["end"] = sl, en
        cur = reg.get(r)
        if cur is None or sl < cur["slack"]:
            reg[r] = {"slack": sl, "start": st, "end": en, "count": (cur or {}).get("count", 0)}
        reg[r]["count"] += 1
        if norm(st).rsplit(".", 1)[0] in srams_n or norm(en).rsplit(".", 1)[0] in srams_n:
            sram_worst = sl if sram_worst is None else min(sram_worst, sl)
    paths = N._section(text, "PATHS")
    st = re.search(r"Startpoint:\s*(\S+)", paths)
    en = re.search(r"Endpoint:\s*(\S+)", paths)

    def fmax(w):
        return (1000.0 / (period_ns - w)) if w is not None and period_ns - w > 0 else None
    repair = N._section(text, "REPAIR")
    return {"worst_slack_ns": worst, "tns_ns": tns, "fmax_mhz": fmax(worst), "worst_pre_ns": pre, "fmax_pre_mhz": fmax(pre),
            "worst_design_ns": wdes, "fmax_design_mhz": fmax(wdes),
            "start_families": sorted(({"start": k, "count": g["count"], "slack": g["slack"], "end": g["end"],
                                       "regions": sorted(g["regions"])} for k, g in starts.items()),
                                     key=lambda g: (-g["count"], g["slack"]))[:30],
            "crit_start": st.group(1) if st else "", "crit_end": en.group(1) if en else "",
            "worst_region": region(st.group(1), en.group(1), tile) if st and en else "",
            "regions": reg, "sram_slack_ns": sram_worst, "region_paths": n,
            "region_error": next((l for l in N._section(text, "REGIONS").splitlines() if l.startswith("ERROR")), ""),
            "n_async_pins": _int(N._section(text, "NASYNC")), "n_sync_cells": _int(N._section(text, "NSYNC")),
            "n_ckpins": _int(N._section(text, "NCKPINS")),
            "repair_log": "\n".join(l for l in repair.splitlines() if re.search(r"Inserted|Resized|ERROR|repair", l))[:2000],
            "clocks": [l for l in N._section(text, "CLOCKS").splitlines() if not l.startswith("@@")],
            "noclock": "\n".join(l for l in N._section(text, "NOCLOCK").splitlines() if not l.startswith("@@"))[:2000],
            "families": N.endpoint_families(N._section(text, "ENDPOINTS"), 20)}


def run_point(a, p: dict, snap: dict, d: Path) -> dict:
    t0 = time.time()
    print(get(N.reset_rocket.chia_remote(snap["rocket_head"], p["patch"])) or "rocket-chip clean", flush=True)
    print(get(N.reset_l2.chia_remote(p.get("l2_patch", ""))) or "L2 clean", flush=True)
    art = build(a, f"soc-fmax-{p['name']}")
    if not art.success:
        save_build(d, art)
        return {"ok": False, "error": "build failed:\n" + build_errors(art)[-3000:], "minutes": (time.time() - t0) / 60}
    rtl = get(extract_soc_rtl.chia_remote(art, a.top))
    paths = N.instance_paths(rtl)
    tiles = sorted(k for k, m in paths.items() if m == N.TOP)
    srams = {k for k, m in paths.items() if m in rtl.blackboxes}
    rtl_hash = hashlib.sha256("".join(rtl.files[f] for f in sorted(rtl.files)).encode()).hexdigest()[:12]
    print(f"{p['name']}: build ok ({(time.time() - t0) / 60:.1f} min), {a.top}: {len(rtl.modules)} modules, "
          f"{len(rtl.blackboxes)} SRAM, tile {tiles}; synth ...", flush=True)
    s = get(soc_synth_sta.chia_remote(rtl, a.period_ns, a.access, a.sram_setup_ns, int(a.yosys_timeout_hours * 3600),
                                      a.buffer, a.max_fanout, a.abc_ns, a.repair_timing,
                                      tuple(a.what_if_false_from)))
    write(d / "yosys_log_tail.txt", s.get("yosys_log_tail", ""))
    res = {"ok": s["ok"], "error": s.get("error", ""), "rtl_sha12": rtl_hash, "tile": tiles[0] if tiles else "",
           "srams": sorted(rtl.blackboxes), "area_um2": s.get("area_um2"), "yosys_minutes": s.get("yosys_minutes"),
           "casts_rewritten": s.get("casts_rewritten", {}), "buffer": s.get("buffer", {}),
           "repair_errors": s.get("repair_errors", {}), "sta": {}}
    if s["ok"]:
        write(d / "stat.txt", s["stat"])
        for acc, out in s["sta"].items():
            write(d / f"sta_{acc}.txt", out)
            res["sta"][acc] = analyze(out, a.period_ns, res["tile"], srams)
    res["minutes"] = (time.time() - t0) / 60
    return res


def csv_rows(name: str, r: dict, period: float) -> list[dict]:
    rows = []
    for acc, x in (r.get("sta") or {}).items():
        rows.append({"point": name, "access_ns": acc, "period_ns": period, "buffer": (r.get("buffer") or {}).get(acc, ""),
                     "worst_slack_ns": fmt(x["worst_slack_ns"]), "fmax_mhz": fmt(x["fmax_mhz"], 2),
                     "worst_pre_ns": fmt(x["worst_pre_ns"]), "fmax_pre_mhz": fmt(x["fmax_pre_mhz"], 2),
                     "worst_region": x["worst_region"], "tns_ns": fmt(x["tns_ns"], 1),
                     **{f"{g}_slack_ns": fmt((x["regions"].get(g) or {}).get("slack")) for g in REGIONS},
                     "sram_slack_ns": fmt(x["sram_slack_ns"]), "n_async_pins": x["n_async_pins"],
                     "n_sync_cells": x["n_sync_cells"], "crit_start": x["crit_start"], "crit_end": x["crit_end"],
                     "area_um2": fmt(r.get("area_um2"), 0), "minutes": fmt(r.get("minutes"), 1)})
    return rows


def summary(a, pts: list[dict]) -> str:
    lines = [f"# Synth cả SoC: {a.run_name}", "",
             f"`{a.top}` của RocketConfig, Yosys `v3` (phân cấp) sky130hd, `abc -D` {a.abc_ns:g} ns, một clock lý tưởng "
             f"{a.period_ns:g} ns. SRAM là macro giả (clock → dữ liệu đọc = cột `SRAM`, setup {a.sram_setup_ns:g} ns). "
             "Không tính: chân reset/set bất đồng bộ của flop (recovery/removal tắt), tầng đầu `*sync_0_reg*` của các bộ "
             f"đồng bộ, cổng reset. Buffer: `{a.buffer}`" + (f" (OpenROAD `repair_design`, max fanout {a.max_fanout}, "
             "không placement, không RC dây)" if a.buffer == "repair" else "") + ". `trước buffer` là Fmax trước "
             "`repair_design`" + (f"; `repair_timing -setup` nhắm {a.period_ns:g} ns (không placement, không RC dây: lạc quan), "
             "`sau repair_design` là Fmax trước bước này" if a.repair_timing else "") + (("**What-if:** bỏ qua đường xuất phát từ " + ", ".join(f"`{x}`" for x in a.what_if_false_from)
              + " (giả sử đã sửa; không phải Fmax thật)") if a.what_if_false_from else "") + ". Slack theo vùng lấy từ 5000 đường xấu nhất (mỗi endpoint một đường).", "",
             "| điểm | SRAM (ns) | buffer | Fmax SoC (MHz) | trước buffer | vùng xấu nhất | tile | uncore | tile<->uncore | I/O | "
             "đường có SRAM | TNS | diện tích (µm², không SRAM) |", "|" + "---|" * 13]
    ref = {}
    for p in pts:
        r = p["res"]
        if not r.get("ok"):
            lines.append(f"| `{p['name']}` | lỗi: {(r.get('error') or '')[:300]} |" + " |" * 11)
            continue
        for acc, x in r["sta"].items():
            g = x["regions"]
            fm = x["fmax_mhz"]
            base = ref.setdefault(acc, fm)
            dd = f" ({(fm / base - 1) * 100:+.1f} %)" if fm and base and p is not pts[0] else ""
            lines.append("| " + " | ".join([f"`{p['name']}`", acc, (r.get("buffer") or {}).get(acc, ""), f"{fmt(fm, 2)}{dd}",
                                             fmt(x["fmax_pre_mhz"], 2), x["worst_region"],
                                             *[fmt((g.get(k) or {}).get("slack")) for k in REGIONS],
                                             fmt(x["sram_slack_ns"]), fmt(x["tns_ns"], 1), fmt(r.get("area_um2"), 0)]) + " |")
    for p in pts:
        r = p["res"]
        if not r.get("ok"):
            continue
        lines += ["", f"## `{p['name']}`", "",
                  f"Tile: `{r['tile']}`; {len(r['srams'])} SRAM; RTL sha `{r['rtl_sha12']}`; Yosys {fmt(r.get('yosys_minutes'), 1)} phút, "
                  f"tổng {fmt(r.get('minutes'), 1)} phút."]
        for acc, err in (r.get("repair_errors") or {}).items():
            lines += ["", f"OpenROAD lỗi (SRAM {acc} ns), đã đo bằng OpenSTA không buffer:", "```", err[-1500:], "```"]
        for acc, x in r["sta"].items():
            lines += ["", f"SRAM {acc} ns: {x['n_ckpins']} chân clock, {x['n_async_pins']} chân reset bất đồng bộ và "
                      f"{x['n_sync_cells']} cell `*sync_0_reg*` không tính; đường xấu nhất `{x['crit_start']}` → `{x['crit_end']}`.", ""]
            if x["repair_log"]:
                lines += ["```", x["repair_log"], "```", ""]
            if x["region_error"]:
                lines.append(f"- lỗi lấy đường theo vùng: {x['region_error'][:300]}")
            for k in REGIONS:
                g = x["regions"].get(k)
                if g:
                    lines.append(f"- {k}: {g['slack']:.3f} ns ({g['count']} endpoint trong {x['region_paths']}), `{g['start']}` → `{g['end']}`")
            if x.get("worst_design_ns") is not None:
                lines.append(f"- sau repair_design, trước repair_timing: {x['worst_design_ns']:.3f} ns "
                             f"({fmt(x['fmax_design_mhz'], 2)} MHz)")
            sf = x.get("start_families") or []
            if sf:
                lines += ["", f"Đường fail (slack < 0 ở clock {a.period_ns:g} ns) gom theo startpoint, nhiều endpoint nhất trước:", "",
                          "| startpoint | endpoint fail | slack xấu nhất | vùng | endpoint xấu nhất |", "|---|---|---|---|---|"]
                lines += [f"| `{g['start']}` | {g['count']} | {g['slack']:.3f} | {', '.join(g['regions'])} | `{g['end']}` |" for g in sf]
                lines.append("")
            if x["noclock"].strip():
                lines.append(f"- check_setup -no_clock: {x['noclock'].strip()[:300]}")
            lines += ["", "```", x["families"], "```"]
    return "\n".join(lines) + "\n"


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)
    ray.init(address="auto", namespace="soc_fmax", ignore_reinit_error=True)
    pts = points(a)
    snap = None
    for p in pts:
        d = out / "runs" / p["name"]
        write(d / "point.patch", p["patch"])
        if p["l2_patch"]:
            write(d / "l2.patch", p["l2_patch"])
        old = d / "result.json"
        if old.exists() and not a.force:
            prev = json.loads(old.read_text())
            if prev.get("ok"):
                p["res"] = prev
                print(f"{p['name']}: đã có kết quả ({old}), bỏ qua (--force để chạy lại)", flush=True)
                continue
        snap = snap or get(N.tree_snapshot.chia_remote())
        print(f"\n{p['name']}: build ...", flush=True)
        try:
            p["res"] = run_point(a, p, snap, d)
        except Exception as e:                                               # noqa: BLE001
            p["res"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:2000]}
        write(d / "result.json", json.dumps(p["res"], indent=2, ensure_ascii=False))
        for row in csv_rows(p["name"], p["res"], a.period_ns):
            append_csv(out / "soc_synth.csv", CSV_FIELDS, row)
        r = p["res"]
        print(f"{p['name']}: " + ("; ".join(f"SRAM {acc} ns: Fmax {fmt(x['fmax_mhz'], 2)} MHz "
                                            f"(trước buffer {fmt(x['fmax_pre_mhz'], 2)}, {x['worst_region']})"
                                            for acc, x in (r.get("sta") or {}).items()) or f"lỗi: {r.get('error', '')[:500]}"), flush=True)
        write(out / "summary.md", summary(a, [q for q in pts if "res" in q]))
    if snap:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
        get(N.reset_l2.chia_remote(""))
    write(out / "summary.md", summary(a, pts))
    print("\n" + (out / "summary.md").read_text())
    print(f"done: {out}")


if __name__ == "__main__":
    main()
