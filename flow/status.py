"""status.py - trang status HTML (tiếng Anh, dùng khi quay video/trình bày) của một lần chạy baseline hoặc agent.

baseline.py và agent.py ghi lại <run_dir>/status.html mỗi khi một task CHIA được gửi hoặc xong. Trang tự làm mới mỗi
3 giây khi lần chạy chưa xong. Bốn phần, từ trên xuống:
  1. Flow       Sim → FPGA → ASIC, mỗi bước kèm các gate (chờ / đang chạy / đạt / trượt / cảnh báo) và khối CHIA chạy nó
  2. Timeline   Gantt theo khối của cluster (thời điểm bắt đầu/kết thúc ghi trên worker): thấy các task chạy chồng nhau
  3. Results    số liệu từng bước (baseline), hoặc baseline so với bản tốt nhất và các vòng nhận/loại (agent)
  4. Logs & files  thu gọn: log của task lỗi, ảnh layout, timeline CHIA, file kết quả
Dựng lại trang của một lần chạy đã xong:  python -m flow.status <run_dir>
Chỉ đọc số liệu, không ảnh hưởng gate hay việc đo; lỗi khi ghi trang chỉ in cảnh báo.
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
from pathlib import Path

STEPS = ("sim", "fpga", "asic")
TITLE = {"sim": "Simulation", "fpga": "FPGA", "asic": "ASIC"}
BLOCKS = ["chipyard", "verilator", "vivado", "asic", "claude"]
BLOCK_NOTE = {"chipyard": "Verilator build · GLS", "verilator": "simulation runs", "vivado": "Vivado 2025.1",
              "asic": "Yosys + OpenROAD + KLayout", "claude": "Claude Code agent"}

e = lambda s: html.escape(str(s))


def tool_of(step: str, cfg: dict) -> str:
    if step == "sim":
        return "Verilator"
    if step == "fpga":
        f = cfg.get("fpga", {})
        return f"{f.get('tool', 'Vivado')} · {f.get('board') or f.get('part', '')}"
    return f"ORFS (Yosys + OpenROAD) · {cfg.get('asic', {}).get('platform', '')}"


def rows(step: str, cfg: dict) -> list:
    """(nhãn, khoá, hệ số, hướng tốt: +1 lớn hơn tốt, -1 nhỏ hơn tốt, 0 trung tính, ns: in hiệu tuyệt đối)."""
    inst = (cfg.get("fpga") or {}).get("power_instance") or "instance"
    return {
        "sim": [("Functional coverage (bins)", "fcov", 1, 0, False), ("Line coverage (DUT)", "cov_line_s", 1, 0, False),
                ("Toggle coverage (DUT)", "cov_toggle_s", 1, 0, False),
                ("Branch coverage (DUT)", "cov_branch_s", 1, 0, False), ("Checks", "checked", 1, +1, False),
                ("Errors", "errors", 1, -1, False)],
        "fpga": [("Clock (MHz)", "clock_mhz", 1, 0, False), ("WNS (ns)", "wns_ns", 1, +1, True),
                 ("WHS (ns)", "whs_ns", 1, +1, True), ("Fmax estimate (MHz)", "fmax_est_mhz", 1, +1, False),
                 ("Methodology critical", "methodology_critical", 1, -1, False), ("LUT", "lut", 1, -1, False),
                 ("FF", "ff", 1, -1, False), ("BUFG", "bufg", 1, 0, False),
                 ("Dynamic power, SAIF (mW)", "power_dynamic_w", 1000, -1, False),
                 (f"{inst} power, SAIF (mW)", "power_inst_w", 1000, -1, False),
                 ("Total power, SAIF (mW)", "power_total_w", 1000, -1, False),
                 ("Dynamic power, vector-less (mW)", "power_dynamic_vl_w", 1000, -1, False)],
        "asic": [("Cell area (µm²)", "area_um2", 1, -1, False), ("Die area (µm²)", "die_area_um2", 1, -1, False),
                 ("Utilization (%)", "util_pct", 1, 0, False), ("Cells", "n_logic_cells", 1, 0, False),
                 ("ICG cells", "n_icg", 1, 0, False), ("Clock buffers", "n_clkbuf", 1, 0, False),
                 ("Setup WS (ns)", "setup_ws_ns", 1, +1, True), ("Hold WS (ns)", "hold_ws_ns", 1, +1, True),
                 ("STA setup WS, worst corner (ns)", "sta_setup_ws_min_ns", 1, +1, True),
                 ("STA hold WS, worst corner (ns)", "sta_hold_ws_min_ns", 1, +1, True),
                 ("Power (mW)", "power_total_w", 1000, -1, False)],
    }[step]


CSS = """
:root{color-scheme:dark;--bg:#0d1117;--card:#161b22;--card2:#1c232c;--ink:#e6edf3;--mute:#8b949e;--line:#2d333b;
--ok:#3fb950;--okbg:#12301b;--bad:#f85149;--badbg:#3b1416;--run:#58a6ff;--runbg:#11284a;--warn:#d29922;
--warnbg:#3a2c0c;--wait:#8b949e;--waitbg:#21262d;--accent:#a371f7}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 "Inter",system-ui,
-apple-system,"Segoe UI",Roboto,sans-serif}main{max-width:1320px;margin:0 auto;padding:22px 16px 56px}
a{color:var(--run)}code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mute)}
header{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;align-items:flex-end;margin-bottom:6px}
h1{font-size:27px;margin:0;letter-spacing:-.01em}h1 small{color:var(--mute);font-weight:500;font-size:17px}
.sub{color:var(--mute);font-size:13.5px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.09em;color:var(--mute);
margin:30px 0 10px;font-weight:600}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;font-weight:600;padding:2px 9px;border-radius:99px;
white-space:nowrap}.c-ok{background:var(--okbg);color:var(--ok)}.c-bad{background:var(--badbg);color:var(--bad)}
.c-run{background:var(--runbg);color:var(--run)}.c-wait{background:var(--waitbg);color:var(--wait)}
.c-warn{background:var(--warnbg);color:var(--warn)}.big{font-size:14px;padding:4px 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.flow{display:grid;grid-template-columns:minmax(0,1fr) 34px minmax(0,1fr) 34px minmax(0,1fr);align-items:stretch}
.arrow{display:grid;place-items:center;color:var(--mute);font-size:24px}
.step{border-width:2px;display:flex;flex-direction:column;gap:8px}.step.s-run{border-color:var(--run);
box-shadow:0 0 0 3px var(--runbg)}.step.s-ok{border-color:var(--ok)}.step.s-bad{border-color:var(--bad)}
.step.s-warn{border-color:var(--warn)}.step.s-wait{opacity:.55}
.step h3{margin:0;font-size:20px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.tool{color:var(--mute);font-size:12.5px}.blocks{display:flex;gap:5px;flex-wrap:wrap}
.blk{font-size:11.5px;color:var(--accent);border:1px solid #3d2f5c;border-radius:5px;padding:0 6px}
ul.gates{list-style:none;margin:0;padding:0;font-size:13.5px}ul.gates li{display:flex;justify-content:space-between;
gap:8px;padding:3px 0;border-bottom:1px dashed var(--line)}ul.gates li:last-child{border:0}
.g{font-weight:700;width:18px;display:inline-block;text-align:center}.g-ok{color:var(--ok)}.g-bad{color:var(--bad)}
.g-run{color:var(--run)}.g-wait{color:var(--wait)}.g-warn{color:var(--warn)}.gv{color:var(--mute);text-align:right}
.headline{font-size:15px;font-weight:600;margin-top:auto;padding-top:4px}
.gantt{position:relative}.grow{display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px;align-items:start;
border-bottom:1px solid var(--line);padding:6px 0}.grow:last-child{border:0}.gname{font-weight:600;font-size:13.5px}
.gname span{display:block;color:var(--mute);font-weight:400;font-size:11.5px}.lanes{position:relative}
.lane{position:relative;height:24px;margin:2px 0}.bar{position:absolute;top:0;height:22px;border-radius:5px;
font-size:11.5px;line-height:22px;padding:0 6px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;
min-width:3px;color:#0d1117;font-weight:600}.b-ok{background:var(--ok)}.b-bad{background:var(--bad)}
.b-run{background:var(--run);background-image:repeating-linear-gradient(45deg,transparent 0 8px,rgba(255,255,255,.18) 8px 16px)}
.axis{display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px;font-size:11.5px;color:var(--mute)}
.ticks{position:relative;height:16px}.ticks span{position:absolute;transform:translateX(-50%)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--mute);margin-top:8px}.legend i{display:inline-block;width:10px;
height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.results{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:start}
.results.agent{grid-template-columns:minmax(0,1fr)}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:4px 6px;border-bottom:1px solid var(--line);
text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}th{color:var(--mute);
font-weight:600;font-size:12px}tr:last-child td{border:0}.up{color:var(--ok);font-weight:600}
.down{color:var(--bad);font-weight:600}.flat{color:var(--mute)}
.card h4{margin:0 0 8px;font-size:16px;display:flex;justify-content:space-between;align-items:center}
.agentrow{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:12px;align-items:start;margin-bottom:12px}
.rounds{display:flex;flex-direction:column;gap:8px}.round{background:var(--card2)}.round h5{margin:0;font-size:15px;
display:flex;gap:8px;align-items:center}.round .meta{color:var(--mute);font-size:12.5px;margin-top:2px}
.round .verdict{font-size:13px;margin-top:4px}.round pre{white-space:pre-wrap;font:13px/1.45 system-ui,sans-serif;
margin:6px 0 0;background:var(--bg);padding:8px 10px;border-radius:6px;max-height:220px;overflow:auto}
figure{margin:10px 0 0}figure img{width:100%;border:1px solid var(--line);border-radius:8px;background:#000}
figcaption{font-size:12px;color:var(--mute)}.imgs{display:grid;grid-template-columns:1fr 1fr;gap:10px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 14px;margin-bottom:8px}
summary{cursor:pointer;font-weight:600}details pre{white-space:pre-wrap;font:12px ui-monospace,monospace;max-height:320px;
overflow:auto;color:var(--mute)}.links a{margin-right:16px}.timer{font-variant-numeric:tabular-nums}
@media (max-width:900px){.flow{grid-template-columns:1fr}.arrow{transform:rotate(90deg);height:30px}
.results,.agentrow,.imgs{grid-template-columns:1fr}.grow,.axis{grid-template-columns:90px minmax(0,1fr)}}
"""

JS = """<script>
function tick(){var n=Date.now()/1000;document.querySelectorAll('[data-since]').forEach(function(e){
var s=Math.max(0,Math.round(n-parseFloat(e.dataset.since)));e.textContent=Math.floor(s/60)+':'+
String(s%60).padStart(2,'0');});}tick();setInterval(tick,1000);
</script>"""


# ---------------------------------------------------------------------------
# Định dạng
# ---------------------------------------------------------------------------

def fmt(v, scale=1) -> str:
    if v is None or v == "":
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    try:
        x = float(v) * scale
    except (TypeError, ValueError):
        return e(v)
    if float(x).is_integer() and abs(x) >= 1:
        return f"{int(x):,}".replace(",", " ")
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if abs(x) >= 1:
        return f"{x:.3f}".rstrip("0").rstrip(".")
    return f"{x:.3g}"


def delta(a, b, scale, good, ns) -> str:
    try:
        a, b = float(a) * scale, float(b) * scale
    except (TypeError, ValueError):
        return ""
    if a == b:
        return '<span class="flat">=</span>'
    if ns:
        txt = f"{b - a:+.3f}"
    elif a and b / a >= 10:
        txt = f"×{b / a:.0f}"
    elif a:
        txt = f"{(b - a) / abs(a) * 100:+.1f} %"
    else:
        txt = f"{b - a:+g}"
    cls = "flat" if not good else ("up" if (b - a) * good > 0 else "down")
    return f'<span class="{cls}">{txt}</span>'


def view(step: str, m: dict | None) -> dict:
    """Thêm khoá dạng chữ 'đã gặp/tổng' cho coverage."""
    m = dict(m or {})
    if step == "sim":
        if m.get("fcov_hit") is not None:
            m["fcov"] = f'{m["fcov_hit"]}/{m.get("fcov_total")}'
        for k in ("line", "toggle", "branch"):
            if m.get(f"cov_{k}") is not None:
                m[f"cov_{k}_s"] = f'{m[f"cov_{k}"]}/{m.get(f"cov_{k}_total")}'
    return m


def chip(state: str, text: str, big: bool = False) -> str:
    return f'<span class="chip c-{state}{" big" if big else ""}">{e(text)}</span>'


def timer(since) -> str:
    return f'<span class="timer" data-since="{since}"></span>' if since else ""


def rel(run_dir: Path, p) -> str | None:
    if not p or not os.path.isfile(p):
        return None
    return os.path.relpath(p, run_dir)


def table(step: str, cfg: dict, base: dict, best: dict | None = None) -> str:
    base, best = view(step, base), (view(step, best) if best is not None else None)
    out = []
    for name, k, sc, good, ns in rows(step, cfg):
        if base.get(k) is None and (best is None or best.get(k) is None):
            continue
        if best is None:
            out.append(f"<tr><td>{e(name)}</td><td>{fmt(base.get(k), sc)}</td></tr>")
        else:
            out.append(f"<tr><td>{e(name)}</td><td>{fmt(base.get(k), sc)}</td><td>{fmt(best.get(k), sc)}</td>"
                       f"<td>{delta(base.get(k), best.get(k), sc, good, ns)}</td></tr>")
    head = "" if best is None else "<tr><th></th><th>Baseline</th><th>Best</th><th>Change</th></tr>"
    return f"<table>{head}{''.join(out)}</table>"


# ---------------------------------------------------------------------------
# Gate của từng bước, đọc từ kết quả task
# ---------------------------------------------------------------------------

def _g(res: dict | None, cond, ok_txt="", bad_txt="", warn: bool = False):
    """(trạng thái, giá trị hiển thị). res None: chưa chạy."""
    if res is None:
        return "wait", ""
    v = cond(res)
    if v is None:
        return "wait", ""
    return ("ok", ok_txt) if v else (("warn" if warn else "bad"), bad_txt)


def gates(step: str, steps: dict, running: set[str], baseline: bool) -> list[tuple[str, str, str]]:
    """[(nhãn gate, trạng thái, giá trị)] của một bước. steps: {task: kết quả} của bản đang xem."""
    def st(task, cond, ok_txt="", bad_txt="", warn=False):
        if task in running:
            return "run", ""
        return _g(steps.get(task), cond, ok_txt, bad_txt, warn)

    out = []
    if step == "sim":
        b, r = steps.get("sim_build") or {}, steps.get("sim_run") or {}
        strict = b.get("lint_mode", "strict") == "strict"
        out.append(("Lint clean (-Wall)" if strict else "Lint, no errors (-Wall)",
                    *st("sim_build", lambda x: x.get("lint_ok"), f"{b.get('lint_warnings')} warnings",
                        f"{b.get('lint_warnings')} warnings")))
        out.append(("Testbench PASS", *st("sim_run", lambda x: x.get("ok"),
                                          f"{fmt(r.get('checked'))} checks" if r.get("checked") else "PASS", "FAIL")))
        if r.get("fcov_hit") is not None or "sim_run" not in steps:
            out.append(("Functional coverage", *st("sim_run", lambda x: True if x.get("fcov_hit") is not None else None,
                                                    f"{r.get('fcov_hit')}/{r.get('fcov_total')} bins")))
        else:
            out.append(("Code coverage (line · branch)", *st("sim_run", lambda x: True if x.get("cov_line") is not None
                                                              else None, f"{r.get('cov_line')}/{r.get('cov_line_total')}"
                                                              f" · {r.get('cov_branch')}/{r.get('cov_branch_total')}")))
    elif step == "fpga":
        f = steps.get("fpga") or {}
        if "fpga_sim" in steps or "fpga_sim" in running:
            out.append(("Early xsim check", *st("fpga_sim", lambda x: x.get("ok"), "PASS", "FAIL")))
        if (f.get("mode") or "board") == "ooc":
            out.append(("Place & route (out-of-context)", *st("fpga", lambda x: x.get("routed"),
                                                               f"{fmt(f.get('clock_mhz'))} MHz", "failed")))
        else:
            out.append(("Bitstream", *st("fpga", lambda x: x.get("bitstream") is not None,
                                         f"{fmt(f.get('clock_mhz'))} MHz", "missing")))
        out.append(("Timing (WNS, WHS ≥ 0)", *st("fpga", lambda x: x.get("timing_met"),
                                                  f"WNS {fmt(f.get('wns_ns'))} ns", f"WNS {fmt(f.get('wns_ns'))} ns",
                                                  warn=baseline)))
        meth = ", ".join(k for k, v in (f.get("methodology") or {}).items()
                         if v.get("severity") in ("Critical Warning", "Error")) or ""
        out.append(("Methodology (no critical)", *st("fpga", lambda x: x.get("methodology_critical") == 0
                                                      if "methodology_critical" in x else None, "0", meth or "critical",
                                                      warn=baseline)))
        out.append(("Power, SAIF", *st("fpga", lambda x: x.get("saif_ok") if "saif_ok" in x else None,
                                       f"{fmt(f.get('power_dynamic_w'), 1000)} mW dyn", "not measured")))
        out.append(("Gate-level sim", *st("fpga", lambda x: x.get("gls_ok") if "gls_ok" in x else None, "PASS", "FAIL")))
    else:
        fin = steps.get("asic_finish") or {}
        busy = {t for t in running if t.startswith("asic_") and t not in ("asic_drc", "asic_lvs", "asic_gls")
                and not t.startswith("asic_sta_")}
        if busy:
            out.append(("Synth → route → finish", "run", next(iter(busy)).removeprefix("asic_")))
        else:
            out.append(("GDS (synth → finish)", *st("asic_finish", lambda x: x.get("gds") is not None,
                                                     f"{fmt(fin.get('die_area_um2'))} µm² die", "missing")))
        out.append(("Router DRC · antenna", *st("asic_finish", lambda x: (not x.get("drc_errors") and x.get("antenna_nets") == 0)
                                                 if "gds" in x else None, "0 · 0",
                                                 f"{fin.get('drc_errors')} · {fin.get('antenna_nets')}")))
        d = steps.get("asic_drc") or {}
        out.append(("DRC sign-off (KLayout)", *st("asic_drc", lambda x: x.get("ok"), "clean",
                                                   f"{d.get('drc_klayout')} violations")))
        out.append(("LVS (KLayout)", *st("asic_lvs", lambda x: x.get("ok"), "match",
                                          (steps.get("asic_lvs") or {}).get("lvs", ""))))
        corners = sorted(k for k in set(steps) | running if k.startswith("asic_sta_"))
        for k in corners or ["asic_sta_tt"]:
            c = steps.get(k) or {}
            out.append((f"STA {k.removeprefix('asic_sta_')}", *st(k, lambda x: x.get("ok"),
                                                                  f"setup {fmt(c.get('setup_ws_ns'))} ns",
                                                                  f"setup {fmt(c.get('setup_ws_ns'))} ns")))
        out.append(("Gate-level sim", *st("asic_gls", lambda x: x.get("ok"), "PASS", "FAIL")))
    return out


def step_state(gs: list) -> str:
    states = [s for _, s, _ in gs]
    if "bad" in states:
        return "bad"
    if "run" in states:
        return "run"
    if states and all(s in ("ok", "warn") for s in states):
        return "warn" if "warn" in states else "ok"
    if any(s in ("ok", "warn") for s in states):
        return "run"
    return "wait"


STATE_TXT = {"ok": "passed", "bad": "failed", "run": "running", "wait": "waiting", "warn": "passed, not signed off",
             "skip": "disabled"}
MARK = {"ok": "✓", "bad": "✗", "run": "●", "wait": "○", "warn": "!"}


def step_card(step: str, cfg: dict, state: str, gs: list, blocks: list[str], headline: str = "",
              extra: str = "") -> str:
    lis = "".join(f'<li><span><span class="g g-{s}">{MARK[s]}</span> {e(n)}</span><span class="gv">{e(v)}</span></li>'
                  for n, s, v in gs)
    blk = "".join(f'<span class="blk">{e(b)}</span>' for b in blocks)
    return (f'<div class="card step s-{state}"><h3>{TITLE[step]} {chip(state if state != "skip" else "wait", STATE_TXT[state])}</h3>'
            f'<div class="tool">{e(tool_of(step, cfg))}</div><div class="blocks">{blk}</div>'
            f'<ul class="gates">{lis}</ul>{extra}'
            + (f'<div class="headline">{e(headline)}</div>' if headline else "") + "</div>")


STEP_BLOCKS = {"sim": ["chipyard", "verilator"], "fpga": ["vivado"], "asic": ["asic", "chipyard"]}


def flow_html(cards: list[str]) -> str:
    out = []
    for i, c in enumerate(cards):
        if i:
            out.append('<div class="arrow">→</div>')
        out.append(c)
    return f'<h2>Flow</h2><div class="flow">{"".join(out)}</div>'


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def timeline_html(report: dict, final: bool) -> str:
    tasks = report.get("tasks") or []
    if not tasks:
        return ""
    now = time.time()
    t0 = min(t.get("t_start") or t["t_submit"] for t in tasks)
    t1 = max([(t.get("t_end") or now) for t in tasks] + ([now] if not final else []))
    span = max(t1 - t0, 1.0)
    pct = lambda x: f"{(x - t0) / span * 100:.3f}%"
    rows_ = []
    used = [b for b in BLOCKS if any(t["block"] == b for t in tasks)] + \
        sorted({t["block"] for t in tasks} - set(BLOCKS))
    for b in used:
        lanes: list[float] = []                      # thời điểm kết thúc của bar cuối trên mỗi làn
        bars: list[list[str]] = []
        for t in sorted((t for t in tasks if t["block"] == b), key=lambda t: t.get("t_start") or t["t_submit"]):
            s = t.get("t_start") or t["t_submit"]
            en = t.get("t_end") or now
            li = next((i for i, end in enumerate(lanes) if end <= s + 0.05), None)
            if li is None:
                lanes.append(en)
                bars.append([])
                li = len(lanes) - 1
            lanes[li] = en
            lab = t["label"] + (f" · {t['round']}" if t.get("round") else "")
            sec = t.get("seconds")
            tip = f"{lab} · {sec:.1f} s" if isinstance(sec, (int, float)) else f"{lab} · running"
            if t.get("host"):
                tip += f" @ {t['host']}"
            w = max((en - s) / span * 100, 0.25)
            bars[li].append(f'<div class="bar b-{t["state"]}" style="left:{pct(s)};width:{w:.3f}%" title="{e(tip)}">'
                            f'{e(lab)}</div>')
        lane_html = "".join(f'<div class="lane">{"".join(x)}</div>' for x in bars)
        rows_.append(f'<div class="grow"><div class="gname">{e(b)}<span>{e(BLOCK_NOTE.get(b, ""))}</span></div>'
                     f'<div class="lanes">{lane_html}</div></div>')
    step = next(s for s in (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600) if span / s <= 8)
    ticks = "".join(f'<span style="left:{k * step / span * 100:.3f}%">'
                    f'{k * step // 60}:{k * step % 60:02d}</span>' for k in range(int(span // step) + 1))
    par = max_parallel(tasks, now)
    note = f"{len(tasks)} CHIA tasks · wall time {span / 60:.1f} min · up to {par} at once"
    return (f'<h2>Timeline <span class="sub" style="text-transform:none;letter-spacing:0">· {e(note)}</span></h2>'
            f'<div class="card"><div class="gantt">{"".join(rows_)}</div>'
            f'<div class="axis"><div>min:s</div><div class="ticks">{ticks}</div></div>'
            '<div class="legend"><span><i style="background:var(--ok)"></i>passed</span><span><i style="background:'
            'var(--bad)"></i>failed</span><span><i style="background:var(--run)"></i>running</span></div></div>')


def max_parallel(tasks: list, now: float) -> int:
    ev = []
    for t in tasks:
        ev += [(t.get("t_start") or t["t_submit"], 1), (t.get("t_end") or now, -1)]
    cur = best = 0
    for _, d in sorted(ev, key=lambda x: (x[0], x[1])):
        cur += d
        best = max(best, cur)
    return best


# ---------------------------------------------------------------------------
# Trang
# ---------------------------------------------------------------------------

def page(title: str, h1: str, sub: str, status: str, body: str, live: bool) -> str:
    refresh = '<meta http-equiv="refresh" content="3">' if live else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">{refresh}'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>'
            f'<style>{CSS}</style></head><body><main><header><div><h1>{h1}</h1><div class="sub">{sub}</div></div>'
            f'<div>{status}</div></header>{body}</main>{JS}</body></html>')


def running_set(report: dict, round_: str | None = None) -> set[str]:
    return {t["name"] for t in report.get("tasks", []) if t["state"] == "run"
            and (round_ is None or t.get("round") == round_)}


def files_html(run_dir: Path, report: dict, fails: list[tuple[str, str]]) -> str:
    links = []
    if report.get("profile") and os.path.isfile(report["profile"]):
        links.append(f'<a href="{e(rel(run_dir, report["profile"]))}">CHIA profiler timeline</a>')
    for f in ("results/baseline.json", "results/agent.json", "results/compare.md"):
        if (run_dir / f).is_file():
            links.append(f'<a href="{f}">{f.split("/")[-1]}</a>')
    out = [f'<p class="links">{"".join(links)}</p>'] if links else []
    for name, tail in fails:
        out.append(f'<details><summary>Log tail · {e(name)}</summary><pre>{e(tail[-3000:])}</pre></details>')
    if not out:
        return ""
    return "<h2>Logs &amp; files</h2>" + "".join(out)


def overall(final: bool, report: dict, bad: str | None) -> str:
    if not final:
        return chip("run", "running", True) + f' <span class="sub">{timer(report.get("t0"))}</span>'
    return chip("bad", f"stopped: {bad}", True) if bad else chip("ok", "RESULT: OK", True)


def render_baseline(report: dict, final: bool) -> str:
    run_dir, cfg = Path(report["run_dir"]), report.get("cfg") or {}
    steps, run = report.get("steps", {}), running_set(report)
    stages = report.get("params", {}).get("stages", list(STEPS))
    enabled = cfg.get("steps", list(STEPS))
    cards, res_cards, fails = [], [], []
    for step in STEPS:
        if step not in enabled:
            continue
        if step not in stages:
            cards.append(step_card(step, cfg, "skip", [], STEP_BLOCKS[step]))
            continue
        gs = gates(step, steps, run, baseline=True)
        state = step_state(gs)
        if report.get("stopped_at") and state == "wait":
            state = "wait"
        m = (steps.get("sim_run") if step == "sim" else steps.get("fpga") if step == "fpga" else steps.get("asic")) or {}
        head = ""
        if step == "sim" and m.get("ok"):
            head = (f'coverage {m.get("fcov_hit")}/{m.get("fcov_total")} bins · {fmt(m.get("checked"))} checks'
                    if m.get("fcov_hit") is not None else
                    f'line {m.get("cov_line")}/{m.get("cov_line_total")} · branch {m.get("cov_branch")}/'
                    f'{m.get("cov_branch_total")}')
        elif step == "fpga" and (m.get("bitstream") or m.get("routed")):
            head = f'{fmt(m.get("clock_mhz"))} MHz · {fmt(m.get("lut"))} LUT · {fmt(m.get("power_dynamic_w"), 1000)} mW'
        elif step == "asic" and steps.get("asic_finish", {}).get("gds"):
            fin = steps["asic_finish"]
            head = f'{fmt(fin.get("area_um2"))} µm² cells · {fmt(fin.get("power_total_w"), 1000)} mW'
        cards.append(step_card(step, cfg, state, gs, STEP_BLOCKS[step], head))
        if step == "asic":
            m = {**steps.get("asic_finish", {}), **(steps.get("asic") or {})}
        if m:
            img = ""
            if step == "asic" and rel(run_dir, m.get("layout_png")):
                img = f'<figure><img src="{e(rel(run_dir, m["layout_png"]))}" alt="layout"><figcaption>Final layout ' \
                      f'(GDS)</figcaption></figure>'
            res_cards.append(f'<div class="card"><h4>{TITLE[step]}</h4>{table(step, cfg, m)}{img}</div>')
    for name, r in steps.items():
        if isinstance(r, dict) and r.get("ok") is False and r.get("log_tail"):
            fails.append((name, r["log_tail"]))
    title = f'{e(report.get("project", report.get("demo", "")))} <small>· baseline</small>'
    sub = (f'{e(cfg.get("title", ""))} · phase 1: reference run orchestrated by CHIA, no agent · '
           f'<code>{e(report.get("run_id", ""))}</code>')
    body = flow_html(cards) + timeline_html(report, final)
    if res_cards:
        body += f'<h2>Results</h2><div class="results">{"".join(res_cards)}</div>'
    body += files_html(run_dir, report, fails)
    return page(f'{report.get("project", "")} baseline', title, sub, overall(final, report, report.get("stopped_at")), body,
                not final)


def round_card(r: dict, report: dict) -> str:
    run = running_set(report, r["round"])
    if r.get("state") in ("agent", "measure") and run:
        what = "Claude is reading the reports and editing" if "agent" in run else \
            "CHIA is re-measuring on the real tools"
        return (f'<div class="card round step s-run"><h5>{e(r["round"])} {chip("run", what)}</h5>'
                + (f'<div class="meta">changed <code>{e(", ".join(r["changed"]))}</code></div>' if r.get("changed") else "")
                + "</div>")
    acc = r.get("accepted")
    nochange = r.get("verdict") == "no change by the agent"
    st = "ok" if acc else ("wait" if nochange else "bad")
    word = "ACCEPTED" if acc else ("NO CHANGE" if nochange else "REJECTED")
    meta = [f'agent {r.get("agent_seconds") or 0:.0f} s']
    sub = (r.get("measure") or {}).get("sub") or {}
    if sub:
        meta.append(f'measured {sum((v.get("seconds") or 0) for v in sub.values()):.0f} s on {len(sub)} tasks')
    if r.get("changed"):
        meta.append("changed <code>" + e(", ".join(r["changed"])) + "</code>")
    summ = (r.get("summary") or "").replace("**", "").strip()
    return (f'<div class="card round"><h5>{e(r["round"])} {chip(st, word)}</h5>'
            f'<div class="meta">{" · ".join(meta)}</div><div class="verdict">{e(r.get("verdict", ""))}</div>'
            + (f'<details open><summary>Agent summary</summary><pre>{e(summ[-2500:])}</pre></details>' if summ else "")
            + "</div>")


def headline_agent(step: str, base: dict, best: dict) -> str:
    def pct(k):
        try:
            return f"{(float(best[k]) - float(base[k])) / abs(float(base[k])) * 100:+.1f} %"
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return "?"
    if step == "sim":
        if base.get("fcov_hit") is None:
            return (f'line {base.get("cov_line")} → {best.get("cov_line")} · branch {base.get("cov_branch")} → '
                    f'{best.get("cov_branch")} · toggle {base.get("cov_toggle")} → {best.get("cov_toggle")}')
        return f'coverage {base.get("fcov_hit")}/{base.get("fcov_total")} → {best.get("fcov_hit")}/{best.get("fcov_total")} bins'
    if step == "fpga":
        return f'WNS {fmt(base.get("wns_ns"))} → {fmt(best.get("wns_ns"))} ns · dyn. power {pct("power_dynamic_w")}'
    return f'die {pct("die_area_um2")} · cells {pct("area_um2")} · power {pct("power_total_w")}'


def render_agent(report: dict, final: bool) -> str:
    run_dir, cfg = Path(report["run_dir"]), report.get("cfg") or {}
    steps = report.get("steps", list(STEPS))
    rounds, result, base_m = report.get("rounds", []), report.get("result", {}), report.get("base_metrics", {})
    cards, sections, fails = [], [], []
    for step in STEPS:
        if step not in cfg.get("steps", STEPS):
            continue
        if step not in steps:
            cards.append(step_card(step, cfg, "skip", [], STEP_BLOCKS[step]))
            continue
        rs = [r for r in rounds if r["step"] == step]
        res = result.get(step) or {}
        base, best = res.get("baseline") or base_m.get(step) or {}, res.get("best")
        live = next((r for r in rs if r.get("state") in ("agent", "measure")), None)
        gs = []
        for r in rs:
            if r is live:
                gs.append((r["round"], "run", "Claude" if r["state"] == "agent" else "measuring"))
            else:
                acc, noc = r.get("accepted"), r.get("verdict") == "no change by the agent"
                gs.append((r["round"], "ok" if acc else ("wait" if noc else "bad"),
                           "accepted" if acc else ("no change" if noc else "rejected")))
        n_acc = sum(1 for r in rs if r.get("accepted"))
        if step in report.get("done_steps", []):
            state = "ok" if n_acc else "warn"
        elif live or rs:
            state = "run"
        else:
            state = "wait"
        txt_state = state
        head = headline_agent(step, base, best) if (best and n_acc) else ("baseline kept" if state == "warn" else "")
        card = step_card(step, cfg, txt_state, gs, STEP_BLOCKS[step] + ["claude"], head)
        cards.append(card.replace("passed, not signed off", "baseline kept") if state == "warn" else card)
        if not rs:
            continue
        img = ""
        if step == "asic" and n_acc:
            figs = []
            for cap, m in (("Baseline", base), ("Best", best)):
                p = rel(run_dir, (m or {}).get("layout_png"))
                if p:
                    figs.append(f'<figure><img src="{e(p)}" alt="layout"><figcaption>{cap}</figcaption></figure>')
            if len(figs) == 2:
                img = f'<div class="imgs">{"".join(figs)}</div>'
        sections.append(f'<div class="agentrow"><div class="card"><h4>{TITLE[step]} '
                        f'<span class="sub">{e(tool_of(step, cfg))}</span></h4>{table(step, cfg, base, best or base)}'
                        f'{img}</div><div class="rounds">{"".join(round_card(r, report) for r in rs)}</div></div>')
        for r in rs:
            if r.get("gate_fail_tail"):
                fails.append((f'{r["round"]} gate', r["gate_fail_tail"]))
    title = f'{e(report.get("project", ""))} <small>· agent</small>'
    sub = (f'{e(cfg.get("title", ""))} · phase 2: Claude ({e(report.get("model", ""))}) proposes edits, CHIA re-measures '
           f'on the real tools and accepts or rejects · baseline <code>{e(os.path.basename(report.get("baseline", "")))}'
           f'</code> · <code>{e(report.get("run_id", ""))}</code>')
    body = flow_html(cards) + timeline_html(report, final)
    if sections:
        body += f'<h2>Results</h2><div class="results agent">{"".join(sections)}</div>'
    body += files_html(run_dir, report, fails)
    return page(f'{report.get("project", "")} agent', title, sub, overall(final, report, report.get("stopped")), body,
                not final)


_warned = False


def write(report: dict, final: bool = False) -> None:
    """Ghi <run_dir>/status.html (ghi file tạm rồi đổi tên, nên trình duyệt không thấy trang dở)."""
    global _warned
    try:
        run_dir = Path(report["run_dir"])
        text = (render_agent if report.get("phase") == "agent" else render_baseline)(report, final)
        tmp = run_dir / ".status.html.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, run_dir / "status.html")
    except Exception as ex:                                     # noqa: BLE001
        if not _warned:
            print(f"[status] không ghi được status.html: {ex!r}", flush=True)
            _warned = True


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    run_dir = Path(sys.argv[1]).expanduser().resolve()
    for name in ("agent.json", "baseline.json"):
        p = run_dir / "results" / name
        if p.is_file():
            rep = json.loads(p.read_text())
            rep["run_dir"] = str(run_dir)
            write(rep, final=True)
            print(run_dir / "status.html")
            return
    raise SystemExit(f"không thấy results/agent.json hay results/baseline.json trong {run_dir}")


if __name__ == "__main__":
    main()
