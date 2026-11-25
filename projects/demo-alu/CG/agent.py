"""agent.py - Pha 2 (agent Claude tối ưu từng bước) của demo CG, do CHIA điều phối.

Chạy từ thư mục chứa file này (projects/demo-alu/CG), cluster bật đủ khối (`chia-up-blocks.py --fpga --asic`):
  chia job submit --working-dir . -- python agent.py [--baseline <run_dir>] [--steps sim,fpga,asic] [--rounds 2]
      [--model opus] [--effort high] [--agent-timeout 1200]
--baseline mặc định là lần baseline mới nhất có KẾT QUẢ OK trong <WORK_DIR>/demo-alu/CG/.

Với từng bước (Sim → FPGA → ASIC), tối đa --rounds vòng:
  1. chép cây nguồn của bản tốt nhất hiện tại (lúc đầu là baseline) sang thư mục vòng <step>-r<k>/;
  2. task CHIA trên khối claude: Claude Code đọc báo cáo của bản tốt nhất (agent/context/), đánh giá, sửa file
     ĐƯỢC PHÉP của bước đó ngay trong thư mục vòng (khối claude không có Verilator/Vivado/ORFS, không tự build);
     sửa ngoài phạm vi bị hoàn lại;
  3. CHIA đo lại bước đó trên các khối như baseline (dùng lại task của baseline.py) và gate:
       sim   chỉ sửa sim/tb_*.v (thêm kiểm tra); nhận nếu vẫn PASS, functional coverage (bin của sim/fcov_*.sv, cố
             định) tăng và line/toggle/branch của DUT không giảm; đã đủ mọi bin thì chỉ nhận khi line/toggle/branch tăng
       fpga  chỉ sửa fpga/ (build.tcl, sources.f, file .v mới riêng cho FPGA); gate: tb_fpga_top PASS trong xsim
             (thư viện UNISIM), có .bit, đúng clock của baseline (fpga/clock.xdc), WNS >= 0 và WHS >= 0; nhận nếu
             bản tốt nhất chưa đạt timing (baseline ở clock cao), hoặc không kém ở WNS, công suất động (SAIF),
             LUT+FF kém không quá 3 %, và tốt hơn ở ít nhất một
       asic  chỉ sửa asic/config.mk (không đổi design, file nguồn, SDC); gate như baseline (GDS, slack >= 0,
             DRC = 0, LVS khớp); nhận nếu không kém ở
             diện tích cell, diện tích die, công suất và tốt hơn ít nhất 1 % ở một
  4. agent không đổi gì (cho là không còn gì đáng sửa) thì dừng bước đó.
rtl/ không ai sửa: tối ưu riêng của FPGA không mang sang ASIC (docs/demo-task.md).

Kết quả: <WORK_DIR>/demo-alu/CG/<run_id>/
    <step>-r<k>/          cây nguồn của vòng + kết quả đo; agent/ (TASK.md, context/, summary.md, stream.log)
    results/agent.json    mọi vòng; results/compare.md bảng baseline / agent cho slide
    status.html           trang trạng thái (tự làm mới, mở bằng trình duyệt; xem status_page.py)
    profile/              timeline CHIA
Vòng bị loại được dọn file lớn (bitstream, checkpoint, GDS, VCD, binary) để tiết kiệm đĩa.
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
from chia.base.ChiaFunction import ChiaFunction, get
from chia.trace.profiler import get_profiler, start_collector, stop_collector

import baseline as B
import status_page as S

DEMO, DUT = B.DEMO, B.DUT
SRC_DIRS = ("rtl", "sim", "fpga", "asic")
# Không chép sang vòng mới (kết quả build), và là thứ được dọn ở vòng bị loại.
BUILD_NAMES = {"build", "out", "reports", "orfs", "make", "xsim.dir", ".Xil", "agent"}
BUILD_FILES = {"layout.png", "xsim.log"}
ORFS_HOST = os.path.realpath(os.path.expanduser(os.environ.get("ORFS_DIR", "~/tools/OpenROAD-flow-scripts")))


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
def agent_session(cand: str, prompt: str, model: str, effort: str, max_turns: int, timeout_s: int) -> dict:
    """Một phiên Claude Code trong thư mục vòng (WORK_DIR mount cùng đường dẫn vào khối claude). Không raise."""
    from chia.models.claude import AuthenticationError, BillingError, RateLimitError
    t0 = time.time()
    out = {"summary": "", "error": "", "host": os.uname().nodename}
    if not os.path.isdir(cand):
        out["error"] = f"setup: khối claude không thấy {cand}: thiếu mount WORK_DIR (dựng lại cluster)"
        return out
    llm = _workdir_llm_class()(model=model, cwd=cand, effort=effort or None, max_turns=max_turns or None,
                               timeout_seconds=timeout_s, retries=1, logging_name=f"demo_{DEMO}_agent")
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
    get_profiler().add_info({"step": "agent", "cand": os.path.basename(cand), "seconds": out["seconds"],
                             "error": out["error"]})
    return out


# ---------------------------------------------------------------------------
# Khối vivado: kiểm tra chức năng của thay đổi FPGA bằng xsim (có UNISIM, ví dụ BUFGCE)
# ---------------------------------------------------------------------------

def _sources(fpga_dir: str) -> list[str]:
    lines = Path(fpga_dir, "sources.f").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


@ChiaFunction(resources={"vivado": 1})
def fpga_sim(run_dir: str) -> dict:
    """tb_fpga_top.v (khung 4096 chu kỳ, chữ ký đúng → PASS, chữ ký sai → FAIL) với đúng các file của sources.f."""
    fpga = os.path.join(run_dir, "fpga")
    srcs = " ".join(_sources(fpga))
    cmd = ("rm -rf xsim.dir && xvlog -nolog tb_fpga_top.v " + srcs + " $XILINX_VIVADO/data/verilog/src/glbl.v"
           " && xelab -nolog --timescale 1ns/1ps -L unisims_ver -s tbsim tb_fpga_top glbl"
           " && xsim -nolog tbsim -R")
    rc, log, sec = B._sh(cmd, fpga, timeout=1200)
    Path(fpga, "xsim.log").write_text(log)
    m = re.search(r"^RESULT: (PASS|FAIL) fpga_top", log, re.M)
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec,
           "host": os.uname().nodename, "result": m.group(0) if m else None, "log_tail": log[-3000:]}
    get_profiler().add_info({"step": "fpga_sim", "ok": res["ok"], "seconds": sec})
    return res


# ---------------------------------------------------------------------------
# Phạm vi sửa của từng bước
# ---------------------------------------------------------------------------

def _snapshot(root: Path) -> dict[str, bytes]:
    snap = {}
    for sub in SRC_DIRS:
        for p in (root / sub).rglob("*"):
            rel = p.relative_to(root)
            if p.is_file() and not any(part in BUILD_NAMES for part in rel.parts) and p.name not in BUILD_FILES:
                snap[str(rel)] = p.read_bytes()
    return snap


def _allowed(step: str, rel: str) -> bool:
    if step == "sim":
        return rel == f"sim/tb_{DUT}.v"
    if step == "fpga":
        if rel in ("fpga/build.tcl", "fpga/sources.f"):
            return True
        # icg_fpga_*.v: biến thể FPGA viết tay của baseline CG (agent dùng được qua sources.f, không sửa).
        fixed = {"fpga/fpga_top.v", "fpga/tb_fpga_top.v", "fpga/tb_power.v", "fpga/icg_fpga_bufgce.v",
                 "fpga/icg_fpga_ce.v"}
        return rel.startswith("fpga/") and rel.count("/") == 1 and rel.endswith(".v") and rel not in fixed
    if step == "asic":
        return rel == "asic/config.mk"
    return False


def _mk_vars(text: str) -> dict[str, str]:
    """Biến của config.mk (gộp dòng nối bằng \\), để kiểm tra các biến cố định."""
    text = re.sub(r"\\\n", " ", text)
    out = {}
    for m in re.finditer(r"^\s*(?:export\s+)?(\w+)\s*[:?]?=\s*(.*)$", text, re.M):
        out[m.group(1)] = " ".join(m.group(2).split())
    return out


def _rule_violations(step: str, cand: Path, before: dict[str, bytes]) -> list[str]:
    """Kiểm tra nội dung của file được phép sửa (giữ công bằng với baseline: cùng clock, part, design)."""
    bad = []
    if step == "sim":
        tb = re.sub(r"//.*|/\*.*?\*/", "", (cand / f"sim/tb_{DUT}.v").read_text(errors="replace"), flags=re.S)
        if re.search(r"\bu_fcov\b|\bfcov_alu\b|\$coverage|coverage_(?:on|off)", tb):
            bad.append("tb: tham chiếu hoặc tắt coverage (fcov cố định)")
    if step == "fpga":
        tcl = (cand / "fpga/build.tcl").read_text()
        if "xc7a100tcsg324-1" not in tcl:
            bad.append("build.tcl: đổi part")
        code = re.sub(r"#.*", "", tcl)
        for need in ("clock.xdc", "synth_opts.tcl"):
            if need not in code:
                bad.append(f"build.tcl: không đọc {need} (clock và biến thể cố định theo baseline)")
        if re.search(r"\b(create_clock|create_generated_clock|set_false_path|set_multicycle_path|set_max_delay)\b",
                     re.sub(r"#.*", "", tcl)):
            bad.append("build.tcl: thêm/đổi ràng buộc timing (clock cố định theo .xdc)")
        try:
            srcs = _sources(str(cand / "fpga"))
        except OSError:
            srcs = []
        if "fpga_top.v" not in srcs:
            bad.append("sources.f: thiếu fpga_top.v")
        for s in srcs:
            if not (cand / "fpga" / s).is_file():
                bad.append(f"sources.f: không có file {s}")
    if step == "asic":
        old = _mk_vars(before.get("asic/config.mk", b"").decode())
        new = _mk_vars((cand / "asic/config.mk").read_text())
        for k in ("PLATFORM", "DESIGN_NAME", "DESIGN_NICKNAME", "VERILOG_FILES", "SDC_FILE"):
            if old.get(k) != new.get(k):
                bad.append(f"config.mk: đổi {k}")
    return bad


def _enforce(step: str, cand: Path, before: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """Hoàn lại mọi thay đổi ngoài phạm vi. Trả (file đổi được giữ, file bị hoàn lại)."""
    after = _snapshot(cand)
    kept, dropped = [], []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        if _allowed(step, rel):
            kept.append(rel)
            continue
        dropped.append(rel)
        p = cand / rel
        if rel in before:
            p.write_bytes(before[rel])
        else:
            p.unlink(missing_ok=True)
    return kept, dropped


def _diff(cand: Path, before: dict[str, bytes], files: list[str]) -> str:
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

def measure(step: str, cand: Path, params: dict, on_task=None) -> dict:
    """Chạy lại bước trên các khối như baseline. Trả {ok, metrics, sub: {task: kết quả}, failed_at}.
    on_task(tên task) được gọi khi mỗi task bắt đầu (trang trạng thái)."""
    sub = {}

    def run(name, ref):
        if on_task:
            on_task(name)
        r = get(ref)
        sub[name] = r
        return r["ok"]

    d = str(cand)
    if step == "sim":
        plus = [f"+seed={params['seed']}", f"+cycles={params['cycles']}", f"+en_pct={params['en_pct']}"]
        _ = run("sim_build", B.sim_build.chia_remote(d)) and run("sim_run", B.sim_run.chia_remote(d, plus))
    elif step == "fpga":
        if run("fpga_sim", fpga_sim.chia_remote(d)) and run(
                "fpga", B.fpga_build.chia_remote(d, power_cycles=params.get("power_cycles", 20000))):
            if not sub["fpga"]["timing_met"]:                # gate của agent: phải đạt timing ở clock của baseline
                sub["fpga"]["ok"] = False
                sub["fpga"]["log_tail"] = (f"timing không đạt: WNS {sub['fpga'].get('wns_ns')} WHS "
                                           f"{sub['fpga'].get('whs_ns')} ns\n") + sub["fpga"].get("log_tail", "")
    else:
        for st in B.ASIC_STAGES:
            if not run(f"asic_{st}", B.asic_stage.chia_remote(d, st)):
                break
    failed = next((k for k, v in sub.items() if not v["ok"]), None)
    # ASIC: số liệu ở finish, kết quả LVS ở lvs; gộp lại. Các bước khác: task cuối.
    last = {**sub.get("asic_finish", {}), **sub.get("asic_lvs", {})} if step == "asic" else \
        (list(sub.values())[-1] if sub else {})
    return {"ok": failed is None and bool(sub), "failed_at": failed, "metrics": metrics_of(step, last),
            "sub": {k: {kk: vv for kk, vv in v.items() if kk not in ("metrics_json", "cells")} for k, v in sub.items()}}


def metrics_of(step: str, r: dict) -> dict:
    if step == "sim":
        return {"checked": _int(r.get("checked")), "errors": _int(r.get("errors")), "result": r.get("result"),
                **{k: r.get(k) for k in ("fcov_hit", "fcov_total", "fcov_miss", "cov_line", "cov_line_total",
                                         "cov_toggle", "cov_toggle_total", "cov_branch", "cov_branch_total")}}
    if step == "fpga":
        return {k: r.get(k) for k in ("wns_ns", "whs_ns", "tns_ns", "timing_met", "clock_mhz", "fmax_est_mhz", "lut",
                                      "ff", "bufg", "power_total_w", "power_dynamic_w", "power_alu_w",
                                      "power_dynamic_vl_w", "power_confidence", "saif_nets_matched_pct", "warnings",
                                      "critical_warnings", "warning_ids", "bitstream")}
    return {k: r.get(k) for k in ("area_um2", "die_area_um2", "core_area_um2", "util_pct", "n_logic_cells", "n_flop",
                                  "n_icg", "n_clkbuf", "setup_ws_ns", "hold_ws_ns", "power_total_w", "drc_errors",
                                  "lvs", "gds", "layout_png")}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def judge_sim(best: dict, new: dict) -> tuple[bool, str]:
    """Luật Sim (từ 25/11/2026): coverage thay cho số phép so sánh. Không loại coverage nào của DUT được giảm;
    nhận khi số bin functional tăng, hoặc (đã đủ mọi bin) khi line/toggle/branch tăng. checked chỉ để báo cáo."""
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
    if new.get("fcov_hit") is None:
        return False, "không đọc được dòng FCOV (" + "; ".join(notes) + ")"
    full = best.get("fcov_hit") is not None and best.get("fcov_hit") == best.get("fcov_total")
    ok = not worse and ("functional" in better or (full and bool(better)))
    if ok:
        why = "phủ thêm: " + ", ".join(better)
    elif worse:
        why = "coverage giảm: " + ", ".join(worse)
    else:
        why = "không phủ thêm" + (" (đã đủ mọi bin functional)" if full else " bin functional nào")
    return ok, why + " (" + "; ".join(notes) + ")"


def judge(step: str, best: dict, new: dict) -> tuple[bool, str]:
    """So bản mới với bản tốt nhất (đã qua gate). Công cụ tất định (baseline chạy 2 lần ra cùng số), nên ngưỡng chỉ
    bỏ qua sai khác làm tròn."""
    if step == "sim":
        return judge_sim(best, new)
    if step == "fpga":
        if best.get("timing_met") is False:              # bản mới đã qua gate nên đạt timing
            return True, (f"đạt timing ở {new.get('clock_mhz')} MHz (WNS {best.get('wns_ns')} → {new.get('wns_ns')} "
                          f"ns), bản tốt nhất chưa đạt")
        # Công suất động đo theo SAIF (từ 25/11/2026), công cụ tất định: ngưỡng 0,02 mW chỉ bỏ qua làm tròn.
        # LUT+FF được kém tối đa 3 % (người dùng chốt 25/11/2026; trước đó ±0,5 ô): ở FPGA, sửa timing/công suất
        # thường tốn vài LUT, Pareto chặt loại cả đánh đổi tốt như CG r1 ngày 21/11.
        crit = [("WNS", "wns_ns", +1, 0.02), ("P động", "power_dynamic_w", -1, 0.00002),
                ("LUT+FF", None, -1, ("pct", 0.03))]
    else:
        crit = [("diện tích cell", "area_um2", -1, 0.01), ("diện tích die", "die_area_um2", -1, 0.01),
                ("công suất", "power_total_w", -1, 0.01)]
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
    why = ("tốt hơn: " + ", ".join(better)) if ok else (("kém hơn: " + ", ".join(worse)) if worse else "không tốt hơn")
    return ok, why + " (" + "; ".join(notes) + ")"


# ---------------------------------------------------------------------------
# Prompt và context
# ---------------------------------------------------------------------------

COMMON = """You are the optimization agent of a CHIA demo flow (Sim -> FPGA -> ASIC) for `{dut}`, an 8-bit ALU with
two pipeline stages (input registers, combinational ALU, output registers; ops ADD SUB AND OR XOR SHL SHR SLT).
{variant}
Your working directory is a copy of the design tree: rtl/ (shared RTL, READ-ONLY: FPGA-only and ASIC-only changes must
not leak into the other flow), sim/, fpga/, asic/. Context for this step is in agent/context/ (start with
agent/context/README.md). Verilator, Vivado and OpenROAD are NOT installed here: do not try to build or run them. After
you finish, the CHIA loop measures your edits on the real tools and keeps them only if they pass the gate and improve
the result. Edits outside the allowed files below are reverted automatically.
If after evaluating the reports you think nothing worthwhile can be improved in this step, change nothing and say so:
that is an acceptable result.
Finish with a short summary IN VIETNAMESE (at most 8 lines) for the demo slides: what the reports showed, what you
changed and why, what you expect to improve.
"""

VARIANT = {   # khoá theo việc có clock gating (DEMO == "CG")
    False: "This variant: registers use ordinary clock enables (feedback mux), no clock gating.",
    True: ("This variant (clock gating): the input/output registers are clocked by gated clocks from module `icg` "
           "(rtl/icg_sim.v, a behavioural latch + AND model of an integrated clock gating cell)."),
}

STEP_TEXT = {
    "sim": """STEP: SIMULATION. Evaluate the self-checking testbench sim/tb_{dut}.v against the RTL: which behaviours and
corner cases are not checked yet (each op with edge operands, zero/carry flags, signed SLT edges, every shift amount,
`en` patterns such as back-to-back and long idle, outputs holding while idle, reset behaviour, ...).
Allowed file: sim/tb_{dut}.v only. Add stimulus and checks; keep the plusargs, the final `RESULT:` line format and the
counters (`checked` must count every comparison). The RTL is the reference behaviour: a new check that fails means
your round is rejected, so make sure the expected values follow the RTL exactly.
Coverage is measured, not counted by you: sim/fcov_{dut}.sv (FIXED, bound into the DUT) defines the functional
coverage bins of the specification and prints `FCOV: hit=<n> total=<m>` and `FCOV_MISS: <bins not hit>` (see sim.log
in the context); Verilator also measures line/toggle/branch coverage of the DUT. Do not edit or reference
fcov_{dut}.sv / u_fcov from the testbench (that round is rejected). Read fcov_{dut}.sv to see what each bin needs.
Gate: Verilator build + run with the same plusargs as baseline prints RESULT: PASS. The round is accepted only if
no coverage kind of the DUT decreases and the number of functional bins hit increases (when every bin is already
hit: only if line/toggle/branch coverage increases). Adding comparisons alone does not count.""",
    "fpga": """STEP: FPGA. Vivado 2025.1, part xc7a100tcsg324-1, clock {mhz} MHz ({period} ns) from fpga/clock.xdc (FIXED).
The ALU is wrapped in fpga/fpga_top.v (LFSR stimulus sending an operation in {en_thr}/128 of the cycles, MISR
self-check, LEDs). Read the reports of the current best build in agent/context/ (timing, utilization, clock
utilization, methodology, warnings, power_saif.rpt). Power is measured with real activity: the routed netlist is
simulated with fpga/tb_power.v and the SAIF is read back (fpga/power_saif.tcl, FIXED); power.rpt is the old vector-less
estimate, for reference only.
Goal: if the current best version does not meet timing at this clock, meet it (WNS >= 0, WHS >= 0); otherwise improve
WNS and/or dynamic power (SAIF) and/or LUT+FF. WNS and dynamic power must not get worse; LUT+FF may grow by at most 3 %.
Allowed files: fpga/build.tcl (synthesis/implementation options and directives; do NOT add or change clocks or timing
exceptions, do NOT change the part), fpga/sources.f (the Verilog files read by Vivado and by the functional simulation),
new Verilog files fpga/<name>.v (for example an FPGA-specific implementation of a module from rtl/ with the SAME module
name and ports; then replace the rtl/ file by yours in sources.f). Not allowed: rtl/, fpga/fpga_top.v,
fpga/tb_fpga_top.v, fpga/tb_power.v, fpga/golden_sig.py, fpga/power_saif.tcl, fpga/synth_opts.tcl, the .xdc files.
build.tcl must keep reading clock.xdc and sourcing synth_opts.tcl.
Gate: tb_fpga_top.v in xsim with the UNISIM library (Xilinx primitives simulate) prints RESULT: PASS fpga_top, the
bitstream is written, the clock is the one of clock.xdc, WNS >= 0 and WHS >= 0, SAIF power measured.""",
    "asic": """STEP: ASIC. OpenROAD-flow-scripts, sky130hd, RTL -> GDS, 10 ns clock from asic/constraint.sdc (fixed).
Read the reports and metrics of the current best run in agent/context/ (ORFS final report, synthesis statistics,
metrics JSON, ORFS variable reference if present).
Goal: reduce standard-cell area, die area and/or power (power is OpenSTA's default-activity estimate) by at least 1 %
in one of them without making the others worse, with timing still closed (setup and hold slack >= 0) and no DRC.
Allowed file: asic/config.mk (ORFS variables such as CORE_UTILIZATION, PLACE_DENSITY, ABC_AREA, synthesis, repair and
CTS options). Do NOT change PLATFORM, DESIGN_NAME, DESIGN_NICKNAME, VERILOG_FILES or SDC_FILE.
Gate: GDS written, setup/hold slack >= 0, DRC errors = 0, LVS clean (KLayout: the layout GDS matches the CDL netlist).
Too high a utilization fails placement or routing and the
round is rejected.""",
}


def write_context(step: str, cand: Path, best_dir: Path, best_m: dict, base_m: dict, history: list) -> None:
    c = cand / "agent" / "context"
    c.mkdir(parents=True, exist_ok=True)
    files = []

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
        rep = best_dir / "asic" / "orfs" / "reports" / "sky130hd" / DUT / "base"
        for f in sorted(rep.glob("*")):
            if f.suffix in (".rpt", ".txt", ".log"):
                put(f, f"asic_{f.name}")
        metrics = {}
        for f in sorted((best_dir / "asic" / "orfs" / "logs" / "sky130hd" / DUT / "base").glob("*.json")):
            try:
                metrics.update(json.loads(f.read_text()))
            except (OSError, ValueError):
                pass
        (c / "asic_metrics.json").write_text(json.dumps(metrics, indent=1))
        files.append("asic_metrics.json")
        put(Path(ORFS_HOST, "flow", "scripts", "variables.yaml"), "orfs_variables.yaml", 2_000_000)
    if not [f for f in files if f not in ("asic_metrics.json", "orfs_variables.yaml")] or (
            step == "asic" and not metrics):
        print(f"[agent {DEMO}] CẢNH BÁO: context {step} không có báo cáo từ {best_dir}", flush=True)
    (c / "metrics.json").write_text(json.dumps({"baseline": base_m, "current_best": best_m}, indent=2,
                                               ensure_ascii=False, default=str))
    (c / "history.json").write_text(json.dumps(history, indent=2, ensure_ascii=False, default=str))
    (c / "README.md").write_text(
        f"# Context for step {step} ({DEMO})\n\n"
        f"- metrics.json: metrics of the baseline and of the current best version (what you must beat)\n"
        f"- history.json: earlier rounds of this agent run (changes, result, accepted or not)\n"
        + "".join(f"- {f}\n" for f in files if f not in ("metrics.json", "history.json")))


def build_prompt(step: str, params: dict | None = None) -> str:
    f = (params or {}).get("fpga") or {"period_ns": 10.0, "en_thr": B.FPGA_EN_THR_DEFAULT}
    fmt = {"dut": DUT, "period": f"{f['period_ns']:g}", "mhz": f"{1000 / f['period_ns']:.4g}", "en_thr": f["en_thr"]}
    return (COMMON.format(dut=DUT, variant=VARIANT[DEMO == "CG"]) + "\n" + STEP_TEXT[step].format(**fmt) + "\n")


# ---------------------------------------------------------------------------
# Thư mục vòng
# ---------------------------------------------------------------------------

def copy_tree(src: Path, dst: Path) -> None:
    ign = shutil.ignore_patterns(*BUILD_NAMES, *BUILD_FILES)
    for sub in SRC_DIRS:
        shutil.copytree(src / sub, dst / sub, ignore=ign)


def slim(d: Path, keep_outputs: bool) -> None:
    """Dọn file lớn của một vòng. Luôn dọn binary/checkpoint/ORFS objects; vòng bị loại dọn cả bitstream, GDS, VCD."""
    junk = [d / "sim/build/obj", d / "fpga/out/post_route.dcp", d / "fpga/xsim.dir", d / "fpga/.Xil",
            d / "asic/orfs/objects"]
    res = d / "asic/orfs/results/sky130hd" / DUT / "base"
    if res.is_dir():
        junk += [f for f in res.glob("*.odb") if f.name != "6_final.odb"]
    if not keep_outputs:
        junk += [d / "sim/build/wave.vcd", d / "fpga/out/fpga_top.bit", res]
    for p in junk:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def latest_baseline(work: Path) -> Path | None:
    for d in sorted((work / "demo-alu" / DEMO).glob("baseline-*"), reverse=True):
        try:
            if json.loads((d / "results" / "baseline.json").read_text()).get("stopped_at") is None:
                return d
        except (OSError, ValueError):
            continue
    return None


def base_metrics(rep: dict) -> dict:
    s = rep["steps"]
    return {"sim": metrics_of("sim", s.get("sim_run", {})), "fpga": metrics_of("fpga", s.get("fpga", {})),
            "asic": metrics_of("asic", {**s.get("asic_finish", {}), **s.get("asic_lvs", {})})}


# ---------------------------------------------------------------------------
# Bảng so sánh
# ---------------------------------------------------------------------------

ROWS = {
    "sim": [("Functional coverage (bin)", "fcov_hit"), ("Line (DUT)", "cov_line"), ("Toggle (DUT)", "cov_toggle"),
            ("Branch (DUT)", "cov_branch"), ("Số lần kiểm tra", "checked"), ("Lỗi", "errors")],
    "fpga": [("Clock (MHz)", "clock_mhz"), ("Timing đạt", "timing_met"), ("WNS (ns)", "wns_ns"), ("WHS (ns)", "whs_ns"),
             ("Fmax ước lượng (MHz)", "fmax_est_mhz"), ("LUT", "lut"), ("FF", "ff"), ("BUFG", "bufg"),
             ("Công suất động SAIF (W)", "power_dynamic_w"), ("Công suất u_alu SAIF (W)", "power_alu_w"),
             ("Công suất tổng SAIF (W)", "power_total_w"), ("Công suất động vector-less (W)", "power_dynamic_vl_w"),
             ("Cảnh báo", "warnings")],
    "asic": [("Diện tích cell (µm²)", "area_um2"), ("Diện tích die (µm²)", "die_area_um2"), ("Util (%)", "util_pct"),
             ("Số cell", "n_logic_cells"), ("ICG", "n_icg"), ("Buffer clock", "n_clkbuf"),
             ("Setup WS (ns)", "setup_ws_ns"), ("Hold WS (ns)", "hold_ws_ns"), ("Công suất (W)", "power_total_w"),
             ("DRC", "drc_errors"), ("LVS", "lvs")],
}


def compare_md(report: dict) -> str:
    out = [f"# Demo {DEMO}: baseline / agent\n", f"Baseline: `{report['baseline']}`  \nAgent: `{report['run_dir']}`\n"]
    for step in report["steps"]:
        st = report["result"][step]
        b, a = st["baseline"], st["best"]
        out.append(f"\n## {step.upper()}\n\n| | Baseline | Agent | Thay đổi |\n|---|---|---|---|")
        for name, k in ROWS[step]:
            x, y = b.get(k), a.get(k)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x:
                ch = f"{(y - x) / abs(x) * 100:+.1f} %" if step != "fpga" or not k.endswith("_ns") else f"{y - x:+.3f}"
            else:
                ch = ""
            out.append(f"| {name} | {x if x is not None else '-'} | {y if y is not None else '-'} | {ch} |")
        out.append("")
        for r in [r for r in report["rounds"] if r["step"] == step]:
            mark = "NHẬN" if r.get("accepted") else "LOẠI"
            out.append(f"- **{r['round']}** {mark}: {r.get('verdict', '')}  ")
            if r.get("changed"):
                out.append(f"  đổi: {', '.join(r['changed'])}  ")
            if r.get("summary"):
                out.append("  > " + r["summary"].replace("\n", "\n  > "))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")))
    ap.add_argument("--baseline", help="thư mục lần chạy baseline (mặc định: bản OK mới nhất)")
    ap.add_argument("--run-id", default=time.strftime("agent-%Y%m%d-%H%M%S"))
    ap.add_argument("--steps", default="sim,fpga,asic")
    ap.add_argument("--rounds", type=int, default=2, help="số vòng tối đa mỗi bước (mặc định 2)")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--effort", default="", help="--effort của Claude Code (để trống: mặc định)")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--agent-timeout", type=int, default=1200, help="giây mỗi phiên agent (mặc định 1200)")
    args = ap.parse_args()

    work = Path(args.work_dir)
    base_dir = Path(args.baseline) if args.baseline else latest_baseline(work)
    if not base_dir or not (base_dir / "results" / "baseline.json").is_file():
        raise SystemExit("LỖI: không thấy baseline OK: chạy baseline.py trước hoặc chỉ --baseline")
    base_rep = json.loads((base_dir / "results" / "baseline.json").read_text())
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    run_dir = work / "demo-alu" / DEMO / args.run_id
    if run_dir.exists():
        raise SystemExit(f"LỖI: {run_dir} đã có, chọn --run-id khác")
    (run_dir / "results").mkdir(parents=True)

    ray.init(address="auto", ignore_reinit_error=True)
    need = {"claude_creds": "claude", "sim": "chipyard", "fpga": "vivado", "asic": "asic"}
    have = ray.cluster_resources()
    for key in ["claude_creds"] + steps:
        res = {"sim": "chipyard", "fpga": "vivado", "asic": "asic"}.get(key, key)
        if not have.get(res):
            raise SystemExit(f"LỖI: cluster không có khối {need[key]} (resource {res}): "
                             "`python scripts/chia-up-blocks.py --fpga --asic`")
    if "sim" in steps and not have.get("verilator_run"):
        raise SystemExit("LỖI: cluster không có khối verilator")
    start_collector(log_dir=str(run_dir / "profile"))

    base_m = base_metrics(base_rep)
    params = base_rep["params"]
    report = {"demo": DEMO, "dut": DUT, "phase": "agent", "run_id": args.run_id, "run_dir": str(run_dir),
              "baseline": str(base_dir), "steps": steps, "model": args.model, "effort": args.effort,
              "rounds": [], "result": {}, "stopped": None, "t0": time.time()}
    tag = f"[agent {DEMO}]"
    print(f"{tag} run_dir = {run_dir}\n{tag} baseline = {base_dir}", flush=True)
    S.write(report)
    print(f"{tag} trạng thái: {run_dir / 'status.html'}", flush=True)

    # Nguồn của vòng đầu: cây của project (cùng RTL với baseline, kiểm bằng sha256); kết quả/báo cáo: thư mục baseline.
    # Bước sau xây trên bản tốt nhất của bước trước.
    src = Path(__file__).resolve().parent
    sha = B._sha256(sorted((src / "rtl").glob("*.v")))
    if sha != base_rep.get("rtl_sha256"):
        raise SystemExit(f"LỖI: rtl/ của project khác rtl/ của baseline {base_dir}: chạy lại baseline.py")
    # Nguồn: bản tốt nhất của bước trước (sửa dồn qua các bước). Báo cáo cho context: thư mục nơi BƯỚC NÀY được đo lần
    # tốt nhất (baseline, vì bước sau chưa đo lại bước trước: vòng sim được nhận không có báo cáo FPGA/ASIC).
    best_src = src
    for step in steps:
        best_m = dict(base_m[step])
        step_best_src, step_best_dir = best_src, base_dir
        report["result"][step] = {"baseline": base_m[step], "best": best_m, "best_dir": str(step_best_dir)}
        for k in range(1, args.rounds + 1):
            name = f"{step}-r{k}"
            cand = run_dir / name
            copy_tree(step_best_src, cand)
            before = _snapshot(cand)
            write_context(step, cand, step_best_dir, best_m, base_m[step],
                          [r for r in report["rounds"] if r["step"] == step])
            prompt = build_prompt(step, params)
            (cand / "agent" / "TASK.md").write_text(prompt)
            print(f"{tag} {name}: agent đang đánh giá ...", flush=True)
            S.set_live(report, step=step, round=name, state="agent")
            s = get(agent_session.chia_remote(str(cand), prompt, args.model, args.effort, args.max_turns,
                                              args.agent_timeout))
            (cand / "agent" / "summary.md").write_text(s.get("summary", ""))
            kept, dropped = _enforce(step, cand, before)
            rnd = {"step": step, "round": name, "dir": str(cand), "agent_seconds": s.get("seconds"),
                   "agent_error": s.get("error"), "summary": s.get("summary", ""), "changed": kept,
                   "reverted": dropped, "accepted": False}
            report["rounds"].append(rnd)
            print(f"{tag} {name}: agent {s.get('seconds', 0):.0f}s @ {s.get('host')}, đổi {kept or 'không'}"
                  + (f", hoàn lại {dropped}" if dropped else "") + (f", LỖI {s['error']}" if s.get("error") else ""),
                  flush=True)
            if s.get("error", "").startswith(("rate_limit", "auth", "setup")):
                report["stopped"] = f"{name}: {s['error']}"
                break
            if not kept:
                rnd["verdict"] = "agent không đổi gì"
                slim(cand, keep_outputs=False)
                print(f"{tag} {name}: agent không đổi gì, xong bước {step}", flush=True)
                break
            (cand / "agent" / "changes.diff").write_text(_diff(cand, before, kept))
            bad = _rule_violations(step, cand, before)
            if bad:
                rnd["verdict"] = "vi phạm luật: " + "; ".join(bad)
                print(f"{tag} {name}: LOẠI ({rnd['verdict']})", flush=True)
                slim(cand, keep_outputs=False)
                continue
            m = measure(step, cand, params,
                        on_task=lambda t: S.set_live(report, step=step, round=name, state="measure", task=t))
            rnd["measure"] = m
            if not m["ok"]:
                rnd["verdict"] = f"không qua gate ở {m['failed_at']}"
                tail = m["sub"].get(m["failed_at"], {}).get("log_tail", "")
                (cand / "agent" / "gate_fail.txt").write_text(tail)
                rnd["gate_fail_tail"] = tail[-1500:]
            else:
                ok, why = judge(step, best_m, m["metrics"])
                rnd["accepted"], rnd["verdict"] = ok, why
                if ok:
                    best_m, step_best_src, step_best_dir = m["metrics"], cand, cand
                    report["result"][step] = {"baseline": base_m[step], "best": best_m,
                                              "best_dir": str(step_best_dir)}
            print(f"{tag} {name}: {'NHẬN' if rnd['accepted'] else 'LOẠI'} ({rnd['verdict']})", flush=True)
            report["live"] = None
            S.write(report)
            slim(cand, keep_outputs=rnd["accepted"])
        report["result"][step] = {"baseline": base_m[step], "best": best_m, "best_dir": str(step_best_dir)}
        best_src = step_best_src
        if report["stopped"]:
            break

    stop_collector()
    prof = run_dir / "profile" / "ChiaProfileCollector.log"
    if prof.is_file():
        subprocess.run(["chia", "viz-profile", str(prof), "--format", "html"], capture_output=True)
        report["profile"] = str(prof.with_name("ChiaProfileCollector_profile.html"))
    (run_dir / "results" / "agent.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    (run_dir / "results" / "compare.md").write_text(compare_md(report))
    report["live"] = None
    S.write(report, final=True)
    print(f"{tag} so sánh: {run_dir / 'results' / 'compare.md'}", flush=True)
    if report.get("profile"):
        print(f"{tag} timeline: {report['profile']}", flush=True)
    acc = [r["round"] for r in report["rounds"] if r["accepted"]]
    print(f"KẾT QUẢ: {'DỪNG ' + report['stopped'] if report['stopped'] else 'OK'}; vòng được nhận: {acc or 'không có'}",
          flush=True)
    raise SystemExit(1 if report["stopped"] else 0)


if __name__ == "__main__":
    main()
