"""ab_tool.py: công cụ A/B cho agent của loop.py (MCP server "ab"), theo TimingExperimentTool của CHIA
(examples/timing_opt) và synth_ab của project rocket-fmax (ab_tool.py, đã chạy thật).

Trong phiên agent (pha asic, task propose), agent đo bản sửa ĐANG CÓ trước khi loop trả ~15 phút ORFS tới place:

  rebuild_verilog(max_wait_seconds)  diff bản chép của agent (chipyard/) so với bản gốc -> áp lên cây Chipyard thật trên
                                     khối chipyard -> `make verilog` -> hoàn nguyên; báo lỗi compile hoặc module đã đổi
  list_modules(pattern, only_changed) module dưới DigitalTop (số dòng, * = bị bản sửa đổi)
  start_ab(module)                   ORFS synth + 2_1_floorplan (repair_timing không chèn buffer, không đặt macro/PDN)
                                     của MỘT module, bản sửa (child) và đầu chuỗi (parent), ở chu kỳ đặt chặt --ab-period
  ab_result(handle, max_wait_seconds) slack hai bên, chênh lệch, họ endpoint

Mỗi lời gọi trả về trong max_wait_seconds (≤ MAX_WAIT); việc dài chạy thành task Ray, agent gọi lại để chờ.

Bố trí:
  - actor của tool chạy trên CÙNG node với phiên agent (khối claude, node affinity), để đọc được bản chép của agent;
  - make verilog: task trên khối chipyard (resource chipyard). Patch đã áp và process group của make ghi vào
    STATE_FILE trước khi chạy; mọi task ab trên khối chipyard (và ab_idle, loop gọi sau phiên agent) dọn phần còn sót
    (giết make, git apply -R) trước khi làm việc khác: phiên agent bị giết giữa chừng không để cây bẩn;
  - ORFS: task trên khối asic (resource asic, nên child và parent chạy lần lượt); variant `ab_<hash>...`, loop xoá cuối job.
Kết quả A/B chỉ là tín hiệu cho agent (độ sâu logic trong một module, không dây giữa các khối, không nhiễu place); luật
nhận của loop không đổi.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import ray

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.tools.ChiaTool import ChiaTool

import orfs_nodes as O
import soc_nodes as S
from patchutil import split_patch

MAX_WAIT = 60                          # giây tối đa một lời gọi MCP được chờ
MAKE_TIMEOUT = 1500                    # make verilog
STATE_DIR = "/tmp/socfmax_ab"          # trên khối chipyard
STATE_FILE = f"{STATE_DIR}/applied.json"
NOISE_NS = 0.02                        # |delta| nhỏ hơn: coi như không đổi (ORFS tất định; chừa sai số làm tròn)
FAMILY_ROWS = 12

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# khối chipyard: áp patch tạm, make verilog, hoàn nguyên
# ---------------------------------------------------------------------------

def _git(args: list, cwd: str, inp: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, input=inp, capture_output=True, text=True, timeout=600)


def _save_state(st: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def cleanup_state(label: str = "còn sót") -> list[str]:
    """Dọn phần A/B trên khối chipyard: giết make verilog, hoàn nguyên patch đã áp. Trả ghi chú; lỗi -> raise."""
    if not os.path.exists(STATE_FILE):
        return []
    notes = []
    st = json.load(open(STATE_FILE))
    pg = st.get("pgid")
    if pg:
        try:
            os.killpg(int(pg), signal.SIGKILL)
            notes.append(f"giết make verilog {label} (pgid {pg})")
        except (ProcessLookupError, PermissionError):
            pass
    errs = []
    for d, text in reversed(st.get("parts", [])):
        if _git(["apply", "-R", "--check", "--whitespace=nowarn", "-"], d, text).returncode == 0:
            r = _git(["apply", "-R", "--whitespace=nowarn", "-"], d, text)
            if r.returncode != 0:
                errs.append(f"{d}: {r.stderr[-500:]}")
            else:
                notes.append(f"hoàn nguyên patch A/B {label} ở {d}")
        elif _git(["apply", "--check", "--whitespace=nowarn", "-"], d, text).returncode != 0:
            errs.append(f"{d}: patch A/B không áp ngược được mà cũng không áp xuôi được (cây ở trạng thái lạ)")
    if errs:
        raise RuntimeError("không dọn được A/B trên cây Chipyard:\n" + "\n".join(errs))
    os.remove(STATE_FILE)
    return notes


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def ab_idle(cy: str) -> dict:
    """Loop gọi sau mỗi phiên agent có A/B: chờ khối chipyard rảnh (task A/B đang chạy xong trước) rồi dọn phần sót."""
    return {"notes": cleanup_state()}


def _collect_sources(sims: str, config: str) -> list:
    dirs = [p for p in Path(sims, "generated-src").glob(f"*{config}*") if (p / "gen-collateral").is_dir()]
    if not dirs:
        raise RuntimeError(f"make verilog xong nhưng không thấy generated-src/*{config}*/gen-collateral")
    cfg = max(dirs, key=lambda p: p.stat().st_mtime)
    src = []
    for root, _d, names in os.walk(cfg / "gen-collateral"):
        for n in names:
            if n.endswith((".v", ".sv")) and n != "TestDriver.v":
                text = open(os.path.join(root, n), errors="replace").read()
                if 'import "DPI-C"' not in text:
                    src.append((n, text))
    src += [(p.name, p.read_text()) for p in cfg.glob("*.top.mems.conf")]
    return src


def _make_errors(log: str, n: int = 50) -> str:
    lines = _ANSI_RE.sub("", log).splitlines()
    errs = [l for l in lines if l.startswith("[error]") or re.search(r"\berror\b|Exception", l, re.I)]
    return "\n".join(errs[:n]) if errs else "\n".join(lines[-n:])


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def ab_make_verilog(cy: str, config: str, patch: str, subs: list, jobs: int, timeout: int = MAKE_TIMEOUT) -> dict:
    """Áp `patch` (đường dẫn từ gốc Chipyard) lên cây, make verilog, tách DigitalTop, hoàn nguyên. Không raise."""
    t0 = time.time()
    out = {"ok": False, "error": "", "rtl": None, "seconds": 0.0, "notes": []}
    try:
        out["notes"] = cleanup_state()
        parts = split_patch(patch, subs)
        for sub, text in parts.items():
            r = _git(["apply", "--check", "--whitespace=nowarn", "-"], os.path.join(cy, sub), text)
            if r.returncode != 0:
                out["error"] = (f"your edits do not apply to the real tree ({sub or 'root'}):\n{r.stderr[-1500:]}\n"
                                "(did you edit a file that changed under you? re-read it from the read-only tree)")
                return out
        st = {"cy": cy, "parts": [], "pgid": None}
        for sub, text in parts.items():
            d = os.path.join(cy, sub)
            st["parts"].append((d, text))
            _save_state(st)                      # ghi trước khi áp: chết giữa chừng thì lần dọn sau vẫn biết
            r = _git(["apply", "--whitespace=nowarn", "-"], d, text)
            if r.returncode != 0:
                st["parts"].pop()
                _save_state(st)
                out["error"] = f"git apply lỗi ở {sub or 'root'}: {r.stderr[-1500:]}"
                return out
        sims = os.path.join(cy, "sims", "verilator")
        p = subprocess.Popen(["make", "verilog", f"CONFIG={config}", f"-j{jobs}"], cwd=sims, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        st["pgid"] = p.pid
        _save_state(st)
        try:
            log, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            out["error"] = f"make verilog timed out after {timeout} s"
            return out
        st["pgid"] = None
        _save_state(st)
        if p.returncode != 0:
            out["error"] = f"make verilog FAILED (exit {p.returncode}):\n{_make_errors(log)}"
            return out
        out["rtl"] = S.soc_rtl_from_sources(_collect_sources(sims, config), S.TOP)
        out["ok"] = True
    except Exception as e:                       # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:3000]
    finally:
        try:
            cleanup_state("của lần này")      # hoàn nguyên bình thường: không ghi chú
        except Exception as e:                   # noqa: BLE001
            out["ok"] = False
            out["error"] = (out["error"] + "\n" if out["error"] else "") + f"CLEANUP: {e}"
        out["seconds"] = time.time() - t0
    return out


# ---------------------------------------------------------------------------
# khối asic: ORFS synth + 2_1_floorplan của một module
# ---------------------------------------------------------------------------

def _load_fakeram(src: str):
    name = "fakeram_sky130"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    exec(compile(src, "fakeram_sky130.py", "exec"), mod.__dict__)
    return mod


def ab_variant(sub: S.SocRTL, cfg: dict) -> str:
    return "ab_" + O.variant_name(sub.hash, cfg["period_ns"], cfg["fr_tag"], cfg["orfs_vars"])


@ChiaFunction(resources={"asic": 1}, max_retries=0)
def ab_orfs(sub: S.SocRTL, cfg: dict) -> O.StageResult:
    """Chuẩn bị design (trong ORFS của container) cho cây con `sub` rồi make 2_1_floorplan (ORFS cũ không có đích
    này: chạy cả floorplan). Không raise."""
    variant = ab_variant(sub, cfg)
    try:
        fk = _load_fakeram(cfg["fakeram_src"])
        prm = fk.Params(**cfg["fr_params"])
        prep = O.prepare_design(O.C_ORFS, sub, cfg["period_ns"], fk, prm, variant, cfg["orfs_vars"])
        r = O.run_stage(prep["config_mk"], variant, "2_1_floorplan", cfg["period_ns"], cfg["orfs_timeout"])
        if not r.ok and "No rule to make target" in (r.make_tail or ""):
            r = O.run_stage(prep["config_mk"], variant, "floorplan", cfg["period_ns"], cfg["orfs_timeout"])
        return r
    except Exception as e:                       # noqa: BLE001
        return O.StageResult(stage="2_1_floorplan", variant=variant, period_ns=cfg["period_ns"], ok=False,
                             error=f"{type(e).__name__}: {e}"[:3000])


# ---------------------------------------------------------------------------
# so sánh
# ---------------------------------------------------------------------------

def _side(r: Optional[O.StageResult]) -> dict:
    if r is None:
        return {"ok": False, "error": "no result"}
    if not r.ok:
        return {"ok": False, "error": (r.error or "ORFS failed").strip()[:1500]}
    return {"ok": True, "slack_ns": round(r.internal_slack_ns, 4), "path_ns": round(r.period_ns - r.internal_slack_ns, 4),
            "area_um2": round(r.std_area_um2 or 0), "crit": f"{r.crit_start} -> {r.crit_end}",
            "minutes": round(r.seconds / 60, 1)}


def compare_families(parent: str, child: str, n: int = FAMILY_ROWS) -> str:
    pf, cf = S.family_table(parent), S.family_table(child)
    keys = sorted(set(pf) | set(cf), key=lambda k: min(pf.get(k, [9e9])[0], cf.get(k, [9e9])[0]))[:n]
    rows = [f"{'parent':>9} {'child':>9} {'delta':>8}  endpoint family"]
    for k in keys:
        p, c = pf.get(k), cf.get(k)
        d = f"{c[0] - p[0]:+8.3f}" if p and c else f"{'':>8}"
        rows.append(f"{p[0] if p else float('nan'):9.3f} {c[0] if c else float('nan'):9.3f} {d}  {k}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# tool
# ---------------------------------------------------------------------------

class ABTool(ChiaTool):

    def __init__(self, name: str, ws: str, orig: dict, readonly: set, cfg: dict, parent_rtl: S.SocRTL,
                 parent_name: str, task_options: Optional[dict] = None):
        super().__init__(name, task_options=task_options)
        # Gán trước __post_init__: nó pickle đối tượng này vào actor.
        self._ws = ws                                # thư mục làm việc của agent (có chipyard/)
        self._orig = orig                            # {đường dẫn từ gốc Chipyard: nội dung gốc}
        self._readonly = set(readonly)
        self._cfg = cfg                              # chipyard, config, subs, make_jobs, period_ns, orfs_vars, fakeram_src,
        #                                              fr_params, fr_tag, orfs_timeout, max_runs
        self._parent_rtl = parent_rtl
        self._parent_name = parent_name
        self._lock = threading.Lock()
        self._rebuild: Optional[dict] = None
        self._child: Optional[dict] = None          # {"rtl", "phash", "changes"}
        self._parent_refs: dict = {}                # module -> ref
        self._jobs: dict = {}
        self._memo: dict = {}                       # hash cây con child -> text
        self._runs = 0
        self._n = 0
        self.mcp.add_tool(self.rebuild_verilog, name="rebuild_verilog")
        self.mcp.add_tool(self.list_modules, name="list_modules")
        self.mcp.add_tool(self.start_ab, name="start_ab")
        self.mcp.add_tool(self.ab_result, name="ab_result")
        super().__post_init__()

    # Lock không pickle được; actor tạo lock mới.
    def __getstate__(self):
        state = super().__getstate__()
        state.pop("_lock", None)
        return state

    def __setstate__(self, state):
        super().__setstate__(state)
        self._lock = threading.Lock()

    # -- trong actor ---------------------------------------------------------

    def _log(self, entry: dict):
        try:
            with open(os.path.join(self._ws, "ab_log.jsonl"), "a") as f:
                f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **entry}, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _patch(self) -> tuple[str, str]:
        import agent as A                            # không import ở đầu file: agent import ab_tool khi bật A/B
        patch, _dropped = A.make_patch(Path(self._ws) / "chipyard", self._orig, self._readonly)
        return patch, hashlib.sha256(patch.encode()).hexdigest()[:12]

    def _finish_rebuild(self, job: dict):
        try:
            res = get(job["ref"])
        except Exception as e:                       # noqa: BLE001
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:2000], "seconds": time.time() - job["started"]}
        job["res"] = res
        if res.get("ok"):
            pt, ct = S.module_texts(self._parent_rtl), S.module_texts(res["rtl"])
            changes = {"changed": sorted(m for m in pt.keys() & ct.keys() if pt[m] != ct[m]),
                       "added": sorted(ct.keys() - pt.keys()), "removed": sorted(pt.keys() - ct.keys())}
            job["changes"] = changes
            self._child = {"rtl": res["rtl"], "phash": job["phash"], "changes": changes}
        self._log({"event": "rebuild", "ok": bool(res.get("ok")), "seconds": round(res.get("seconds", 0)),
                   "changed": job.get("changes", {}).get("changed"), "error": (res.get("error") or "")[:800],
                   "notes": res.get("notes")})

    def _rebuild_text(self, job: dict) -> str:
        if "res" not in job:
            return (f"status: running ({time.time() - job['started']:.0f} s; make verilog of the whole SoC usually takes "
                    "a few minutes)\ncall rebuild_verilog again to keep waiting (it does not start a second build "
                    "while one is running)")
        res = job["res"]
        if not res.get("ok"):
            return (f"status: FAILED after {res.get('seconds', 0):.0f} s\n{res.get('error')}\n"
                    "Fix your edit and call rebuild_verilog again.")
        c = job["changes"]
        return (f"status: ok ({res['seconds']:.0f} s)\n"
                f"modules changed vs {self._parent_name}: {', '.join(c['changed']) or '(none: your edit does not change the Verilog)'}\n"
                + (f"added: {', '.join(c['added'][:30])}\n" if c["added"] else "")
                + (f"removed: {', '.join(c['removed'][:30])}\n" if c["removed"] else "")
                + f"next: start_ab(module) on the smallest changed module that holds the path you target "
                  f"({self._cfg['max_runs'] - self._runs} A/B left in this session)")

    # -- MCP -----------------------------------------------------------------

    def rebuild_verilog(self, max_wait_seconds: int = MAX_WAIT) -> str:
        """Elaborate your CURRENT edits (the diff of chipyard/ against the original) to Verilog: they are applied to
        the real Chipyard tree, `make verilog` runs (no simulator build), and the tree is restored. Reports compile
        errors, or which modules your edit changed. Waits up to max_wait_seconds (max 60); if it says `running`,
        call it again. Run it after every edit, before start_ab."""
        with self._lock:
            job = self._rebuild
            if job is None or "res" in job:
                patch, phash = self._patch()
                if not patch.strip():
                    return "ERROR: you have not edited anything under chipyard/ yet."
                if job is not None and job["phash"] == phash:
                    return self._rebuild_text(job)   # bản này đã build (được hoặc lỗi): không build lại
                c = self._cfg
                job = {"started": time.time(), "phash": phash,
                       "ref": ab_make_verilog.chia_remote(c["chipyard"], c["config"], patch, c["subs"], c["make_jobs"])}
                self._rebuild = job
        if "res" not in job:
            ready, _ = ray.wait([job["ref"]], timeout=max(0, min(int(max_wait_seconds), MAX_WAIT)))
            if ready:
                with self._lock:
                    if "res" not in job:
                        self._finish_rebuild(job)
        return self._rebuild_text(job)

    def list_modules(self, pattern: str = "", only_changed: bool = False) -> str:
        """Modules under DigitalTop (from your last good rebuild, else from the current head): name, Verilog lines,
        number of SRAM macros below it, and `*` if your edit changed it. `pattern` is a regex filter."""
        child = self._child
        rtl = child["rtl"] if child else self._parent_rtl
        changed = set(child["changes"]["changed"] + child["changes"]["added"]) if child else set()
        rows = []
        for m in sorted(rtl.modules):
            if pattern and not re.search(pattern, m):
                continue
            if only_changed and m not in changed:
                continue
            rows.append(f"{'*' if m in changed else ' '} {m:48s} {rtl.modules[m].count(chr(10)):7d} lines")
        side = "your last rebuild" if child else f"{self._parent_name} (no rebuild yet)"
        return (f"modules of {side} (* = changed by your edit; {len(rows)} shown, max 300):\n"
                + ("\n".join(rows[:300]) or "(none)"))

    def start_ab(self, module: str) -> str:
        """ORFS synth + first floorplan step (timing repair by sizing, no buffering, no placement, no wires) of ONE
        module: your last rebuild (child) and the current head (parent), at the A/B clock period. Only
        register-to-register paths inside the module count (ports get 0 delay and are left out). Takes a few minutes
        per side; returns a handle for ab_result. Limited number per session (see rebuild_verilog)."""
        c = self._cfg
        with self._lock:
            if self._rebuild is not None and "res" not in self._rebuild:
                return "ERROR: a rebuild is running. Call rebuild_verilog until it says ok first."
            child = self._child
            if child is None:
                return "ERROR: no good rebuild yet. Call rebuild_verilog first."
            if self._patch()[1] != child["phash"]:
                return "ERROR: you edited files since your last rebuild, so its Verilog is stale. Call rebuild_verilog."
            if module not in child["rtl"].modules:
                close = [m for m in child["rtl"].modules if module.lower() in m.lower()][:15]
                return f"ERROR: module {module!r} is not under {S.TOP}. Similar: {close or '(none)'}; see list_modules."
            if module not in self._parent_rtl.modules:
                return (f"ERROR: {module} does not exist in the head, so there is nothing to compare with. Pick the "
                        "existing module that instantiates it.")
            try:
                c_sub = S.sub_rtl(child["rtl"], module)
                p_sub = S.sub_rtl(self._parent_rtl, module)
            except Exception as e:                   # noqa: BLE001
                return f"ERROR: cannot extract {module}: {e}"
            if c_sub.hash == p_sub.hash:
                return (f"ERROR: your edit does not change {module} or anything below it. Pick a module that contains "
                        "a changed one (list_modules(only_changed=True)).")
            self._n += 1
            handle = f"ab{self._n}"
            if c_sub.hash in self._memo:
                self._jobs[handle] = {"module": module, "text": "ALREADY MEASURED (this exact Verilog):\n"
                                      + self._memo[c_sub.hash]}
                return f"handle={handle}: already measured, call ab_result(handle=\"{handle}\")"
            if self._runs >= c["max_runs"]:
                return (f"ERROR: A/B budget of this session used ({c['max_runs']}). Finish with your best judgement; "
                        "the loop measures the final patch after place.")
            self._runs += 1
            job = {"module": module, "key": c_sub.hash, "started": time.time(),
                   "child": ab_orfs.chia_remote(c_sub, c), "n_modules": len(c_sub.modules), "srams": len(c_sub.srams)}
            if module not in self._parent_refs or self._parent_refs[module][0] != p_sub.hash:
                self._parent_refs[module] = (p_sub.hash, ab_orfs.chia_remote(p_sub, c))
            job["parent"] = self._parent_refs[module][1]
            self._jobs[handle] = job
        self._log({"event": "start", "handle": handle, "module": module, "child_hash": c_sub.hash[:12]})
        return (f"started: handle={handle}, module={module} ({job['n_modules']} modules, {job['srams']} SRAM macro types), "
                f"clock {c['period_ns']:g} ns; child and parent run one after the other (a few minutes each)\n"
                f"poll with ab_result(handle=\"{handle}\", max_wait_seconds=60)")

    def ab_result(self, handle: str, max_wait_seconds: int = MAX_WAIT) -> str:
        """Result of start_ab: worst register-to-register slack of child and parent at the A/B period, the delta
        (child - parent; positive = your edit shortened the worst path), and the worst endpoint families of both.
        Waits up to max_wait_seconds (max 60); if it says `running`, call it again."""
        job = self._jobs.get(handle)
        if job is None:
            return f"ERROR: unknown handle {handle!r} (known: {', '.join(self._jobs) or 'none'})"
        if "text" in job:
            return job["text"]
        refs = [job["child"], job["parent"]]
        ready, _ = ray.wait(refs, num_returns=2, timeout=max(0, min(int(max_wait_seconds), MAX_WAIT)))
        if len(ready) < 2:
            return (f"status: running ({time.time() - job['started']:.0f} s, {len(ready)}/2 sides done)\n"
                    f"call ab_result(handle=\"{handle}\", max_wait_seconds=60) again")
        with self._lock:
            if "text" in job:
                return job["text"]
            try:
                cr, pr = get(job["child"]), get(job["parent"])
            except Exception as e:                   # noqa: BLE001
                cr = pr = None
                job["text"] = f"status: FAILED ({type(e).__name__}: {str(e)[:1500]})"
            if "text" not in job:
                job["text"] = self._result_text(handle, job, cr, pr)
            return job["text"]

    def _result_text(self, handle: str, job: dict, cr, pr) -> str:
        c, p = _side(cr), _side(pr)
        per = self._cfg["period_ns"]
        L = [f"status: done ({time.time() - job['started']:.0f} s), module {job['module']}, clock {per:g} ns"]
        for side, x in (("parent (" + self._parent_name + ")", p), ("child (your edit)", c)):
            L.append(f"{side}: " + (f"worst reg-to-reg slack {x['slack_ns']:+.3f} ns, path {x['path_ns']:.3f} ns, "
                                     f"std area {x['area_um2']} um2, {x['crit']}" if x["ok"] else f"FAILED: {x['error']}"))
        delta = None
        if c["ok"] and p["ok"]:
            delta = round(c["slack_ns"] - p["slack_ns"], 4)
            verdict = ("better" if delta > NOISE_NS else "worse" if delta < -NOISE_NS
                       else f"no change (|delta| <= {NOISE_NS} ns)")
            L.append(f"delta worst slack (child - parent): {delta:+.3f} ns -> {verdict}")
            L += ["worst endpoint families (slack in ns; delta positive = shorter):",
                  compare_families(pr.endpoints, cr.endpoints)]
            d = Path(self._ws) / "ab" / handle
            d.mkdir(parents=True, exist_ok=True)
            for name, r in (("child", cr), ("parent", pr)):
                (d / f"{name}_paths.rpt").write_text(r.paths or "")
                (d / f"{name}_families.txt").write_text(r.families or "")
            L.append(f"full path reports: ab/{handle}/child_paths.rpt, ab/{handle}/parent_paths.rpt")
        elif not c["ok"]:
            self._runs = max(0, self._runs - 1)      # lỗi của tool/ORFS không tính vào ngân sách (đang giữ _lock)
            L.append("(this failed run does not count against your A/B budget)")
        text = "\n".join(L)
        if c["ok"]:
            self._memo[job["key"]] = text
        self._log({"event": "ab", "handle": handle, "module": job["module"], "child": c, "parent": p,
                   "delta_ns": delta, "period_ns": per})
        return text
