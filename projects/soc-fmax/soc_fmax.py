"""soc-fmax: Fmax của cả SoC chipyard.MinimalSoCConfig (DigitalTop) trên sky130hd, đo bằng ORFS sau place.

Mốc M1 (chưa có agent): baseline đo thật, từng bước, mỗi bước có cache:
  rtl        build Verilator MinimalSoCConfig (ChiselBuildNode trên Chipyard ở host), tách cây DigitalTop,
             danh sách macro SRAM (mems.conf), cây instance cấp 1-2 và vùng gán cho từng instance
  gate       riscv-tests rv64ui/um/ua/uf/ud-p-* + 8 benchmark trên Verilator (fan-out trên verilator_run);
             test ISA hỏng sẵn ở baseline được ghi ra (known_fail) và loại khỏi gate
  func       (M2) vết commit của dhrystone/qsort/mm/spmv (func_check.py), lưu làm chuẩn; tự so baseline với
             chính nó ở seed khác để kiểm func_check không báo nhầm
  synth      sinh fakeram + thư mục design ORFS, `make synth` (Yosys phân cấp, ABC ở chu kỳ mục tiêu) +
             OpenSTA với Liberty fakeram: bộ lọc rẻ
  floorplan  `make floorplan` (đặt macro, PDN): kiểm LEF/PDN của fakeram trước khi tốn giờ place
  place      `make place` (global place + repair_design + detailed place), RC ước lượng sau place:
             Fmax = 1000 / (chu kỳ - slack xấu nhất không kể I/O), slack theo vùng và theo họ endpoint
--until <bước> dừng sau bước đó. Mỗi bước in thời gian và RAM đỉnh (docker stats + log của ORFS).
--noise (M2): sau place, đo nhiễu bằng 4 lần chạy baseline nhiễu nhẹ (chạy lại y hệt, PLACE_DENSITY ±0,01,
CORE_UTILIZATION +1), ghi noise.json (ngưỡng nhận của policy.py).

Chạy từ thư mục này trên host (README.md):
  chia job submit --no-wait --submission-id m1-base --working-dir . -- python soc_fmax.py --run-name m1-base
Kết quả ở --out-dir/<run-name>/ (mặc định ~/soc-fmax-runs): results.csv, summary.md, memory.json, runs/baseline/.
Cần cluster: python scripts/chia-up-blocks.py --asic --verilator-slots 4 (ASIC_MEM=20g).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ray

import func_check as F
import orfs_nodes as O
import policy as P
import soc_nodes as S
from memwatch import MemWatch
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
from chia.trace.profiler import start_collector, stop_collector

HERE = Path(__file__).resolve().parent
EXP_YAML = HERE / "exp.yaml"
STEPS = ("rtl", "gate", "func", "synth", "floorplan", "place")
ORFS_STEPS = ("synth", "floorplan", "place")
CACHED_FNS = ("ChiselBuildNode.build", "extract_soc_rtl", "run_gate", "run_func", "orfs_stage")
REGION_COLS = ("tile", "L2", "bus", "uncore", "I/O")
CSV_FIELDS = ["time", "run", "point", "stage", "period_ns", "fmax_mhz", "internal_slack_ns", "worst_slack_ns",
              "tns_ns", *[f"{r}_slack_ns" for r in REGION_COLS], "family_score", "std_area_um2", "macro_area_um2",
              "core_area_um2", "n_std", "crit_start", "crit_end", "minutes", "peak_gb", "cached", "variant", "ok", "error"]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(ap)
    ap.add_argument("--until", choices=STEPS, default="place", help="dừng sau bước này (mặc định place)")
    ap.add_argument("--noise", action="store_true", help="sau place: đo nhiễu (4 lần chạy baseline nhiễu nhẹ), ghi noise.json")
    a = ap.parse_args()
    finish_args(a, ap)
    return a


def add_common_args(ap):
    """Tham số chung của soc_fmax.py và loop.py."""
    home = Path.home()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--out-dir", default=str(home / "soc-fmax-runs"))
    ap.add_argument("--period-ns", type=float, default=10.0, help="chu kỳ ràng buộc (bậc hiện tại)")
    ap.add_argument("--chipyard", default=os.environ.get("CHIPYARD_FORK", str(home / "tools" / "chipyard")))
    ap.add_argument("--orfs-dir", default=os.environ.get("ORFS_DIR", str(home / "tools" / "OpenROAD-flow-scripts")))
    ap.add_argument("--repo", default=str(home / "CHIA-workflow"), help="repo CHIA-workflow (lấy scripts/fakeram_sky130.py)")
    ap.add_argument("--config", default=S.CONFIG)
    ap.add_argument("--make-jobs", type=int, default=16)
    ap.add_argument("--build-timeout", type=int, default=3 * 3600)
    ap.add_argument("--clean-build", action="store_true", help="make clean trước khi build (xoá cả chipyard.jar)")
    ap.add_argument("--allow-dirty", action="store_true", help="cho build baseline khi cây Chipyard có thay đổi")
    ap.add_argument("--repeat-seed", type=int, default=7, help="chạy benchmark lần hai với seed này (kiểm tất định)")
    ap.add_argument("--synth-timeout", type=int, default=3 * 3600)
    ap.add_argument("--floorplan-timeout", type=int, default=2 * 3600)
    ap.add_argument("--place-timeout", type=int, default=3 * 3600)
    ap.add_argument("--fakeram-access-ns", type=float, default=1.5)
    ap.add_argument("--fakeram-um2-per-bit", type=float, default=3.0)
    ap.add_argument("--orfs-var", action="append", default=[], metavar="KEY=VAL",
                    help="ghi đè biến config.mk (vd PLACE_DENSITY=0.5, CORE_UTILIZATION=35)")
    ap.add_argument("--mem-limit-gb", type=float, default=20.0, help="cảnh báo khi RAM đỉnh vượt mức này")
    ap.add_argument("--cache-dir", default=str(home / "chia_cache" / "soc-fmax"))
    ap.add_argument("--cache-gb", type=float, default=8.0)
    ap.add_argument("--keep-orfs", action="store_true",
                    help="không dọn: giữ kết quả ORFS của mọi variant job đã dùng (mặc định chỉ giữ bản tốt nhất)")


def finish_args(a, ap):
    """Phần chung với loop.py: chuẩn hoá đường dẫn, biến ORFS."""
    a.chipyard = os.path.realpath(os.path.expanduser(a.chipyard))
    a.orfs_dir = os.path.realpath(os.path.expanduser(a.orfs_dir))
    a.repo = os.path.realpath(os.path.expanduser(a.repo))
    a.orfs_vars = dict(O.ORFS_DEFAULTS)
    for kv in a.orfs_var:
        k, _, v = kv.partition("=")
        if not k or not _:
            ap.error(f"--orfs-var cần KEY=VAL: {kv}")
        a.orfs_vars[k] = v


# ---------------------------------------------------------------------------

def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def as_json(obj) -> str:
    return json.dumps(dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj, indent=2, default=str)


def fmt(x, nd=3):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def append_csv(path: Path, row: dict):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: fmt(v) if isinstance(v, float) else v for k, v in row.items()})


def cache_has(tag: str) -> bool:
    from chia.base.cache import get_active_cache
    return get(get_active_cache().has.chia_remote(tag))


def cache_evict(tag: str):
    """Kết quả hỏng không giữ trong cache (cache tự ghi mọi kết quả có _chia_tag khi get())."""
    from chia.base.cache import get_active_cache
    try:
        get(get_active_cache().evict.chia_remote(tag))
    except Exception as e:      # noqa: BLE001
        print(f"  (không xoá được cache {tag}: {e})", flush=True)


def load_fakeram(repo: str):
    path = Path(repo) / "scripts" / "fakeram_sky130.py"
    if not path.is_file():
        raise SystemExit(f"không thấy {path}: đặt --repo tới repo CHIA-workflow (nhánh đã merge setup)")
    spec = importlib.util.spec_from_file_location("fakeram_sky130", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fakeram_sky130"] = mod
    spec.loader.exec_module(mod)
    return mod


def build_errors(art) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", (art.stdout or "") + "\n" + (art.stderr or ""))
    lines, frames = [], 0
    for l in text.splitlines():
        if re.search(r"\[error\]|error:|%Error|Exception|FAILED", l):
            lines.append(l)
            frames = 12
        elif frames and re.search(r"\bat (freechips|chipyard|testchipip|sifive)\.\S*\(\w+\.scala:\d+\)", l):
            lines.append(l)
            frames -= 1
    return "\n".join(lines[:120]) if lines else "\n".join(text.splitlines()[-60:])


class Timer:
    """Thời gian của từng bước (kể cả khi lấy từ cache) cho bảng cuối."""

    def __init__(self):
        self.rows = []

    def add(self, step: str, minutes: float, cached: bool, note: str = ""):
        self.rows.append({"step": step, "minutes": round(minutes, 2), "cached": cached, "note": note})


# ---------------------------------------------------------------------------
# Các bước
# ---------------------------------------------------------------------------

def step_rtl(a, d: Path, mw: MemWatch, tm: Timer, baseline: bool = True) -> tuple:
    """Build + tách DigitalTop. baseline=False (vòng của loop.py): cây được phép có thay đổi (patch của agent),
    build lỗi thì trả art (success=False) thay vì dừng."""
    state = get(S.tree_state.chia_remote(a.chipyard))
    write(d / "tree_state.json", json.dumps(state, indent=2))
    dirty = S.dirty_paths(state)
    print(f"Chipyard {a.chipyard}: HEAD {state['root_head'][:12]}, {len(state['subs'])} submodule, "
          f"digest {state['digest'][:12]}", flush=True)
    if state.get("ignored_dirty"):
        print("  bỏ qua (không ảnh hưởng build Verilator): " + ", ".join(state["ignored_dirty"]), flush=True)
    if dirty:
        print("  cây có thay đổi:\n    " + "\n    ".join(dirty[:40]), flush=True)
        if baseline and not a.allow_dirty:
            raise SystemExit("Baseline cần cây Chipyard sạch (trừ MinimalSoCConfig.scala). Xem lại hoặc --allow-dirty.")
    tag = f"build_{state['digest'][:16]}_{a.config}"
    hit = cache_has(tag)
    print(f"build: tag {tag} ({'cache HIT' if hit else 'build thật'})", flush=True)
    t0 = time.time()
    with mw.step("build"):
        cb = ChiselBuildNode(a.chipyard, a.config, target=BuildTarget.VERILATOR, make_jobs=a.make_jobs,
                             timeout_seconds=a.build_timeout, collect_generated_src=True, clean=a.clean_build,
                             name=f"soc-fmax-{a.config}")
        art = get(cb.build.chia_remote(cb, _chia_tag=tag))
    tm.add("build", (time.time() - t0) / 60, hit)
    if not art.success:
        cache_evict(tag)
        write(d / "build.log", f"returncode={art.returncode}\n[stdout]\n{(art.stdout or '')[-300000:]}\n"
                               f"[stderr]\n{(art.stderr or '')[-100000:]}")
        if not baseline:
            return state, art, None
        raise SystemExit(f"build lỗi (rc={art.returncode}), xem {d}/build.log:\n{build_errors(art)}")
    print(f"build ok: {len(art.generated_src_files)} file sinh ra, simulator "
          f"{len(art.simulator_binary_content) / 1e6:.1f} MB ({(time.time() - t0) / 60:.1f} phút)", flush=True)
    t0 = time.time()
    rtag = f"rtl_{state['digest'][:16]}_{a.config}_x1"
    rhit = cache_has(rtag)
    rtl = get(S.extract_soc_rtl.chia_remote(art, S.TOP, _chia_tag=rtag))
    tm.add("tách RTL", (time.time() - t0) / 60, rhit)
    tree = S.instance_tree(rtl, 2)
    write(d / "instances.txt", "\n".join(f"{p:70s} {m:45s} {S.region_of(p + '/x')}" for p, m in tree) + "\n")
    write(d / "mems.conf", rtl.mems_conf + "\n")
    write(d / "rtl_info.json", json.dumps({"top": rtl.top, "hash": rtl.hash, "n_files": rtl.n_files,
                                           "n_modules": len(rtl.modules), "srams": rtl.srams,
                                           "replaced": rtl.replaced, "children": rtl.children}, indent=2))
    write(d / "DigitalTop.sv", rtl.verilog())
    print(f"DigitalTop: {len(rtl.modules)} module ({rtl.n_files} file), RTL hash {rtl.hash[:12]}, "
          f"thay thế {rtl.replaced or '-'}", flush=True)
    print("instance cấp 1 (vùng gán):", flush=True)
    for inst, mod in sorted(rtl.children.items()):
        print(f"  {inst:55s} {mod:40s} {S.region_of(inst + '/x')}", flush=True)
    inst = S.sram_instances(rtl)
    print(f"macro SRAM ({len(rtl.srams)} module, {sum(inst.values())} instance):", flush=True)
    for line in rtl.mems_conf.splitlines():
        p = line.split()
        bits = int(p[3]) * int(p[5])
        n = inst.get(p[1], 0)
        print(f"  x{n:<2d} {line.strip()}   ({n * bits / 8192:.1f} KiB)", flush=True)
    write(d / "sram_instances.json", json.dumps(inst, indent=2))
    return state, art, rtl


def step_gate(a, d: Path, art, rtl, mw: MemWatch, tm: Timer, baseline: bool = True, elfs: dict | None = None) -> dict:
    """baseline=False: không chạy lại seed thứ hai, không dừng khi hỏng (loop.py chấm bằng policy.gate_check)."""
    elfs = elfs or get(S.load_test_elfs.chia_remote())
    seed = a.repeat_seed if baseline else None
    tag = f"gate_{rtl.hash[:16]}_{S.GATE_VERSION}_s{seed if seed is not None else 'x'}"
    hit = cache_has(tag)
    print(f"gate: {len(elfs['isa'])} test ISA, {len(elfs['bench'])} benchmark ({elfs['root']}); tag {tag} "
          f"({'cache HIT' if hit else 'chạy thật'})", flush=True)
    t0 = time.time()
    with mw.step("gate"):
        g = get(S.run_gate.chia_remote(art, elfs, repeat_seed=seed, _chia_tag=tag))
    tm.add("gate", (time.time() - t0) / 60, hit, f"sim {g.seconds / 60:.1f} phút")
    write(d / "gate.json", as_json(g))
    rows = ["bench,passed,mcycle,minstret,sim_cycles,mcycle_seed2"]
    rows += [f"{n},{b['passed']},{b['mcycle']},{b['minstret']},{b['sim_cycles']},"
             f"{g.bench_repeat.get(n, {}).get('mcycle')}" for n, b in g.bench.items()]
    write(d / "cycles.csv", "\n".join(rows) + "\n")
    if g.fail_logs:
        write(d / "sim_fail.log", "\n\n".join(f"===== {n}\n{t}" for n, t in g.fail_logs.items()))
    known = sorted(n for n, ok in g.isa.items() if not ok)
    write(d / "known_fail.json", json.dumps(known, indent=2))
    print(f"gate: ISA {sum(g.isa.values())}/{len(g.isa)} pass ({g.seconds / 60:.1f} phút sim)"
          + (f"; hỏng sẵn ở baseline (loại khỏi gate): {known}" if known else ""), flush=True)
    for n, b in g.bench.items():
        print(f"  {n:10s} pass={b['passed']} mcycle={b['mcycle']} minstret={b['minstret']} "
              f"seed{a.repeat_seed}: mcycle={g.bench_repeat.get(n, {}).get('mcycle')}", flush=True)
    if not baseline:
        return {"gate": g, "known_fail": known, "elfs": elfs, "tag": tag}
    problems = [f"benchmark hỏng: {n}" for n, b in g.bench.items() if not b["passed"]]
    problems += [f"{n}: mcycle khác khi đổi seed" for n, b in g.bench.items()
                 if n in g.bench_repeat and g.bench_repeat[n]["mcycle"] != b["mcycle"]]
    if problems:
        cache_evict(tag)
        write(d / "problems.txt", "\n".join(problems))
        raise SystemExit("baseline chưa dùng được:\n  " + "\n  ".join(problems) + f"\nxem {d}/sim_fail.log")
    return {"gate": g, "known_fail": known, "elfs": elfs, "tag": tag}


def step_func(a, d: Path, art, rtl, elfs: dict, mw: MemWatch, tm: Timer, base: "F.FuncResult | None" = None,
              base_hash: str = "", report: list | None = None) -> "F.FuncResult":
    """base=None: ghi vết baseline (tag ftrace_), rồi tự so baseline ở seed khác (tag fcheck_, phải ĐẠT).
    Có base: so ứng viên với vết baseline (tag fcheck_<ứng viên>_<baseline>)."""
    if base is None:
        tag = f"ftrace_{rtl.hash[:16]}_{F.FUNC_VERSION}"
        hit = cache_has(tag)
        print(f"func: ghi vết {', '.join(F.FUNC_BENCHES)}; tag {tag} ({'cache HIT' if hit else 'chạy thật'})", flush=True)
        t0 = time.time()
        with mw.step("func"):
            fr = get(F.run_func.chia_remote(art, elfs, None, None, _chia_tag=tag))
        tm.add("func vết", (time.time() - t0) / 60, hit, f"sim {fr.seconds / 60:.1f} phút")
        for line in F.summary_lines(fr):
            print(line, flush=True)
        write(d / "func_base.json", as_json({b: dataclasses.replace(r, blob=b"") for b, r in fr.benches.items()}))
        if not fr.ok:
            cache_evict(tag)
            raise SystemExit("func: không ghi được vết baseline (xem func_base.json: bad_samples, log_tail)")
        base, base_hash = fr, rtl.hash
    tag = f"fcheck_{rtl.hash[:16]}_{base_hash[:16]}_{F.FUNC_VERSION}{F.CHECK_VERSION}_s{a.repeat_seed}"
    hit = cache_has(tag)
    print(f"func: so với vết baseline (seed {a.repeat_seed}); tag {tag} ({'cache HIT' if hit else 'chạy thật'})", flush=True)
    t0 = time.time()
    with mw.step("func"):
        fc = get(F.run_func.chia_remote(art, elfs, base, a.repeat_seed, _chia_tag=tag))
    tm.add("func so", (time.time() - t0) / 60, hit, f"sim {fc.seconds / 60:.1f} phút")
    for line in F.summary_lines(fc):
        print(line, flush=True)
    if report is not None:
        report += ["", "## Func check", "", f"Seed {a.repeat_seed}, tag `{tag}`", "", "```", *F.summary_lines(fc), "```"]
    write(d / "func_check.json", as_json(fc))
    if not fc.ok and base_hash == rtl.hash:
        cache_evict(tag)
        raise SystemExit("func: baseline so với chính nó KHÔNG đạt: func_check báo nhầm, cần sửa trước khi dùng")
    return base if base_hash == rtl.hash and fc.ok else fc


def stage_row(a, point: str, r: "O.StageResult", minutes: float, peak, cached: bool) -> dict:
    row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run": a.run_name, "point": point,
           "stage": r.stage, "period_ns": r.period_ns, "fmax_mhz": r.fmax_mhz, "internal_slack_ns": r.internal_slack_ns,
           "worst_slack_ns": r.worst_slack_ns, "tns_ns": r.tns_ns, "family_score": r.family_score,
           "std_area_um2": r.std_area_um2, "macro_area_um2": r.macro_area_um2, "core_area_um2": r.core_area_um2,
           "n_std": r.n_std, "crit_start": r.crit_start, "crit_end": r.crit_end, "minutes": round(minutes, 2),
           "peak_gb": peak, "cached": cached, "variant": r.variant, "ok": r.ok, "error": r.error.splitlines()[0] if r.error else ""}
    for reg in REGION_COLS:
        row[f"{reg}_slack_ns"] = r.regions.get(reg, {}).get("slack")
    return row


def print_stage(r: "O.StageResult"):
    print(f"[{r.stage}] ok={r.ok} rc={r.rc} {r.seconds / 60:.1f} phút  Fmax {fmt(r.fmax_mhz, 2)} MHz  "
          f"slack (không I/O) {fmt(r.internal_slack_ns)}  WNS mọi đường {fmt(r.worst_slack_ns)}  TNS {fmt(r.tns_ns)}  "
          f"điểm họ {fmt(r.family_score)}", flush=True)
    if r.crit_start:
        print(f"  đường găng: {r.crit_start} -> {r.crit_end}", flush=True)
    for reg, v in sorted(r.regions.items(), key=lambda kv: kv[1]["slack"]):
        print(f"  vùng {reg:7s} slack {v['slack']:8.3f}  ({v['neg']}/{v['count']} endpoint âm)  "
              f"{v['start']} -> {v['end']}", flush=True)
    if r.std_area_um2 is not None:
        print(f"  diện tích logic {r.std_area_um2:,.0f} um2 ({r.n_std} cell), macro {fmt(r.macro_area_um2, 0)} um2 "
              f"({r.n_macro}), core {fmt(r.core_area_um2, 0)} um2", flush=True)
    if r.counts:
        print(f"  {r.counts}", flush=True)
    if getattr(r, "noclock", ""):
        rows = r.noclock.splitlines()
        print(f"  thanh ghi không có clock nào, theo nhóm ({len(rows)} nhóm; số, nhóm, net clock mẫu):", flush=True)
        for line in rows[:20]:
            print("    " + line, flush=True)
    for s in r.steps:
        print(f"    {s['log']:32s} {s['elapsed_s'] / 60:7.1f} phút  RAM đỉnh {s['peak_mb'] / 1024:6.2f} GB", flush=True)
    if r.error:
        print("  LỖI/CẢNH BÁO: " + r.error[:3000], flush=True)


def step_orfs(a, d: Path, stage: str, rtl, fakeram, mw: MemWatch, tm: Timer, prep: dict, point: str = "baseline",
              fatal: bool = True, make_vars: list | None = None) -> "O.StageResult":
    variant = prep["variant"]
    tag = f"orfs_{variant}_{O.REPORT_VERSION}_{stage}"
    if make_vars:
        tag += "_" + hashlib.sha1(" ".join(sorted(make_vars)).encode()).hexdigest()[:6]
    hit = cache_has(tag)
    timeout = {"synth": a.synth_timeout, "floorplan": a.floorplan_timeout, "place": a.place_timeout,
               "cts": getattr(a, "cts_timeout", 3 * 3600), "route": getattr(a, "route_timeout", 8 * 3600)}[stage]
    print(f"\n== {stage} @ {a.period_ns} ns, variant {variant} ({'cache HIT' if hit else f'chạy thật, giới hạn {timeout / 60:.0f} phút'})",
          flush=True)
    t0 = time.time()
    with mw.step(stage):
        r = get(O.orfs_stage.chia_remote(prep["config_mk"], variant, stage, a.period_ns, timeout,
                                         make_vars=list(make_vars or []), _chia_tag=tag))
    minutes = (time.time() - t0) / 60
    peak = mw.peaks().get(stage, {}).get("_tổng")
    tm.add(stage if point == "baseline" else f"{stage} {point}", minutes, hit, f"make {r.seconds / 60:.1f} phút")
    write(d / f"orfs_{stage}.json", as_json(dataclasses.replace(r, endpoints="", paths="", report_log="")))
    write(d / f"families_{stage}.txt", r.families + "\n")
    write(d / f"endpoints_{stage}.txt", r.endpoints + "\n")
    write(d / f"paths_{stage}.rpt", r.paths + "\n")
    write(d / f"report_{stage}.log", r.report_log + "\n")
    write(d / f"top_slack_{stage}.txt", getattr(r, "top_slack", "") + "\n")
    write(d / f"top_worst_{stage}.txt", getattr(r, "top_worst", "") + "\n")
    write(d / f"noclock_{stage}.txt", getattr(r, "noclock", "") + "\n")
    write(d / f"make_{stage}.tail", r.make_tail + "\n")
    print_stage(r)
    append_csv(Path(a.out) / "results.csv", stage_row(a, point, r, minutes, peak, hit))
    if not r.ok:
        cache_evict(tag)
        if not fatal:
            return r
        raise SystemExit(f"{stage} lỗi: {r.error.splitlines()[0] if r.error else ''}. Log ORFS: "
                         f"{a.orfs_dir}/{O.work_dirs_rel(variant)['logs']}/")
    return r


def run_orfs_point(a, d: Path, rtl, fakeram, orfs_vars: dict, point: str, until: str, mw: MemWatch, tm: Timer,
                   fatal: bool = True) -> tuple[dict, dict]:
    """Chuẩn bị design ORFS cho (RTL, chu kỳ, biến ORFS) rồi chạy synth → ... → `until`.
    Trả (prep, {bước: StageResult}); bước lỗi (fatal=False) là bước cuối trong dict."""
    prm = fakeram.Params(access_ns=a.fakeram_access_ns, um2_per_bit=a.fakeram_um2_per_bit)
    fr_tag = f"{fakeram.VERSION}a{a.fakeram_access_ns:g}u{a.fakeram_um2_per_bit:g}"
    variant = O.variant_name(rtl.hash, a.period_ns, fr_tag, orfs_vars)
    a.__dict__.setdefault("orfs_variants", {})[point] = variant      # cho prune_orfs cuối job
    prep = O.prepare_design(a.orfs_dir, rtl, a.period_ns, fakeram, prm, variant, orfs_vars)
    write(d / "prepare.json", json.dumps(prep, indent=2))
    print(f"\ndesign ORFS: {prep['design_dir']} (đổi: {prep['changed_files'] or 'không'}), cổng {prep['ports']}, "
          f"clock {prep['clock_ports']}, clock phụ (bất đồng bộ) {prep.get('aux_clock_ports')}, {prep['sv_casts_rewritten']} cast viết lại", flush=True)
    if prep["fakeram_port_issues"]:
        print("  CẢNH BÁO chân fakeram khác Verilog:\n    " + "\n    ".join(prep["fakeram_port_issues"]), flush=True)
    if point == "baseline":
        for m in prep["fakeram"]["macros"]:
            print(f"  fakeram {m['name']:36s} {m['depth']}x{m['width']} {','.join(m['ports'])} "
                  f"{m['w_um']:.0f}x{m['h_um']:.0f} um", flush=True)
    stages = {}
    for stage in ORFS_STEPS[:ORFS_STEPS.index(until) + 1]:
        r = stages[stage] = step_orfs(a, d, stage, rtl, fakeram, mw, tm, prep, point=point, fatal=fatal)
        if not r.ok:
            break
        pk = mw.peaks().get(stage, {}).get("_tổng") or 0
        orfs_pk = max((s["peak_mb"] for s in r.steps), default=0) / 1024
        if max(pk, orfs_pk) > a.mem_limit_gb:
            msg = (f"RAM đỉnh {max(pk, orfs_pk):.1f} GB > {a.mem_limit_gb} GB ở {stage}: dừng, cần giảm "
                   "(PLACE_DENSITY, die size, place theo vùng)")
            print("CẢNH BÁO: " + msg, flush=True)
            raise SystemExit(msg)
    return prep, stages


def _du(p: Path) -> int:
    n = 0
    for dp, _dn, fns in os.walk(p):
        for f in fns:
            try:
                n += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return n


def prune_orfs(a, keep_points: list[str]) -> None:
    """Cuối job: chỉ giữ kết quả ORFS (flow/results|logs|reports|objects/<variant>) của bản tốt nhất; xoá của các
    variant khác mà job này đã dùng (vòng bị loại, lần chạy nhiễu, baseline khi đã có bản tốt hơn). Thư mục design
    (nhỏ) và cache CHIA (số đo) giữ nguyên, nên RTL đã đo vẫn không đo lại. --keep-orfs để tắt."""
    vs = getattr(a, "orfs_variants", {})
    keep = {vs[p] for p in keep_points if p in vs}
    drop = sorted(set(vs.values()) - keep)
    if not drop:
        return
    if getattr(a, "keep_orfs", False):
        print(f"\nORFS: giữ hết {len(vs)} variant (--keep-orfs)", flush=True)
        return
    print(f"\n== dọn ORFS: giữ {', '.join(sorted(keep)) or '-'} ({', '.join(keep_points)}), xoá {len(drop)} variant", flush=True)
    total = 0
    for v in drop:
        freed = 0
        for rel in O.work_dirs_rel(v).values():
            p = Path(a.orfs_dir) / rel
            if not p.exists():
                continue
            size = _du(p)
            try:
                shutil.rmtree(p)
                freed += size
            except OSError as e:
                print(f"  không xoá được {p}: {e}", flush=True)
        total += freed
        name = next((k for k, x in vs.items() if x == v), "?")
        print(f"  {v} ({name}): {freed / 2**30:.2f} GB", flush=True)
    print(f"  tổng {total / 2**30:.2f} GB", flush=True)


def noise_variants(orfs_vars: dict) -> list[tuple[str, dict]]:
    """4 lần chạy baseline nhiễu nhẹ: y hệt (biến giả chỉ đổi tên variant), PLACE_DENSITY ±0,01, CORE_UTILIZATION +1."""
    dens = float(orfs_vars["PLACE_DENSITY"])
    util = float(orfs_vars["CORE_UTILIZATION"])
    return [("rerun", {**orfs_vars, "SOCFMAX_NOISE": "rerun"}),
            ("density-", {**orfs_vars, "PLACE_DENSITY": f"{dens - 0.01:.2f}"}),
            ("density+", {**orfs_vars, "PLACE_DENSITY": f"{dens + 0.01:.2f}"}),
            ("util+1", {**orfs_vars, "CORE_UTILIZATION": f"{util + 1:g}"})]


def step_noise(a, out: Path, rtl, fakeram, base_stages: dict, mw: MemWatch, tm: Timer,
               report: list | None = None) -> "P.Noise":
    samples = [{"name": "baseline", "fmax": base_stages["place"].fmax_mhz, "family": base_stages["place"].family_score,
                "synth_fmax": base_stages["synth"].fmax_mhz, "area": base_stages["place"].std_area_um2}]
    print("\n== nhiễu: 4 lần chạy baseline nhiễu nhẹ (synth → place mỗi lần)", flush=True)
    for name, ov in noise_variants(a.orfs_vars):
        print(f"\n-- nhiễu {name}: {', '.join(f'{k}={v}' for k, v in ov.items() if a.orfs_vars.get(k) != v)}", flush=True)
        _prep, st = run_orfs_point(a, out / "runs" / f"noise_{name}", rtl, fakeram, ov, f"noise:{name}", "place", mw, tm)
        samples.append({"name": name, "fmax": st["place"].fmax_mhz, "family": st["place"].family_score,
                        "synth_fmax": st["synth"].fmax_mhz, "area": st["place"].std_area_um2,
                        "variant": st["place"].variant})
    noise = P.noise_from(samples)
    write(out / "noise.json", json.dumps(dataclasses.asdict(noise), indent=2))
    print("\n== nhiễu (sau place):", flush=True)
    for s_ in samples:
        print(f"  {s_['name']:9s} Fmax {fmt(s_['fmax'], 3)} MHz  điểm họ {fmt(s_['family'])}  synth {fmt(s_['synth_fmax'], 2)} MHz"
              f"  logic {fmt(s_['area'], 0)} um2", flush=True)
    print(f"  {noise.note}", flush=True)
    print(f"  NGƯỠNG NHẬN: Fmax ±{noise.fmax_mhz:.3f} MHz, điểm họ ±{noise.family_ns:.3f} ns -> {out}/noise.json", flush=True)
    if report is not None:
        report += ["", "## Nhiễu (sau place)", "", "| lần | Fmax (MHz) | điểm họ | synth Fmax (MHz) | logic (um2) |",
                   "|---|---|---|---|---|"]
        report += [f"| {s_['name']} | {fmt(s_['fmax'], 3)} | {fmt(s_['family'])} | {fmt(s_['synth_fmax'], 2)} | "
                   f"{fmt(s_['area'], 0)} |" for s_ in samples]
        report += ["", noise.note, "", f"**Ngưỡng nhận:** Fmax ±{noise.fmax_mhz:.3f} MHz, điểm họ ±{noise.family_ns:.3f} ns"]
    return noise


def summary(a, tm: Timer, mw: MemWatch, rtl, gate, prep, stages: dict, report: list | None = None) -> str:
    L = [f"# soc-fmax {a.run_name}", "",
         f"- Thời điểm: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
         f"- Config `{a.config}`, top `DigitalTop`, PDK sky130hd `tt_025C_1v80`, chu kỳ {a.period_ns} ns",
         f"- Chipyard `{a.chipyard}`, ORFS `{a.orfs_dir}`"]
    if rtl:
        L.append(f"- RTL hash `{rtl.hash[:12]}`, {len(rtl.modules)} module, {len(rtl.srams)} macro SRAM")
    if prep:
        fr = prep["fakeram"]
        L += [f"- fakeram `{fr.get('version', '')}`: access {a.fakeram_access_ns} ns, {a.fakeram_um2_per_bit} um2/bit, "
              f"{len(fr['macros'])} macro, {fr['total_bits']} bit, {fr['total_area_um2'] / 1e6:.3f} mm2 (timing giả định)",
              f"- ORFS variant `{prep['variant']}`, biến: `{prep['orfs_vars']}`"]
    L += ["", "## Thời gian và RAM đỉnh", "", "| bước | phút | cache | RAM đỉnh (GB, tổng container) | ghi chú |", "|---|---|---|---|---|"]
    peaks = mw.peaks()
    for row in tm.rows:
        key = {"tách RTL": "build", "func vết": "func", "func so": "func"}.get(row["step"], row["step"])
        L.append(f"| {row['step']} | {row['minutes']} | {'có' if row['cached'] else ''} | "
                 f"{fmt(peaks.get(key, {}).get('_tổng'), 2) if key == row['step'] else ''} | {row['note']} |")
    if gate:
        g = gate["gate"]
        L += ["", "## Gate", "", f"ISA {sum(g.isa.values())}/{len(g.isa)} pass; hỏng sẵn: {gate['known_fail'] or '-'}", "",
              "| benchmark | mcycle | minstret |", "|---|---|---|"]
        L += [f"| {n} | {b['mcycle']} | {b['minstret']} |" for n, b in g.bench.items()]
    L += report or []
    for stage, r in stages.items():
        L += ["", f"## {stage}", "",
              f"Fmax **{fmt(r.fmax_mhz, 2)} MHz**, slack không I/O {fmt(r.internal_slack_ns)} ns, WNS mọi đường "
              f"{fmt(r.worst_slack_ns)} ns, TNS {fmt(r.tns_ns)} ns, điểm họ {fmt(r.family_score)}; diện tích logic "
              f"{fmt(r.std_area_um2, 0)} um2, macro {fmt(r.macro_area_um2, 0)} um2", "",
              f"Đường găng: `{r.crit_start}` → `{r.crit_end}`", "", "| vùng endpoint | slack | âm/tổng | đường |", "|---|---|---|---|"]
        L += [f"| {k} | {v['slack']:.3f} | {v['neg']}/{v['count']} | `{v['start']}` → `{v['end']}` |"
              for k, v in sorted(r.regions.items(), key=lambda kv: kv[1]["slack"])]
        L += ["", "Bước ORFS:", "", "| log | phút | RAM đỉnh (GB) |", "|---|---|---|"]
        L += [f"| {s['log']} | {s['elapsed_s'] / 60:.1f} | {s['peak_mb'] / 1024:.2f} |" for s in r.steps]
        L += ["", "```", r.families, "```"]
    return "\n".join(L) + "\n"


def main():
    a = parse_args()
    a.out = str(Path(os.path.expanduser(a.out_dir)) / a.run_name)
    out = Path(a.out)
    d = out / "runs" / "baseline"
    d.mkdir(parents=True, exist_ok=True)
    write(out / "args.json", json.dumps(vars(a), indent=2, default=str))
    until = STEPS.index(a.until)

    ray.init(address="auto", namespace="soc_fmax", ignore_reinit_error=True)
    start_collector(log_dir=str(out / "profile"))
    start_cache(size=a.cache_gb, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(EXP_YAML))
    bypass = Bypass(yaml_path=str(EXP_YAML))
    for fn in CACHED_FNS:
        bypass.set_provider(fn, S.cache_provider)
        bypass.set_cond(fn, S.cache_hit_cond)
    mw = MemWatch().start()
    tm = Timer()
    rtl = gate = prep = None
    stages = {}
    report = []   # mục func check / nhiễu cho summary.md
    try:
        res = ray.cluster_resources()
        need = ["chipyard", "verilator_run"] + (["asic"] if until >= STEPS.index("synth") else [])
        missing = [r for r in need if not res.get(r)]
        if missing:
            raise SystemExit(f"cluster thiếu resource {missing}: python scripts/chia-up-blocks.py --asic --verilator-slots 4")
        print(f"verilator_run: {res.get('verilator_run')} slot", flush=True)
        _state, art, rtl = step_rtl(a, d, mw, tm)
        if until >= STEPS.index("gate"):
            gate = step_gate(a, d, art, rtl, mw, tm)
        if until >= STEPS.index("func"):
            func_base = step_func(a, d, art, rtl, gate["elfs"], mw, tm, report=report)
        if until >= STEPS.index("synth"):
            fakeram = load_fakeram(a.repo)
            prep, stages = run_orfs_point(a, d, rtl, fakeram, a.orfs_vars, "baseline",
                                          STEPS[until] if STEPS[until] in ORFS_STEPS else "place", mw, tm)
            if a.noise and "place" in stages and stages["place"].ok:
                step_noise(a, out, rtl, fakeram, stages, mw, tm, report=report)
    except SystemExit as e:
        print(f"\nDỪNG: {e}", flush=True)
        raise
    except Exception:
        traceback.print_exc()
        raise
    finally:
        mw.stop()
        write(out / "memory.json", json.dumps({"peaks": mw.peaks(), "error": mw.error}, indent=2))
        write(out / "summary.md", summary(a, tm, mw, rtl, gate, prep, stages, report))
        prune_orfs(a, ["baseline"])     # lần chạy nhiễu là đo lại cùng RTL, không phải bản thiết kế: chỉ giữ baseline
        print("\n## Thời gian và RAM đỉnh (docker stats, GB)", flush=True)
        for row in tm.rows:
            print(f"  {row['step']:10s} {row['minutes']:8.2f} phút  {'(cache)' if row['cached'] else '       '}  {row['note']}", flush=True)
        for step, pk in mw.peaks().items():
            if step != "idle":
                print(f"  {step:10s} " + "  ".join(f"{k}={v}" for k, v in sorted(pk.items())), flush=True)
        print(f"\nKết quả: {out}/summary.md, {out}/results.csv", flush=True)
        stop_collector()
        stop_cache()


if __name__ == "__main__":
    main()
