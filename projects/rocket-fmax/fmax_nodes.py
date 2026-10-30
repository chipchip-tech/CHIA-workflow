"""Nodes for the Rocket Fmax loop (projects/rocket-fmax).

Every node passes data (str/bytes/dataclass) and never file paths: the chipyard,
verilator, claude and vivado containers do not share a filesystem.

  chipyard (resource "chipyard"):  load_test_elfs, tree_snapshot, reset_rocket,
                                   collect_diff, restore_outside, stage_for_agent,
                                   read_text, ab_cleanup, find_scala_classes,
                                   read_source_lines, round_diff
  claude (resource "claude_creds"): read_text_claude (SalvageClaudeLLM runs there)
  any node (no resource):          extract_tile_rtl, run_gate (fans out to
                                   VerilatorRunNode on "verilator_run")
Vivado (synth_ab A/B, place + route) is in vivado_nodes.py.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass

from chia.base.ChiaFunction import ChiaFunction, get
from chia.models.claude import ClaudeCodeLLM, ClaudeCodeQueryResult, parse_rate_limit_event

CHIPYARD = "/home/ray/chipyard"
ROCKET = f"{CHIPYARD}/generators/rocket-chip"
ROCKET_REL = "generators/rocket-chip"
CONFIG = "RocketConfig"
TOP = "RocketTile"
STAGE_DIR = "/home/ray/rocket_fmax"          # on chipyard: what the agent may read
SIM_WORK_DIR = "/home/ray/rocket_fmax_sim"   # on verilator_run
# Written by the loop scripts and the A/B tool (never by an agent), removed after use.
AB_PGID_FILE = "/home/ray/rocket_fmax_ab.pgid"   # process group of the A/B tool's make verilog
LLM_PARTIAL_FILE = "/home/ray/rocket_fmax_llm_partial.txt"  # on claude: transcript of a cut-off run

ISA_SUITES = ("rv64ui", "rv64um", "rv64ua", "rv64uf", "rv64ud")
BENCHMARKS = ("dhrystone", "median", "multiply", "qsort", "towers", "vvadd")

# The agent may only change Scala under rocket-chip/src/main/scala, and never a
# file whose name says it holds configs (cache sizes, FPU on/off, ...).
EDITABLE_PREFIX = "src/main/scala/"
FORBIDDEN_FILE_RE = re.compile(r"Config", re.IGNORECASE)

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
def load_test_elfs(extra_bench: tuple = ()) -> dict:
    """riscv-tests ELFs shipped in the chipyard image, as bytes: BENCHMARKS + `extra_bench`.

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
    have = sorted(n[:-len(".riscv")] for n in os.listdir(f"{root}/benchmarks") if n.endswith(".riscv"))
    missing = [n for n in extra_bench if n not in have]
    if missing:
        raise RuntimeError(f"--extra-bench: không có {missing}; benchmark có sẵn: {have}")
    for name in (*BENCHMARKS, *extra_bench):
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


def _read_local(path: str, remove: bool) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, errors="replace") as f:
        text = f.read()
    if remove:
        os.remove(path)
    return text


@ChiaFunction(resources={"claude_creds": 0.01}, max_retries=0)
def read_text_claude(path: str, remove: bool = False) -> str | None:
    """A file on the claude worker (SalvageClaudeLLM's LLM_PARTIAL_FILE); `remove` deletes it."""
    return _read_local(path, remove)


def _kill(proc):
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


class SalvageClaudeLLM(ClaudeCodeLLM):
    """ClaudeCodeLLM (`claude --print`) for the loop.

    - Claude's own tools (Bash, Read, Edit, ...) would run in the claude container, which has no
      Chipyard: `--tools ""` removes them all and `--strict-mcp-config` loads only the MCP tools
      the loop passes (chipyard_bash, synth_ab).
    - `effort` is `--effort` (low/medium/high/xhigh/max); `max_turns` is `--max-turns` (claude
      then stops without a final answer).
    - The stock streaming run waits on `claude` with no time limit (timeout_seconds only applies
      without log_stream). Here it is killed after timeout_seconds, and the event stream of a
      run that timed out, failed or hit the usage limit is written to LLM_PARTIAL_FILE on the
      claude worker (prompt() itself then returns an empty result or raises RateLimitError);
      the loop reads it with read_text_claude."""

    def __init__(self, *args, effort: str | None = None, max_turns: int | None = None, **kwargs):
        kwargs.setdefault("log_stream", True)
        super().__init__(*args, **kwargs)
        extra = ["--tools", "", "--strict-mcp-config"]
        if effort:
            extra += ["--effort", effort]
        if max_turns:
            extra += ["--max-turns", str(max_turns)]
        self.extra_cli_args = extra + list(self.extra_cli_args or [])

    def _run_claude_streaming(self, user_message, tools=None):
        cmd = self._build_cmd(tools)
        self.logger.info("Running: %s", " ".join(cmd[:6]) + " ...")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result_parts: list[str] = []
        stderr_parts: list[str] = []
        stream_parts: list[str] = [f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] claude {' '.join(cmd[1:4])}\n\n"]
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
        if proc.returncode != 0:
            self.logger.warning("claude exited %d: %s", proc.returncode, "".join(stderr_parts)[-500:])
        return ClaudeCodeQueryResult(result=result, returncode=proc.returncode, stderr="".join(stderr_parts),
                                     stream_result="".join(stream_parts))

    def _salvage(self, stream_parts: list[str], why: str) -> None:
        try:
            with open(LLM_PARTIAL_FILE, "w") as f:
                f.write(f"[claude {why}]\n\n{''.join(stream_parts)}")
        except OSError as e:
            self.logger.warning("could not salvage claude run: %s", e)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def ab_cleanup() -> str:
    """After the A/B tool (ab_tool.py) is stopped: kill a `make verilog` it left
    running (its process group)."""
    out = []
    if os.path.exists(AB_PGID_FILE):
        pgid = open(AB_PGID_FILE).read().strip()
        try:
            if pgid.isdigit() and int(pgid) > 1:
                os.killpg(int(pgid), 9)
                out.append(f"killed make verilog (pgid {pgid})")
        except (ProcessLookupError, PermissionError):
            pass
        os.remove(AB_PGID_FILE)
    return "; ".join(out) or "nothing to clean"


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


def modules_of(files: dict) -> dict[str, str]:
    """{module: text} over several files (the first definition of a name wins)."""
    mods: dict[str, str] = {}
    for _, text in sorted(files.items()):
        for name, mtext in _split_modules(text).items():
            mods.setdefault(name, mtext)
    return mods


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
    blackboxes: dict        # {sram module: original text} (*_ext)
    stubs: str              # empty port-only modules of the blackboxes (unused since Yosys left)
    modules: list           # modules in the hierarchy (blackboxes included)
    replaced: list          # modules swapped for a synthesis stub (EICG_wrapper)
    mems_conf: str = ""
    sram_models: str = ""   # blackboxes + the modules they instantiate (split_*_ext), for Vivado P&R


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
    models, todo = [], sorted(blackboxes)
    while todo:              # an SRAM *_ext may instantiate split_*_ext pieces
        mod = todo.pop()
        if mod in models:
            continue
        models.append(mod)
        todo.extend(c for c in _INST_RE.findall(defined[mod][1]) if c in defined and c not in models)
    replaced = sorted(seen & {"EICG_wrapper"})
    keep_files = sorted({defined[m][0] for m in seen - srams - set(replaced)})
    out_files = {f: files[f] for f in keep_files}
    if replaced:
        out_files["EICG_wrapper_synth_stub.v"] = EICG_STUB
    return TileRTL(top=top, files=out_files, blackboxes=blackboxes,
                   stubs="\n".join(_port_stub(t) for t in blackboxes.values()),
                   modules=sorted(seen), replaced=replaced, mems_conf=mems_conf,
                   sram_models="\n\n".join(defined[m][1] for m in sorted(models)))


@ChiaFunction(max_retries=0)
def extract_tile_rtl(artifact) -> TileRTL:
    """RocketTile hierarchy (as text) from a BuildArtifact built with
    collect_generated_src=True."""
    return tile_rtl_from_sources(artifact.generated_src_files, TOP)


# ---------------------------------------------------------------------------
# instance paths: which module a flattened name like fpuOpt.dfma.fma.x_reg is in
# (the worst path located for the prompt)
# ---------------------------------------------------------------------------

_INST_PATH_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+(?:#\s*\(.*?\)\s*)?([A-Za-z_]\w*)\s*\(", re.M | re.S)


def instance_paths(rtl: TileRTL) -> dict[str, str]:
    """{instance path below rtl.top: module}; "" is rtl.top itself, "fpuOpt.dfma" the
    instance dfma inside instance fpuOpt. Black boxes are leaves."""
    mods = modules_of(rtl.files)
    known = set(mods) | set(rtl.blackboxes)
    kids = {m: [(i, c) for c, i in _INST_PATH_RE.findall(t) if c in known] for m, t in mods.items()}
    out: dict[str, str] = {}

    def walk(m, path):
        out[path] = m
        for inst, c in kids.get(m, []):
            walk(c, f"{path}.{inst}" if path else inst)

    walk(rtl.top, "")
    return out


def inst_prefix(name: str, paths: dict) -> str:
    """Longest instance path that a flattened cell/pin name starts with
    ("fpuOpt.dfma.in_in2[53]_reg" -> "fpuOpt.dfma"; a top-level register -> "")."""
    parts = name.lstrip("\\").split(".")
    best = ""
    for k in range(1, len(parts)):
        p = ".".join(parts[:k])
        if p not in paths:
            break
        best = p
    return best


def common_path(paths: list[str]) -> str:
    """Deepest instance path containing all of `paths` ("" = the top)."""
    if not paths:
        return ""
    split = [p.split(".") if p else [] for p in paths]
    out = []
    for parts in zip(*split):
        if any(x != parts[0] for x in parts):
            break
        out.append(parts[0])
    return ".".join(out)


def path_chain(path: str) -> list[str]:
    """"a.b.c" -> ["", "a", "a.b", "a.b.c"]."""
    parts = path.split(".") if path else []
    return [""] + [".".join(parts[:k]) for k in range(1, len(parts) + 1)]


def pick_target(rtl: TileRTL, synth) -> dict:
    """The smallest instance holding both ends of the worst path of `synth` (crit_start /
    crit_end as dotted names, see vivado_nodes.dotted), with its module and the chain above it."""
    paths = instance_paths(rtl)
    start, end = inst_prefix(synth.crit_start, paths), inst_prefix(synth.crit_end, paths)
    path = common_path([start, end])
    while path and paths[path] in rtl.blackboxes:
        path = path.rpartition(".")[0]
    return {"path": path, "module": paths[path], "start_path": start, "end_path": end,
            "chain": [(p, paths[p]) for p in path_chain(path)],
            "crit": f"{synth.crit_start} -> {synth.crit_end}"}


def changed_paths(rtl: TileRTL, modules: set) -> list[str]:
    """Instance paths of `rtl` whose module is in `modules`."""
    return sorted(p for p, m in instance_paths(rtl).items() if m in modules)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def find_scala_classes(modules: list[str]) -> dict:
    """{Verilog module: rocket-chip files under src/main/scala that define a class or
    object of that name}. Trailing _<part> pieces are dropped until one is found: a
    dedup suffix (_1) or a desiredName suffix (FPUFMAPipe_l4_f64 -> FPUFMAPipe)."""
    out = {}
    for m in modules:
        parts, files = m.split("_"), []
        while parts and not files:
            cls = "_".join(parts)
            r = subprocess.run(["grep", "-rlE", rf"\b(class|object)\s+{cls}\b", EDITABLE_PREFIX],
                               cwd=ROCKET, capture_output=True, text=True)
            files = sorted(r.stdout.split())[:5]
            parts.pop()
        out[m] = files
    return out


# ---------------------------------------------------------------------------
# the Chisel at the ends of the worst path, quoted in the prompt
# (lab-t2: the agent spent 24 of its 40 tool calls finding it)
# ---------------------------------------------------------------------------

_LOC_RE = re.compile(r"@\[([^\]]*)\]")
_LOC_ITEM_RE = re.compile(r"([\w./\-]+\.scala)?:(\d+):")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def source_locs(line: str) -> list[tuple[str, int]]:
    """Scala locations in the firtool `// @[...]` comments of a Verilog line:
    "@[a/FPU.scala:10:5, :12:7]" -> [("a/FPU.scala", 10), ("a/FPU.scala", 12)]."""
    out = []
    for m in _LOC_RE.finditer(line):
        cur = None
        for f, n in _LOC_ITEM_RE.findall(m.group(1)):
            cur = f or cur
            if cur:
                out.append((cur, int(n)))
    return out


def rocket_file(path: str) -> str | None:
    """A source path of a `@[...]` comment as a rocket-chip path (src/main/scala/...),
    or None when it is in another generator (hardfloat, ...)."""
    i = path.find(EDITABLE_PREFIX)
    if i < 0:
        return None
    head = path[:i]
    if head and not head.rstrip("/").endswith("rocket-chip"):
        return None
    return path[i:]


def reg_name(flat: str, inst: str) -> str:
    """Register of a dotted cell name: ("core.mem_reg_wdata_reg", "core") -> "mem_reg_wdata"."""
    name = flat.lstrip("\\")
    if inst and name.startswith(inst + "."):
        name = name[len(inst) + 1:]
    name = re.sub(r"_reg$", "", name)
    return re.sub(r"\[\d+\]", "", name)


def reg_statements(module_text: str, reg: str) -> list[str]:
    """Verilog lines that declare or assign `reg` (an assignment up to its `;` and the
    comment after it). A Vec element may not exist under its own name: "x_15" -> "x"."""
    names = [reg] + ([re.sub(r"_\d+$", "", reg)] if re.search(r"_\d+$", reg) else [])
    for name in names:
        n = re.escape(name)
        found = re.findall(rf"^[ \t]*reg\b[^;\n]*\b{n}\b[^;\n]*;[^\n]*", module_text, re.M)
        found += re.findall(rf"^[ \t]*{n}\b(?:\[[^\]]*\])?\s*<=[^;]*;[^\n]*", module_text, re.M)
        if found:
            return found
    return []


def path_end_locs(rtl: TileRTL, synth) -> list[dict]:
    """The worst path's start and end registers with the Scala lines firtool points to:
    [{"end": "start"|"end", "inst", "module", "reg", "locs": [(file, line)]}]."""
    paths = instance_paths(rtl)
    texts = modules_of(rtl.files)
    out = []
    for end, flat in (("start", synth.crit_start), ("end", synth.crit_end)):
        inst = inst_prefix(flat, paths)
        mod = paths.get(inst, "")
        reg = reg_name(flat, inst)
        locs = []
        for st in reg_statements(texts.get(mod, ""), reg):
            for f, n in source_locs(st):
                if (f, n) not in locs:
                    locs.append((f, n))
        out.append({"end": end, "inst": inst, "module": mod, "reg": reg, "locs": locs})
    return out


def patch_added_lines(patch: str) -> dict[str, set]:
    """{file: new-side line numbers of the `+` lines} of a git diff."""
    out: dict[str, set] = {}
    cur, n = None, 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            cur = line[4:].removeprefix("b/") if line[4:] != "/dev/null" else None
            continue
        if line.startswith("--- ") or line.startswith("diff "):
            continue
        m = _HUNK_RE.match(line)
        if m:
            n = int(m.group(1))
            continue
        if cur is None:
            continue
        if line.startswith("+"):
            out.setdefault(cur, set()).add(n)
            n += 1
        elif line.startswith(" "):
            n += 1
    return out


def source_spans(lines: list[int], context: int = 25) -> list[tuple[int, int]]:
    """Windows of +-context lines around `lines`, overlapping ones merged."""
    spans: list[list[int]] = []
    for n in sorted(set(lines)):
        lo, hi = max(1, n - context), n + context
        if spans and lo <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    return [tuple(s) for s in spans]


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def read_source_lines(spans: dict) -> dict:
    """{rocket-chip file: [(first, last)]} -> {file: {line number: text}} of the working tree."""
    out = {}
    for rel, ranges in spans.items():
        path = os.path.join(ROCKET, rel)
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as f:
            text = f.read().splitlines()
        out[rel] = {n: text[n - 1] for lo, hi in ranges for n in range(lo, min(hi, len(text)) + 1)}
    return out


def format_sources(ends: list[dict], lines: dict, added: dict, spans: dict, max_lines: int = 300) -> str:
    """The quoted Chisel: per file and window, numbered lines, `>` on a line the path's
    start/end register points to, `+` on a line the current best's patch added."""
    marks: dict[str, set] = {}
    for e in ends:
        for f, n in e["locs"]:
            rel = rocket_file(f)
            if rel:
                marks.setdefault(rel, set()).add(n)
    out, used = [], 0
    for rel, ranges in spans.items():
        got = lines.get(rel) or {}
        for lo, hi in ranges:
            nums = [n for n in range(lo, hi + 1) if n in got]
            if not nums or used >= max_lines:
                continue
            nums = nums[:max_lines - used]
            used += len(nums)
            out += [f"{rel} lines {nums[0]}-{nums[-1]}:", "", "```scala"]
            out += [f"{'>' if n in marks.get(rel, ()) else ' '}{n:5d}{'+' if n in added.get(rel, ()) else ' '} "
                    f"{got[n]}" for n in nums]
            out += ["```", ""]
    return "\n".join(out).rstrip()


def path_sources(rtl: TileRTL, synth, best_patch: str, context: int = 40) -> tuple[list[dict], str]:
    """(path_end_locs, the quoted Chisel around them) for the prompt of an edit session."""
    ends = path_end_locs(rtl, synth)
    by_file: dict[str, list[int]] = {}
    for e in ends:
        for f, n in e["locs"]:
            rel = rocket_file(f)
            if rel:
                by_file.setdefault(rel, []).append(n)
    if not by_file:
        return ends, ""
    spans = {rel: source_spans(ns, context) for rel, ns in by_file.items()}
    lines = get(read_source_lines.chia_remote(spans))
    return ends, format_sources(ends, lines, patch_added_lines(best_patch), spans)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def round_diff(rocket_head: str, base_patch: str) -> str:
    """Diff of the rocket-chip working tree against `rocket_head` + `base_patch`: the
    edits of this round only."""
    return _round_diff(rocket_head, base_patch)


def _round_diff(rocket_head: str, base_patch: str) -> str:
    """round_diff in the caller's process (also used by ab_tool in its actor). Uses a
    temporary index, so the tree and its index are left as they are."""
    fd, idx = tempfile.mkstemp(prefix="rocket_fmax_idx_")
    os.close(fd)
    os.remove(idx)
    env = {**os.environ, "GIT_INDEX_FILE": idx}

    def git(*args, check=True):
        r = subprocess.run(["git", "-C", ROCKET, *args], env=env, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr[-2000:]}")
        return r.stdout

    try:
        git("read-tree", rocket_head)
        if base_patch.strip():
            with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
                f.write(base_patch)
            try:
                git("apply", "--cached", "--whitespace=nowarn", f.name)
            finally:
                os.unlink(f.name)
        new = git("ls-files", "--others", "--exclude-standard", "--", EDITABLE_PREFIX).split()
        if new:
            git("add", "-N", "--", *new)
        return git("diff")
    finally:
        if os.path.exists(idx):
            os.remove(idx)


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
    # mm prints its own counters as "C0: <n> cycles" / "C0: <n> instructions"
    mc = mc or re.search(r"^C0:\s*(\d+)\s+cycles\s*$", r.log, re.M)
    mi = mi or re.search(r"^C0:\s*(\d+)\s+instructions\s*$", r.log, re.M)
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
