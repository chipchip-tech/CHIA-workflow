"""Ascon-Hash256 RoCC secure-boot loop on CHIA: agent edit -> build -> 2 tests.

Per iteration N (tags "<run_id>/iterN/..."):
  1. agent  : OpenCode edits generators/chipyard through a BashTool on the
              chipyard container, then the tool is stopped (frees the slot).
              --fake-llm replaces the LLM with a canned reply (milestone M1).
  2. collect: git diff + untracked files of generators/chipyard, packed into
              bytes and written to <out-dir>/iterN/ on the host.
  3. build  : ChiselBuildNode(<config>). A failure goes into the next prompt.
  4. tests  : boot_good and boot_tamper, one after the other on the single
              verilator_run slot. Their verdict/log goes into the next prompt.
Stops as soon as both tests pass.

Before the loop: AsconCoreV4.sv is written into the chipyard container and
linted, and the two test ELFs are compiled (htif_nano.specs).

Cache/replay (loop/exp.yaml): agent turns, tree snapshots, builds and
compiles are cached by _chia_tag on the head. Re-running with the same
--run-id replays the iterations that completed (and restores their tree into
the container); compile tags hash the C sources, so editing boot.c recompiles
without rebuilding hardware.

Run from projects/ascon-secureboot (all paths absolute: --working-dir is a
temporary copy made by Ray):
    chia job submit --working-dir loop -- python ascon_loop.py \
        --project-dir $PWD --out-dir $PWD/out/m1 --run-id m1 \
        --config RocketConfig --sw-only --fake-llm --iters 1

Needs workers advertising: chipyard, verilator_run, opencode_creds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time

import ray

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.bypass import Bypass
from chia.base.cache import get_active_cache, start_cache, stop_cache
from chia.base.tools.BashTool import BashTool
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.models.opencode import OpenCodeLLM, OpenCodeQueryResult
from chia.trace.profiler import start_collector

# Paths inside the containers. The ASCON_* environment overrides exist only for
# the local smoke test (tests/smoke_local.py); the cluster uses the defaults.
CHIPYARD_PATH = os.environ.get("ASCON_CHIPYARD_PATH", "/home/ray/chipyard")
SIM_WORK_DIR = os.environ.get("ASCON_SIM_WORK_DIR", "/home/ray/ascon-sim")
RISCV_GCC = f"{CHIPYARD_PATH}/.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc"
AGENT_DIR = "generators/chipyard"                       # the only tree the agent may edit
VSRC_REL = f"{AGENT_DIR}/src/main/resources/vsrc/AsconCoreV4.sv"
CFLAGS = ["-O2", "-mcmodel=medany", "-static", "-specs=htif_nano.specs"]
# test name -> (expected verdict, extra cflags)
TESTS = {"boot_good": ("OK", []), "boot_tamper": ("FAIL", ["-DTAMPER"])}
MAX_UNTRACKED_BYTES = 2 * 1024 * 1024                   # skip larger untracked files
CSV_FIELDS = ["iter", "build_ok", "test", "boot_result", "digest_match",
              "cycles_sw", "cycles_rocc", "passed", "sim_rc", "config", "run_id"]

FAKE_REPLY = ("[fake-llm] Canned reply for milestone M1: no files were changed. "
              "The loop should now build the unmodified tree.")


# ---------------------------------------------------------------------------
# Nodes on the chipyard container
# ---------------------------------------------------------------------------

def _sh(cmd: str, cwd: str = CHIPYARD_PATH, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, executable="/bin/bash")
    return p.returncode, (p.stdout + p.stderr).strip()


# Tracked-file changes, top level (submodules excluded) and inside every
# initialized submodule ("<submodule>: XY path"). Untracked files inside
# submodules are left out: builds create those.
STATUS_CMD = ("git status --porcelain --ignore-submodules=all; "
              "git submodule foreach --quiet "
              "'git status --porcelain --untracked-files=no | sed \"s|^|$sm_path: |\"'")


def find_violations(status: str, baseline: set) -> list:
    """New status lines that touch submodules or generators/* outside AGENT_DIR."""
    bad = []
    for line in status.splitlines():
        if line in baseline or not line.strip():
            continue
        path = line[3:]
        if re.match(r"^\S+: ", line):          # "<submodule>: XY path"
            bad.append(line)
        elif path.startswith("generators/") and not path.startswith(AGENT_DIR):
            bad.append(line)
    return bad


@ChiaFunction(resources={"chipyard": 1})
def install_rtl(sv_text: str, sha256: str) -> dict:
    """Write AsconCoreV4.sv into the Chipyard tree, lint it, describe the tree."""
    path = os.path.join(CHIPYARD_PATH, VSRC_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(sv_text)
    with open(path, "rb") as f:
        sha_ok = hashlib.sha256(f.read()).hexdigest() == sha256
    rc_v, version = _sh("verilator --version")
    lint_rc, lint_log = _sh("verilator --lint-only -Wno-fatal -Wno-UNOPTFLAT "
                            f"--top-module AsconCoreV4 {path}")
    _, head = _sh("git rev-parse HEAD")
    _, branch = _sh("git rev-parse --abbrev-ref HEAD")
    _, status = _sh(STATUS_CMD)
    return {
        "path": path,
        "sha_ok": sha_ok,
        "verilator_version": version if rc_v == 0 else f"<verilator not found: {version}>",
        "lint_rc": lint_rc,
        "lint_ok": lint_rc == 0 and "%Error" not in lint_log,
        "lint_log": lint_log[-4000:],
        "git_head": head,
        "git_branch": branch,
        "git_status": status,
    }


@ChiaFunction(resources={"chipyard": 1})
def compile_program(name: str, files: dict, cflags: list) -> bytes:
    """Compile boot.c (+ headers in `files`) for bare-metal Rocket (HTIF)."""
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as tmp:
        for fname, text in files.items():
            with open(os.path.join(tmp, fname), "w", encoding="utf-8") as f:
                f.write(text)
        elf = os.path.join(tmp, f"{name}.riscv")
        proc = subprocess.run([RISCV_GCC, *cflags, "-I", tmp, os.path.join(tmp, "boot.c"),
                               "-o", elf], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"compile {name} {cflags} failed:\n{proc.stderr}")
        with open(elf, "rb") as f:
            return f.read()


@ChiaFunction(resources={"chipyard": 1})
def collect_tree(replay: bool = False) -> dict:
    """Snapshot the Chipyard tree: diffs vs HEAD + untracked files of AGENT_DIR.

    `replay` is not used here; the bypass condition reads it to decide whether
    this call may be served from the cache.
    """
    rc, head = _sh("git rev-parse HEAD")
    if rc != 0:
        return {"error": f"{CHIPYARD_PATH} is not a git checkout: {head}"}
    _, patch = _sh(f"git diff HEAD --binary -- {AGENT_DIR}")
    _, full_diff = _sh("git diff HEAD --binary --ignore-submodules=dirty")
    _, status = _sh(STATUS_CMD)
    _, listing = _sh(f"git ls-files --others --exclude-standard -- {AGENT_DIR}")
    untracked, skipped = [], []
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in filter(None, listing.splitlines()):
            full = os.path.join(CHIPYARD_PATH, rel)
            if not os.path.isfile(full):
                continue
            if os.path.getsize(full) > MAX_UNTRACKED_BYTES:
                skipped.append(rel)
                continue
            tar.add(full, arcname=rel)
            untracked.append(rel)
    return {
        "error": "",
        "head": head,
        "patch": patch + "\n" if patch else "",
        "full_diff": full_diff[:5_000_000],
        "status": status,
        "untracked": untracked,
        "skipped": skipped,
        "tar": buf.getvalue(),
    }


@ChiaFunction(resources={"chipyard": 1})
def restore_tree(snap: dict) -> str:
    """Put AGENT_DIR back into the state captured by collect_tree()."""
    log = []
    for cmd in (f"git checkout HEAD -- {AGENT_DIR}", f"git clean -fdq -- {AGENT_DIR}"):
        rc, out = _sh(cmd)
        log.append(f"$ {cmd} -> {rc} {out}")
    if snap["patch"]:
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            f.write(snap["patch"])
        rc, out = _sh(f"git apply --binary --whitespace=nowarn {f.name}")
        log.append(f"$ git apply -> {rc} {out}")
        if rc != 0:
            raise RuntimeError("\n".join(log))
    with tarfile.open(fileobj=io.BytesIO(snap["tar"]), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise RuntimeError(f"unsafe path in snapshot: {m.name}")
        tar.extractall(CHIPYARD_PATH)
    log.append(f"restored {len(snap['untracked'])} untracked files")
    return "\n".join(log)


# ---------------------------------------------------------------------------
# Bypass providers / conditions (conditions run in the driver)
# ---------------------------------------------------------------------------

REPLAY = {"enabled": True, "fake_llm": False, "iters": set()}


def _cache_has(tag) -> bool:
    return tag is not None and bool(get(get_active_cache().has.chia_remote(tag)))


def _iter_of(tag: str) -> str:
    return tag.split("/iter")[0] + "/iter" + tag.split("/iter")[1].split("/")[0]


def replay_from_cache(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    if not hit:
        raise KeyError(f"cache miss for tag {tag!r}")
    return value


def fake_llm(tag, data_path, *args, **kwargs):
    return OpenCodeQueryResult(result=FAKE_REPLY, returncode=0, stderr="",
                               stream_result="[fake-llm]", success=True)


def compile_cond(tag, data_path, *args, **kwargs):
    # Compile tags hash the sources and flags, so a hit is always valid.
    return REPLAY["enabled"] and _cache_has(tag)


def agent_cond(tag, data_path, *args, **kwargs):
    # Replay an agent turn only together with the tree it produced.
    return (REPLAY["enabled"] and _cache_has(tag)
            and _cache_has(tag.replace("/agent", "/collect")))


def collect_cond(tag, data_path, *args, **kwargs):
    return REPLAY["enabled"] and _iter_of(tag) in REPLAY["iters"] and _cache_has(tag)


def build_cond(tag, data_path, *args, **kwargs):
    # A cached build is valid only if the tree it was built from is the same:
    # the agent turn of this iteration was replayed, or no agent edits at all.
    same_tree = REPLAY["fake_llm"] or _iter_of(tag) in REPLAY["iters"]
    return REPLAY["enabled"] and same_tree and _cache_has(tag)


# ---------------------------------------------------------------------------
# Driver-side steps
# ---------------------------------------------------------------------------

def ask_agent(llm, prompt: str, tag: str):
    bash = BashTool("chipyard_bash", CHIPYARD_PATH, timeout_seconds=300,
                    task_options={"resources": {"chipyard": 1}})
    try:
        return get(llm.prompt.chia_remote(llm, prompt, tools=[bash], _chia_tag=tag))
    finally:
        bash.stop()  # frees the chipyard slot for collect/build


def parse_run(name: str, r, expected: dict, sw_only: bool) -> dict:
    text = (r.log or "") + "\n" + (r.out or "")

    def grab(pat):
        m = re.search(pat, text, re.MULTILINE)
        return m.group(1) if m else ""

    d_sw = grab(r"^DIGEST_SW=([0-9a-f]{64})\s*$")
    d_hw = grab(r"^DIGEST_HW=([0-9a-f]{64}|NA)\s*$")
    boot = grab(r"^BOOT (OK|FAIL)\s*$") or "NONE"
    want = expected["digest"][name]
    digest_match = d_sw == want and (sw_only or d_hw == want)
    passed = r.returncode == 0 and boot == TESTS[name][0] and digest_match
    return {
        "test": name, "boot_result": boot, "digest_match": int(digest_match),
        "cycles_sw": grab(r"^CYCLES_SW=(\d+)\s*$"),
        "cycles_rocc": grab(r"^CYCLES_ROCC=(\d+|NA)\s*$"),
        "passed": int(passed), "sim_rc": r.returncode,
        "digest_sw": d_sw, "digest_hw": d_hw, "digest_expected": want,
    }


def build_feedback(build_ok: bool, artifact, sims: list, violations: list) -> str:
    parts = []
    if violations:
        parts.append("RULE VIOLATION: files outside generators/chipyard changed "
                     "(revert them):\n" + "\n".join(violations))
    if not build_ok:
        log = (artifact.stdout or "") + "\n" + (artifact.stderr or "")
        errors = [l for l in log.splitlines()
                  if re.search(r"\[error\]|error:|%Error|Exception", l)][:80]
        parts.append(f"BUILD FAILED (returncode={artifact.returncode}). Error lines:\n"
                     + "\n".join(errors) + "\n\nLast 3000 characters of the build log:\n"
                     + log[-3000:])
    for row, r in sims:
        status = "PASS" if row["passed"] else "FAIL"
        parts.append(
            f"TEST {row['test']}: {status} (simulator rc={row['sim_rc']}, "
            f"boot={row['boot_result']}, expected boot={TESTS[row['test']][0]})\n"
            f"  DIGEST_SW={row['digest_sw'] or '<missing>'}\n"
            f"  DIGEST_HW={row['digest_hw'] or '<missing>'}\n"
            f"  expected ={row['digest_expected']}\n"
            f"--- program output (tail) ---\n{(r.log or '')[-1500:]}\n"
            f"--- simulator messages (tail) ---\n{(r.out or '')[-1500:]}")
    parts.append("The tree still contains your previous edits; fix them in place.")
    return "\n\n".join(parts)


def write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def save_snapshot(it_dir: str, snap: dict) -> None:
    write(f"{it_dir}/chipyard.patch", snap["patch"])
    write(f"{it_dir}/full.diff", snap["full_diff"])
    write(f"{it_dir}/git_status.txt", snap["status"] + "\n")
    write(f"{it_dir}/untracked.tar.gz", snap["tar"])
    write(f"{it_dir}/untracked.txt", "\n".join(snap["untracked"]) + "\n"
          + "".join(f"SKIPPED (too large): {s}\n" for s in snap["skipped"]))
    with tarfile.open(fileobj=io.BytesIO(snap["tar"]), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and not m.name.startswith("/") and ".." not in m.name.split("/"):
                write(f"{it_dir}/files/{m.name}", tar.extractfile(m).read())


def write_csv(path: str, rows: list) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def print_table(rows: list) -> None:
    print(f"\n{'iter':>4} {'test':<12} {'build':>5} {'boot':<5} {'digest':>6} "
          f"{'CYCLES_SW':>10} {'CYCLES_ROCC':>11} {'speedup':>8} {'pass':>4}")
    for r in rows:
        try:
            speedup = f"{int(r['cycles_sw']) / int(r['cycles_rocc']):.1f}x"
        except (TypeError, ValueError, ZeroDivisionError):
            speedup = "-"
        print(f"{r['iter']:>4} {r['test']:<12} {r['build_ok']:>5} {r['boot_result']:<5} "
              f"{r['digest_match']:>6} {r['cycles_sw'] or '-':>10} "
              f"{r['cycles_rocc'] or '-':>11} {speedup:>8} {r['passed']:>4}")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True, help="absolute path of projects/ascon-secureboot")
    ap.add_argument("--out-dir", required=True, help="absolute host path for results")
    ap.add_argument("--run-id", required=True, help="cache namespace; reuse it to resume/replay")
    ap.add_argument("--config", default="AsconRocketConfig")
    ap.add_argument("--iters", type=int, default=8)
    ap.add_argument("--stop-after-build", action="store_true",
                    help="M2 gate: stop after the first iteration whose build passes "
                         "(its tests still run and are reported)")
    ap.add_argument("--sw-only", action="store_true", help="compile tests with -DSW_ONLY")
    ap.add_argument("--fake-llm", action="store_true", help="canned agent reply (M1)")
    ap.add_argument("--no-replay", action="store_true", help="ignore cached results")
    ap.add_argument("--model", default="opencode/big-pickle")
    ap.add_argument("--llm-timeout", type=int, default=1800)
    ap.add_argument("--make-jobs", type=int, default=32)
    ap.add_argument("--build-timeout", type=int, default=3600)
    ap.add_argument("--timeout-cycles", type=int, default=20_000_000)
    ap.add_argument("--sim-timeout", type=int, default=3600)
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/chia_cache/ascon-secureboot"))
    ap.add_argument("--cache-gb", type=float, default=20)
    args = ap.parse_args()

    proj, out = os.path.abspath(args.project_dir), os.path.abspath(args.out_dir)
    os.makedirs(out, exist_ok=True)
    read = lambda rel: open(os.path.join(proj, rel), encoding="utf-8").read()  # noqa: E731
    sv_text = read("rtl/AsconCoreV4.sv")
    sv_sha = hashlib.sha256(sv_text.encode()).hexdigest()
    sw_files = {"boot.c": read("sw/boot.c"), "image.h": read("sw/image.h")}
    expected = json.loads(read("sw/expected.json"))
    template = read("loop/agent_prompt.md")
    write(f"{out}/run_args.json", json.dumps(vars(args), indent=2) + "\n")
    write(f"{out}/expected.json", json.dumps(expected, indent=2) + "\n")

    ray.init(address="auto", ignore_reinit_error=True)
    start_collector(log_dir=f"{out}/profile")
    yaml_path = os.path.join(proj, "loop/exp.yaml")
    stop_cache()  # a detached cache actor from another experiment would keep its own dir/config
    start_cache(size=args.cache_gb, units="GB", cache_dir_path=args.cache_dir, yaml_path=yaml_path)
    REPLAY["enabled"], REPLAY["fake_llm"] = not args.no_replay, args.fake_llm
    bypass = Bypass(yaml_path=yaml_path)
    if args.fake_llm:
        bypass.set_provider("OpenCodeLLM.prompt", fake_llm)
    else:
        bypass.set_provider("OpenCodeLLM.prompt", replay_from_cache)
        bypass.set_cond("OpenCodeLLM.prompt", agent_cond)
    for fn, cond in (("collect_tree", collect_cond), ("ChiselBuildNode.build", build_cond),
                     ("compile_program", compile_cond)):
        bypass.set_provider(fn, replay_from_cache)
        bypass.set_cond(fn, cond)

    # -- 0. RTL into the container (before any agent turn) ------------------
    info = get(install_rtl.chia_remote(sv_text, sv_sha))
    write(f"{out}/install_rtl.json", json.dumps(info, indent=2) + "\n")
    print(f"AsconCoreV4.sv sha256={sv_sha} -> {info['path']} sha_ok={info['sha_ok']}")
    print(f"container: {info['verilator_version']}; chipyard {info['git_branch']} "
          f"@ {info['git_head']}")
    print(f"lint: rc={info['lint_rc']} ok={info['lint_ok']}\n{info['lint_log'][-1500:]}")
    if not info["sha_ok"]:
        raise SystemExit("AsconCoreV4.sv was not written correctly")
    baseline = set(info["git_status"].splitlines())

    # -- 1. compile the two tests -------------------------------------------
    elfs = {}
    for name, (_, flags) in TESTS.items():
        cflags = CFLAGS + flags + (["-DSW_ONLY"] if args.sw_only else [])
        key = hashlib.sha256(json.dumps([sw_files, cflags, RISCV_GCC]).encode()).hexdigest()[:12]
        elfs[name] = get(compile_program.chia_remote(name, sw_files, cflags,
                                                     _chia_tag=f"compile/{name}/{key}"))
        print(f"compiled {name} {cflags}: {len(elfs[name])} bytes")

    # -- 2. the loop --------------------------------------------------------
    llm = OpenCodeLLM(model=args.model, timeout_seconds=args.llm_timeout, retries=1)
    sim = VerilatorRunNode()
    rows, success = [], False
    feedback = "(none: this is the first attempt)"
    for i in range(args.iters):
        tag = f"{args.run_id}/iter{i}"
        it_dir = f"{out}/iter{i}"
        print(f"\n===== iteration {i} ({tag}) =====", flush=True)
        t0 = time.time()

        prompt = (template.replace("{{ITER}}", str(i + 1))
                  .replace("{{MAX_ITERS}}", str(args.iters))
                  .replace("{{FEEDBACK}}", feedback))
        write(f"{it_dir}/prompt.md", prompt)
        agent_tag = f"{tag}/agent-fake" if args.fake_llm else f"{tag}/agent"
        replayed = not args.fake_llm and agent_cond(agent_tag, None)
        if replayed:
            REPLAY["iters"].add(tag)
        resp = ask_agent(llm, prompt, agent_tag)
        print(f"agent ({'replayed' if replayed else 'live'}, success={resp.success}, "
              f"{time.time() - t0:.0f}s): {resp.result[-1500:]}")
        write(f"{it_dir}/agent_reply.md", resp.result or "")
        write(f"{it_dir}/agent_transcript.txt", resp.stream_result or "")

        snap = get(collect_tree.chia_remote(replayed, _chia_tag=f"{tag}/collect"))
        if snap.get("error"):
            raise SystemExit(snap["error"])
        if replayed:
            print(get(restore_tree.chia_remote(snap)))
        save_snapshot(it_dir, snap)
        violations = find_violations(snap["status"], baseline)
        print(f"collected: patch {len(snap['patch'])} chars, untracked {snap['untracked']}"
              + (f", VIOLATIONS {violations}" if violations else ""))

        cb = ChiselBuildNode(CHIPYARD_PATH, args.config, target=BuildTarget.VERILATOR,
                             make_jobs=args.make_jobs, timeout_seconds=args.build_timeout)
        t1 = time.time()
        artifact = get(cb.build.chia_remote(cb, _chia_tag=f"{tag}/build/{args.config}"))
        build_ok = bool(artifact.success)
        build_seconds = round(time.time() - t1)
        write(f"{it_dir}/build.log", f"[stdout]\n{artifact.stdout}\n[stderr]\n{artifact.stderr}\n")
        print(f"build {args.config}: success={build_ok} rc={artifact.returncode} "
              f"({time.time() - t1:.0f}s)", flush=True)

        sims = []
        if build_ok:
            art_ref = ray.put(artifact)
            for name in TESTS:  # sequential: one verilator_run slot, one test at a time
                r = get(sim.run.chia_remote(sim, art_ref, elfs[name], f"{name}.riscv", SIM_WORK_DIR,
                                            timeout_cycles=args.timeout_cycles,
                                            timeout_seconds=args.sim_timeout, verbose=False))
                write(f"{it_dir}/sim_{name}.log", r.log or "")
                write(f"{it_dir}/sim_{name}.out", (r.out or "")[-200_000:])
                row = parse_run(name, r, expected, args.sw_only)
                sims.append((row, r))
                print(f"  {name}: rc={r.returncode} boot={row['boot_result']} "
                      f"digest_match={row['digest_match']} passed={row['passed']}", flush=True)
            iter_rows = [row for row, _ in sims]
        else:
            iter_rows = [{"test": n, "boot_result": "NOT_RUN", "digest_match": 0, "cycles_sw": "",
                          "cycles_rocc": "", "passed": 0, "sim_rc": ""} for n in TESTS]
        for row in iter_rows:
            row.update(iter=i, build_ok=int(build_ok), config=args.config, run_id=args.run_id)
        rows += iter_rows
        write_csv(f"{out}/results.csv", rows)
        write(f"{it_dir}/summary.json", json.dumps(
            {"replayed": replayed, "build_ok": build_ok, "rows": iter_rows,
             "violations": violations, "build_seconds": build_seconds,
             "seconds": round(time.time() - t0)}, indent=2) + "\n")

        if build_ok and all(r["passed"] for r in iter_rows) and not violations:
            success = True
            print(f"\nSUCCESS after {i + 1} iteration(s)")
            break
        if args.stop_after_build and build_ok:
            success = True
            print(f"\nBUILD PASSED after {i + 1} iteration(s), stopping (--stop-after-build)")
            break
        feedback = build_feedback(build_ok, artifact, sims, violations)
        write(f"{it_dir}/feedback.md", feedback)

    print_table(rows)
    print(f"\nresults: {out}/results.csv")
    stop_cache()
    if not success:
        print(f"Gave up after {args.iters} iteration(s).")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
