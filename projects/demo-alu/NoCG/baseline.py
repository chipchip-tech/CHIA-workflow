"""baseline.py - Pha 1 (baseline, KHÔNG có agent) của demo NoCG, do CHIA điều phối.

Chạy từ thư mục chứa file này (projects/demo-alu/NoCG), cluster theo khối đang bật:
  chia job submit --working-dir . -- python baseline.py [--seed 1] [--cycles 20000] [--en-pct 30]

Các bước (mỗi bước là một task CHIA, chạy trên khối có resource tương ứng):
  1. sim_build  khối chipyard   Verilator 5 của Chipyard build testbench (tb + RTL) thành binary Vtb
  2. sim_run    khối verilator  chạy Vtb; gate: dòng `RESULT: PASS`; ghi waveform VCD
  (FPGA, ASIC: thêm sau; chỉ chạy khi bước trước qua gate)

Mỗi lần chạy có một thư mục riêng trong thư mục làm việc chung (mount vào mọi khối, xem docs/cluster-blocks.md):
  <WORK_DIR>/demo-alu/NoCG/<run_id>/
      rtl/, sim/                bản chép của project (đầu vào cố định của lần chạy)
      sim/build/                build.log, sim.log, wave.vcd, obj/Vtb
      results/baseline.json     số liệu từng bước (đầu vào cho pha 2: agent)
      profile/                  timeline của CHIA: ChiaProfileCollector.log (JSONL) và _profile.html (tự sinh)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.trace.profiler import get_profiler, start_collector, stop_collector

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
    get_profiler().add_info({"step": "sim_run", "ok": res["ok"], "seconds": sec})
    return res


def _sha256(paths: list[Path]) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=os.path.realpath(os.path.expanduser("~/chia_demo")),
                    help="thư mục làm việc chung (WORK_DIR của cluster), mặc định ~/chia_demo")
    ap.add_argument("--run-id", default=time.strftime("baseline-%Y%m%d-%H%M%S"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cycles", type=int, default=20000)
    ap.add_argument("--en-pct", type=int, default=30)
    args = ap.parse_args()

    src = Path(__file__).resolve().parent            # bản chép --working-dir của project
    run_dir = Path(args.work_dir) / "demo-alu" / DEMO / args.run_id
    if run_dir.exists():
        raise SystemExit(f"LỖI: {run_dir} đã có, chọn --run-id khác")
    for sub in ("rtl", "sim"):
        shutil.copytree(src / sub, run_dir / sub, ignore=shutil.ignore_patterns("build"))
    (run_dir / "results").mkdir()

    ray.init(address="auto", ignore_reinit_error=True)
    start_collector(log_dir=str(run_dir / "profile"))

    report = {"demo": DEMO, "dut": DUT, "phase": "baseline", "run_id": args.run_id, "run_dir": str(run_dir),
              "rtl_sha256": _sha256(sorted((run_dir / "rtl").glob("*.v"))),
              "params": {"seed": args.seed, "cycles": args.cycles, "en_pct": args.en_pct},
              "steps": {}, "stopped_at": None}
    print(f"[baseline {DEMO}] run_dir = {run_dir}", flush=True)

    def done(step: str, res: dict) -> bool:
        report["steps"][step] = res
        print(f"[baseline {DEMO}] {step:10s} {'OK  ' if res['ok'] else 'FAIL'} {res['seconds']:7.2f}s"
              f"  @ {res.get('host', '?')}", flush=True)
        if not res["ok"]:
            report["stopped_at"] = step
            print(res.get("log_tail", ""), flush=True)
        return res["ok"]

    # 1-2. Sim: build ở khối chipyard, chạy ở khối verilator. get() rồi mới gọi bước sau (profiler bọc ObjectRef).
    if done("sim_build", get(sim_build.chia_remote(str(run_dir)))):
        plus = [f"+seed={args.seed}", f"+cycles={args.cycles}", f"+en_pct={args.en_pct}"]
        r = get(sim_run.chia_remote(str(run_dir), plus))
        if done("sim_run", r):
            print(f"[baseline {DEMO}] {r['result']}", flush=True)
            print(f"[baseline {DEMO}] waveform: {r['vcd']} ({r['vcd_bytes']} byte)", flush=True)
    # 3. FPGA, 4. ASIC: thêm sau.

    stop_collector()                                 # đợi sự kiện cuối ghi xong vào log
    prof = run_dir / "profile" / "ChiaProfileCollector.log"
    if prof.is_file():                               # timeline HTML cho video/slide (không bắt buộc)
        subprocess.run(["chia", "viz-profile", str(prof), "--format", "html"], capture_output=True)
        report["profile"] = str(prof.with_name("ChiaProfileCollector_profile.html"))

    out = run_dir / "results" / "baseline.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    ok = report["stopped_at"] is None
    print(f"[baseline {DEMO}] số liệu: {out}", flush=True)
    if report.get("profile"):
        print(f"[baseline {DEMO}] timeline: {report['profile']}", flush=True)
    print(f"KẾT QUẢ: {'OK' if ok else 'DỪNG ở ' + report['stopped_at']}", flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
