"""One Vivado post-route measurement of RocketTile for a few patches, side by side.

No agent, no gate: for each point (the baseline and/or --patch NAME=PATH) build RocketConfig,
extract RocketTile and run vivado_nodes.vivado_impl (synth_design, then place + route with each
of --strategies, the best post-route worst slack wins), xc7a100tcsg324-1 (Arty A7-100T) at
10 ns, out-of-context, SRAM models mapped to BRAM / LUTRAM. Same flow and cache tags as the
Vivado loop of 30/10/2026 (fmax-r4, fmax-r5; commit d2a1ac0), so a point measured back then
(e.g. fmax-r4/patches/best.patch, 63.61 MHz) is served from the cache instead of rerun.

Outputs in --out-dir/<run-name>/: summary.md (post-route timing and resources of every point,
deltas against the first point, endpoint families), vivado_impl.csv (one row per strategy),
runs/<point>/impl_<strategy>/ (Vivado reports).

Needs the workers chipyard and vivado of cluster.yaml. Builds run first, then place + route one
point at a time (one Vivado at a time: 23 GB RAM). Leaves rocket-chip clean at its HEAD.

  cd ~/CHIA-workflow/projects/rocket-fmax
  chia job submit --no-wait --submission-id viv-asic6 --working-dir . -- \
    python vivado_check.py --run-name viv-asic6 --baseline \
      --patch fmax-r4=~/rocket-fmax-v2/fmax-r4/patches/best.patch \
      --patch asic-6=~/rocket-fmax-v2/asic-6/patches/best.patch
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import ray

import fmax_nodes as N
import rocket_fmax as R
import vivado_nodes as V
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache

IMPL_TAG = "vivimpl4"        # tag format of the Vivado loop (d2a1ac0): cached results stay usable
IMPL_FIELDS = ["point", "strategy", "best", "synth_fmax_mhz", "place_slack_ns", "fmax_mhz", "slack_ns", "tns_ns",
               "failing", "luts", "lutram", "ffs", "brams", "dsps", "minutes", "critical_path", "error"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--baseline", action="store_true", help="also measure the unmodified tree (first point)")
    p.add_argument("--patch", action="append", default=[], metavar="NAME=PATH",
                   help="a rocket-chip patch to measure (repeatable; order = order in the summary)")
    p.add_argument("--period-ns", type=float, default=10.0)
    p.add_argument("--vivado-part", default="xc7a100tcsg324-1")
    p.add_argument("--vivado-threads", type=int, default=8, help="maxThreads of synth_design")
    p.add_argument("--strategies", nargs="+", default=["explore", "extratiming"],
                   choices=sorted(V.IMPL_STRATEGIES))
    p.add_argument("--impl-threads", type=int, default=4, help="maxThreads of each place + route run")
    p.add_argument("--impl-parallel", type=int, default=1, help="place + route runs at once")
    p.add_argument("--out-dir", default="~/rocket-fmax-v2")
    p.add_argument("--cache-dir", default="~/chia_cache")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3600)
    a = p.parse_args()
    a.out_dir, a.cache_dir = os.path.expanduser(a.out_dir), os.path.expanduser(a.cache_dir)
    if not a.baseline and not a.patch:
        p.error("nothing to measure: give --baseline and/or --patch NAME=PATH")
    return a


def points(a) -> list[dict]:
    out = [{"name": "baseline", "patch": "", "prefix": "baseline"}] if a.baseline else []
    for spec in a.patch:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path:
            raise SystemExit(f"--patch {spec!r}: expected NAME=PATH")
        text = Path(path).expanduser().read_text()
        if not text.strip():
            raise SystemExit(f"--patch {spec!r}: empty patch")
        out.append({"name": name, "patch": text, "prefix": f"baseline_start_{R.patch_hash(text)}", "path": path})
    names = [p["name"] for p in out]
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate point names: {names}")
    return out


def impl_tag(a, prefix: str) -> str:
    ptag = f"{a.period_ns:g}".replace(".", "p")
    return f"{prefix}_{IMPL_TAG}_{a.vivado_part}_p{ptag}_" + "-".join(a.strategies)


def save_impl(out: Path, label: str, res) -> None:
    """runs/<label>/impl_*/ reports and vivado_impl.csv rows (one per strategy, plus synth)."""
    d, syn = out / "runs" / label, res.synth
    rows = []
    for n, r in ([("synth", syn)] if syn is not None else []) + list(res.runs.items()):
        for name, text in (("vivado_paths.rpt", r.paths), ("vivado_timing_summary.rpt", r.summary),
                           ("vivado_utilization.rpt", r.utilization), ("vivado_families.txt", r.families),
                           ("vivado_endpoints.txt", r.endpoints), ("vivado_log_tail.txt", r.log_tail)):
            R.write(d / f"impl_{n}" / name, text)
        rows.append({"strategy": n, "best": n == res.best if n != "synth" else "",
                     "place_slack_ns": R.fmt(res.place_slack.get(n), 3), "fmax_mhz": R.fmt(r.fmax_mhz, 2),
                     "slack_ns": R.fmt(r.worst_slack_ns, 3), "tns_ns": R.fmt(r.tns_ns, 1),
                     "failing": r.failing_endpoints if r.failing_endpoints is not None else "",
                     "luts": r.luts or "", "lutram": r.lutram if r.lutram is not None else "", "ffs": r.ffs or "",
                     "brams": r.brams if r.brams is not None else "", "dsps": r.dsps if r.dsps is not None else "",
                     "minutes": f"{r.seconds / 60:.1f}", "critical_path": f"{r.crit_start} -> {r.crit_end}",
                     "error": r.error + (f" (returncodes {r.returncodes})" if len(r.returncodes) > 1 else "")})
    for row in rows or [{"error": res.error}]:
        R.append_csv(out / "vivado_impl.csv", IMPL_FIELDS,
                     {"point": label, "synth_fmax_mhz": R.fmt(syn.fmax_mhz, 2) if syn is not None else "", **row})
    for n, r in res.runs.items():
        print(f"  {n:12s} place {R.fmt(res.place_slack.get(n), 3)} ns -> route "
              + (f"{r.worst_slack_ns:.3f} ns ({r.fmax_mhz:.2f} MHz)" if r.success else f"lỗi: {r.error}")
              + f", {r.seconds / 60:.1f} min", flush=True)


def _delta(x, ref, nd, pct=False) -> str:
    if x is None or ref is None:
        return ""
    s = f" ({x - ref:+.{nd}f}" + (f", {(x / ref - 1) * 100:+.1f} %" if pct and ref else "") + ")"
    return s


def summary(a, pts: list[dict]) -> str:
    ok = [p for p in pts if p["impl"].success]
    ref = ok[0]["impl"].result if ok else None
    hdr = ["điểm", "post-route MHz", "chiến lược", "WNS (ns)", "TNS (ns)", "endpoint âm", "synth MHz",
           "LUT", "LUTRAM", "FF", "BRAM", "DSP"]
    lines = [f"# Vivado post-route: {a.run_name}", "",
             f"{a.vivado_part}, {a.period_ns:g} ns, out-of-context, chiến lược {', '.join(a.strategies)} "
             f"(lấy chiến lược có worst slack tốt nhất). Chênh lệch so với điểm đầu tiên"
             + (f" (`{ok[0]['name']}`)." if ok else "."), "",
             "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for p in pts:
        res = p["impl"]
        if not res.success:
            lines.append(f"| `{p['name']}` | lỗi: {res.error[:200]} |" + " |" * (len(hdr) - 2))
            continue
        b, s = res.result, res.synth
        lines.append("| " + " | ".join([
            f"`{p['name']}`", f"{b.fmax_mhz:.2f}{_delta(b.fmax_mhz, ref.fmax_mhz, 2, True)}", res.best,
            f"{b.worst_slack_ns:.3f}", R.fmt(b.tns_ns, 1), str(b.failing_endpoints),
            R.fmt(s.fmax_mhz if s else None, 2),
            f"{b.luts}{_delta(b.luts, ref.luts, 0, True)}", f"{b.lutram}{_delta(b.lutram, ref.lutram, 0)}",
            f"{b.ffs}{_delta(b.ffs, ref.ffs, 0, True)}", f"{b.brams}{_delta(b.brams, ref.brams, 1)}",
            f"{b.dsps}{_delta(b.dsps, ref.dsps, 0)}"]) + " |")
    lines += ["", "## Đường găng", ""]
    for p in ok:
        b = p["impl"].result
        lines += [f"- `{p['name']}`: `{b.crit_start}` → `{b.crit_end}`"
                  + "; " + ", ".join(f"{n} {r.fmax_mhz:.2f} MHz" if r.success else f"{n} lỗi"
                                     for n, r in p["impl"].runs.items())]
    for p in ok[1:]:
        lines += ["", f"## Họ endpoint: `{ok[0]['name']}` → `{p['name']}`", "", "```",
                  V.compare_families(ok[0]["impl"].result.endpoints, p["impl"].result.endpoints), "```"]
    for p in ok:
        lines += ["", f"## Họ endpoint xấu nhất của `{p['name']}`", "", "```", p["impl"].result.families, "```"]
    return "\n".join(lines) + "\n"


def run(a, out: Path):
    pts = points(a)
    snap = get(N.tree_snapshot.chia_remote())
    if get(N.reset_rocket.chia_remote(snap["rocket_head"], "")).strip():
        raise SystemExit("rocket-chip is not clean after reset")
    try:
        for p in pts:      # builds first: a build next to Vivado (18 GB) would not fit in 23 GB of RAM
            p["tags"] = {"rtl": f"{p['prefix']}_{R.RTL_TAG}", "impl": impl_tag(a, p["prefix"])}
            hits = {k: R.cache_has(t) for k, t in p["tags"].items()}
            print(f"\n{p['name']}: " + ", ".join(f"{p['tags'][k]}={'HIT' if v else 'miss'}" for k, v in hits.items()),
                  flush=True)
            R.write(out / "runs" / p["name"] / "point.patch", p["patch"])
            art = None
            if not hits["impl"] and not hits["rtl"]:
                t0 = time.time()
                print(get(N.reset_rocket.chia_remote(snap["rocket_head"], p["patch"])) or "rocket-chip clean",
                      flush=True)
                art = R.build(a, p["name"])
                R.save_build(out / "runs" / p["name"], art)
                if not art.success:
                    raise SystemExit(f"{p['name']}: build failed:\n{R.build_errors(art)}")
                print(f"{p['name']}: build ok ({(time.time() - t0) / 60:.1f} min)", flush=True)
            p["rtl"] = None if hits["impl"] else get(N.extract_tile_rtl.chia_remote(art, _chia_tag=p["tags"]["rtl"]))
    finally:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
    for p in pts:
        t0 = time.time()
        ref = V.vivado_impl.chia_remote(p["rtl"], a.period_ns, a.vivado_part, tuple(a.strategies),
                                        a.vivado_threads, a.impl_threads, a.impl_parallel, _chia_tag=p["tags"]["impl"])
        try:
            p["impl"] = get(ref)
        except Exception as e:                                       # noqa: BLE001
            p["impl"] = V.VivadoImplResult(success=False, part=a.vivado_part, period_ns=a.period_ns,
                                           error=f"{type(e).__name__}: {e}"[:500])
        res = p["impl"]
        print(f"\n{p['name']}: place + route ({(time.time() - t0) / 60:.1f} min): "
              + (f"{res.result.fmax_mhz:.2f} MHz ({res.best}), LUT {res.result.luts}, FF {res.result.ffs}, "
                 f"BRAM {res.result.brams}, DSP {res.result.dsps}" if res.success else f"lỗi: {res.error}"),
              flush=True)
        save_impl(out, p["name"], res)
        R.write(out / "summary.md", summary(a, [q for q in pts if "impl" in q]))
    print("\n" + (out / "summary.md").read_text())


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    R.write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)
    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    start_cache(size=4, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(R.EXP_YAML))
    bypass = Bypass(yaml_path=str(R.EXP_YAML))
    for fn in ("extract_tile_rtl", "vivado_impl"):
        bypass.set_provider(fn, N.cache_provider)
        bypass.set_cond(fn, N.cache_hit_cond)
    try:
        run(a, out)
    finally:
        stop_cache()
    print(f"done: {out}")


if __name__ == "__main__":
    main()
