"""Synth of the whole SoC (DigitalTop of RocketConfig) with the loop's ASIC flow, for a few rocket-chip patches.

Question answered: does a faster RocketTile make the SoC faster? The loop's metric synthesizes RocketTile
alone, with its SRAMs as black boxes whose pins are not timed. Here, for the baseline and each
--patch NAME=PATH:
  1. build RocketConfig with the patch (as the loop does) and take the DigitalTop hierarchy (the SoC without
     ChipTop's IO cells and clock generation: system bus, L2, peripherals, tile);
  2. Yosys, flow v3 (hierarchical, like the loop), sky130hd, `abc -D` at --period-ns;
  3. OpenSTA once per --sram-access-ns value: each SRAM (*_ext of the mems.conf) becomes a fake macro of a
     generated Liberty: clock -> read data = the access time, setup --sram-setup-ns on every input. The same
     for every point, so the comparison between points is fair; the absolute numbers depend on it (0 = ideal
     SRAM). All clock inputs of DigitalTop (ports whose name ends in "clock") get the same ideal clock (one synchronous domain at --period-ns),
     other inputs and outputs a 0 delay;
  4. the worst paths (report_checks -format summary) are sorted by region: inside the tile, uncore (outside
     the tile), tile <-> uncore (one end in each), I/O (a top-level port), and whether an SRAM is an end.

Outputs in --out-dir/<run-name>/: summary.md, soc_synth.csv (one row per point and access time),
runs/<point>/{point.patch, build.log (on failure), result.json, sta_<access>.txt, stat.txt, yosys_log_tail.txt}.
A point with a result.json is not rerun (--force).

  cd ~/CHIA-workflow/projects/rocket-fmax
  chia job submit --no-wait --submission-id soc-synth-1 --working-dir . -- \\
    python soc_synth.py --run-name soc-synth-1 \\
      --patch asic-6=~/rocket-fmax-v2/asic-6/patches/best.patch \\
      --patch asic-7=~/rocket-fmax-v2/asic-7/patches/best.patch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import ray

import fmax_nodes as N
import rocket_fmax as R
from chia.base.ChiaFunction import ChiaFunction, get

SOC_TOP = "DigitalTop"
REGIONS = ("tile", "uncore", "tile<->uncore", "I/O")
CSV_FIELDS = ["point", "access_ns", "period_ns", "worst_slack_ns", "fmax_mhz", "worst_region", "tns_ns",
              *[f"{r}_slack_ns" for r in REGIONS], "sram_slack_ns", "crit_start", "crit_end", "area_um2", "minutes"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--patch", action="append", default=[], metavar="NAME=PATH",
                   help="a rocket-chip patch (repeatable); the baseline is always the first point")
    p.add_argument("--period-ns", type=float, default=6.0, help="clock of abc -D and of STA (asic-7 used 6)")
    p.add_argument("--sram-access-ns", default="0,1.5,3",
                   help="comma-separated SRAM clock-to-read-data times, one STA run each (0 = ideal SRAM)")
    p.add_argument("--sram-setup-ns", type=float, default=0.2)
    p.add_argument("--top", default=SOC_TOP, help="module synthesized (DigitalTop; ChipTop adds IO cells and clocking)")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3 * 3600)
    p.add_argument("--yosys-timeout-hours", type=float, default=6.0)
    p.add_argument("--out-dir", default="~/rocket-fmax-v2")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    a.out_dir = os.path.expanduser(a.out_dir)
    a.access = [float(x) for x in a.sram_access_ns.split(",") if x.strip()]
    if not a.access:
        p.error("--sram-access-ns: at least one value")
    return a


def points(a) -> list[dict]:
    out = [{"name": "baseline", "patch": ""}]
    for spec in a.patch:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path or name == "baseline":
            raise SystemExit(f"--patch {spec!r}: expected NAME=PATH (NAME other than baseline)")
        text = Path(path).expanduser().read_text()
        if not text.strip():
            raise SystemExit(f"--patch {spec!r}: empty patch")
        out.append({"name": name, "patch": text})
    return out


# ---------------------------------------------------------------------------
# fake SRAM Liberty
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


# ---------------------------------------------------------------------------
# eda: Yosys + OpenSTA on the SoC
# ---------------------------------------------------------------------------

STA_SOC = """\
set_hierarchy_separator .
read_liberty {lib}
read_liberty {fakelib}
read_verilog {netlist}
link_design {top}
set clks {{}}
set others {{}}
foreach p [all_inputs] {{
  set n [get_full_name $p]
  if {{([string match -nocase *clock $n] || $n eq "clk") && ![string match -nocase *reset* $n]}} {{ lappend clks $p }} else {{ lappend others $p }}
}}
puts "@@CLOCKS"
foreach p $clks {{ puts [get_full_name $p] }}
# Clock ports of DigitalTop reach the flops through PRCI clock nodes/gates; a port clock alone left
# ~42k register pins unclocked (soc-synth-3b). Put the ideal clock on every leaf register clock pin
# and every SRAM macro clock pin as well (one synchronous domain).
set ckpins [all_registers -edge_triggered -clock_pins]
foreach q [get_pins -hierarchical *_clk] {{
  if {{[catch {{get_property $q is_hierarchical}} h] == 0 && !$h}} {{ lappend ckpins $q }}
}}
puts "@@NCKPINS"
puts [llength $ckpins]
create_clock -name clk -period {period_ns} [concat $clks $ckpins]
if {{[llength $others]}} {{ set_input_delay 0 -clock clk $others }}
# Resets are asynchronous / pass through synchronizers: not timed.
set rst [get_ports -quiet *reset*]
if {{[llength $rst]}} {{ set_false_path -from $rst }}
set_output_delay 0 -clock clk [all_outputs]
puts "@@WORST"
report_worst_slack -max -digits 4
puts "@@TNS"
report_tns -digits 4
puts "@@SUMMARY"
if {{[catch {{report_checks -path_delay max -group_count 5000 -endpoint_count 1 -format summary -digits 3}} err]}} {{ puts "ERROR $err" }}
puts "@@ENDPOINTS"
report_checks -path_delay max -group_count 3000 -format end -digits 3
puts "@@PATHS"
report_checks -path_delay max -group_count 5 -fields {{slew cap input_pins fanout}} -digits 3
puts "@@NOCLOCK"
if {{[catch {{check_setup -no_clock}} err]}} {{ puts "ERROR $err" }}
puts "@@END"
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


@ChiaFunction(resources={"eda": 1}, num_cpus=2, max_retries=0)
def soc_synth_sta(rtl: N.TileRTL, period_ns: float, access: list, setup_ns: float, yosys_timeout: int) -> dict:
    """Yosys v3 on rtl.top, then OpenSTA once per SRAM access time. Returns {"ok", "error", "area_um2",
    "stat", "yosys_log_tail", "yosys_minutes", "sta": {access: stdout}}."""
    res = {"ok": False, "error": "", "sta": {}}
    lib_src = os.environ.get("SKY130HD_LIB", "/home/ray/pdk/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib")
    work = tempfile.mkdtemp(prefix="rocket_fmax_soc_")
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
            lib=lib, top=rtl.top, period_ps=int(round(period_ns * 1000)), synth=N.SYNTH_FLOWS["v3"].format(top=rtl.top),
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
            # show the source lines the frontend choked on
            for fn, ln in sorted(set(re.findall(r"(rtl/[\w.]+):(\d+): ERROR", res["yosys_log_tail"])))[:5]:
                try:
                    src = open(os.path.join(work, fn), errors="replace").read().splitlines()
                    res["error"] += f"\n{fn}:{ln}: " + src[int(ln) - 1].strip()[:300]
                except (OSError, IndexError):
                    pass
            return res
        res["stat"] = open(os.path.join(work, "stat.txt")).read()
        res["area_um2"] = N.parse_area(res["stat"])
        for acc in access:
            with open(os.path.join(work, "fakeram.lib"), "w") as f:
                f.write(fake_liberty(rtl.blackboxes, acc, setup_ns))
            with open(os.path.join(work, "sta.tcl"), "w") as f:
                f.write(STA_SOC.format(lib=lib, fakelib="fakeram.lib", netlist="netlist.v", top=rtl.top, period_ns=period_ns))
            s = subprocess.run(["sta", "-no_init", "-no_splash", "-exit", "sta.tcl"], cwd=work,
                               capture_output=True, text=True, timeout=4 * 3600)
            res["sta"][str(acc)] = s.stdout + ("\nSTDERR:\n" + s.stderr[-5000:] if s.stderr else "")
        res["ok"] = True
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# head: regions of the worst paths
# ---------------------------------------------------------------------------

_SUM_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(-?\d+\.\d+)\s*(?:\(\w+\))?\s*$")


def region(start: str, end: str, tile: str) -> str:
    def where(n):
        n = n.lstrip("\\")
        if "/" not in n and "." not in n:
            return "io"
        return "tile" if tile and (n == tile or n.startswith(tile + ".")) else "uncore"
    s, e = where(start), where(end)
    if "io" in (s, e):
        return "I/O"
    if s == e:
        return s
    return "tile<->uncore"


def analyze(sta_out: str, period_ns: float, tile: str, srams: set) -> dict:
    """Worst slack, TNS, clocks, and per region the worst path of the summary report."""
    text = sta_out.replace("\\", "")
    worst = N._num(r"worst slack(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", N._section(text, "WORST"))
    tns = N._num(r"tns(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", N._section(text, "TNS"))
    reg: dict = {}
    sram_worst = None
    n = 0
    for line in N._section(text, "SUMMARY").splitlines():
        m = _SUM_RE.match(line)
        if not m or m.group(1) in ("Startpoint", "---"):
            continue
        st, en, sl = m.group(1), m.group(2), float(m.group(3))
        n += 1
        r = region(st, en, tile)
        cur = reg.get(r)
        if cur is None or sl < cur["slack"]:
            reg[r] = {"slack": sl, "start": st, "end": en, "count": 0}
        reg[r]["count"] += 1
        if st.rsplit("/", 1)[0] in srams or en.rsplit("/", 1)[0] in srams:
            sram_worst = sl if sram_worst is None else min(sram_worst, sl)
    paths = N._section(text, "PATHS")
    st = re.search(r"Startpoint:\s*(\S+)", paths)
    en = re.search(r"Endpoint:\s*(\S+)", paths)
    worst_region = region(st.group(1), en.group(1), tile) if st and en else ""
    return {"worst_slack_ns": worst, "tns_ns": tns, "fmax_mhz": (1000.0 / (period_ns - worst)) if worst is not None and period_ns - worst > 0 else None,
            "crit_start": st.group(1) if st else "", "crit_end": en.group(1) if en else "", "worst_region": worst_region,
            "regions": reg, "sram_slack_ns": sram_worst, "summary_paths": n,
            "clocks": [l for l in N._section(text, "CLOCKS").splitlines() if not l.startswith("@@")],
            "noclock": "\n".join(l for l in N._section(text, "NOCLOCK").splitlines() if not l.startswith("@@"))[:2000],
            "families": N.endpoint_families(N._section(text, "ENDPOINTS"), 20)}


def run_point(a, p: dict, snap: dict, d: Path) -> dict:
    t0 = time.time()
    print(get(N.reset_rocket.chia_remote(snap["rocket_head"], p["patch"])) or "rocket-chip clean", flush=True)
    art = R.build(a, f"soc-synth-{p['name']}")
    if not art.success:
        R.save_build(d, art)
        return {"ok": False, "error": "build failed:\n" + R.build_errors(art)[-3000:], "minutes": (time.time() - t0) / 60}
    rtl = get(extract_soc_rtl.chia_remote(art, a.top))
    paths = N.instance_paths(rtl)
    tiles = sorted(k for k, m in paths.items() if m == N.TOP)
    srams = {k for k, m in paths.items() if m in rtl.blackboxes}
    rtl_hash = hashlib.sha256("".join(rtl.files[f] for f in sorted(rtl.files)).encode()).hexdigest()[:12]
    print(f"{p['name']}: build ok ({(time.time() - t0) / 60:.1f} min), {a.top}: {len(rtl.modules)} modules, "
          f"{len(rtl.blackboxes)} SRAM, tile {tiles}; synth ...", flush=True)
    s = get(soc_synth_sta.chia_remote(rtl, a.period_ns, a.access, a.sram_setup_ns, int(a.yosys_timeout_hours * 3600)))
    R.write(d / "yosys_log_tail.txt", s.get("yosys_log_tail", ""))
    res = {"ok": s["ok"], "error": s.get("error", ""), "rtl_sha12": rtl_hash, "tile": tiles[0] if tiles else "",
           "srams": sorted(rtl.blackboxes), "area_um2": s.get("area_um2"), "yosys_minutes": s.get("yosys_minutes"), "casts_rewritten": s.get("casts_rewritten", {}), "sta": {}}
    if s["ok"]:
        R.write(d / "stat.txt", s["stat"])
        for acc, out in s["sta"].items():
            R.write(d / f"sta_{acc}.txt", out)
            res["sta"][acc] = analyze(out, a.period_ns, res["tile"], srams)
    res["minutes"] = (time.time() - t0) / 60
    return res


def csv_rows(name: str, r: dict, period: float) -> list[dict]:
    rows = []
    for acc, x in (r.get("sta") or {}).items():
        rows.append({"point": name, "access_ns": acc, "period_ns": period, "worst_slack_ns": R.fmt(x["worst_slack_ns"]),
                     "fmax_mhz": R.fmt(x["fmax_mhz"], 2), "worst_region": x["worst_region"], "tns_ns": R.fmt(x["tns_ns"], 1),
                     **{f"{g}_slack_ns": R.fmt((x["regions"].get(g) or {}).get("slack")) for g in REGIONS},
                     "sram_slack_ns": R.fmt(x["sram_slack_ns"]), "crit_start": x["crit_start"], "crit_end": x["crit_end"],
                     "area_um2": R.fmt(r.get("area_um2"), 0), "minutes": R.fmt(r.get("minutes"), 1)})
    return rows


def summary(a, pts: list[dict]) -> str:
    def f(x, nd=3):
        return "" if x is None else f"{x:.{nd}f}"
    lines = [f"# Synth cả SoC: {a.run_name}", "",
             f"`{a.top}` của RocketConfig, Yosys `v3` (phân cấp) sky130hd, `abc -D` {a.period_ns:g} ns, một clock lý tưởng "
             f"{a.period_ns:g} ns cho mọi cổng clock. SRAM là macro giả: clock → dữ liệu đọc = thời gian truy cập "
             f"(cột `SRAM`), setup {a.sram_setup_ns:g} ns; giống nhau cho mọi điểm. Vùng của một đường: `tile` (cả hai "
             "đầu trong RocketTile), `uncore` (cả hai ngoài), `tile<->uncore` (mỗi đầu một bên), `I/O` (một đầu là cổng "
             "của DigitalTop, độ trễ ngoài 0). Slack theo vùng lấy từ 5000 đường xấu nhất (mỗi endpoint một đường).", "",
             "| điểm | SRAM (ns) | Fmax SoC (MHz) | worst slack | vùng xấu nhất | tile | uncore | tile<->uncore | I/O | "
             "đường có SRAM | TNS | diện tích (µm², không SRAM) |", "|" + "---|" * 12]
    ref = {}
    for p in pts:
        r = p["res"]
        if not r.get("ok"):
            lines.append(f"| `{p['name']}` | lỗi: {(r.get('error') or '')[:300]} |" + " |" * 10)
            continue
        for acc, x in r["sta"].items():
            g = x["regions"]
            fm = x["fmax_mhz"]
            base = ref.setdefault(acc, fm)
            dd = f" ({(fm / base - 1) * 100:+.1f} %)" if fm and base and p is not pts[0] else ""
            lines.append("| " + " | ".join([f"`{p['name']}`", acc, f"{f(fm, 2)}{dd}", f(x["worst_slack_ns"]),
                                             x["worst_region"], *[f((g.get(k) or {}).get("slack")) for k in REGIONS],
                                             f(x["sram_slack_ns"]), f(x["tns_ns"], 1), f(r.get("area_um2"), 0)]) + " |")
    for p in pts:
        r = p["res"]
        if not r.get("ok"):
            continue
        lines += ["", f"## `{p['name']}`", "",
                  f"Tile: `{r['tile']}`; {len(r['srams'])} SRAM; RTL sha `{r['rtl_sha12']}`; Yosys {f(r.get('yosys_minutes'), 1)} phút, "
                  f"tổng {f(r.get('minutes'), 1)} phút."]
        for acc, x in r["sta"].items():
            lines += ["", f"SRAM {acc} ns: clock `{', '.join(x['clocks'][:6])}`" + (" ..." if len(x["clocks"]) > 6 else "")
                      + f"; đường xấu nhất `{x['crit_start']}` → `{x['crit_end']}`.", ""]
            for k in REGIONS:
                g = x["regions"].get(k)
                if g:
                    lines.append(f"- {k}: {g['slack']:.3f} ns ({g['count']} endpoint trong 5000), `{g['start']}` → `{g['end']}`")
            if x["noclock"].strip():
                lines.append(f"- check_setup -no_clock: {x['noclock'].strip()[:300]}")
            lines += ["", "```", x["families"], "```"]
    return "\n".join(lines) + "\n"


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    R.write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)
    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    pts = points(a)
    snap = None
    for p in pts:
        d = out / "runs" / p["name"]
        R.write(d / "point.patch", p["patch"])
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
        R.write(d / "result.json", json.dumps(p["res"], indent=2, ensure_ascii=False))
        for row in csv_rows(p["name"], p["res"], a.period_ns):
            R.append_csv(out / "soc_synth.csv", CSV_FIELDS, row)
        r = p["res"]
        print(f"{p['name']}: " + ("; ".join(f"SRAM {acc} ns: Fmax {R.fmt(x['fmax_mhz'], 2)} MHz ({x['worst_region']})"
                                            for acc, x in (r.get("sta") or {}).items()) or f"lỗi: {r.get('error', '')[:500]}"), flush=True)
        R.write(out / "summary.md", summary(a, [q for q in pts if "res" in q]))
    if snap:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
    R.write(out / "summary.md", summary(a, pts))
    print("\n" + (out / "summary.md").read_text())
    print(f"done: {out}")


if __name__ == "__main__":
    main()
