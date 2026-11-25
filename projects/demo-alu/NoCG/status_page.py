"""status_page.py - trang trạng thái HTML cho demo (baseline.py, agent.py), để xem trực quan khi quay video/chụp slide.

baseline.py và agent.py ghi lại <run_dir>/status.html sau mỗi sự kiện (bắt đầu/xong một task CHIA, agent sửa xong,
nhận/loại một vòng). Trang tự làm mới mỗi 3 giây khi lần chạy chưa xong. Cách xem tiện nhất là ../run_demo.py: nó
submit job, phục vụ trang ở http://127.0.0.1 và tự mở trình duyệt (giao diện tối).
Dựng lại trang từ kết quả của một lần chạy đã xong (results/agent.json hoặc results/baseline.json):
  python status_page.py <run_dir>
Chỉ đọc số liệu, không ảnh hưởng gate hay việc đo. File này giống hệt nhau ở NoCG/ và CG/ (job chỉ mang theo
--working-dir của demo).
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
from pathlib import Path

STEPS = ("sim", "fpga", "asic")
TITLE = {"sim": "Sim", "fpga": "FPGA", "asic": "ASIC"}
TOOL = {"sim": "Verilator", "fpga": "Vivado 2025.1 · Arty A7-100T", "asic": "Yosys + OpenROAD (ORFS) · sky130hd"}
# Task CHIA -> khối (resource) chạy nó.
BLOCK = {"sim_build": "chipyard", "sim_run": "verilator", "fpga_sim": "vivado", "fpga": "vivado", "agent": "claude"}
ASIC_STAGES = ["synth", "floorplan", "place", "cts", "route", "finish", "drc", "lvs"]
SUBTASKS = {"sim": ["sim_build", "sim_run"], "fpga": ["fpga"], "asic": [f"asic_{s}" for s in ASIC_STAGES]}
LABEL = {"sim_build": "build (Verilator)", "sim_run": "chạy testbench", "fpga_sim": "xsim tb_fpga_top",
         "fpga": "synth → bitstream", **{f"asic_{s}": s for s in ASIC_STAGES}}

# (nhãn, khoá, hệ số, hướng tốt: +1 lớn hơn tốt, -1 nhỏ hơn tốt, 0 trung tính, đơn vị ns: in hiệu tuyệt đối)
ROWS = {
    "sim": [("Functional coverage (bin)", "fcov", 1, 0, False), ("Line (DUT)", "cov_line_s", 1, 0, False),
            ("Toggle (DUT)", "cov_toggle_s", 1, 0, False), ("Branch (DUT)", "cov_branch_s", 1, 0, False),
            ("Số lần kiểm tra", "checked", 1, +1, False), ("Lỗi", "errors", 1, -1, False)],
    "fpga": [("Clock (MHz)", "clock_mhz", 1, 0, False), ("Timing", "timing_s", 1, 0, False),
             ("WNS (ns)", "wns_ns", 1, +1, True), ("WHS (ns)", "whs_ns", 1, +1, True),
             ("Fmax ước lượng (MHz)", "fmax_est_mhz", 1, +1, False), ("LUT", "lut", 1, -1, False),
             ("FF", "ff", 1, -1, False), ("BUFG", "bufg", 1, 0, False),
             ("P động SAIF (mW)", "power_dynamic_w", 1000, -1, False),
             ("P u_alu SAIF (mW)", "power_alu_w", 1000, -1, False),
             ("P tổng SAIF (mW)", "power_total_w", 1000, -1, False),
             ("P động vector-less (mW)", "power_dynamic_vl_w", 1000, -1, False),
             ("Cảnh báo", "warnings", 1, -1, False)],
    "asic": [("Diện tích cell (µm²)", "area_um2", 1, -1, False), ("Diện tích die (µm²)", "die_area_um2", 1, -1, False),
             ("Util (%)", "util_pct", 1, 0, False), ("Số cell", "n_logic_cells", 1, 0, False),
             ("ICG", "n_icg", 1, 0, False), ("Buffer clock", "n_clkbuf", 1, 0, False),
             ("Setup WS (ns)", "setup_ws_ns", 1, +1, True), ("Hold WS (ns)", "hold_ws_ns", 1, +1, True),
             ("Công suất (mW)", "power_total_w", 1000, -1, False), ("DRC router", "drc_errors", 1, -1, False), ("DRC KLayout", "drc_klayout", 1, -1, False),
             ("LVS", "lvs", 1, 0, False)],
}

CSS = """
:root{color-scheme:dark;--bg:#0f1419;--card:#171d24;--ink:#e6e9ed;--mute:#8b95a1;--line:#2a323c;--ok:#3fcf7f;
--okbg:#163323;--bad:#ff6b5b;--badbg:#3a1a17;--run:#5aa9ff;--runbg:#152a44;--wait:#8b95a1;--waitbg:#232a32}
a{color:var(--run)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}main{max-width:1280px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:21px;margin:28px 0 10px}.sub{color:var(--mute);font-size:14px}
.pipe{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:10px;align-items:stretch;margin:18px 0}
.arrow{align-self:center;font-size:28px;color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.stage{border-width:2px}.stage h3{margin:0;font-size:22px;display:flex;justify-content:space-between;gap:8px}
.stage .tool{color:var(--mute);font-size:13px;margin-bottom:8px}
.s-run{border-color:var(--run);box-shadow:0 0 0 3px var(--runbg)}.s-ok{border-color:var(--ok)}
.s-bad{border-color:var(--bad)}.s-wait{opacity:.65}
.chip{display:inline-block;font-size:13px;font-weight:600;padding:2px 9px;border-radius:99px;white-space:nowrap}
.c-ok{background:var(--okbg);color:var(--ok)}.c-bad{background:var(--badbg);color:var(--bad)}
.c-run{background:var(--runbg);color:var(--run)}.c-wait{background:var(--waitbg);color:var(--wait)}
ul.tasks{list-style:none;margin:0;padding:0;font-size:14px}ul.tasks li{display:flex;justify-content:space-between;
gap:8px;padding:2px 0;border-bottom:1px dashed var(--line)}ul.tasks li:last-child{border:0}
.blk{color:var(--mute);font-size:12px}.headline{margin-top:8px;font-size:15px;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{padding:5px 8px;border-bottom:1px solid var(--line);
text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}
th{color:var(--mute);font-weight:600;font-size:13px}.up{color:var(--ok);font-weight:600}
.down{color:var(--bad);font-weight:600}.flat{color:var(--mute)}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:14px;align-items:start}
.rounds{display:flex;flex-direction:column;gap:10px}.round h4{margin:0 0 4px;display:flex;gap:8px;align-items:center;
font-size:17px}.round .meta{color:var(--mute);font-size:13px}.round .verdict{font-size:14px;margin:4px 0}
.round pre{white-space:pre-wrap;font:14px/1.45 system-ui,sans-serif;margin:6px 0 0;background:var(--bg);padding:8px 10px;
border-radius:6px;max-height:260px;overflow:auto}code{font-size:13px}
.imgs{display:grid;grid-template-columns:1fr 1fr;gap:10px}.imgs figure{margin:0}.imgs img{width:100%;
border:1px solid var(--line);border-radius:6px;background:#000}.imgs figcaption{font-size:13px;color:var(--mute)}
.timer{font-variant-numeric:tabular-nums}.links a{margin-right:14px}
@media (max-width:820px){.pipe{grid-template-columns:1fr}.arrow{transform:rotate(90deg);justify-self:center}
.grid2,.imgs{grid-template-columns:1fr}}
"""

JS = """<script>
function tick(){var n=Date.now()/1000;document.querySelectorAll('[data-since]').forEach(function(e){
var s=Math.max(0,Math.round(n-parseFloat(e.dataset.since)));e.textContent=Math.floor(s/60)+':'+
String(s%60).padStart(2,'0');});}tick();setInterval(tick,1000);
</script>"""

e = lambda s: html.escape(str(s))


def fmt(v, scale=1) -> str:
    if v is None or v == "":
        return "–"
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


def _cov_view(m: dict | None) -> dict | None:
    """Thêm khoá dạng chữ 'đã gặp/tổng' cho các dòng coverage của bảng Sim."""
    if m is None:
        return None
    m = dict(m)
    if m.get("fcov_hit") is not None:
        m["fcov"] = f'{m["fcov_hit"]}/{m.get("fcov_total")}'
    for k in ("line", "toggle", "branch"):
        if m.get(f"cov_{k}") is not None:
            m[f"cov_{k}_s"] = f'{m[f"cov_{k}"]}/{m.get(f"cov_{k}_total")}'
    return m


def _fpga_view(m: dict | None) -> dict | None:
    if m is None or "timing_met" not in m:
        return m
    so = m.get("signoff_met", m["timing_met"])
    return {**m, "timing_s": ("đạt" if m["timing_met"] else "KHÔNG đạt") + ("" if so else " (sign-off KHÔNG đạt)")}


def table(step: str, base: dict, best: dict | None) -> str:
    if step == "sim":
        base, best = _cov_view(base), _cov_view(best)
    if step == "fpga":
        base, best = _fpga_view(base), _fpga_view(best)
    rows = []
    for name, k, sc, good, ns in ROWS[step]:
        if best is None:
            rows.append(f"<tr><td>{e(name)}</td><td>{fmt(base.get(k), sc)}</td></tr>")
        else:
            rows.append(f"<tr><td>{e(name)}</td><td>{fmt(base.get(k), sc)}</td><td>{fmt(best.get(k), sc)}</td>"
                        f"<td>{delta(base.get(k), best.get(k), sc, good, ns)}</td></tr>")
    head = "<tr><th></th><th>Giá trị</th></tr>" if best is None else \
        "<tr><th></th><th>Baseline</th><th>Tốt nhất</th><th>Thay đổi</th></tr>"
    return f'<table>{head}{"".join(rows)}</table>'


def chip(state: str, text: str) -> str:
    return f'<span class="chip c-{state}">{e(text)}</span>'


def timer(since) -> str:
    return f'<span class="timer" data-since="{since}"></span>' if since else ""


def rel(run_dir: Path, p) -> str | None:
    if not p or not os.path.isfile(p):
        return None
    return os.path.relpath(p, run_dir)


def slide_summary(text: str) -> str:
    """Phần 'Tóm tắt' tiếng Việt cuối bản tóm tắt của agent (prompt yêu cầu); không có thì lấy cả."""
    text = (text or "").strip()
    i = text.lower().rfind("tóm tắt")
    if i > 0:
        text = text[text.rfind("\n", 0, i) + 1:]
    return text.replace("**", "")


def page(title: str, sub: str, body: str, live: bool) -> str:
    refresh = '<meta http-equiv="refresh" content="3">' if live else ""
    return (f'<!doctype html><html lang="vi"><head><meta charset="utf-8">{refresh}'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>'
            f'<style>{CSS}</style></head><body><main><h1>{e(title)}</h1><div class="sub">{sub}</div>{body}'
            f'</main>{JS}</body></html>')


def pipeline(cards: list[str]) -> str:
    out = []
    for i, c in enumerate(cards):
        if i:
            out.append('<div class="arrow">→</div>')
        out.append(c)
    return f'<div class="pipe">{"".join(out)}</div>'


def stage_card(step: str, state: str, chip_html: str, inner: str) -> str:
    return (f'<div class="card stage s-{state}"><h3>{TITLE[step]} {chip_html}</h3>'
            f'<div class="tool">{e(TOOL[step])}</div>{inner}</div>')


# ---------------------------------------------------------------------------
# Pha 1: baseline
# ---------------------------------------------------------------------------

def render_baseline(report: dict, final: bool) -> str:
    run_dir = Path(report["run_dir"])
    steps, live = report.get("steps", {}), report.get("live") or {}
    stages = report.get("params", {}).get("stages", list(STEPS))
    stopped = report.get("stopped_at")
    cards, sections = [], []
    for step in STEPS:
        subs = SUBTASKS[step]
        if step not in stages:
            cards.append(stage_card(step, "wait", chip("wait", "bỏ qua"), ""))
            continue
        lis, states = [], []
        for t in subs:
            r = steps.get(t)
            if r is not None:
                st = "ok" if r.get("ok") else "bad"
                right = f'{chip(st, "OK" if r.get("ok") else "LỖI")} {r.get("seconds", 0):.0f}s'
            elif not final and live.get("task") == t:
                st, right = "run", f'{chip("run", "đang chạy")} {timer(live.get("since"))}'
            else:
                st, right = "wait", chip("wait", "chờ")
            states.append(st)
            lis.append(f'<li><span>{e(LABEL[t])} <span class="blk">@ {BLOCK.get(t, "asic")}</span></span>'
                       f'<span>{right}</span></li>')
        state = "bad" if "bad" in states else "run" if "run" in states else "ok" if all(
            s == "ok" for s in states) else "wait"
        txt = {"bad": "dừng", "run": "đang chạy", "ok": "qua gate", "wait": "chờ"}[state]
        last = steps.get(subs[-1])
        if step == "asic" and last is not None:          # số liệu ở finish, DRC KLayout ở drc, LVS ở lvs
            last = {**steps.get("asic_finish", {}), **steps.get("asic_drc", {}), **last}
        head = ""
        if last and last.get("ok"):
            head = {"sim": lambda r: f'PASS · coverage {r.get("fcov_hit")}/{r.get("fcov_total")} bin',
                    "fpga": lambda r: (f'{fmt(r.get("clock_mhz"))} MHz · WNS {fmt(r.get("wns_ns"))} ns'
                                       + ("" if r.get("signoff_met", r.get("timing_met", True)) else " (sign-off KHÔNG đạt)")),
                    "asic": lambda r: f'die {fmt(r.get("die_area_um2"))} µm² · GDS · DRC sạch · LVS khớp'}[step](last)
        cards.append(stage_card(step, state, chip(state, txt), f'<ul class="tasks">{"".join(lis)}</ul>'
                                + (f'<div class="headline">{e(head)}</div>' if head else "")))
        if last and last.get("ok"):
            extra = ""
            if step == "sim":
                extra = f'<p class="sub"><code>{e(last.get("result", ""))}</code><br>VCD: {fmt(last.get("vcd_bytes"))} byte</p>'
            elif step == "fpga":
                extra = f'<p class="sub">Bitstream: <code>{e(os.path.basename(last.get("bitstream") or "–"))}</code></p>'
            img = ""
            if step == "asic" and rel(run_dir, last.get("layout_png")):
                img = f'<div class="imgs"><figure><img src="{e(rel(run_dir, last["layout_png"]))}"><figcaption>' \
                      f'Layout (GDS)</figcaption></figure></div>'
            sections.append(f'<h2>{TITLE[step]}</h2><div class="grid2"><div class="card">{table(step, last, None)}'
                            f'{extra}</div><div>{img}</div></div>')
    if final:
        status = chip("ok", "KẾT QUẢ: OK") if not stopped else chip("bad", f"DỪNG ở {stopped}")
    else:
        status = chip("run", "đang chạy") + f' {timer(report.get("t0"))}'
    sub = (f'Pha 1 · baseline do CHIA điều phối, không có agent · <code>{e(report.get("run_id", ""))}</code> · '
           f'{status}')
    return page(f'Demo {report.get("demo", "")}: baseline', sub,
                pipeline(cards) + "".join(sections) + links(run_dir, report), not final)


def links(run_dir: Path, report: dict) -> str:
    out = []
    if report.get("profile") and os.path.isfile(report["profile"]):
        out.append(f'<a href="{e(rel(run_dir, report["profile"]))}">Timeline CHIA (task trên từng khối)</a>')
    cmp_ = run_dir / "results" / "compare.md"
    if cmp_.is_file():
        out.append('<a href="results/compare.md">compare.md</a>')
    return f'<h2>Tệp</h2><p class="links">{"".join(out)}</p>' if out else ""


# ---------------------------------------------------------------------------
# Pha 2: agent
# ---------------------------------------------------------------------------

def headline(step: str, base: dict, best: dict) -> str:
    def pct(k, sc=1):
        try:
            return f"{(float(best[k]) - float(base[k])) / abs(float(base[k])) * 100:+.1f} %"
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return "?"
    if step == "sim":
        return (f'coverage {base.get("fcov_hit")}/{base.get("fcov_total")} → '
                f'{best.get("fcov_hit")}/{best.get("fcov_total")} bin')
    if step == "fpga":
        return f'WNS {fmt(base.get("wns_ns"))} → {fmt(best.get("wns_ns"))} ns'
    return f'die {pct("die_area_um2")} · công suất {pct("power_total_w")}'


def round_card(r: dict, live: dict | None) -> str:
    if live:
        what = "agent Claude đang đọc báo cáo và sửa" if live.get("state") == "agent" else \
            f'CHIA đang đo lại: {LABEL.get(live.get("task"), live.get("task") or "")}'
        blk = "claude" if live.get("state") == "agent" else BLOCK.get(live.get("task"), "asic")
        return (f'<div class="card round s-run"><h4>{e(live["round"])} {chip("run", what)}</h4>'
                f'<div class="meta">khối <b>{e(blk)}</b> · {timer(live.get("since"))}</div>'
                + (f'<div class="meta">agent đã đổi: <code>{e(", ".join(r["changed"]))}</code></div>'
                   if r and r.get("changed") else "") + "</div>")
    acc = r.get("accepted")
    st = "ok" if acc else "bad"
    word = "NHẬN" if acc else ("KHÔNG ĐỔI" if r.get("verdict") == "agent không đổi gì" else "LOẠI")
    meta = [f'agent {r.get("agent_seconds") or 0:.0f}s @ claude']
    sub = (r.get("measure") or {}).get("sub") or {}
    if sub:
        meta.append("đo " + f'{sum((v.get("seconds") or 0) for v in sub.values()):.0f}s')
    if r.get("changed"):
        meta.append("đổi <code>" + e(", ".join(r["changed"])) + "</code>")
    summ = slide_summary(r.get("summary", ""))
    return (f'<div class="card round s-{st}"><h4>{e(r["round"])} {chip(st, word)}</h4>'
            f'<div class="meta">{" · ".join(meta)}</div><div class="verdict">{e(r.get("verdict", ""))}</div>'
            + (f'<details open><summary>Agent tóm tắt</summary><pre>{e(summ)}</pre></details>' if summ else "")
            + "</div>")


def render_agent(report: dict, final: bool) -> str:
    run_dir = Path(report["run_dir"])
    live = None if final else (report.get("live") or None)
    steps = report.get("steps", list(STEPS))
    rounds, result = report.get("rounds", []), report.get("result", {})
    base_m = report.get("base_metrics", {})
    cards, sections = [], []
    for step in STEPS:
        rs = [r for r in rounds if r["step"] == step]
        res = result.get(step)
        base, best = (res or {}).get("baseline") or base_m.get(step) or {}, (res or {}).get("best")
        here = live and live.get("step") == step
        if step not in steps:
            cards.append(stage_card(step, "wait", chip("wait", "bỏ qua"), ""))
            continue
        lis = []
        for r in rs:
            if here and r["round"] == live.get("round"):
                continue
            st = "ok" if r.get("accepted") else "bad"
            lis.append(f'<li><span>{e(r["round"])}</span><span>{chip(st, "NHẬN" if r.get("accepted") else "LOẠI")}'
                       f'</span></li>')
        if here:
            lis.append(f'<li><span>{e(live["round"])}</span><span>{chip("run", "agent" if live["state"] == "agent" else "đo")}'
                       f' {timer(live.get("since"))}</span></li>')
        n_acc = sum(1 for r in rs if r.get("accepted"))
        done = res is not None and not here and (final or step != (live or {}).get("step")) and rs
        if here:
            state, txt = "run", "đang chạy"
        elif done:
            state, txt = ("ok", f"{n_acc} vòng nhận") if n_acc else ("bad", "giữ baseline")
        else:
            state, txt = "wait", "chờ"
        hl = headline(step, base, best) if (best and n_acc) else ("không vòng nào tốt hơn" if done else "")
        cards.append(stage_card(step, state, chip(state, txt), f'<ul class="tasks">{"".join(lis)}</ul>'
                                + (f'<div class="headline">{e(hl)}</div>' if hl else "")))
        if not (rs or here):
            continue
        cur = None
        if here:
            cur = next((r for r in rs if r["round"] == live.get("round")), {})
        cards_html = "".join(round_card(r, None) for r in rs if not (here and r["round"] == live.get("round")))
        if here:
            cards_html += round_card(cur, live)
        img = ""
        if step == "asic":
            figs = []
            for cap, m in (("Baseline", base), ("Tốt nhất", best)):
                p = rel(run_dir, (m or {}).get("layout_png"))
                if p:
                    figs.append(f'<figure><img src="{e(p)}"><figcaption>{cap}</figcaption></figure>')
            if len(figs) == 2 and n_acc:
                img = f'<div class="imgs" style="margin-top:12px">{"".join(figs)}</div>'
        sections.append(f'<h2>{TITLE[step]} <span class="sub">{e(TOOL[step])}</span></h2><div class="grid2">'
                        f'<div><div class="card">{table(step, base, best or base)}</div>{img}</div>'
                        f'<div class="rounds">{cards_html}</div></div>')
    if final:
        status = chip("bad", "DỪNG " + report["stopped"]) if report.get("stopped") else chip("ok", "KẾT QUẢ: OK")
    else:
        status = chip("run", "đang chạy") + f' {timer(report.get("t0"))}'
    sub = (f'Pha 2 · agent Claude ({e(report.get("model", ""))}) tối ưu từng bước, CHIA đo lại trên công cụ thật và '
           f'nhận/loại · baseline <code>{e(os.path.basename(report.get("baseline", "")))}</code> · '
           f'<code>{e(report.get("run_id", ""))}</code> · {status}')
    return page(f'Demo {report.get("demo", "")}: agent tối ưu Sim → FPGA → ASIC', sub,
                pipeline(cards) + "".join(sections) + links(run_dir, report), not final)


# ---------------------------------------------------------------------------

_warned = False


def write(report: dict, final: bool = False) -> None:
    """Ghi <run_dir>/status.html (ghi file tạm rồi đổi tên, nên trình duyệt không thấy trang dở). Lỗi chỉ in cảnh báo
    một lần: trang trạng thái không được làm hỏng lần chạy."""
    global _warned
    try:
        run_dir = Path(report["run_dir"])
        text = (render_agent if report.get("phase") == "agent" else render_baseline)(report, final)
        tmp = run_dir / ".status.html.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, run_dir / "status.html")
    except Exception as ex:                                     # noqa: BLE001
        if not _warned:
            print(f"[status_page] không ghi được status.html: {ex!r}", flush=True)
            _warned = True


def set_live(report: dict, **live) -> None:
    report["live"] = {**live, "since": time.time()}
    write(report)


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
