"""loop.py: một đợt vòng tối ưu Fmax. Agent thật (agent.py, Claude Code trên khối claude); --bypass thay agent bằng
câu trả lời dựng sẵn (thử loop không tốn lượt agent, hoặc chạy lại patch có sẵn).

Mỗi vòng, chung cho hai pha:
  1. áp đầu chuỗi (best.patch + các vòng đã cộng dồn trong đợt) lên ~/tools/chipyard; agent đề xuất một patch
     (đường dẫn tính từ gốc Chipyard, có thể trải qua nhiều submodule); patch rỗng thì hỏi lại tối đa --empty-retries
     lần; không được tạo/xoá file hay sửa file chưa track;
  2. build (tag theo digest cây); build lỗi -> agent sửa build (tối đa --fix-retries lần) -> vẫn lỗi thì loại;
  3. RTL hash đã đo -> bỏ, không đo lại;
  ... (theo pha, xem dưới) ...
  cuối vòng hoàn nguyên cây (git apply -R theo từng submodule) và kiểm digest bằng digest baseline: khác thì dừng đợt.

--phase sim (M3): 4. gate (riscv-tests + 8 benchmark, minstret bằng baseline) + func_check (so vết với baseline) + chi
  phí cycle; hỏng -> agent chẩn đoán; qua -> ỨNG VIÊN (candidates.json). Không ORFS, vòng nào cũng đi từ bản tốt nhất.

--phase asic (mặc định, M4), đợt cộng dồn đo bằng place:
  4. ORFS synth -> floorplan -> place; so với đầu chuỗi bằng ngưỡng nhiễu place (policy.decide_place, noise.json);
     không tốt hơn -> bỏ vòng;
  5. gate nhanh (--quick-gate isa|full|none); hỏng -> agent chẩn đoán, bỏ vòng; qua -> CỘNG DỒN (vòng sau xây trên nó);
  cuối đợt: các vòng đã cộng dồn xếp theo Fmax place giảm dần (trong chuỗi, Fmax tăng dần nên đây là thứ tự lùi), lần
  lượt gate đủ (gate + func_check + ngân sách cycle), không agent chẩn đoán; bản đầu tiên qua -> NHẬN (best.patch).
  Hết lượt agent giữa đợt vẫn chạy bước cuối đợt.
  Ngưỡng nhiễu tính theo ns của chu kỳ đạt được (chu kỳ − WNS), không theo MHz.

Bậc chu kỳ (--ladder 10,9,8,7,6,5): một vòng cộng dồn có WNS ≥ 0 sau place ở bậc hiện tại -> kết thúc đợt sớm (gate đủ
  cuối đợt). Bản được nhận có WNS ≥ 0 -> bậc đạt: --on-met next (mặc định) xuống bậc kế ngay trong job (đo lại bản tốt
  nhất ở bậc mới, đo nhiễu trên nó: 3 lần place nhiễu nhẹ, --rung-noise keep để dùng lại ngưỡng cũ), các vòng còn lại
  chạy ở bậc mới; --on-met stop dừng job. Đạt bậc cuối (5 ns) -> dừng job (sau đó M5). Variant ORFS của bản tốt nhất ở
  mỗi bậc đã đạt được giữ lại (cho M5). Baseline luôn đo ở bậc đầu (10 ns). Đi tiếp bằng --history-from: bậc bắt đầu
  là chu kỳ của bản tốt nhất cũ (trừ khi có --period-ns), nếu nó đã đạt bậc đó thì xuống bậc ngay đầu job.

Kết quả ở ~/soc-fmax-runs/<run>/: summary.md (tổng hợp job), history.md, history.json, best.patch, candidates.json
(sim), results.csv, runs/r<N>/ (patch, log agent, báo cáo). Cuối job chỉ giữ kết quả ORFS (flow/results|logs|reports|
objects) của bản tốt nhất, kể cả xoá của bản tốt nhất cũ (--history-from) khi job có bản tốt hơn (--keep-orfs để giữ
hết). Đợt sau đi tiếp: --history-from ~/soc-fmax-runs/<run>.

--bypass (mỗi vòng một mục, lặp lại nếu ít hơn số vòng):
  empty         patch rỗng (kiểm đường hỏi lại / vòng rỗng)
  comment       thêm một dòng chú thích cuối RocketCore.scala (RTL hash không đổi -> kiểm "không đo lại")
  broken        thêm một dòng Scala sai cuối RocketCore.scala (kiểm đường build lỗi)
  file:<path>   patch có sẵn (đường dẫn trên host)

Chạy (README.md):
  chia job submit --no-wait --submission-id m4-c --working-dir . -- python loop.py --run-name m4-c \\
      --noise-json ~/soc-fmax-runs/m2-noise/noise.json --history-from ~/soc-fmax-runs/m4-b --rounds 5
"""
from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import os
import re
import sys
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ray

import agent as A
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
                                  # ứng viên (sim) | floorplan | gate nhanh | cộng dồn | place | nhận |
                                  # trong bản nhận | chưa kiểm | lỗi
    accepted: bool = False
    candidate: bool = False       # --phase sim: qua gate + func + ngân sách cycle
    pending: bool = False         # --phase asic: đã cộng dồn, chờ place + gate đủ ở cuối đợt
    agent_minutes: float = 0.0
    reasons: list = dataclasses.field(default_factory=list)
    point: dict = dataclasses.field(default_factory=dict)
    rtl_hash: str = ""
    diagnosis: str = ""
    minutes: float = 0.0
    attempts: int = 1
    gate_rank: int = 0            # thứ tự gate đủ ở cuối đợt (1 = Fmax place cao nhất, ...)
    gate_minutes: float = 0.0


def patch_stat(patch: str) -> str:
    files = re.findall(r"^\+\+\+ b/(\S+)", patch, re.M)
    add = len(re.findall(r"^\+(?!\+\+)", patch, re.M))
    rem = len(re.findall(r"^-(?!--)", patch, re.M))
    return f"{len(files)} file, +{add} −{rem}: " + ", ".join(files[:6]) + (" ..." if len(files) > 6 else "")


def med_txt(p: dict) -> str:
    """Ô "trung vị đo lặp" của một điểm (dict của P.Point): Fmax trung vị (số lần đo), trống nếu chưa đo lặp."""
    fs = [f for f in (p.get("fmax_samples") or []) if f]
    if len(fs) < 2:
        return ""
    return f"{1000.0 / P.median([1000.0 / f for f in fs]):.3f} ({len(fs)})"


def history_md(a, base: P.Point, best: P.Point, noise: P.Noise, rounds: list[Round], rungs: list | None = None) -> str:
    L = [f"# soc-fmax loop {a.run_name}", "",
         f"- Cập nhật {datetime.now(timezone.utc).isoformat(timespec='seconds')}, chu kỳ hiện tại {a.period_ns} ns "
         f"(bậc {', '.join(f'{x:g}' for x in a.ladder_list)})",
         f"- Baseline (@ {D.fmt(base.period_ns, 1)} ns): Fmax {D.fmt(base.fmax, 3)} MHz, điểm họ {D.fmt(base.family)}, "
         f"logic {D.fmt(base.area, 0)} um2",
         f"- Bản tốt nhất: **{best.name}** (@ {D.fmt(best.period_ns, 1)} ns), Fmax {D.fmt(best.fmax, 3)} MHz, "
         f"điểm họ {D.fmt(best.family)}" + (f", trung vị {med_txt(best.as_dict())} lần đo" if med_txt(best.as_dict()) else ""),
         f"- Luật nhận: một lần đo để lọc (loại khi xấu đi quá nhiễu); hứa hẹn thì đo lặp, nhận khi trung vị chu kỳ đạt "
         f"được tốt lên ≥ {a.repeat_gain:.3f} ns",
         f"- Pha {a.phase}" + (" (đợt cộng dồn)" if a.phase == "asic" else ""),
         f"- Ngưỡng nhiễu: chu kỳ đạt được ±{noise.period_ns:.3f} ns, điểm họ ±{noise.family_ns:.3f} ({noise.note})"]
    for x in rungs or []:
        L.append(f"- Bậc {x['period']:g} ns ĐẠT: {x['best']} WNS {D.fmt(x['wns'])} ns, Fmax {D.fmt(x['fmax'], 3)} MHz "
                 f"(sau vòng {x['round']}, job {x['run']})")
    L += ["", "| vòng | agent | patch | kết quả | chu kỳ | Fmax floorplan | Fmax place | trung vị đo lặp | điểm họ place | cycle | logic | phút |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rounds:
        p = r.point or {}
        who = ("file:" + os.path.basename(r.bypass[5:])) if r.bypass.startswith("file:") else (r.bypass or "agent")
        res = "**NHẬN**" if r.accepted else ("**ỨNG VIÊN**" if r.candidate else r.status)
        if r.gate_rank:
            res += f" (cuối đợt, lần {r.gate_rank})"
        L.append(f"| {r.n} | {who} | {r.patch_stat} | {res} | {D.fmt(p.get('period_ns'), 1)} | "
                 f"{D.fmt(p.get('fp_fmax'), 3)} | {D.fmt(p.get('fmax'), 3)} | {med_txt(p)} | {D.fmt(p.get('family'))} | {D.fmt(p.get('cycle_cost'), 4)} | "
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
        self.budget = P.Budget(cycle=a.cycle_budget, area=a.area_budget, gain_ns=a.repeat_gain)
        self.base = P.Point(name="baseline", rtl_hash=base_rtl.hash, gate_ok=True, func_ok=True, cycle_cost=1.0,
                            synth_fmax=base_stages["synth"].fmax_mhz, synth_family=base_stages["synth"].family_score,
                            fmax=base_stages["place"].fmax_mhz, family=base_stages["place"].family_score,
                            area=base_stages["place"].std_area_um2, period_ns=base_stages["place"].period_ns,
                            fp_fmax=base_stages["floorplan"].fmax_mhz, fp_family=base_stages["floorplan"].family_score,
                            fp_area=base_stages["floorplan"].std_area_um2)
        self.best, self.best_patch = self.base, ""
        self.head, self.head_patch = self.best, ""   # cây agent sửa trên đó (asic: đầu chuỗi cộng dồn của đợt)
        self.chain: list[dict] = []                  # asic: các vòng đã cộng dồn trong đợt
        self.start_best = self.base                  # bản tốt nhất lúc bắt đầu job (cho summary.md)
        self.first_round = 1                         # vòng đầu tiên của job này (--history-from: tiếp số vòng)
        self.place_info = {"baseline": place_info(base_stages["place"])}   # tên điểm -> WNS, đường găng, vùng
        self.start_info: dict = {}                   # place_info của bản tốt nhất lúc bắt đầu job (trước khi xuống bậc)
        self.rungs: list[dict] = []                  # bậc đã đạt (mọi job): period, best, wns, fmax, round, run
        self.keep_points: list[str] = []             # điểm ORFS giữ lại ngoài bản tốt nhất (bản tốt nhất của bậc đã đạt)
        self.met_round: int | None = None            # vòng cộng dồn có WNS ≥ 0 ở bậc hiện tại -> kết thúc đợt sớm
        self.noise_runs: list[dict] = []             # các lần đo nhiễu trong job (cho summary.md)
        self.measured = {base_rtl.hash: "baseline"}
        self.rounds: list[Round] = []
        self.candidates: list[dict] = []
        self.untracked = sorted(base_state.get("untracked_sha", {}))
        self.ctx_files = {"baseline_place.txt": stage_text(base_stages["place"]),
                          "baseline_floorplan.txt": stage_text(base_stages["floorplan"]),
                          "baseline_synth.txt": stage_text(base_stages["synth"])}

    def set_best(self, pt: P.Point, patch: str):
        self.best, self.best_patch = pt, patch
        self.head, self.head_patch = pt, patch


def place_info(r) -> dict:
    """Vài số của báo cáo place (StageResult) cho summary.md."""
    return {"wns": r.internal_slack_ns, "tns": r.tns_ns, "crit": f"{r.crit_start} -> {r.crit_end}",
            "regions": {k: v.get("slack") for k, v in (r.regions or {}).items()}}


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
    files = {**c.ctx_files, "history.md": history_md(c.a, c.base, c.best, c.noise, c.rounds, c.rungs)}
    if c.head_patch:
        files["best.patch"] = c.head_patch
    if c.candidates:
        files["candidates.json"] = json.dumps(c.candidates, indent=2)
    return {"round": n, "phase": c.a.phase, "period_ns": c.a.period_ns, "chipyard": c.a.chipyard,
            "best": c.head.as_dict(), "baseline": c.base.as_dict(), "batch": "chain" if c.a.phase == "asic" else "",
            "noise": {"period_ns": c.noise.period_ns, "family_ns": c.noise.family_ns,
                      "measured_on": c.noise.point, "at_period_ns": c.noise.at_period_ns},
            "ladder_ns": c.a.ladder_list,
            "budget": dataclasses.asdict(c.budget), "best_patch": c.head_patch, "untracked": c.untracked,
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
        if c.head_patch:                     # agent nhìn thấy (và sửa trên) cây đã có các vòng được nhận/cộng dồn
            try:
                applied.apply(c.head_patch, c.subs)
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
        if c.head_patch:
            applied.apply(c.head_patch, c.subs)
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
    if a.phase == "asic":
        return chain_round(c, n, kind, rd, d, tag_base, patch, cand_patch, art, rtl, pt, ctx_in)
    if not gate_point(c, d, art, rtl, pt, patch, ctx_in, kind, tag_base, rd):
        return rd
    rd.candidate, rd.status = True, "ứng viên"
    rd.reasons = [f"gate + func đạt, chi phí cycle {pt.cycle_cost:.4f} ≤ {c.budget.cycle:.2f}"]
    D.write(d / "candidate.patch", cand_patch)
    c.candidates.append({"round": n, "rtl_hash": rtl.hash, "cycle_cost": pt.cycle_cost,
                         "patch": str(d / "candidate.patch"), "patch_stat": rd.patch_stat,
                         "summary": rd.summary[-1500:]})
    return rd


def quick_gate(c: Ctx, d: Path, art, rtl) -> tuple[bool, list, float | None]:
    """Gate nhanh mỗi vòng của --phase asic. isa: chỉ riscv-tests ISA (tag riêng, không lẫn với gate đủ);
    full: gate đủ (ISA + benchmark, minstret, ngân sách cycle), không func_check."""
    a = c.a
    if a.quick_gate == "full":
        g = D.step_gate(a, d, art, rtl, c.mw, c.tm, baseline=False, elfs=c.base_gate["elfs"])["gate"]
        ok, why = P.gate_check(c.base_gate["gate"], g, c.base_gate["known_fail"])
        cc = S.cycle_cost(c.base_gate["gate"], g)
        if ok and (cc is None or cc > c.budget.cycle):
            ok, why = False, [f"chi phí cycle {cc} > {c.budget.cycle}"]
        return ok, why, cc
    elfs = {**c.base_gate["elfs"], "bench": {}}
    tag = f"gate_q_{rtl.hash[:16]}_{S.GATE_VERSION}_isa"
    hit = D.cache_has(tag)
    t0 = time.time()
    with c.mw.step("gate nhanh"):
        g = get(S.run_gate.chia_remote(art, elfs, _chia_tag=tag))
    c.tm.add("gate nhanh", (time.time() - t0) / 60, hit, f"{len(elfs['isa'])} test ISA")
    bad = sorted(t for t, ok in g.isa.items() if not ok and t not in c.base_gate["known_fail"])
    print(f"gate nhanh (ISA): {sum(g.isa.values())}/{len(g.isa)} pass, {(time.time() - t0) / 60:.1f} phút"
          f"{' (cache)' if hit else ''}", flush=True)
    for t in bad:
        D.write(d / "gate_quick_fail" / f"{t}.log", g.fail_logs.get(t, ""))
    return not bad, ([f"ISA hỏng: {', '.join(bad[:10])}" + (f" (+{len(bad) - 10})" if len(bad) > 10 else "")]
                     if bad else []), None


def chain_round(c: Ctx, n: int, kind: str, rd: Round, d: Path, tag_base: str, patch: str, cand_patch: str, art, rtl,
                pt: P.Point, ctx_in: dict) -> Round:
    """--phase asic: ORFS tới place, so với đầu chuỗi (ngưỡng nhiễu place); tốt hơn thì gate nhanh rồi cộng dồn (vòng
    sau xây trên vòng này). Gate đủ để ở cuối đợt (finish_chain)."""
    a = c.a
    _prep, st = D.run_orfs_point(a, d, rtl, c.fakeram, a.orfs_vars, f"r{n}", "place", c.mw, c.tm, fatal=False)
    last = list(st.values())[-1]
    if not last.ok or "place" not in st:
        rd.status, rd.reasons = "lỗi", [f"ORFS {last.stage}: {(last.error or '')[:1500]}"]
        return rd
    pt.synth_fmax, pt.synth_family = st["synth"].fmax_mhz, st["synth"].family_score
    fp, pl = st["floorplan"], st["place"]
    pt.fp_fmax, pt.fp_family, pt.fp_area = fp.fmax_mhz, fp.family_score, fp.std_area_um2
    pt.fmax, pt.family, pt.area = pl.fmax_mhz, pl.family_score, pl.std_area_um2
    rd.point = pt.as_dict()
    c.ctx_files[f"r{n}_place.txt"] = stage_text(pl)
    c.place_info[f"r{n}"] = place_info(pl)
    dec = P.decide_place(c.base, c.head, pt, c.noise, c.budget)
    rd.reasons = dec.reasons
    print(f"  place: {'tốt hơn' if dec.accept else ('hứa hẹn' if dec.repeat else 'không tốt hơn')} đầu chuỗi "
          f"{c.head.name}: " + "; ".join(dec.reasons), flush=True)
    if not dec.accept and not dec.repeat:
        rd.status = "place"
        return rd
    if a.quick_gate != "none":
        ok, why, cc = quick_gate(c, d, art, rtl)
        pt.cycle_cost = cc if cc is not None else pt.cycle_cost
        rd.point = pt.as_dict()
        if not ok:
            rd.status, rd.reasons = "gate nhanh", why + rd.reasons
            dg = ask("agent_debug", agent_debug, {**ctx_in, "patch": patch, "failures": why}, kind, a,
                     f"{tag_base}_dbg", d, rd)
            rd.diagnosis = dg.get("diagnosis", "")
            return rd
    if dec.repeat:
        repeat_point(c, d, rtl, pt, f"r{n}")
        rd.point = pt.as_dict()
        first = dec.reasons[1:]
        dec = P.decide_place(c.base, c.head, pt, c.noise, c.budget)
        rd.reasons = dec.reasons + ["một lần đo: " + x for x in first[:1]]
        print(f"  đo lặp: {'tốt hơn' if dec.accept else 'không tốt hơn'} đầu chuỗi {c.head.name}: "
              + "; ".join(dec.reasons), flush=True)
        if not dec.accept:
            rd.status = "đo lặp"
            return rd
    rd.status, rd.pending = "cộng dồn", True
    c.chain.append({"n": n, "tree_patch": cand_patch, "patch": patch, "point": pt, "tag_base": tag_base, "kind": kind})
    c.head, c.head_patch = pt, cand_patch
    print(f"  cộng dồn: vòng sau xây trên r{n} (chuỗi {' + '.join('r' + str(x['n']) for x in c.chain)})", flush=True)
    wns = pt.eff_wns()
    wns = pl.internal_slack_ns if wns is None else wns
    if wns is not None and wns >= 0:
        c.met_round = n
        print(f"  r{n} có WNS {wns:+.3f}{' (trung vị)' if pt.eff()[2] >= 2 else ''} ≥ 0 ở {a.period_ns:g} ns: "
              "kết thúc đợt sớm", flush=True)
    return rd


def finish_chain(c: Ctx):
    """Cuối đợt (--phase asic): các vòng đã cộng dồn (đã có số place) xếp theo Fmax place giảm dần; lần lượt gate đủ
    (gate + func_check + ngân sách cycle, không agent chẩn đoán); bản đầu tiên qua thì nhận."""
    a = c.a
    rounds = {r.n: r for r in c.rounds}
    order = sorted(c.chain, key=lambda x: (x["point"].eff()[0] or 0, x["n"]), reverse=True)
    if not order:
        print("\n== cuối đợt: không vòng nào được cộng dồn", flush=True)
        return
    print("\n== cuối đợt: gate đủ theo Fmax place (trung vị nếu đã đo lặp): "
          + ", ".join(f"r{x['n']} {D.fmt(x['point'].eff()[0], 3)}" for x in order), flush=True)
    won = None
    for rank, info in enumerate(order, 1):
        n, pt = info["n"], info["point"]
        rd = rounds[n]
        print(f"\n######## gate đủ r{n} (lần {rank}, place {D.fmt(pt.fmax, 3)} MHz)", flush=True)
        t0 = time.time()
        d = Path(a.out) / "runs" / f"r{n}"
        rd.gate_rank, rd.pending, ok = rank, False, False
        applied = Applied(a.chipyard)
        try:
            applied.apply(info["tree_patch"], c.subs)
            _state, art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=False)
            if rtl is None or rtl.hash != pt.rtl_hash:
                rd.status, rd.reasons = "lỗi", [f"build lại r{n} ra RTL khác ({rtl.hash[:12] if rtl else 'lỗi'})"]
            else:
                ok = gate_point(c, d, art, rtl, pt, info["patch"], {}, info["kind"], info["tag_base"], rd, debug=False)
        except Exception as e:      # noqa: BLE001
            traceback.print_exc()
            rd.status, rd.reasons = "lỗi", [f"cuối đợt: {type(e).__name__}: {e}"[:1500]]
        finally:
            applied.revert()
            check_tree(c, f"cuối đợt r{n}")
            rd.gate_minutes = (time.time() - t0) / 60
        if ok:
            dec = P.decide(c.base, c.best, pt, c.noise, c.budget)
            rd.point = pt.as_dict()
            rd.accepted, rd.status = dec.accept, ("nhận" if dec.accept else dec.stage)
            rd.reasons = [f"gate + func đạt, chi phí cycle {pt.cycle_cost:.4f}"] + dec.reasons
            if dec.accept:
                c.set_best(pt, info["tree_patch"])
                won = n
        print(f"r{n}: {'NHẬN' if rd.accepted else rd.status} ({rd.gate_minutes:.1f} phút)", flush=True)
        save(Path(a.out), a, c)
        if won is not None:
            break
    if won is not None:
        for x in c.chain:
            r = rounds[x["n"]]
            if x["n"] < won and r.pending:
                r.status, r.pending = "trong bản nhận", False
                r.reasons = [f"nằm trong bản cộng dồn được nhận (r{won})"] + r.reasons
    for x in c.chain:
        r = rounds[x["n"]]
        if r.pending:
            r.status, r.pending = "chưa kiểm", False
    c.chain.clear()
    c.head, c.head_patch = c.best, c.best_patch
    save(Path(a.out), a, c)


def repeat_point(c: Ctx, d: Path, rtl, pt: P.Point, label: str):
    """Đo lặp một ứng viên (phương án A): 3 biến thể nhiễu (như đo nhiễu, bỏ "rerun") ở chu kỳ hiện tại; ghi
    pt.fmax_samples/family_samples (gồm lần đo chính)."""
    a = c.a
    fs, gs = [pt.fmax], [pt.family]
    print(f"\n-- đo lặp {label} @ {a.period_ns:g} ns (3 biến thể nhiễu)", flush=True)
    for name, ov in D.noise_variants(a.orfs_vars):
        if name == "rerun":
            continue
        _p, sn = D.run_orfs_point(a, d / f"repeat_{name}", rtl, c.fakeram, ov, f"{label}~{name}", "place",
                                  c.mw, c.tm, fatal=False)
        last = list(sn.values())[-1]
        if not last.ok or "place" not in sn:
            print(f"  đo lặp {name}: ORFS {last.stage} lỗi, bỏ lần đo này", flush=True)
            continue
        fs.append(sn["place"].fmax_mhz)
        gs.append(sn["place"].family_score)
    pt.fmax_samples, pt.family_samples = fs, gs
    f, g, k = pt.eff()
    print(f"  {label}: {', '.join(D.fmt(x, 3) for x in fs)} MHz → trung vị {D.fmt(f, 3)} MHz ({k} lần), "
          f"điểm họ trung vị {D.fmt(g)}", flush=True)


def attach_noise_samples(c: Ctx):
    """Ngưỡng nhiễu đo trên chính bản tốt nhất ở chu kỳ hiện tại: dùng các lần đo đó làm đo lặp của nó."""
    b, nz = c.best, c.noise
    if (nz.point == b.name and nz.at_period_ns is not None and b.period_ns is not None
            and abs(nz.at_period_ns - b.period_ns) < 1e-9 and len(b.fmax_samples) < 2):
        b.fmax_samples = [s_.get("fmax") for s_ in nz.samples]
        b.family_samples = [s_.get("family") for s_ in nz.samples]


def measure_best(c: Ctx, why: str, noise: bool = False):
    """Đo bản tốt nhất ở chu kỳ hiện tại (build + ORFS tới place; thường lấy từ cache) và cập nhật số đo của nó; noise:
    thêm 3 lần place nhiễu nhẹ (PLACE_DENSITY ±0,01, CORE_UTILIZATION +1) -> ngưỡng nhiễu mới (c.noise)."""
    a, b = c.a, c.best
    d = Path(a.out) / "runs" / f"best_{b.name}_p{a.period_ns:g}"
    print(f"\n== bản tốt nhất {b.name} @ {a.period_ns:g} ns: {why}", flush=True)
    applied = Applied(a.chipyard)
    samples = []
    try:
        if c.best_patch:
            applied.apply(c.best_patch, c.subs)
        _state, _art, rtl = D.step_rtl(a, d, c.mw, c.tm, baseline=not c.best_patch)
        if rtl is None or rtl.hash != b.rtl_hash:
            raise SystemExit(f"best.patch build ra RTL khác ({rtl.hash[:12] if rtl else 'build lỗi'} ≠ {b.rtl_hash[:12]})")
        _prep, st = D.run_orfs_point(a, d, rtl, c.fakeram, a.orfs_vars, b.name, "place", c.mw, c.tm)
        if noise:
            pl = st["place"]
            samples.append({"name": b.name, "fmax": pl.fmax_mhz, "family": pl.family_score, "area": pl.std_area_um2})
            for name, ov in D.noise_variants(a.orfs_vars):
                if name == "rerun":          # ORFS tất định (M2): chạy lại y hệt cho cùng số
                    continue
                print(f"\n-- nhiễu {name} @ {a.period_ns:g} ns", flush=True)
                _p, sn = D.run_orfs_point(a, d / f"noise_{name}", rtl, c.fakeram, ov, f"noise@{a.period_ns:g}:{name}",
                                          "place", c.mw, c.tm)
                samples.append({"name": name, "fmax": sn["place"].fmax_mhz, "family": sn["place"].family_score,
                                "area": sn["place"].std_area_um2, "variant": sn["place"].variant})
    finally:
        applied.revert()
        check_tree(c, f"bản tốt nhất {b.name}")
    pl, fp, sy = st["place"], st["floorplan"], st["synth"]
    same = b.period_ns is not None and abs(b.period_ns - a.period_ns) < 1e-9
    fs = [s_["fmax"] for s_ in samples] if noise else (list(b.fmax_samples) if same else [])
    gs = [s_["family"] for s_ in samples] if noise else (list(b.family_samples) if same else [])
    pt = dataclasses.replace(b, period_ns=a.period_ns, fmax=pl.fmax_mhz, family=pl.family_score, area=pl.std_area_um2,
                             fp_fmax=fp.fmax_mhz, fp_family=fp.family_score, fp_area=fp.std_area_um2,
                             synth_fmax=sy.fmax_mhz, synth_family=sy.family_score,
                             fmax_samples=fs, family_samples=gs)
    c.set_best(pt, c.best_patch)
    c.ctx_files[f"{b.name}_p{a.period_ns:g}_place.txt"] = stage_text(pl)
    c.place_info[info_key(pt)] = place_info(pl)
    print(f"  {b.name} @ {a.period_ns:g} ns: Fmax {D.fmt(pt.fmax, 3)} MHz, WNS {D.fmt(pl.internal_slack_ns)}", flush=True)
    if noise:
        c.noise = P.noise_from(samples, at_period_ns=a.period_ns, point=b.name)
        D.write(Path(a.out) / f"noise_p{a.period_ns:g}.json", json.dumps(dataclasses.asdict(c.noise), indent=2))
        c.noise_runs.append({"period": a.period_ns, "point": b.name, "samples": samples,
                             "period_thr": c.noise.period_ns, "family_thr": c.noise.family_ns, "note": c.noise.note})
        print(f"  NGƯỠNG NHIỄU MỚI: chu kỳ đạt được ±{c.noise.period_ns:.3f} ns, điểm họ ±{c.noise.family_ns:.3f} "
              f"({c.noise.note})", flush=True)


def info_key(pt: P.Point) -> str:
    """Khoá place_info của một lần đo bản tốt nhất (khác khoá r<N> của vòng: cùng điểm có thể đo ở nhiều bậc)."""
    return f"{pt.name}@{pt.period_ns:g}" if pt.period_ns else pt.name


def best_info(c: Ctx, pt: P.Point | None = None) -> dict:
    pt = pt or c.best
    return c.place_info.get(info_key(pt)) or c.place_info.get(pt.name, {})


def best_wns(c: Ctx) -> float | None:
    """WNS của bản tốt nhất ở chu kỳ hiện tại: trung vị nếu đã đo lặp, không thì lần đo chính."""
    w = c.best.eff_wns()
    return best_info(c).get("wns") if w is None else w


def best_met(c: Ctx) -> bool:
    wns = best_wns(c)
    return (wns is not None and wns >= 0 and c.best.period_ns is not None
            and abs(c.best.period_ns - c.a.period_ns) < 1e-9)


def next_period(a) -> float | None:
    return next((p for p in a.ladder_list if p < a.period_ns - 1e-9), None)


def step_down(c: Ctx, last_round: int) -> bool:
    """Bản tốt nhất đạt bậc hiện tại: ghi bậc, giữ variant ORFS của nó (M5), xuống bậc kế (đo lại bản tốt nhất và
    nhiễu). Lặp nếu bản tốt nhất đạt luôn bậc mới. Trả False khi dừng job (--on-met stop hoặc hết bậc)."""
    a = c.a
    while best_met(c):
        b = c.best
        c.rungs.append({"period": a.period_ns, "best": b.name, "wns": best_wns(c), "fmax": b.eff()[0],
                        "round": last_round, "run": a.run_name})
        key = f"{b.name}@{a.period_ns:g}ns"
        v = getattr(a, "orfs_variants", {}).get(b.name)
        if v:
            a.orfs_variants[key] = v
            c.keep_points.append(key)
        print(f"\n######## BẬC {a.period_ns:g} ns ĐẠT: {b.name} WNS {D.fmt(best_wns(c))} ns "
              f"(giữ variant ORFS {v or '-'} cho M5)", flush=True)
        nxt = next_period(a)
        if nxt is None:
            print("đã đạt bậc cuối: dừng job (bước tiếp theo: M5)", flush=True)
            return False
        if a.on_met == "stop":
            print(f"--on-met stop: dừng job; job sau chạy ở {nxt:g} ns", flush=True)
            return False
        a.period_ns = nxt
        measure_best(c, f"xuống bậc {nxt:g} ns", noise=a.rung_noise == "measure")
        save(Path(a.out), a, c)
    return True


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
    ap.add_argument("--quick-gate", choices=("isa", "full", "none"), default="isa",
                    help="--phase asic: gate mỗi vòng trước khi cộng dồn: isa (riscv-tests ISA), full (ISA + benchmark "
                         "+ ngân sách cycle, không func_check), none")
    ap.add_argument("--bypass", default="", help="M2: thay agent bằng câu trả lời dựng sẵn, vd empty,comment,broken")
    ap.add_argument("--empty-retries", type=int, default=2, help="hỏi lại agent bao nhiêu lần khi patch rỗng")
    ap.add_argument("--noise-json", required=True, help="noise.json của soc_fmax.py --noise")
    ap.add_argument("--history-from", default="", help="thư mục run trước (history.json, best.patch) để đi tiếp")
    ap.add_argument("--cycle-budget", type=float, default=1.03)
    ap.add_argument("--area-budget", type=float, default=1.15)
    ap.add_argument("--ladder", default="10,9,8,7,6,5", help="bậc chu kỳ (ns), giảm dần; baseline đo ở bậc đầu")
    ap.add_argument("--on-met", choices=("next", "stop"), default="next",
                    help="bản được nhận đạt bậc (WNS ≥ 0 sau place): next = xuống bậc kế trong job, stop = dừng job")
    ap.add_argument("--rung-noise", choices=("measure", "keep"), default="measure",
                    help="khi xuống bậc: measure = đo nhiễu trên bản tốt nhất ở bậc mới (3 lần place), keep = giữ ngưỡng")
    ap.add_argument("--repeat-gain", type=float, default=P.GAIN_NS,
                    help="đo lặp: trung vị chu kỳ đạt được của ứng viên phải tốt lên ít nhất chừng này ns (mặc định 0,367)")
    a = ap.parse_args()
    a.period_explicit = any(x == "--period-ns" or x.startswith("--period-ns=") for x in sys.argv[1:])
    D.finish_args(a, ap)
    a.ladder_list = sorted({float(x) for x in a.ladder.split(",") if x.strip()}, reverse=True)
    a.bypass_list = [x.strip() for x in a.bypass.split(",") if x.strip()]
    return a


def load_noise(path: str) -> P.Noise:
    j = json.loads(Path(os.path.expanduser(path)).read_text())
    return P.Noise(**j)


def main():
    a = parse_args()
    t_start = datetime.now(timezone.utc)
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
        period0, a.period_ns = a.period_ns, a.ladder_list[0]       # baseline luôn ở bậc đầu
        _prep, stages = D.run_orfs_point(a, d0, rtl, fakeram, a.orfs_vars, "baseline", "place", mw, tm)
        a.period_ns = period0
        c = Ctx(a, mw, tm, fakeram, state, art, rtl, gate, func_base, stages, noise)
        if a.phase == "asic":
            print(f"pha asic: mỗi vòng tới place; gate nhanh: {a.quick_gate}; cuối đợt gate đủ theo Fmax place", flush=True)
        start = 1
        if a.history_from:
            hdir = Path(os.path.expanduser(a.history_from))
            h = json.loads((hdir / "history.json").read_text())
            c.rounds = [Round(**r) for r in h["rounds"]]
            c.candidates = h.get("candidates", [])
            c.set_best(P.Point(**h["best"]),
                       (hdir / "best.patch").read_text() if (hdir / "best.patch").exists() else "")
            c.measured.update(h.get("measured", {}))
            c.rungs = h.get("rungs", [])
            start = max((r.n for r in c.rounds), default=0) + 1
            if not a.period_explicit and c.best.period_ns:
                a.period_ns = c.best.period_ns
            hn = h.get("noise")
            if hn and abs((hn.get("at_period_ns") or 0) - a.period_ns) < 1e-9:
                c.noise = P.Noise(**hn)
                print(f"ngưỡng nhiễu lấy từ {hdir.name} (đo trên {c.noise.point} @ {a.period_ns:g} ns)", flush=True)
            print(f"đi tiếp từ {hdir}: {len(c.rounds)} vòng, bản tốt nhất {c.best.name} ({D.fmt(c.best.fmax, 3)} MHz "
                  f"@ {D.fmt(c.best.period_ns, 1)} ns)", flush=True)
            if a.phase == "asic" and c.best_patch:
                measure_best(c, f"dựng lại từ {hdir.name} (cache)")
        c.start_best = c.best
        c.start_info = dict(best_info(c))
        c.first_round = start
        if a.phase == "asic":
            if not step_down(c, start - 1):
                raise SystemExit("bản tốt nhất đã đạt bậc cuối (sang M5)" if next_period(a) is None else
                                 f"bản tốt nhất đã đạt {a.period_ns:g} ns và --on-met stop: chạy lại với --on-met next "
                                 f"hoặc --period-ns {next_period(a):g}")
            if (a.rung_noise == "measure" and abs(c.noise.at_period_ns - a.period_ns) > 1e-9):
                measure_best(c, f"đo nhiễu ở {a.period_ns:g} ns (ngưỡng hiện có đo ở {c.noise.at_period_ns:g} ns)",
                             noise=True)
        attach_noise_samples(c)
        print(f"chu kỳ {a.period_ns:g} ns (bậc {', '.join(f'{x:g}' for x in a.ladder_list)}, --on-met {a.on_met}); "
              f"ngưỡng nhiễu: chu kỳ đạt được ±{c.noise.period_ns:.3f} ns, điểm họ ±{c.noise.family_ns:.3f} "
              f"(đo trên {c.noise.point} @ {c.noise.at_period_ns:g} ns); ngân sách {c.budget}", flush=True)
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
            if a.phase == "asic" and c.met_round is not None:
                print(f"\n== r{c.met_round} đạt {a.period_ns:g} ns: kết thúc đợt sớm", flush=True)
                c.met_round = None
                finish_chain(c)
                if not step_down(c, n):
                    break
        if a.phase == "asic":
            finish_chain(c)
    except AgentStop as e:
        print(f"\nDỪNG: {e}", flush=True)
        if c is not None and a.phase == "asic" and c.chain:
            print("cuối đợt cho chuỗi đã cộng dồn", flush=True)
            finish_chain(c)
        raise
    except SystemExit as e:
        print(f"\nDỪNG: {e}", flush=True)
        raise
    finally:
        if c is not None:
            save(out, a, c)
        mw.stop()
        D.prune_orfs(a, ([c.best.name] + c.keep_points) if c is not None else ["baseline"])
        if c is not None:
            D.write(out / "summary.md", summary_md(a, c, tm, mw, t_start))
        print("\n## Thời gian", flush=True)
        for row in tm.rows:
            print(f"  {row['step']:10s} {row['minutes']:8.2f} phút  {'(cache)' if row['cached'] else '       '}  {row['note']}", flush=True)
        print(f"\nKết quả: {out}/summary.md, {out}/history.md, {out}/best.patch, {out}/results.csv", flush=True)
        stop_collector()
        stop_cache()


def summary_md(a, c: Ctx, tm, mw, t_start: datetime) -> str:
    """Tổng hợp một job: bản tốt nhất đầu/cuối job so với baseline, các vòng của job, gate cuối đợt, thời gian, RAM."""
    now = datetime.now(timezone.utc)
    base, b0, b1 = c.base, c.start_best, c.best

    def pct(x, y):
        return f"{(x / y - 1) * 100:+.1f} %" if x and y else "-"

    def row(label, pt, pi=None):
        pi = pi if pi is not None else c.place_info.get(pt.name, {})
        m = med_txt(pt.as_dict())
        return (f"| {label} | {D.fmt(pt.period_ns, 1)} | {D.fmt(pt.fmax, 3)}{f' (trung vị {m})' if m else ''} | {D.fmt(pi.get('wns'))} | {D.fmt(pt.family)} | "
                f"{D.fmt(pt.area, 0)} ({pct(pt.area, base.area)}) | {D.fmt(pt.cycle_cost, 4)} | `{pi.get('crit', '-')}` |")

    L = [f"# soc-fmax job {a.run_name}", "",
         f"- {t_start.isoformat(timespec='minutes')} → {now.isoformat(timespec='minutes')} "
         f"({(now - t_start).total_seconds() / 3600:.2f} giờ), chu kỳ {a.period_ns} ns, pha {a.phase}",
         f"- {'Đi tiếp từ `' + a.history_from + '`' if a.history_from else 'Bắt đầu từ baseline'}; "
         f"agent {a.bypass and 'bypass ' + a.bypass or a.agent_model + ', effort ' + (a.agent_effort or '-')}; "
         f"{a.rounds} vòng, gate nhanh {getattr(a, 'quick_gate', '-')}",
         f"- Ngưỡng nhận (place): chu kỳ đạt được ±{c.noise.period_ns:.3f} ns, điểm họ ±{c.noise.family_ns:.3f} "
         f"(đo trên {c.noise.point} @ {c.noise.at_period_ns:g} ns) cho một lần đo; hứa hẹn thì đo lặp (3 biến thể), nhận "
         f"khi trung vị tốt lên ≥ {c.budget.gain_ns:.3f} ns; ngân sách cycle {c.budget.cycle}, diện tích {c.budget.area}",
         f"- Bậc: {', '.join(f'{x:g}' for x in a.ladder_list)} ns, --on-met {a.on_met}, --rung-noise {a.rung_noise}; "
         f"cuối job ở {a.period_ns:g} ns", ""]
    if b1.name != b0.name:
        L.append(f"**Có bản tốt hơn: {b1.name}**, place {D.fmt(b1.fmax, 3)} MHz: "
                 f"{(b1.fmax or 0) - (b0.fmax or 0):+.3f} MHz so với đầu job ({b0.name}), "
                 f"{(b1.fmax or 0) - (base.fmax or 0):+.3f} MHz ({pct(b1.fmax, base.fmax)}) so với baseline.")
    else:
        L.append(f"**Không có bản tốt hơn**: giữ {b1.name} ({D.fmt(b1.fmax, 3)} MHz).")
    L += ["", "| điểm | chu kỳ (ns) | Fmax place (MHz) | WNS (ns) | điểm họ | logic (um2) | cycle | đường găng place |",
          "|---|---|---|---|---|---|---|---|", row("baseline", base)]
    if b0.name != base.name:
        L.append(row(f"đầu job ({b0.name})", b0, c.start_info))
    if b1 is not b0:
        L.append(row(f"**cuối job ({b1.name})**", b1, best_info(c)))
    new_rungs = [x for x in c.rungs if x.get("run") == a.run_name]
    if new_rungs or c.rungs:
        L += ["", "## Bậc chu kỳ", ""]
        L += [f"- {x['period']:g} ns ĐẠT: {x['best']}, WNS {D.fmt(x['wns'])} ns, Fmax {D.fmt(x['fmax'], 3)} MHz "
              f"(sau vòng {x['round']}, job {x['run']}){' **(job này)**' if x.get('run') == a.run_name else ''}"
              for x in c.rungs]
        if not next_period(a) and best_met(c):
            L.append("- Đã đạt bậc cuối: bước tiếp theo là M5.")
    for nr in c.noise_runs:
        L += ["", f"Nhiễu đo trên {nr['point']} @ {nr['period']:g} ns: "
              + ", ".join(f"{s_['name']} {D.fmt(s_['fmax'], 3)} MHz" for s_ in nr["samples"])
              + f" → ngưỡng ±{nr['period_thr']:.3f} ns chu kỳ, điểm họ ±{nr['family_thr']:.3f}"]
    regions = best_info(c).get("regions") or {}
    if regions:
        L += ["", f"Slack theo vùng của {b1.name} (place): " + ", ".join(f"{k} {D.fmt(v)}" for k, v in regions.items())]
    rs = [r for r in c.rounds if r.n >= c.first_round]
    L += ["", "## Các vòng của job", "",
          "| vòng | kết quả | chu kỳ | Fmax floorplan | Fmax place | trung vị đo lặp | WNS place | cycle | logic | phút | patch |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        p = r.point or {}
        res = "**NHẬN**" if r.accepted else ("**ỨNG VIÊN**" if r.candidate else r.status)
        L.append(f"| r{r.n} | {res} | {D.fmt(p.get('period_ns'), 1)} | {D.fmt(p.get('fp_fmax'), 3)} | {D.fmt(p.get('fmax'), 3)} | "
                 f"{med_txt(p)} | {D.fmt(c.place_info.get(f'r{r.n}', {}).get('wns'))} | {D.fmt(p.get('cycle_cost'), 4)} | "
                 f"{D.fmt(p.get('area'), 0)} | {r.minutes:.1f} | {r.patch_stat.split(':')[0] or '-'} |")
    gated = sorted((r for r in rs if r.gate_rank), key=lambda r: (-((r.point or {}).get("period_ns") or 0), r.gate_rank))
    if gated:
        L += ["", "## Gate đủ cuối đợt (theo Fmax place giảm dần)", ""]
        for r in gated:
            L.append(f"- r{r.n} (@ {D.fmt((r.point or {}).get('period_ns'), 1)} ns, lần {r.gate_rank} của đợt, "
                     f"{D.fmt((r.point or {}).get('fmax'), 3)} MHz): "
                     f"{'**NHẬN**' if r.accepted else r.status}, {r.gate_minutes:.1f} phút"
                     + (f"; {r.reasons[0].splitlines()[0][:300]}" if r.reasons and not r.accepted else ""))
    agg: dict = {}
    for row_ in tm.rows:
        k_ = re.sub(r" r\d+~\S+$", " (đo lặp)", row_["step"])
        k_ = re.sub(r" noise@\S+$", " (nhiễu)", re.sub(r" r\d+(@\S+)?$", "", k_))
        x = agg.setdefault(k_, [0, 0.0, 0])
        x[0] += 1
        x[1] += row_["minutes"]
        x[2] += row_["cached"]
    L += ["", "## Thời gian theo bước", "", "| bước | số lần (cache) | phút |", "|---|---|---|"]
    L += [f"| {k} | {v[0]} ({v[2]}) | {v[1]:.1f} |" for k, v in agg.items()]
    agent_min = sum(r.agent_minutes for r in rs)
    L.append(f"| agent | {len(rs)} vòng | {agent_min:.1f} |")
    peaks = {k: v.get("_tổng") for k, v in mw.peaks().items() if v.get("_tổng")}
    if peaks:
        k = max(peaks, key=peaks.get)
        L += ["", f"RAM đỉnh {peaks[k]:.1f} GB (bước {k})."]
    vs_ = getattr(a, "orfs_variants", {})
    kept = ", ".join(f"`{vs_[k]}` ({k})" for k in [b1.name] + c.keep_points if k in vs_)
    L += ["", f"ORFS giữ lại: {kept or '(không có variant trong job)'}; variant khác của job đã xoá"
          + (" (--keep-orfs: giữ hết)" if getattr(a, "keep_orfs", False) else "") + ".",
          "", "Chi tiết: `history.md`, patch: `best.patch`, số đo: `results.csv`."]
    return "\n".join(L) + "\n"


def save(out: Path, a, c: Ctx):
    D.write(out / "history.json", json.dumps({"rounds": [dataclasses.asdict(r) for r in c.rounds],
                                              "best": c.best.as_dict(), "measured": c.measured,
                                              "candidates": c.candidates, "rungs": c.rungs,
                                              "noise": dataclasses.asdict(c.noise)}, indent=2, default=str))
    D.write(out / "best.patch", c.best_patch)
    D.write(out / "candidates.json", json.dumps(c.candidates, indent=2))
    D.write(out / "history.md", history_md(a, c.base, c.best, c.noise, c.rounds, c.rungs))


if __name__ == "__main__":
    main()
