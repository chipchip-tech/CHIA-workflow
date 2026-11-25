"""baseline.py - Pha 1 (baseline, KHÔNG có agent) của demo NoCG, do CHIA điều phối.

Chạy từ thư mục chứa file này (projects/demo-alu/NoCG), cluster theo khối đang bật:
  chia job submit --working-dir . -- python baseline.py [--stages sim,fpga,asic] [--seed 1] [--cycles 20000] [--en-pct 30]
Bước fpga cần khối vivado, bước asic cần khối asic: `python scripts/chia-up-blocks.py --fpga --asic`.

Các bước (mỗi bước là một task CHIA, chạy trên khối có resource tương ứng):
  1. sim_build  khối chipyard   Verilator 5 của Chipyard build testbench (tb + RTL) thành binary Vtb
  2. sim_run    khối verilator  chạy Vtb; gate: dòng `RESULT: PASS`; ghi waveform VCD; số liệu: functional coverage
                                (dòng FCOV của sim/fcov_<DUT>.sv) và line/toggle/branch của DUT (build/coverage.dat)
  3. fpga       khối vivado     Vivado (thiết lập mặc định) build fpga/fpga_top.v cho xc7a100tcsg324-1 tới bitstream;
                                gate: có .bit và WNS >= 0; số liệu: LUT, FF, BUFG, WNS/WHS, công suất, cảnh báo
  4. asic_*     khối asic       ORFS sky130hd theo asic/config.mk, mỗi bước make là một task CHIA:
                                synth → floorplan → place → cts → route → finish (GDS, ảnh layout bằng KLayout)
                                → lvs (`make lvs` của ORFS: KLayout so layout GDS với netlist CDL);
                                gate: có .gds, setup/hold slack >= 0, không lỗi DRC, LVS khớp; số liệu: diện tích,
                                số cell (flop, ICG, buffer clock), slack, công suất
  Mỗi bước chỉ chạy khi bước trước qua gate.

Mỗi lần chạy có một thư mục riêng trong thư mục làm việc chung (mount vào mọi khối, xem docs/cluster-blocks.md):
  <WORK_DIR>/demo-alu/NoCG/<run_id>/
      rtl/, sim/, fpga/, asic/  bản chép của project (đầu vào cố định của lần chạy)
      sim/build/                build.log, sim.log, wave.vcd, obj/Vtb
      fpga/out/, fpga/reports/  fpga_top.bit, post_route.dcp, vivado.log; util, timing, power, methodology, drc
      asic/orfs/                WORK_HOME của ORFS: logs/, reports/, results/ (6_final.gds, 6_lvs.lvsdb), objects/
      asic/layout.png           ảnh layout
      results/baseline.json     số liệu từng bước (đầu vào cho pha 2: agent)
      status.html               trang trạng thái (tự làm mới, mở bằng trình duyệt; xem status_page.py)
      profile/                  timeline của CHIA: ChiaProfileCollector.log (JSONL) và _profile.html (tự sinh)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.trace.profiler import get_profiler, start_collector, stop_collector

import status_page as S

DEMO = "NoCG"
DUT = "NoCG_alu"
RESULT_RE = re.compile(r"^RESULT: (PASS|FAIL) (.*)$", re.M)


def _sh(cmd: str, cwd: str, env: dict | None = None, timeout: int = 1800) -> tuple[int, str, float]:
    t0 = time.time()
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, p.stdout + p.stderr, round(time.time() - t0, 2)


@ChiaFunction(resources={"chipyard": 1})
def sim_build(run_dir: str) -> dict:
    """Build testbench bằng Verilator của Chipyard (worker chipyard đã nạp env.sh)."""
    sim = os.path.join(run_dir, "sim")
    _, ver, _ = _sh("verilator --version", sim)
    rc, out, sec = _sh("./run_sim.sh", sim, {"BUILD_ONLY": "1"})
    binary = os.path.join(sim, "build", "obj", "Vtb")
    ok = rc == 0 and os.path.isfile(binary)
    get_profiler().add_info({"step": "sim_build", "ok": ok, "seconds": sec})
    return {"ok": ok, "rc": rc, "seconds": sec, "verilator": ver.strip(), "host": os.uname().nodename,
            "log_tail": out[-2000:]}


def _num(pattern: str, text: str, cast=float):
    m = re.search(pattern, text, re.M)
    return cast(m.group(1)) if m else None


def _read(path: str) -> str:
    try:
        return open(path, errors="replace").read()
    except OSError:
        return ""


def _timing(text: str) -> dict:
    """Dòng số ngay dưới tiêu đề WNS(ns) của Design Timing Summary (như examples/fpga_smoke.py)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("WNS(ns)") and i + 2 < len(lines):
            v = lines[i + 2].split()
            try:
                return {"wns_ns": float(v[0]), "tns_ns": float(v[1]), "whs_ns": float(v[4]),
                        "ths_ns": float(v[5]), "hold_failing_endpoints": int(float(v[6]))}
            except (IndexError, ValueError):
                break
    return {}


@ChiaFunction(resources={"vivado": 1})
def fpga_build(run_dir: str, timeout_s: int = 3600) -> dict:
    """Vivado batch theo fpga/build.tcl (worker vivado đã nạp settings64.sh). Gate: có .bit và WNS >= 0."""
    fpga = os.path.join(run_dir, "fpga")
    os.makedirs(os.path.join(fpga, "out"), exist_ok=True)
    _, ver, _ = _sh("vivado -version | head -1", fpga)
    rc, out, sec = _sh("vivado -mode batch -nojournal -log out/vivado.log -source build.tcl", fpga, timeout=timeout_s)
    rep = lambda name: _read(os.path.join(fpga, "reports", name))
    util, power, log = rep("util.rpt"), rep("power.rpt"), _read(os.path.join(fpga, "out", "vivado.log"))
    bit = os.path.join(fpga, "out", "fpga_top.bit")
    res = {
        "vivado": ver.strip(), "rc": rc, "seconds": sec, "host": os.uname().nodename,
        "bitstream": bit if os.path.isfile(bit) else None,
        **_timing(rep("timing.rpt")),
        "lut": _num(r"^\|\s*Slice LUTs\*?\s*\|\s*(\d+)", util, int),
        "ff": _num(r"^\|\s*Slice Registers\s*\|\s*(\d+)", util, int),
        "bufg": _num(r"^\|\s*BUFGCTRL\s*\|\s*(\d+)", util, int),
        "power_total_w": _num(r"^\|\s*Total On-Chip Power \(W\)\s*\|\s*([\d.]+)", power),
        "power_dynamic_w": _num(r"^\|\s*Dynamic \(W\)\s*\|\s*([\d.]+)", power),
        "critical_warnings": len(re.findall(r"^CRITICAL WARNING:", log, re.M)),
        "warnings": len(re.findall(r"^WARNING:", log, re.M)),
        # Các mã cảnh báo duy nhất (ví dụ PDRC-153 gated clock): đầu vào cho agent ở pha 2.
        "warning_ids": sorted(set(re.findall(r"^(?:CRITICAL )?WARNING: \[([^\]]+)\]", log, re.M))),
        "reports": os.path.join(fpga, "reports"),
    }
    res["ok"] = rc == 0 and res["bitstream"] is not None and res.get("wns_ns") is not None and res["wns_ns"] >= 0
    res["log_tail"] = out[-3000:]
    get_profiler().add_info({"step": "fpga", "ok": res["ok"], "seconds": sec, "wns_ns": res.get("wns_ns"),
                             "lut": res["lut"], "ff": res["ff"]})
    return res


# ---------------- ASIC (ORFS) ----------------
ORFS_FLOW = "/OpenROAD-flow-scripts/flow"            # trong khối asic (ORFS của host mount vào đây)
ASIC_STAGES = ["synth", "floorplan", "place", "cts", "route", "finish", "lvs"]


def _orfs_paths(run_dir: str) -> dict:
    asic = os.path.join(run_dir, "asic")
    work = os.path.join(asic, "orfs")
    sub = os.path.join("sky130hd", DUT, "base")
    return {"asic": asic, "work": work, "config": os.path.join(asic, "config.mk"),
            **{k: os.path.join(work, k, sub) for k in ("logs", "reports", "results")}}


def _run_logged(cmd: list[str], cwd: str, timeout: int, log_path: str) -> tuple[int, float, str]:
    """Chạy trong process group riêng, log ra file; quá giờ thì giết cả nhóm (make, yosys, openroad)."""
    t0 = time.time()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            rc = -1
    return rc, round(time.time() - t0, 2), _read(log_path)[-3000:]


@ChiaFunction(resources={"asic": 1})
def asic_stage(run_dir: str, stage: str, timeout_s: int = 3600) -> dict:
    """Một bước `make <stage>` của ORFS (worker asic đã nạp env.sh của ORFS). WORK_HOME trỏ vào thư mục run, nên
    ORFS trên host không bị ghi. Bước finish còn đọc số liệu và vẽ ảnh layout."""
    P = _orfs_paths(run_dir)
    cmd = ["make", "-C", ORFS_FLOW, f"DESIGN_CONFIG={P['config']}", f"WORK_HOME={P['work']}", stage]
    rc, sec, tail = _run_logged(cmd, ORFS_FLOW, timeout_s, os.path.join(P["asic"], "make", f"{stage}.log"))
    res = {"stage": stage, "rc": rc, "seconds": sec, "host": os.uname().nodename, "ok": rc == 0, "log_tail": tail}
    if stage == "synth":
        _, ver, _ = _sh("yosys -V; openroad -version 2>/dev/null | head -1", ORFS_FLOW)
        res["tools"] = ver.strip()
        if rc == 0 and not os.path.isdir(P["results"]):
            res["ok"] = False
            res["log_tail"] = (f"make synth xong nhưng không có {P['results']}: ORFS này không nhận WORK_HOME? "
                               f"xem {ORFS_FLOW}/results/sky130hd/{DUT}\n") + tail
    if stage == "finish" and rc == 0:
        res.update(_asic_metrics(P))
        res["ok"] = res["gate_ok"]
    if stage == "lvs":
        res.update(_lvs_result(P, tail))
        res["ok"] = rc == 0 and res["lvs"] == "match"
    get_profiler().add_info({"step": f"asic_{stage}", "ok": res["ok"], "seconds": sec})
    return res


def _asic_metrics(P: dict) -> dict:
    """Số liệu sau finish: metrics JSON của ORFS (logs/*.json), báo cáo 6_finish.rpt, netlist 6_final.v."""
    metrics = {}
    for f in sorted(Path(P["logs"]).glob("*.json")):
        try:
            metrics.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            pass
    fin = _read(os.path.join(P["reports"], "6_finish.rpt"))

    def m(keys, pattern=None):
        for k in keys:
            if isinstance(metrics.get(k), (int, float)):
                return metrics[k]
        return _num(pattern, fin) if pattern else None

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
        "clock_period_ns": m(["finish__timing__clock__period"]),
    }
    if isinstance(r["util_pct"], float) and r["util_pct"] <= 1.0 and "finish__design__instance__utilization" in metrics:
        r["util_pct"] = round(100 * r["util_pct"], 2)           # metrics JSON ghi tỉ lệ 0..1
    # Đếm cell theo loại trong netlist cuối (không tính cell vật lý: tap, decap, fill).
    cells = {}
    for t in re.findall(r"^\s*sky130_fd_sc_hd__(\w+?)_\d+\s+\S+\s*\(", _read(os.path.join(P["results"], "6_final.v")), re.M):
        cells[t] = cells.get(t, 0) + 1
    phys = ("tapvpwrvgnd", "decap", "fill", "tap")
    logic = {k: v for k, v in cells.items() if not k.startswith(phys)}
    r["cells"] = dict(sorted(logic.items(), key=lambda kv: -kv[1]))
    r["n_flop"] = sum(v for k, v in logic.items() if re.match(r"(s?df|edf|sedf|dlx|dlr)", k))
    r["n_icg"] = sum(v for k, v in logic.items() if "dlclkp" in k)
    r["n_clkbuf"] = sum(v for k, v in logic.items() if k.startswith(("clkbuf", "clkinv", "clkdlybuf")))
    r["n_logic_cells"] = sum(logic.values())
    # Thời gian từng bước con của ORFS (dòng "Elapsed time" ở cuối mỗi log).
    steps = {}
    for f in sorted(Path(P["logs"]).glob("*.log")):
        mm = re.search(r"Elapsed time: (?:(\d+):)?(\d+):([\d.]+)", _read(str(f)))
        if mm:
            steps[f.stem] = round(int(mm.group(1) or 0) * 3600 + int(mm.group(2)) * 60 + float(mm.group(3)), 1)
    r["orfs_step_seconds"] = steps
    # Ảnh layout cho slide/video (không thuộc gate).
    png = os.path.join(P["asic"], "layout.png")
    if r["gds"]:
        lyp = next(iter(sorted(Path(ORFS_FLOW, "platforms", "sky130hd").glob("*.lyp"))), None)
        cmd = f"klayout -zz -r render_gds.py -rd gds={r['gds']} -rd png={png}" + (f" -rd lyp={lyp}" if lyp else "")
        rc, out, _ = _sh(cmd, P["asic"], timeout=600)
        r["layout_png"] = png if os.path.isfile(png) else None
        if not r["layout_png"]:
            r["layout_png_error"] = out[-1000:]
    r["reports"] = P["reports"]
    r["metrics_json"] = {k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}
    r["gate_ok"] = (r["gds"] is not None and r["setup_ws_ns"] is not None and r["setup_ws_ns"] >= 0
                    and (r["hold_ws_ns"] is None or r["hold_ws_ns"] >= 0) and not r["drc_errors"])
    return r


def _lvs_result(P: dict, make_tail: str) -> dict:
    """Kết quả `make lvs` của ORFS. Deck Python (klayout_lvs.py) in "Congratulations! Netlists match." hoặc
    "Netlists don't match" và luôn thoát 0, nên phải đọc chữ; deck Ruby cũ ghi trạng thái vào 6_lvs.lvsdb. Platform
    không có deck thì ORFS ghi "LVS not supported on this platform" vào lvsdb (không phải lỗi make)."""
    lvsdb = os.path.join(P["results"], "6_lvs.lvsdb")
    db = _read(lvsdb)
    log = _read(os.path.join(P["logs"], "6_lvs.log")) + _read(os.path.join(P["asic"], "make", "lvs.log"))
    if "not supported" in db[:200]:
        st = "unsupported"
    elif re.search(r"Netlists don'?t match|Netlists do not match", log):
        st = "mismatch"
    elif re.search(r"Netlists match", log):
        st = "match"
    else:
        # lvsdb (định dạng chữ của KLayout): dòng xref của mạch top, ví dụ " circuit(NoCG_alu NoCG_alu match".
        m = re.search(rf"circuit\({DUT} {DUT} (\w+)", db)
        st = {"match": "match", "nomatch": "mismatch", "mismatch": "mismatch"}.get(m.group(1), "unknown") if m \
            else "unknown"
    r = {"lvs": st, "lvsdb": lvsdb if os.path.isfile(lvsdb) else None,
         "lvs_log": os.path.join(P["logs"], "6_lvs.log")}
    if st == "unsupported":
        r["log_tail"] = ("ORFS này không có deck LVS cho sky130hd (KLAYOUT_LVS_FILE rỗng): xem "
                         f"{ORFS_FLOW}/platforms/sky130hd/config.mk\n") + make_tail
    return r


FCOV_RE = re.compile(r"^FCOV: hit=(\d+) total=(\d+)", re.M)
FCOV_MISS_RE = re.compile(r"^FCOV_MISS: (.*)$", re.M)


def _struct_cov(dat: str, dut_files: set[str]) -> dict:
    """coverage.dat của Verilator: mỗi điểm là `C '<khoá>' <số lần>`, khoá gồm các cặp \\x01<tên>\\x02<giá trị>
    (f: file, page: v_line/..., h: hierarchy). Chỉ đếm điểm trong file RTL của DUT. Trả {loại: [đã gặp, tổng]}."""
    out: dict[str, list[int]] = {}
    for key, n in re.findall(r"^C '(.*)' (\d+)$", dat, re.M):
        f = dict(re.findall(r"\x01([^\x02]+)\x02([^\x01]*)", key))
        if os.path.basename(f.get("f", "")) not in dut_files:
            continue
        kind = f.get("page", "?").split("/")[0].removeprefix("v_")
        c = out.setdefault(kind, [0, 0])
        c[0] += int(n) > 0
        c[1] += 1
    return out


@ChiaFunction(resources={"verilator_run": 1})
def sim_run(run_dir: str, plusargs: list[str]) -> dict:
    """Chạy binary đã build. Gate: dòng RESULT là PASS."""
    sim = os.path.join(run_dir, "sim")
    rc, out, sec = _sh("./run_sim.sh " + " ".join(plusargs), sim, {"SKIP_BUILD": "1"})
    m = RESULT_RE.search(out)
    fields = dict(kv.split("=", 1) for kv in m.group(2).split()) if m else {}
    vcd = os.path.join(sim, "build", "wave.vcd")
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec,
           "result": m.group(0) if m else None, **fields,
           "vcd": vcd if os.path.isfile(vcd) else None,
           "vcd_bytes": os.path.getsize(vcd) if os.path.isfile(vcd) else 0,
           "host": os.uname().nodename, "log_tail": out[-2000:]}
    fc, miss = FCOV_RE.search(out), FCOV_MISS_RE.search(out)
    res["fcov_hit"], res["fcov_total"] = (int(fc.group(1)), int(fc.group(2))) if fc else (None, None)
    res["fcov_miss"] = [] if not miss or miss.group(1).strip() == "-" else miss.group(1).strip().split(",")
    dut_files = {p.name for p in Path(run_dir, "rtl").glob("*.v")}
    cov = _struct_cov(_read(os.path.join(sim, "build", "coverage.dat")), dut_files)
    for kind in ("line", "toggle", "branch"):
        res[f"cov_{kind}"] = cov.get(kind, [None, None])[0]
        res[f"cov_{kind}_total"] = cov.get(kind, [None, None])[1]
    if "FCOV_ERROR" in out:                      # kiểm tra âm của fcov (thanh ghi vào đổi khi rảnh) cũng là lỗi
        res["ok"] = False
    get_profiler().add_info({"step": "sim_run", "ok": res["ok"], "seconds": sec})
    return res


def _sha256(paths: list[Path]) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")),
                    help="thư mục làm việc chung (WORK_DIR của cluster), mặc định ~/chia_demo")
    ap.add_argument("--run-id", default=time.strftime("baseline-%Y%m%d-%H%M%S"))
    ap.add_argument("--stages", default="sim,fpga,asic", help="các bước cần chạy, theo thứ tự (mặc định sim,fpga,asic)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cycles", type=int, default=20000)
    ap.add_argument("--en-pct", type=int, default=30)
    args = ap.parse_args()

    src = Path(__file__).resolve().parent            # bản chép --working-dir của project
    run_dir = Path(args.work_dir) / "demo-alu" / DEMO / args.run_id
    if run_dir.exists():
        raise SystemExit(f"LỖI: {run_dir} đã có, chọn --run-id khác")
    stages = [x.strip() for x in args.stages.split(",") if x.strip()]
    for sub in ("rtl", "sim", "fpga", "asic"):
        shutil.copytree(src / sub, run_dir / sub, ignore=shutil.ignore_patterns("build"))
    (run_dir / "results").mkdir()

    ray.init(address="auto", ignore_reinit_error=True)
    for st, res_name, flag in (("fpga", "vivado", "--fpga"), ("asic", "asic", "--asic")):
        if st in stages and not ray.cluster_resources().get(res_name):
            raise SystemExit(f"LỖI: cluster không có khối {res_name}: bật bằng `python scripts/chia-up-blocks.py "
                             f"--fpga --asic`, hoặc bỏ {st} khỏi --stages")
    start_collector(log_dir=str(run_dir / "profile"))

    report = {"demo": DEMO, "dut": DUT, "phase": "baseline", "run_id": args.run_id, "run_dir": str(run_dir),
              "rtl_sha256": _sha256(sorted((run_dir / "rtl").glob("*.v"))),
              "params": {"seed": args.seed, "cycles": args.cycles, "en_pct": args.en_pct, "stages": stages},
              "steps": {}, "stopped_at": None, "t0": time.time()}
    print(f"[baseline {DEMO}] run_dir = {run_dir}", flush=True)
    S.write(report)
    print(f"[baseline {DEMO}] trạng thái: {run_dir / 'status.html'}", flush=True)

    def done(step: str, res: dict) -> bool:
        report["steps"][step] = res
        report["live"] = None
        S.write(report)
        print(f"[baseline {DEMO}] {step:15s} {'OK  ' if res['ok'] else 'FAIL'} {res['seconds']:7.2f}s"
              f"  @ {res.get('host', '?')}", flush=True)
        if not res["ok"]:
            report["stopped_at"] = step
            print(res.get("log_tail", ""), flush=True)
        return res["ok"]

    # Mỗi bước chỉ chạy khi bước trước qua gate. get() rồi mới gọi bước sau (profiler bọc ObjectRef).
    ok = True
    if "sim" in stages:
        # 1-2. Sim: build ở khối chipyard, chạy ở khối verilator.
        S.set_live(report, task="sim_build")
        ok = done("sim_build", get(sim_build.chia_remote(str(run_dir))))
        if ok:
            plus = [f"+seed={args.seed}", f"+cycles={args.cycles}", f"+en_pct={args.en_pct}"]
            S.set_live(report, task="sim_run")
            r = get(sim_run.chia_remote(str(run_dir), plus))
            ok = done("sim_run", r)
            if ok:
                print(f"[baseline {DEMO}] {r['result']}", flush=True)
                print(f"[baseline {DEMO}] coverage: functional {r['fcov_hit']}/{r['fcov_total']} bin, "
                      f"line {r['cov_line']}/{r['cov_line_total']}, toggle {r['cov_toggle']}/{r['cov_toggle_total']}, "
                      f"branch {r['cov_branch']}/{r['cov_branch_total']} (DUT); thiếu: {', '.join(r['fcov_miss']) or '-'}",
                      flush=True)
                print(f"[baseline {DEMO}] waveform: {r['vcd']} ({r['vcd_bytes']} byte)", flush=True)
    if ok and "fpga" in stages:
        # 3. FPGA: Vivado ở khối vivado.
        S.set_live(report, task="fpga")
        r = get(fpga_build.chia_remote(str(run_dir)))
        ok = done("fpga", r)
        print(f"[baseline {DEMO}] fpga: LUT={r['lut']} FF={r['ff']} BUFG={r['bufg']} WNS={r.get('wns_ns')} "
              f"WHS={r.get('whs_ns')} ns  P={r['power_total_w']} W (động {r['power_dynamic_w']} W)  "
              f"cảnh báo={r['warnings']} (critical {r['critical_warnings']})", flush=True)
        print(f"[baseline {DEMO}] bitstream: {r['bitstream']}", flush=True)
    if ok and "asic" in stages:
        # 4. ASIC: ORFS ở khối asic, từng bước make là một task (timeline thấy rõ từng bước).
        for stage in ASIC_STAGES:
            S.set_live(report, task=f"asic_{stage}")
            r = get(asic_stage.chia_remote(str(run_dir), stage))
            ok = done(f"asic_{stage}", r)
            if not ok:
                break
        if "asic_lvs" in report["steps"]:
            print(f"[baseline {DEMO}] lvs: {report['steps']['asic_lvs']['lvs']}", flush=True)
        r = report["steps"].get("asic_finish", {})
        if "gds" in r:
            print(f"[baseline {DEMO}] asic: diện tích {r['area_um2']} um2 (util {r['util_pct']} %), "
                  f"{r['n_logic_cells']} cell (flop {r['n_flop']}, ICG {r['n_icg']}, buffer clock {r['n_clkbuf']}), "
                  f"setup WS={r['setup_ws_ns']} hold WS={r['hold_ws_ns']} ns, P={r['power_total_w']} W, "
                  f"DRC={r['drc_errors']}", flush=True)
            print(f"[baseline {DEMO}] gds: {r['gds']}", flush=True)
            print(f"[baseline {DEMO}] layout: {r.get('layout_png')}", flush=True)

    stop_collector()                                 # đợi sự kiện cuối ghi xong vào log
    prof = run_dir / "profile" / "ChiaProfileCollector.log"
    if prof.is_file():                               # timeline HTML cho video/slide (không bắt buộc)
        subprocess.run(["chia", "viz-profile", str(prof), "--format", "html"], capture_output=True)
        report["profile"] = str(prof.with_name("ChiaProfileCollector_profile.html"))

    out = run_dir / "results" / "baseline.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    S.write(report, final=True)
    ok = report["stopped_at"] is None
    print(f"[baseline {DEMO}] số liệu: {out}", flush=True)
    if report.get("profile"):
        print(f"[baseline {DEMO}] timeline: {report['profile']}", flush=True)
    print(f"KẾT QUẢ: {'OK' if ok else 'DỪNG ở ' + report['stopped_at']}", flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
