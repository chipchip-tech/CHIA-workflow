"""Các ChiaFunction của minsoc-fmax (Mốc 1: baseline, Mốc 2: vòng agent). Mọi build chạy trên fork (đường dẫn suy từ $RISCV).

  chipyard   fork_state, load_test_elfs, elaborate_fpga (make verilog của flow fpga/)
  verilator  run_gate (qua VerilatorRunNode của CHIA, binary build bởi ChiselBuildNode trên khối chipyard)
  chipyard   (Mốc 2) fork_checkpoint, fork_scope, fork_revert, fork_commit: git trong fork + submodule
  vivado     vivado_impl (tcl của vivado_tcl.py)
  claude     read_text_claude; MinSocClaudeLLM (`claude --print`, có timeout thật)

Cache (exp.yaml) theo _chia_tag: tag baseline_* và m2_*.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass

from chia.base.ChiaFunction import ChiaFunction, get

import vivado_tcl as VT

SIM_CONFIG = "MinSocFmaxConfig"
SIM_FPGA_CONFIG = "MinSocFmaxFpgaConfig"   # Mốc 2: gate chạy kèm WithMinSocFpgaTweaks (giống bản Arty)
AGENT_BRANCH = "minsoc-fmax/agent"
# Agent chỉ được sửa: Scala nguồn của 3 submodule và file điểm gắn mixin của project.
AGENT_SCOPE = {"generators/rocket-chip": r"^src/main/scala/.*\.scala$",
               "generators/sifive-cache": r"^(design/craft/inclusivecache/)?src/.*\.scala$",
               "generators/testchipip": r"^src/main/scala/.*\.scala$"}
AGENT_OWN_OK = ("MinSocFmaxConfig.scala",)
LLM_PARTIAL_FILE = "/home/ray/minsoc_fmax_llm_partial.txt"
GIT_ID = "-c user.name=minsoc-agent -c user.email=minsoc-agent@localhost"
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
        for f in _untracked_scala(path):   # file .scala mới (agent tạo) cũng vào hash
            if os.path.basename(f) not in OWN_FILES:
                parts.append(f"### {d or '.'} untracked {f}\n" + open(os.path.join(path, f), errors="replace").read())
    own = {}
    for o in OWN_FILES:
        hits = glob.glob(f"{cy}/generators/chipyard/src/main/scala/config/{o}") + \
            glob.glob(f"{cy}/fpga/src/main/scala/arty100t/{o}")
        own[o] = open(hits[0]).read() if hits else None
    patch = "\n".join(parts)
    h = hashlib.sha256((patch + "".join(f"{k}{v}" for k, v in sorted(own.items()))).encode()).hexdigest()[:10]
    heads = sh("git rev-parse --short HEAD; for s in " + " ".join(SUBMODULES) +
               "; do echo \"$s $(git -C $s rev-parse --short HEAD)\"; done", cwd=cy)
    rev = hashlib.sha256(heads.encode()).hexdigest()[:8]   # commit hiện tại của fork + submodule (Mốc 2)
    return {"fork": cy, "patch": patch, "dirty": dirty, "hash": h, "heads": heads, "rev": rev,
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


def _drop_jar_without_fpga(cy: str) -> str:
    """sims/verilator và fpga/ dùng chung .classpath_cache/chipyard.jar. Nếu jar do project sbt `chipyard`
    build (thiếu class fpga_platforms) mà mới hơn nguồn Scala, make của fpga/ không build lại và
    chipyard.Generator báo `Option --top-module failed`. Xoá jar thiếu Arty100THarness để make build lại."""
    import zipfile
    jar = f"{cy}/.classpath_cache/chipyard.jar"
    if not os.path.exists(jar):
        return ""
    try:
        has = any("fpga/arty100t/Arty100THarness" in n for n in zipfile.ZipFile(jar).namelist())
    except zipfile.BadZipFile:
        has = False
    if has:
        return ""
    os.remove(jar)
    return f"xoá {jar} (thiếu class Arty100THarness)"


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def elaborate_fpga(config: str, jobs: int = 8, tries: int = 3) -> dict:
    """make verilog của flow fpga/ (Chisel -> Verilog + tcl IP + xdc), thử lại khi lỗi mạng khi tải thư viện."""
    cy = fork_dir()
    log = f"{cy}/fpga/minsoc-elab-{config}.log"
    t0, rc, notes = time.time(), None, []
    notes.append(_drop_jar_without_fpga(cy))
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
                extra_xdc: str = "", directives: list | None = None, incr_ref: str = "",
                stop_below: float | None = None) -> dict:
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
                      board_repo=_board_repo(), gen_xdcs=xdcs,
                      directives=directives, incr_ref=incr_ref, stop_below=stop_below)
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
    return {"ok": m["all_done"] and bool(impls or m["early"]), "error": err,
            "place_wns": m["place"], "incr": m["incr"], "early": m["early"],
            "incr_ref": incr_ref, "incr_ref_exists": bool(incr_ref) and os.path.exists(incr_ref), "run_dir": run_dir, "seconds": time.time() - t0,
            "part": m["part"], "board_part": m["board_part"], "synth_done": m["synth_done"],
            "bit": m["bit"], "bit_fail": m["bit_fail"], "impls": impls, "errors": errors,
            "synth": {**VT.parse_wns_tns(rd("timing_synth.rpt")), **VT.parse_util(rd("util_synth.rpt"))},
            "n_vsrcs": len(vsrcs), "ip_tcls": ip_tcls, "log_tail": log[-4000:]}


# ---------------------------------------------------------------------------
# Mốc 2: git trong fork (khối chipyard)
# ---------------------------------------------------------------------------

def _own_paths(cy: str) -> dict:
    out = {}
    for o in OWN_FILES:
        hits = glob.glob(f"{cy}/generators/chipyard/src/main/scala/config/{o}") + \
            glob.glob(f"{cy}/fpga/src/main/scala/arty100t/{o}")
        if hits:
            out[o] = hits[0]
    return out


def _untracked_scala(path: str) -> list:
    return [f for f in sh("git ls-files -o --exclude-standard", cwd=path).splitlines()
            if f.endswith(".scala") and not f.startswith("[rc=")]


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def fork_checkpoint() -> dict:
    """Điểm quay lui trước một lượt agent: HEAD từng repo, file .scala chưa track sẵn có, nội dung file Config."""
    cy = fork_dir()
    ck = {"fork": cy, "heads": {}, "untracked": {}, "own": {}}
    for d in ("",) + SUBMODULES:
        path = os.path.join(cy, d) if d else cy
        ck["heads"][d] = sh("git rev-parse HEAD", cwd=path, check=True).strip()
        ck["untracked"][d] = _untracked_scala(path)
    for o, p in _own_paths(cy).items():
        ck["own"][o] = open(p).read()
    return ck


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def fork_scope(ck: dict) -> dict:
    """File agent đã đổi so với checkpoint, chia trong/ngoài phạm vi cho phép (AGENT_SCOPE, AGENT_OWN_OK)."""
    cy, inside, outside = ck["fork"], [], []
    for d in ("",) + SUBMODULES:
        path = os.path.join(cy, d) if d else cy
        files = [f for f in sh(f"git diff --name-only --ignore-submodules=all {ck['heads'][d]}", cwd=path).splitlines()
                 if f and not f.startswith("[rc=")]
        files += [f for f in _untracked_scala(path) if f not in ck["untracked"][d]]
        own_rel = {os.path.relpath(p, cy) for p in _own_paths(cy).values()}
        for f in dict.fromkeys(files):
            full = f"{d}/{f}" if d else f
            if not d and full in own_rel:
                continue                                    # file Config: xét bên dưới theo nội dung
            ok = bool(d) and re.search(AGENT_SCOPE[d], f)
            (inside if ok else outside).append(full)
    for o, p in _own_paths(cy).items():
        if open(p).read() != ck["own"].get(o):
            (inside if o in AGENT_OWN_OK else outside).append(os.path.relpath(p, cy))
    return {"inside": inside, "outside": outside, "changed": bool(inside or outside)}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def fork_revert(ck: dict) -> dict:
    """Đưa fork về checkpoint: reset --hard từng repo, xoá .scala mới, ghi lại file Config. Không đụng file build."""
    cy, log = ck["fork"], []
    for d in ("",) + SUBMODULES:
        path = os.path.join(cy, d) if d else cy
        log.append(sh(f"git reset -q --hard {ck['heads'][d]}", cwd=path))
        for f in _untracked_scala(path):
            if f not in ck["untracked"][d] and os.path.basename(f) not in OWN_FILES:
                os.remove(os.path.join(path, f))
                log.append(f"xoá {d}/{f}")
    for o, p in _own_paths(cy).items():
        if o in ck["own"]:
            open(p, "w").write(ck["own"][o])
    left = [d or "." for d in ("",) + SUBMODULES
            if sh("git diff --name-only --ignore-submodules=all HEAD",
                  cwd=os.path.join(cy, d) if d else cy).strip()]
    return {"ok": not left, "left": left, "log": "\n".join(x for x in log if x.strip())}


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def fork_commit(message: str, ck: dict) -> dict:
    """Commit lượt được nhận lên nhánh AGENT_BRANCH: từng submodule có thay đổi, rồi fork gốc
    (con trỏ submodule + file Config của project, `add -f` vì có thể nằm trong .gitignore)."""
    cy, out = fork_dir(), {}
    msg = shlex.quote(message)
    for d in SUBMODULES:
        path = os.path.join(cy, d)
        new = " ".join(shlex.quote(f) for f in _untracked_scala(path) if f not in ck["untracked"][d])
        if sh("git diff --name-only HEAD", cwd=path).strip() or new:
            out[d] = sh(f"git checkout -q -B {AGENT_BRANCH} && git add -u" + (f" && git add -- {new}" if new else "") +
                        f" && git {GIT_ID} commit -q -m {msg} && git rev-parse --short HEAD", cwd=path, check=True)
    own = " ".join(shlex.quote(os.path.relpath(p, cy)) for p in _own_paths(cy).values())
    out["."] = sh(f"git checkout -q -B {AGENT_BRANCH} && git add -- {' '.join(SUBMODULES)} && git add -f -- {own} && "
                  f"(git diff --cached --quiet || git {GIT_ID} commit -q -m {msg}) && git rev-parse --short HEAD",
                  cwd=cy, check=True)
    return out


# ---------------------------------------------------------------------------
# khối claude: agent
# ---------------------------------------------------------------------------

def _read_local(path: str, remove: bool) -> str | None:
    if not os.path.isfile(path):
        return None
    text = open(path, errors="replace").read()
    if remove:
        os.remove(path)
    return text


@ChiaFunction(resources={"claude_creds": 0.01}, max_retries=0)
def read_text_claude(path: str, remove: bool = False) -> str | None:
    """File trên khối claude (transcript dở của MinSocClaudeLLM)."""
    return _read_local(path, remove)


def _kill(proc):
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


def make_llm_class():
    """Import trễ: chỉ driver và khối claude cần chia.models.claude."""
    from chia.models.claude import ClaudeCodeLLM, ClaudeCodeQueryResult, parse_rate_limit_event

    class MinSocClaudeLLM(ClaudeCodeLLM):
        """ClaudeCodeLLM (`claude --print`) như project rocket-fmax: bỏ hết tool riêng của Claude (container
        claude không có fork), chỉ dùng MCP tool vòng lặp đưa vào (chipyard_bash); `--effort`; giết tiến trình
        sau timeout_seconds và ghi transcript dở vào LLM_PARTIAL_FILE."""

        def __init__(self, *args, effort: str | None = None, **kwargs):
            kwargs.setdefault("log_stream", True)
            super().__init__(*args, **kwargs)
            extra = ["--tools", "", "--strict-mcp-config"] + (["--effort", effort] if effort else [])
            self.extra_cli_args = extra + list(self.extra_cli_args or [])

        def _run_claude_streaming(self, user_message, tools=None):
            cmd = self._build_cmd(tools)
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result_parts, stderr_parts = [], []
            stream_parts = [f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] claude {' '.join(cmd[1:4])}\n\n"]
            lock = threading.Lock()

            class _Tee:
                def write(self, s):
                    stream_parts.append(s)

                def flush(self):
                    pass

            tee = _Tee()
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=env, start_new_session=True)
            proc.stdin.write(user_message)
            proc.stdin.close()

            def drain_out():
                for line in proc.stdout:
                    if line.strip():
                        with lock:
                            self._process_event_line(line.strip(), tee, result_parts)

            def drain_err():
                for line in proc.stderr:
                    with lock:
                        stderr_parts.append(line)
                        stream_parts.append(f"[stderr] {line}")

            threads = [threading.Thread(target=drain_out), threading.Thread(target=drain_err)]
            for t in threads:
                t.start()
            try:
                proc.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                _kill(proc)
                for t in threads:
                    t.join(10)
                self._salvage(stream_parts, f"hết {self.timeout_seconds} s")
                raise
            for t in threads:
                t.join()
            result = "".join(result_parts)
            limited = (self._rate_limit_event is not None and parse_rate_limit_event(self._rate_limit_event)) \
                or "hit your limit" in result
            if proc.returncode != 0 or limited:
                self._salvage(stream_parts, "usage limit" if limited else f"exit {proc.returncode}")
            return ClaudeCodeQueryResult(result=result, returncode=proc.returncode, stderr="".join(stderr_parts),
                                         stream_result="".join(stream_parts))

        def _salvage(self, stream_parts, why):
            try:
                with open(LLM_PARTIAL_FILE, "w") as f:
                    f.write(f"[claude {why}]\n\n{''.join(stream_parts)}")
            except OSError:
                pass

    return MinSocClaudeLLM


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


# ---------------------------------------------------------------------------
# Pha ASIC, bước A: synth Yosys (ORFS) + STA không tải dây. SRAM (*.top.mems.v) để black-box.
# ---------------------------------------------------------------------------
ORFS = "/OpenROAD-flow-scripts"
ASIC_PLATFORMS = {
    "sky130hd": {"tech_lef": "lef/sky130_fd_sc_hd.tlef",
                 "sc_lef": "lef/sky130_fd_sc_hd_merged.lef",
                 "lib": "lib/sky130_fd_sc_hd__tt_025C_1v80.lib"},
    "nangate45": {"tech_lef": "lef/NangateOpenCellLibrary.tech.lef",
                  "sc_lef": "lef/NangateOpenCellLibrary.macro.mod.lef",
                  "lib": "lib/NangateOpenCellLibrary_typical.lib"},
}


def verilator_build_dir(cy: str, config: str) -> str:
    return f"{cy}/sims/verilator/generated-src/chipyard.harness.TestHarness.{config}"


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def asic_rtl_list(config: str) -> dict:
    """File RTL của ChipTop (từ bản build Verilator), bỏ file bộ nhớ hành vi; trả kèm nội dung mems.v để làm stub."""
    cy = fork_dir()
    d = verilator_build_dir(cy, config)
    if not os.path.isdir(d):
        raise RuntimeError(f"chưa có {d}: build Verilator {config} trước")
    base = os.path.basename(d)
    topf = f"{d}/{base}.top.f"
    mems = f"{d}/{base}.top.mems.v"
    if os.path.exists(topf):
        files = [ln.strip() for ln in open(topf) if ln.strip() and not ln.startswith("#")]
        files = [f if os.path.isabs(f) else os.path.join(d, f) for f in files]
        src = topf
    else:  # dự phòng: mọi .v/.sv trong gen-collateral trừ harness/testbench
        files = sorted(glob.glob(f"{d}/gen-collateral/*.v") + glob.glob(f"{d}/gen-collateral/*.sv"))
        files = [f for f in files if not re.search(r"(TestHarness|SimDRAM|SimSerial|SimJTAG|SimUART|Sim[A-Z]\w*\.|TestDriver)",
                                                   os.path.basename(f))]
        src = "gen-collateral (không có top.f)"
    files = [f for f in files if f.endswith((".v", ".sv")) and os.path.realpath(f) != os.path.realpath(mems)
             and "mems" not in os.path.basename(f)]
    missing = [f for f in files if not os.path.exists(f)]
    return {"dir": d, "source": src, "files": files, "missing": missing,
            "mems_v": open(mems).read() if os.path.exists(mems) else "",
            "listing": sh(f"ls {d} | head -40")}


def _bb_stubs(mems_v: str) -> tuple[str, list]:
    """Giữ phần khai báo cổng (ANSI) của mỗi module bộ nhớ, bỏ thân, gắn (* blackbox *)."""
    out, names = [], []
    for m in re.finditer(r"\bmodule\s+(\w+)\s*(#\s*\(.*?\)\s*)?\((.*?)\);", mems_v, re.S):
        names.append(m.group(1))
        out.append(f"(* blackbox *)\nmodule {m.group(1)}({m.group(3)});\nendmodule\n")
    return "\n".join(out), names


def _parse_sta(txt: str) -> dict:
    wns = re.search(r"worst slack\s+(-?[\d.]+)", txt)
    tns = re.search(r"\btns\s+(-?[\d.]+)", txt)
    clk = re.search(r"MINSOC_CLOCKS (.*)", txt)
    paths = []
    for blk in re.split(r"\n(?=Startpoint:)", txt)[1:]:
        s = re.search(r"Startpoint:\s*(\S+)", blk)
        e = re.search(r"Endpoint:\s*(\S+)", blk)
        sl = re.search(r"(-?[\d.]+)\s+slack", blk)
        dl = re.search(r"(-?[\d.]+)\s+data arrival time", blk)
        paths.append({"start": s and s.group(1), "end": e and e.group(1),
                      "slack": sl and float(sl.group(1)), "arrival": dl and float(dl.group(1))})
    return {"wns": wns and float(wns.group(1)), "tns": tns and float(tns.group(1)),
            "clocks": clk and clk.group(1), "paths": paths[:10]}


@ChiaFunction(resources={"asic": 1}, max_retries=0)
def asic_synth_sta(run: str, rtl: dict, period_ns: float, sdc: str, sta_tcl: str,
                   platform: str = "nangate45", top: str = "ChipTop", timeout: int = 4 * 3600) -> dict:
    t0 = time.time()
    plat = ASIC_PLATFORMS[platform]
    pdir = f"{ORFS}/flow/platforms/{platform}"
    nick = re.sub(r"\W", "_", f"minsoc_{run}")
    ddir = f"{ORFS}/flow/designs/{platform}/{nick}"
    os.makedirs(ddir, exist_ok=True)
    stubs, bb = _bb_stubs(rtl["mems_v"])
    open(f"{ddir}/mems_bb.v", "w").write(stubs)
    open(f"{ddir}/constraint.sdc", "w").write(sdc)
    open(f"{ddir}/sta.tcl", "w").write(sta_tcl)
    vfiles = " ".join(rtl["files"] + [f"{ddir}/mems_bb.v"])
    open(f"{ddir}/config.mk", "w").write("\n".join([
        f"export PLATFORM = {platform}", f"export DESIGN_NAME = {top}", f"export DESIGN_NICKNAME = {nick}",
        f"export VERILOG_FILES = {vfiles}", f"export SDC_FILE = {ddir}/constraint.sdc",
        f"export ABC_CLOCK_PERIOD_IN_PS = {int(period_ns * 1000)}", "export SYNTH_HIERARCHICAL = 0",
        "export CORE_UTILIZATION = 40", ""]))
    env = f"export MINSOC_PERIOD_NS={period_ns}; source {ORFS}/env.sh >/dev/null"
    mk = f"make -C {ORFS}/flow DESIGN_CONFIG={ddir}/config.mk"
    sh(f"{env}; {mk} clean_synth", timeout=600)
    synth = sh(f"{env}; {mk} synth", timeout=timeout)
    res = f"{ORFS}/flow/results/{platform}/{nick}/base"
    rep = f"{ORFS}/flow/reports/{platform}/{nick}/base"
    logs = f"{ORFS}/flow/logs/{platform}/{nick}/base"
    net = next((p for p in (f"{res}/1_synth.v", f"{res}/1_2_yosys.v", f"{res}/1_yosys.v") if os.path.exists(p)), None)
    out = {"ok": False, "platform": platform, "period_ns": period_ns, "design_dir": ddir, "results": res,
           "n_files": len(rtl["files"]), "blackboxes": bb, "synth_tail": synth[-4000:], "netlist": net,
           "synth_stat": sh(f"cat {rep}/synth_stat.txt 2>/dev/null | tail -60"),
           "yosys_errors": sh(f"grep -hiE 'error|blackbox' {logs}/1_*.log 2>/dev/null | head -40")}
    if not net:
        out["seconds"] = time.time() - t0
        return out
    sta_env = (f"{env}; export MINSOC_TECH_LEF={pdir}/{plat['tech_lef']} MINSOC_SC_LEF={pdir}/{plat['sc_lef']} "
               f"MINSOC_LIB={pdir}/{plat['lib']} MINSOC_NETLIST={net} MINSOC_STUBS={ddir}/mems_bb.v "
               f"MINSOC_TOP={top} MINSOC_SDC={ddir}/constraint.sdc")
    sta = sh(f"{sta_env}; openroad -exit -no_init {ddir}/sta.tcl", timeout=3600)
    open(f"{ddir}/sta.log", "w").write(sta)
    area = re.search(r"Chip area[^:]*:\s*([\d.]+)", out["synth_stat"])
    cells = re.search(r"Number of cells:\s*(\d+)", out["synth_stat"])
    out.update(_parse_sta(sta), sta_tail=sta[-6000:], area_um2=area and float(area.group(1)),
               cells=cells and int(cells.group(1)), seconds=time.time() - t0)
    out["ok"] = out["wns"] is not None
    return out
