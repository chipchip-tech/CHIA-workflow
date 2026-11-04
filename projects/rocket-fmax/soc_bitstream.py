"""Bitstream of the whole SoC with Chipyard's FPGA flow, for a few rocket-chip patches. No board needed.

For each point (the baseline and/or --patch NAME=PATH), on the chipyard worker: apply the patch,
`make bitstream` in chipyard/fpga (SUB_PROJECT=arty100t: Arty A7-100T, RocketArty100TConfig =
RocketConfig + harness with DDR3, UART, JTAG; one clock for the whole SoC, 50 MHz by default,
--freq-mhz for another), then a second Vivado run on post_route.dcp for timing per clock and
the resources and timing of the RocketTile instance (vivado_nodes.soc_bitstream).

Unlike the out-of-context measurement of fmax-r4 / viv-asic6 (RocketTile alone, 10 ns, BUFG
clock), this is the real SoC at a fixed clock: Vivado stops optimizing once timing is met, so
the "Fmax ước tính" (1000 / (period - WNS)) is a lower bound when WNS is well above 0.

Outputs in --out-dir/<run-name>/: summary.md, soc.csv (one row per point),
runs/<point>/{Arty100THarness.bit, point.patch, make_log_tail.txt, result.json, reports/}.
A point whose result.json says ok is not rerun (--force to rerun).

Needs the chipyard worker of cluster.yaml with Vivado mounted (~/tools/Xilinx) and the Digilent
board files in ~/tools/Xilinx/vivado-boards. One point at a time; a point takes about 1-2 h.

  cd ~/CHIA-workflow/projects/rocket-fmax
  chia job submit --no-wait --submission-id soc-asic6 --working-dir . -- \\
    python soc_bitstream.py --run-name soc-asic6 --baseline \\
      --patch asic-6=~/rocket-fmax-v2/asic-6/patches/best.patch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import ray

import fmax_nodes as N
import rocket_fmax as R
import vivado_nodes as V
from chia.base.ChiaFunction import get

DEFAULT_MHZ = 50          # RocketArty100TConfig (WithArty100TTweaks default)
CSV_FIELDS = ["point", "ok", "freq_mhz", "core_clock", "wns_ns", "tns_ns", "failing", "fmax_est_mhz",
              "tile_slack_ns", "tile_fmax_est_mhz", "luts", "lutram", "ffs", "brams", "dsps",
              "tile_luts", "tile_lutram", "tile_ffs", "tile_brams", "tile_dsps", "bit_sha12", "minutes", "error"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--baseline", action="store_true", help="also build the unmodified tree (first point)")
    p.add_argument("--patch", action="append", default=[], metavar="NAME=PATH",
                   help="a rocket-chip patch (repeatable; order = order in the summary)")
    p.add_argument("--freq-mhz", type=int, default=DEFAULT_MHZ,
                   help="clock of the SoC; other than 50 writes a one-run config class in fpga/src (removed after)")
    p.add_argument("--threads", type=int, default=8, help="Vivado general.maxThreads")
    p.add_argument("--make-jobs", type=int, default=8)
    p.add_argument("--timeout-hours", type=float, default=5.0, help="per point")
    p.add_argument("--out-dir", default="~/rocket-fmax-v2")
    p.add_argument("--force", action="store_true", help="rerun points that already have a bitstream")
    a = p.parse_args()
    a.out_dir = os.path.expanduser(a.out_dir)
    if not a.baseline and not a.patch:
        p.error("nothing to build: give --baseline and/or --patch NAME=PATH")
    if not 10 <= a.freq_mhz <= 200:
        p.error("--freq-mhz: 10..200")
    return a


def points(a) -> list[dict]:
    out = [{"name": "baseline", "patch": ""}] if a.baseline else []
    for spec in a.patch:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path:
            raise SystemExit(f"--patch {spec!r}: expected NAME=PATH")
        text = Path(path).expanduser().read_text()
        if not text.strip():
            raise SystemExit(f"--patch {spec!r}: empty patch")
        out.append({"name": name, "patch": text})
    names = [p["name"] for p in out]
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate point names: {names}")
    return out


def fmax_est(period, slack):
    return 1000.0 / (period - slack) if period and slack is not None and period - slack > 0 else None


def core_clock(r: dict) -> dict | None:
    """The clock of the tile's worst endpoint (the SoC clock), else the clock with the most endpoints."""
    clocks = r.get("clocks") or []
    name = (r.get("tile_path") or {}).get("clock")
    for c in clocks:
        if c["name"] == name:
            return c
    return min(clocks, key=lambda c: c["wns_ns"]) if clocks else None


def row(name: str, r: dict, freq: int) -> dict:
    c, tp, u, tu = core_clock(r), r.get("tile_path") or {}, r.get("util") or {}, r.get("tile_util") or {}
    f = fmax_est(c["period_ns"], c["wns_ns"]) if c else None
    tf = fmax_est(tp.get("period_ns"), tp.get("slack_ns"))
    return {"point": name, "ok": r["ok"], "freq_mhz": freq, "core_clock": c["name"] if c else "",
            "wns_ns": R.fmt(c["wns_ns"], 3) if c else "", "tns_ns": R.fmt(c["tns_ns"], 1) if c else "",
            "failing": c["failing"] if c else "", "fmax_est_mhz": R.fmt(f, 2),
            "tile_slack_ns": R.fmt(tp.get("slack_ns"), 3), "tile_fmax_est_mhz": R.fmt(tf, 2),
            **{k: "" if u.get(k) is None else u[k] for k in ("luts", "lutram", "ffs", "brams", "dsps")},
            **{f"tile_{k}": "" if tu.get(k) is None else tu[k] for k in ("luts", "lutram", "ffs", "brams", "dsps")},
            "bit_sha12": r.get("bit_sha12", ""), "minutes": f"{r.get('minutes', 0):.1f}", "error": r.get("error", "")}


def _d(x, ref, pct=False) -> str:
    try:
        x, ref = float(x), float(ref)
    except (TypeError, ValueError):
        return ""
    return f" ({x - ref:+g}" + (f", {(x / ref - 1) * 100:+.1f} %" if pct and ref else "") + ")"


def summary(a, pts: list[dict]) -> str:
    rows = [row(p["name"], p["res"], a.freq_mhz) for p in pts]
    ok = [r for r in rows if r["ok"]]
    ref = ok[0] if ok else {}
    lines = [f"# SoC bitstream (Chipyard fpga/, arty100t): {a.run_name}", "",
             f"`{pts[0]['config']}`, xc7a100ticsg324-1L, clock SoC {a.freq_mhz} MHz, flow của fpga-shells "
             "(synth `-flatten_hierarchy rebuilt`, opt/place/phys_opt/route `Explore`). Fmax ước tính = 1000 / "
             "(chu kỳ − WNS): là cận dưới khi WNS dương (Vivado ngừng tối ưu khi đã đạt). Chênh lệch so với "
             "điểm đầu" + (f" (`{ok[0]['point']}`)." if ok else "."), "",
             "## Timing (clock SoC) và tài nguyên cả chip", "",
             "| điểm | bitstream | clock | WNS (ns) | TNS (ns) | endpoint âm | Fmax ước tính | slack tile | "
             "Fmax tile ước tính | LUT | LUTRAM | FF | BRAM | DSP | phút |", "|" + "---|" * 15]
    for r in rows:
        if not r["ok"] and not r["wns_ns"]:
            lines.append(f"| `{r['point']}` | lỗi: {r['error'][:300]} |" + " |" * 13)
            continue
        lines.append("| " + " | ".join([
            f"`{r['point']}`", f"có (`{r['bit_sha12']}`)" if r["ok"] else "không", f"`{r['core_clock']}`",
            str(r["wns_ns"]), str(r["tns_ns"]), str(r["failing"]),
            f"{r['fmax_est_mhz']}{_d(r['fmax_est_mhz'], ref.get('fmax_est_mhz'), True)}",
            str(r["tile_slack_ns"]), f"{r['tile_fmax_est_mhz']}{_d(r['tile_fmax_est_mhz'], ref.get('tile_fmax_est_mhz'), True)}",
            *[f"{r[k]}{_d(r[k], ref.get(k), k in ('luts', 'ffs'))}" for k in ("luts", "lutram", "ffs", "brams", "dsps")],
            r["minutes"]]) + " |")
    lines += ["", "## Tài nguyên của instance RocketTile trong SoC", "",
              "| điểm | LUT | LUTRAM | FF | BRAM | DSP |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join([f"`{r['point']}`"] + [
            f"{r['tile_' + k]}{_d(r['tile_' + k], ref.get('tile_' + k), k in ('luts', 'ffs'))}"
            for k in ("luts", "lutram", "ffs", "brams", "dsps")]) + " |")
    for p in pts:
        res = p["res"]
        if not res.get("clocks"):
            continue
        lines += ["", f"## Clock của `{p['name']}`", "", "| clock | chu kỳ (ns) | WNS (ns) | TNS (ns) | endpoint âm |",
                  "|---|---|---|---|---|"]
        lines += [f"| `{c['name']}` | {c['period_ns']:g} | {c['wns_ns']:.3f} | {c['tns_ns']:.1f} | {c['failing']} |"
                  for c in res["clocks"]]
        tp = res.get("tile_path") or {}
        if tp:
            lines += ["", f"Đường xấu nhất tới RocketTile (`{res.get('tile', '')}`): `{tp['start']}` → `{tp['end']}`, "
                          f"slack {tp['slack_ns']:.3f} ns."]
    ok_pts = [p for p in pts if p["res"].get("endpoints")]
    for p in ok_pts[1:]:
        lines += ["", f"## Họ endpoint trong tile: `{ok_pts[0]['name']}` → `{p['name']}`", "", "```",
                  V.compare_families(ok_pts[0]["res"]["endpoints"], p["res"]["endpoints"]), "```"]
    for p in ok_pts:
        lines += ["", f"## Họ endpoint xấu nhất trong tile của `{p['name']}`", "", "```",
                  V.endpoint_families(p["res"]["endpoints"]), "```"]
    return "\n".join(lines) + "\n"


def strip_tile(res: dict) -> dict:
    """Endpoint pins relative to the tile instance (shorter, same names as the out-of-context runs)."""
    tile = res.get("tile") or ""
    if tile and res.get("endpoints"):
        res["endpoints"] = res["endpoints"].replace(tile + "/", "")
    return res


def save(out: Path, p: dict, res: dict):
    d = out / "runs" / p["name"]
    if res.get("bit"):
        (d / f"{V.MODEL}.bit").parent.mkdir(parents=True, exist_ok=True)
        (d / f"{V.MODEL}.bit").write_bytes(res["bit"])
        res["bit_sha12"] = hashlib.sha256(res["bit"]).hexdigest()[:12]
    for name, text in (res.get("reports") or {}).items():
        R.write(d / "reports" / name, text)
    R.write(d / "make_log_tail.txt", res.get("make_log_tail", ""))
    if res.get("endpoints"):
        R.write(d / "reports" / "tile_endpoints.txt", res["endpoints"])
    keep = {k: v for k, v in res.items() if k not in ("bit", "reports", "make_log_tail")}
    R.write(d / "result.json", json.dumps(keep, indent=2))


def run(a, out: Path):
    pts = points(a)
    config, scala = ("RocketArty100TConfig", "") if a.freq_mhz == DEFAULT_MHZ else V.freq_config(a.freq_mhz)
    snap = None
    for p in pts:
        p["config"] = config
        d = out / "runs" / p["name"]
        R.write(d / "point.patch", p["patch"])
        old = d / "result.json"
        if old.exists() and not a.force:
            prev = json.loads(old.read_text())
            if prev.get("ok") and prev.get("config") == config:
                print(f"\n{p['name']}: đã có bitstream ({old}), bỏ qua (--force để chạy lại)", flush=True)
                p["res"] = prev
                continue
        if snap is None:
            snap = get(N.tree_snapshot.chia_remote())
        print(f"\n{p['name']}: make bitstream ({config}) ...", flush=True)
        t0 = time.time()
        try:
            res = get(V.soc_bitstream.chia_remote(snap["rocket_head"], p["patch"], config, scala, a.threads,
                                                  a.make_jobs, int(a.timeout_hours * 3600)))
        except Exception as e:                                       # noqa: BLE001
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:1000], "minutes": (time.time() - t0) / 60}
        res["config"] = config
        p["res"] = strip_tile(res)
        save(out, p, res)
        r = row(p["name"], res, a.freq_mhz)
        print(f"{p['name']}: {r['minutes']} min, bitstream {'có' if res['ok'] else 'không'}, "
              f"clock {r['core_clock']} WNS {r['wns_ns']} ns (Fmax ước tính {r['fmax_est_mhz']} MHz), "
              f"LUT {r['luts']}, FF {r['ffs']}, BRAM {r['brams']}, DSP {r['dsps']}"
              + (f"\n  lỗi: {res['error']}" if res.get("error") else ""), flush=True)
        if not res["ok"]:
            print("  make log (cuối):\n" + "\n".join((res.get("make_log_tail") or "").splitlines()[-40:]), flush=True)
        R.append_csv(out / "soc.csv", CSV_FIELDS, r)
        R.write(out / "summary.md", summary(a, [q for q in pts if "res" in q]))
    R.write(out / "summary.md", summary(a, pts))
    print("\n" + (out / "summary.md").read_text())


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    R.write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)
    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    run(a, out)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
