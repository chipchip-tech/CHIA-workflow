"""Node CHIA của soc-fmax: khối chipyard (trạng thái cây git, ELF test), verilator (gate), tách RTL DigitalTop.

Chép có chọn lọc từ projects/rocket-fmax/fmax_nodes.py (nhánh project/rocket-fmax, commit 2b796c9):
load_test_elfs, GateResult/run_gate, tách module theo cây instance, họ endpoint (family_table/family_score),
provider cache/bypass. Khác rocket-fmax:
  - Chipyard gốc 1.15.0 mount từ host (~/tools/chipyard, cùng đường dẫn thật ở host và trong container),
    không phải /home/ray/chipyard của image;
  - top là DigitalTop (cả SoC), macro SRAM lấy từ *.top.mems.conf (macro không có định nghĩa Verilog cũng được);
  - trạng thái cây git tính trên MỌI submodule cấp một (agent sẽ được sửa cả SoC: rocket-chip, inclusive-cache,
    chipyard, testchipip, ...), không chỉ rocket-chip.

Mọi node truyền dữ liệu (str/bytes/dataclass), trừ Chipyard trên host được mount cùng đường dẫn vào khối
chipyard (ChiselBuildNode build tại chỗ).
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from chia.base.ChiaFunction import ChiaFunction, get

ISA_SUITES = ("rv64ui", "rv64um", "rv64ua", "rv64uf", "rv64ud")
BENCHMARKS = ("dhrystone", "median", "multiply", "qsort", "towers", "vvadd", "mm", "spmv")
SIM_WORK_DIR = "/home/ray/soc_fmax_sim"          # trên verilator_run
CONFIG = "MinimalSoCConfig"
TOP = "DigitalTop"
GATE_VERSION = "g1"                               # đổi khi đổi bộ test / cách chấm gate
# File chưa track được phép có trong cây Chipyard khi đo baseline (cài bằng scripts/install-chipyard-config.sh).
ALLOWED_UNTRACKED = (r"generators/chipyard/src/main/scala/config/MinimalSoCConfig\.scala",)
# Submodule ảnh hưởng tới build Verilator (Chisel/Scala, firtool, Verilog nguồn). Submodule khác (sims/firesim,
# software/*, toolchains/*, fpga/*, vlsi/*) chỉ được ghi lại, không tính vào kiểm "cây sạch" và digest của build:
# FireSim/FireMarshal sau build-setup.sh có thể để lại thay đổi không liên quan (15/11/2026: sims/firesim 1 dòng).
BUILD_SUBMODULE_RE = r"(generators|tools)/.*"

# EICG_wrapper (cổng clock kiểu latch + AND của rocket-chip) thay bằng cell ICG thật của sky130hd,
# để CTS thấy một clock gate chuẩn.
EICG_SKY130 = """module EICG_wrapper(output out, input en, input test_en, input in);
  sky130_fd_sc_hd__dlclkp_4 icg (.CLK(in), .GATE(en | test_en), .GCLK(out));
endmodule
"""


def _sh(cmd: str, cwd: str | None = None, timeout: int = 600, check: bool = True) -> str:
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise RuntimeError(f"`{cmd}` failed ({res.returncode}):\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    return res.stdout


# ---------------------------------------------------------------------------
# chipyard: trạng thái cây git, ELF test
# ---------------------------------------------------------------------------

def _file_sha(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return "-"


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def tree_state(cy: str) -> dict:
    """HEAD + thay đổi của repo Chipyard và mọi submodule cấp một.

    Trả {"root_head", "root_status": [dòng porcelain], "subs": {path: {"head", "dirty": số dòng, "diff": hash}},
    "untracked_sha": {file chưa track: sha}, "digest"}. `digest` đổi khi bất cứ thứ gì ảnh hưởng tới build đổi
    (dùng làm tag cache của build)."""
    root_head = _sh(f"git -C {shlex.quote(cy)} rev-parse HEAD").strip()
    status = _sh(f"git -C {shlex.quote(cy)} status --porcelain --ignore-submodules=all").splitlines()
    root_diff = _sh(f"git -C {shlex.quote(cy)} diff HEAD --ignore-submodules=all")
    cmd = ('echo "$sm_path $(git rev-parse HEAD) '
           '$(git status --porcelain --ignore-submodules=all | wc -l) '
           '$( (git status --porcelain --ignore-submodules=all; git diff HEAD --ignore-submodules=all) | sha1sum | cut -c1-16)"')
    subs = {}
    for line in _sh(f"git -C {shlex.quote(cy)} submodule foreach --quiet {shlex.quote(cmd)}", timeout=900).splitlines():
        parts = line.split()
        if len(parts) == 4:
            subs[parts[0]] = {"head": parts[1], "dirty": int(parts[2]), "diff": parts[3]}
    untracked = {l[3:]: _file_sha(os.path.join(cy, l[3:])) for l in status if l.startswith("?? ")}
    build_subs = {k: v for k, v in subs.items() if re.fullmatch(BUILD_SUBMODULE_RE, k)}
    blob = "\n".join([root_head, root_diff, *sorted(status), *sorted(f"{k} {v}" for k, v in untracked.items()),
                      *sorted(f"{k} {v['head']} {v['diff']}" for k, v in build_subs.items())])
    return {"root_head": root_head, "root_status": status, "subs": subs, "untracked_sha": untracked,
            "ignored_dirty": sorted(f"{k} ({v['dirty']} dòng)" for k, v in subs.items()
                                    if v["dirty"] and k not in build_subs),
            "digest": hashlib.sha256(blob.encode()).hexdigest()}


def dirty_paths(state: dict, allowed=ALLOWED_UNTRACKED) -> list[str]:
    """Những gì làm cây khác bản gốc (trừ file chưa track được phép)."""
    out = [l for l in state["root_status"] if not any(re.fullmatch(p, l[3:]) for p in allowed)]
    out += [f"{p} ({s['dirty']} dòng thay đổi)" for p, s in sorted(state["subs"].items())
            if s["dirty"] and re.fullmatch(BUILD_SUBMODULE_RE, p)]
    return out


@ChiaFunction(resources={"chipyard": 1})
def load_test_elfs(suites: tuple = ISA_SUITES, benches: tuple = BENCHMARKS) -> dict:
    """ELF của riscv-tests trong $RISCV của Chipyard (dạng bytes): ISA `<suite>-p-*` và các benchmark.

    Trả {"isa": {name: bytes}, "bench": {name: bytes}, "root": đường dẫn}."""
    riscv = os.environ.get("RISCV")
    if not riscv:
        raise RuntimeError("worker chipyard không có $RISCV (env.sh chưa được nạp?)")
    root = f"{riscv}/riscv64-unknown-elf/share/riscv-tests"
    isa = {}
    for name in sorted(os.listdir(f"{root}/isa")):
        if name.endswith(".dump") or not any(name.startswith(f"{s}-p-") for s in suites):
            continue
        with open(f"{root}/isa/{name}", "rb") as f:
            isa[name] = f.read()
    have = sorted(n[:-len(".riscv")] for n in os.listdir(f"{root}/benchmarks") if n.endswith(".riscv"))
    missing = [n for n in benches if n not in have]
    if missing:
        raise RuntimeError(f"thiếu benchmark {missing}; có sẵn: {have}")
    bench = {}
    for name in benches:
        with open(f"{root}/benchmarks/{name}.riscv", "rb") as f:
            bench[name] = f.read()
    return {"isa": isa, "bench": bench, "root": root}


# ---------------------------------------------------------------------------
# RTL của DigitalTop
# ---------------------------------------------------------------------------

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.M)
_INST_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+(?:#\s*\(.*?\)\s*)?([A-Za-z_]\w*)\s*\(", re.M | re.S)
# Chú thích vị trí nguồn của firtool: đổi khi dòng Scala dịch chỗ mà logic không đổi.
_LOC_RE = re.compile(r"[ \t]*//[ \t]*(?:@\[[^\]\n]*\]|[\w./-]+\.scala:\d+:\d+[^\n]*)")


def _split_modules(text: str) -> dict[str, str]:
    out = {}
    for m in _MODULE_RE.finditer(text):
        end = text.find("endmodule", m.end())
        out[m.group(1)] = text[m.start(): end + len("endmodule") if end >= 0 else len(text)]
    return out


@dataclass
class SocRTL:
    top: str
    modules: dict            # {module: text} mọi module dưới top (không gồm macro SRAM)
    srams: list              # tên macro SRAM (dòng mems.conf) có trong cây
    mems_conf: str           # các dòng mems.conf của `srams`
    children: dict           # {instance cấp một của top: module}
    replaced: list           # module thay bằng bản cho synth (EICG_wrapper)
    hash: str = ""           # sha256 của RTL đã bỏ chú thích vị trí (khoá "không đo lại")
    n_files: int = 0

    def verilog(self) -> str:
        """Toàn bộ RTL cho synth: các module + bản thay thế, một file."""
        body = [self.modules[m] for m in sorted(self.modules)]
        if "EICG_wrapper" in self.replaced:
            body.append(EICG_SKY130)
        return "\n\n".join(body) + "\n"


def rtl_hash(modules: dict, mems_conf: str, replaced: list) -> str:
    h = hashlib.sha256()
    for name in sorted(modules):
        h.update(name.encode() + b"\0" + _LOC_RE.sub("", modules[name]).encode() + b"\0")
    h.update(mems_conf.encode())
    h.update(",".join(replaced).encode())
    return h.hexdigest()


def soc_rtl_from_sources(generated_src_files: list, top: str = TOP) -> SocRTL:
    """Hàm thuần sau extract_soc_rtl (tách riêng để thử cục bộ)."""
    mems_conf, defined, n_files = "", {}, 0
    for name, text in generated_src_files:
        if name.endswith(".top.mems.conf"):
            mems_conf = text
        elif name.endswith((".v", ".sv")):
            n_files += 1
            for mod, mtext in _split_modules(text).items():
                defined.setdefault(mod, mtext)
    conf_lines = {l.split()[1]: l for l in mems_conf.splitlines() if l.startswith("name ")}
    if top not in defined:
        raise RuntimeError(f"không thấy module {top} trong {n_files} file sinh ra")
    seen, srams, todo = set(), set(), [top]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        for child, _inst in _INST_RE.findall(defined[mod]):
            if child in conf_lines:
                srams.add(child)
            elif child in defined and child not in seen and child != "EICG_wrapper":
                todo.append(child)
            elif child == "EICG_wrapper":
                seen.add(child)
    replaced = sorted(seen & {"EICG_wrapper"})
    modules = {m: defined[m] for m in sorted(seen - set(replaced))}
    children = {inst: child for child, inst in _INST_RE.findall(defined[top])
                if child in defined or child in conf_lines}
    conf = "\n".join(conf_lines[s] for s in sorted(srams))
    return SocRTL(top=top, modules=modules, srams=sorted(srams), mems_conf=conf, children=children,
                  replaced=replaced, hash=rtl_hash(modules, conf, replaced), n_files=n_files)


@ChiaFunction(max_retries=0)
def extract_soc_rtl(artifact, top: str = TOP) -> SocRTL:
    """Cây DigitalTop từ BuildArtifact build với collect_generated_src=True."""
    return soc_rtl_from_sources(artifact.generated_src_files, top)


def instance_tree(rtl: SocRTL, depth: int = 2) -> list[tuple[str, str]]:
    """[(đường instance, module)] tới độ sâu `depth` (để in và để chỉnh luật vùng)."""
    kids = {m: [(i, c) for c, i in _INST_RE.findall(t) if c in rtl.modules or c in rtl.srams]
            for m, t in rtl.modules.items()}
    out = []

    def walk(m, path, d):
        for inst, c in kids.get(m, []):
            p = f"{path}/{inst}" if path else inst
            out.append((p, c))
            if d > 1:
                walk(c, p, d - 1)
    walk(rtl.top, "", depth)
    return out


# ---------------------------------------------------------------------------
# Vùng (tile / L2 / bus / uncore / I/O) theo tên instance sau synth
# ---------------------------------------------------------------------------

# Luật theo instance cấp một của DigitalTop (Chipyard 1.15.0). Thứ tự quan trọng: luật đầu tiên khớp thắng.
# Chưa kiểm trên build thật: M1 in cây instance để chỉnh lại.
REGION_RULES = (
    ("tile", r"tile_prci_domain.*|.*rockettile.*|tile\b.*"),
    ("L2", r".*l2.*|.*inclusive_?cache.*|.*bank_?store.*"),
    ("bus", r"subsystem_[smcpfo]bus.*|.*_xbar.*|.*coupler.*|.*crossing.*"),
)


def norm_name(name: str) -> str:
    return name.replace("\\", "").replace(".", "/")


def region_of(name: str) -> str:
    """Vùng của một chân/cell (tên đầy đủ sau synth, "/" hoặc "."). Không có tầng nào = cổng I/O."""
    n = norm_name(name)
    if "/" not in n:
        return "I/O"
    first = n.split("/", 1)[0].lower()
    for reg, pat in REGION_RULES:
        if re.fullmatch(pat, first):
            return reg
    return "uncore"


# ---------------------------------------------------------------------------
# Họ endpoint (từ rocket-fmax): gom các bit của cùng một thanh ghi
# ---------------------------------------------------------------------------

_END_LINE_RE = re.compile(r"\s*(\S+)\s.*?(-?\d+\.\d+)\s*(?:\(VIOLATED\)|\(MET\))?\s*$")


def family_key(pin: str) -> str:
    key = norm_name(pin).rsplit("/", 1)[0]
    key = re.sub(r"\[\d+\]", "[*]", key)
    key = re.sub(r"_\d+(?=(_reg|\$_\w+_)?$)", "_*", key)
    return key


def family_table(endpoints: str) -> dict:
    """{họ: [slack xấu nhất, số endpoint, chân xấu nhất]} từ `report_checks -format end`."""
    fam: dict[str, list] = {}
    for line in endpoints.splitlines():
        m = _END_LINE_RE.match(line)
        if not m or m.group(1) in ("Endpoint", "---"):
            continue
        pin, slack = m.group(1), float(m.group(2))
        f = fam.setdefault(family_key(pin), [slack, 0, pin])
        f[1] += 1
        if slack < f[0]:
            f[0], f[2] = slack, pin
    return fam


def endpoint_families(endpoints: str, n: int = 20) -> str:
    rows = sorted(family_table(endpoints).items(), key=lambda kv: kv[1][0])[:n]
    out = [f"{'worst slack':>11}  {'bits':>5}  {'vùng':6}  họ thanh ghi (chỉ số bit -> [*]; endpoint xấu nhất)"]
    out += [f"{v[0]:11.3f}  {v[1]:5d}  {region_of(v[2]):6}  {k}  ({v[2]})" for k, v in rows]
    return "\n".join(out)


def family_score(endpoints: str, k: int = 8) -> float:
    """Tổng slack xấu nhất của k họ xấu nhất, mỗi họ cắt ở 0 (0 khi không có gì âm)."""
    worst = sorted(v[0] for v in family_table(endpoints).values())[:k]
    return sum(min(x, 0.0) for x in worst)


# ---------------------------------------------------------------------------
# Gate: riscv-tests ISA + benchmark trên Verilator (fan-out trên verilator_run)
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    isa: dict                    # {test: passed}
    bench: dict                  # {name: {"passed", "mcycle", "minstret", "sim_cycles"}}
    bench_repeat: dict           # lần chạy thứ hai với seed khác (chỉ baseline)
    fail_logs: dict              # {test: đuôi log/out} của test hỏng
    seconds: float = 0.0
    version: str = GATE_VERSION


def _bench_info(r) -> dict:
    mc = re.search(r"mcycle\s*=\s*(\d+)", r.log)
    mi = re.search(r"minstret\s*=\s*(\d+)", r.log)
    mc = mc or re.search(r"^C0:\s*(\d+)\s+cycles\s*$", r.log, re.M)        # mm in kiểu riêng
    mi = mi or re.search(r"^C0:\s*(\d+)\s+instructions\s*$", r.log, re.M)
    sc = re.search(r"after\s+(\d+)\s+simulation cycles", r.out + r.log)
    return {"passed": r.returncode == 0 and mc is not None,
            "mcycle": int(mc.group(1)) if mc else None,
            "minstret": int(mi.group(1)) if mi else None,
            "sim_cycles": int(sc.group(1)) if sc else None}


def _tail(r) -> str:
    return f"returncode={r.returncode}\n[log]\n{r.log[-1500:]}\n[out]\n{r.out[-1500:]}"


@ChiaFunction(num_cpus=0, max_retries=0)
def run_gate(artifact, elfs: dict, repeat_seed: int | None = None,
             isa_cycles: int = 5_000_000, bench_cycles: int = 200_000_000) -> GateResult:
    """Chạy mọi test ISA và benchmark trên simulator của `artifact`; các sim chạy song song tới số slot
    verilator_run. Với `repeat_seed`, benchmark chạy thêm lần nữa với +verilator+seed+<n> (kiểm tất định)."""
    import ray
    from chia.chipyard.verilator_run_node import VerilatorRunNode

    t0 = time.time()
    art = ray.put(artifact)
    sim = VerilatorRunNode()

    def launch(filename, elf, cycles, timeout, plusargs=None):
        return sim.run.chia_remote(sim, art, elf, filename, SIM_WORK_DIR, plusargs=plusargs or {},
                                   timeout_cycles=cycles, timeout_seconds=timeout, verbose=False)

    isa_refs = {n: launch(n, e, isa_cycles, 900) for n, e in elfs["isa"].items()}
    bench_refs = {n: launch(f"{n}.riscv", e, bench_cycles, 5400) for n, e in elfs["bench"].items()}
    rep_refs = ({n: launch(f"{n}.riscv", e, bench_cycles, 5400, {f"+verilator+seed+{repeat_seed}": ""})
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
    return GateResult(isa=isa, bench=bench, bench_repeat=rep, fail_logs=fails, seconds=time.time() - t0)


def cycle_cost(base: GateResult, g: GateResult) -> float | None:
    """Trung bình nhân mcycle / mcycle baseline trên các benchmark (1.0 = bằng baseline)."""
    import math
    r = [g.bench[n]["mcycle"] / base.bench[n]["mcycle"] for n in base.bench
         if g.bench.get(n, {}).get("mcycle") and base.bench[n]["mcycle"]]
    return math.exp(sum(map(math.log, r)) / len(r)) if r else None


# ---------------------------------------------------------------------------
# provider bypass (cấp module để pickle theo tham chiếu)
# ---------------------------------------------------------------------------

def cache_provider(tag, data_path, *args, **kwargs):
    from chia.base.cache import get_active_cache
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss cho tag {tag!r}")
    return value


def cache_hit_cond(tag, data_path, *args, **kwargs):
    from chia.base.cache import get_active_cache
    return bool(tag) and get(get_active_cache().has.chia_remote(tag))
