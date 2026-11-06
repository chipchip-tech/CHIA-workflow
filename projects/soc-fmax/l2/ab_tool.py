"""A/B synthesis tool for the agent of l2_fmax.py (synth_ab); from projects/rocket-fmax/ab_tool.py (2b796c9).

Modeled on TimingExperimentTool of examples/timing_opt in CHIA (BOOM): while it
edits, the agent re-elaborates its Chisel to Verilog and synthesizes one module
of the edited RTL (child) next to the same module of the current best (parent),
so it can see whether an edit shortens the path before the loop pays for the
full build + gate + synth. Synthesis is the loop's own Yosys + OpenSTA flow
(fmax_nodes.synth_sta, sky130_fd_sc_hd, same period; SRAMs are black boxes), so a
sub-block takes about a minute and InclusiveCache (the whole L2) a few minutes.

MCP methods (claude shows them as mcp__synth_ab__<method>):

  rebuild_verilog(max_wait_seconds)   start `make verilog` (unless one is running), wait
  list_modules(pattern, only_changed) modules under InclusiveCache, with size and changed flag
  start_ab(module)                    synth child (and parent, cached) of `module`
  ab_result(handle, max_wait_seconds) result: slack of both sides and the delta

Every call returns within max_wait_seconds (<= MAX_WAIT): long work runs in a
thread (make) or as Ray tasks (synth_sta on "eda"), and the agent polls.

The tool actor runs on the chipyard worker next to chipyard_bash (the two share
the "chipyard" slot, 0.5 each), so `make verilog` sees the agent's edits and the
reports land where chipyard_bash can read them. The loop calls ab_cleanup
(fmax_nodes) after stopping the tool: it kills a `make verilog` still running.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from typing import Optional

import ray

from chia.base.ChiaFunction import get
from chia.base.tools.ChiaTool import ChiaTool

import fmax_nodes as N

AB_DIR = f"{N.STAGE_DIR}/ab"                 # reports for the agent, wiped every round
LOG_FILE = f"{AB_DIR}/experiments.jsonl"     # read back by the loop after the agent
MAX_WAIT = 60                                 # seconds a call may block (MCP round trip)
REBUILD_TIMEOUT = 1500
SUB_BLOCK_EDA = 0.5                           # two sub-block synths at once on "eda"
AB_YOSYS_TIMEOUT = 900                        # a child/parent Yosys past this is killed (normal: 3-5 min)
NOISE_NS = {"v2": 0.1, "v3": 0.01}            # |delta| below this is not a change (v2: flat-synthesis noise)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LOC_RE = re.compile(r"\s*//\s*@\[[^\]]*\]")   # firtool source locators move when Scala lines move


def tile_sources(rtl: N.TileRTL) -> list:
    """(name, text) sources of a TileRTL, as tile_rtl_from_sources takes them."""
    src = list(rtl.files.items()) + [(f"bb_{m}.v", t) for m, t in rtl.blackboxes.items()]
    if rtl.sram_models:      # the split_*_ext pieces the *_ext models instantiate
        src.append(("sram_models.v", rtl.sram_models))
    if rtl.mems_conf:
        src.append(("tile.top.mems.conf", rtl.mems_conf))
    return src


def module_texts(rtl: N.TileRTL) -> dict:
    """{module: text without source locators} for the modules under rtl.top."""
    out = {}
    for text in rtl.files.values():
        for m, t in N._split_modules(text).items():
            if m in rtl.modules:
                out[m] = _LOC_RE.sub("", t)
    return out


def child_key(rtl: N.TileRTL, module: str, flow: str, period_ns: float) -> str:
    """Hash of what one A/B synthesizes (module texts without source locators, SRAM models, flow,
    period): the same key means the same netlist, so the result can be reused (fmax-y3/y4 measured
    the same mulUnroll edit four times, 5 min each)."""
    h = hashlib.sha256(f"{module}\0{flow}\0{period_ns:g}\0".encode())
    for m, t in sorted(module_texts(rtl).items()):
        h.update(m.encode() + b"\0" + t.encode() + b"\0")
    h.update((rtl.sram_models or "").encode())
    return h.hexdigest()[:16]


def changed_modules(parent: dict, child: dict) -> dict:
    return {"changed": sorted(m for m in parent.keys() & child.keys() if parent[m] != child[m]),
            "added": sorted(child.keys() - parent.keys()),
            "removed": sorted(parent.keys() - child.keys())}


def make_errors(log: str, n: int = 40) -> str:
    lines = [l for l in _ANSI_RE.sub("", log).splitlines()]
    errs = [l for l in lines if l.startswith("[error]") or " error:" in l.lower()]
    return "\n".join(errs[:n]) if errs else "\n".join(lines[-n:])


def summary(s) -> dict:
    """Small dict of a SynthResult (what the agent and the log need)."""
    if s is None:
        return {"ok": False, "error": "no result"}
    if not s.success:
        return {"ok": False, "error": s.error or "synth failed"}
    return {"ok": True, "slack_ns": round(s.worst_slack_ns, 4), "path_ns": round(s.period_ns - s.worst_slack_ns, 4),
            "area_um2": round(s.area_um2 or 0), "crit": f"{s.crit_start} -> {s.crit_end}"}


def report_files(child, parent) -> dict:
    """{name: text} of the path reports and endpoint families of both sides."""
    out = {}
    for side, s in (("child", child), ("parent", parent)):
        if s is not None and s.success:
            out[f"{side}_paths.rpt"] = s.paths
            out[f"{side}_families.txt"] = s.families
    return out


def ab_lines(parent_name: str, p: dict, c: dict, child, parent=None, family_k: int = 0,
             noise: float = 0.1) -> tuple[list, Optional[float]]:
    """Report lines of one A/B (summaries `p`, `c`; SynthResults `child`, `parent`) and the delta
    child - parent in ns. The worst slack alone hides a family that got shorter under another one
    (fmax-r4 round 4: frontend edit, FPU path worst on both sides), so families are compared too."""
    lines = [f"parent ({parent_name}): " + (f"worst slack {p['slack_ns']:+.3f} ns, path {p['path_ns']:.3f} ns, "
                                            f"area {p['area_um2']}, {p['crit']}" if p["ok"] else p["error"]),
             "child (your edit):   " + (f"worst slack {c['slack_ns']:+.3f} ns, path {c['path_ns']:.3f} ns, "
                                        f"area {c['area_um2']}, {c['crit']}" if c["ok"] else c["error"])]
    delta = round(c["slack_ns"] - p["slack_ns"], 4) if c["ok"] and p["ok"] else None
    if delta is not None:
        verdict = ("better" if delta > noise else "worse" if delta < -noise
                   else f"no clear change (|delta| <= {noise} ns)")
        lines.append(f"delta of the worst slack (child - parent): {delta:+.3f} ns -> {verdict}")
    if family_k and c["ok"] and parent is not None and parent.success:
        ps, cs = N.family_score(parent.endpoints, family_k), N.family_score(child.endpoints, family_k)
        lines.append(f"family score (sum of the {family_k} worst families' slack, clipped at 0): parent {ps:.3f}, "
                     f"child {cs:.3f}, delta {cs - ps:+.3f} ns")
    table = (N.compare_families(parent.endpoints, child.endpoints)
             if c["ok"] and parent is not None and parent.success else "")
    if table:
        lines += ["worst slack per endpoint family (ns; delta = child - parent, positive = shorter):", table]
    elif c["ok"]:
        lines += ["child worst endpoint families:", "\n".join(child.families.splitlines()[:9])]
    return lines, delta


class SynthABTool(ChiaTool):

    def __init__(self, name: str, parent_rtl: N.TileRTL, parent_synth, parent_name: str, config: str,
                 period_ns: float, flow: str, make_jobs: int = 16, base_patch: str = "",
                 task_options: Optional[dict] = None, memo: Optional[dict] = None, round_label: str = "",
                 allow_synth: bool = True, family_k: int = 8):
        super().__init__(name, task_options=task_options)
        # Set before __post_init__: it pickles this object into the actor.
        self._parent_rtl = parent_rtl
        self._parent_synth = parent_synth            # full InclusiveCache result of the parent
        self._parent_tile = summary(parent_synth)
        self._parent_name = parent_name
        self._config = config
        self._period_ns = period_ns
        self._flow = flow
        self._make_jobs = make_jobs
        self._base_patch = base_patch                # the parent's patch: the logged diffs are the edits on top of it
        self._memo = dict(memo or {})                # child_key -> {"text", "label"}: A/Bs already measured
        self._round_label = round_label
        self._allow_synth = allow_synth              # False in --phase sim: rebuild only
        self._family_k = family_k
        self._lock = threading.Lock()
        self._rebuild: Optional[dict] = None        # current / last make verilog
        self._child: Optional[dict] = None          # {"rtl", "diff_hash", "diff", "changes"} of the last good rebuild
        self._parent_refs: dict = {}                # module -> ref (parent never changes)
        self._jobs: dict = {}                       # handle -> A/B job
        self._n = 0
        self.mcp.add_tool(self.rebuild_verilog, name="rebuild_verilog")
        self.mcp.add_tool(self.list_modules, name="list_modules")
        self.mcp.add_tool(self.start_ab, name="start_ab")
        self.mcp.add_tool(self.ab_result, name="ab_result")
        super().__post_init__()

    # A lock does not pickle; the actor gets a fresh one.
    def __getstate__(self):
        state = super().__getstate__()
        state.pop("_lock", None)
        return state

    def __setstate__(self, state):
        super().__setstate__(state)
        self._lock = threading.Lock()

    # -- helpers (run in the actor) ------------------------------------------

    @staticmethod
    def _diff_hash() -> str:
        """Hash of the edits (tracked diff + new files), without touching the git index."""
        d = subprocess.run(f"cd {N.ROCKET} && git diff HEAD && git ls-files --others --exclude-standard -z "
                           "| xargs -0 -r sha1sum", shell=True, capture_output=True, text=True).stdout
        return hashlib.sha256(d.encode()).hexdigest()[:12]

    def _edit_diff(self) -> str:
        """The agent's edits on top of the parent (logged with each A/B, so the loop can tell
        the next session what was measured and undone). Never fails the rebuild."""
        try:
            head = subprocess.run(["git", "-C", N.ROCKET, "rev-parse", "HEAD"], capture_output=True,
                                  text=True, check=True).stdout.strip()
            return N._round_diff(head, self._base_patch)
        except Exception as e:                                       # noqa: BLE001
            return f"(diff unavailable: {type(e).__name__}: {e})"

    def _log(self, entry: dict):
        os.makedirs(AB_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **entry}, ensure_ascii=False) + "\n")

    def _parent_modules(self) -> dict:
        if not hasattr(self, "_parent_texts"):
            self._parent_texts = module_texts(self._parent_rtl)
        return self._parent_texts

    def _run_make(self, job: dict):
        sims = f"{N.CHIPYARD}/sims/verilator"
        t0 = time.time()
        try:
            p = subprocess.Popen(["make", "verilog", f"CONFIG={self._config}", f"-j{self._make_jobs}"], cwd=sims,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 start_new_session=True)
            with open(N.AB_PGID_FILE, "w") as f:
                f.write(str(p.pid))
            try:
                out, _ = p.communicate(timeout=REBUILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                out, _ = p.communicate()
                job["error"] = f"make verilog timed out after {REBUILD_TIMEOUT} s"
                return
            if p.returncode != 0:
                job["error"] = f"make verilog failed (exit {p.returncode}):\n{make_errors(out)}"
                return
            cfg_dir = f"{sims}/generated-src/chipyard.harness.TestHarness.{self._config}"
            src = []
            for root, _d, names in os.walk(f"{cfg_dir}/gen-collateral"):
                for n in names:
                    if n.endswith((".v", ".sv")) and n != "TestDriver.v":
                        text = open(os.path.join(root, n), errors="replace").read()
                        if 'import "DPI-C"' not in text:
                            src.append((n, text))
            src += [(n, open(os.path.join(cfg_dir, n)).read()) for n in os.listdir(cfg_dir)
                    if n.endswith(".top.mems.conf")]
            rtl = N.tile_rtl_from_sources(src, N.TOP)
            changes = changed_modules(self._parent_modules(), module_texts(rtl))
            with self._lock:
                self._child = {"rtl": rtl, "diff_hash": job["diff_hash"], "diff": job["diff"], "changes": changes}
            job["changes"] = changes
        except Exception as e:                                       # noqa: BLE001
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            if os.path.exists(N.AB_PGID_FILE):
                os.remove(N.AB_PGID_FILE)
            job["seconds"] = time.time() - t0
            job["done"] = True
            self._log({"event": "rebuild", "ok": "error" not in job, "seconds": round(job["seconds"]),
                       "changed": job.get("changes", {}).get("changed"), "error": job.get("error", "")[:500]})

    def _rebuild_text(self, job: dict) -> str:
        if not job.get("done"):
            return (f"status: running ({time.time() - job['started']:.0f} s; usually 1-3 min)\n"
                    "call synth_ab_rebuild_verilog again to keep waiting (it does not start a second build "
                    "while one is running)")
        if "error" in job:
            return f"status: FAILED after {job['seconds']:.0f} s\n{job['error']}\nFix the Chisel and call synth_ab_rebuild_verilog again."
        c = job["changes"]
        return (f"status: ok ({job['seconds']:.0f} s)\n"
                f"modules changed vs {self._parent_name}: {', '.join(c['changed']) or '(none)'}\n"
                + (f"added: {', '.join(c['added'][:20])}\n" if c["added"] else "")
                + (f"removed: {', '.join(c['removed'][:20])}\n" if c["removed"] else "")
                + "next: synth_ab_start_ab(module) on the smallest changed module that holds the path you target")

    # -- MCP methods ---------------------------------------------------------

    def rebuild_verilog(self, max_wait_seconds: int = MAX_WAIT) -> str:
        """Elaborate your current Chisel edits to Verilog (`make verilog`, no simulator build).

        Starts a new elaboration unless one is already running, then waits up to
        max_wait_seconds (max 60). If it says `running`, call it again. Run it after every
        edit, before start_ab. Reports which modules changed compared with the current best.
        """
        with self._lock:
            job = self._rebuild
            if job is None or job.get("done"):
                job = {"started": time.time(), "diff_hash": self._diff_hash(), "diff": self._edit_diff()}
                self._rebuild = job
                threading.Thread(target=self._run_make, args=(job,), daemon=True).start()
        deadline = time.time() + max(0, min(int(max_wait_seconds), MAX_WAIT))
        while not job.get("done") and time.time() < deadline:
            time.sleep(1)
        return self._rebuild_text(job)

    def list_modules(self, pattern: str = "", only_changed: bool = False) -> str:
        """Modules under InclusiveCache, the L2 (from your last good rebuild, else from the current best):
        name, Verilog lines, and `*` if your edit changed it. `pattern` is a regex filter."""
        child = self._child
        rtl = child["rtl"] if child else self._parent_rtl
        texts = module_texts(rtl)
        changed = set(child["changes"]["changed"] + child["changes"]["added"]) if child else set()
        rows = []
        for m in sorted(texts):
            if pattern and not re.search(pattern, m):
                continue
            if only_changed and m not in changed:
                continue
            rows.append(f"{'*' if m in changed else ' '} {m:40s} {texts[m].count(chr(10)):6d} lines")
        side = "your last rebuild" if child else f"{self._parent_name} (no rebuild yet)"
        return f"modules of {side} (* = changed by your edit):\n" + ("\n".join(rows[:300]) or "(none)")

    def start_ab(self, module: str) -> str:
        """Synthesize `module` of your last rebuild (child) and of the current best (parent)
        with the loop's Yosys + OpenSTA flow and period. Returns a handle for ab_result.

        Sub-blocks (InclusiveCacheBankScheduler, SinkA, SourceC, MSHR, ListBuffer, ...) take about
        1-2 min and their ports get 0 input/output delay, so only paths inside the module count.
        module=InclusiveCache synthesizes the whole L2 (one at a time) and compares it with the best's
        full result: that is the number the loop judges.
        """
        if not self._allow_synth:
            return ("ERROR: no synthesis in this phase (simulation only): use synth_ab_rebuild_verilog to check "
                    "that your edit compiles; the loop measures its cycle cost on the benchmarks.")
        if self._rebuild is not None and not self._rebuild.get("done"):
            return "ERROR: a rebuild is running. Call synth_ab_rebuild_verilog until it says ok first."
        child = self._child
        if child is None:
            return "ERROR: no good rebuild yet. Call synth_ab_rebuild_verilog first."
        if self._diff_hash() != child["diff_hash"]:
            return ("ERROR: the source changed since your last rebuild, so its Verilog is stale. "
                    "Call synth_ab_rebuild_verilog again first.")
        whole = module == N.TOP
        try:
            c_rtl = N.tile_rtl_from_sources(tile_sources(child["rtl"]), module)
            p_rtl = None if whole else N.tile_rtl_from_sources(tile_sources(self._parent_rtl), module)
        except RuntimeError:
            close = [m for m in child["rtl"].modules if module.lower() in m.lower()][:15]
            return f"ERROR: module {module!r} is not under {N.TOP}. Similar: {close or '(none)'}; see list_modules."
        if not whole and module in child["rtl"].blackboxes:
            return f"ERROR: {module} is an SRAM black box; pick the module that instantiates it."
        key = child_key(c_rtl, module, self._flow, self._period_ns)
        res = {"resources": {"eda": 1 if whole else SUB_BLOCK_EDA}}
        with self._lock:
            self._n += 1
            handle = f"ab{self._n}"
            old = self._memo.get(key)
            if old is not None:
                text = (f"ALREADY MEASURED: this exact Verilog of {module} was synthesized before ({old['label']}); "
                        f"not synthesized again. That result (its parent may be an older best):\n{old['text']}")
                self._jobs[handle] = {"module": module, "text": text}
                self._log({"event": "ab", "handle": handle, "module": module, "memo": True, "child_key": key,
                           "diff": child.get("diff", "")[-6000:], "child": old.get("child", {}),
                           "parent": old.get("parent", {}), "delta_ns": old.get("delta_ns"),
                           "text": old["text"][-8000:]})
                return f"handle={handle}: already measured, call synth_ab_ab_result(handle=\"{handle}\") for the result"
            job = {"module": module, "key": key, "started": time.time(), "whole": whole, "diff": child.get("diff", ""),
                   "child": N.synth_sta.options(**res).chia_remote(c_rtl, self._period_ns, self._flow,
                                                                       yosys_timeout=AB_YOSYS_TIMEOUT)}
            if not whole:
                if module not in self._parent_refs:
                    self._parent_refs[module] = N.synth_sta.options(**res).chia_remote(
                        p_rtl, self._period_ns, self._flow, yosys_timeout=AB_YOSYS_TIMEOUT)
                job["parent"] = self._parent_refs[module]
            self._jobs[handle] = job
        return (f"started: handle={handle}, module={module} ({len(c_rtl.modules)} modules, "
                f"{'whole L2' if whole else 'child + parent'})\n"
                f"poll with synth_ab_ab_result(handle=\"{handle}\", max_wait_seconds=60)")

    def ab_result(self, handle: str, max_wait_seconds: int = MAX_WAIT) -> str:
        """Result of start_ab: worst slack of child and parent and the delta (child - parent;
        positive = your edit shortened the worst path). Waits up to max_wait_seconds (max 60);
        if it says `running`, call it again."""
        job = self._jobs.get(handle)
        if job is None:
            return f"ERROR: unknown handle {handle!r} (known: {', '.join(self._jobs) or 'none'})"
        if "text" in job:
            return job["text"]
        refs = [job["child"]] + ([job["parent"]] if "parent" in job else [])
        ready, _ = ray.wait(refs, num_returns=len(refs), timeout=max(0, min(int(max_wait_seconds), MAX_WAIT)))
        if len(ready) < len(refs):
            return (f"status: running ({time.time() - job['started']:.0f} s)\n"
                    f"call synth_ab_ab_result(handle=\"{handle}\", max_wait_seconds=60) again")
        try:
            child = get(job["child"])
            parent = get(job["parent"]) if "parent" in job else None
        except Exception as e:                                       # noqa: BLE001
            job["text"] = f"status: FAILED ({type(e).__name__}: {str(e)[:1500]})"
            self._log({"event": "ab", "handle": handle, "module": job["module"], "error": job["text"][:500]})
            return job["text"]
        c = summary(child)
        p = self._parent_tile if job["whole"] else summary(parent)
        d = os.path.join(AB_DIR, handle)
        os.makedirs(d, exist_ok=True)
        for rel, text in report_files(child, parent).items():
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        lines, delta = ab_lines(self._parent_name, p, c, child, self._parent_synth if job["whole"] else parent,
                                self._family_k, NOISE_NS.get(self._flow, 0.1))
        lines.insert(0, f"status: done ({time.time() - job['started']:.0f} s), module {job['module']}"
                     + (" (whole L2, parent = full result of the best)" if job["whole"] else ""))
        if c["ok"]:
            lines.append(f"full reports: {d}/child_paths.rpt, {d}/parent_paths.rpt (read with chipyard_bash)")
        job["text"] = "\n".join(lines)
        label = f"{self._round_label}, {handle}".strip(", ")
        self._memo[job["key"]] = {"text": job["text"], "label": label, "child": c, "parent": p, "delta_ns": delta}
        self._log({"event": "ab", "handle": handle, "module": job["module"], "child": c, "parent": p, "delta_ns": delta,
                   "diff": job["diff"][-6000:], "child_key": job["key"], "label": label, "text": job["text"][-8000:]})
        return job["text"]
