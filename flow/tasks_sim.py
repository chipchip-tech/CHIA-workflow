"""tasks_sim.py - task CHIA của bước Sim (sim.kind = "verilator").

sim_build  khối chipyard   lint (verilator --lint-only -Wall trên rtl/) + Verilator build sim/run_sim.sh (BUILD_ONLY=1)
sim_run    khối verilator  chạy binary; gate: RESULT: PASS (và không có FCOV_ERROR); số liệu: dòng FCOV của
                           functional coverage, line/toggle/branch của file RTL (build/coverage.dat), VCD
Quy ước của project: sim/run_sim.sh nhận BUILD_ONLY/SKIP_BUILD, testbench in `RESULT: PASS|FAIL k=v ...`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from chia.base.ChiaFunction import ChiaFunction

from flow.common import RESULT_RE, profile_info, read, sh, timed

FCOV_RE = re.compile(r"^FCOV: hit=(\d+) total=(\d+)", re.M)
FCOV_MISS_RE = re.compile(r"^FCOV_MISS: (.*)$", re.M)


@ChiaFunction(resources={"chipyard": 1})
@timed
def sim_build(run_dir: str, cfg: dict) -> dict:
    """Build testbench bằng Verilator của Chipyard (worker chipyard đã nạp env.sh). Gate lint: RTL của DUT (rtl/*.v,
    cố định) phải sạch với -Wall (cảnh báo nào cũng là lỗi)."""
    sim = os.path.join(run_dir, "sim")
    _, ver, _ = sh("verilator --version", sim)
    lint_ok, lint_msgs, lout = True, [], ""
    if cfg["sim"].get("lint", True):
        rtl = " ".join(sorted(str(p) for p in Path(run_dir, "rtl").glob("*.v")))
        lrc, lout, _ = sh(f"verilator --lint-only -Wall --top-module {cfg['dut']} {rtl}", sim)
        lint_msgs = re.findall(r"^%(?:Warning|Error)-?(\w*)", lout, re.M)
        lint_ok = lrc == 0 and not lint_msgs
    rc, out, sec = sh("./run_sim.sh", sim, {"BUILD_ONLY": "1"})
    ok = lint_ok and rc == 0 and os.path.isfile(os.path.join(sim, "build", "obj", "Vtb"))
    profile_info(step="sim_build", ok=ok, seconds=sec)
    tail = out[-2000:] if lint_ok else "LINT (verilator --lint-only -Wall) không sạch:\n" + lout[-2000:]
    return {"ok": ok, "rc": rc, "seconds": sec, "verilator": ver.strip(), "lint_ok": lint_ok,
            "lint_warnings": len(lint_msgs), "lint_ids": sorted(set(lint_msgs)), "log_tail": tail}


def struct_cov(dat: str, dut_files: set[str]) -> dict:
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
@timed
def sim_run(run_dir: str, cfg: dict, plusargs: list[str]) -> dict:
    """Chạy binary đã build. Gate: dòng RESULT là PASS, không có FCOV_ERROR (kiểm tra âm của fcov)."""
    sim = os.path.join(run_dir, "sim")
    rc, out, sec = sh("./run_sim.sh " + " ".join(plusargs), sim, {"SKIP_BUILD": "1"})
    m = RESULT_RE.search(out)
    fields = dict(kv.split("=", 1) for kv in m.group(2).split() if "=" in kv) if m else {}
    vcd = os.path.join(sim, "build", "wave.vcd")
    res = {"ok": rc == 0 and bool(m) and m.group(1) == "PASS", "rc": rc, "seconds": sec,
           "result": m.group(0) if m else None, **fields,
           "vcd": vcd if os.path.isfile(vcd) else None, "vcd_bytes": os.path.getsize(vcd) if os.path.isfile(vcd) else 0,
           "log_tail": out[-2000:]}
    fc, miss = FCOV_RE.search(out), FCOV_MISS_RE.search(out)
    res["fcov_hit"], res["fcov_total"] = (int(fc.group(1)), int(fc.group(2))) if fc else (None, None)
    res["fcov_miss"] = [] if not miss or miss.group(1).strip() == "-" else miss.group(1).strip().split(",")
    dut_files = {p.name for p in Path(run_dir, "rtl").glob("*.v")}
    cov = struct_cov(read(os.path.join(sim, "build", "coverage.dat")), dut_files)
    for kind in ("line", "toggle", "branch"):
        res[f"cov_{kind}"], res[f"cov_{kind}_total"] = cov.get(kind, [None, None])
    if "FCOV_ERROR" in out:
        res["ok"] = False
    profile_info(step="sim_run", ok=res["ok"], seconds=sec)
    return res
