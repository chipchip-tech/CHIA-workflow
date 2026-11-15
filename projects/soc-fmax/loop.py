"""loop.py: một đợt vòng tối ưu Fmax. Agent thật (agent.py, Claude Code trên khối claude) từ M3; --bypass thay
agent bằng câu trả lời dựng sẵn (M2).

--phase sim (M3): chỉ đo đúng/cycle, không ORFS; vòng qua gate + func + ngân sách cycle thành ỨNG VIÊN
  (candidates.json), không cộng dồn (vòng nào cũng đi từ baseline).
--phase asic (M2, M4): đủ các bước dưới. Mặc định gate ở CUỐI ĐỢT: mỗi vòng chỉ build + ORFS; vòng nào sau place tốt
  hơn bản tốt nhất (vượt nhiễu) thì `chờ gate`; cuối đợt xếp theo Fmax, gate vòng đầu, hỏng thì vòng kế tiếp; vòng
  đầu tiên qua gate + func + ngân sách được nhận, các vòng sau ghi `chưa gate`. --gate-each: gate mọi vòng trước ORFS.

Mỗi vòng:
  1. agent đề xuất một patch (unified diff, đường dẫn tính từ gốc Chipyard, có thể trải qua nhiều submodule);
     patch rỗng thì hỏi lại tối đa --empty-retries lần; không được tạo/xoá file hay sửa file chưa track;
  2. áp best.patch (các vòng đã nhận) + patch của vòng lên ~/tools/chipyard, build (tag theo digest cây);
     build lỗi -> agent sửa build (tối đa --fix-retries lần) -> vẫn lỗi thì loại;
  3. RTL hash đã đo (baseline hoặc vòng trước) -> bỏ, không đo lại;
  4. (--gate-each / --phase sim) gate (policy.gate_check) + func_check (so vết với baseline) + chi phí cycle; gate
     hỏng -> agent debug (ghi lại chẩn đoán), loại; mặc định bước này dời xuống cuối đợt (xem trên);
  5. synth (bộ lọc mức 1, policy.synth_filter) -> floorplan -> place -> policy.decide (so với bản tốt nhất,
     ngưỡng nhiễu từ noise.json);
  6. hoàn nguyên cây (git apply -R theo từng submodule), kiểm digest cây bằng digest baseline: khác thì dừng đợt.
Kết quả ở ~/soc-fmax-runs/<run>/: history.md, history.json, best.patch, candidates.json, runs/r<N>/ (patch, log
agent). Cuối job chỉ giữ kết quả ORFS (flow/results|logs|reports|objects) của bản tốt nhất (--keep-orfs để giữ hết).

Agent M2 (--bypass, mỗi vòng một mục, lặp lại nếu ít hơn số vòng):
  empty         patch rỗng (kiểm đường hỏi lại / vòng rỗng)
  comment       thêm một dòng chú thích cuối RocketCore.scala (RTL hash không đổi -> kiểm "không đo lại")
  broken        thêm một dòng Scala sai cuối RocketCore.scala (kiểm đường build lỗi -> agent sửa build)
  file:<path>   patch có sẵn (đường dẫn trên host)
Các patch giả sinh lúc chạy từ nội dung file thật, nên luôn áp được vào cây hiện tại.

Chạy (README.md, mục M2/M3):
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

import agent as A
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
# Agent: Claude Code trên khối claude (agent.py); --bypass thay bằng bypass_agent_provider
# ---------------------------------------------------------------------------

@ChiaFunction(resources={"claude_creds": 1}, max_retries=0)
def agent_propose(ctx: dict) -> dict:
    """-> {"patch": unified diff, "summary": str, "log", "error", ...}."""
    return A.run_agent(ctx, "propose", ctx["tag"])


@ChiaFunction(resources={"claude_creds": 1}, max_retries=0)
def agent_fix_build(ctx: dict) -> dict:
    """ctx có patch, lỗi build, base_files. -> {"patch": patch thay thế (rỗng = bỏ cuộc), "summary", ...}."""
    return A.run_agent(ctx, "fix", ctx["tag"])


@ChiaFunction(resources={"claude_creds": 1}, max_retries=0)
def agent_debug(ctx: dict) -> dict:
    """ctx có patch và lý do gate/func hỏng. -> {"diagnosis": str} (chỉ ghi vào history, không sửa)."""
    r = A.run_agent(ctx, "debug", ctx["tag"])
    return {**r, "diagnosis": r.get("summary") or r.get("error", "")}


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


def patch_files(patch: str) -> list[str]:
    """File mà patch đụng tới (đường dẫn từ gốc Chipyard)."""
    out = []
    for m in re.finditer(r"^(?:diff --git a/(\S+) b/|\+\+\+ b/(\S+)|--- a/(\S+))", patch, re.M):
        f = next(g for g in m.groups() if g)
        if f not in out:
            out.append(f)
    return out


def check_patch(patch: str, untracked: list[str]) -> None:
    """Không tạo/xoá/đổi tên file (tree_diff chỉ thấy file đã track) và không sửa file chưa track."""
    if re.search(r"^(new file mode|deleted file mode|rename from|--- /dev/null|\+\+\+ /dev/null)", patch, re.M):
        raise PatchError("patch tạo, xoá hoặc đổi tên file: không được phép (chỉ sửa file đã có)")
    bad = [f for f in patch_files(patch) if f in set(untracked)]
    if bad:
        raise PatchError(f"patch sửa file chưa track trong Chipyard: {bad}")


def read_files(cy: str, files: list[str]) -> dict:
    """Nội dung hiện tại của các file (None nếu không có): bản gốc để agent sửa build so patch thay thế."""
    out = {}
    for f in files:
        p = Path(cy) / f
        out[f] = p.read_text(errors="surrogateescape") if p.is_file() else None
    return out


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
    status: str = ""              # rỗng | agent lỗi | patch lỗi | build lỗi | RTL trùng | gate | cycle | func |
                                  # ứng viên (sim) | synth | place | chờ gate | chưa gate | nhận | lỗi
    accepted: bool = False
    candidate: bool = False       # --phase sim: qua gate + func + ngân sách cycle
    pending: bool = False         # --phase asic (gate cuối đợt): đã đo ORFS, tốt hơn bản tốt nhất, chờ gate
    agent_minutes: float = 0.0
    reasons: list = dataclasses.field(default_factory=list)
    point: dict = dataclasses.field(default_factory=dict)
    rtl_hash: str = ""
    diagnosis: str = ""
    minutes: float = 0.0
    attempts: int = 1
    gate_rank: int = 0            # thứ tự được gate ở cuối đợt (1 = đo tốt nhất)
    gate_minutes: float = 0.0


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
        res = "**NHẬN**" if r.accepted else ("**ỨNG VIÊN**" if r.candidate else r.status)
        if r.gate_rank:
            res += f" (gate #{r.gate_rank})"
        L.append(f"| {r.n} | {who} | {r.patch_stat} | {res} | "
                 f"{D.fmt(p.get('fmax'), 3)} | {D.fmt(p.get('family'))} | {D.fmt(p.get('cycle_cost'), 4)} | "
                 f"{D.fmt(p.get('area'), 0)} | {r.minutes:.1f} |")
    for r in rounds:
        L += ["", f"## Vòng {r.n}: {'NHẬN' if r.accepted else ('ỨNG VIÊN' if r.candidate else r.status)}", "",
              "- Agent:", "", *[f"  > {x}" for x in (r.summary or "-").splitlines()[-12:]], "",
              f"- Patch: {r.patch_stat or '-'} (lần hỏi: {r.attempts}, agent {r.agent_minutes:.1f} phút)",
              f"- RTL hash: `{r.rtl_hash[:12] or '-'}`"]
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
        self.candidates: list[dict] = []
        self.pending: dict[int, dict] = {}     # vòng chờ gate cuối đợt: patch cây, patch agent, Point
        self.untracked = sorted(base_state.get("untracked_sha", {}))
        self.ctx_files = {"baseline_place.txt": stage_text(base_stages["place"]),
                          "baseline_synth.txt": stage_text(base_stages["synth"])}


def stage_text(r) -> str:
    """Báo cáo ORFS một bước (StageResult) dạng chữ cho agent."""
    L = [f"stage {r.stage}, variant {r.variant}, period {r.period_ns} ns",
         f"Fmax {D.fmt(r.fmax_mhz, 2)} MHz (internal slack {D.fmt(r.internal_slack_ns)} ns, I/O paths excluded), "
         f"WNS all paths {D.fmt(r.worst_slack_ns)}, TNS {D.fmt(r.tns_ns)}",
         f"worst path: {r.crit_start} -> {r.crit_end}",
         f"std-cell area {D.fmt(r.std_area_um2, 0)} um2 ({r.n_std} cells), macros {D.fmt(r.macro_area_um2, 0)} um2", "",
         "## per region (endpoint region): slack, negative endpoints / endpoints, worst path"]
    for k, v in (r.regions or {}).items():
        L.append(f"  {k:8s} {D.fmt(v.get('slack'))}  ({v.get('neg')}/{v.get('count')})  {v.get('start')} -> {v.get('end')}")
    if r.region_pairs:
        L += ["", "## worst slack per start region -> end region"]
        L += [f"  {k}: {D.fmt(v)}" for k, v in r.region_pairs.items()]
    for title, body in (("endpoint families (score = sum of worst family slacks)", r.families),
                        ("worst slack per top-level instance (instance region slack endpoints negative)", r.top_slack),
                        ("report_checks of the worst endpoint", r.top_worst),
                        ("worst endpoints", r.endpoints), ("worst paths", r.paths)):
        if body:
            L += ["", f"## {title}", body.strip()]
    return "\n".join(L) + "\n"


class AgentStop(SystemExit):
    pass


def ask(fn_name: str, fn, ctx_in: dict, kind: str, a, tag: str, d: Path | None = None, rd: Round | None = None) -> dict:
    """Gọi agent; ghi log phiên vào d/<tag>.log. Hết lượt dùng/đăng nhập hỏng -> dừng đợt."""
    if a.bypass:
        ctx_in = {**ctx_in, "bypass_reply": bypass_reply(kind, a.chipyard, fn_name)}
    print(f"  agent {fn_name} ({tag}) ...", flush=True)
    r = get(fn.chia_remote({**ctx_in, "tag": tag}, _chia_tag=tag))
    if d is not None and (r.get("log") or r.get("summary")):
        D.write(d / f"{tag}.log", f"# {fn_name} {tag}\n\n## tóm tắt\n{r.get('summary', '')}\n\n## lỗi\n{r.get('error', '')}\n"
                f"\n## bỏ khỏi patch\n" + "\n".join(r.get("dropped") or []) + f"\n\n## luồng sự kiện\n{r.get('log', '')}")
    if rd is not None:
        rd.agent_minutes += r.get("minutes") or 0.0
    if r.get("minutes"):
        print(f"  agent {fn_name}: {r['minutes']:.1f} phút{', lỗi: ' + r['error'] if r.get('error') else ''}"
              f"{', bỏ: ' + '; '.join(r['dropped']) if r.get('dropped') else ''}", flush=True)
    err = r.get("error") or ""
    if err.startswith(("rate_limit", "auth", "setup")):
        raise AgentStop(f"agent {fn_name}: {err[:500]}")
    return r


def agent_context(c: Ctx, n: int) -> dict:
    """Thông tin cho agent: báo cáo ORFS baseline, lịch sử, ngân sách, cấu hình agent."""
    files = {**c.ctx_files, "history.md": history_md(c.a, c.base, c.best, c.noise, c.rounds)}
    if c.best_patch:
        files["best.patch"] = c.best_patch
    if c.candidates:
        files["candidates.json"] = json.dumps(c.candidates, indent=2)
    return {"round": n, "phase": c.a.phase, "period_ns": c.a.period_ns, "chipyard": c.a.chipyard,
            "best": c.best.as_dict(), "baseline": c.base.as_dict(),
            "noise": {"fmax_mhz": c.noise.fmax_mhz, "family_ns": c.noise.family_ns},
            "budget": dataclasses.asdict(c.budget), "best_patch": c.best_patch, "untracked": c.untracked,
            "context_files": files,
            "agent": {"model": c.a.agent_model, "effort": c.a.agent_effort, "max_turns": c.a.agent_max_turns,
                      "timeout_s": c.a.agent_timeout, "keep_workspace": c.a.agent_keep_workspace}}


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
        check_tree(c, f"vòng {n}")


def _round_body(c: Ctx, n: int, kind: str, rd: Round, d: Path, tag_base: str, ctx_in: dict, applied: Applied) -> Round:
    a = c.a
    prop = {}
    for attempt in range(1, a.empty_retries + 2):
        rd.attempts = attempt
        prop = ask("agent_propose", agent_propose, {**ctx_in, "attempt": attempt}, kind, a, f"{tag_base}_p{attempt}",
                   d, rd)
        if (prop.get("patch") or "").strip() or prop.get("error"):
            break
        print(f"  agent trả patch rỗng (lần {attempt})", flush=True)
    rd.summary = prop.get("summary", "")
    patch = prop.get("patch") or ""
    D.write(d / "proposal.patch", patch)
    if prop.get("error") and not patch.strip():
        rd.status, rd.reasons = "agent lỗi", [f"agent: {prop['error'][:1500]}"]
        return rd
    if not patch.strip():
        rd.status = "rỗng"
        rd.reasons = [f"patch rỗng sau {rd.attempts} lần hỏi"]
        return rd
    rd.patch_stat = patch_stat(patch)
    print(f"  patch: {rd.patch_stat}", flush=True)
    if True:
        def apply_round(p: str) -> dict:
            """Cây đang ở trạng thái best: ghi nội dung trước patch (cho agent sửa build) rồi áp."""
            check_patch(p, c.untracked)
            pre = read_files(a.chipyard, patch_files(p))
            applied.apply(p, c.subs)
            return pre

        try:
            pre = apply_round(patch)
        except PatchError as e:
            rd.status, rd.reasons = "patch lỗi", [str(e)[:1500]]
            return rd
        state, art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=False)
        fixes = 0
        while rtl is None:
            err = D.build_errors(art)
            print("  build lỗi:\n    " + "\n    ".join(err.splitlines()[:15]), flush=True)
            if fixes >= a.fix_retries:
                rd.status, rd.reasons = "build lỗi", [f"build vẫn lỗi sau {fixes} lần agent sửa", err[-1500:]]
                return rd
            fixes += 1
            fix = ask("agent_fix_build", agent_fix_build,
                      {**ctx_in, "patch": patch, "build_errors": err, "base_files": pre}, kind, a,
                      f"{tag_base}_fix{fixes}" if fixes > 1 else f"{tag_base}_fix", d, rd)
            if not (fix.get("patch") or "").strip():
                rd.status, rd.reasons = "build lỗi", ["build lỗi, agent không sửa" + (f" ({fix['error']})" if fix.get("error") else ""),
                                                      err[-1500:]]
                return rd
            applied.revert()
            if c.best_patch:
                applied.apply(c.best_patch, c.subs)
            try:
                pre = apply_round(fix["patch"])
            except PatchError as e:
                rd.status, rd.reasons = "build lỗi", [f"patch sửa build không áp được: {e}"[:1500]]
                return rd
            patch = fix["patch"]
            rd.summary += f"\n[sửa build {fixes}] " + (fix.get("summary") or "")
            rd.patch_stat = patch_stat(patch) + f" (sau {fixes} lần sửa build)"
            D.write(d / f"proposal_fix{fixes}.patch", patch)
            state, art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=False)
        rd.rtl_hash = rtl.hash
        cand_patch = tree_diff(a.chipyard, c.subs)
        D.write(d / "tree.patch", cand_patch)
        if rtl.hash in c.measured:
            rd.status = "RTL trùng"
            rd.reasons = [f"RTL hash {rtl.hash[:12]} đã đo ({c.measured[rtl.hash]}): không đo lại"]
            return rd
        c.measured[rtl.hash] = f"vòng {n}"
        pt = P.Point(name=f"r{n}", rtl_hash=rtl.hash, period_ns=a.period_ns)
        if a.phase == "sim" or a.gate_each:
            if not gate_point(c, d, art, rtl, pt, patch, ctx_in, kind, tag_base, rd):
                return rd
        if a.phase == "sim":
            rd.candidate, rd.status = True, "ứng viên"
            rd.reasons = [f"gate + func đạt, chi phí cycle {pt.cycle_cost:.4f} ≤ {c.budget.cycle:.2f}"]
            D.write(d / "candidate.patch", cand_patch)
            c.candidates.append({"round": n, "rtl_hash": rtl.hash, "cycle_cost": pt.cycle_cost,
                                 "patch": str(d / "candidate.patch"), "patch_stat": rd.patch_stat,
                                 "summary": rd.summary[-1500:]})
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
        c.ctx_files[f"r{n}_place.txt"] = stage_text(st["place"])
        if not a.gate_each:
            dec = P.decide_place(c.base, c.best, pt, c.noise, c.budget)
            rd.reasons = dec.reasons
            if dec.accept:
                rd.pending, rd.status = True, "chờ gate"
                c.pending[n] = {"tree_patch": cand_patch, "patch": patch, "point": pt, "tag_base": tag_base,
                                "kind": kind}
            else:
                rd.status = dec.stage
            return rd
        dec = P.decide(c.base, c.best, pt, c.noise, c.budget)
        rd.accepted, rd.status, rd.reasons = dec.accept, ("nhận" if dec.accept else dec.stage), dec.reasons
        if dec.accept:
            c.best, c.best_patch = pt, cand_patch
        return rd


def gate_point(c: Ctx, d: Path, art, rtl, pt: P.Point, patch: str, ctx_in: dict, kind: str, tag_base: str,
               rd: Round, debug: bool = True) -> bool:
    """gate + func_check + chi phí cycle; hỏng thì agent chẩn đoán (debug=True) và ghi trạng thái vào rd."""
    a = c.a
    g = D.step_gate(a, d, art, rtl, c.mw, c.tm, baseline=False, elfs=c.base_gate["elfs"])
    pt.gate_ok, pt.gate_reasons = P.gate_check(c.base_gate["gate"], g["gate"], c.base_gate["known_fail"])
    pt.cycle_cost = S.cycle_cost(c.base_gate["gate"], g["gate"])
    if pt.gate_ok:
        fr = D.step_func(a, d, art, rtl, c.base_gate["elfs"], c.mw, c.tm, base=c.func_base,
                         base_hash=c.base_rtl.hash)
        pt.func_ok = fr.ok
        pt.func_reasons = [f"{b}: {r.message}" for b, r in fr.benches.items() if not r.ok]
    rd.point = pt.as_dict()
    if (not pt.gate_ok or not pt.func_ok) and debug:
        dg = ask("agent_debug", agent_debug, {**ctx_in, "patch": patch,
                                              "failures": pt.gate_reasons + pt.func_reasons}, kind, a,
                 f"{tag_base}_dbg", d, rd)
        rd.diagnosis = dg.get("diagnosis", "")
    if not pt.gate_ok or not pt.func_ok or pt.cycle_cost is None or pt.cycle_cost > c.budget.cycle:
        dec = P.decide(c.base, c.best, pt, c.noise, c.budget)
        rd.status, rd.reasons = dec.stage, dec.reasons + rd.reasons
        return False
    return True


def finish_batch(c: Ctx, debug: bool = True):
    """Gate cuối đợt (--phase asic, mặc định): xếp các vòng chờ gate theo Fmax sau place (rồi điểm họ), gate lần lượt;
    vòng đầu tiên qua gate + func + ngân sách cycle được nhận, các vòng sau ghi `chưa gate`."""
    a = c.a
    if not c.pending:
        print("\n== cuối đợt: không vòng nào đo tốt hơn bản tốt nhất, không gate", flush=True)
        return
    rounds = {r.n: r for r in c.rounds}
    order = sorted(c.pending, key=lambda n: (-(c.pending[n]["point"].fmax or 0),
                                             -(c.pending[n]["point"].family or -1e9)))
    print("\n== cuối đợt: gate theo thứ tự Fmax sau place: " +
          ", ".join(f"r{n} {D.fmt(c.pending[n]['point'].fmax, 3)} MHz" for n in order), flush=True)
    won = None
    for k, n in enumerate(order, 1):
        rd, info = rounds[n], c.pending[n]
        if won is not None:
            rd.status = "chưa gate"
            rd.reasons = [f"r{won} đã được nhận trước (Fmax cao hơn)"] + rd.reasons
            continue
        print(f"\n######## gate r{n} (hạng {k}/{len(order)})", flush=True)
        t0 = time.time()
        d = Path(a.out) / "runs" / f"r{n}"
        rd.gate_rank, rd.pending = k, False
        pt = info["point"]
        applied = Applied(a.chipyard)
        try:
            applied.apply(info["tree_patch"], c.subs)      # tree.patch = best.patch + patch của vòng
            _state, art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=False)   # cache HIT (cùng digest cây)
            if rtl is None or rtl.hash != pt.rtl_hash:
                rd.status, rd.reasons = "lỗi", [f"build lại r{n} cho gate ra RTL khác ({rtl.hash[:12] if rtl else 'lỗi'})"]
                continue
            ok = gate_point(c, d, art, rtl, pt, info["patch"], agent_context(c, n), info["kind"], info["tag_base"],
                            rd, debug=debug)
        except AgentStop as e:
            print(f"  (agent không chẩn đoán được: {e})", flush=True)
            ok, debug = False, False
        except Exception as e:      # noqa: BLE001
            traceback.print_exc()
            rd.status, rd.reasons = "lỗi", [f"gate cuối đợt: {type(e).__name__}: {e}"[:1500]]
            ok = False
        finally:
            applied.revert()
            check_tree(c, f"gate r{n}")
            rd.gate_minutes = (time.time() - t0) / 60
        if ok:
            dec = P.decide(c.base, c.best, pt, c.noise, c.budget)
            rd.point = pt.as_dict()
            rd.accepted, rd.status = dec.accept, ("nhận" if dec.accept else dec.stage)
            rd.reasons = [f"gate + func đạt, chi phí cycle {pt.cycle_cost:.4f}"] + dec.reasons
            if dec.accept:
                c.best, c.best_patch, won = pt, info["tree_patch"], n
        print(f"r{n}: {'NHẬN' if rd.accepted else rd.status} (gate {rd.gate_minutes:.1f} phút)", flush=True)
        save(Path(a.out), a, c)
    c.pending.clear()


def check_tree(c: Ctx, what: str):
    after = get(S.tree_state.chia_remote(c.a.chipyard))
    if after["digest"] != c.base_state["digest"]:
        raise SystemExit(f"cây Chipyard sau khi hoàn nguyên {what} KHÁC baseline (digest {after['digest'][:12]} ≠ "
                         f"{c.base_state['digest'][:12]}): dừng đợt. Xem `git -C {c.a.chipyard} submodule foreach git status`.")


# ---------------------------------------------------------------------------

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    D.add_common_args(ap)
    ap.add_argument("--rounds", type=int, default=5, help="số vòng của đợt (mặc định 5)")
    ap.add_argument("--phase", choices=("sim", "asic"), default="asic",
                    help="sim (M3): chỉ gate/func/cycle, ra candidates.json; asic: đo ORFS sau place, nhận/loại")
    ap.add_argument("--agent-model", default="opus", help="model của claude --model (mặc định opus)")
    ap.add_argument("--agent-effort", default="medium", help="claude --effort (rỗng = mặc định của CLI)")
    ap.add_argument("--agent-max-turns", type=int, default=200)
    ap.add_argument("--agent-timeout", type=int, default=1800, help="giây cho mỗi phiên agent")
    ap.add_argument("--agent-keep-workspace", action="store_true", help="giữ /tmp/socfmax-agent/<tag> trên khối claude")
    ap.add_argument("--fix-retries", type=int, default=2, help="số lần agent sửa build mỗi vòng")
    ap.add_argument("--gate-each", action="store_true",
                    help="--phase asic: gate mọi vòng trước ORFS (cách cũ). Mặc định: đo ORFS mọi vòng, cuối đợt chỉ "
                         "gate vòng đo tốt nhất, hỏng thì vòng tốt kế tiếp")
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
        need = ["chipyard", "verilator_run", "asic", "claude_creds"]   # agent bypass vẫn chạy trên khối claude (resource claude_creds)
        missing = [r for r in need if not res.get(r)]
        if missing:
            raise SystemExit(f"cluster thiếu resource {missing}: python scripts/chia-up-blocks.py --asic --verilator-slots 4 "
                             f"(khối claude bật mặc định)")
        print(f"verilator_run: {res.get('verilator_run')} slot", flush=True)
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
            c.candidates = h.get("candidates", [])
            c.best = P.Point(**h["best"])
            c.best_patch = (hdir / "best.patch").read_text() if (hdir / "best.patch").exists() else ""
            c.measured.update(h.get("measured", {}))
            start = max((r.n for r in c.rounds), default=0) + 1
            print(f"đi tiếp từ {hdir}: {len(c.rounds)} vòng, bản tốt nhất {c.best.name} ({D.fmt(c.best.fmax, 3)} MHz)", flush=True)
        print(f"ngưỡng nhiễu: Fmax ±{noise.fmax_mhz:.3f} MHz, điểm họ ±{noise.family_ns:.3f}; ngân sách {c.budget}", flush=True)
        who = ("bypass " + a.bypass) if a.bypass_list else \
            f"{a.agent_model}, effort {a.agent_effort or '-'}, {a.agent_timeout} s/phiên, sửa build {a.fix_retries} lần"
        print(f"pha {a.phase}; agent: {who}", flush=True)
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
        if a.phase == "asic" and not a.gate_each:
            finish_batch(c)
    except AgentStop as e:
        print(f"\nDỪNG: {e}", flush=True)
        if c is not None and c.pending:
            print("gate các vòng đã đo (không có agent chẩn đoán)", flush=True)
            finish_batch(c, debug=False)
        raise
    except SystemExit as e:
        print(f"\nDỪNG: {e}", flush=True)
        raise
    finally:
        if c is not None:
            save(out, a, c)
        mw.stop()
        D.prune_orfs(a, [c.best.name] if c is not None else ["baseline"])
        print("\n## Thời gian", flush=True)
        for row in tm.rows:
            print(f"  {row['step']:10s} {row['minutes']:8.2f} phút  {'(cache)' if row['cached'] else '       '}  {row['note']}", flush=True)
        print(f"\nKết quả: {out}/history.md, {out}/best.patch, {out}/results.csv", flush=True)
        stop_collector()
        stop_cache()


def save(out: Path, a, c: Ctx):
    D.write(out / "history.json", json.dumps({"rounds": [dataclasses.asdict(r) for r in c.rounds],
                                              "best": c.best.as_dict(), "measured": c.measured,
                                              "candidates": c.candidates,
                                              "pending": sorted(c.pending)}, indent=2, default=str))
    D.write(out / "best.patch", c.best_patch)
    D.write(out / "candidates.json", json.dumps(c.candidates, indent=2))
    D.write(out / "history.md", history_md(a, c.base, c.best, c.noise, c.rounds))


if __name__ == "__main__":
    main()
