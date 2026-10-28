"""Nodes for the Rocket Fmax loop (projects/rocket-fmax).

Every node passes data (str/bytes/dataclass) and never file paths: the chipyard,
verilator, eda and opencode containers do not share a filesystem.

  chipyard (resource "chipyard"):  load_test_elfs, tree_snapshot, reset_rocket,
                                   collect_diff, restore_outside, apply_edit,
                                   stage_for_agent, ab_cleanup,
                                   find_scala_classes, round_diff
  any node (no resource):          extract_tile_rtl, run_gate (fans out to
                                   VerilatorRunNode on "verilator_run")
  eda (resource "eda"):            synth_sta (Yosys + OpenSTA, sky130hd tt),
                                   synth_area (ChipTop, hierarchical), equiv_formal
  chipyard, after the loop:        equiv_sim (Verilator lockstep), audit config file
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

from types import SimpleNamespace

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.tools.BashTool import BashTool
from chia.models.opencode import OpenCodeLLM, parse_session_id

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
# Written by the loop scripts and the A/B tool (never by an agent), removed after use.
AUDIT_CONFIG_FILE = f"{CHIPYARD}/generators/chipyard/src/main/scala/config/RocketFmaxAudit.scala"
AB_PGID_FILE = "/home/ray/rocket_fmax_ab.pgid"   # process group of the A/B tool's make verilog
LLM_PARTIAL_FILE = "/home/ray/rocket_fmax_llm_partial.txt"  # on opencode: transcript of a timed-out run

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


@ChiaFunction(resources={"opencode_creds": 1}, max_retries=0)
def read_text_opencode(path: str, remove: bool = False) -> str | None:
    """Same as read_text on the opencode worker: opencode's built-in bash runs there, so an
    agent that used it instead of chipyard_bash left its output there. `remove` deletes it."""
    if not os.path.isfile(path):
        return None
    with open(path, errors="replace") as f:
        text = f.read()
    if remove:
        os.remove(path)
    return text


class SalvageOpenCodeLLM(OpenCodeLLM):
    """OpenCodeLLM that keeps the transcript of a run cut off by timeout_seconds.

    The stock prompt() turns a timeout into an empty result, so nothing shows what the
    agent did. Here a timed-out `opencode run` is exported (or its raw event stream kept)
    to LLM_PARTIAL_FILE on the opencode worker; the loop reads it with read_text_opencode.

    builtin_tools picks what is left of opencode's own tools (bash, read, edit, ...). They run in
    the opencode container, which has no Chipyard (fmax-ab1: 142 such bash calls hunting for the
    workspace), but switching them all off ("off") makes opencode's free models answer "Free usage
    limit reached" at once (probe-tools, 27/10/2026: off failed, on worked, same model, minutes
    apart). So:
      "on"       all of them (m3/m4);
      "off"      none, only the MCP tools;
      "deny"     all listed, but permission bash/edit/webfetch = deny;
      "readonly" only read/grep/glob/list/lsp (no bash, edit, write, web, subagents).

    A provider error ("stream error" in opencode's log) does not end `opencode run`: it hung
    until the timeout (fmax-ab-t1: 45 min). Its log (INFO, which also logs every agent step)
    therefore always goes to stderr, and a run whose last log line is that error, with nothing
    after it for ERROR_GRACE s, is killed and returned as failed.

    max_steps sets the agent's `steps` in opencode's config: after that many steps the model is
    told to stop calling tools and answer in text (--loop-ab edit sessions, see BudgetBashTool)."""

    BUILTIN_TOOLS = ("bash", "read", "edit", "write", "patch", "multiedit", "grep", "glob", "list",
                     "lsp", "task", "webfetch", "websearch", "codesearch")
    READONLY_TOOLS = ("read", "grep", "glob", "list", "lsp")
    MODES = ("on", "off", "deny", "readonly")
    ERROR_GRACE = 60

    def __init__(self, *args, builtin_tools: bool | str = True, max_steps: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        mode = {True: "on", False: "off"}.get(builtin_tools, builtin_tools)
        if mode not in self.MODES:
            raise ValueError(f"builtin_tools must be one of {self.MODES}, not {builtin_tools!r}")
        self.builtin_tools = mode
        self.max_steps = max_steps     # opencode agent `steps`: after that many, a text-only answer
        if "--print-logs" not in (self.extra_cli_args or []):
            self.extra_cli_args = list(self.extra_cli_args or []) + ["--print-logs", "--log-level", "INFO"]

    def _build_config(self, tools):
        cfg = super()._build_config(tools)
        mode = self.builtin_tools
        if mode in ("off", "readonly"):
            keep = self.READONLY_TOOLS if mode == "readonly" else ()
            sel = {t: t in keep for t in self.BUILTIN_TOOLS}
            cfg["tools"] = sel
            cfg["agent"][self.agent_name]["tools"] = dict(sel)
        if mode != "on":
            cfg["permission"] = {**cfg.get("permission", {}), "bash": "deny", "edit": "deny", "webfetch": "deny"}
        if self.max_steps:
            cfg["agent"][self.agent_name]["steps"] = self.max_steps
        return cfg

    def _capture(self, cmd, env):
        if cmd[1:2] != ["run"]:
            return super()._capture(cmd, env)
        fd, out_path = tempfile.mkstemp(prefix="opencode_out_", suffix=".out")
        efd, err_path = tempfile.mkstemp(prefix="opencode_err_", suffix=".log")
        try:
            with os.fdopen(fd, "w") as out_fh, os.fdopen(efd, "w") as err_fh:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh,
                                        text=True, env=env, start_new_session=True)
                rc, t0, last_size, last_change = None, time.time(), -1, time.time()
                while rc is None:
                    try:
                        rc = proc.wait(timeout=5)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    now = time.time()
                    size = os.path.getsize(err_path)
                    if size != last_size:
                        last_size, last_change = size, now
                    if now - t0 > self.timeout_seconds:
                        self._kill(proc)
                        self._salvage(out_path, env, self._read(err_path))
                        raise subprocess.TimeoutExpired(cmd, self.timeout_seconds)
                    if now - last_change > self.ERROR_GRACE:
                        err = self._read(err_path)
                        tail = err.rstrip().rsplit("\n", 1)[-1]
                        if "level=ERROR" in tail and "stream error" in tail:
                            self._kill(proc)
                            self.logger.warning("opencode: provider error, killed after %d s silence: %s",
                                                self.ERROR_GRACE, self._last_error(err))
                            self._salvage(out_path, env, err, why="lỗi provider")
                            rc = -9
            with open(out_path) as f:
                stdout = f.read()
            return SimpleNamespace(returncode=rc, stdout=stdout, stderr=self._read(err_path))
        finally:
            for path in (out_path, err_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()

    @staticmethod
    def _read(path: str) -> str:
        with open(path, errors="replace") as f:
            return f.read()

    @staticmethod
    def _last_error(err: str) -> str:
        lines = [ln for ln in err.splitlines() if "level=ERROR" in ln]
        m = re.search(r'error\.error="([^"]*)"', lines[-1]) if lines else None
        return m.group(1) if m else (lines[-1][-300:] if lines else "")

    def _salvage(self, out_path: str, env: dict, stderr=None, why: str | None = None) -> None:
        try:
            with open(out_path, errors="replace") as f:
                raw = f.read()
            sid = parse_session_id(raw)
            stream = ""
            if sid:
                try:
                    stream = self._extract_from_export(self._run_export(sid, env))[2]
                except Exception as e:                               # noqa: BLE001
                    self.logger.warning("export of %s failed: %s", sid, e)
            with open(LLM_PARTIAL_FILE, "w") as f:
                head = why or f"hết {self.timeout_seconds} s"
                f.write(f"[opencode run {head}, session {sid}]\n\n{stream or raw}")
                if stderr:   # empty stdout (fmax-ab-t1: no event in 45 min) -> stderr is all there is
                    err = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
                    f.write(f"\n\n----- stderr (cuối) -----\n{err[-20000:]}")
        except Exception as e:                                       # noqa: BLE001
            self.logger.warning("could not salvage timed-out run: %s", e)


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def ab_cleanup() -> str:
    """After the A/B tool (ab_tool.py) is stopped: kill a `make verilog` it left
    running (its process group) and remove the config file written for it."""
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
    if os.path.exists(AUDIT_CONFIG_FILE):
        os.remove(AUDIT_CONFIG_FILE)
        out.append(f"removed {AUDIT_CONFIG_FILE}")
    return "; ".join(out) or "nothing to clean"


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


# ---------------------------------------------------------------------------
# instance paths: which module a flattened name like fpuOpt.dfma.fma.x_reg is in
# (--loop-ab picks the target module and the A/B module from them)
# ---------------------------------------------------------------------------

_INST_PATH_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+(?:#\s*\(.*?\)\s*)?([A-Za-z_]\w*)\s*\(", re.M | re.S)


def instance_paths(rtl: TileRTL) -> dict[str, str]:
    """{instance path below rtl.top: module}; "" is rtl.top itself, "fpuOpt.dfma" the
    instance dfma inside instance fpuOpt. Black boxes are leaves."""
    mods = E.modules_of(rtl.files)
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
    """Target of a --loop-ab round: the smallest instance holding both ends of the
    worst path of `synth`, with its module and the chain of modules above it."""
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
# --loop-ab: the Chisel at the ends of the worst path, quoted in the prompt
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
    """Register of a flattened Yosys name: ("fpuOpt.dfma.in_in2[53]_reg", "fpuOpt.dfma") -> "in_in2"."""
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
    texts = E.modules_of(rtl.files)
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


# ---------------------------------------------------------------------------
# --loop-ab: chipyard_bash with an edit budget
# ---------------------------------------------------------------------------

_HEREDOC_RE = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n(.*?)^[ \t]*\2[ \t]*$", re.M | re.S)
_INLINE_RE = re.compile(r"\b(python3?|bash|sh)\s+-c\s+(['\"])(.*?)(?<!\\)\2", re.S)
_WORK_RE = re.compile(r"\b(sed|grep|rg|git|nl|awk|perl|find|ls|cat|wc|diff|patch|cp|mv|rm|echo|printf|tee|"
                      r"head|tail|sort|uniq|make|sbt|mill)\b")
_PY_EFFECT_RE = re.compile(r"\b(print|open|write|subprocess|os|sys|shutil|Path|exec|eval)\b")


def _empty_script(body: str, python: bool) -> bool:
    """Nothing but `pass`, comments and blank lines; for Python also: no output, no file
    written (lab-t2: "w = 107 / # try ... / pass")."""
    keep = [ln.strip() for ln in body.replace("\\n", "\n").splitlines()]
    keep = [ln for ln in keep if ln and ln != "pass" and not ln.startswith(("#", "//"))]
    return not keep or (python and not _PY_EFFECT_RE.search("\n".join(keep)))


def noop_command(cmd: str) -> bool:
    """A command whose scripts (heredocs, `python3 -c ...`) do nothing (empty_script), and
    which does nothing else (lab-t2: 16 of its 40 calls were such notes)."""
    scripts = []
    for m in _HEREDOC_RE.finditer(cmd):
        lead = re.split(r"[\n;|&]", cmd[:m.start()])[-1]
        scripts.append((m.group(3), "python" in lead and ">" not in lead))
    scripts += [(m.group(3), m.group(1).startswith("python")) for m in _INLINE_RE.finditer(cmd)]
    if not scripts or not all(_empty_script(b, py) for b, py in scripts):
        return False
    rest = _INLINE_RE.sub(" ", _HEREDOC_RE.sub(" ", cmd))
    rest = re.sub(r"\bcat\s*>>?\s*\S+", " ", rest)                     # writing the empty heredoc
    rest = re.sub(r"\|\s*(tail|head)\b[^|;&]*", " ", rest)              # and trimming its output
    rest = re.sub(r"\S*/\S*", " ", rest)                                # paths (".../scala/tile")
    return not _WORK_RE.search(rest)


def tree_hash() -> str:
    """Hash of the rocket-chip edits (tracked diff + new files under src/main/scala)."""
    d = subprocess.run(f"cd {ROCKET} && git diff HEAD && git ls-files --others --exclude-standard -z "
                       f"-- {EDITABLE_PREFIX} | xargs -0 -r sha1sum", shell=True, capture_output=True,
                       text=True).stdout
    return hashlib.sha256(d.encode()).hexdigest()[:12]


class BudgetBashTool(BashTool):
    """chipyard_bash for a --loop-ab edit session, which must end with an edit (lab-t1, lab-t2:
    40 calls, no edit). Calls are counted; while the tree has no edit of this session:
      - a call with only empty scripts (noop_command) is not run;
      - after `soft` calls every answer carries a budget note;
      - after `read_only` calls the output of a command that changed no file is withheld
        (a script written to /tmp and run in a later call still works);
      - after `hard` calls nothing is run: the agent is told to stop and answer with its plan.
    An edit lifts all of it (undoing the edit puts it back). Each call goes to `log_file` (jsonl)."""

    def __init__(self, name: str, work_dir: str, soft: int, read_only: int, hard: int, log_file: str,
                 timeout_seconds: int = 120, task_options: dict | None = None):
        # Set before BashTool.__init__: its __post_init__ pickles this object into the actor.
        self.soft, self.read_only, self.hard, self.log_file = soft, read_only, hard, log_file
        self._n, self._start, self._edited = 0, None, False
        self._lock = threading.Lock()
        super().__init__(name, work_dir, timeout_seconds=timeout_seconds, task_options=task_options)

    def __getstate__(self):
        state = super().__getstate__()
        state.pop("_lock", None)
        return state

    def __setstate__(self, state):
        super().__setstate__(state)
        self._lock = threading.Lock()

    def _log(self, entry: dict):
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            with open(self.log_file, "a") as f:
                f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **entry}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def run_command(self, command: str) -> str:
        """Run a bash command and return combined stdout/stderr."""
        with self._lock:
            if self._start is None:
                self._start = tree_hash()
                if os.path.exists(self.log_file):      # left by an earlier run of the same name
                    os.remove(self.log_file)
            self._n += 1
            n = self._n
            self._edited = tree_hash() != self._start
            entry = {"n": n, "cmd": command[:400]}
            if noop_command(command):
                entry["action"] = "noop"
                out = ("[loop] Not run: this command only holds an empty script (`pass`, an empty heredoc, "
                       "comments). Do not use tools as a notepad: think in your reply, then edit the Scala "
                       "file directly (`sed -i`, or a python3 heredoc that rewrites the lines).")
            elif not self._edited and n > self.hard:
                entry["action"] = "stop"
                out = (f"[loop] Session over: {self.hard} commands without an edit. Do not call any more "
                       "tools. Reply now with the edit you planned: file, line numbers, the exact new Chisel "
                       "code, and why it shortens the path and keeps every cycle the same. The next session "
                       "starts from your answer.")
            else:
                out = super().run_command(command)
                self._edited = tree_hash() != self._start
                entry["edited"] = self._edited
                if not self._edited and n > self.read_only:
                    entry["action"] = "withheld"
                    err = out[-1500:] if "[exit code:" in out else ""
                    out = (f"[loop] Output withheld: {n} commands used and this one changed no file under "
                           f"rocket-chip. Only edits are allowed now (`sed -i`, or a python3 script that "
                           f"rewrites the Scala file); {self.hard - n} commands left."
                           + (f"\n\nIt failed:\n{err}" if err else ""))
                elif not self._edited and n > self.soft:
                    entry["action"] = "warned"
                    out += (f"\n\n[loop] Budget: {n} commands used, no edit yet. After command "
                            f"{self.read_only} only commands that change a Scala file show their output; "
                            f"after command {self.hard} the session ends. Make your edit now.")
            self._log(entry)
            return out


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def round_diff(rocket_head: str, base_patch: str) -> str:
    """Diff of the rocket-chip working tree against `rocket_head` + `base_patch`: the
    edits of this round only. Uses a temporary index, so the tree and its index are
    left as they are."""
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
