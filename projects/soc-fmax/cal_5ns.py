"""cal_5ns.py: hiệu chỉnh một lần, đo baseline và các bản tốt nhất ở chu kỳ ép chặt (mặc định 5 ns).

Không phải một phần của loop, không agent, không gate (các bản đã qua gate trong loop). Câu hỏi:
  1. Ép 5 ns có giữ thứ tự và độ lớn cải thiện giữa các bản không (so với số đo ở 9/10 ns của loop)?
  2. Ở 5 ns floorplan còn bão hoà không, và có xếp hạng giống place không (để chọn chỗ A/B cho agent)?
  3. Nhiễu place ở 5 ns (bản cuối + 3 biến thể density ±0,01, util+1) so với 0,46–0,87 ns ở 9 ns.
  4. Diện tích, thời gian, RAM khi ép 5 ns.

Các bước, với mỗi điểm (baseline rồi best.patch của từng --runs, theo thứ tự):
  áp patch lên ~/tools/chipyard -> build + tách RTL (cache) -> kiểm RTL hash = bản tốt nhất của job đó ->
  ORFS synth -> floorplan -> place ở --period-ns -> hoàn nguyên cây.
Rồi đo nhiễu cho điểm cuối (--noise-variants 0 để tắt). Bước lỗi không dừng job (ghi lại, sang điểm kế).
Cuối job chỉ giữ ORFS của điểm cuối (--keep-orfs để giữ hết).

Mỗi điểm đo ở chu kỳ khác loop nên là variant ORFS mới (không đo lại cùng RTL ở cùng thiết lập).

Kết quả ở ~/soc-fmax-runs/<run>/: summary.md, results.csv, runs/<điểm>/.

Chạy (README.md):
  chia job submit --no-wait --submission-id cal-5ns --working-dir . -- python cal_5ns.py --run-name cal-5ns
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import ray

import policy as P
import soc_fmax as D
import soc_nodes as S
from loop import Applied, PatchError
from memwatch import MemWatch
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.trace.profiler import start_collector, stop_collector


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    D.add_common_args(ap)
    ap.add_argument("--runs", default="m4-a,m4-c,m4-d,m4-e",
                    help="các job loop.py (trong --out-dir) lấy best.patch, theo thứ tự; trùng RTL thì bỏ")
    ap.add_argument("--no-baseline", action="store_true", help="không đo baseline")
    ap.add_argument("--noise-variants", type=int, default=3, help="số biến thể nhiễu cho điểm cuối (0..3)")
    a = ap.parse_args()
    if not any(x == "--period-ns" or x.startswith("--period-ns=") for x in sys.argv[1:]):
        a.period_ns = 5.0
    D.finish_args(a, ap)
    return a


def _point(d: dict) -> "P.Point":
    """P.Point từ history.json của job cũ (bỏ khoá mà bản Point hiện tại không có)."""
    import dataclasses
    keys = {f.name for f in dataclasses.fields(P.Point)}
    return P.Point(**{k: v for k, v in d.items() if k in keys})


def load_baseline(hdir: Path) -> dict:
    """Baseline: history.json không lưu khoá "baseline" (chỉ có trong ngữ cảnh agent), nên lấy RTL hash
    từ "measured" (hash -> tên điểm) và số đo place đầu tiên của điểm baseline trong results.csv."""
    import csv
    h = json.loads((hdir / "history.json").read_text())
    if "baseline" in h:
        b = _point(h["baseline"])
        f, _g, n = b.eff()
        return {"name": "baseline", "run": "", "patch": "", "rtl_hash": b.rtl_hash,
                "ref_period": b.period_ns, "ref_fmax": f, "ref_n": n}
    hashes = [k for k, v in h.get("measured", {}).items() if v == "baseline"]
    if len(hashes) != 1:
        raise SystemExit(f"{hdir}/history.json: không tìm thấy đúng một RTL baseline trong measured ({hashes})")
    ref_p, ref_f = None, None
    rc = hdir / "results.csv"
    if rc.exists():
        with rc.open() as fh:
            for r in csv.DictReader(fh):
                if r.get("point") == "baseline" and r.get("stage") == "place" and r.get("fmax_mhz"):
                    ref_p, ref_f = float(r["period_ns"]), float(r["fmax_mhz"])
                    break
    return {"name": "baseline", "run": "", "patch": "", "rtl_hash": hashes[0],
            "ref_period": ref_p, "ref_fmax": ref_f, "ref_n": 1}


def load_points(a) -> list[dict]:
    """[{name, run, patch, rtl_hash, ref_period, ref_fmax, ref_n}] theo thứ tự đo."""
    root = Path(os.path.expanduser(a.out_dir))
    pts, seen = [], set()
    if not a.no_baseline:
        b = load_baseline(root / a.runs.split(",")[0].strip())
        pts.append(b)
        seen.add(b["rtl_hash"])
    for run in [x.strip() for x in a.runs.split(",") if x.strip()]:
        hdir = root / run
        h = json.loads((hdir / "history.json").read_text())
        best = _point(h["best"])
        patch = (hdir / "best.patch").read_text() if (hdir / "best.patch").exists() else ""
        if not patch.strip() or best.rtl_hash in seen:
            print(f"{run}: bản tốt nhất {best.name} trùng điểm đã có hoặc là baseline, bỏ", flush=True)
            continue
        seen.add(best.rtl_hash)
        f, _g, n = best.eff()
        pts.append({"name": best.name, "run": run, "patch": patch, "rtl_hash": best.rtl_hash,
                    "ref_period": best.period_ns, "ref_fmax": f, "ref_n": n})
    return pts


def ach(f):
    """Chu kỳ đạt được (ns) từ Fmax."""
    return 1000.0 / f if f else None


def measure(a, pt: dict, out: Path, fakeram, before: dict, mw, tm) -> tuple:
    """-> (rtl, {bước: StageResult}, lỗi)."""
    d = out / "runs" / pt["name"]
    d.mkdir(parents=True, exist_ok=True)
    applied = Applied(a.chipyard)
    try:
        if pt["patch"]:
            try:
                applied.apply(pt["patch"], sorted(before["subs"]))
            except PatchError as e:
                return None, {}, f"patch không áp được: {e}"
        _state, _art, rtl = D.step_rtl(a, d, mw, tm, baseline=not pt["patch"])
        if rtl is None:
            return None, {}, "build lỗi"
        if pt["rtl_hash"] and rtl.hash != pt["rtl_hash"]:
            return None, {}, f"RTL {rtl.hash[:12]} ≠ bản tốt nhất {pt['rtl_hash'][:12]}"
    finally:
        applied.revert()
        after = get(S.tree_state.chia_remote(a.chipyard))
        if after["digest"] != before["digest"]:
            raise SystemExit(f"cây Chipyard sau khi hoàn nguyên KHÁC lúc đầu ({after['digest'][:12]} ≠ "
                             f"{before['digest'][:12]}): dừng")
    _prep, st = D.run_orfs_point(a, d, rtl, fakeram, a.orfs_vars, pt["name"], "place", mw, tm, fatal=False)
    bad = [s for s, r in st.items() if not r.ok]
    return rtl, st, (f"{bad[0]} lỗi" if bad else "")


def summary_md(a, pts: list, res: dict, noise: list, tm, mw, t0) -> str:
    now = datetime.now(timezone.utc)
    L = [f"# soc-fmax cal-5ns {a.run_name}", "",
         f"- {t0.isoformat(timespec='minutes')} → {now.isoformat(timespec='minutes')} "
         f"({(now - t0).total_seconds() / 3600:.2f} giờ), chu kỳ ép {a.period_ns:g} ns, biến ORFS `{a.orfs_vars}`",
         f"- Điểm: {', '.join(p['name'] + (' (' + p['run'] + ')' if p['run'] else '') for p in pts)}", "",
         f"## Số đo ở {a.period_ns:g} ns", "",
         "| điểm | loop: Fmax (MHz) @ chu kỳ | synth | floorplan | place | WNS place | TNS place | logic place (um2) | đường găng place |",
         "|---|---|---|---|---|---|---|---|---|"]
    for p in pts:
        st, err = res.get(p["name"], ({}, "chưa chạy"))
        ref = (f"{D.fmt(p['ref_fmax'], 3)} @ {D.fmt(p['ref_period'], 1)}" + (f" (trung vị {p['ref_n']})" if p["ref_n"] > 1 else "")
               if p["ref_fmax"] else "")
        def f_(s):
            r = st.get(s)
            return D.fmt(r.fmax_mhz, 2) if r is not None and r.ok else ("LỖI" if r is not None else "")
        pl = st.get("place")
        ok = pl is not None and pl.ok
        L.append(f"| {p['name']} | {ref} | {f_('synth')} | {f_('floorplan')} | {f_('place')} | "
                 f"{D.fmt(pl.internal_slack_ns) if ok else ''} | {D.fmt(pl.tns_ns, 1) if ok else ''} | "
                 f"{D.fmt(pl.std_area_um2, 0) if ok else ''} | "
                 f"{'`' + pl.crit_start + ' -> ' + pl.crit_end + '`' if ok else err} |")
    # Bước cải thiện giữa các điểm liên tiếp, theo chu kỳ đạt được (ns, dương = tốt hơn)
    L += ["", "## Cải thiện giữa hai điểm liên tiếp (chu kỳ đạt được, ns, dương = nhanh hơn)", "",
          "| từ → tới | loop (9/10 ns) | floorplan @ ép | place @ ép |", "|---|---|---|---|"]
    def stage_ach(name, s):
        r = res.get(name, ({}, ""))[0].get(s)
        return ach(r.fmax_mhz) if r is not None and r.ok else None
    for x, y in zip(pts, pts[1:]):
        def dl(u, v):
            return f"{u - v:+.3f}" if u is not None and v is not None else ""
        L.append(f"| {x['name']} → {y['name']} | {dl(ach(x['ref_fmax']), ach(y['ref_fmax']))} | "
                 f"{dl(stage_ach(x['name'], 'floorplan'), stage_ach(y['name'], 'floorplan'))} | "
                 f"{dl(stage_ach(x['name'], 'place'), stage_ach(y['name'], 'place'))} |")
    L.append("\nLoop: chu kỳ đo ghi ở cột `loop` của bảng trên (baseline, r5 ở 10 ns; từ r8 ở 9 ns); số loop là trung vị khi đã đo lặp.")
    # Xếp hạng
    def order(key):
        vals = [(p["name"], key(p)) for p in pts]
        if any(v is None for _n, v in vals):
            return None
        return [n for n, _v in sorted(vals, key=lambda t: t[1])]
    o_ref = order(lambda p: ach(p["ref_fmax"]))
    o_fp = order(lambda p: stage_ach(p["name"], "floorplan"))
    o_pl = order(lambda p: stage_ach(p["name"], "place"))
    L += ["", "## Xếp hạng (nhanh nhất trước)", "",
          f"- loop: {' > '.join(o_ref) if o_ref else '(thiếu số)'}",
          f"- floorplan @ ép: {' > '.join(o_fp) if o_fp else '(thiếu số)'}",
          f"- place @ ép: {' > '.join(o_pl) if o_pl else '(thiếu số)'}",
          f"- floorplan khớp place: {'có' if o_fp and o_fp == o_pl else 'không' if o_fp and o_pl else '?'}; "
          f"place @ ép khớp loop: {'có' if o_pl and o_pl == o_ref else 'không' if o_pl and o_ref else '?'}"]
    if noise:
        per = [ach(f) for _n, f in noise if f]
        L += ["", f"## Nhiễu place @ {a.period_ns:g} ns ({pts[-1]['name']})", "",
              "| lần | Fmax place (MHz) | chu kỳ đạt được (ns) |", "|---|---|---|"]
        L += [f"| {n} | {D.fmt(f, 3)} | {D.fmt(ach(f), 3)} |" for n, f in noise]
        if len(per) >= 2:
            L.append(f"\nDao động {max(per) - min(per):.3f} ns, trung vị {P.median(per):.3f} ns "
                     f"(ở 9 ns: 0,46–0,87 ns với 4 lần).")
    L += ["", "## Thời gian và RAM", "", "| bước | phút | cache | RAM đỉnh (GB) |", "|---|---|---|---|"]
    peaks = mw.peaks()
    for row in tm.rows:
        k = row["step"].split()[0]
        L.append(f"| {row['step']} | {row['minutes']:.1f} | {'có' if row['cached'] else ''} | "
                 f"{D.fmt(peaks.get(k, {}).get('_tổng'), 2)} |")
    return "\n".join(L) + "\n"


def main():
    a = parse_args()
    t0 = datetime.now(timezone.utc)
    a.out = str(Path(os.path.expanduser(a.out_dir)) / a.run_name)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    D.write(out / "args.json", json.dumps(vars(a), indent=2, default=str))
    pts = load_points(a)
    print("điểm: " + ", ".join(f"{p['name']} ({p['run'] or '-'}, RTL {p['rtl_hash'][:12]}, loop "
                               f"{D.fmt(p['ref_fmax'], 3)} MHz @ {p['ref_period']} ns)" for p in pts), flush=True)

    ray.init(address="auto", namespace="soc_fmax", ignore_reinit_error=True)
    start_collector(log_dir=str(out / "profile"))
    start_cache(size=a.cache_gb, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(D.EXP_YAML))
    bypass = Bypass(yaml_path=str(D.EXP_YAML))
    for fn in D.CACHED_FNS:
        bypass.set_provider(fn, S.cache_provider)
        bypass.set_cond(fn, S.cache_hit_cond)
    mw = MemWatch().start()
    tm = D.Timer()
    res: dict = {}
    noise: list = []
    try:
        cr = ray.cluster_resources()
        missing = [r for r in ("chipyard", "asic") if not cr.get(r)]
        if missing:
            raise SystemExit(f"cluster thiếu resource {missing}: python scripts/chia-up-blocks.py --asic")
        before = get(S.tree_state.chia_remote(a.chipyard))
        if S.dirty_paths(before):
            raise SystemExit("cây Chipyard có thay đổi: cần cây sạch trước khi áp best.patch")
        fakeram = D.load_fakeram(a.repo)
        last_rtl = None
        for p in pts:
            print(f"\n######## {p['name']} ({p['run'] or 'baseline'}) @ {a.period_ns:g} ns", flush=True)
            rtl, st, err = measure(a, p, out, fakeram, before, mw, tm)
            res[p["name"]] = (st, err)
            if err:
                print(f"{p['name']}: {err}", flush=True)
            last_rtl = rtl
        last = pts[-1] if pts else None
        pl = res.get(last["name"], ({}, ""))[0].get("place") if last else None
        if a.noise_variants > 0 and last_rtl is not None and pl is not None and pl.ok:
            noise.append(("chính", pl.fmax_mhz))
            for name, ov in [v for v in D.noise_variants(a.orfs_vars) if v[0] != "rerun"][:a.noise_variants]:
                print(f"\n-- nhiễu {last['name']} {name}", flush=True)
                _prep, st = D.run_orfs_point(a, out / "runs" / f"noise_{name}", last_rtl, fakeram, ov,
                                             f"noise:{name}", "place", mw, tm, fatal=False)
                r = st.get("place")
                noise.append((name, r.fmax_mhz if r is not None and r.ok else None))
    except SystemExit as e:
        print(f"\nDỪNG: {e}", flush=True)
        raise
    except Exception:
        traceback.print_exc()
        raise
    finally:
        mw.stop()
        D.write(out / "summary.md", summary_md(a, pts, res, noise, tm, mw, t0))
        if pts:
            D.prune_orfs(a, [pts[-1]["name"]])
        print(f"\nKết quả: {out}/summary.md, {out}/results.csv", flush=True)
        stop_collector()
        stop_cache()


if __name__ == "__main__":
    main()
