"""loop.py: một đợt vòng tối ưu Fmax (M2: agent bị bypass; M3/M4 thay bằng agent thật).

Mỗi vòng:
  1. agent đề xuất một patch (unified diff, đường dẫn tính từ gốc Chipyard, có thể trải qua nhiều submodule);
     patch rỗng thì hỏi lại tối đa --empty-retries lần;
  2. áp best.patch (các vòng đã nhận) + patch của vòng lên ~/tools/chipyard, build (tag theo digest cây);
     build lỗi -> agent sửa build (một lần) -> vẫn lỗi thì loại;
  3. RTL hash đã đo (baseline hoặc vòng trước) -> bỏ, không đo lại;
  4. gate (policy.gate_check) + func_check (so vết với baseline) + chi phí cycle; gate hỏng -> agent debug (ghi
     lại chẩn đoán), loại;
  5. synth (bộ lọc mức 1, policy.synth_filter) -> floorplan -> place -> policy.decide (so với bản tốt nhất,
     ngưỡng nhiễu từ noise.json);
  6. hoàn nguyên cây (git apply -R theo từng submodule), kiểm digest cây bằng digest baseline: khác thì dừng đợt.
Kết quả ở ~/soc-fmax-runs/<run>/: history.md, history.json, best.patch, results.csv, runs/r<N>/.

Agent M2 (--bypass, mỗi vòng một mục, lặp lại nếu ít hơn số vòng):
  empty         patch rỗng (kiểm đường hỏi lại / vòng rỗng)
  comment       thêm một dòng chú thích cuối RocketCore.scala (RTL hash không đổi -> kiểm "không đo lại")
  broken        thêm một dòng Scala sai cuối RocketCore.scala (kiểm đường build lỗi -> agent sửa build)
  file:<path>   patch có sẵn (đường dẫn trên host)
Các patch giả sinh lúc chạy từ nội dung file thật, nên luôn áp được vào cây hiện tại.

Chạy (README.md, mục M2):
  chia job submit --no-wait --submission-id m2-loop --working-dir . -- python loop.py --run-name m2-loop \\
      --noise-json ~/soc-fmax-runs/m2-noise/noise.json --rounds 3 --bypass empty,comment,broken
"""
from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import os
import re
import subprocess
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ray

import func_check as F
import orfs_nodes as O
import policy as P
import soc_fmax as D
import soc_nodes as S
from memwatch import MemWatch
from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector

BYPASS_FILE = "generators/rocket-chip/src/main/scala/rocket/RocketCore.scala"
AGENT_FNS = ("agent_propose", "agent_fix_build", "agent_debug")


# ---------------------------------------------------------------------------
# Agent (M2: chỉ bypass; thân hàm thật viết ở M3/M4 với ClaudeCodeLLM trên khối claude)
# ---------------------------------------------------------------------------

@ChiaFunction(resources={"claude": 1}, max_retries=0)
def agent_propose(ctx: dict) -> dict:
    """-> {"patch": unified diff, "summary": str}."""
    raise NotImplementedError("agent thật chưa có (M3/M4): chạy với --bypass")


@ChiaFunction(resources={"claude": 1}, max_retries=0)
def agent_fix_build(ctx: dict) -> dict:
    """ctx có patch và lỗi build. -> {"patch": patch thay thế (rỗng = bỏ cuộc), "summary"}."""
    raise NotImplementedError("agent thật chưa có (M3/M4): chạy với --bypass")


@ChiaFunction(resources={"claude": 1}, max_retries=0)
def agent_debug(ctx: dict) -> dict:
    """ctx có patch và lý do gate/func hỏng. -> {"diagnosis": str} (chỉ ghi vào history, không sửa)."""
    raise NotImplementedError("agent thật chưa có (M3/M4): chạy với --bypass")


def bypass_agent_provider(tag, data_path, ctx, *args, **kwargs) -> dict:
    """Thay LLM ở M2: trả câu trả lời dựng sẵn mà driver đặt trong ctx["bypass_reply"]."""
    return dict(ctx.get("bypass_reply") or {})


def bypass_reply(kind: str, cy: str, fn: str) -> dict:
    """Câu trả lời giả của agent cho mục --bypass `kind`."""
    if fn == "agent_debug":
        return {"diagnosis": f"(bypass {kind}: không chẩn đoán)"}
    if fn == "agent_fix_build":
        return {"patch": "", "summary": f"(bypass {kind}: không sửa build)"}
    if kind == "empty":
        return {"patch": "", "summary": "(bypass: patch rỗng)"}
    if kind.startswith("file:"):
        path = os.path.expanduser(kind[5:])
        return {"patch": Path(path).read_text(), "summary": f"(bypass: patch {path})"}
    if kind in ("comment", "broken"):
        text = (Path(cy) / BYPASS_FILE).read_text()
        add = ("// soc-fmax M2 bypass: chú thích, không đổi RTL\n" if kind == "comment"
               else "this is not scala // soc-fmax M2 bypass: build phải lỗi\n")
        new = text + ("" if text.endswith("\n") else "\n") + add
        diff = "".join(difflib.unified_diff(text.splitlines(True), new.splitlines(True),
                                            f"a/{BYPASS_FILE}", f"b/{BYPASS_FILE}"))
        return {"patch": f"diff --git a/{BYPASS_FILE} b/{BYPASS_FILE}\n" + diff,
                "summary": f"(bypass: {kind} cuối {BYPASS_FILE})"}
    raise SystemExit(f"--bypass không biết mục {kind!r} (empty, comment, broken, file:<path>)")


# ---------------------------------------------------------------------------
# Patch trên cây Chipyard (nhiều submodule)
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str, inp: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, input=inp, capture_output=True, text=True, timeout=600)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} ({cwd}) lỗi {r.returncode}:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r


def split_patch(patch: str, subs: list[str]) -> dict[str, str]:
    """Tách patch theo submodule chứa file (tiền tố dài nhất), viết lại đường dẫn tương đối với submodule đó.
    Khoá "" là repo gốc. Mỗi file cần header `diff --git` hoặc `--- a/...`."""
    head = "diff --git " if re.search(r"^diff --git ", patch, re.M) else "--- "
    sections, cur = [], []
    for line in patch.splitlines(True):
        if line.startswith(head) and cur:
            sections.append(cur)
            cur = []
        cur.append(line)
    if cur:
        sections.append(cur)
    subs = sorted(subs, key=len, reverse=True)
    out: dict[str, list[str]] = {}
    for sec in sections:
        path = None
        for l in sec:
            m = re.match(r"^(?:\+\+\+ b/|--- a/|diff --git a/)(\S+)", l)
            if m and m.group(1) != "/dev/null":
                path = m.group(1)
                break
        if path is None:
            if any(l.strip() for l in sec):
                raise ValueError("patch có đoạn không đọc được đường dẫn file:\n" + "".join(sec[:5]))
            continue
        sub = next((s for s in subs if path == s or path.startswith(s + "/")), "")
        pre = sub + "/" if sub else ""

        def strip(l: str) -> str:
            if not pre:
                return l
            return (l.replace(f" a/{pre}", " a/").replace(f" b/{pre}", " b/")
                    .replace(f"--- a/{pre}", "--- a/").replace(f"+++ b/{pre}", "+++ b/")
                    .replace(f"rename from {pre}", "rename from ").replace(f"rename to {pre}", "rename to "))
        out.setdefault(sub, []).extend(strip(l) if l.startswith(("diff --git", "--- ", "+++ ", "rename ")) else l
                                       for l in sec)
    return {k: "".join(v) for k, v in out.items()}


class Applied:
    """Patch đã áp (theo thứ tự) để hoàn nguyên ngược lại."""

    def __init__(self, cy: str):
        self.cy, self.done = cy, []          # [(dir, patch text)]

    def apply(self, patch: str, subs: list[str]):
        parts = split_patch(patch, subs)
        for sub in parts:                    # --check hết trước khi áp cái nào
            d = os.path.join(self.cy, sub)
            r = _git(["apply", "--check", "--whitespace=nowarn", "-"], d, parts[sub], check=False)
            if r.returncode != 0:
                raise PatchError(f"patch không áp được vào {sub or '(gốc)'}:\n{r.stderr[-2000:]}")
        for sub, text in parts.items():
            _git(["apply", "--whitespace=nowarn", "-"], os.path.join(self.cy, sub), text)
            self.done.append((os.path.join(self.cy, sub), text))

    def revert(self):
        errs = []
        while self.done:
            d, text = self.done.pop()
            r = _git(["apply", "-R", "--whitespace=nowarn", "-"], d, text, check=False)
            if r.returncode != 0:
                errs.append(f"{d}: {r.stderr[-500:]}")
        if errs:
            raise RuntimeError("không hoàn nguyên được patch:\n" + "\n".join(errs))


class PatchError(Exception):
    pass


def tree_diff(cy: str, subs: list[str]) -> str:
    """Patch tổng của cây hiện tại so với HEAD (mọi submodule build), đường dẫn tính từ gốc Chipyard."""
    out = []
    for sub in [""] + sorted(s for s in subs if re.fullmatch(S.BUILD_SUBMODULE_RE, s)):
        d = os.path.join(cy, sub)
        args = ["diff", "HEAD", "--ignore-submodules=all", "--binary"]
        if sub:
            args += [f"--src-prefix=a/{sub}/", f"--dst-prefix=b/{sub}/"]
        out.append(_git(args, d).stdout)
    return "".join(out)


# ---------------------------------------------------------------------------
# Lịch sử
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Round:
    n: int
    bypass: str = ""
    summary: str = ""
    patch_stat: str = ""
    status: str = ""              # rỗng | patch lỗi | build lỗi | RTL trùng | gate | func | synth | place | lỗi
    accepted: bool = False
    reasons: list = dataclasses.field(default_factory=list)
    point: dict = dataclasses.field(default_factory=dict)
    rtl_hash: str = ""
    diagnosis: str = ""
    minutes: float = 0.0
    attempts: int = 1


def patch_stat(patch: str) -> str:
    files = re.findall(r"^\+\+\+ b/(\S+)", patch, re.M)
    add = len(re.findall(r"^\+(?!\+\+)", patch, re.M))
    rem = len(re.findall(r"^-(?!--)", patch, re.M))
    return f"{len(files)} file, +{add} −{rem}: " + ", ".join(files[:6]) + (" ..." if len(files) > 6 else "")


def history_md(a, base: P.Point, best: P.Point, noise: P.Noise, rounds: list[Round]) -> str:
    L = [f"# soc-fmax loop {a.run_name}", "",
         f"- Cập nhật {datetime.now(timezone.utc).isoformat(timespec='seconds')}, chu kỳ {a.period_ns} ns",
         f"- Baseline: Fmax {D.fmt(base.fmax, 3)} MHz, điểm họ {D.fmt(base.family)}, logic {D.fmt(base.area, 0)} um2",
         f"- Bản tốt nhất: **{best.name}**, Fmax {D.fmt(best.fmax, 3)} MHz, điểm họ {D.fmt(best.family)}",
         f"- Ngưỡng nhiễu: Fmax ±{noise.fmax_mhz:.3f} MHz, điểm họ ±{noise.family_ns:.3f} ({noise.note})", "",
         "| vòng | agent | patch | kết quả | Fmax | điểm họ | cycle | logic | phút |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rounds:
        p = r.point or {}
        who = ("file:" + os.path.basename(r.bypass[5:])) if r.bypass.startswith("file:") else (r.bypass or "agent")
        L.append(f"| {r.n} | {who} | {r.patch_stat} | {'**NHẬN**' if r.accepted else r.status} | "
                 f"{D.fmt(p.get('fmax'), 3)} | {D.fmt(p.get('family'))} | {D.fmt(p.get('cycle_cost'), 4)} | "
                 f"{D.fmt(p.get('area'), 0)} | {r.minutes:.1f} |")
    for r in rounds:
        L += ["", f"## Vòng {r.n}: {'NHẬN' if r.accepted else r.status}", "", f"- Agent: {r.summary}",
              f"- Patch: {r.patch_stat or '-'} (lần hỏi: {r.attempts})", f"- RTL hash: `{r.rtl_hash[:12] or '-'}`"]
        L += [f"- {x}" for x in r.reasons]
        if r.diagnosis:
            L.append(f"- Chẩn đoán: {r.diagnosis}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Một vòng
# ---------------------------------------------------------------------------

class Ctx:
    """Trạng thái của đợt, dùng chung giữa các vòng."""

    def __init__(self, a, mw, tm, fakeram, base_state, base_art, base_rtl, base_gate, func_base, base_stages, noise):
        self.a, self.mw, self.tm, self.fakeram = a, mw, tm, fakeram
        self.base_state, self.base_art, self.base_rtl = base_state, base_art, base_rtl
        self.base_gate, self.func_base, self.base_stages, self.noise = base_gate, func_base, base_stages, noise
        self.subs = sorted(base_state["subs"])
        self.budget = P.Budget(cycle=a.cycle_budget, area=a.area_budget, synth_margin=a.synth_margin)
        self.base = P.Point(name="baseline", rtl_hash=base_rtl.hash, gate_ok=True, func_ok=True, cycle_cost=1.0,
                            synth_fmax=base_stages["synth"].fmax_mhz, synth_family=base_stages["synth"].family_score,
                            fmax=base_stages["place"].fmax_mhz, family=base_stages["place"].family_score,
                            area=base_stages["place"].std_area_um2, period_ns=a.period_ns)
        self.best, self.best_patch = self.base, ""
        self.measured = {base_rtl.hash: "baseline"}
        self.rounds: list[Round] = []


def ask(fn_name: str, fn, ctx_in: dict, kind: str, a, tag: str) -> dict:
    if a.bypass:
        ctx_in = {**ctx_in, "bypass_reply": bypass_reply(kind, a.chipyard, fn_name)}
    return get(fn.chia_remote(ctx_in, _chia_tag=tag))


def agent_context(c: Ctx, n: int) -> dict:
    """Thông tin agent thật sẽ cần (M3/M4); ở M2 chỉ để ghi lại."""
    return {"round": n, "period_ns": c.a.period_ns, "best": c.best.as_dict(), "baseline": c.base.as_dict(),
            "noise": {"fmax_mhz": c.noise.fmax_mhz, "family_ns": c.noise.family_ns},
            "budget": dataclasses.asdict(c.budget), "best_patch": c.best_patch,
            "history": [{"n": r.n, "status": r.status, "accepted": r.accepted, "reasons": r.reasons[:4],
                         "patch_stat": r.patch_stat} for r in c.rounds],
            "families": c.base_stages["place"].families}


def run_round(c: Ctx, n: int, kind: str) -> Round:
    a = c.a
    rd = Round(n=n, bypass=kind)
    d = Path(a.out) / "runs" / f"r{n}"
    d.mkdir(parents=True, exist_ok=True)
    tag_base = f"agent_{'bypass_' if a.bypass else ''}{a.run_name}_r{n}"
    ctx_in = agent_context(c, n)
    applied = Applied(a.chipyard)
    try:
        if c.best_patch:                     # agent nhìn thấy (và sửa trên) cây đã có các vòng được nhận
            try:
                applied.apply(c.best_patch, c.subs)
            except PatchError as e:
                raise SystemExit(f"best.patch không áp được vào cây hiện tại: {e}")
        return _round_body(c, n, kind, rd, d, tag_base, ctx_in, applied)
    finally:
        applied.revert()
        after = get(S.tree_state.chia_remote(a.chipyard))
        if after["digest"] != c.base_state["digest"]:
            raise SystemExit(f"cây Chipyard sau khi hoàn nguyên vòng {n} KHÁC baseline (digest {after['digest'][:12]} ≠ "
                             f"{c.base_state['digest'][:12]}): dừng đợt. Xem `git -C {a.chipyard} submodule foreach git status`.")


def _round_body(c: Ctx, n: int, kind: str, rd: Round, d: Path, tag_base: str, ctx_in: dict, applied: Applied) -> Round:
    a = c.a
    prop = {}
    for attempt in range(1, a.empty_retries + 2):
        rd.attempts = attempt
        prop = ask("agent_propose", agent_propose, {**ctx_in, "attempt": attempt}, kind, a, f"{tag_base}_p{attempt}")
        if (prop.get("patch") or "").strip():
            break
        print(f"  agent trả patch rỗng (lần {attempt})", flush=True)
    rd.summary = prop.get("summary", "")
    patch = prop.get("patch") or ""
    D.write(d / "proposal.patch", patch)
    if not patch.strip():
        rd.status = "rỗng"
        rd.reasons = [f"patch rỗng sau {rd.attempts} lần hỏi"]
        return rd
    rd.patch_stat = patch_stat(patch)
    print(f"  patch: {rd.patch_stat}", flush=True)
    if True:
        try:
            applied.apply(patch, c.subs)
        except PatchError as e:
            rd.status, rd.reasons = "patch lỗi", [str(e)[:1500]]
            return rd
        state, art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=False)
        if rtl is None:
            err = D.build_errors(art)
            print("  build lỗi:\n    " + "\n    ".join(err.splitlines()[:15]), flush=True)
            fix = ask("agent_fix_build", agent_fix_build, {**ctx_in, "patch": patch, "build_errors": err}, kind, a,
                      f"{tag_base}_fix")
            if not (fix.get("patch") or "").strip():
                rd.status, rd.reasons = "build lỗi", ["build lỗi, agent không sửa", err[-1500:]]
                return rd
            applied.revert()
            if c.best_patch:
                applied.apply(c.best_patch, c.subs)
            try:
                applied.apply(fix["patch"], c.subs)
            except PatchError as e:
                rd.status, rd.reasons = "build lỗi", [f"patch sửa build không áp được: {e}"[:1500]]
                return rd
            patch = fix["patch"]
            rd.patch_stat = patch_stat(patch) + " (sau sửa build)"
            D.write(d / "proposal_fixed.patch", patch)
            state, art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=False)
            if rtl is None:
                rd.status, rd.reasons = "build lỗi", ["build vẫn lỗi sau khi agent sửa", D.build_errors(art)[-1500:]]
                return rd
        rd.rtl_hash = rtl.hash
        cand_patch = tree_diff(a.chipyard, c.subs)
        D.write(d / "tree.patch", cand_patch)
        if rtl.hash in c.measured:
            rd.status = "RTL trùng"
            rd.reasons = [f"RTL hash {rtl.hash[:12]} đã đo ({c.measured[rtl.hash]}): không đo lại"]
            return rd
        c.measured[rtl.hash] = f"vòng {n}"
        pt = P.Point(name=f"r{n}", rtl_hash=rtl.hash, period_ns=a.period_ns)
        g = D.step_gate(a, d, art, rtl, c.mw, c.tm, baseline=False, elfs=c.base_gate["elfs"])
        pt.gate_ok, pt.gate_reasons = P.gate_check(c.base_gate["gate"], g["gate"], c.base_gate["known_fail"])
        pt.cycle_cost = S.cycle_cost(c.base_gate["gate"], g["gate"])
        if pt.gate_ok:
            fr = D.step_func(a, d, art, rtl, c.base_gate["elfs"], c.mw, c.tm, base=c.func_base,
                             base_hash=c.base_rtl.hash)
            pt.func_ok = fr.ok
            pt.func_reasons = [f"{b}: {r.message}" for b, r in fr.benches.items() if not r.ok]
        rd.point = pt.as_dict()
        if not pt.gate_ok or not pt.func_ok:
            dg = ask("agent_debug", agent_debug, {**ctx_in, "patch": patch,
                                                  "failures": pt.gate_reasons + pt.func_reasons}, kind, a, f"{tag_base}_dbg")
            rd.diagnosis = dg.get("diagnosis", "")
        if not pt.gate_ok or not pt.func_ok or pt.cycle_cost is None or pt.cycle_cost > c.budget.cycle:
            dec = P.decide(c.base, c.best, pt, c.noise, c.budget)
            rd.status, rd.reasons = dec.stage, dec.reasons
            return rd

        def synth_gate(r):
            pt.synth_fmax, pt.synth_family = r.fmax_mhz, r.family_score
            go, why = P.synth_filter(c.best, pt, c.noise, c.budget)
            print(f"  bộ lọc synth: {'đi tiếp' if go else 'dừng'}: {why}", flush=True)
            return "" if go else why

        _prep, st = D.run_orfs_point(a, d, rtl, c.fakeram, a.orfs_vars, f"r{n}", "place", c.mw, c.tm, fatal=False,
                                     stop_after_synth=synth_gate)
        last = list(st.values())[-1]
        if not last.ok:
            rd.status, rd.reasons = "lỗi", [f"ORFS {last.stage}: {last.error[:1500]}"]
            rd.point = pt.as_dict()
            return rd
        if "place" not in st:
            rd.status, rd.reasons = "synth", [P.synth_filter(c.best, pt, c.noise, c.budget)[1]]
            rd.point = pt.as_dict()
            return rd
        pt.fmax, pt.family, pt.area = st["place"].fmax_mhz, st["place"].family_score, st["place"].std_area_um2
        rd.point = pt.as_dict()
        dec = P.decide(c.base, c.best, pt, c.noise, c.budget)
        rd.accepted, rd.status, rd.reasons = dec.accept, ("nhận" if dec.accept else dec.stage), dec.reasons
        if dec.accept:
            c.best, c.best_patch = pt, cand_patch
        return rd


# ---------------------------------------------------------------------------

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    D.add_common_args(ap)
    ap.add_argument("--rounds", type=int, default=5, help="số vòng của đợt (mặc định 5)")
    ap.add_argument("--bypass", default="", help="M2: thay agent bằng câu trả lời dựng sẵn, vd empty,comment,broken")
    ap.add_argument("--empty-retries", type=int, default=2, help="hỏi lại agent bao nhiêu lần khi patch rỗng")
    ap.add_argument("--noise-json", required=True, help="noise.json của soc_fmax.py --noise")
    ap.add_argument("--history-from", default="", help="thư mục run trước (history.json, best.patch) để đi tiếp")
    ap.add_argument("--cycle-budget", type=float, default=1.03)
    ap.add_argument("--area-budget", type=float, default=1.15)
    ap.add_argument("--synth-margin", type=float, default=0.05)
    a = ap.parse_args()
    D.finish_args(a, ap)
    a.bypass_list = [x.strip() for x in a.bypass.split(",") if x.strip()]
    return a


def load_noise(path: str) -> P.Noise:
    j = json.loads(Path(os.path.expanduser(path)).read_text())
    return P.Noise(**j)


def main():
    a = parse_args()
    a.out = str(Path(os.path.expanduser(a.out_dir)) / a.run_name)
    out = Path(a.out)
    d0 = out / "runs" / "baseline"
    d0.mkdir(parents=True, exist_ok=True)
    D.write(out / "args.json", json.dumps(vars(a), indent=2, default=str))
    noise = load_noise(a.noise_json)

    ray.init(address="auto", namespace="soc_fmax", ignore_reinit_error=True)
    start_collector(log_dir=str(out / "profile"))
    start_cache(size=a.cache_gb, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(D.EXP_YAML))
    bypass = Bypass(yaml_path=str(D.EXP_YAML))
    for fn in D.CACHED_FNS:
        bypass.set_provider(fn, S.cache_provider)
        bypass.set_cond(fn, S.cache_hit_cond)
    for fn in AGENT_FNS:
        bypass.set_provider(fn, bypass_agent_provider)
    mw = MemWatch().start()
    tm = D.Timer()
    c = None
    try:
        res = ray.cluster_resources()
        need = ["chipyard", "verilator_run", "asic", "claude"]   # agent bypass vẫn chạy trên khối claude
        missing = [r for r in need if not res.get(r)]
        if missing:
            raise SystemExit(f"cluster thiếu resource {missing}")
        if not a.bypass_list:
            raise SystemExit("M2 chưa có agent thật: chạy với --bypass empty,comment,broken")
        print("== baseline (phải có sẵn trong cache: soc_fmax.py --until place)", flush=True)
        state, art, rtl = D.step_rtl(a, d0, mw, tm)
        gate = D.step_gate(a, d0, art, rtl, mw, tm)
        func_base = D.step_func(a, d0, art, rtl, gate["elfs"], mw, tm)
        fakeram = D.load_fakeram(a.repo)
        _prep, stages = D.run_orfs_point(a, d0, rtl, fakeram, a.orfs_vars, "baseline", "place", mw, tm)
        c = Ctx(a, mw, tm, fakeram, state, art, rtl, gate, func_base, stages, noise)
        start = 1
        if a.history_from:
            hdir = Path(os.path.expanduser(a.history_from))
            h = json.loads((hdir / "history.json").read_text())
            c.rounds = [Round(**r) for r in h["rounds"]]
            c.best = P.Point(**h["best"])
            c.best_patch = (hdir / "best.patch").read_text() if (hdir / "best.patch").exists() else ""
            c.measured.update(h.get("measured", {}))
            start = max((r.n for r in c.rounds), default=0) + 1
            print(f"đi tiếp từ {hdir}: {len(c.rounds)} vòng, bản tốt nhất {c.best.name} ({D.fmt(c.best.fmax, 3)} MHz)", flush=True)
        print(f"ngưỡng nhiễu: Fmax ±{noise.fmax_mhz:.3f} MHz, điểm họ ±{noise.family_ns:.3f}; ngân sách {c.budget}", flush=True)
        for i in range(a.rounds):
            n = start + i
            kind = a.bypass_list[i % len(a.bypass_list)] if a.bypass_list else ""
            print(f"\n######## vòng {n}{f' (bypass {kind})' if kind else ''}", flush=True)
            t0 = time.time()
            try:
                rd = run_round(c, n, kind)
            except SystemExit:
                raise
            except Exception as e:      # noqa: BLE001  một vòng hỏng không làm mất cả đợt
                traceback.print_exc()
                rd = Round(n=n, bypass=kind, status="lỗi", reasons=[f"{type(e).__name__}: {e}"[:1500]])
            rd.minutes = (time.time() - t0) / 60
            c.rounds.append(rd)
            print(f"vòng {n}: {'NHẬN' if rd.accepted else rd.status} ({rd.minutes:.1f} phút)", flush=True)
            for x in rd.reasons[:6]:
                print("    " + x.splitlines()[0][:300] if x else "", flush=True)
            save(out, a, c)
    except SystemExit as e:
        print(f"\nDỪNG: {e}", flush=True)
        raise
    finally:
        if c is not None:
            save(out, a, c)
        mw.stop()
        print("\n## Thời gian", flush=True)
        for row in tm.rows:
            print(f"  {row['step']:10s} {row['minutes']:8.2f} phút  {'(cache)' if row['cached'] else '       '}  {row['note']}", flush=True)
        print(f"\nKết quả: {out}/history.md, {out}/best.patch, {out}/results.csv", flush=True)
        stop_collector()
        stop_cache()


def save(out: Path, a, c: Ctx):
    D.write(out / "history.json", json.dumps({"rounds": [dataclasses.asdict(r) for r in c.rounds],
                                              "best": c.best.as_dict(), "measured": c.measured}, indent=2, default=str))
    D.write(out / "best.patch", c.best_patch)
    D.write(out / "history.md", history_md(a, c.base, c.best, c.noise, c.rounds))


if __name__ == "__main__":
    main()
