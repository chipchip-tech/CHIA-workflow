"""tasks_asic.py - task CHIA của bước ASIC (ORFS, khối asic; GLS ở khối chipyard).

asic_stage(synth|floorplan|place|cts|route|finish)  `make <stage>` của ORFS, WORK_HOME trong thư mục chạy. finish còn đọc
        số liệu (metrics JSON, 6_finish.rpt, netlist), vẽ ảnh layout, và chuẩn bị GLS (model cell Yosys từ .lib tt).
Sau finish, các kiểm tra sign-off độc lập nhau chạy CÙNG LÚC (mỗi task xin cfg.asic.check_slots của resource asic):
asic_drc     `make drc`: KLayout chạy bộ luật DRC đầy đủ của platform trên GDS cuối; gate: 0 vi phạm
asic_lvs     `make lvs`: KLayout so GDS với netlist CDL; gate: khớp
asic_sta     một corner: kit asic/signoff.tcl qua `make run` (SPEF sau route, derate OCV hoặc .lib corner thật);
             gate: setup và hold slack >= 0, có ít nhất một clock
asic_gls     (khối chipyard) Verilator chạy asic/gls/tb_gls_<dut>.v trên 6_final.v + model cell; gate: RESULT: PASS
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from chia.base.ChiaFunction import ChiaFunction

from flow.common import RESULT_RE, num, profile_info, read, run_logged, sh, timed

ORFS_FLOW = "/OpenROAD-flow-scripts/flow"            # trong khối asic (ORFS của host mount vào đây)
MAKE_STAGES = ["synth", "floorplan", "place", "cts", "route", "finish"]


def paths(run_dir: str, cfg: dict) -> dict:
    asic = os.path.join(run_dir, "asic")
    work = os.path.join(asic, "orfs")
    sub = os.path.join(cfg["asic"]["platform"], cfg["dut"], "base")
    return {"asic": asic, "work": work, "config": os.path.join(asic, "config.mk"),
            **{k: os.path.join(work, k, sub) for k in ("logs", "reports", "results")}}


def _make(P: dict, target: str, timeout_s: int, log_name: str, extra: list[str] | None = None,
          env: dict | None = None) -> tuple[int, float, str]:
    cmd = ["make", "-C", ORFS_FLOW, f"DESIGN_CONFIG={P['config']}", f"WORK_HOME={P['work']}", target, *(extra or [])]
    return run_logged(cmd, ORFS_FLOW, timeout_s, os.path.join(P["asic"], "make", f"{log_name}.log"), env)


def _lib_dir(cfg: dict) -> Path:
    return Path(ORFS_FLOW, "platforms", cfg["asic"]["platform"], "lib")


def sta_corners(cfg: dict) -> list[tuple[str, str | None, float]]:
    """(tên, .lib hoặc None = LIB_FILES của flow, derate): tt, tt + OCV, và mọi .lib corner thật có trên máy."""
    a = cfg["asic"]
    out = [("tt", None, 0.0)]
    if a.get("ocv_derate"):
        out.append((f"tt_ocv{int(round(a['ocv_derate'] * 100))}", None, float(a["ocv_derate"])))
    for lib in sorted(_lib_dir(cfg).glob(a["corner_glob"])):
        out.append((lib.stem.replace(a["cell_prefix"], ""), str(lib), 0.0))
    return out


@ChiaFunction(resources={"asic": 1})
@timed
def asic_stage(run_dir: str, cfg: dict, stage: str, timeout_s: int = 3600) -> dict:
    P = paths(run_dir, cfg)
    rc, sec, tail = _make(P, stage, timeout_s, stage)
    res = {"stage": stage, "rc": rc, "seconds": sec, "ok": rc == 0, "log_tail": tail}
    if stage == "synth":
        _, ver, _ = sh("yosys -V; openroad -version 2>/dev/null | head -1", ORFS_FLOW)
        res["tools"] = ver.strip()
        if rc == 0 and not os.path.isdir(P["results"]):
            res["ok"] = False
            res["log_tail"] = f"make synth xong nhưng không có {P['results']}: ORFS này không nhận WORK_HOME?\n" + tail
    if stage == "finish" and rc == 0:
        res.update(_metrics(P, cfg))
        g = _gls_prep(P, cfg, timeout_s)
        res["gls_prep_ok"] = g["ok"]
        res["sta_corner_list"] = [list(c) for c in sta_corners(cfg)]
        res["ok"] = res["gate_ok"] and g["ok"]
        if not g["ok"]:
            res["log_tail"] = "chuẩn bị GLS (Yosys) lỗi:\n" + g["log_tail"]
    profile_info(step=f"asic_{stage}", ok=res["ok"], seconds=sec)
    return res


def _metrics(P: dict, cfg: dict) -> dict:
    """Số liệu sau finish: metrics JSON của ORFS (logs/*.json), báo cáo 6_finish.rpt, netlist 6_final.v."""
    metrics = {}
    for f in sorted(Path(P["logs"]).glob("*.json")):
        try:
            metrics.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            pass
    fin = read(os.path.join(P["reports"], "6_finish.rpt"))

    def m(keys, pattern=None):
        for k in keys:
            if isinstance(metrics.get(k), (int, float)):
                return metrics[k]
        return num(pattern, fin) if pattern else None

    gds = os.path.join(P["results"], "6_final.gds")
    r = {
        "gds": gds if os.path.isfile(gds) else None,
        "setup_ws_ns": m(["finish__timing__setup__ws"], r"^worst slack max\s+(-?[\d.]+)"),
        "hold_ws_ns": m(["finish__timing__hold__ws"], r"^worst slack min\s+(-?[\d.]+)"),
        "setup_tns_ns": m(["finish__timing__setup__tns"], r"^tns(?: max)?\s+(-?[\d.]+)"),
        "area_um2": m(["finish__design__instance__area"], r"^Design area\s+([\d.]+)\s+u\^2"),
        "util_pct": m(["finish__design__instance__utilization"], r"^Design area\s+[\d.]+\s+u\^2\s+([\d.]+)%"),
        "core_area_um2": m(["finish__design__core__area", "floorplan__design__core__area"]),
        "die_area_um2": m(["finish__design__die__area", "floorplan__design__die__area"]),
        "stdcells": m(["finish__design__instance__count__stdcell"]),
        "power_total_w": m(["finish__power__total"], r"^Total\s+\S+\s+\S+\s+\S+\s+([\d.eE+-]+)"),
        "drc_errors": m(["detailedroute__route__drc_errors", "route__drc_errors"]),
        "antenna_nets": m(["detailedroute__antenna__violating__nets", "finish__antenna__violating__nets"]),
        "clock_period_ns": m(["finish__timing__clock__period"]),
    }
    if isinstance(r["util_pct"], float) and r["util_pct"] <= 1.0 and "finish__design__instance__utilization" in metrics:
        r["util_pct"] = round(100 * r["util_pct"], 2)           # metrics JSON ghi tỉ lệ 0..1
    pre = cfg["asic"]["cell_prefix"]
    cells = {}
    for t in re.findall(rf"^\s*{re.escape(pre)}(\w+?)_\d+\s+\S+\s*\(", read(os.path.join(P["results"], "6_final.v")), re.M):
        cells[t] = cells.get(t, 0) + 1
    logic = {k: v for k, v in cells.items() if not k.startswith(("tapvpwrvgnd", "decap", "fill", "tap"))}
    r["cells"] = dict(sorted(logic.items(), key=lambda kv: -kv[1]))
    r["n_flop"] = sum(v for k, v in logic.items() if re.match(r"(s?df|edf|sedf|dlx|dlr)", k))
    r["n_icg"] = sum(v for k, v in logic.items() if cfg["asic"]["icg_cell"] in k)
    r["n_clkbuf"] = sum(v for k, v in logic.items() if k.startswith(("clkbuf", "clkinv", "clkdlybuf")))
    r["n_logic_cells"] = sum(logic.values())
    steps = {}
    for f in sorted(Path(P["logs"]).glob("*.log")):
        mm = re.search(r"Elapsed time: (?:(\d+):)?(\d+):([\d.]+)", read(str(f)))
        if mm:
            steps[f.stem] = round(int(mm.group(1) or 0) * 3600 + int(mm.group(2)) * 60 + float(mm.group(3)), 1)
    r["orfs_step_seconds"] = steps
    png = os.path.join(P["asic"], "layout.png")
    if r["gds"]:
        lyp = next(iter(sorted(Path(ORFS_FLOW, "platforms", cfg["asic"]["platform"]).glob("*.lyp"))), None)
        cmd = f"klayout -zz -r render_gds.py -rd gds={r['gds']} -rd png={png}" + (f" -rd lyp={lyp}" if lyp else "")
        _, out, _ = sh(cmd, P["asic"], timeout=600)
        r["layout_png"] = png if os.path.isfile(png) else None
        if not r["layout_png"]:
            r["layout_png_error"] = out[-1000:]
    r["reports"] = P["reports"]
    r["metrics_json"] = {k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}
    r["gate_ok"] = (r["gds"] is not None and r["setup_ws_ns"] is not None and r["setup_ws_ns"] >= 0
                    and (r["hold_ws_ns"] is None or r["hold_ws_ns"] >= 0) and not r["drc_errors"]
                    and r["antenna_nets"] == 0)
    return r


def _gls_prep(P: dict, cfg: dict, timeout_s: int) -> dict:
    """Model Verilog của cell từ .lib tt (asic/gls/build/cells.v; cell Yosys không chuyển được, ví dụ ICG dlclkp mô tả
    bằng statetable, nằm trong asic/gls/cells_extra.v của kit) và bản chép netlist 6_final.v."""
    b = os.path.join(P["asic"], "gls", "build")
    os.makedirs(b, exist_ok=True)
    lib = _lib_dir(cfg) / cfg["asic"]["tt_lib"]
    net = os.path.join(P["results"], "6_final.v")
    rc, out, _ = sh(f"yosys -q -p 'read_liberty -ignore_miss_func {lib}; write_verilog -noattr cells.v' && "
                    f"cp {net} 6_final.v", b, timeout=timeout_s)
    ok = rc == 0 and os.path.isfile(os.path.join(b, "cells.v")) and os.path.isfile(os.path.join(b, "6_final.v"))
    return {"ok": ok, "log_tail": out[-2000:]}


@ChiaFunction(resources={"asic": 1})
@timed
def asic_drc(run_dir: str, cfg: dict, timeout_s: int = 3600) -> dict:
    P = paths(run_dir, cfg)
    rc, sec, tail = _make(P, "drc", timeout_s, "drc")
    res = {"rc": rc, "seconds": sec, "log_tail": tail, **_drc_result(P)}
    res["ok"] = rc == 0 and res["drc_klayout"] == 0
    profile_info(step="asic_drc", ok=res["ok"], seconds=sec)
    return res


def _drc_result(P: dict) -> dict:
    """reports/6_drc.lyrdb (XML, mỗi vi phạm một <item>), 6_drc_count.rpt (số vi phạm). Platform không có bộ luật thì
    ORFS ghi "DRC not supported" vào lyrdb."""
    lyrdb = os.path.join(P["reports"], "6_drc.lyrdb")
    db = read(lyrdb)
    if "not supported" in db[:200]:
        return {"drc_klayout": None, "drc_status": "unsupported", "drc_lyrdb": lyrdb,
                "log_tail": "ORFS này không có bộ luật DRC KLayout cho platform (KLAYOUT_DRC_FILE rỗng)"}
    if not db:
        return {"drc_klayout": None, "drc_status": "missing", "drc_lyrdb": None}
    items = db.split("<items>", 1)[1] if "<items>" in db else ""
    rules = {}
    for r in re.findall(r"<category>'?([^<']+)'?</category>", items):
        rules[r] = rules.get(r, 0) + 1
    n = sum(rules.values())
    cnt = read(os.path.join(P["reports"], "6_drc_count.rpt")).strip()
    if cnt.isdigit():
        n = int(cnt)
    return {"drc_klayout": n, "drc_status": "clean" if n == 0 else "violations",
            "drc_rules": dict(sorted(rules.items(), key=lambda kv: -kv[1])[:20]), "drc_lyrdb": lyrdb}


@ChiaFunction(resources={"asic": 1})
@timed
def asic_lvs(run_dir: str, cfg: dict, timeout_s: int = 3600) -> dict:
    """Deck Python in "Congratulations! Netlists match." hoặc "Netlists don't match" và luôn thoát 0, nên phải đọc
    chữ; deck Ruby ghi trạng thái vào 6_lvs.lvsdb. Không có deck thì ORFS ghi "LVS not supported" vào lvsdb."""
    P = paths(run_dir, cfg)
    rc, sec, tail = _make(P, "lvs", timeout_s, "lvs")
    lvsdb = os.path.join(P["results"], "6_lvs.lvsdb")
    db = read(lvsdb)
    log = read(os.path.join(P["logs"], "6_lvs.log")) + read(os.path.join(P["asic"], "make", "lvs.log"))
    dut = cfg["dut"]
    if "not supported" in db[:200]:
        st = "unsupported"
    elif re.search(r"Netlists don'?t match|Netlists do not match", log):
        st = "mismatch"
    elif re.search(r"Netlists match", log):
        st = "match"
    else:
        m = re.search(rf"circuit\({dut} {dut} (\w+)", db)
        st = {"match": "match", "nomatch": "mismatch", "mismatch": "mismatch"}.get(m.group(1), "unknown") if m \
            else "unknown"
    res = {"rc": rc, "seconds": sec, "lvs": st, "lvsdb": lvsdb if os.path.isfile(lvsdb) else None,
           "lvs_log": os.path.join(P["logs"], "6_lvs.log"), "log_tail": tail, "ok": rc == 0 and st == "match"}
    profile_info(step="asic_lvs", ok=res["ok"], seconds=sec)
    return res


@ChiaFunction(resources={"asic": 1})
@timed
def asic_sta(run_dir: str, cfg: dict, corner: list, timeout_s: int = 3600) -> dict:
    """Một corner STA sau route. corner = [tên, .lib hoặc None, derate]."""
    name, lib, derate = corner
    P = paths(run_dir, cfg)
    extra = [f"RUN_SCRIPT={os.path.join(P['asic'], 'signoff.tcl')}", f"RUN_LOG_NAME_STEM=sta_{name}"]
    if lib:
        extra.append(f"LIB_FILES={lib}")
    rc, sec, tail = _make(P, "run", timeout_s, f"sta_{name}", extra,
                          env={"SIGNOFF_CORNER": name, "SIGNOFF_DERATE": str(derate)})
    text = read(os.path.join(P["asic"], "make", f"sta_{name}.log"))
    m = re.search(r"^SIGNOFF corner=\S+ parasitics=(\S+) derate=\S+ libs=\d+ (?:clocks=(?P<clk>\d+) )?"
                  r"setup_ws=(\S+) setup_tns=(\S+) hold_ws=(\S+) hold_tns=(\S+)", text, re.M)
    f = lambda x: None if x in (None, "NA") else float(x)
    c = {"corner": name, "lib": lib or "LIB_FILES của flow", "derate": derate, "rc": rc, "seconds": sec}
    if m:
        c.update({"parasitics": m.group(1), "setup_ws_ns": f(m.group(3)), "setup_tns_ns": f(m.group(4)),
                  "hold_ws_ns": f(m.group(5)), "hold_tns_ns": f(m.group(6))})
        if m.group("clk") is not None:
            c["clocks"] = int(m.group("clk"))
    if c.get("setup_ws_ns") is None:                  # dự phòng: dòng in của report_worst_slack
        ws = re.findall(r"^worst slack (?:max )?(-?[\d.]+)", text, re.M)
        wh = re.findall(r"^worst slack min (-?[\d.]+)", text, re.M)
        c["setup_ws_ns"] = float(ws[0]) if ws else None
        c["hold_ws_ns"] = c.get("hold_ws_ns") or (float(wh[0]) if wh else None)
    c["ok"] = rc == 0 and c.get("setup_ws_ns") is not None and c["setup_ws_ns"] >= 0 and \
        c.get("hold_ws_ns") is not None and c["hold_ws_ns"] >= 0 and c.get("clocks", 1) > 0
    c["log_tail"] = "" if c["ok"] else tail[-1500:]
    profile_info(step=f"asic_sta_{name}", ok=c["ok"], seconds=sec)
    return c


@ChiaFunction(resources={"chipyard": 1})
@timed
def asic_gls(run_dir: str, cfg: dict, plusargs: list[str], timeout_s: int = 1800) -> dict:
    """Mô phỏng gate-level của netlist ASIC sau route bằng Verilator của khối chipyard. Gate: RESULT: PASS."""
    dut = cfg["dut"]
    b = os.path.join(run_dir, "asic", "gls", "build")
    rc, out, sec = sh(f"verilator --binary --timing -Wno-fatal -Wno-lint -Wno-style --top-module tb_{dut} -Mdir obj "
                      f"-o Vgls ../tb_gls_{dut}.v 6_final.v cells.v ../cells_extra.v > build.log 2>&1 && "
                      f"./obj/Vgls {' '.join(plusargs)} | tee sim.log", b, timeout=timeout_s)
    m = RESULT_RE.search(out)
    fields = dict(kv.split("=", 1) for kv in m.group(2).split() if "=" in kv) if m else {}
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec,
           "gls_result": m.group(0) if m else None, "gls_checked": fields.get("checked"),
           "log_tail": out[-2000:] if m else read(os.path.join(b, "build.log"))[-2000:]}
    profile_info(step="asic_gls", ok=res["ok"], seconds=sec)
    return res


def sta_summary(corners: dict) -> dict:
    """Gộp kết quả các task asic_sta thành số liệu của bước (corner xấu nhất)."""
    worst = lambda k: min((c[k] for c in corners.values() if c.get(k) is not None), default=None)
    return {"sta_corners": {k: {kk: vv for kk, vv in v.items() if kk != "log_tail"} for k, v in corners.items()},
            "sta_setup_ws_min_ns": worst("setup_ws_ns"), "sta_hold_ws_min_ns": worst("hold_ws_ns"),
            "sta_real_corners": [k for k, v in corners.items() if v.get("lib") != "LIB_FILES của flow"],
            "sta_ok": bool(corners) and all(c.get("ok") for c in corners.values())}
