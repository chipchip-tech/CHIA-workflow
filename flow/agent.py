"""agent.py - Pha 2 (agent Claude tối ưu từng bước) của một project, do CHIA điều phối.

Chạy từ GỐC repo, cluster bật đủ khối (`chia-up-blocks.py --fpga --asic`):
  chia job submit --working-dir . -- python -m flow.agent --project projects/demo-alu/NoCG [--baseline <run_dir>]
      [--steps sim,fpga,asic] [--rounds 2] [--model opus] [--effort high] [--agent-timeout 1200]
Lệnh tiện hơn: python flow/run.py <project> agent [tham số như trên].
--baseline mặc định là lần baseline mới nhất có KẾT QUẢ OK và chạy đủ các bước của project.

Với từng bước (Sim → FPGA → ASIC), tối đa --rounds vòng:
  1. chép cây nguồn của bản tốt nhất hiện tại (lúc đầu là bản đã chuẩn bị như baseline) sang thư mục vòng <step>-r<k>/;
  2. task CHIA trên khối claude: Claude Code đọc báo cáo của bản tốt nhất (agent/context/), sửa file ĐƯỢC PHÉP của bước
     đó ngay trong thư mục vòng (khối claude không có Verilator/Vivado/ORFS); sửa ngoài phạm vi bị hoàn lại;
  3. CHIA đo lại bước đó trên các khối như baseline (flow/orchestrate.py, cùng đồ thị task) và gate:
       sim   chỉ sửa sim/tb_<dut>.v; nhận nếu vẫn PASS, functional coverage tăng và line/toggle/branch không giảm
             (đã đủ mọi bin thì chỉ nhận khi line/toggle/branch tăng)
       fpga  chỉ sửa fpga/build.tcl, fpga/sources.f, file .v mới trong fpga/; gate: fpga_sim (lọc sớm) PASS, có .bit,
             đúng clock của baseline, sign-off (WNS >= 0, WHS >= 0, methodology không Critical), SAIF, GLS; nhận nếu
             bản tốt nhất chưa đạt sign-off, hoặc không kém ở WNS và công suất động, LUT+FF kém <= 3 %, tốt hơn ở một
       asic  chỉ sửa asic/config.mk (không đổi design, file nguồn, SDC); gate như baseline (GDS, slack, DRC router và
             KLayout, antenna, LVS, STA mọi corner, GLS); nhận nếu không kém quá 1 % ở diện tích cell, diện tích die,
             công suất và tốt hơn quá 1 % ở một
  4. agent không đổi gì (cho là không còn gì đáng sửa) thì dừng bước đó.
rtl/ không ai sửa: tối ưu riêng của FPGA không mang sang ASIC.
Kết quả: <WORK_DIR>/<group>/<project>/<run_id>/ (source/, <step>-r<k>/, results/agent.json, results/compare.md,
status.html, profile/). Vòng bị loại được dọn file lớn.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import ray
from chia.base.ChiaFunction import ChiaFunction
from chia.trace.profiler import start_collector

from flow import status as ST
from flow.baseline import check_cluster, finish_profile, task_logger
from flow.common import profile_info, sha256, timed
from flow.orchestrate import Runner, asic_merged, first_failed, run_asic, run_fpga, run_sim
from flow.project import Project
from flow.tasks_fpga import sources

# Không chép sang vòng mới (kết quả build), và là thứ được dọn ở vòng bị loại.
BUILD_NAMES = {"build", "out", "reports", "orfs", "make", "xsim.dir", ".Xil", "agent", "xsim_pw", "xsim_gls"}
BUILD_FILES = {"layout.png", "xsim.log", "xsim_gls.log"}
ORFS_HOST = os.path.realpath(os.path.expanduser(os.environ.get("ORFS_DIR", "~/tools/OpenROAD-flow-scripts")))
NO_CHANGE = "no change by the agent"
FPGA_FIXED = {"fpga_top.v", "tb_fpga_top.v", "tb_power.v", "tb_gls.v"}


def fpga_fixed(cfg: dict) -> set[str]:
    """File .v trong fpga/ agent không được sửa: vỏ board và testbench của nó (mode board), cộng fpga.fixed."""
    return (FPGA_FIXED if cfg["fpga"].get("mode", "board") == "board" else set()) | set(cfg["fpga"]["fixed"])


# ---------------------------------------------------------------------------
# Khối claude: Claude Code chạy trong thư mục vòng
# ---------------------------------------------------------------------------

def _workdir_llm_class():
    """ClaudeCodeLLM với cwd = thư mục vòng và giới hạn thời gian thật (bản streaming gốc của CHIA 2.0.1 chờ không
    giới hạn). Như WorkdirClaudeLLM của project soc-fmax-sky130 (đã chạy thật). Import trễ: chỉ khối claude cần."""
    from chia.models.claude import ClaudeCodeLLM, ClaudeCodeQueryResult

    class WorkdirClaudeLLM(ClaudeCodeLLM):
        def __init__(self, *args, cwd: str, effort: str | None = None, max_turns: int | None = None, **kwargs):
            kwargs.setdefault("log_stream", True)
            super().__init__(*args, **kwargs)
            self.cwd = cwd
            extra = (["--effort", effort] if effort else []) + (["--max-turns", str(max_turns)] if max_turns else [])
            self.extra_cli_args = extra + list(self.extra_cli_args or [])
            self.stream_text = ""
            self.timed_out = False

        def _run_claude_streaming(self, user_message, tools=None):
            cmd = self._build_cmd(tools)
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            parts: list[str] = []
            stream: list[str] = [f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {' '.join(cmd[:4])} (cwd {self.cwd})\n\n"]
            lock = threading.Lock()

            class _Tee:
                def write(self, s):
                    stream.append(s)

                def flush(self):
                    pass

            tee = _Tee()
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=env, cwd=self.cwd, start_new_session=True)
            proc.stdin.write(user_message)
            proc.stdin.close()
            err: list[str] = []

            def drain_out():
                for line in proc.stdout:
                    if line.strip():
                        with lock:
                            self._process_event_line(line.strip(), tee, parts)

            def drain_err():
                for line in proc.stderr:
                    with lock:
                        err.append(line)
                        stream.append(f"[stderr] {line}")

            th = [threading.Thread(target=drain_out, daemon=True), threading.Thread(target=drain_err, daemon=True)]
            for t in th:
                t.start()
            try:
                proc.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait()
                for t in th:
                    t.join(10)
                self.timed_out = True
                self.stream_text = "".join(stream) + f"\n[hết {self.timeout_seconds} s: đã giết claude]\n"
                raise
            for t in th:
                t.join()
            self.stream_text = "".join(stream)
            return ClaudeCodeQueryResult(result="".join(parts), returncode=proc.returncode, stderr="".join(err),
                                         stream_result="")

    return WorkdirClaudeLLM


@ChiaFunction(resources={"claude_creds": 1}, max_retries=0)
@timed
def agent_session(cand: str, prompt: str, model: str, effort: str, max_turns: int, timeout_s: int,
                  name: str = "agent") -> dict:
    """Một phiên Claude Code trong thư mục vòng (WORK_DIR mount cùng đường dẫn vào khối claude). Không raise."""
    from chia.models.claude import AuthenticationError, BillingError, RateLimitError
    t0 = time.time()
    out = {"summary": "", "error": ""}
    if not os.path.isdir(cand):
        out.update(error=f"setup: khối claude không thấy {cand}: thiếu mount WORK_DIR (dựng lại cluster)", ok=False)
        return out
    llm = _workdir_llm_class()(model=model, cwd=cand, effort=effort or None, max_turns=max_turns or None,
                               timeout_seconds=timeout_s, retries=1, logging_name=f"flow_{name}_agent")
    try:
        res = llm.prompt(prompt, [])
        if not res.success:
            out["error"] = "hết giờ" if llm.timed_out else "claude lỗi (xem stream.log)"
        out["summary"] = (res.result or "").strip()[-4000:]
    except RateLimitError as e:
        out["error"] = f"rate_limit: {e}"
    except (AuthenticationError, BillingError) as e:
        out["error"] = f"auth: {e}"
    except Exception as e:      # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    Path(cand, "agent", "stream.log").write_text(llm.stream_text or "")
    out["seconds"] = round(time.time() - t0, 1)
    out["ok"] = not out["error"]
    profile_info(step="agent", cand=os.path.basename(cand), seconds=out["seconds"], error=out["error"])
    return out


# ---------------------------------------------------------------------------
# Phạm vi sửa của từng bước
# ---------------------------------------------------------------------------

class Scope:
    def __init__(self, prj: Project):
        self.prj, self.cfg, self.dut = prj, prj.cfg, prj.dut

    def snapshot(self, root: Path) -> dict[str, bytes]:
        snap = {}
        for sub in self.cfg["src_dirs"]:
            for p in (root / sub).rglob("*"):
                rel = p.relative_to(root)
                if p.is_file() and not any(part in BUILD_NAMES for part in rel.parts) and p.name not in BUILD_FILES:
                    snap[str(rel)] = p.read_bytes()
        return snap

    def allowed(self, step: str, rel: str) -> bool:
        if step == "sim":
            return rel == self.cfg["sim"]["tb"]
        if step == "fpga":
            if rel in ("fpga/build.tcl", "fpga/sources.f"):
                return True
            fixed = {f"fpga/{x}" for x in fpga_fixed(self.cfg)}
            return rel.startswith("fpga/") and rel.count("/") == 1 and rel.endswith(".v") and rel not in fixed
        if step == "asic":
            return rel == "asic/config.mk"
        return False

    def violations(self, step: str, cand: Path, before: dict[str, bytes]) -> list[str]:
        """Kiểm tra nội dung của file được phép sửa (công bằng với baseline: cùng clock, part, design)."""
        bad = []
        if step == "sim":
            tb = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", (cand / self.cfg["sim"]["tb"]).read_text(errors="replace"),
                                                flags=re.S))
            names = [n for n in ("u_fcov", self.cfg["sim"].get("fcov_module")) if n]
            if re.search(r"\b(" + "|".join(map(re.escape, names)) + r")\b|\$coverage|coverage_(?:on|off)", tb):
                bad.append("tb references or disables coverage (fcov is fixed)")
        if step == "fpga":
            tcl = (cand / "fpga/build.tcl").read_text()
            if self.cfg["fpga"]["part"] not in tcl:
                bad.append("build.tcl changes the part")
            code = re.sub(r"#.*", "", tcl)
            for need in ("clock.xdc", "synth_opts.tcl"):
                if need not in code:
                    bad.append(f"build.tcl does not read {need} (clock and variant are fixed by the baseline)")
            if re.search(r"\b(create_clock|create_generated_clock|set_false_path|set_multicycle_path|set_max_delay)\b",
                         code):
                bad.append("build.tcl adds/changes timing constraints (the clock is fixed by the .xdc)")
            try:
                srcs = sources(str(cand / "fpga"))
            except OSError:
                srcs = []
            if self.cfg["fpga"].get("mode", "board") == "board" and f"{self.cfg['fpga']['top']}.v" not in srcs:
                bad.append(f"sources.f lacks {self.cfg['fpga']['top']}.v")
            for s in srcs:
                if not (cand / "fpga" / s).is_file():
                    bad.append(f"sources.f lists a missing file {s}")
        if step == "asic":
            old = mk_vars(before.get("asic/config.mk", b"").decode())
            new = mk_vars((cand / "asic/config.mk").read_text())
            for k in self.cfg["asic"]["fixed_vars"]:
                if old.get(k) != new.get(k):
                    bad.append(f"config.mk changes {k}")
        return bad

    def enforce(self, step: str, cand: Path, before: dict[str, bytes]) -> tuple[list[str], list[str]]:
        """Hoàn lại mọi thay đổi ngoài phạm vi. Trả (file đổi được giữ, file bị hoàn lại)."""
        after = self.snapshot(cand)
        kept, dropped = [], []
        for rel in sorted(set(before) | set(after)):
            if before.get(rel) == after.get(rel):
                continue
            if self.allowed(step, rel):
                kept.append(rel)
                continue
            dropped.append(rel)
            p = cand / rel
            if rel in before:
                p.write_bytes(before[rel])
            else:
                p.unlink(missing_ok=True)
        return kept, dropped

    def copy_tree(self, src: Path, dst: Path) -> None:
        ign = shutil.ignore_patterns(*BUILD_NAMES, *BUILD_FILES)
        for sub in self.cfg["src_dirs"]:
            if (src / sub).is_dir():
                shutil.copytree(src / sub, dst / sub, ignore=ign)


def mk_vars(text: str) -> dict[str, str]:
    """Biến của config.mk (gộp dòng nối bằng \\), để kiểm tra các biến cố định."""
    text = re.sub(r"\\\n", " ", text)
    out = {}
    for m in re.finditer(r"^\s*(?:export\s+)?(\w+)\s*[:?]?=\s*(.*)$", text, re.M):
        out[m.group(1)] = " ".join(m.group(2).split())
    return out


def diff(cand: Path, before: dict[str, bytes], files: list[str]) -> str:
    import difflib
    out = []
    for rel in files:
        a = before.get(rel, b"").decode(errors="replace").splitlines(keepends=True)
        p = cand / rel
        b = p.read_text(errors="replace").splitlines(keepends=True) if p.is_file() else []
        out += difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}")
    return "".join(out)


# ---------------------------------------------------------------------------
# Đo và so sánh
# ---------------------------------------------------------------------------

def measure(R: Runner, step: str, cand: Path, cfg: dict, prj: Project, params: dict, round_: str) -> dict:
    """Chạy lại bước trên các khối như baseline. Trả {ok, metrics, sub: {task: kết quả}, failed_at}."""
    d, plus = str(cand), prj.plusargs(params.get("plusargs"))
    if step == "sim":
        sub = run_sim(R, d, cfg, plus, round_)
    elif step == "fpga":
        sub = run_fpga(R, d, cfg, params.get("power_cycles", 20000), early_sim=True, round_=round_)
        f = sub.get("fpga")
        if f and f.get("ok"):
            want = (params.get("fpga") or {}).get("period_ns")
            got = f.get("clock_period_ns")
            if want is not None and (got is None or abs(got - want) > 1e-6):   # cùng clock với baseline
                f["ok"] = False
                f["log_tail"] = f"round clock ({got} ns) differs from the baseline ({want} ns)\n" + f.get("log_tail", "")
            elif not f.get("signoff_met"):                 # gate của agent: timing + methodology ở clock baseline
                f["ok"] = False
                f["log_tail"] = (f"not signed off: WNS {f.get('wns_ns')} WHS {f.get('whs_ns')} ns, methodology "
                                 f"critical {f.get('methodology_critical')} {f.get('methodology')}\n") + f.get("log_tail", "")
    else:
        sub = run_asic(R, d, cfg, plus, round_)
    failed = first_failed(sub)
    last = asic_merged(sub) if step == "asic" else (list(sub.values())[-1] if sub else {})
    return {"ok": failed is None and bool(sub), "failed_at": failed, "metrics": metrics_of(step, last),
            "sub": {k: {kk: vv for kk, vv in v.items() if kk not in ("metrics_json", "cells")} for k, v in sub.items()}}


SIM_KEYS = ("fcov_hit", "fcov_total", "fcov_miss", "cov_line", "cov_line_total", "cov_toggle", "cov_toggle_total",
            "cov_branch", "cov_branch_total")
FPGA_KEYS = ("wns_ns", "whs_ns", "tns_ns", "timing_met", "signoff_met", "methodology_critical", "methodology", "gls_ok",
             "clock_mhz", "fmax_est_mhz", "lut", "ff", "bufg", "power_total_w", "power_dynamic_w", "power_inst_w",
             "power_dynamic_vl_w", "power_confidence", "saif_nets_matched_pct", "warnings", "critical_warnings",
             "warning_ids", "bitstream")
ASIC_KEYS = ("area_um2", "die_area_um2", "core_area_um2", "util_pct", "n_logic_cells", "n_flop", "n_icg", "n_clkbuf",
             "setup_ws_ns", "hold_ws_ns", "power_total_w", "drc_errors", "drc_klayout", "lvs", "antenna_nets",
             "sta_setup_ws_min_ns", "sta_hold_ws_min_ns", "gls_result", "gds", "layout_png")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def metrics_of(step: str, r: dict) -> dict:
    if step == "sim":
        return {"checked": _int(r.get("checked")), "errors": _int(r.get("errors")), "result": r.get("result"),
                **{k: r.get(k) for k in SIM_KEYS}}
    return {k: r.get(k) for k in (FPGA_KEYS if step == "fpga" else ASIC_KEYS)}


def judge_sim(best: dict, new: dict) -> tuple[bool, str]:
    """Luật Sim: không loại coverage nào của DUT được giảm; nhận khi số bin functional tăng, hoặc (đã đủ mọi bin) khi
    line/toggle/branch tăng. checked chỉ để báo cáo."""
    kinds = [("functional", "fcov_hit"), ("line", "cov_line"), ("toggle", "cov_toggle"), ("branch", "cov_branch")]
    worse, better, notes = [], [], []
    for name, k in kinds:
        a, b = best.get(k), new.get(k)
        if a is None or b is None:
            notes.append(f"{name} ?")
            continue
        notes.append(f"{name} {a} → {b}")
        if b < a:
            worse.append(name)
        elif b > a:
            better.append(name)
    notes.append(f"checked {best.get('checked')} → {new.get('checked')}")
    if best.get("fcov_hit") is None:                     # project không có functional coverage: chỉ line/toggle/branch
        ok = not worse and bool(better)
        why = ("more coverage: " + ", ".join(better)) if ok else (("coverage dropped: " + ", ".join(worse)) if worse
                                                                    else "no new line/toggle/branch coverage")
        return ok, why + " (" + "; ".join(notes) + ")"
    if new.get("fcov_hit") is None:
        return False, "FCOV line not found (" + "; ".join(notes) + ")"
    full = best.get("fcov_hit") is not None and best.get("fcov_hit") == best.get("fcov_total")
    ok = not worse and ("functional" in better or (full and bool(better)))
    if ok:
        why = "more coverage: " + ", ".join(better)
    elif worse:
        why = "coverage dropped: " + ", ".join(worse)
    else:
        why = "no new coverage" + (" (every functional bin already hit)" if full else " (no new functional bin)")
    return ok, why + " (" + "; ".join(notes) + ")"


def judge(step: str, best: dict, new: dict) -> tuple[bool, str]:
    """So bản mới với bản tốt nhất (đã qua gate). Công cụ tất định, nên ngưỡng chỉ bỏ qua sai khác làm tròn."""
    if step == "sim":
        return judge_sim(best, new)
    if step == "fpga":
        if best.get("signoff_met", best.get("timing_met")) is False:   # bản mới đã qua gate nên đạt sign-off
            return True, (f"signs off at {new.get('clock_mhz')} MHz (WNS {best.get('wns_ns')} → {new.get('wns_ns')} "
                          f"ns, methodology critical {best.get('methodology_critical')} → "
                          f"{new.get('methodology_critical')}); the best version did not")
        # Công suất động SAIF, ngưỡng 0,02 mW chỉ bỏ qua làm tròn. LUT+FF được kém tối đa 3 % (chốt 25/11/2026).
        crit = [("WNS", "wns_ns", +1, 0.02), ("dynamic power", "power_dynamic_w", -1, 0.00002),
                ("LUT+FF", None, -1, ("pct", 0.03))]
    else:
        crit = [("cell area", "area_um2", -1, 0.01), ("die area", "die_area_um2", -1, 0.01),
                ("power", "power_total_w", -1, 0.01)]
    worse, better, notes = [], [], []
    for name, key, sign, tol in crit:
        if key is None:
            a, b = (best.get("lut") or 0) + (best.get("ff") or 0), (new.get("lut") or 0) + (new.get("ff") or 0)
        else:
            a, b = best.get(key), new.get(key)
        if a is None or b is None:
            notes.append(f"{name} ?")
            continue
        delta = (b - a) * sign                                   # > 0: tốt hơn
        if isinstance(tol, tuple):                               # ("pct", x): được kém x, tốt hơn khi vượt 0,5
            if delta < -max(0.5, tol[1] * abs(a)):
                worse.append(name)
            elif delta > 0.5:
                better.append(name)
            notes.append(f"{name} {a:g} → {b:g}")
            continue
        thr = tol * abs(a) if step == "asic" else tol            # asic: theo tỉ lệ (1 %)
        if delta < -thr:
            worse.append(name)
        elif delta > thr:
            better.append(name)
        notes.append(f"{name} {a:g} → {b:g}")
    ok = not worse and bool(better)
    why = ("better: " + ", ".join(better)) if ok else (("worse: " + ", ".join(worse)) if worse else "not better")
    return ok, why + " (" + "; ".join(notes) + ")"


# ---------------------------------------------------------------------------
# Prompt và context
# ---------------------------------------------------------------------------

COMMON = """You are the optimization agent of a CHIA flow (Sim -> FPGA -> ASIC) for `{dut}`: {description}
{variant}
Your working directory is a copy of the design tree: rtl/ (shared RTL, READ-ONLY: FPGA-only and ASIC-only changes must
not leak into the other flow), sim/, fpga/, asic/. Context for this step is in agent/context/ (start with
agent/context/README.md). Verilator, Vivado and OpenROAD are NOT installed here: do not try to build or run them. After
you finish, the CHIA loop measures your edits on the real tools and keeps them only if they pass the gate and improve
the result. Edits outside the allowed files below are reverted automatically.
If after evaluating the reports you think nothing worthwhile can be improved in this step, change nothing and say so:
that is an acceptable result.
Finish with a short summary IN ENGLISH (at most 8 lines) for the demo slides: what the reports showed, what you
changed and why, what you expect to improve.
"""

STEP_TEXT = {
    "sim": """STEP: SIMULATION. Evaluate the self-checking testbench {tb} against the RTL: which behaviours and
corner cases are not checked yet ({sim_hints}).
Allowed file: {tb} only. Add stimulus and checks; keep the plusargs and the way the testbench reports its result
({result_rule}). The RTL is the reference behaviour: a new check that fails means your round is rejected, so make sure
the expected values follow the RTL (or the reference algorithm) exactly.{gls_note}
{cov_text}
Gate: lint of the RTL (no new error), Verilator build + run with the same plusargs as baseline passes.""",
    "fpga": """STEP: FPGA. {fpga_tool}, part {part}, clock {mhz} MHz ({period} ns) from fpga/clock.xdc (FIXED).
{fpga_wrapper}
Read the reports of the current best build in agent/context/ (timing, utilization, clock utilization, methodology,
warnings, power_saif.rpt). Power is measured with real activity: the routed netlist is simulated with {power_tb} and
the SAIF is read back (fpga/power_saif.tcl, FIXED); power.rpt is the old vector-less estimate, for reference only.
Goal: if the current best version does not pass sign-off at this clock (WNS >= 0, WHS >= 0 and no Critical Warning or
Error in report_methodology, e.g. TIMING-14 LUT on the clock tree), make it pass; otherwise improve
WNS and/or dynamic power (SAIF) and/or LUT+FF. WNS and dynamic power must not get worse; LUT+FF may grow by at most 3 %.
Allowed files: fpga/build.tcl (synthesis/implementation options and directives; do NOT add or change clocks or timing
exceptions, do NOT change the part), fpga/sources.f (the Verilog files read by Vivado), new Verilog files
fpga/<name>.v (for example an FPGA-specific implementation of a module from rtl/ with the SAME module name and ports;
then replace the rtl/ file by yours in sources.f). Not allowed: rtl/, {fpga_fixed}fpga/power_saif.tcl,
fpga/synth_opts.tcl, fpga/sim_cfg.tcl, the .xdc files. build.tcl must keep reading clock.xdc and sourcing synth_opts.tcl.
Gate: {fpga_gate}WNS >= 0 and WHS >= 0 at the clock of clock.xdc, no Critical Warning/Error in report_methodology,
SAIF power measured, and the gate-level simulation of the routed netlist ({gls_tb}) passes.""",
    "asic": """STEP: ASIC. OpenROAD-flow-scripts, {platform}, RTL -> GDS, clock from asic/constraint.sdc (fixed).
Read the reports and metrics of the current best run in agent/context/ (ORFS final report, synthesis statistics,
metrics JSON, ORFS variable reference if present).
Goal: reduce standard-cell area, die area and/or power (power is OpenSTA's default-activity estimate) by at least 1 %
in one of them without making the others worse, with timing still closed (setup and hold slack >= 0) and no DRC.
Allowed file: asic/config.mk (ORFS variables such as CORE_UTILIZATION, PLACE_DENSITY, ABC_AREA, synthesis, repair and
CTS options). Do NOT change {fixed_vars}.
Gate: GDS written, setup/hold slack >= 0, router DRC errors = 0, no antenna violation, KLayout DRC sign-off clean (full
rule deck on the final GDS), LVS clean (KLayout: the layout GDS matches the CDL netlist), post-route STA with
setup/hold slack >= 0 at every corner ({corners}), and the gate-level simulation of the routed netlist (6_final.v with
the cell models) passes the testbench. Too high a utilization fails placement or routing and the round is rejected.""",
}


def build_prompt(step: str, prj: Project, params: dict) -> str:
    c = prj.cfg
    sim, fp = c["sim"], c["fpga"]
    ooc = fp.get("mode", "board") == "ooc"
    f = params.get("fpga") or {"period_ns": fp["period_ns"]}
    if sim.get("fcov_module"):
        cov = (f"Coverage is measured, not counted by you: sim/fcov_{prj.dut}.sv (FIXED, bound into the DUT) defines the "
               "functional coverage bins of the specification and prints `FCOV: hit=<n> total=<m>` and `FCOV_MISS: <bins "
               "not hit>` (see sim.log in the context); Verilator also measures line/toggle/branch coverage of the DUT. "
               f"Do not edit or reference fcov_{prj.dut}.sv / u_fcov from the testbench (that round is rejected). The "
               "round is accepted only if no coverage kind of the DUT decreases and the number of functional bins hit "
               "increases (when every bin is already hit: only if line/toggle/branch coverage increases). Adding "
               "comparisons alone does not count.")
    else:
        cov = ("Coverage is measured by Verilator: line, toggle and branch coverage of the RTL files (metrics.json in the "
               "context). The round is accepted only if none of them decreases and at least one increases. Adding "
               "comparisons that exercise nothing new does not count.")
    rule = ("the final `RESULT: PASS|FAIL k=v ...` line and its counters; `checked` must count every comparison"
            if not sim.get("pass_patterns") else
            "every line matching " + ", ".join(f"/{p}/" for p in sim["pass_patterns"]) + " must appear and no line "
            "matching " + (", ".join(f"/{p}/" for p in sim.get("fail_patterns") or []) or "a failure pattern"))
    fixed = sorted(fpga_fixed(c))
    fmt = {**{k: v for k, v in f.items() if isinstance(v, (int, float, str))}, "dut": prj.dut, "tb": sim["tb"],
           "period": f"{f['period_ns']:g}", "mhz": f"{1000 / f['period_ns']:.4g}", "part": fp["part"],
           "fpga_tool": fp["tool"], "top": fp["top"], "platform": c["asic"]["platform"],
           "fpga_fixed": "".join(f"fpga/{x}, " for x in fixed),
           "fixed_vars": ", ".join(c["asic"]["fixed_vars"]), "sim_hints": c["agent"]["sim_hints"] or "...",
           "cov_text": cov, "result_rule": rule,
           "gls_note": (" The same testbench also runs on the synthesized netlists (gate-level simulation, with the macro "
                        "GLS defined): do not read signals inside the DUT, only its ports, or the gate-level step fails."
                        if c["generated"].get("gls_from_sim") else ""),
           "power_tb": f"the simulation testbench {sim['tb']}" if ooc else "fpga/tb_power.v",
           "gls_tb": f"the simulation testbench {sim['tb']}" if ooc else "fpga/tb_gls.v, FIXED",
           "fpga_gate": ("the out-of-context implementation (synthesis, place, route; no board, no bitstream) completes, "
                         if ooc else f"fpga/tb_{fp['top']}.v in xsim with the UNISIM library prints RESULT: PASS "
                         f"{fp['top']}, the bitstream is written, "),
           "corners": "tt" + (f" and tt with +/-{c['asic']['ocv_derate'] * 100:g} % OCV derate"
                              if c["asic"].get("ocv_derate") else "") + ", plus real corner libraries if installed"}
    wrapper = c["agent"]["fpga_wrapper"] or ("The block is implemented out of context (no board wrapper, I/O "
                                             "unconstrained)." if ooc else "")
    fmt["fpga_wrapper"] = wrapper.format(**fmt)
    return (COMMON.format(dut=prj.dut, description=c["agent"]["description"], variant=c["agent"]["variant"])
            + "\n" + STEP_TEXT[step].format(**fmt) + "\n")


def write_context(step: str, prj: Project, cand: Path, best_dir: Path, best_m: dict, base_m: dict,
                  history: list) -> None:
    c = cand / "agent" / "context"
    c.mkdir(parents=True, exist_ok=True)
    files, metrics = [], {}

    def put(src: Path, name: str, limit: int = 300_000):
        if src.is_file():
            (c / name).write_text(src.read_text(errors="replace")[-limit:])
            files.append(name)

    if step == "sim":
        put(best_dir / "sim" / "build" / "sim.log", "sim.log")
    elif step == "fpga":
        for f in sorted((best_dir / "fpga" / "reports").glob("*.rpt")):
            put(f, f"fpga_{f.name}")
        log = best_dir / "fpga" / "out" / "vivado.log"
        if log.is_file():
            warn = [ln for ln in log.read_text(errors="replace").splitlines() if "WARNING" in ln]
            (c / "vivado_warnings.txt").write_text("\n".join(warn[:500]))
            files.append("vivado_warnings.txt")
    else:
        sub = Path(prj.cfg["asic"]["platform"], prj.dut, "base")
        for f in sorted((best_dir / "asic" / "orfs" / "reports" / sub).glob("*")):
            if f.suffix in (".rpt", ".txt", ".log"):
                put(f, f"asic_{f.name}")
        for f in sorted((best_dir / "asic" / "orfs" / "logs" / sub).glob("*.json")):
            try:
                metrics.update(json.loads(f.read_text()))
            except (OSError, ValueError):
                pass
        (c / "asic_metrics.json").write_text(json.dumps(metrics, indent=1))
        files.append("asic_metrics.json")
        put(Path(ORFS_HOST, "flow", "scripts", "variables.yaml"), "orfs_variables.yaml", 2_000_000)
    if not [f for f in files if f not in ("asic_metrics.json", "orfs_variables.yaml")] or (step == "asic" and not metrics):
        print(f"[agent {prj.name}] CẢNH BÁO: context {step} không có báo cáo từ {best_dir}", flush=True)
    (c / "metrics.json").write_text(json.dumps({"baseline": base_m, "current_best": best_m}, indent=2,
                                               ensure_ascii=False, default=str))
    (c / "history.json").write_text(json.dumps(history, indent=2, ensure_ascii=False, default=str))
    (c / "README.md").write_text(
        f"# Context for step {step} ({prj.name})\n\n"
        "- metrics.json: metrics of the baseline and of the current best version (what you must beat)\n"
        "- history.json: earlier rounds of this agent run (changes, result, accepted or not)\n"
        + "".join(f"- {f}\n" for f in files if f not in ("metrics.json", "history.json")))


def slim(d: Path, prj: Project, keep_outputs: bool) -> None:
    """Dọn file lớn của một vòng. Luôn dọn binary/checkpoint/ORFS objects; vòng bị loại dọn cả bitstream, GDS, VCD."""
    junk = [d / "sim/build/obj", d / "fpga/out/post_route.dcp", d / "fpga/xsim.dir", d / "fpga/.Xil",
            d / "fpga/xsim_pw/xsim.dir", d / "fpga/xsim_gls", d / "asic/orfs/objects", d / "asic/gls/build/obj"]
    res = d / "asic/orfs/results" / prj.cfg["asic"]["platform"] / prj.dut / "base"
    if res.is_dir():
        junk += [f for f in res.glob("*.odb") if f.name != "6_final.odb"]
    if not keep_outputs:
        junk += [d / "sim/build/wave.vcd", d / f"fpga/out/{prj.cfg['fpga']['top']}.bit", res]
    for p in junk:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def latest_baseline(prj: Project, work: Path) -> Path | None:
    for d in sorted(prj.run_root(work).glob("baseline-*"), reverse=True):
        try:
            rep = json.loads((d / "results" / "baseline.json").read_text())
            if rep.get("stopped_at") is None and set(rep.get("params", {}).get("stages", [])) >= set(prj.steps):
                return d
        except (OSError, ValueError):
            continue
    return None


def base_metrics(rep: dict) -> dict:
    s = rep["steps"]
    return {"sim": metrics_of("sim", s.get("sim_run", {})), "fpga": metrics_of("fpga", s.get("fpga", {})),
            "asic": metrics_of("asic", s.get("asic", {}))}


ROWS = {
    "sim": [("Functional coverage (bins)", "fcov_hit"), ("Line (DUT)", "cov_line"), ("Toggle (DUT)", "cov_toggle"),
            ("Branch (DUT)", "cov_branch"), ("Checks", "checked"), ("Errors", "errors")],
    "fpga": [("Clock (MHz)", "clock_mhz"), ("Timing met", "timing_met"), ("Sign-off met", "signoff_met"),
             ("Methodology critical", "methodology_critical"), ("GLS", "gls_ok"), ("WNS (ns)", "wns_ns"),
             ("WHS (ns)", "whs_ns"), ("Fmax est. (MHz)", "fmax_est_mhz"), ("LUT", "lut"), ("FF", "ff"), ("BUFG", "bufg"),
             ("Dynamic power SAIF (W)", "power_dynamic_w"), ("Instance power SAIF (W)", "power_inst_w"),
             ("Total power SAIF (W)", "power_total_w"), ("Dynamic power vector-less (W)", "power_dynamic_vl_w"),
             ("Warnings", "warnings")],
    "asic": [("Cell area (µm²)", "area_um2"), ("Die area (µm²)", "die_area_um2"), ("Util (%)", "util_pct"),
             ("Cells", "n_logic_cells"), ("ICG", "n_icg"), ("Clock buffers", "n_clkbuf"),
             ("Setup WS (ns)", "setup_ws_ns"), ("Hold WS (ns)", "hold_ws_ns"), ("Power (W)", "power_total_w"),
             ("DRC router", "drc_errors"), ("DRC KLayout", "drc_klayout"), ("LVS", "lvs"),
             ("Antenna (nets)", "antenna_nets"), ("STA setup WS min (ns)", "sta_setup_ws_min_ns"),
             ("STA hold WS min (ns)", "sta_hold_ws_min_ns"), ("GLS", "gls_result")],
}


def compare_md(report: dict) -> str:
    out = [f"# {report['project']}: baseline / agent\n", f"Baseline: `{report['baseline']}`  \nAgent: `{report['run_dir']}`\n"]
    for step in report["steps"]:
        st = report["result"][step]
        b, a = st["baseline"], st["best"]
        out.append(f"\n## {step.upper()}\n\n| | Baseline | Agent | Change |\n|---|---|---|---|")
        for name, k in ROWS[step]:
            x, y = b.get(k), a.get(k)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) and x:
                ch = f"{(y - x) / abs(x) * 100:+.1f} %" if step != "fpga" or not k.endswith("_ns") else f"{y - x:+.3f}"
            else:
                ch = ""
            out.append(f"| {name} | {x if x is not None else '-'} | {y if y is not None else '-'} | {ch} |")
        out.append("")
        for r in [r for r in report["rounds"] if r["step"] == step]:
            mark = "ACCEPTED" if r.get("accepted") else "REJECTED"
            out.append(f"- **{r['round']}** {mark}: {r.get('verdict', '')}  ")
            if r.get("changed"):
                out.append(f"  changed: {', '.join(r['changed'])}  ")
            if r.get("summary"):
                out.append("  > " + r["summary"].replace("\n", "\n  > "))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="thư mục project (có project.py), tính từ gốc repo")
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")))
    ap.add_argument("--baseline", help="thư mục lần chạy baseline (mặc định: bản OK mới nhất)")
    ap.add_argument("--run-id", default=time.strftime("agent-%Y%m%d-%H%M%S"))
    ap.add_argument("--steps", help="mặc định: steps của project")
    ap.add_argument("--rounds", type=int, default=2, help="số vòng tối đa mỗi bước (mặc định 2)")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--effort", default="", help="--effort của Claude Code (để trống: mặc định)")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--agent-timeout", type=int, default=1200, help="giây mỗi phiên agent (mặc định 1200)")
    args = ap.parse_args()

    prj = Project(args.project)
    cfg, scope, tag = prj.cfg, Scope(prj), f"[agent {prj.name}]"
    work = Path(args.work_dir)
    base_dir = Path(args.baseline) if args.baseline else latest_baseline(prj, work)
    if not base_dir or not (base_dir / "results" / "baseline.json").is_file():
        raise SystemExit("LỖI: không thấy baseline OK: chạy baseline trước hoặc chỉ --baseline")
    base_rep = json.loads((base_dir / "results" / "baseline.json").read_text())
    if base_rep.get("cfg") is None:
        raise SystemExit(f"LỖI: {base_dir} là baseline của bản flow cũ (trước flow/): chạy lại baseline")
    steps = [s.strip() for s in (args.steps or ",".join(prj.steps)).split(",") if s.strip()]
    run_dir = prj.run_root(work) / args.run_id
    if run_dir.exists():
        raise SystemExit(f"LỖI: {run_dir} đã có, chọn --run-id khác")
    (run_dir / "results").mkdir(parents=True)

    ray.init(address="auto", ignore_reinit_error=True)
    check_cluster(steps, [("claude_creds", "claude")])
    start_collector(log_dir=str(run_dir / "profile"))

    base_m = base_metrics(base_rep)
    params = base_rep["params"]
    report = {"project": prj.name, "group": prj.group, "demo": prj.name, "dut": prj.dut, "phase": "agent",
              "run_id": args.run_id, "run_dir": str(run_dir), "cfg": cfg, "baseline": str(base_dir), "steps": steps,
              "model": args.model, "effort": args.effort, "rounds": [], "result": {}, "base_metrics": base_m,
              "stopped": None, "t0": time.time()}
    write = lambda final=False: ST.write(report, final)
    R = Runner(report, write, task_logger(tag))
    print(f"{tag} run_dir = {run_dir}\n{tag} baseline = {base_dir}", flush=True)
    write()
    print(f"{tag} trạng thái: {run_dir / 'status.html'}", flush=True)

    # Nguồn của vòng đầu: cây của project (cùng RTL với baseline, kiểm bằng sha256), chuẩn bị như baseline (clock.xdc,
    # sources.f, synth_opts.tcl theo params["fpga"]; cây project để clock mặc định nên không dùng thẳng được).
    if sha256(sorted((prj.dir / "rtl").glob("*.v"))) != base_rep.get("rtl_sha256"):
        raise SystemExit(f"LỖI: rtl/ của project khác rtl/ của baseline {base_dir}: chạy lại baseline")
    best_src = run_dir / "source"
    prj.copy_sources(best_src)
    if params.get("fpga"):
        f = params["fpga"]
        prj.prepare_fpga(best_src, f["period_ns"], f["variant"], f.get("opts"))
    for step in steps:
        best_m = dict(base_m[step])
        step_best_src, step_best_dir = best_src, base_dir
        report["result"][step] = {"baseline": base_m[step], "best": best_m, "best_dir": str(step_best_dir)}
        for k in range(1, args.rounds + 1):
            name = f"{step}-r{k}"
            cand = run_dir / name
            scope.copy_tree(step_best_src, cand)
            before = scope.snapshot(cand)
            write_context(step, prj, cand, step_best_dir, best_m, base_m[step],
                          [r for r in report["rounds"] if r["step"] == step])
            prompt = build_prompt(step, prj, params)
            (cand / "agent" / "TASK.md").write_text(prompt)
            rnd = {"step": step, "round": name, "dir": str(cand), "state": "agent", "accepted": False}
            report["rounds"].append(rnd)
            print(f"{tag} {name}: agent đang đánh giá ...", flush=True)
            s = R.run("agent", agent_session.chia_remote(str(cand), prompt, args.model, args.effort, args.max_turns,
                                                         args.agent_timeout, prj.name), name)
            (cand / "agent" / "summary.md").write_text(s.get("summary", ""))
            kept, dropped = scope.enforce(step, cand, before)
            rnd.update({"agent_seconds": s.get("seconds"), "agent_error": s.get("error"),
                        "summary": s.get("summary", ""), "changed": kept, "reverted": dropped})
            print(f"{tag} {name}: agent {s.get('seconds') or 0:.0f}s @ {s.get('host')}, đổi {kept or 'không'}"
                  + (f", hoàn lại {dropped}" if dropped else "") + (f", LỖI {s['error']}" if s.get("error") else ""),
                  flush=True)
            if (s.get("error") or "").startswith(("rate_limit", "auth", "setup")):
                report["stopped"] = f"{name}: {s['error']}"
                rnd["state"] = "done"
                break
            if not kept:
                rnd.update(verdict=NO_CHANGE, state="done")
                slim(cand, prj, keep_outputs=False)
                write()
                print(f"{tag} {name}: agent không đổi gì, xong bước {step}", flush=True)
                break
            (cand / "agent" / "changes.diff").write_text(diff(cand, before, kept))
            bad = scope.violations(step, cand, before)
            if bad:
                rnd.update(verdict="rule violation: " + "; ".join(bad), state="done")
                print(f"{tag} {name}: LOẠI ({rnd['verdict']})", flush=True)
                slim(cand, prj, keep_outputs=False)
                write()
                continue
            rnd["state"] = "measure"
            m = measure(R, step, cand, cfg, prj, params, name)
            rnd["measure"] = m
            if not m["ok"]:
                rnd["verdict"] = f"gate failed at {m['failed_at']}"
                tail = m["sub"].get(m["failed_at"], {}).get("log_tail", "")
                (cand / "agent" / "gate_fail.txt").write_text(tail)
                rnd["gate_fail_tail"] = tail[-1500:]
            else:
                ok, why = judge(step, best_m, m["metrics"])
                rnd["accepted"], rnd["verdict"] = ok, why
                if ok:
                    best_m, step_best_src, step_best_dir = m["metrics"], cand, cand
                    report["result"][step] = {"baseline": base_m[step], "best": best_m, "best_dir": str(step_best_dir)}
            rnd["state"] = "done"
            print(f"{tag} {name}: {'NHẬN' if rnd['accepted'] else 'LOẠI'} ({rnd['verdict']})", flush=True)
            write()
            slim(cand, prj, keep_outputs=rnd["accepted"])
        report["result"][step] = {"baseline": base_m[step], "best": best_m, "best_dir": str(step_best_dir)}
        report.setdefault("done_steps", []).append(step)
        write()
        best_src = step_best_src
        if report["stopped"]:
            break

    finish_profile(run_dir, report)
    (run_dir / "results" / "agent.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    (run_dir / "results" / "compare.md").write_text(compare_md(report))
    write(True)
    print(f"{tag} so sánh: {run_dir / 'results' / 'compare.md'}", flush=True)
    if report.get("profile"):
        print(f"{tag} timeline CHIA: {report['profile']}", flush=True)
    acc = [r["round"] for r in report["rounds"] if r["accepted"]]
    print(f"KẾT QUẢ: {'DỪNG ' + report['stopped'] if report['stopped'] else 'OK'}; vòng được nhận: {acc or 'không có'}",
          flush=True)
    raise SystemExit(1 if report["stopped"] else 0)


if __name__ == "__main__":
    main()
