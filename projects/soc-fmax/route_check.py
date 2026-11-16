"""route_check.py: hiệu chỉnh một lần, place so với CTS và route, trên MỘT bản thiết kế (patch tốt nhất của một job).

Không phải một phần của loop: chạy riêng khi không có job nào khác (sau một job loop.py), rồi loop chạy tiếp như cũ.
Câu hỏi cần trả lời: Fmax sau place (số loop dùng để chọn) lệch bao nhiêu so với sau CTS (clock thật, có
repair_timing) và sau route (dây thật, RC trích xuất bằng OpenRCX), và đường găng có giống nhau không.

Các bước:
  1. áp patch lên ~/tools/chipyard (--from-run DIR: best.patch + bản tốt nhất trong history.json của job đó; hoặc
     --patch FILE), build + tách RTL (thường lấy từ cache);
  2. ORFS synth -> floorplan -> place ở --period-ns (mặc định: chu kỳ của bản tốt nhất; cache nếu đã đo);
  3. make cts, make route trên cùng variant (lỗi hay quá giờ thì ghi lại, không dừng), báo cáo bằng orfs/report.tcl;
  4. hoàn nguyên cây, kiểm digest bằng lúc đầu.
--allow-congestion: global route tràn (GRT-0116) vẫn đi tiếp detailed route (GLOBAL_ROUTE_ARGS có -allow_congestion, chỉ
cho lệnh make route, không đổi variant, nên place/cts dùng lại); summary.md ghi tổng tràn của global route và số vi phạm DRC cuối detailed
route. Không gate/func (bản thiết kế đã qua gate trong loop). Không dọn ORFS: variant giữ nguyên (cts/route thêm file vào nó).

Kết quả ở ~/soc-fmax-runs/<run>/: summary.md (bảng place/cts/route), results.csv, runs/check/ (báo cáo từng bước).

Chạy (README.md):
  chia job submit --no-wait --submission-id route-r8 --working-dir . -- python route_check.py --run-name route-r8 \\
      --from-run ~/soc-fmax-runs/m4-b
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ray

import soc_fmax as D
import soc_nodes as S
from loop import Applied, PatchError
from memwatch import MemWatch
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector

STAGES = ("place", "cts", "route")
# GLOBAL_ROUTE_ARGS mặc định của ORFS cài sẵn (log 5_1_grt: "-congestion_report_iter_step 5 -verbose"; -congestion_iterations
# 30 do global_route.tcl tự thêm) + -allow_congestion, cờ mà global_route.tcl tìm trong biến này (không có ALLOW_CONGESTION).
ALLOW_CONGESTION_ARGS = "GLOBAL_ROUTE_ARGS=-congestion_report_iter_step 5 -verbose -allow_congestion"


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    D.add_common_args(ap)
    ap.add_argument("--from-run", default="", help="thư mục một job loop.py: lấy best.patch và bản tốt nhất")
    ap.add_argument("--patch", default="", help="patch (đường dẫn từ gốc Chipyard) thay cho --from-run")
    ap.add_argument("--point", default="", help="tên điểm (mặc định: tên bản tốt nhất của --from-run)")
    ap.add_argument("--cts-timeout", type=int, default=3 * 3600)
    ap.add_argument("--route-timeout", type=int, default=8 * 3600)
    ap.add_argument("--allow-congestion", action="store_true",
                    help="global route tràn vẫn chạy tiếp detailed route (thêm -allow_congestion vào GLOBAL_ROUTE_ARGS của make route)")
    a = ap.parse_args()
    a.period_explicit = any(x == "--period-ns" or x.startswith("--period-ns=") for x in sys.argv[1:])
    D.finish_args(a, ap)
    if bool(a.from_run) == bool(a.patch):
        ap.error("cần đúng một trong --from-run và --patch")
    return a


def load_target(a) -> tuple[str, str, str]:
    """-> (patch, tên điểm, rtl hash mong đợi hoặc "")."""
    if a.patch:
        return Path(os.path.expanduser(a.patch)).read_text(), a.point or "patch", ""
    hdir = Path(os.path.expanduser(a.from_run))
    h = json.loads((hdir / "history.json").read_text())
    best = h["best"]
    patch = (hdir / "best.patch").read_text() if (hdir / "best.patch").exists() else ""
    if not patch.strip():
        raise SystemExit(f"{hdir}: bản tốt nhất là baseline (best.patch rỗng)")
    if not a.period_explicit and best.get("period_ns"):
        a.period_ns = best["period_ns"]
    print(f"{hdir.name}: bản tốt nhất {best['name']} ({D.fmt(best.get('fmax'), 3)} MHz @ {best.get('period_ns')} ns), "
          f"RTL {best.get('rtl_hash', '')[:12]}", flush=True)
    return patch, a.point or best["name"], best.get("rtl_hash", "")


def route_logs(a, variant: str) -> list[str]:
    """Vài dòng từ log ORFS của route (trên host): tổng tràn global route, số vi phạm DRC cuối detailed route."""
    if not variant:
        return []
    logs = Path(os.path.expanduser(a.orfs_dir)) / D.O.work_dirs_rel(variant)["logs"]
    out = []
    grt = logs / "5_1_grt.log"
    if grt.exists():
        t = grt.read_text(errors="replace")
        m = re.findall(r"^Total\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", t, re.M)
        if m:
            x = m[-1]
            out.append(f"global route: dùng {x[2]} % tài nguyên, tràn tối đa {x[3]} (ngang) / {x[4]} (dọc), "
                       f"tổng tràn {x[5]}" + (" (GRT-0116: tràn)" if "GRT-0116" in t else ""))
    drt = logs / "5_2_route.log"
    if drt.exists():
        v = re.findall(r"Number of violations = (\d+)", drt.read_text(errors="replace"))
        if v:
            out.append(f"detailed route: {v[-1]} vi phạm DRC ở lần lặp cuối (lần đầu {v[0]})")
    return out


def summary_md(a, point: str, rtl_hash: str, st: dict, tm, mw, t0: datetime) -> str:
    now = datetime.now(timezone.utc)
    L = [f"# soc-fmax route_check {a.run_name}", "",
         f"- {t0.isoformat(timespec='minutes')} → {now.isoformat(timespec='minutes')} "
         f"({(now - t0).total_seconds() / 3600:.2f} giờ)",
         f"- Điểm {point}, RTL `{rtl_hash[:12]}`, chu kỳ {a.period_ns:g} ns, "
         f"{'từ `' + a.from_run + '`' if a.from_run else 'patch `' + a.patch + '`'}",
         f"- Variant ORFS `{next(iter(st.values())).variant if st else '-'}`", "",
         "| bước | Fmax (MHz) | WNS không I/O (ns) | WNS mọi đường | TNS | logic (um2) | đường găng | make (phút) |",
         "|---|---|---|---|---|---|---|---|"]
    for s in STAGES:
        r = st.get(s)
        if r is None:
            L.append(f"| {s} | (không chạy) | | | | | | |")
            continue
        L.append(f"| {s} | {D.fmt(r.fmax_mhz, 3)} | {D.fmt(r.internal_slack_ns)} | {D.fmt(r.worst_slack_ns)} | "
                 f"{D.fmt(r.tns_ns)} | {D.fmt(r.std_area_um2, 0)} | `{r.crit_start} -> {r.crit_end}` | "
                 f"{r.seconds / 60:.1f} |" + (f" LỖI: {r.error.splitlines()[0][:200]}" if not r.ok and r.error else ""))
    variant = next(iter(st.values())).variant if st else ""
    L += [""] + [f"- {x}" for x in route_logs(a, variant)] if route_logs(a, variant) else []
    pl = st.get("place")
    for s in ("cts", "route"):
        r = st.get(s)
        if pl and r and pl.fmax_mhz and r.fmax_mhz:
            L.append(f"\n{s} so với place: Fmax {r.fmax_mhz - pl.fmax_mhz:+.3f} MHz "
                     f"({(r.fmax_mhz / pl.fmax_mhz - 1) * 100:+.1f} %), chu kỳ đạt được "
                     f"{1000 / r.fmax_mhz - 1000 / pl.fmax_mhz:+.3f} ns (dương = chậm hơn place)")
    regs = sorted({k for r in st.values() for k in (r.regions or {})})
    if regs:
        L += ["", "## Slack theo vùng", "", "| vùng | " + " | ".join(s for s in STAGES if s in st) + " |",
              "|---|" + "---|" * len([s for s in STAGES if s in st])]
        for k in regs:
            L.append(f"| {k} | " + " | ".join(D.fmt((st[s].regions or {}).get(k, {}).get("slack"))
                                              for s in STAGES if s in st) + " |")
    for s in STAGES:
        r = st.get(s)
        if r is not None and r.families:
            L += ["", f"## Họ endpoint xấu nhất sau {s}", "", "```", r.families.strip()[:4000], "```"]
    L += ["", "## Thời gian và RAM", "", "| bước | phút | cache | RAM đỉnh (GB) |", "|---|---|---|---|"]
    peaks = mw.peaks()
    for row in tm.rows:
        k = row["step"].split()[0]
        L.append(f"| {row['step']} | {row['minutes']:.1f} | {'có' if row['cached'] else ''} | "
                 f"{D.fmt(peaks.get(k, {}).get('_tổng'), 2)} |")
    for s in STAGES:
        r = st.get(s)
        if r is not None and r.steps and s != "place":
            L += ["", f"Bước ORFS tới {s}:", "", "| log | phút | RAM đỉnh (GB) |", "|---|---|---|"]
            L += [f"| {x['log']} | {x['elapsed_s'] / 60:.1f} | {x['peak_mb'] / 1024:.2f} |" for x in r.steps]
    return "\n".join(L) + "\n"


def main():
    a = parse_args()
    t0 = datetime.now(timezone.utc)
    a.out = str(Path(os.path.expanduser(a.out_dir)) / a.run_name)
    out = Path(a.out)
    d = out / "runs" / "check"
    d.mkdir(parents=True, exist_ok=True)
    D.write(out / "args.json", json.dumps(vars(a), indent=2, default=str))
    patch, point, want_hash = load_target(a)

    ray.init(address="auto", namespace="soc_fmax", ignore_reinit_error=True)
    start_collector(log_dir=str(out / "profile"))
    start_cache(size=a.cache_gb, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(D.EXP_YAML))
    bypass = Bypass(yaml_path=str(D.EXP_YAML))
    for fn in D.CACHED_FNS:
        bypass.set_provider(fn, S.cache_provider)
        bypass.set_cond(fn, S.cache_hit_cond)
    mw = MemWatch().start()
    tm = D.Timer()
    st: dict = {}
    rtl_hash = ""
    try:
        res = ray.cluster_resources()
        missing = [r for r in ("chipyard", "asic") if not res.get(r)]
        if missing:
            raise SystemExit(f"cluster thiếu resource {missing}: python scripts/chia-up-blocks.py --asic")
        before = get(S.tree_state.chia_remote(a.chipyard))
        subs = sorted(before["subs"])
        applied = Applied(a.chipyard)
        try:
            try:
                applied.apply(patch, subs)
            except PatchError as e:
                raise SystemExit(f"patch không áp được: {e}")
            _state, _art, rtl = D.step_rtl(a, d, mw, tm, baseline=False)
            if rtl is None:
                raise SystemExit("build lỗi")
            rtl_hash = rtl.hash
            if want_hash and rtl.hash != want_hash:
                raise SystemExit(f"RTL {rtl.hash[:12]} ≠ bản tốt nhất {want_hash[:12]}")
            fakeram = D.load_fakeram(a.repo)
            prep, sp = D.run_orfs_point(a, d, rtl, fakeram, a.orfs_vars, point, "place", mw, tm)
            st["place"] = sp["place"]
            for stage in ("cts", "route"):
                try:
                    mv = [ALLOW_CONGESTION_ARGS] if stage == "route" and a.allow_congestion else None
                    r = D.step_orfs(a, d, stage, rtl, fakeram, mw, tm, prep, point=point, fatal=False, make_vars=mv)
                except SystemExit as e:          # noqa: PERF203
                    print(f"{stage}: {e}", flush=True)
                    break
                st[stage] = r
                if not r.ok:
                    print(f"{stage} lỗi: dừng ở đây (log ORFS: {a.orfs_dir}/"
                          f"{D.O.work_dirs_rel(prep['variant'])['logs']}/)", flush=True)
                    break
        finally:
            applied.revert()
            after = get(S.tree_state.chia_remote(a.chipyard))
            if after["digest"] != before["digest"]:
                print(f"CẢNH BÁO: cây Chipyard sau khi hoàn nguyên KHÁC lúc đầu ({after['digest'][:12]} ≠ "
                      f"{before['digest'][:12]})", flush=True)
    except SystemExit as e:
        print(f"\nDỪNG: {e}", flush=True)
        raise
    except Exception:
        traceback.print_exc()
        raise
    finally:
        mw.stop()
        D.write(out / "summary.md", summary_md(a, point, rtl_hash, st, tm, mw, t0))
        print(f"\nKết quả: {out}/summary.md, {out}/results.csv", flush=True)
        stop_collector()
        stop_cache()


if __name__ == "__main__":
    main()
