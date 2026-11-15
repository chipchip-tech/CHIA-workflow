"""ORFS (OpenROAD-flow-scripts) sky130hd cho DigitalTop: chuẩn bị design, chạy từng bước, đọc kết quả.

Chia hai phía:
  - driver (host): prepare_design() ghi thư mục design vào ORFS trên host
      <ORFS_DIR>/flow/designs/sky130hd/soc_fmax/<variant>/  (soc.sv, constraint.sdc, config.mk, fakeram/, *.tcl)
    Khối asic mount <ORFS_DIR> vào /OpenROAD-flow-scripts, nên container thấy đúng các file này.
  - khối asic: orfs_stage() chạy `make <bước>` có giới hạn thời gian, rồi script báo cáo (make run), đọc log
    và trả StageResult (dữ liệu, không phải đường dẫn). Kết quả của ORFS (odb, report, GDS) nằm lại trong
    <ORFS_DIR>/flow/{results,logs,reports}/sky130hd/soc_fmax/<variant>/ trên host.

variant = <rtl hash 12>_p<chu kỳ>_<FLOW_VERSION>_<fakeram>: cùng RTL + chu kỳ + phiên bản flow thì dùng lại.
"""
from __future__ import annotations

import dataclasses
import glob
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from chia.base.ChiaFunction import ChiaFunction

import soc_nodes as S

FLOW_VERSION = "o3"                       # đổi khi đổi config.mk/SDC/script báo cáo
PLATFORM = "sky130hd"
NICK = "soc_fmax"
C_ORFS = "/OpenROAD-flow-scripts"          # đường dẫn ORFS trong container asic
HERE = Path(__file__).resolve().parent

# Mặc định của config.mk; --orfs-var KEY=VAL ghi đè (ghi vào variant qua hash các biến).
ORFS_DEFAULTS = {
    "SYNTH_HIERARCHICAL": "1",
    "ABC_AREA": "0",
    "CORE_UTILIZATION": "40",
    "PLACE_DENSITY": "0.55",
    "MACRO_PLACE_HALO": "40 40",   # 10 10: PDN-0179 (khe met1 giữa macro không có strap met4/met5)
    "TNS_END_PERCENT": "100",
}


def design_dir_rel(variant: str) -> str:
    return f"flow/designs/{PLATFORM}/{NICK}/{variant}"


def work_dirs_rel(variant: str) -> dict:
    return {k: f"flow/{k}/{PLATFORM}/{NICK}/{variant}" for k in ("results", "logs", "reports", "objects")}


# ---------------------------------------------------------------------------
# driver: chuẩn bị design
# ---------------------------------------------------------------------------

_CAST_SIGN_RE = re.compile(r"\b(signed|unsigned)\s*'\s*\(")
_CAST_TYPE_RE = re.compile(r"\b(?!\d)([A-Za-z_]\w*)\s*'\s*\(")
_PACKED_PATTERN_RE = re.compile(r"(^[ \t]*(?:wire|logic|reg)\s+(?:\[[^\]]+\]\s*){2,}[A-Za-z_]\w*\s*=\s*)'\{", re.M)


def sv_casts_for_yosys(text: str) -> tuple[str, int]:
    """Viết lại cast signed'/unsigned'/T'(x) và '{...} trên mảng packed (từ soc-fmax cũ: Yosys cũ không đọc
    được Atomics.sv của InclusiveCache). Cast kích thước 8'(x) giữ nguyên."""
    text, n1 = _CAST_SIGN_RE.subn(lambda m: f"${m.group(1)}(", text)
    text, n2 = _CAST_TYPE_RE.subn("(", text)
    text, n3 = _PACKED_PATTERN_RE.subn(lambda m: m.group(1) + "{", text)
    return text, n1 + n2 + n3


def top_ports(rtl: "S.SocRTL", fakeram) -> dict:
    """Cổng của DigitalTop chia nhóm: clock, reset, vào khác, ra."""
    ports = fakeram.verilog_ports(rtl.modules[rtl.top], rtl.top) or {}
    clk = sorted(p for p, (d, _) in ports.items() if d == "input" and re.search(r"(^|_)clock$", p))
    # Clock ngoài, bất đồng bộ với clk (đo thật ở synth o2: serial_tl_0_clock_in nuôi hàng đợi async của
    # serial_tl_domain/phy, debug_systemjtag_jtag_TCK nuôi dtm và tlDM/dmOuter).
    aux = sorted(p for p, (d, _) in ports.items() if d == "input" and p not in clk
                 and re.search(r"(^|_)(clock_in|TCK)$", p))
    rst = sorted(p for p, (d, _) in ports.items() if d == "input" and re.search(r"reset", p, re.I)
                 and p not in clk and p not in aux)
    ins = sorted(p for p, (d, _) in ports.items() if d == "input" and p not in clk + aux + rst)
    outs = sorted(p for p, (d, _) in ports.items() if d == "output")
    return {"clock": clk, "aux_clock": aux, "reset": rst, "in": ins, "out": outs}


def _tcl_list(names) -> str:
    return "{" + " ".join(names) + "}"


def sdc_text(period_ns: float, ports: dict) -> str:
    """Một clock lý tưởng `clk` trên mọi cổng clock (cùng một nguồn trong ChipTop: tính như một miền đồng bộ).
    Cổng I/O trễ 0 so với clk (Fmax không tính đường từ/tới cổng, xem report.tcl). Reset bất đồng bộ và tầng đầu
    bộ đồng bộ (*sync_0*) không tính (như soc_synth.py của soc-fmax cũ)."""
    if not ports["clock"]:
        raise RuntimeError("DigitalTop không có cổng nào tên *clock")
    t = [f"# soc-fmax {FLOW_VERSION}: sinh bởi orfs_nodes.py",
         f"set clk_period {period_ns}",
         f"create_clock -name clk -period $clk_period [get_ports {_tcl_list(ports['clock'])}]"]
    aux = ports.get("aux_clock", [])
    for i, p in enumerate(aux):
        # Miền phụ (JTAG, serial TL): chu kỳ 5x để không bao giờ thành đường găng; không tính vào Fmax.
        t.append(f"create_clock -name aux{i} -period [expr {{5 * $clk_period}}] [get_ports {_tcl_list([p])}]")
    if aux:
        t.append("set_clock_groups -asynchronous -group {clk} " + " ".join(f"-group {{aux{i}}}" for i in range(len(aux))))
    if ports["in"]:
        t.append(f"set_input_delay 0 -clock clk [get_ports {_tcl_list(ports['in'])}]")
    if ports["out"]:
        t.append(f"set_output_delay 0 -clock clk [get_ports {_tcl_list(ports['out'])}]")
    if ports["reset"]:
        t.append(f"set_false_path -from [get_ports {_tcl_list(ports['reset'])}]")
    t += ["# Chân reset/set bất đồng bộ của flop và tầng đầu bộ đồng bộ: không tính (cần netlist; bỏ qua nếu lỗi)",
          "if {[catch {set ap [all_registers -async_pins]}] == 0 && [llength $ap]} { set_false_path -to $ap }",
          "if {[catch {set sy [get_cells -hierarchical -quiet *sync_0*]}] == 0 && [llength $sy]} { set_false_path -to $sy }",
          ""]
    return "\n".join(t)


def variant_name(rtl_hash: str, period_ns: float, fr_tag: str, orfs_vars: dict) -> str:
    extra = ""
    if orfs_vars != ORFS_DEFAULTS:
        import hashlib
        extra = "_v" + hashlib.sha256(json.dumps(orfs_vars, sort_keys=True).encode()).hexdigest()[:6]
    return f"{rtl_hash[:12]}_p{period_ns:g}_{FLOW_VERSION}_{fr_tag}{extra}".replace(".", "p")


def _put(path: Path, data) -> bool:
    """Ghi khi nội dung đổi (giữ mtime: make của ORFS chạy lại mọi bước nếu file vào mới hơn kết quả)."""
    data = data.encode() if isinstance(data, str) else data
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def prepare_design(orfs_dir: str, rtl: "S.SocRTL", period_ns: float, fakeram, fr_params, variant: str,
                   orfs_vars: dict) -> dict:
    """Ghi thư mục design (trên host). Trả thông tin để báo cáo (fakeram, cổng, số cast đã viết lại)."""
    import tempfile
    d = Path(orfs_dir) / design_dir_rel(variant)
    cd = f"{C_ORFS}/{design_dir_rel(variant)}"
    changed = []
    text, ncast = sv_casts_for_yosys(rtl.verilog())
    if _put(d / "soc.sv", text):
        changed.append("soc.sv")
    mems = fakeram.parse_conf(rtl.mems_conf)
    issues = fakeram.check_against_verilog(mems, "\n".join(rtl.modules.values()))
    fr_info = {"macros": [], "total_area_um2": 0, "total_bits": 0}
    if mems:
        with tempfile.TemporaryDirectory() as tmp:
            fr_info = fakeram.generate(mems, tmp, fr_params)
            for f in sorted(Path(tmp).iterdir()):
                if _put(d / "fakeram" / f.name, f.read_bytes()):
                    changed.append(f"fakeram/{f.name}")
    ports = top_ports(rtl, fakeram)
    if _put(d / "constraint.sdc", sdc_text(period_ns, ports)):
        changed.append("constraint.sdc")
    _put(d / "report.tcl", (HERE / "orfs" / "report.tcl").read_text())
    _put(d / "diag_floorplan.tcl", (HERE / "orfs" / "diag_floorplan.tcl").read_text())
    lines = [f"# soc-fmax {FLOW_VERSION}: sinh bởi orfs_nodes.py, variant {variant}",
             f"export PLATFORM = {PLATFORM}", "export DESIGN_NAME = DigitalTop", f"export DESIGN_NICKNAME = {NICK}",
             f"export VERILOG_FILES = {cd}/soc.sv", f"export SDC_FILE = {cd}/constraint.sdc",
             f"export ABC_CLOCK_PERIOD_IN_PS = {int(round(period_ns * 1000))}"]
    if mems:
        lines += [f"export ADDITIONAL_LEFS = {cd}/fakeram/fakeram_sky130.lef",
                  f"export ADDITIONAL_LIBS = {cd}/fakeram/fakeram_sky130.lib",
                  f"export ADDITIONAL_GDS = {cd}/fakeram/fakeram_sky130.gds"]
    lines += [f"export {k} = {v}" for k, v in orfs_vars.items()]
    if _put(d / "config.mk", "\n".join(lines) + "\n"):
        changed.append("config.mk")
    info = {"variant": variant, "design_dir": str(d), "config_mk": f"{cd}/config.mk",
            "ports": {k: len(v) for k, v in ports.items()}, "clock_ports": ports["clock"], "aux_clock_ports": ports["aux_clock"],
            "reset_ports": ports["reset"], "sv_casts_rewritten": ncast, "fakeram": fr_info,
            "fakeram_port_issues": issues, "orfs_vars": orfs_vars, "changed_files": changed}
    _put(d / "prepare.json", json.dumps(info, indent=2))
    return info


# ---------------------------------------------------------------------------
# khối asic: chạy make, báo cáo, đọc log
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class StageResult:
    stage: str                     # synth | floorplan | place | ...
    variant: str
    period_ns: float
    ok: bool
    rc: int = 0
    seconds: float = 0.0
    error: str = ""
    make_tail: str = ""
    steps: list = dataclasses.field(default_factory=list)   # [{log, elapsed_s, peak_mb}] từ log ORFS
    worst_slack_ns: float | None = None      # mọi đường, kể cả I/O
    tns_ns: float | None = None
    internal_slack_ns: float | None = None   # không tính đường từ/tới cổng (dùng cho Fmax)
    fmax_mhz: float | None = None            # 1000 / (chu kỳ - internal_slack)
    crit_start: str = ""
    crit_end: str = ""
    regions: dict = dataclasses.field(default_factory=dict)  # {vùng endpoint: {"slack","start","end","count","neg"}}
    region_pairs: dict = dataclasses.field(default_factory=dict)  # {"bắt đầu->kết thúc": slack xấu nhất}
    n_paths: int = 0
    families: str = ""
    family_score: float | None = None
    endpoints: str = ""
    paths: str = ""
    std_area_um2: float | None = None
    macro_area_um2: float | None = None
    n_std: int | None = None
    n_macro: int | None = None
    core_area_um2: float | None = None
    counts: dict = dataclasses.field(default_factory=dict)  # async pins, sync cells, unclocked...
    noclock: str = ""   # "<nhóm instance> <số thanh ghi> <net clock mẫu>" mỗi dòng, xếp theo số giảm dần
    report_log: str = ""


_ELAPSED_RE = re.compile(r"Elapsed time:\s*([\d:.]+)\[h:\]min:sec.*?Peak memory:\s*(\d+)KB", re.S)


def _hms(s: str) -> float:
    parts = [float(x) for x in s.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_step_logs(log_dir: str) -> list[dict]:
    """[{log, elapsed_s, peak_mb}] từ các dòng `Elapsed time ... Peak memory` mà ORFS ghi cuối mỗi bước."""
    out = []
    for path in sorted(glob.glob(os.path.join(log_dir, "*.log"))):
        try:
            text = open(path, errors="replace").read()
        except OSError:
            continue
        m = None
        for m in _ELAPSED_RE.finditer(text):
            pass
        if m:
            out.append({"log": os.path.basename(path), "elapsed_s": round(_hms(m.group(1)), 1),
                        "peak_mb": round(int(m.group(2)) / 1024, 1)})
    return out


def _section(log: str, name: str) -> str:
    m = re.search(rf"@@{name}\n(.*?)(?=\n@@|\Z)", log, re.S)
    return m.group(1).strip() if m else ""


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


_PATH_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+(-?[\d.]+(?:e[-+]?\d+)?)\s*$")


def analyze_report(log: str, period_ns: float, r: StageResult) -> StageResult:
    log = log.replace("\\", "")
    r.report_log = log[-20000:]
    r.worst_slack_ns = _num(r"worst slack(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", _section(log, "WORST"))
    r.tns_ns = _num(r"tns(?:\s+max)?\s+(-?[\d.]+(?:e-?\d+)?)", _section(log, "TNS"))
    worst_int = None
    for line in _section(log, "PATHS_LIST").splitlines():
        m = _PATH_LINE_RE.match(line.strip())
        if not m:
            continue
        st, en, sl = m.group(1), m.group(2), float(m.group(3))
        r.n_paths += 1
        rs, re_ = S.region_of(st), S.region_of(en)
        key = "I/O" if "I/O" in (rs, re_) else re_      # vùng theo endpoint; đường chạm cổng tính riêng
        cur = r.regions.get(key)
        if cur is None or sl < cur["slack"]:
            r.regions[key] = {"slack": sl, "start": st, "end": en, "count": (cur or {}).get("count", 0),
                              "neg": (cur or {}).get("neg", 0)}
        r.regions[key]["count"] += 1
        r.regions[key]["neg"] += sl < 0
        k = f"{rs}->{re_}"
        r.region_pairs[k] = min(sl, r.region_pairs.get(k, sl))
        if "I/O" not in (rs, re_) and (worst_int is None or sl < worst_int[0]):
            worst_int = (sl, st, en)
    if worst_int:
        r.internal_slack_ns, r.crit_start, r.crit_end = worst_int
        if period_ns - worst_int[0] > 0:
            r.fmax_mhz = 1000.0 / (period_ns - worst_int[0])
    r.endpoints = _section(log, "ENDPOINTS")
    r.families = S.endpoint_families(r.endpoints, 25)
    r.family_score = S.family_score(r.endpoints) if r.endpoints else None
    r.paths = _section(log, "PATHS")[:60000]
    area = _section(log, "AREA")
    for key, attr in (("std", "std_area_um2"), ("macro", "macro_area_um2"), ("core", "core_area_um2")):
        v = _num(rf"{key}\s+(-?[\d.]+)", area)
        setattr(r, attr, v)
    n = _num(r"nstd\s+(\d+)", area)
    r.n_std = int(n) if n is not None else None
    n = _num(r"nmacro\s+(\d+)", area)
    r.n_macro = int(n) if n is not None else None
    for line in _section(log, "COUNTS").splitlines():
        p = line.split()
        if len(p) == 2 and re.fullmatch(r"-?\d+", p[1]):
            r.counts[p[0]] = int(p[1])
    rows = []
    for line in _section(log, "NOCLOCK_GROUPS").splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            rows.append((int(p[1]), p[0], " ".join(p[2:])))
    r.noclock = "\n".join(f"{n:6d}  {g}  {net}" for n, g, net in sorted(rows, reverse=True))
    errs = [l for l in log.splitlines() if l.startswith(("ERROR", "[ERROR"))]
    if errs:
        r.counts["report_errors"] = len(errs)
        r.error = (r.error + "\n" if r.error else "") + "\n".join(errs[:10])
    return r


def _run(cmd: list[str], cwd: str, timeout: int, log_path: str) -> tuple[int, float, str]:
    """Chạy trong process group riêng; quá giờ thì giết cả nhóm (make, yosys, openroad)."""
    t0 = time.time()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            rc = -1
    try:
        tail = open(log_path, errors="replace").read()[-8000:]
    except OSError:
        tail = ""
    return rc, time.time() - t0, tail


# Kết quả mà script báo cáo đọc ở từng bước (lấy file đầu tiên có).
STAGE_INPUTS = {
    "synth": (["1_synth.v", "1_2_yosys.v", "1_synth.odb"], ["1_synth.sdc"]),
    "floorplan": (["2_floorplan.odb"], ["2_floorplan.sdc", "1_synth.sdc"]),
    "place": (["3_place.odb", "3_5_place_dp.odb", "3_4_place_resized.odb"], ["3_place.sdc", "2_floorplan.sdc"]),
    "cts": (["4_cts.odb"], ["4_cts.sdc", "3_place.sdc"]),
    "route": (["5_route.odb"], ["5_route.sdc", "4_cts.sdc"]),
    "finish": (["6_final.odb"], ["6_final.sdc", "5_route.sdc"]),
}


@ChiaFunction(resources={"asic": 1}, max_retries=0)
def orfs_stage(config_mk: str, variant: str, stage: str, period_ns: float, timeout_s: int,
               report_timeout_s: int = 3600, nproc: int | None = None) -> StageResult:
    """`make <stage>` cho variant (bước trước đã xong thì make không chạy lại), rồi report.tcl trên kết quả của
    bước đó (sau place: RC ước lượng từ placement). Không raise: lỗi nằm trong StageResult.error."""
    flow = f"{C_ORFS}/flow"
    w = {k: f"{C_ORFS}/{v}" for k, v in work_dirs_rel(variant).items()}
    base = ["make", "-C", flow, f"DESIGN_CONFIG={config_mk}", f"FLOW_VARIANT={variant}"]
    if nproc:
        base.append(f"NUM_CORES={nproc}")
    r = StageResult(stage=stage, variant=variant, period_ns=period_ns, ok=False)
    rc, sec, tail = _run(base + [stage], flow, timeout_s, f"{w['logs']}/soc_fmax_make_{stage}.txt")
    r.rc, r.seconds, r.make_tail = rc, sec, tail[-6000:]
    r.steps = parse_step_logs(w["logs"])
    if rc != 0:
        r.error = f"make {stage}: " + ("quá giờ" if rc == -1 else f"rc={rc}") + f" sau {sec / 60:.1f} phút"
        return r
    db, sdc = STAGE_INPUTS[stage]
    db_path = next((f"{w['results']}/{x}" for x in db if os.path.exists(f"{w['results']}/{x}")), None)
    sdc_path = next((f"{w['results']}/{x}" for x in sdc if os.path.exists(f"{w['results']}/{x}")), None)
    if not db_path:
        r.error = f"không thấy kết quả {db} trong {w['results']}"
        return r
    env_vars = [f"SOCFMAX_DB={db_path}", f"SOCFMAX_SDC={sdc_path or ''}", f"SOCFMAX_STAGE={stage}",
                f"RUN_SCRIPT={os.path.dirname(config_mk)}/report.tcl", f"RUN_LOG_NAME_STEM=soc_fmax_report_{stage}"]
    rc2, sec2, _ = _run(base + env_vars + ["run"], flow, report_timeout_s, f"{w['logs']}/soc_fmax_report_{stage}.txt")
    rep = open(f"{w['logs']}/soc_fmax_report_{stage}.txt", errors="replace").read()
    analyze_report(rep, period_ns, r)
    if rc2 != 0 or "@@END" not in rep:
        r.error = (r.error + "\n" if r.error else "") + f"report.tcl: rc={rc2}, {'thiếu @@END' if '@@END' not in rep else ''}"
        r.error += "\n" + rep[-3000:]
        return r
    r.ok = r.internal_slack_ns is not None
    if not r.ok:
        r.error = "không đọc được slack từ báo cáo"
    return r
