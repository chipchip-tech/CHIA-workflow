"""Công cụ A/B cho agent của vòng ASIC (`asic_ab`), theo `synth_ab` của rocket-fmax (ab_tool.py ở nhánh
project/rocket-fmax).

Trong lúc sửa, agent dịch Chisel ra Verilog (`make verilog`) rồi synth + STA MỘT module của bản đang sửa (child)
cạnh cùng module đó của best (parent), bằng chính flow ORFS + OpenROAD của vòng lặp (cùng PDK, chu kỳ, cờ
phân cấp; SRAM black-box; cổng vào/ra delay 0 nên chỉ path bên trong module được tính). Nhờ vậy nó thấy một sửa
có rút ngắn path không, và path nào bị đẩy dài ra, trước khi vòng lặp tốn 15 phút build + gate + synth cả SoC.

MCP (claude thấy dạng mcp__asic_ab__asic_ab_<method>):

  rebuild_verilog(max_wait_seconds)   chạy `make verilog` (nếu chưa chạy), chờ
  list_modules(pattern, only_changed) module dưới ChipTop: số dòng, cờ đã đổi
  start_ab(module)                    synth child (và parent, có nhớ) của `module`
  ab_result(handle, max_wait_seconds) kết quả: slack hai bên, chênh lệch theo họ register

Mọi lời gọi trả về trong max_wait_seconds (≤ MAX_WAIT): việc dài chạy ở thread (make) hoặc Ray task
(asic_module_sta trên khối asic), agent hỏi lại.

Actor chạy trên khối chipyard cạnh chipyard_bash (mỗi bên 0,5 slot), nên `make verilog` thấy đúng file agent sửa.
Nhật ký: AB_DIR/experiments.jsonl, vòng lặp đọc sau lượt (N.ab_read_log).
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from typing import Optional

import ray

from chia.base.ChiaFunction import get
from chia.base.tools.ChiaTool import ChiaTool

import minsoc_nodes as N

MAX_WAIT = 60                 # giây một lời gọi được chặn (vòng MCP)
REBUILD_TIMEOUT = 2400
NOISE_NS = 0.02               # |chênh| dưới mức này coi như không đổi (cùng Verilog thì ra cùng netlist)
TOP_FORBIDDEN = ("ChipTop", "DigitalTop")


def _make_errors(log: str, n: int = 40) -> str:
    lines = log.splitlines()
    errs = [l for l in lines if l.startswith("[error]") or " error:" in l.lower()]
    return "\n".join(errs[:n]) if errs else "\n".join(lines[-n:])


def ab_key(mods: dict, top: str, period_ns: float, hier: bool, platform: str) -> str:
    """Băm những gì một A/B synth (text các module, bỏ locator; top; chu kỳ; cờ): cùng khoá = cùng netlist."""
    h = hashlib.sha256(f"{top}\0{period_ns:g}\0{int(hier)}\0{platform}\0".encode())
    for m in sorted(mods):
        h.update(m.encode() + b"\0" + N.strip_loc(mods[m]).encode() + b"\0")
    return h.hexdigest()[:16]


def summary(r) -> dict:
    if not r or not r.get("ok"):
        return {"ok": False, "error": (r or {}).get("yosys_errors") or (r or {}).get("sta_tail", "")[-800:]
                or "synth/STA failed"}
    return {"ok": True, "wns": r["wns"], "tns": r.get("tns"), "area_um2": round(r.get("area_um2") or 0),
            "family_score": r.get("family_score"), "minutes": round((r.get("seconds") or 0) / 60, 1)}


def compare_families(p_ends: str, c_ends: str, n: int = 14) -> str:
    """Bảng slack xấu nhất theo họ register, parent cạnh child (chênh dương = child ngắn hơn)."""
    pf, cf = N.family_table(p_ends), N.family_table(c_ends)
    keys = sorted(set(pf) | set(cf), key=lambda k: min(pf.get(k, [9e9])[0], cf.get(k, [9e9])[0]))[:n]
    rows = [f"{'parent':>8} {'child':>8} {'delta':>7}  family"]
    for k in keys:
        p, c = pf.get(k, [None])[0], cf.get(k, [None])[0]
        d = f"{c - p:+7.3f}" if p is not None and c is not None else "    n/a"
        rows.append(f"{p if p is not None else '-':>8} {c if c is not None else '-':>8} {d}  {k}")
    return "\n".join(rows)


class AsicABTool(ChiaTool):

    def __init__(self, name: str, config: str, period_ns: float, sdc: str, sta_tcl: str, platform: str,
                 frontend: str, hier: bool, parent_name: str, round_label: str = "", memo: Optional[dict] = None,
                 make_jobs: int = 16, task_options: Optional[dict] = None):
        super().__init__(name, task_options=task_options)
        # Đặt trước __post_init__: nó pickle đối tượng này vào actor.
        self._config = config
        self._period_ns = period_ns
        self._sdc = sdc
        self._sta_tcl = sta_tcl
        self._platform = platform
        self._frontend = frontend
        self._hier = hier
        self._parent_name = parent_name
        self._round_label = round_label
        self._memo = dict(memo or {})                 # khoá -> {"text", "label", ...}: A/B đã đo
        self._make_jobs = make_jobs
        self._lock = threading.Lock()
        self._rebuild: Optional[dict] = None
        self._child: Optional[dict] = None             # {"mods", "mems", "diff_hash", "changes"}
        self._parent: Optional[tuple] = None           # (mods, mems), đọc lười
        self._parent_refs: dict = {}                   # module -> ref (parent không đổi trong lượt)
        self._jobs: dict = {}
        self._n = 0
        self.mcp.add_tool(self.rebuild_verilog, name="rebuild_verilog")
        self.mcp.add_tool(self.list_modules, name="list_modules")
        self.mcp.add_tool(self.start_ab, name="start_ab")
        self.mcp.add_tool(self.ab_result, name="ab_result")
        super().__post_init__()

    def __getstate__(self):
        state = super().__getstate__()
        state.pop("_lock", None)
        return state

    def __setstate__(self, state):
        super().__setstate__(state)
        self._lock = threading.Lock()

    # -- trong actor ----------------------------------------------------------

    @staticmethod
    def _repos() -> list:
        cy = N.fork_dir()
        return [cy] + [os.path.join(cy, d) for d in N.SUBMODULES]

    def _diff(self) -> str:
        out = []
        for path in self._repos():
            out.append(subprocess.run(f"git -C {path} diff HEAD --ignore-submodules=all; "
                                      f"git -C {path} ls-files -o --exclude-standard -- '*.scala' | "
                                      f"xargs -r -I{{}} sh -c 'echo +++ {{}}; cat {path}/{{}}'",
                                      shell=True, capture_output=True, text=True).stdout)
        return "\n".join(out)

    def _diff_hash(self) -> str:
        return hashlib.sha256(self._diff().encode()).hexdigest()[:12]

    def _log(self, entry: dict):
        d = N.ab_dir(N.fork_dir())
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/experiments.jsonl", "a") as f:
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "round": self._round_label, **entry},
                               ensure_ascii=False) + "\n")

    def _parent_rtl(self) -> tuple:
        if self._parent is None:
            self._parent = N.read_rtl_modules(f"{N.ab_dir(N.fork_dir())}/parent")
        return self._parent

    def _run_make(self, job: dict):
        cy = N.fork_dir()
        t0 = time.time()
        try:
            p = subprocess.Popen(["make", "verilog", f"CONFIG={self._config}", f"-j{self._make_jobs}"],
                                 cwd=f"{cy}/sims/verilator", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, start_new_session=True, stdin=subprocess.DEVNULL)
            try:
                out, _ = p.communicate(timeout=REBUILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                out, _ = p.communicate()
                job["error"] = f"make verilog timed out after {REBUILD_TIMEOUT} s"
                return
            if p.returncode != 0:
                job["error"] = f"make verilog failed (exit {p.returncode}):\n{_make_errors(out)}"
                return
            mods, mems = N.read_rtl_modules(f"{N.verilator_build_dir(cy, self._config)}/gen-collateral")
            pm, _ = self._parent_rtl()
            changes = {"changed": sorted(m for m in pm.keys() & mods.keys()
                                         if N.strip_loc(pm[m]) != N.strip_loc(mods[m])),
                       "added": sorted(mods.keys() - pm.keys()), "removed": sorted(pm.keys() - mods.keys())}
            with self._lock:
                self._child = {"mods": mods, "mems": mems, "diff_hash": job["diff_hash"], "changes": changes}
            job["changes"] = changes
        except Exception as e:                                       # noqa: BLE001
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["seconds"] = time.time() - t0
            job["done"] = True
            self._log({"event": "rebuild", "ok": "error" not in job, "seconds": round(job["seconds"]),
                       "changed": job.get("changes", {}).get("changed"), "error": job.get("error", "")[:500]})

    def _rebuild_text(self, job: dict) -> str:
        if not job.get("done"):
            return (f"status: running ({time.time() - job['started']:.0f} s; usually 2-6 min)\n"
                    "call asic_ab_rebuild_verilog again to keep waiting (it does not start a second build)")
        if "error" in job:
            return (f"status: FAILED after {job['seconds']:.0f} s\n{job['error']}\n"
                    "Fix the Chisel and call asic_ab_rebuild_verilog again.")
        c = job["changes"]
        return (f"status: ok ({job['seconds']:.0f} s)\n"
                f"modules changed vs {self._parent_name}: {', '.join(c['changed']) or '(none)'}\n"
                + (f"added: {', '.join(c['added'][:20])}\n" if c["added"] else "")
                + (f"removed: {', '.join(c['removed'][:20])}\n" if c["removed"] else "")
                + "next: asic_ab_start_ab(module) on the smallest module that holds both ends of the path you target")

    # -- MCP ------------------------------------------------------------------

    def rebuild_verilog(self, max_wait_seconds: int = MAX_WAIT) -> str:
        """Elaborate your current Chisel edits to Verilog (`make verilog`, no simulator build).

        Starts an elaboration unless one is running, then waits up to max_wait_seconds (max 60).
        If it says `running`, call it again. Run it after every edit, before start_ab. Reports which
        modules changed compared with the current best.
        """
        with self._lock:
            job = self._rebuild
            if job is None or job.get("done"):
                job = {"started": time.time(), "diff_hash": self._diff_hash()}
                self._rebuild = job
                threading.Thread(target=self._run_make, args=(job,), daemon=True).start()
        deadline = time.time() + max(0, min(int(max_wait_seconds), MAX_WAIT))
        while not job.get("done") and time.time() < deadline:
            time.sleep(1)
        return self._rebuild_text(job)

    def list_modules(self, pattern: str = "", only_changed: bool = False) -> str:
        """Modules of the design (your last good rebuild, else the current best): name, Verilog lines,
        `*` if your edit changed it. `pattern` is a substring filter (case-insensitive)."""
        import re
        child = self._child
        mods = child["mods"] if child else self._parent_rtl()[0]
        changed = set(child["changes"]["changed"] + child["changes"]["added"]) if child else set()
        rows = [f"{'*' if m in changed else ' '} {m:44s} {mods[m].count(chr(10)):7d} lines"
                for m in sorted(mods) if (not pattern or re.search(re.escape(pattern), m, re.I))
                and (not only_changed or m in changed)]
        side = "your last rebuild" if child else f"{self._parent_name} (no rebuild yet)"
        return f"modules of {side} (* = changed by your edit):\n" + ("\n".join(rows[:300]) or "(none)")

    def start_ab(self, module: str) -> str:
        """Synthesize `module` (with all its submodules) of your last rebuild (child) and of the current
        best (parent) with the loop's Yosys + OpenROAD STA flow, PDK and period. Returns a handle for ab_result.

        Ports get 0 input/output delay, so only register-to-register paths INSIDE the module count: pick a
        module that holds both ends of the path (e.g. `Rocket` for core paths, `RocketTile` for frontend ->
        core or dcache -> frontend paths, `InclusiveCache` for L2). Small blocks take 1-3 min, RocketTile
        about 5-10 min. ChipTop/DigitalTop are not allowed (that is the loop's own measurement).
        """
        if module in TOP_FORBIDDEN:
            return f"ERROR: {module} is the whole SoC; the loop measures that. Pick RocketTile or smaller."
        if self._rebuild is not None and not self._rebuild.get("done"):
            return "ERROR: a rebuild is running. Call asic_ab_rebuild_verilog until it says ok first."
        child = self._child
        if child is None:
            return "ERROR: no good rebuild yet. Call asic_ab_rebuild_verilog first."
        if self._diff_hash() != child["diff_hash"]:
            return ("ERROR: the source changed since your last rebuild, so its Verilog is stale. "
                    "Call asic_ab_rebuild_verilog again first.")
        if module not in child["mods"]:
            close = [m for m in child["mods"] if module.lower() in m.lower()][:15]
            return f"ERROR: no module {module!r}. Similar: {close or '(none)'}; see asic_ab_list_modules."
        pm, pmems = self._parent_rtl()
        c_mods = N.module_closure(child["mods"], module)
        p_mods = N.module_closure(pm, module) if module in pm else None
        key = ab_key(c_mods, module, self._period_ns, self._hier, self._platform)
        args = (self._period_ns, self._sdc, self._sta_tcl, self._platform, self._frontend, self._hier)
        with self._lock:
            self._n += 1
            handle = f"ab{self._n}"
            old = self._memo.get(key)
            if old is not None:
                text = (f"ALREADY MEASURED: this exact Verilog of {module} was synthesized before ({old['label']}); "
                        f"not synthesized again. That result (its parent may be an older best):\n{old['text']}")
                self._jobs[handle] = {"module": module, "text": text}
                self._log({"event": "ab", "handle": handle, "module": module, "memo": True, "key": key,
                           "delta_ns": old.get("delta_ns"), "text": old["text"][-6000:]})
                return f"handle={handle}: already measured, call asic_ab_ab_result(handle=\"{handle}\")"
            job = {"module": module, "key": key, "started": time.time(),
                   "child": N.asic_module_sta.chia_remote(f"c{key}", c_mods, child["mems"], module, *args)}
            if p_mods is not None:
                if module not in self._parent_refs:
                    pk = ab_key(p_mods, module, self._period_ns, self._hier, self._platform)
                    self._parent_refs[module] = N.asic_module_sta.chia_remote(f"p{pk}", p_mods, pmems, module, *args)
                job["parent"] = self._parent_refs[module]
            self._jobs[handle] = job
        return (f"started: handle={handle}, module={module} ({len(c_mods)} modules, "
                f"{'child + parent' if p_mods is not None else 'child only: module is new'})\n"
                f"poll with asic_ab_ab_result(handle=\"{handle}\", max_wait_seconds=60)")

    def ab_result(self, handle: str, max_wait_seconds: int = MAX_WAIT) -> str:
        """Result of start_ab: WNS/TNS of child and parent, the delta (child - parent; positive = your edit
        made it shorter) and the worst slack per register family on both sides, so you see which paths your
        edit made LONGER. Waits up to max_wait_seconds (max 60); if it says `running`, call it again."""
        job = self._jobs.get(handle)
        if job is None:
            return f"ERROR: unknown handle {handle!r} (known: {', '.join(self._jobs) or 'none'})"
        if "text" in job:
            return job["text"]
        refs = [job["child"]] + ([job["parent"]] if "parent" in job else [])
        ready, _ = ray.wait(refs, num_returns=len(refs), timeout=max(0, min(int(max_wait_seconds), MAX_WAIT)))
        if len(ready) < len(refs):
            return (f"status: running ({time.time() - job['started']:.0f} s)\n"
                    f"call asic_ab_ab_result(handle=\"{handle}\", max_wait_seconds=60) again")
        try:
            child = get(job["child"])
            parent = get(job["parent"]) if "parent" in job else None
        except Exception as e:                                       # noqa: BLE001
            job["text"] = f"status: FAILED ({type(e).__name__}: {str(e)[:1500]})"
            self._log({"event": "ab", "handle": handle, "module": job["module"], "error": job["text"][:500]})
            return job["text"]
        c, p = summary(child), summary(parent) if parent is not None else {"ok": False, "error": "no parent"}
        fmt = lambda s: (f"WNS {s['wns']:+.3f} ns, TNS {s['tns']} ns, family score {s['family_score']}, "  # noqa: E731
                         f"area {s['area_um2']} um2" if s["ok"] else f"FAILED: {s['error']}")
        lines = [f"status: done ({time.time() - job['started']:.0f} s), module {job['module']}, "
                 f"period {self._period_ns:g} ns, ports with 0 delay (only paths inside the module)",
                 f"parent ({self._parent_name}): {fmt(p)}", f"child (your edit):  {fmt(c)}"]
        delta = round(c["wns"] - p["wns"], 4) if c["ok"] and p["ok"] else None
        if delta is not None:
            verdict = "better" if delta > NOISE_NS else "worse" if delta < -NOISE_NS else "no change"
            lines.append(f"delta WNS (child - parent): {delta:+.3f} ns -> {verdict}; delta family score "
                         f"{(c['family_score'] or 0) - (p['family_score'] or 0):+.3f} ns")
        if c["ok"]:
            lines += ["worst slack per register family (ns; delta = child - parent, positive = shorter):",
                      compare_families(parent.get("endpoints", "") if p["ok"] else "", child.get("endpoints", ""))]
        job["text"] = "\n".join(lines)
        label = f"{self._round_label} {handle}".strip()
        self._memo[job["key"]] = {"text": job["text"], "label": label, "delta_ns": delta}
        self._log({"event": "ab", "handle": handle, "module": job["module"], "key": job["key"], "child": c,
                   "parent": p, "delta_ns": delta, "label": label, "text": job["text"][-6000:],
                   "diff": self._diff()[-6000:]})
        return job["text"]
