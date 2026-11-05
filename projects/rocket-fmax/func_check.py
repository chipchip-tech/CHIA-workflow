"""Functional checks of rocket-chip patches beyond the loop's gate, against the baseline (and Spike).

For the baseline and each --patch NAME=PATH, on the cluster of cluster.yaml:
  1. build RocketConfig (Verilator) with the patch applied;
  2. every riscv-tests ISA test of SUITES, physical (-p-) and virtual-memory (-v-) variants: the gate only
     runs rv64ui/um/ua/uf/ud -p-; added here: rv64uc (compressed), rv64si/rv64mi (traps, interrupts, CSRs),
     Zba/Zbb/Zbs and the -v- variants;
  3. the programs of functest/ (built once with the image's gcc, run once on Spike as reference):
     rand_diff (FP, mul/div, loads used at once, AMOs; directed random), rand_zbb (bit manipulation; only
     compared when the baseline passes rv64uzbb), cebreak_umode (C.EBREAK in U/S-mode: the suspected bug
     of asic-7), mmio_load (sub-word uncached loads used at once). Each prints "FT:" lines; a point must
     print the same lines and exit code as the baseline (and as Spike);
  4. commit trace (+verbose) of the benchmarks of --trace-bench: the committed (pc, instruction) stream and,
     per register, the sequence of values written, must equal the baseline's. Timing differs between
     builds, so the comparison stops at the second read of a cycle/time counter (the end of the measured
     region of the riscv-tests benchmarks) and the counter values themselves are masked.

Outputs in --out-dir/<run-name>/: summary.md, prep/ (functest build log, Spike output, ELFs),
runs/<point>/{point.patch, build.log (on failure), result.json, traces/<bench>.txt.gz}.
A point with a result.json is not rerun (--force to rerun).

  cd ~/CHIA-workflow/projects/rocket-fmax
  chia job submit --no-wait --submission-id func-asic7 --working-dir . -- \\
    python func_check.py --run-name func-asic7 \\
      --patch asic-6=~/rocket-fmax-v2/asic-6/patches/best.patch \\
      --patch asic-7=~/rocket-fmax-v2/asic-7/patches/best.patch
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import ray

import fmax_nodes as N
import rocket_fmax as R
from chia.base.ChiaFunction import ChiaFunction, get

FT_DIR = Path(__file__).resolve().parent / "functest"
SUITES = ("rv64ui", "rv64um", "rv64ua", "rv64uf", "rv64ud", "rv64uc", "rv64si", "rv64mi",
          "rv64uzba", "rv64uzbb", "rv64uzbs")
# name -> (sources, -march, spike --isa)
FUNCTESTS = {
    "rand_diff": (("crt.S", "rand_diff.c"), "rv64gc", "rv64gc"),
    "rand_zbb": (("crt.S", "rand_zbb.c"), "rv64gc_zba_zbb_zbs", "rv64gc_zba_zbb_zbs"),
    "cebreak_umode": (("cebreak_umode.S",), "rv64gc", "rv64gc"),
    "mmio_load": (("mmio_load.S",), "rv64gc", "rv64gc"),
}
ZB_TESTS = "rand_zbb"
COUNTER_CSRS = {0xB00, 0xC00, 0xC01}          # mcycle, cycle, time (instret is the same on every build)
ISA_CYCLES, FT_CYCLES, TRACE_CYCLES = 20_000_000, 50_000_000, 200_000_000


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-name", required=True)
    p.add_argument("--patch", action="append", default=[], metavar="NAME=PATH",
                   help="a rocket-chip patch (repeatable); the baseline is always the first point")
    p.add_argument("--iters", type=int, default=2048, help="iterations of rand_diff / rand_zbb")
    p.add_argument("--seed", default="0x9E3779B97F4A7C15", help="seed of rand_diff / rand_zbb")
    p.add_argument("--trace-bench", nargs="*", default=list(N.BENCHMARKS),
                   help="benchmarks whose commit trace is compared (none: no trace)")
    p.add_argument("--exclude-isa", nargs="*", default=["rv64ui-p-ma_data", "rv64ui-v-ma_data"],
                   help="ISA tests not run (ma_data already fails on the baseline)")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3 * 3600)
    p.add_argument("--out-dir", default="~/rocket-fmax-v2")
    p.add_argument("--force", action="store_true", help="rerun points that already have a result.json")
    a = p.parse_args()
    a.out_dir = os.path.expanduser(a.out_dir)
    return a


def points(a) -> list[dict]:
    out = [{"name": "baseline", "patch": ""}]
    for spec in a.patch:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path or name == "baseline":
            raise SystemExit(f"--patch {spec!r}: expected NAME=PATH (NAME other than baseline)")
        text = Path(path).expanduser().read_text()
        if not text.strip():
            raise SystemExit(f"--patch {spec!r}: empty patch")
        out.append({"name": name, "patch": text})
    names = [p["name"] for p in out]
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate point names: {names}")
    return out


# ---------------------------------------------------------------------------
# chipyard: test ELFs, functest build, Spike reference
# ---------------------------------------------------------------------------

def ft_lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip().startswith("FT:")]


@ChiaFunction(resources={"chipyard": 1}, max_retries=0)
def prepare_tests(sources: dict, iters: int, seed: str, suites: tuple, bench: tuple, exclude: tuple) -> dict:
    """ISA test and benchmark ELFs of the image, the functest ELFs (gcc of the image) and their Spike
    output. Returns {"isa", "bench", "ft": {name: bytes}, "spike": {name: {rc, lines, tail}}, "log"}."""
    riscv = os.environ.get("RISCV") or f"{N.CHIPYARD}/.conda-env/riscv-tools"
    root = f"{riscv}/riscv64-unknown-elf/share/riscv-tests"
    isa = {}
    for name in sorted(os.listdir(f"{root}/isa")):
        if name.endswith(".dump") or name in exclude:
            continue
        if any(name.startswith(f"{s}-{v}-") for s in suites for v in ("p", "v")):
            with open(f"{root}/isa/{name}", "rb") as f:
                isa[name] = f.read()
    elfs = {}
    for name in bench:
        with open(f"{root}/benchmarks/{name}.riscv", "rb") as f:
            elfs[name] = f.read()
    gcc, spike = f"{riscv}/bin/riscv64-unknown-elf-gcc", f"{riscv}/bin/spike"
    work = tempfile.mkdtemp(prefix="rocket_fmax_ft_")
    log, ft, ref = [], {}, {}
    try:
        for n, text in sources.items():
            with open(os.path.join(work, n), "wb") as f:
                f.write(text)
        for name, (srcs, march, isa_str) in FUNCTESTS.items():
            cmd = [gcc, f"-march={march}", "-mabi=lp64d", "-mcmodel=medany", "-static", "-nostdlib",
                   "-nostartfiles", "-ffreestanding", "-O1", "-mno-relax", "-T", "link.ld",
                   f"-DITERS={iters}", f"-DSEED={seed}ull", *srcs, "-o", f"{name}.riscv"]
            r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=600)
            log.append(f"$ {' '.join(cmd)}\nrc={r.returncode}\n{r.stdout}{r.stderr}")
            if r.returncode != 0:
                continue
            with open(os.path.join(work, f"{name}.riscv"), "rb") as f:
                ft[name] = f.read()
            try:
                s = subprocess.run([spike, f"--isa={isa_str}", f"{name}.riscv"], cwd=work,
                                   capture_output=True, text=True, timeout=1800)
                ref[name] = {"rc": s.returncode, "lines": ft_lines(s.stdout),
                             "tail": (s.stdout + s.stderr)[-2000:]}
            except subprocess.TimeoutExpired:
                ref[name] = {"rc": None, "lines": [], "tail": "spike: timeout (1800 s)"}
            log.append(f"$ spike --isa={isa_str} {name}.riscv\nrc={ref[name]['rc']}\n{ref[name]['tail']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"isa": isa, "bench": elfs, "ft": ft, "spike": ref, "log": "\n\n".join(log)}


# ---------------------------------------------------------------------------
# verilator_run: one simulation, reduced on the worker
# ---------------------------------------------------------------------------

_TRACE_RE = re.compile(r"^C\d+:\s+\d+\s+\[(\d)\]\s+pc=\[([0-9a-f]+)\]\s+W\[r\s*(\d+)=([0-9a-f]+)\]\[(\d)\]"
                       r".*?inst=\[([0-9a-f]+)\]")


def trace_streams(out: str) -> tuple[str, int, bool]:
    """The committed (pc inst) lines and, per register, the values written, from Rocket's +verbose
    trace, from the first commit at or above 0x80000000 (the program, not the boot ROM) to the second
    read of a cycle/time counter (whose value is written as "C"). Returns (text, commits, cut)."""
    commits, writes, reads, started, cut = [], {}, 0, False, False
    for line in out.splitlines():
        m = _TRACE_RE.match(line)
        if not m:
            continue
        valid, pc, rd, wdata, wen, inst = m.groups()
        counter = False
        if valid == "1":
            if not started and int(pc, 16) >= 0x80000000:
                started = True
            if started:
                iv = int(inst, 16)
                counter = (iv & 0x7F) == 0x73 and ((iv >> 12) & 7) in (1, 2, 3, 5, 6, 7) and (iv >> 20) in COUNTER_CSRS
                commits.append(f"{pc} {inst}")
        if started and wen == "1" and rd != "0":
            writes.setdefault(int(rd), []).append("C" if counter else wdata)
        if counter:
            reads += 1
            if reads >= 2:
                cut = True
                break
    text = "\n".join(commits) + "\n#writes\n" + "\n".join(
        f"r{r}: " + " ".join(v) for r, v in sorted(writes.items())) + "\n"
    return text, len(commits), cut


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def sim_one(artifact, fname: str, elf: bytes, kind: str) -> dict:
    """kind: "isa" (pass/fail), "ft" (FT: lines and exit code), "trace" (+verbose, reduced streams)."""
    from chia.chipyard.verilator_run_node import VerilatorRunNode
    cycles, timeout = {"isa": (ISA_CYCLES, 1800), "ft": (FT_CYCLES, 3600), "trace": (TRACE_CYCLES, 4 * 3600)}[kind]
    sim = VerilatorRunNode()
    r = VerilatorRunNode.run(sim, artifact, elf, fname, N.SIM_WORK_DIR, timeout_cycles=cycles,
                             timeout_seconds=timeout, verbose=(kind == "trace"))
    tail = f"returncode={r.returncode}\n[log]\n{(r.log or '')[-1500:]}\n[out]\n{(r.out or '')[-1500:]}"
    res = {"rc": r.returncode, "tail": tail}
    if kind == "isa":
        res["ok"] = r.returncode == 0 and "FAILED" not in (r.out or "")[-4000:] and "FAILED" not in (r.log or "")[-4000:]
    elif kind == "ft":
        res["lines"] = ft_lines(r.log or "")
    else:
        text, n, cut = trace_streams(r.out or "")
        res.update(ok=r.returncode == 0, commits=n, cut=cut,
                   digest=hashlib.sha256(text.encode()).hexdigest()[:16], gz=gzip.compress(text.encode()))
        res["tail"] = f"returncode={r.returncode}\n[log]\n{(r.log or '')[-1500:]}"
    return res


# ---------------------------------------------------------------------------
# head
# ---------------------------------------------------------------------------

def run_point(a, p: dict, snap: dict, tests: dict, d: Path) -> dict:
    t0 = time.time()
    print(get(N.reset_rocket.chia_remote(snap["rocket_head"], p["patch"])) or "rocket-chip clean", flush=True)
    art = R.build(a, f"func-{p['name']}")
    if not art.success:
        R.save_build(d, art)
        return {"ok": False, "error": "build failed:\n" + R.build_errors(art)[-3000:], "minutes": (time.time() - t0) / 60}
    print(f"{p['name']}: build ok ({(time.time() - t0) / 60:.1f} min), simulating ...", flush=True)
    ref = ray.put(art)
    refs = {("isa", n): sim_one.chia_remote(ref, n, e, "isa") for n, e in tests["isa"].items()}
    refs.update({("ft", n): sim_one.chia_remote(ref, f"{n}.riscv", e, "ft") for n, e in tests["ft"].items()})
    refs.update({("trace", n): sim_one.chia_remote(ref, f"{n}.riscv", e, "trace") for n, e in tests["bench"].items()})
    res = {"ok": True, "isa": {}, "ft": {}, "trace": {}, "fail_tails": {}}
    for (kind, n), r in refs.items():
        try:
            v = get(r)
        except Exception as e:                                              # noqa: BLE001
            v = {"rc": None, "ok": False, "lines": [], "tail": f"{type(e).__name__}: {e}"[:2000]}
        if kind == "isa":
            res["isa"][n] = v["ok"]
            if not v["ok"]:
                res["fail_tails"][n] = v["tail"]
        elif kind == "ft":
            res["ft"][n] = {"rc": v["rc"], "lines": v.get("lines", [])}
            res["fail_tails"][n] = v["tail"]
        else:
            if v.get("gz"):
                (d / "traces").mkdir(parents=True, exist_ok=True)
                (d / "traces" / f"{n}.txt.gz").write_bytes(v["gz"])
            res["trace"][n] = {k: v.get(k) for k in ("ok", "rc", "commits", "cut", "digest")}
            if not v.get("ok"):
                res["fail_tails"][f"trace:{n}"] = v["tail"]
    res["minutes"] = (time.time() - t0) / 60
    return res


def first_diff(base: Path, other: Path, ctx: int = 4) -> str:
    """Where two reduced traces part: the commit index and the lines around it, or the first register
    whose written values differ."""
    a = gzip.decompress(base.read_bytes()).decode().split("\n#writes\n")
    b = gzip.decompress(other.read_bytes()).decode().split("\n#writes\n")
    ca, cb = a[0].splitlines(), b[0].splitlines()
    for i in range(max(len(ca), len(cb))):
        if i >= len(ca) or i >= len(cb) or ca[i] != cb[i]:
            lo = max(0, i - ctx)
            return (f"commit {i} khác (baseline {len(ca)} lệnh, điểm này {len(cb)}):\n  baseline: "
                    + " | ".join(ca[lo:i + 2]) + "\n  điểm này: " + " | ".join(cb[lo:i + 2]))
    wa = dict(l.split(": ", 1) if ": " in l else (l.rstrip(":"), "") for l in a[1].splitlines() if l)
    wb = dict(l.split(": ", 1) if ": " in l else (l.rstrip(":"), "") for l in b[1].splitlines() if l)
    out = []
    for r in sorted(set(wa) | set(wb), key=lambda x: int(x[1:])):
        va, vb = wa.get(r, "").split(), wb.get(r, "").split()
        if va != vb:
            j = next((k for k in range(max(len(va), len(vb))) if k >= len(va) or k >= len(vb) or va[k] != vb[k]), 0)
            out.append(f"{r}: lần ghi thứ {j} khác: baseline {va[j] if j < len(va) else '-'}, "
                       f"điểm này {vb[j] if j < len(vb) else '-'}")
    return "chuỗi lệnh giống nhau; giá trị ghi khác: " + "; ".join(out[:5]) if out else "giống nhau"


def verdict(base: dict, r: dict, spike: dict, zbb_on: bool) -> dict:
    new_fail = sorted(n for n, ok in r["isa"].items() if not ok and base["isa"].get(n))
    ft = {}
    for n, v in r["ft"].items():
        b, s = base["ft"].get(n), spike.get(n)
        if n == ZB_TESTS and not zbb_on:
            ft[n] = "bỏ qua (baseline không pass rv64uzbb: Zbb không bật)"
            continue
        same_base = b is not None and (v["rc"], v["lines"]) == (b["rc"], b["lines"])
        same_spike = s is not None and v["lines"] == s["lines"] and (v["rc"] == 0) == (s["rc"] == 0)
        ft[n] = ("khớp baseline" if same_base else "KHÁC baseline") + (", khớp Spike" if same_spike else ", khác Spike")
    return {"new_fail": new_fail, "ft": ft}


def summary(a, pts: list[dict], tests: dict, out: Path) -> str:
    base = pts[0]["res"]
    spike = tests["spike"]
    zb = [ok for n, ok in base.get("isa", {}).items() if n.startswith("rv64uzbb")]
    zbb_on = bool(base.get("ok")) and bool(zb) and all(zb)
    lines = [f"# Kiểm chức năng: {a.run_name}", "",
             f"So với baseline (và Spike cho functest/). ISA: {len(tests['isa'])} test của "
             f"{', '.join(SUITES)} (-p- và -v-; trừ {', '.join(a.exclude_isa)}). rand_diff/rand_zbb: "
             f"{a.iters} vòng, seed {a.seed}. Trace: {', '.join(a.trace_bench) or 'không'}.", "",
             "## Spike (tham chiếu của functest/)", "",
             "| test | exit | dòng FT cuối |", "|---|---|---|"]
    for n, s in spike.items():
        lines.append(f"| `{n}` | {s['rc']} | `{(s['lines'] or [''])[-1]}` |")
    missing = sorted(set(FUNCTESTS) - set(tests["ft"]))
    if missing:
        lines += ["", f"Không build được: {', '.join(missing)} (xem prep/build.log)."]
    lines += ["", "## Kết quả từng điểm", ""]
    for p in pts:
        r = p["res"]
        lines += [f"### `{p['name']}`", ""]
        if not r.get("ok"):
            lines += [f"Lỗi: {r.get('error', '')[:1500]}", ""]
            continue
        nf = sorted(n for n, ok in r["isa"].items() if not ok)
        lines.append(f"- ISA: {len(r['isa']) - len(nf)}/{len(r['isa'])} pass"
                     + (f"; fail: {', '.join(nf)}" if nf else ""))
        if p is pts[0]:
            lines.append(f"- Zbb: {'bật (rv64uzbb pass)' if zbb_on else 'không bật hoặc lỗi (rv64uzbb fail trên baseline)'}")
            for n, v in r["ft"].items():
                s = spike.get(n)
                same = s is not None and v["lines"] == s["lines"] and (v["rc"] == 0) == (s["rc"] == 0)
                lines.append(f"- `{n}`: exit {v['rc']}, {'khớp Spike' if same else 'KHÁC Spike'}")
            for n, t in r["trace"].items():
                lines.append(f"- trace `{n}`: {t['commits']} lệnh, {'đến lần đọc counter thứ hai' if t['cut'] else 'cả chương trình (không gặp counter lần hai)'}"
                             + ("" if t["ok"] else ", sim lỗi"))
        else:
            v = verdict(base, r, spike, zbb_on)
            lines.append(f"- ISA fail mới so với baseline: {', '.join(v['new_fail']) or 'không'}")
            for n, s in v["ft"].items():
                lines.append(f"- `{n}` (exit {r['ft'][n]['rc']}): {s}")
            for n, t in r["trace"].items():
                bt = base["trace"].get(n)
                if not t["commits"]:
                    lines.append(f"- trace `{n}`: không có dòng commit (sim không in trace?)")
                elif bt and t["digest"] == bt["digest"]:
                    lines.append(f"- trace `{n}`: giống baseline ({t['commits']} lệnh)")
                else:
                    bp, cp = out / "runs" / "baseline" / "traces" / f"{n}.txt.gz", out / "runs" / p["name"] / "traces" / f"{n}.txt.gz"
                    diff = first_diff(bp, cp) if bp.exists() and cp.exists() else "thiếu file trace"
                    lines.append(f"- trace `{n}`: **KHÁC baseline**: {diff}")
        lines.append(f"- {r.get('minutes', 0):.1f} phút")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    R.write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)
    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    pts = points(a)
    srcs = {f.name: f.read_bytes() for f in FT_DIR.iterdir() if f.is_file()}
    tests = get(prepare_tests.chia_remote(srcs, a.iters, a.seed, SUITES, tuple(a.trace_bench), tuple(a.exclude_isa)))
    R.write(out / "prep" / "build.log", tests["log"])
    R.write(out / "prep" / "spike.json", json.dumps(tests["spike"], indent=2))
    for n, e in tests["ft"].items():
        (out / "prep" / f"{n}.riscv").write_bytes(e)
    print(f"tests: {len(tests['isa'])} ISA, functest {sorted(tests['ft'])}, trace {sorted(tests['bench'])}; Spike: "
          + ", ".join(f"{n} exit {s['rc']}" for n, s in tests["spike"].items()), flush=True)
    snap = None
    for p in pts:
        d = out / "runs" / p["name"]
        R.write(d / "point.patch", p["patch"])
        old = d / "result.json"
        if old.exists() and not a.force:
            p["res"] = json.loads(old.read_text())
            print(f"{p['name']}: đã có kết quả ({old}), bỏ qua (--force để chạy lại)", flush=True)
            continue
        snap = snap or get(N.tree_snapshot.chia_remote())
        print(f"\n{p['name']}: build ...", flush=True)
        try:
            p["res"] = run_point(a, p, snap, tests, d)
        except Exception as e:                                               # noqa: BLE001
            p["res"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:2000]}
        R.write(d / "result.json", json.dumps(p["res"], indent=2, ensure_ascii=False))
        R.write(out / "summary.md", summary(a, [q for q in pts if "res" in q], tests, out))
    if snap:
        get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
    R.write(out / "summary.md", summary(a, pts, tests, out))
    print("\n" + (out / "summary.md").read_text())
    print(f"done: {out}")


if __name__ == "__main__":
    main()
