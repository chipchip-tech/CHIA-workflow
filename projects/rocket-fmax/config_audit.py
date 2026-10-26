"""Side milestone: an agent audits RocketConfig for SoC blocks that the system
does not need (debug/bring-up leftovers, unused peripherals or memories), and
the loop checks each candidate.

  1. baseline: build the unmodified RocketConfig, Yosys area of ChipTop per block
     (RocketTile and SRAMs as black boxes); cached (audit_soc_rtl, audit_soc_area;
     the gate reuses baseline_gate from rocket_fmax.py).
  2. agent (read-only, chipyard_bash): reads the config sources, the generated
     hierarchy and the area table, writes candidates.json.
  3. per candidate: the loop (not the agent) writes RocketFmaxAudit.scala with
     `class RocketFmaxAudit<k> extends Config(<fragments> ++ new chipyard.RocketConfig)`,
     builds it, runs the gate (ISA + benchmarks, mcycle vs baseline) and the area.
  4. optionally one config with every candidate that passed with unchanged mcycle;
     the final config is exported as patches/chipyard-config.patch.

Blocks the later FPGA (Arty A7-100T) and ASIC (OpenROAD flow scripts) flows need are
protected (PROTECTED): a fragment that removes one is refused before the build, and a
build whose ChipTop lost a protected port group or module is refused before the gate.
Neither flow is run here.

  chia job submit --no-wait --submission-id fmax-audit --working-dir . -- \\
      python config_audit.py --run-name audit --exclude-isa rv64ui-p-ma_data

rocket-chip is reset to its HEAD (no Fmax patch) for the whole run; the audit
config file is removed at the end.
"""

import argparse
import csv
import json
import os
import re
import string
import time
import traceback
from datetime import datetime
from pathlib import Path

import ray

import equiv as E
import fmax_nodes as N
from rocket_fmax import (build, build_errors, cache_has, compare_gate, export_profile, save_build,
                         save_gate, write)
from chia.base.ChiaFunction import get
from chia.base.bypass import Bypass
from chia.base.cache import start_cache, stop_cache
from chia.base.tools.BashTool import BashTool
from chia.models.opencode import OpenCodeLLM
from chia.trace.profiler import start_collector, stop_collector

HERE = Path(__file__).resolve().parent
EXP_YAML = HERE / "exp.yaml"
PROMPT = HERE / "prompts" / "audit_config.md"
CANDIDATES = f"{N.AUDIT_STAGE_DIR}/candidates.json"
FRAGMENT_RE = re.compile(r"^new\s+[A-Za-z_][\w.]*(\s*\([^;{}`]*\))?$")
CSV_FIELDS = ["id", "block", "category", "fragments", "protected_hit", "build_ok", "isa_pass", "bench_pass",
              "mcycle_changed", "area_um2", "area_saved_um2", "area_saved_pct", "sram_bits_saved",
              "needed_for_tapeout", "verdict", "reason", "minutes"]
CONFIG_PATH = "generators/chipyard/src/main/scala/config/RocketFmaxSoC.scala"
ARTY_PATH = "fpga/src/main/scala/arty100t/RocketFmaxSoCArty100T.scala"

# Blocks needed by the FPGA (chipyard.fpga.arty100t: WithArty100TTweaks maps UART, UART-TSI,
# JTAG and DDR over the mem port) and ASIC flows (ChipLikeRocketConfig: serial-TL to the
# bring-up host, boot pin, clock tap, PLL/reset). `frag` matches a fragment that removes the
# block; `ports` (ChipTop ports) and `mods` (module names) are compared with the baseline.
PROTECTED = [
    dict(name="debug", fpga="JTAG load/debug", asic="JTAG silicon bring-up",
         frag=r"\bWithNoDebug\w*|\bWithDMIDTM\b", ports=r"^jtag_", mods=r"^TLDebugModule"),
    dict(name="uart", fpga="console (PMOD JD)", asic="console",
         frag=r"\bWithNoUART\b", ports=r"^uart_", mods=r"^TLUART"),
    dict(name="serial_tl", fpga="(tied off)", asic="bring-up host, off-chip memory",
         frag=r"\bWithNoSerialTL\w*|\bWithSerialTL\s*\(\s*(Nil|Seq\s*\(\s*\))", ports=r"^serial_tl_",
         mods=r"Serdes"),
    dict(name="mem_port", fpga="DDR (Arty: TL mem port)", asic="kept, same SoC",
         frag=r"\bWithNoMemPort\b|\bWithNMemoryChannels\s*\(\s*0\s*\)", ports=r"^axi4_mem_", mods=None),
    dict(name="bootrom", fpga="boot", asic="boot",
         frag=r"\bWithNoBootROM\b", ports=None, mods=r"^(TLROM|BootROM)"),
    dict(name="clint", fpga="software timer", asic="software timer",
         frag=r"\bWithNoCLINT\b", ports=None, mods=r"^CLINT"),
    dict(name="plic", fpga="interrupts", asic="interrupts",
         frag=r"\bWithNoPLIC\b", ports=None, mods=r"^TLPLIC"),
    dict(name="boot_select", fpga="(unused)", asic="boot-select pin, boot-addr reg",
         frag=r"\bWithNo(CustomBootPin|BootAddrReg)\b", ports=r"^custom_boot", mods=r"BootAddrReg"),
    dict(name="clock_tap", fpga="(unused)", asic="clock measurement",
         frag=r"\bWithNoClockTap\b", ports=r"^clock_tap", mods=None),
    dict(name="clock_reset", fpga="Arty: passthrough", asic="PLL/divider, reset sync",
         frag=r"\bWithNo(ResetSynchronizers|ClockGenerator\w*)\b", ports=r"^(clock(?!_tap)|reset)",
         mods=r"ResetCatchAndSync|ResetSynchronizer"),
]
SYSTEM_MESSAGE = ("You are an SoC integration engineer reviewing a Chipyard configuration. You only "
                  "read files through the chipyard_bash tool; you never edit, build or simulate.")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--period-ns", type=float, default=21.2, help="abc -D target for the area runs")
    p.add_argument("--max-candidates", type=int, default=8)
    p.add_argument("--candidates-file", default=None,
                   help="use this candidates.json instead of asking the agent")
    p.add_argument("--no-combine", action="store_true", help="skip the combined config")
    p.add_argument("--run-name", default=None)
    p.add_argument("--out-dir", default="~/rocket-fmax-out")
    p.add_argument("--cache-dir", default="~/chia_cache")
    p.add_argument("--flush-cache", action="store_true")
    p.add_argument("--make-jobs", type=int, default=16)
    p.add_argument("--build-timeout", type=int, default=3600)
    p.add_argument("--exclude-isa", nargs="*", default=[])
    p.add_argument("--model", default="opencode/big-pickle")
    p.add_argument("--llm-timeout", type=int, default=1800)
    a = p.parse_args()
    a.run_name = a.run_name or f"audit-{datetime.now().strftime('%Y%m%d-%H%M')}"
    a.out_dir = os.path.expanduser(a.out_dir)
    a.cache_dir = os.path.expanduser(a.cache_dir)
    return a


def append_row(path: Path, row: dict):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def area_table(rtl, area, max_depth=3) -> str:
    rows = N.block_tree(rtl, area.module_area, max_depth)
    total = area.area_um2 or 1.0
    out = [f"{'area um2':>12} {'%':>6}  instance path (module)   [RocketTile and *_ext SRAMs are black boxes]"]
    for depth, path, mod, ar in rows:
        a_s = f"{ar:12.0f} {100 * ar / total:6.2f}" if ar is not None else f"{'(bbox)':>12} {'':>6}"
        out.append(f"{a_s}  {'  ' * depth}{path} ({mod})")
    return "\n".join(out)


def sram_table(mems_conf: str) -> str:
    bits = N.sram_bits(mems_conf)
    return "\n".join([f"{'bits':>10}  sram macro (from .top.mems.conf)"]
                     + [f"{b:10d}  {n}" for n, b in sorted(bits.items(), key=lambda kv: -kv[1])])


def config_text(classes: dict, header="// Generated by projects/rocket-fmax/config_audit.py; "
                                      "removed at the end of the run.") -> str:
    body = [header, "package chipyard", "", "import org.chipsalliance.cde.config.{Config}", ""]
    for name, frags in classes.items():
        body.append(f"class {name} extends Config(")
        body += [f"  {f} ++" for f in frags]
        body += ["  new chipyard.RocketConfig)", ""]
    return "\n".join(body)


def parse_candidates(text: str | None, resp: str) -> tuple[list, str]:
    """Candidates from candidates.json, else from the last ```json block of the reply."""
    src = text
    if src is None:
        blocks = re.findall(r"```json\s*(.*?)```", resp or "", re.S)
        src = blocks[-1] if blocks else None
    if src is None:
        return [], "agent không ghi candidates.json và không có khối ```json"
    try:
        data = json.loads(src)
    except json.JSONDecodeError as e:
        return [], f"candidates.json không phải JSON hợp lệ: {e}"
    if isinstance(data, dict):
        data = data.get("candidates", [])
    return [c for c in data if isinstance(c, dict)], ""


def validate(c: dict) -> str:
    frags = c.get("removal")
    if not isinstance(frags, list) or not frags:
        return "thiếu `removal` (danh sách fragment)"
    for f in frags:
        if not isinstance(f, str) or not FRAGMENT_RE.match(f.strip()):
            return f"fragment không đúng dạng `new a.b.C(...)`: {f!r}"
    return ""


def protected_by_fragment(frags: list[str]) -> list[str]:
    return [p["name"] for p in PROTECTED if any(re.search(p["frag"], f) for f in frags)]


def protected_signature(rtl) -> dict:
    """{block: {"ports": [...], "mods": [...]}} of the protected blocks in a ChipTop."""
    top = ""
    for text in rtl.files.values():
        top = N._split_modules(text).get(N.SOC_TOP, top)
    ports = [f"{d} {n}[{w}]" for d, n, w in E.ports(top)]
    sig = {}
    for p in PROTECTED:
        sig[p["name"]] = {
            "ports": sorted(x for x in ports if p["ports"] and re.search(p["ports"], x.split()[1])),
            "mods": sorted(m for m in rtl.modules if p["mods"] and re.search(p["mods"], m))}
    return sig


def protected_diff(base_sig: dict, sig: dict) -> list[str]:
    out = []
    for name, b in base_sig.items():
        lost_p = sorted(set(b["ports"]) - set(sig[name]["ports"]))
        lost_m = sorted(set(b["mods"]) - set(sig[name]["mods"]))
        changed_p = set(sig[name]["ports"]) != set(b["ports"])
        if lost_p or lost_m or changed_p:
            what = [f"mất cổng {', '.join(lost_p[:6])}" + (" ..." if len(lost_p) > 6 else "")] if lost_p else []
            what += [f"mất module {', '.join(lost_m[:6])}"] if lost_m else []
            what += ["cổng đổi"] if changed_p and not lost_p else []
            out.append(f"{name}: {'; '.join(what)}")
    return out


def protected_table(base_sig: dict) -> str:
    lines = [f"{'block':12} {'FPGA Arty A7-100T':24} {'ASIC (ORFS)':32} found in baseline ChipTop"]
    for p in PROTECTED:
        s = base_sig.get(p["name"], {"ports": [], "mods": []})
        found = (f"{len(s['ports'])} ports, modules: {', '.join(s['mods'][:4]) or '-'}"
                 if s["ports"] or s["mods"] else "NOT FOUND (check the regex)")
        lines.append(f"{p['name']:12} {p['fpga']:24} {p['asic']:32} {found}")
    return "\n".join(lines)


def new_file_patch(files: dict) -> str:
    """git-apply patch that adds `files` ({path in chipyard: text})."""
    out = []
    for path, text in files.items():
        lines = text.rstrip("\n").split("\n")
        out += [f"diff --git a/{path} b/{path}", "new file mode 100644", "--- /dev/null", f"+++ b/{path}",
                f"@@ -0,0 +1,{len(lines)} @@"] + [f"+{l}" for l in lines]
    return "\n".join(out) + "\n"


def export_config(out: Path, frags: list[str], ids: list[str]) -> Path:
    note = ("// Generated by projects/rocket-fmax/config_audit.py: RocketConfig without "
            f"{', '.join(ids)}.\n// Verified: riscv-tests + benchmark mcycle (Verilator), protected "
            "FPGA/ASIC blocks still in ChipTop.\n// Not run yet: Vivado (Arty A7-100T), OpenROAD flow scripts.")
    soc = config_text({"RocketFmaxSoCConfig": frags}, note).replace(
        "  new chipyard.RocketConfig)", "  new chipyard.RocketConfig)  // + best.patch in rocket-chip")
    arty = "\n".join([
        "// Generated by projects/rocket-fmax/config_audit.py; not elaborated yet.",
        "package chipyard.fpga.arty100t", "", "import org.chipsalliance.cde.config.{Config}", "",
        "class RocketFmaxSoCArty100TConfig extends Config(",
        "  new WithArty100TTweaks ++",
        "  new chipyard.config.WithBroadcastManager ++ // no L2, as RocketArty100TConfig",
        "  new chipyard.RocketFmaxSoCConfig)", ""])
    p = out / "patches" / "chipyard-config.patch"
    write(p, new_file_patch({CONFIG_PATH: soc, ARTY_PATH: arty}))
    write(out / "patches" / "RocketFmaxSoC.scala", soc)
    return p


def baseline(a, out: Path, elfs: dict, snap: dict) -> dict:
    # area tag a2: '{...} patterns rewritten (fmax_nodes.sv_packed_patterns); the first
    # audit_soc_area_p21p2 cached a failed Yosys run
    tags = {"rtl": "audit_soc_rtl", "area": f"audit_soc_area_a2_p{a.period_ns:g}".replace(".", "p"),
            "gate": "baseline_gate"}
    hits = {k: cache_has(t) for k, t in tags.items()}
    print("audit baseline cache: " + ", ".join(f"{tags[k]}={'HIT' if v else 'miss'}" for k, v in hits.items()),
          flush=True)
    d = out / "runs" / "baseline"
    art = None
    if not (hits["rtl"] and hits["gate"]):
        art = get(build(a, "audit-baseline"))
        save_build(d, art)
        if not art.success:
            raise SystemExit(f"baseline build failed:\n{build_errors(art)}")
    rtl = get(N.extract_soc_rtl.chia_remote(art, _chia_tag=tags["rtl"]))
    gate_ref = N.run_gate.chia_remote(art, elfs, _chia_tag=tags["gate"])
    area = get(N.synth_area.chia_remote(rtl, a.period_ns, _chia_tag=tags["area"]))
    gate = get(gate_ref)
    if not area.success:
        write(d / "yosys.log", area.yosys_log_tail)
        raise SystemExit(f"Yosys (area ChipTop) lỗi: {area.error}; xem {d}/yosys.log")
    save_gate(d, gate)
    write(d / "area_by_block.txt", area_table(rtl, area))
    write(d / "sram_macros.txt", sram_table(rtl.mems_conf))
    write(d / "yosys_stat.txt", area.stat)
    print(f"ChipTop (không tính RocketTile và SRAM): {area.area_um2:.0f} um2, Yosys {area.seconds / 60:.1f} min")
    print(area_table(rtl, area, 2), flush=True)
    sig = protected_signature(rtl)
    write(d / "protected_blocks.txt", protected_table(sig))
    print("\nkhối bảo vệ (FPGA/ASIC):\n" + protected_table(sig), flush=True)
    return {"rtl": rtl, "area": area, "gate": gate, "sig": sig}


def run_agent(a, out: Path, base: dict, snap: dict) -> tuple[list, str]:
    d = out / "runs" / "agent"
    gate = base["gate"]
    cycles = "\n".join(f"{n}: mcycle {b['mcycle']}" for n, b in gate.bench.items())
    files = {"area_by_block.txt": area_table(base["rtl"], base["area"], 6),
             "sram_macros.txt": sram_table(base["rtl"].mems_conf),
             "modules.txt": "\n".join(base["rtl"].modules),
             "baseline_cycles.txt": cycles,
             "protected_blocks.txt": protected_table(base["sig"])}
    get(N.stage_for_agent.chia_remote(files, N.AUDIT_STAGE_DIR))
    get(N.read_text_opencode.chia_remote(CANDIDATES, True))     # no stale file from an earlier run
    prompt = string.Template(PROMPT.read_text()).safe_substitute(
        STAGE_DIR=N.AUDIT_STAGE_DIR, CANDIDATES=CANDIDATES, MAX=a.max_candidates,
        AREA=area_table(base["rtl"], base["area"], 2), CYCLES=cycles, PROTECTED=protected_table(base["sig"]))
    write(d / "prompt.md", prompt)
    llm = OpenCodeLLM(model=a.model, system_message=SYSTEM_MESSAGE, timeout_seconds=a.llm_timeout, retries=1)
    bash = BashTool("chipyard_bash", N.CHIPYARD, timeout_seconds=120,
                    task_options={"resources": {"chipyard": 1}})
    resp = None
    try:
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=[bash], _chia_tag=f"audit_{a.run_name}_llm"))
    except Exception:                                                # noqa: BLE001
        write(d / "agent_error.txt", traceback.format_exc())
    finally:
        bash.stop()
    if resp is not None:
        write(d / "agent_response.md", resp.result)
        write(d / "agent_transcript.txt", resp.stream_result)
    # read-only check: nothing in the tree may have changed
    diff = get(N.collect_diff.chia_remote(snap["rocket_head"], snap["outside"]))
    notes = []
    if diff["patch"].strip():
        notes.append(f"agent đã sửa rocket-chip ({', '.join(diff['files'])}); đã revert")
        get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
    if diff["outside_changed"]:
        notes.append(f"agent đã sửa ngoài rocket-chip ({diff['outside_changed']}); đã khôi phục")
        get(N.restore_outside.chia_remote(snap["outside"]))
    raw = get(N.read_text.chia_remote(CANDIDATES))
    if raw is None:
        raw = get(N.read_text_opencode.chia_remote(CANDIDATES))
        if raw is not None:
            notes.append("agent ghi candidates.json bằng bash của opencode (container opencode), không qua chipyard_bash")
    write(d / "candidates.json", raw or "")
    cands, err = parse_candidates(raw, resp.result if resp else "")
    if err:
        notes.append(err)
    write(d / "notes.txt", "\n".join(notes))
    for n in notes:
        print(f"[agent] {n}", flush=True)
    return cands, (resp.result if resp else "")


def check_candidate(a, out: Path, elfs, base, cid: str, cls: str, frags: list[str]) -> dict:
    d = out / "runs" / cid
    r = {"build_ok": False, "protected_hit": "", "isa_pass": "", "bench_pass": "", "mcycle_changed": "", "area_um2": "",
         "area_saved_um2": "", "area_saved_pct": "", "sram_bits_saved": "", "reason": ""}
    text = config_text({cls: frags})
    write(d / "RocketFmaxAudit.scala", text)
    get(N.write_audit_config.chia_remote(text))
    try:
        art = get(build(a, cid, config=cls))
    finally:
        get(N.write_audit_config.chia_remote(None))
    save_build(d, art)
    if not art.success:
        r["reason"] = "build lỗi: " + build_errors(art)[:1500]
        return r
    r["build_ok"] = True
    rtl = get(N.extract_soc_rtl.chia_remote(art))
    hit = protected_diff(base["sig"], protected_signature(rtl))
    if hit:     # the FPGA/ASIC flows need it: no gate, no area
        r.update(protected_hit="; ".join(hit), reason="đụng khối bảo vệ FPGA/ASIC: " + "; ".join(hit))
        return r
    gate_ref = N.run_gate.chia_remote(art, elfs)
    area = get(N.synth_area.chia_remote(rtl, a.period_ns))
    gate = get(gate_ref)
    save_gate(d, gate)
    isa_ok, _, why = compare_gate(base["gate"], gate, a.exclude_isa)
    bench_ok = all(b["passed"] for b in gate.bench.values())
    changed = [f"{n} {base['gate'].bench[n]['mcycle']}->{b['mcycle']}" for n, b in gate.bench.items()
               if b["passed"] and b["mcycle"] != base["gate"].bench[n]["mcycle"]]
    r.update(isa_pass=isa_ok, bench_pass=bench_ok, mcycle_changed="; ".join(changed))
    base_bits = sum(N.sram_bits(base["rtl"].mems_conf).values())
    r["sram_bits_saved"] = base_bits - sum(N.sram_bits(rtl.mems_conf).values())
    if area.success:
        write(d / "area_by_block.txt", area_table(rtl, area))
        saved = base["area"].area_um2 - area.area_um2
        r.update(area_um2=f"{area.area_um2:.0f}", area_saved_um2=f"{saved:.0f}",
                 area_saved_pct=f"{100 * saved / base['area'].area_um2:.2f}")
    else:
        write(d / "yosys.log", area.yosys_log_tail)
    r["reason"] = "; ".join(why) if why else ("" if area.success else f"area lỗi: {area.error}")
    return r


def verdict(r: dict) -> str:
    if r.get("protected_hit"):
        return "cần cho FPGA/ASIC (đụng khối bảo vệ)"
    if not r["build_ok"]:
        return "không build được"
    if not (r["isa_pass"] and r["bench_pass"]):
        return "cần cho harness/chức năng (gate trượt)"
    if r["mcycle_changed"]:
        return "chạy được nhưng đổi cycle"
    return "bỏ được (gate pass, cycle giữ nguyên)"


def run(a, out: Path):
    elfs = get(N.load_test_elfs.chia_remote())
    snap = get(N.tree_snapshot.chia_remote())
    write(out / "runs" / "tree_snapshot.json", json.dumps(snap, indent=2))
    get(N.reset_rocket.chia_remote(snap["rocket_head"], ""))
    get(N.write_audit_config.chia_remote(None))
    base = baseline(a, out, elfs, snap)

    if a.candidates_file:
        cands, err = parse_candidates(Path(a.candidates_file).expanduser().read_text(), "")
        resp = ""
        if err:
            raise SystemExit(err)
    else:
        cands, resp = run_agent(a, out, base, snap)
    print(f"\n{len(cands)} ứng viên từ agent", flush=True)

    csv_path = out / "audit.csv"
    rows, passed = [], []
    for k, c in enumerate(cands[: a.max_candidates], 1):
        cid = re.sub(r"\W+", "_", str(c.get("id") or f"c{k}"))[:40]
        frags = [f.strip() for f in c.get("removal") or []]
        row = {"id": cid, "block": c.get("block", ""), "category": c.get("category", ""),
               "fragments": " ++ ".join(frags), "needed_for_tapeout": c.get("needed_for_tapeout", "")}
        t0 = time.time()
        bad = validate(c)
        hit = [] if bad else protected_by_fragment(frags)
        if bad:
            row.update(build_ok=False, reason=f"bỏ qua: {bad}", verdict="ứng viên không hợp lệ")
        elif hit:
            row.update(build_ok=False, protected_hit=", ".join(hit), verdict="bị chặn: khối bảo vệ FPGA/ASIC",
                       reason=f"fragment bỏ khối bảo vệ ({', '.join(hit)}); không build")
        else:
            print(f"\n######## {k}/{min(len(cands), a.max_candidates)} {cid}: {row['fragments']}", flush=True)
            try:
                res = check_candidate(a, out, elfs, base, f"cand{k}_{cid}", f"RocketFmaxAudit{k}", frags)
            except Exception as e:                                   # noqa: BLE001
                write(out / "runs" / f"cand{k}_{cid}" / "error.txt", traceback.format_exc())
                res = {"build_ok": False, "reason": f"lỗi hạ tầng: {type(e).__name__}: {e}"[:800]}
            row.update(res)
            row["verdict"] = verdict({"isa_pass": False, "bench_pass": False, "mcycle_changed": "", **res})
            if row["verdict"].startswith("bỏ được"):
                passed.append((cid, frags))
        row["minutes"] = f"{(time.time() - t0) / 60:.1f}"
        row["reason"] = str(row.get("reason", "")).replace("\n", " | ")[:1000]
        append_row(csv_path, row)
        rows.append({**row, "evidence": c.get("evidence", ""), "notes": c.get("notes", "")})
        print(f"{cid}: {row['verdict']} | area -{row.get('area_saved_um2', '')} um2 "
              f"({row.get('area_saved_pct', '')} %) | {row['reason'][:300]}", flush=True)

    final = passed if len(passed) == 1 else []
    if len(passed) > 1 and not a.no_combine:
        frags = [f for _, fs in passed for f in fs]
        print(f"\n######## combined: {[cid for cid, _ in passed]}", flush=True)
        t0 = time.time()
        try:
            res = check_candidate(a, out, elfs, base, "combined", "RocketFmaxAuditAll", frags)
        except Exception as e:                                       # noqa: BLE001
            res = {"build_ok": False, "reason": f"lỗi hạ tầng: {type(e).__name__}: {e}"[:800]}
        row = {"id": "combined", "block": " + ".join(cid for cid, _ in passed), "category": "all",
               "fragments": " ++ ".join(frags), **res,
               "verdict": verdict({"isa_pass": False, "bench_pass": False, "mcycle_changed": "", **res}),
               "minutes": f"{(time.time() - t0) / 60:.1f}"}
        row["reason"] = str(row.get("reason", "")).replace("\n", " | ")[:1000]
        append_row(csv_path, row)
        rows.append(row)
        if row["verdict"].startswith("bỏ được"):
            final = passed
        print(f"combined: {row['verdict']} | area -{row.get('area_saved_um2', '')} um2 "
              f"({row.get('area_saved_pct', '')} %)", flush=True)

    exported = None
    if final:
        exported = export_config(out, [f for _, fs in final for f in fs], [cid for cid, _ in final])
        print(f"config: {exported}", flush=True)
    report(out, base, rows, resp, exported)


def report(out: Path, base: dict, rows: list, resp: str, exported: Path | None = None):
    lines = ["# Config audit: RocketConfig", "",
             f"Area ChipTop (Yosys, sky130hd, không tính RocketTile và SRAM): {base['area'].area_um2:.0f} um2. "
             f"SRAM: {sum(N.sram_bits(base['rtl'].mems_conf).values())} bit.", "",
             "| Ứng viên | Nhóm | Fragment | Kết luận | Khối bảo vệ bị đụng | Area bớt (um2, %) | SRAM bit bớt "
             "| mcycle đổi | Chip thật cần? |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r.get('category', '')} | `{r.get('fragments', '')}` | {r.get('verdict', '')} | "
                     f"{r.get('protected_hit', '') or '-'} | {r.get('area_saved_um2', '')} ({r.get('area_saved_pct', '')}) | {r.get('sram_bits_saved', '')} | "
                     f"{r.get('mcycle_changed', '') or '-'} | {r.get('needed_for_tapeout', '')} |")
    lines += ["", "## Config xuất ra", ""]
    lines += ([f"`{exported}`: thêm `RocketFmaxSoCConfig` (`{CONFIG_PATH}`) và `RocketFmaxSoCArty100TConfig` "
               f"(`{ARTY_PATH}`). Áp bằng `git -C /home/ray/chipyard apply chipyard-config.patch`, cùng "
               "`best.patch` trong rocket-chip."] if exported else
              ["Không xuất: không có ứng viên nào (hoặc config gộp) qua đủ gate."])
    lines += ["", "Gate còn treo (chưa chạy): Vivado synth/timing trên Arty A7-100T (50 MHz) và elaborate "
              "`RocketFmaxSoCArty100TConfig`; OpenROAD flow scripts (sky130) cho `ChipTop`. "
              "Adder viết tay trong `best.patch` cần kiểm lại timing trên FPGA (carry chain cứng).",
              "", "## Khối bảo vệ (FPGA/ASIC)", "", "```", protected_table(base["sig"]), "```"]
    lines += ["", "## Bằng chứng của agent", ""]
    lines += [f"- **{r['id']}** ({r.get('block', '')}): {r.get('evidence', '')}"
              + (f" Ghi chú: {r['notes']}" if r.get("notes") else "") for r in rows if r.get("evidence")]
    lines += ["", "## Báo cáo đầy đủ của agent", "", resp or "(không có)"]
    write(out / "audit.md", "\n".join(lines) + "\n")
    print(f"\nreport: {out / 'audit.md'}")


def main():
    a = parse_args()
    out = Path(a.out_dir) / a.run_name
    for sub in ("runs", "profile"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    write(out / "args.json", json.dumps(vars(a), indent=2))
    print(f"run {a.run_name}: output {out}", flush=True)
    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    start_collector(log_dir=str(out / "profile"))
    cache = start_cache(size=4, units="GB", cache_dir_path=a.cache_dir, yaml_path=str(EXP_YAML))
    if a.flush_cache:
        get(cache.flush.chia_remote())
    bypass = Bypass(yaml_path=str(EXP_YAML))
    for fn in ("extract_soc_rtl", "synth_area", "run_gate"):
        bypass.set_provider(fn, N.cache_provider)
        bypass.set_cond(fn, N.cache_hit_cond)
    try:
        run(a, out)
    finally:
        try:
            get(N.write_audit_config.chia_remote(None))
        except Exception as e:                                       # noqa: BLE001
            print(f"không xoá được {N.AUDIT_CONFIG_FILE}: {e}")
        try:
            stop_collector()
        finally:
            stop_cache()
            export_profile(out)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
