"""Các ChiaFunction của minsoc-fmax (Mốc 1: baseline). Mọi build chạy trên fork (đường dẫn suy từ $RISCV).

  chipyard   fork_state, load_test_elfs, elaborate_fpga (make verilog của flow fpga/)
  verilator  run_gate (qua VerilatorRunNode của CHIA, binary build bởi ChiselBuildNode trên khối chipyard)
  vivado     vivado_impl (tcl của vivado_tcl.py)

Cache (exp.yaml) theo _chia_tag: chỉ các tag baseline_*.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass

from chia.base.ChiaFunction import ChiaFunction, get

import vivado_tcl as VT

SIM_CONFIG = "MinSocFmaxConfig"
FPGA_CONFIGS = {10.0: "MinSocArty100TF100Config", 6.667: "MinSocArty100TF150Config", 5.0: "MinSocArty100TF200Config"}
BENCHMARKS = ("dhrystone", "median", "multiply", "qsort", "towers", "vvadd", "mm", "spmv")
OWN_FILES = ("MinSocFmaxConfig.scala", "MinSocArty100TConfigs.scala", "MinimalSoCConfig.scala")
SUBMODULES = ("generators/rocket-chip", "generators/sifive-cache", "generators/testchipip")
SIM_WORK_DIR = "/home/ray/minsoc_fmax_sim"


def sh(cmd: str, cwd: str | None = None, timeout: int = 600, check: bool = False) -> str:
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    out = (p.stdout + p.stderr).strip()
    if check and p.returncode != 0:
        raise RuntimeError(f"`{cmd[:200]}` rc={p.returncode}\n{out[-3000:]}")
    return out + ("" if p.returncode == 0 else f"\n[rc={p.returncode}]")


def fork_dir() -> str:
    riscv = os.environ.get("RISCV") or ""
    if not riscv:
        raise RuntimeError("RISCV chưa đặt: worker chưa nạp env.sh của fork")
    return os.path.realpath(os.path.join(riscv, "..", ".."))


def fpga_build_dir(cy: str, config: str) -> str:
    return f"{cy}/fpga/generated-src/chipyard.fpga.arty100t.Arty100THarness.{config}"


# ---------------------------------------------------------------------------
# khối chipyard
# ---------------------------------------------------------------------------

@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def fork_state() -> dict:
    """Diff (file đã track) của fork và các submodule agent được sửa, cộng nội dung Config của project.
    `hash` dùng trong tag cache; baseline yêu cầu diff rỗng."""
    cy = fork_dir()
    parts, dirty = [], []
    for d in ("",) + SUBMODULES:
        path = os.path.join(cy, d) if d else cy
        diff = sh("git diff --binary --ignore-submodules=all HEAD", cwd=path)
        if diff.strip():
            parts.append(f"### {d or '.'}\n{diff}")
            dirty.append(d or ".")
    own = {}
    for o in OWN_FILES:
        hits = glob.glob(f"{cy}/generators/chipyard/src/main/scala/config/{o}") + \
            glob.glob(f"{cy}/fpga/src/main/scala/arty100t/{o}")
        own[o] = open(hits[0]).read() if hits else None
    patch = "\n".join(parts)
    h = hashlib.sha256((patch + "".join(f"{k}{v}" for k, v in sorted(own.items()))).encode()).hexdigest()[:10]
    heads = sh("git rev-parse --short HEAD; for s in " + " ".join(SUBMODULES) +
               "; do echo \"$s $(git -C $s rev-parse --short HEAD)\"; done", cwd=cy)
    return {"fork": cy, "patch": patch, "dirty": dirty, "hash": h, "heads": heads,
            "own_missing": [k for k, v in own.items() if v is None]}


@ChiaFunction(resources={"chipyard": 1})
def load_test_elfs() -> dict:
    """riscv-tests của fork: mọi rv64*-p-* (không .dump) + 8 benchmark, dạng bytes."""
    root = f"{os.environ['RISCV']}/riscv64-unknown-elf/share/riscv-tests"
    isa = {}
    for p in sorted(glob.glob(f"{root}/isa/rv64*-p-*")):
        if not p.endswith(".dump"):
            isa[os.path.basename(p)] = open(p, "rb").read()
    bench = {b: open(f"{root}/benchmarks/{b}.riscv", "rb").read() for b in BENCHMARKS}
    return {"isa": isa, "bench": bench}


def _clean_coursier() -> str:
    """File khoá/tải dở của coursier làm sbt treo (gặp ở Bước 0)."""
    return sh("find /home/ray/.cache/coursier \\( -name '*.lock' -o -name '*.part' \\) -print -delete 2>/dev/null | head")


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def elaborate_fpga(config: str, jobs: int = 8, tries: int = 3) -> dict:
    """make verilog của flow fpga/ (Chisel -> Verilog + tcl IP + xdc), thử lại khi lỗi mạng khi tải thư viện."""
    cy = fork_dir()
    log = f"{cy}/fpga/minsoc-elab-{config}.log"
    t0, rc, notes = time.time(), None, []
    for i in range(tries):
        notes.append(_clean_coursier())
        out = sh(f"cd {cy}/fpga && make -j{jobs} SUB_PROJECT=arty100t CONFIG_PACKAGE=chipyard.fpga.arty100t "
                 f"CONFIG={config} verilog > {log} 2>&1 < /dev/null; echo RC=$?", timeout=5400)
        rc = out.strip().splitlines()[-1]
        if rc == "RC=0":
            break
        txt = open(log, errors="replace").read()
        if not re.search(r"Connection reset|download error|ResolveException|timed out", txt):
            break
        time.sleep(15)
    g = fpga_build_dir(cy, config)
    return {"ok": rc == "RC=0", "rc": rc, "tries": i + 1, "seconds": time.time() - t0, "log": log,
            "tail": sh(f"tail -30 {log}"), "build_dir": g, "coursier_cleanup": [n for n in notes if n]}


# ---------------------------------------------------------------------------
# khối verilator: gate
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    isa: dict             # {test: passed}
    bench: dict           # {name: {"passed", "mcycle", "minstret", "sim_cycles"}}
    bench_repeat: dict    # như trên, chạy lại với seed khác (kiểm tính tất định)
    fail_logs: dict       # {test: đuôi log}
    seconds: float = 0.0


def _bench_info(r) -> dict:
    mc = re.search(r"mcycle\s*=\s*(\d+)", r.log) or re.search(r"^C0:\s*(\d+)\s+cycles\s*$", r.log, re.M)
    mi = re.search(r"minstret\s*=\s*(\d+)", r.log) or re.search(r"^C0:\s*(\d+)\s+instructions\s*$", r.log, re.M)
    sc = re.search(r"after\s+(\d+)\s+simulation cycles", r.out + r.log)
    return {"passed": r.returncode == 0 and mc is not None,
            "mcycle": int(mc.group(1)) if mc else None,
            "minstret": int(mi.group(1)) if mi else None,
            "sim_cycles": int(sc.group(1)) if sc else None}


def _tail(r) -> str:
    return f"returncode={r.returncode}\n[log]\n{r.log[-1500:]}\n[out]\n{r.out[-1500:]}"


@ChiaFunction(num_cpus=0, max_retries=0)
def run_gate(artifact, elfs: dict, repeat_seed: int | None = 7,
             isa_cycles: int = 5_000_000, bench_cycles: int = 200_000_000) -> GateResult:
    """Mọi ISA test và benchmark trên simulator của `artifact` (SoC MinSocFmaxConfig).
    `repeat_seed`: chạy lại benchmark với +verilator+seed+<n>, mcycle phải giống."""
    import ray
    from chia.chipyard.verilator_run_node import VerilatorRunNode

    t0 = time.time()
    art = ray.put(artifact)
    sim = VerilatorRunNode()

    def launch(filename, elf, cycles, timeout, plusargs=None):
        return sim.run.chia_remote(sim, art, elf, filename, SIM_WORK_DIR, plusargs=plusargs or {},
                                   timeout_cycles=cycles, timeout_seconds=timeout, verbose=False)

    isa_refs = {n: launch(n, e, isa_cycles, 900) for n, e in elfs["isa"].items()}
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
    return GateResult(isa=isa, bench=bench, bench_repeat=rep, fail_logs=fails, seconds=time.time() - t0)


# ---------------------------------------------------------------------------
# khối vivado
# ---------------------------------------------------------------------------

def _board_repo() -> str:
    xv = os.environ.get("XILINX_VIVADO", "")
    root = os.path.realpath(os.path.join(xv, "..", "..")) if xv else ""
    for c in (f"{root}/vivado-boards/new/board_files", f"{root}/vivado-boards/board_files"):
        if glob.glob(f"{c}/arty-a7-100/*/board.xml"):
            return c
    return ""


@ChiaFunction(resources={"vivado": 1}, max_retries=0)
def vivado_impl(config: str, run_name: str, simdram_fpga_v: str, fpga_mem: str = "bram",
                n_impl: int = 3, write_bit: bool = True, timeout_s: int = 6 * 3600,
                extra_xdc: str = "") -> dict:
    """Synth một lần + n_impl lần place/route của Arty100THarness.<config> (đã elaborate).
    Kết quả ở <build_dir>/minsoc-vivado/<run_name>/obj; trả về số đo và nội dung report."""
    cy = fork_dir()
    g = fpga_build_dir(cy, config)
    run_dir = f"{g}/minsoc-vivado/{run_name}"
    os.makedirs(run_dir, exist_ok=True)
    common = f"{g}/sim_files.common.f"
    if not os.path.exists(common):
        return {"ok": False, "error": f"không thấy {common}: chưa elaborate {config}"}
    extra = [f"{cy}/generators/sifive-blocks/vsrc/SRLatch.v"]
    if fpga_mem == "bram":
        p = f"{run_dir}/SimDRAM_fpga.v"
        open(p, "w").write(simdram_fpga_v)
        extra.append(p)
    vsrcs = VT.vsrc_list(open(common).read().splitlines(), extra, fpga_mem)
    vsrcs_f = f"{run_dir}/vsrcs.f"
    open(vsrcs_f, "w").write("\n".join(vsrcs) + "\n")
    ip_tcls = sorted(glob.glob(f"{g}/*.vivado.tcl"))
    xdcs = sorted(glob.glob(f"{g}/*.xdc"))
    if extra_xdc:  # nạp sau shell.xdc: ràng buộc trực tiếp của project (vd. chân reset)
        px = f"{run_dir}/minsoc_extra.xdc"
        open(px, "w").write(extra_xdc)
        xdcs.append(px)
    tcl = VT.make_tcl(shells_tcl_dir=f"{cy}/fpga/fpga-shells/xilinx/common/tcl", vsrcs_f=vsrcs_f,
                      ip_tcls=ip_tcls, run_dir=run_dir, n_impl=n_impl, write_bit=write_bit,
                      board_repo=_board_repo(), gen_xdcs=xdcs)
    open(f"{run_dir}/run.tcl", "w").write(tcl)
    t0 = time.time()
    try:
        sh(f"cd {run_dir} && vivado -mode batch -nojournal -log vivado.log -source run.tcl > stdout.txt 2>&1",
           timeout=timeout_s)
        err = None
    except subprocess.TimeoutExpired:
        err = f"timeout {timeout_s}s"
    log = open(f"{run_dir}/vivado.log", errors="replace").read() if os.path.exists(f"{run_dir}/vivado.log") else ""
    m = VT.parse_markers(log)

    def rd(name):
        p = f"{run_dir}/obj/{name}"
        return open(p, errors="replace").read() if os.path.exists(p) else ""

    impls = {}
    for tag, directive in m["impl"].items():
        ts = rd(f"timing_{tag}.rpt")
        clk = VT.dut_clock(m["clk"].get(tag, []))
        impls[tag] = {"directive": directive, **VT.parse_wns_tns(ts), **VT.parse_util(rd(f"util_{tag}.rpt")),
                      "dut_clock": clk, "fmax_mhz": VT.fmax_mhz(clk["period"], clk["slack"]) if clk else None,
                      "paths": m["paths"].get(tag, []),
                      "reports": {k: rd(f"{k}_{tag}.rpt") for k in ("timing", "paths", "util", "util_hier", "clocks", "drc")}}
    errors = [l for l in log.splitlines() if l.startswith(("ERROR", "CRITICAL WARNING"))][:60]
    return {"ok": m["all_done"] and bool(impls), "error": err, "run_dir": run_dir, "seconds": time.time() - t0,
            "part": m["part"], "board_part": m["board_part"], "synth_done": m["synth_done"],
            "bit": m["bit"], "bit_fail": m["bit_fail"], "impls": impls, "errors": errors,
            "synth": {**VT.parse_wns_tns(rd("timing_synth.rpt")), **VT.parse_util(rd("util_synth.rpt"))},
            "n_vsrcs": len(vsrcs), "ip_tcls": ip_tcls, "log_tail": log[-4000:]}


# ---------------------------------------------------------------------------
# bypass providers (module level để pickle theo tham chiếu)
# ---------------------------------------------------------------------------

def cache_provider(tag, data_path, *args, **kwargs):
    from chia.base.cache import get_active_cache
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss for tag {tag!r}")
    return value


def cache_hit_cond(tag, data_path, *args, **kwargs):
    from chia.base.cache import get_active_cache
    return get(get_active_cache().has.chia_remote(tag))
