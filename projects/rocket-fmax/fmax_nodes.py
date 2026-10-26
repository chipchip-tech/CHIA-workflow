"""Nodes for the Rocket Fmax loop (projects/rocket-fmax).

Every node passes data (str/bytes/dataclass) and never file paths: the chipyard,
verilator, eda and opencode containers do not share a filesystem.

  chipyard (resource "chipyard"):  load_test_elfs, tree_snapshot, reset_rocket,
                                   collect_diff, restore_outside, apply_edit,
                                   stage_for_agent
  any node (no resource):          extract_tile_rtl, run_gate (fans out to
                                   VerilatorRunNode on "verilator_run")
  eda (resource "eda"):            synth_sta (Yosys + OpenSTA, sky130hd tt),
                                   synth_area (ChipTop, hierarchical), equiv_formal
  chipyard, after the loop:        equiv_sim (Verilator lockstep), audit config file
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from chia.base.ChiaFunction import ChiaFunction, get

import equiv as E

CHIPYARD = "/home/ray/chipyard"
ROCKET = f"{CHIPYARD}/generators/rocket-chip"
ROCKET_REL = "generators/rocket-chip"
CONFIG = "RocketConfig"
TOP = "RocketTile"
SOC_TOP = "ChipTop"
STAGE_DIR = "/home/ray/rocket_fmax"          # on chipyard: what the agent may read
SIM_WORK_DIR = "/home/ray/rocket_fmax_sim"   # on verilator_run
AUDIT_STAGE_DIR = "/home/ray/config_audit"   # on chipyard: inputs/outputs of the audit agent
# Written by config_audit.py only (never by an agent), removed at the end of the run.
AUDIT_CONFIG_FILE = f"{CHIPYARD}/generators/chipyard/src/main/scala/config/RocketFmaxAudit.scala"

ISA_SUITES = ("rv64ui", "rv64um", "rv64ua", "rv64uf", "rv64ud")
BENCHMARKS = ("dhrystone", "median", "multiply", "qsort", "towers", "vvadd")

# The agent may only change Scala under rocket-chip/src/main/scala, and never a
# file whose name says it holds configs (cache sizes, FPU on/off, ...).
EDITABLE_PREFIX = "src/main/scala/"
FORBIDDEN_FILE_RE = re.compile(r"Config", re.IGNORECASE)

# Cells ORFS marks dont_use for sky130hd (flow/platforms/sky130hd/config.mk).
DONT_USE_RE = re.compile(r"^sky130_fd_sc_hd__(probe_p_8|probec_p_8|lpflow_\w+)$")

# EICG_wrapper is a latch-based clock gate; for a synthesis-only estimate it is
# replaced by a wire so the gated registers stay clocked by `clock`.
EICG_STUB = """module EICG_wrapper(output out, input en, input test_en, input in);
  assign out = in;
endmodule
"""


def _sh(cmd: str, cwd: str | None = None, timeout: int = 600, check: bool = True) -> str:
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                         timeout=timeout)
    if check and res.returncode != 0:
        raise RuntimeError(f"`{cmd}` failed ({res.returncode}):\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    return res.stdout


# ---------------------------------------------------------------------------
# chipyard: test ELFs and the git tree
# ---------------------------------------------------------------------------

@ChiaFunction(resources={"chipyard": 1})
def load_test_elfs() -> dict:
    """riscv-tests ELFs shipped in the chipyard image, as bytes.

    Returns {"isa": {name: bytes}, "bench": {name: bytes}}.
    """
    riscv = os.environ.get("RISCV") or f"{CHIPYARD}/.conda-env/riscv-tools"
    root = f"{riscv}/riscv64-unknown-elf/share/riscv-tests"
    isa = {}
    for name in sorted(os.listdir(f"{root}/isa")):
        if name.endswith(".dump") or not any(name.startswith(f"{s}-p-") for s in ISA_SUITES):
            continue
        with open(f"{root}/isa/{name}", "rb") as f:
            isa[name] = f.read()
    bench = {}
    for name in BENCHMARKS:
        with open(f"{root}/benchmarks/{name}.riscv", "rb") as f:
            bench[name] = f.read()
    return {"isa": isa, "bench": bench}


def _outside_state() -> dict:
    """Status of chipyard outside generators/rocket-chip.

    `lines`: porcelain status of the root repo; `subs`: per-submodule hash of
    status + diff; `digest`: hash of everything (also of the root diff).
    """
    root = _sh(f"git -C {CHIPYARD} status --porcelain --ignore-submodules=none")
    lines = sorted(l for l in root.splitlines() if not l.endswith(ROCKET_REL))
    root_diff = _sh(f"git -C {CHIPYARD} diff HEAD --ignore-submodules=all")
    sub_cmd = ("case $sm_path in " + ROCKET_REL + ") ;; *) "
               "echo \"$sm_path $( (git status --porcelain --ignore-submodules=all; "
               "git diff HEAD --ignore-submodules=all) | sha1sum | cut -c1-16)\";; esac")
    subs = _sh(f"git -C {CHIPYARD} submodule foreach --quiet {shlex.quote(sub_cmd)}", timeout=900)
    digest = hashlib.sha256("\n".join([*lines, root_diff, subs]).encode()).hexdigest()
    return {"lines": lines, "subs": sorted(subs.splitlines()), "digest": digest}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def tree_snapshot() -> dict:
    """rocket-chip HEAD plus the state of everything outside it, taken once at
    the start of a run (before any agent touches the tree)."""
    head = _sh(f"git -C {ROCKET} rev-parse HEAD").strip()
    return {"rocket_head": head, "outside": _outside_state()}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def reset_rocket(rocket_head: str, patch: str) -> str:
    """Put rocket-chip back at `rocket_head` (dropping edits and commits), then
    apply `patch` (the best version so far; "" = baseline)."""
    _sh(f"git -C {ROCKET} reset -q --hard {rocket_head}")
    _sh(f"git -C {ROCKET} clean -fdq")
    if patch.strip():
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            f.write(patch)
        try:
            _sh(f"git -C {ROCKET} apply --whitespace=nowarn {f.name}")
        finally:
            os.unlink(f.name)
    return _sh(f"git -C {ROCKET} status --short")


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def collect_diff(rocket_head: str, outside_before: dict) -> dict:
    """Diff of rocket-chip vs `rocket_head` (new files included) and whether
    anything outside rocket-chip changed since `outside_before`."""
    _sh(f"git -C {ROCKET} add -A")
    patch = _sh(f"git -C {ROCKET} diff --cached --binary {rocket_head}")
    files = _sh(f"git -C {ROCKET} diff --cached --name-only {rocket_head}").split()
    _sh(f"git -C {ROCKET} reset -q {rocket_head}")      # unstage; also undoes agent commits
    after = _outside_state()
    outside_changed = []
    if after["digest"] != outside_before["digest"]:
        outside_changed = (sorted(set(after["lines"]) - set(outside_before["lines"]))
                           + sorted(set(after["subs"]) - set(outside_before["subs"])))
        if not outside_changed:
            outside_changed = ["(nội dung file đã sửa sẵn từ trước trong chipyard bị thay đổi)"]
    return {"patch": patch, "files": files, "outside_changed": outside_changed}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def restore_outside(outside_before: dict) -> dict:
    """Undo agent edits outside rocket-chip (entries that were not dirty at the
    start of the run). Returns {"ok": bool, "remaining": [...]}."""
    after = _outside_state()
    for line in sorted(set(after["lines"]) - set(outside_before["lines"])):
        code, path = line[:2], line[3:].split(" -> ")[-1]
        full = os.path.join(CHIPYARD, path)
        if code == "??":
            shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) else os.remove(full)
        elif os.path.isdir(os.path.join(full, ".git")) or os.path.isfile(os.path.join(full, ".git")):
            _sh(f"git -C {full} checkout -q -- . && git -C {full} clean -fdq", check=False)
        else:
            _sh(f"git -C {CHIPYARD} checkout -q HEAD -- {shlex.quote(path)}", check=False)
    for entry in sorted(set(after["subs"]) - set(outside_before["subs"])):
        sub = os.path.join(CHIPYARD, entry.split()[0])
        _sh(f"git -C {sub} checkout -q -- . && git -C {sub} clean -fdq", check=False)
    final = _outside_state()
    remaining = (sorted(set(final["lines"]) ^ set(outside_before["lines"]))
                 + sorted(set(final["subs"]) ^ set(outside_before["subs"])))
    return {"ok": final["digest"] == outside_before["digest"], "remaining": remaining}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def apply_edit(patch: str | None, marker_file: str, marker: str) -> str:
    """Milestone 2 stand-in for the agent: apply `patch` on top of the tree, or,
    when no patch is given, append the comment `marker` to `marker_file`
    (a comment-only edit: same RTL, so the round must be rejected)."""
    if patch:
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            f.write(patch)
        try:
            _sh(f"git -C {ROCKET} apply --whitespace=nowarn {f.name}")
        finally:
            os.unlink(f.name)
    else:
        with open(os.path.join(ROCKET, marker_file), "a") as f:
            f.write(f"\n// {marker}\n")
    return _sh(f"git -C {ROCKET} status --short")


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def stage_for_agent(files: dict, root: str = STAGE_DIR) -> str:
    """Write {relative path: text} under `root` (wiped first)."""
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    return _sh(f"find {root} -maxdepth 1 | sort")


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def read_text(path: str) -> str | None:
    """A file written by the agent on the chipyard worker (None if missing)."""
    if not os.path.isfile(path):
        return None
    with open(path, errors="replace") as f:
        return f.read()


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def write_audit_config(text: str | None) -> str:
    """Write (or, with None, delete) AUDIT_CONFIG_FILE."""
    if text is None:
        if os.path.exists(AUDIT_CONFIG_FILE):
            os.remove(AUDIT_CONFIG_FILE)
        return "removed"
    with open(AUDIT_CONFIG_FILE, "w") as f:
        f.write(text)
    return AUDIT_CONFIG_FILE


def scope_violations(files: list[str], outside_changed: list[str]) -> list[str]:
    """Rule check on a collected diff (pure Python, runs on the driver)."""
    bad = [f"sửa file ngoài {EDITABLE_PREFIX}: {f}" for f in files if not f.startswith(EDITABLE_PREFIX)]
    bad += [f"sửa file cấu hình: {f}" for f in files
            if f.startswith(EDITABLE_PREFIX) and FORBIDDEN_FILE_RE.search(os.path.basename(f))]
    bad += [f"sửa ngoài generators/rocket-chip: {x}" for x in outside_changed]
    return bad


# ---------------------------------------------------------------------------
# RocketTile RTL out of the build's generated sources
# ---------------------------------------------------------------------------

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.M)
_INST_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+(?:#\s*\(|[A-Za-z_]\w*\s*\()", re.M)


def _split_modules(text: str) -> dict[str, str]:
    """{module name: module text} for every module defined in `text`."""
    out = {}
    for m in _MODULE_RE.finditer(text):
        end = text.find("endmodule", m.end())
        out[m.group(1)] = text[m.start(): end + len("endmodule") if end >= 0 else len(text)]
    return out


def _port_stub(module_text: str) -> str:
    """Empty module with the same ports, for STA: header up to `);` plus any
    non-ANSI port declarations."""
    head_end = module_text.find(");")
    header = module_text[: head_end + 2]
    decls = [l for l in module_text[head_end + 2:].splitlines()
             if re.match(r"\s*(input|output|inout)\b", l)]
    return "\n".join([header, *decls, "endmodule", ""])


@dataclass
class TileRTL:
    top: str
    files: dict             # {filename: text} defining the modules under `top`
    blackboxes: dict        # {sram module: original text}, read with -lib by Yosys
    stubs: str              # empty port-only modules of the blackboxes, for STA
    modules: list           # modules in the hierarchy (blackboxes included)
    replaced: list          # modules swapped for a synthesis stub (EICG_wrapper)
    mems_conf: str = ""


def tile_rtl_from_sources(generated_src_files: list, top: str = TOP, stub_modules=()) -> TileRTL:
    """Pure function behind extract_tile_rtl (kept separate for local tests).
    `stub_modules` become black boxes like the SRAMs (e.g. RocketTile in ChipTop)."""
    files = {}
    mems_conf = ""
    for name, text in generated_src_files:
        if name.endswith(".top.mems.conf"):
            mems_conf = text
        elif name.endswith((".v", ".sv")):
            files.setdefault(name, text)
    srams = {l.split()[1] for l in mems_conf.splitlines() if l.startswith("name ")} | set(stub_modules)

    defined: dict[str, tuple[str, str]] = {}     # module -> (file, module text)
    for fname, text in files.items():
        for mod, mtext in _split_modules(text).items():
            defined.setdefault(mod, (fname, mtext))
    if top not in defined:
        raise RuntimeError(f"module {top} not found in {len(files)} generated files")

    seen, todo = set(), [top]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        if mod in srams or mod == "EICG_wrapper":
            continue
        children = {c for c in _INST_RE.findall(defined[mod][1]) if c in defined}
        todo.extend(children - seen)

    blackboxes = {m: defined[m][1] for m in sorted(seen & srams)}
    replaced = sorted(seen & {"EICG_wrapper"})
    keep_files = sorted({defined[m][0] for m in seen - srams - set(replaced)})
    out_files = {f: files[f] for f in keep_files}
    if replaced:
        out_files["EICG_wrapper_synth_stub.v"] = EICG_STUB
    return TileRTL(top=top, files=out_files, blackboxes=blackboxes,
                   stubs="\n".join(_port_stub(t) for t in blackboxes.values()),
                   modules=sorted(seen), replaced=replaced, mems_conf=mems_conf)


@ChiaFunction(max_retries=0)
def extract_tile_rtl(artifact) -> TileRTL:
    """RocketTile hierarchy (as text) from a BuildArtifact built with
    collect_generated_src=True."""
    return tile_rtl_from_sources(artifact.generated_src_files, TOP)


@ChiaFunction(max_retries=0)
def extract_soc_rtl(artifact) -> TileRTL:
    """ChipTop hierarchy with RocketTile and the SRAMs as black boxes (config audit)."""
    return tile_rtl_from_sources(artifact.generated_src_files, SOC_TOP, stub_modules=(TOP,))


def sram_bits(mems_conf: str) -> dict:
    """{sram module: depth * width} from a .top.mems.conf."""
    out = {}
    for line in mems_conf.splitlines():
        f = line.split()
        kv = dict(zip(f[0::2], f[1::2]))
        if "name" in kv and "depth" in kv and "width" in kv:
            out[kv["name"]] = int(kv["depth"]) * int(kv["width"])
    return out


# ---------------------------------------------------------------------------
# eda: Yosys + OpenSTA
# ---------------------------------------------------------------------------

# Synthesis flows; the name is part of the baseline cache tag, so add a new
# name instead of changing an existing flow.
#   v1 (gone): abc -D without -constr: no buffering/sizing, ~80 ns of unbuffered fanout.
#   v2: abc -D -constr (ABC's default script then adds buffer; upsize; dnsize).
#       $lcu is Brent-Kung (techmap.v default), $mul is Yosys' array multiplier.
#   v3 (gone): -booth + +/choices/kogge-stone.v; that file only exists from Yosys 0.40
#       and the eda image has 0.38.
SYNTH_FLOWS = {
    "v2": "synth -flatten -top {top}",
}

# Same driver/load as ORFS sky130hd (ABC_DRIVER_CELL, ABC_LOAD_IN_FF).
ABC_CONSTR = "set_driving_cell sky130_fd_sc_hd__buf_1\nset_load 5\n"

YOSYS_SCRIPT = """\
read_liberty -lib {lib}
{read_blackboxes}
read_verilog -sv -DSYNTHESIS {files}
hierarchy -check -top {top}
{synth}
rename -wire -suffix _reg t:*DFF*
dfflibmap -liberty {lib}
abc -D {period_ps} -liberty {lib} -constr abc.constr
opt_clean -purge
setundef -zero
hilomap -singleton -hicell sky130_fd_sc_hd__conb_1 HI -locell sky130_fd_sc_hd__conb_1 LO
opt_clean -purge
tee -o {stat} stat -liberty {lib}
write_verilog -noattr -noexpr -nohex -nodec {netlist}
"""

STA_SCRIPT = """\
read_liberty {lib}
{read_stubs}
read_verilog {netlist}
link_design {top}
create_clock -name clk -period {period_ns} [get_ports clock]
set_input_delay 0 -clock clk [lsearch -inline -all -not -exact [all_inputs] [get_ports clock]]
set_output_delay 0 -clock clk [all_outputs]
puts "@@WORST"
report_worst_slack -max -digits 4
puts "@@WNS"
report_wns -digits 4
puts "@@TNS"
report_tns -digits 4
puts "@@ENDPOINTS"
report_checks -path_delay max -group_count 2000 -format end -digits 3
puts "@@PATHS"
report_checks -path_delay max -group_count 10 -fields {{slew cap input_pins nets fanout}} -digits 3
puts "@@END"
"""


@dataclass
class SynthResult:
    success: bool
    period_ns: float
    flow: str = ""
    worst_slack_ns: float | None = None   # report_worst_slack (can be > 0)
    wns_ns: float | None = None           # report_wns (0 when nothing fails)
    tns_ns: float | None = None
    fmax_mhz: float | None = None         # 1000 / (period - worst_slack)
    crit_start: str = ""
    crit_end: str = ""
    endpoints: str = ""                   # worst 2000 endpoints with slack (one line each)
    families: str = ""                    # endpoints grouped by register (bit index removed)
    paths: str = ""                       # top-10 full path reports
    area_um2: float | None = None
    yosys_log_tail: str = ""
    stat: str = ""
    sta_log: str = ""
    yosys_seconds: float = 0.0
    sta_seconds: float = 0.0
    error: str = ""
    lib_sha256: str = ""
    removed_cells: list = field(default_factory=list)


def filter_liberty(text: str, dont_use: re.Pattern = DONT_USE_RE) -> tuple[str, list[str]]:
    """Drop `cell (name) {...}` groups whose name matches `dont_use`."""
    out, removed, pos = [], [], 0
    for m in re.finditer(r'^\s*cell\s*\(\s*"?([\w$]+)"?\s*\)\s*\{', text, re.M):
        if m.start() < pos or not dont_use.match(m.group(1)):
            continue
        depth, i = 1, m.end()
        while depth:
            j = min(k for k in (text.find("{", i), text.find("}", i)) if k >= 0)
            depth += 1 if text[j] == "{" else -1
            i = j + 1
        out.append(text[pos:m.start()])
        pos = i
        removed.append(m.group(1))
    out.append(text[pos:])
    return "".join(out), removed


def _section(log: str, name: str) -> str:
    m = re.search(rf"@@{name}\n(.*?)(?=\n@@|\Z)", log, re.S)
    return m.group(1).strip() if m else ""


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def parse_sta(log: str, period_ns: float) -> dict:
    worst = _num(r"worst slack(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", _section(log, "WORST"))
    wns = _num(r"wns(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", _section(log, "WNS"))
    tns = _num(r"tns(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", _section(log, "TNS"))
    paths = _section(log, "PATHS")
    start = re.search(r"Startpoint:\s*(\S+)", paths)
    end = re.search(r"Endpoint:\s*(\S+)", paths)
    fmax = 1000.0 / (period_ns - worst) if worst is not None and period_ns - worst > 0 else None
    return dict(worst_slack_ns=worst, wns_ns=wns, tns_ns=tns, fmax_mhz=fmax,
                crit_start=start.group(1) if start else "", crit_end=end.group(1) if end else "",
                endpoints=_section(log, "ENDPOINTS"), paths=paths)


def endpoint_families(endpoints: str, n: int = 15) -> str:
    """Group `report_checks -format end` lines by register name without the
    bit index: worst slack, number of failing bits, one example endpoint."""
    fam: dict[str, list] = {}
    for line in endpoints.splitlines():
        m = re.match(r"\s*(\S+)\s.*?(-?\d+\.\d+)\s*(?:\(VIOLATED\)|\(MET\))?\s*$", line)
        if not m:
            continue
        pin, slack = m.group(1), float(m.group(2))
        key = re.sub(r"\[\d+\]", "[*]", pin.rsplit("/", 1)[0])
        key = re.sub(r"_\d+(?=_reg$)", "_*", key)
        f = fam.setdefault(key, [slack, 0, pin])
        f[1] += 1
        if slack < f[0]:
            f[0], f[2] = slack, pin
    rows = sorted(fam.items(), key=lambda kv: kv[1][0])[:n]
    out = [f"{'worst slack':>11}  {'bits':>4}  register (bit index -> [*])"]
    out += [f"{v[0]:11.3f}  {v[1]:4d}  {k}" for k, v in rows]
    return "\n".join(out)


def parse_area(stat: str) -> float | None:
    vals = re.findall(r"Chip area for (?:top )?module '?\\?[\w$.]*'?:\s*([\d.]+)", stat)
    return float(vals[-1]) if vals else None


@ChiaFunction(resources={"eda": 1}, num_cpus=2, max_retries=0)
def synth_sta(rtl: TileRTL, period_ns: float, flow: str = "v2",
              yosys_timeout: int = 4 * 3600) -> SynthResult:
    """Yosys (synth -flatten, dfflibmap, abc -D) + OpenSTA on rtl.top at a fixed
    clock period. SRAM *_ext modules stay black boxes (their pins are
    unconstrained), I/O get 0 delay against `clock`."""
    res = SynthResult(success=False, period_ns=period_ns, flow=flow)
    lib_src = os.environ.get("SKY130HD_LIB", "/home/ray/pdk/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib")
    with open(lib_src) as f:
        lib_text = f.read()
    res.lib_sha256 = hashlib.sha256(lib_text.encode()).hexdigest()
    work = tempfile.mkdtemp(prefix="rocket_fmax_synth_")
    try:
        # Scripts use paths relative to `work` (the cwd of yosys and sta).
        lib = "sky130hd_filtered.lib"
        filtered, res.removed_cells = filter_liberty(lib_text)
        with open(os.path.join(work, lib), "w") as f:
            f.write(filtered)
        os.makedirs(os.path.join(work, "rtl"))
        for name, text in rtl.files.items():
            with open(os.path.join(work, "rtl", name), "w") as f:
                f.write(text)
        bb_paths = []
        for name, text in rtl.blackboxes.items():
            bb_paths.append(f"bb_{name}.v")
            with open(os.path.join(work, bb_paths[-1]), "w") as f:
                f.write(text + "\n")
        stubs, netlist, stat = "bb_stubs.v", "netlist.v", "stat.txt"
        with open(os.path.join(work, stubs), "w") as f:
            f.write(rtl.stubs)
        ys = YOSYS_SCRIPT.format(
            lib=lib, top=rtl.top, period_ps=int(round(period_ns * 1000)),
            synth=SYNTH_FLOWS[flow].format(top=rtl.top),
            read_blackboxes="\n".join(f"read_verilog -lib -sv {p}" for p in bb_paths),
            files=" ".join(f"rtl/{n}" for n in sorted(rtl.files)),
            stat=stat, netlist=netlist)
        with open(os.path.join(work, "synth.ys"), "w") as f:
            f.write(ys)
        with open(os.path.join(work, "abc.constr"), "w") as f:
            f.write(ABC_CONSTR)
        t0 = time.time()
        try:
            y = subprocess.run(["yosys", "-q", "-l", "yosys.log", "-s", "synth.ys"],
                               cwd=work, capture_output=True, text=True, timeout=yosys_timeout)
            rc, yerr = y.returncode, y.stdout + y.stderr
        except subprocess.TimeoutExpired:
            rc, yerr = -1, ""
        res.yosys_seconds = time.time() - t0
        ylog_path = os.path.join(work, "yosys.log")
        ylog = open(ylog_path, errors="replace").read() if os.path.exists(ylog_path) else ""
        res.yosys_log_tail = ylog[-20000:] + (f"\n[yosys stdout/stderr]\n{yerr[-5000:]}" if yerr else "")
        if rc != 0 or not os.path.exists(os.path.join(work, netlist)):
            res.error = f"yosys returncode {rc}" + (" (timeout)" if rc == -1 else "")
            return res
        stat_path = os.path.join(work, stat)
        res.stat = open(stat_path).read() if os.path.exists(stat_path) else ""
        res.area_um2 = parse_area(res.stat)

        with open(os.path.join(work, "sta.tcl"), "w") as f:
            f.write(STA_SCRIPT.format(lib=lib, netlist=netlist, top=rtl.top, period_ns=period_ns,
                                      read_stubs=f"read_verilog {stubs}" if rtl.stubs else ""))
        t0 = time.time()
        s = subprocess.run(["sta", "-no_init", "-no_splash", "-exit", "sta.tcl"], cwd=work,
                           capture_output=True, text=True, timeout=3600)
        res.sta_seconds = time.time() - t0
        res.sta_log = s.stdout + ("\nSTDERR:\n" + s.stderr if s.stderr else "")
        for k, v in parse_sta(s.stdout, period_ns).items():
            setattr(res, k, v)
        res.families = endpoint_families(res.endpoints)
        if s.returncode != 0 or res.worst_slack_ns is None:
            res.error = f"sta returncode {s.returncode}, worst slack not found"
            return res
        res.success = True
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


# Area only, hierarchy kept (config audit): per-module cells -> area per block.
AREA_SCRIPT = """\
read_liberty -lib {lib}
{read_blackboxes}
read_verilog -sv -DSYNTHESIS {files}
hierarchy -check -top {top}
synth -top {top}
dfflibmap -liberty {lib}
abc -D {period_ps} -liberty {lib} -constr abc.constr
opt_clean -purge
tee -o {stat} stat -liberty {lib} -top {top}
"""


def sv_packed_patterns(text: str) -> tuple[str, int]:
    """firtool writes small Vec lookups as packed-array assignment patterns
    (`wire [3:0][3:0] _GEN = '{4'hC, 4'h8, ...};`), which the Yosys in the eda
    image cannot parse (`unexpected OP_CAST`). For a packed array '{a, b, ...}
    equals the concatenation {a, b, ...} as long as every element has the
    element's width, which firtool's sized literals and signals do. Returns
    (text, patterns rewritten); raises on patterns that are not plain lists."""
    code = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), text)   # comments masked, same offsets
    starts = [m.start() for m in re.finditer(r"'\{", code)]
    for s in starts:
        depth, i = 0, s + 1
        while i < len(code):
            depth += {"{": 1, "}": -1}.get(code[i], 0)
            if depth == 0:
                break
            i += 1
        body = code[s + 2: i]
        if re.search(r"\bdefault\b|[{,]\s*\w+\s*:(?!:)|^\s*\w+\s*:(?!:)", body) or \
                any(re.fullmatch(r"\s*\d+\s*", e) for e in body.split(",")):
            raise ValueError(f"unsupported assignment pattern: '{{{body[:120]}}}")
    out = list(text)
    for s in starts:
        out[s] = " "
    return "".join(out), len(starts)


@dataclass
class AreaResult:
    success: bool
    top: str
    area_um2: float | None = None      # whole hierarchy (black boxes count 0)
    module_area: dict = field(default_factory=dict)   # {module: own cell area}
    stat: str = ""
    yosys_log_tail: str = ""
    seconds: float = 0.0
    error: str = ""


def parse_module_areas(stat: str) -> dict:
    out = {}
    for m in re.finditer(r"Chip area for module '\\?([^']+)':\s*([\d.]+)", stat):
        out[m.group(1)] = float(m.group(2))
    return out


@ChiaFunction(resources={"eda": 1}, num_cpus=2, max_retries=0)
def synth_area(rtl: TileRTL, period_ns: float, yosys_timeout: int = 4 * 3600) -> AreaResult:
    """Hierarchical Yosys run on rtl.top (no flatten, no STA): area per module."""
    res = AreaResult(success=False, top=rtl.top)
    lib_src = os.environ.get("SKY130HD_LIB", "/home/ray/pdk/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib")
    work = tempfile.mkdtemp(prefix="rocket_fmax_area_")
    try:
        lib = "sky130hd_filtered.lib"
        with open(lib_src) as f:
            filtered, _ = filter_liberty(f.read())
        with open(os.path.join(work, lib), "w") as f:
            f.write(filtered)
        os.makedirs(os.path.join(work, "rtl"))
        for name, text in rtl.files.items():
            try:
                text, _ = sv_packed_patterns(text)
            except ValueError as e:
                res.error = f"{name}: {e}"
                return res
            with open(os.path.join(work, "rtl", name), "w") as f:
                f.write(text)
        bb_paths = []
        for name, text in rtl.blackboxes.items():
            bb_paths.append(f"bb_{name}.v")
            with open(os.path.join(work, bb_paths[-1]), "w") as f:
                f.write(text + "\n")
        with open(os.path.join(work, "abc.constr"), "w") as f:
            f.write(ABC_CONSTR)
        with open(os.path.join(work, "area.ys"), "w") as f:
            f.write(AREA_SCRIPT.format(
                lib=lib, top=rtl.top, period_ps=int(round(period_ns * 1000)), stat="stat.txt",
                read_blackboxes="\n".join(f"read_verilog -lib -sv {p}" for p in bb_paths),
                files=" ".join(f"rtl/{n}" for n in sorted(rtl.files))))
        t0 = time.time()
        try:
            y = subprocess.run(["yosys", "-q", "-l", "yosys.log", "-s", "area.ys"],
                               cwd=work, capture_output=True, text=True, timeout=yosys_timeout)
            rc, yerr = y.returncode, y.stdout + y.stderr
        except subprocess.TimeoutExpired:
            rc, yerr = -1, ""
        res.seconds = time.time() - t0
        ylog = os.path.join(work, "yosys.log")
        res.yosys_log_tail = (open(ylog, errors="replace").read()[-20000:] if os.path.exists(ylog) else "") \
            + (f"\n[yosys stdout/stderr]\n{yerr[-5000:]}" if yerr else "")
        stat = os.path.join(work, "stat.txt")
        if rc != 0 or not os.path.exists(stat):
            res.error = f"yosys returncode {rc}" + (" (timeout)" if rc == -1 else "")
            return res
        res.stat = open(stat).read()
        res.area_um2 = parse_area(res.stat)
        res.module_area = parse_module_areas(res.stat)
        res.success = res.area_um2 is not None
        if not res.success:
            res.error = "không đọc được area trong stat"
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def block_tree(rtl: TileRTL, module_area: dict, max_depth: int = 3) -> list[tuple]:
    """[(depth, instance path, module, subtree area or None for black boxes)]
    for rtl.top down to `max_depth`, from the RTL instance structure."""
    mods = E.modules_of(rtl.files)
    inst_re = re.compile(r"^\s*([A-Za-z_]\w*)\s+(?:#\s*\(.*?\)\s*)?([A-Za-z_]\w*)\s*\(", re.M | re.S)
    kids = {m: [(i, c) for c, i in inst_re.findall(t) if c in mods or c in rtl.blackboxes]
            for m, t in mods.items()}
    memo: dict[str, float] = {}

    def area(m):
        if m not in memo:
            memo[m] = module_area.get(m, 0.0) + sum(area(c) for _, c in kids.get(m, []))
        return memo[m]

    rows = []

    def walk(m, path, depth):
        rows.append((depth, path, m, None if m in rtl.blackboxes else area(m)))
        if depth < max_depth:
            for inst, c in sorted(kids.get(m, []), key=lambda ic: -area(ic[1])):
                walk(c, f"{path}.{inst}", depth + 1)

    walk(rtl.top, rtl.top, 0)
    return rows


# ---------------------------------------------------------------------------
# equivalence of the best patch vs the baseline (see equiv.py)
# ---------------------------------------------------------------------------

def _write_unit(work: str, unit) -> None:
    for name, text in (("gold.sv", unit.gold), ("gate.sv", unit.gate), ("shared.sv", unit.shared),
                       ("tb.sv", E.tb_verilog(unit)), ("tb.cpp", E.tb_cpp(unit)),
                       ("eq.ys", E.formal_script(unit))):
        with open(os.path.join(work, name), "w") as f:
            f.write(text)


@ChiaFunction(resources={"eda": 1}, num_cpus=2, max_retries=0)
def equiv_formal(unit, timeout_s: int = 1800) -> dict:
    """Yosys equiv_* on one unit. status: proven / unproven / timeout / error."""
    work = tempfile.mkdtemp(prefix="rocket_fmax_equiv_")
    t0 = time.time()
    try:
        _write_unit(work, unit)
        try:
            y = subprocess.run(["yosys", "-q", "-l", "formal.log", "-s", "eq.ys"], cwd=work,
                               capture_output=True, text=True, timeout=timeout_s)
            rc, err = y.returncode, y.stdout + y.stderr
        except subprocess.TimeoutExpired:
            rc, err = None, ""
        log_path, st_path = os.path.join(work, "formal.log"), os.path.join(work, "equiv_status.txt")
        log = open(log_path, errors="replace").read() if os.path.exists(log_path) else ""
        st = E.parse_equiv_status(open(st_path).read()) if os.path.exists(st_path) else {}
        if rc is None:
            status = "timeout"
        elif rc != 0 or st.get("unproven") is None:
            status = "error"
        else:
            status = "proven" if st["unproven"] == 0 and st["total"] else "unproven"
        return {"unit": unit.top, "status": status, "counts": st, "returncode": rc,
                "seconds": time.time() - t0, "log_tail": log[-8000:] + err[-2000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def equiv_sim(unit, cycles: int = 10_000_000, seed: int = 1, jobs: int = 8,
              timeout_s: int = 3600) -> dict:
    """Verilator lockstep of the baseline and patched unit (random inputs,
    every output compared). status: pass / mismatch / error."""
    work = tempfile.mkdtemp(prefix="rocket_fmax_equivsim_")
    t0 = time.time()
    try:
        _write_unit(work, unit)
        env = (f"[ -f {CHIPYARD}/env.sh ] && source {CHIPYARD}/env.sh >/dev/null 2>&1; "
               f"export PATH=$PATH:{CHIPYARD}/.conda-env/bin; cd {work}")
        vcmd = E.VERILATOR_CMD.format(jobs=jobs, shared="shared.sv" if unit.shared else "")
        b = subprocess.run(["bash", "-lc", f"{env} && {vcmd}"], capture_output=True, text=True,
                           timeout=timeout_s)
        build_log = (b.stdout + b.stderr)[-6000:]
        if b.returncode != 0:
            return {"unit": unit.top, "status": "error", "error": "verilator build lỗi",
                    "build_log": build_log, "seconds": time.time() - t0}
        r = subprocess.run(["bash", "-lc", f"{env} && obj/equiv_sim {cycles} {seed}"],
                           capture_output=True, text=True, timeout=timeout_s)
        p = E.parse_sim(r.stdout)
        status = ("pass" if r.returncode == 0 and p["bad_cycles"] == 0
                  else "mismatch" if p["bad_cycles"] else "error")
        return {"unit": unit.top, "status": status, **p, "returncode": r.returncode,
                "output_tail": (r.stdout + r.stderr)[-4000:], "build_log": build_log,
                "seconds": time.time() - t0}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# verilator_run: the correctness / cycle gate
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    isa: dict                    # {test: passed}
    bench: dict                  # {name: {"passed", "mcycle", "minstret", "sim_cycles", "seed_line"}}
    bench_repeat: dict           # same, second run with another seed (baseline only)
    fail_logs: dict              # {test: log/out tail} for failures
    seconds: float = 0.0


def _bench_info(r) -> dict:
    mc = re.search(r"mcycle\s*=\s*(\d+)", r.log)
    mi = re.search(r"minstret\s*=\s*(\d+)", r.log)
    sc = re.search(r"after\s+(\d+)\s+simulation cycles", r.out + r.log)
    seed = next((l for l in (r.out + "\n" + r.log).splitlines() if "random" in l and "seed" in l), "")
    return {"passed": r.returncode == 0 and mc is not None,
            "mcycle": int(mc.group(1)) if mc else None,
            "minstret": int(mi.group(1)) if mi else None,
            "sim_cycles": int(sc.group(1)) if sc else None,
            "seed_line": seed.strip()}


def _tail(r) -> str:
    return f"returncode={r.returncode}\n[log]\n{r.log[-1500:]}\n[out]\n{r.out[-1500:]}"


@ChiaFunction(num_cpus=0, max_retries=0)
def run_gate(artifact, elfs: dict, repeat_seed: int | None = None,
             isa_cycles: int = 5_000_000, bench_cycles: int = 200_000_000) -> GateResult:
    """Run every ISA test and benchmark on the simulator in `artifact`.

    With `repeat_seed`, the benchmarks run a second time with
    +verilator+seed+<n> (determinism check, baseline only).
    """
    import ray
    from chia.chipyard.verilator_run_node import VerilatorRunNode

    t0 = time.time()
    art = ray.put(artifact)
    sim = VerilatorRunNode()

    def launch(filename, elf, cycles, timeout, plusargs=None):
        return sim.run.chia_remote(sim, art, elf, filename, SIM_WORK_DIR, plusargs=plusargs or {},
                                   timeout_cycles=cycles, timeout_seconds=timeout, verbose=False)

    isa_refs = {n: launch(n, e, isa_cycles, 600) for n, e in elfs["isa"].items()}
    bench_refs = {n: launch(f"{n}.riscv", e, bench_cycles, 3600) for n, e in elfs["bench"].items()}
    rep_refs = ({n: launch(f"{n}.riscv", e, bench_cycles, 3600, {f"+verilator+seed+{repeat_seed}": ""})
                 for n, e in elfs["bench"].items()} if repeat_seed is not None else {})

    isa, bench, rep, fails = {}, {}, {}, {}
    for n, ref in isa_refs.items():
        r = get(ref)
        isa[n] = r.returncode == 0 and "FAILED" not in r.out[-4000:] and "FAILED" not in r.log[-4000:]
        if not isa[n]:
            fails[n] = _tail(r)
    for dst, refs in ((bench, bench_refs), (rep, rep_refs)):
        for n, ref in refs.items():
            r = get(ref)
            dst[n] = _bench_info(r)
            if not dst[n]["passed"]:
                fails[n if dst is bench else f"{n}(seed {repeat_seed})"] = _tail(r)
    return GateResult(isa=isa, bench=bench, bench_repeat=rep, fail_logs=fails,
                      seconds=time.time() - t0)


# ---------------------------------------------------------------------------
# bypass providers (module level so they pickle by reference)
# ---------------------------------------------------------------------------

def cache_provider(tag, data_path, *args, **kwargs):
    """Serve a cached result for `tag` (baseline replay)."""
    from chia.base.cache import get_active_cache
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss for tag {tag!r}")
    return value


def cache_hit_cond(tag, data_path, *args, **kwargs):
    """Bypass only when `tag` is really in the cache; otherwise run for real."""
    from chia.base.cache import get_active_cache
    return get(get_active_cache().has.chia_remote(tag))


def canned_llm_result(tag, data_path, *args, **kwargs):
    """Milestone 2: stands in for OpenCodeLLM.prompt; the edit itself is made
    by apply_edit on the chipyard worker."""
    from chia.base.llm_call import QueryResult
    return QueryResult(result=f"[bypass {tag}] LLM skipped; the loop applies the milestone-2 edit.",
                       returncode=0, stderr="", stream_result="", success=True)
