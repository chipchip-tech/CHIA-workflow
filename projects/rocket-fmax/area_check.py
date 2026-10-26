"""Check the ChipTop area flow outside CHIA, on generated sources copied into the
eda container (see README, "Mốc phụ"). Picks the ChipTop hierarchy exactly like
extract_soc_rtl, parses every file on its own with the eda Yosys (so all parse
errors show up in one go), then runs synth_area itself.

    python area_check.py <gen-collateral dir> [--period-ns 21.2] [--parse-only]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

try:
    import chia.base.ChiaFunction  # noqa: F401
except ImportError:     # plain python in the container: the decorator is all fmax_nodes needs
    m = types.ModuleType("chia.base.ChiaFunction")
    m.ChiaFunction = lambda *a, **k: (lambda f: f)
    m.get = lambda x: x
    for name in ("chia", "chia.base"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["chia.base.ChiaFunction"] = m

import fmax_nodes as N


def main():
    p = argparse.ArgumentParser()
    p.add_argument("gen", type=Path)
    p.add_argument("--period-ns", type=float, default=21.2)
    p.add_argument("--parse-only", action="store_true")
    a = p.parse_args()

    files = [f for f in sorted(a.gen.iterdir()) if f.is_file() and f.name.endswith((".v", ".sv"))]
    files += sorted(a.gen.parent.glob("*.top.mems.conf")) + sorted(a.gen.glob("*.top.mems.conf"))
    srcs = [(f.name, f.read_text(errors="replace")) for f in files]
    if not any(f.name.endswith(".top.mems.conf") for f in files):
        sys.exit(f"no *.top.mems.conf next to {a.gen}: the SRAMs would not be black boxes")
    print(subprocess.run(["yosys", "-V"], capture_output=True, text=True).stdout.strip())
    rtl = N.tile_rtl_from_sources(srcs, N.SOC_TOP, stub_modules=(N.TOP,))
    print(f"ChipTop: {len(rtl.files)} files, {len(rtl.modules)} modules, black boxes: {sorted(rtl.blackboxes)}")

    bad, rewritten = [], 0
    with tempfile.TemporaryDirectory() as work:
        for name, text in sorted(rtl.files.items()):
            try:
                text, n = N.sv_packed_patterns(text)
            except ValueError as e:
                bad.append((name, str(e)))
                continue
            rewritten += n
            path = os.path.join(work, name)
            Path(path).write_text(text)
            y = subprocess.run(["yosys", "-q", "-p", f"read_verilog -sv -DSYNTHESIS {name}"],
                               cwd=work, capture_output=True, text=True)
            if y.returncode != 0:
                err = [l for l in (y.stdout + y.stderr).splitlines() if "ERROR" in l]
                bad.append((name, err[0] if err else (y.stdout + y.stderr)[-300:]))
    print(f"'{{...}} patterns rewritten: {rewritten}")
    print(f"parse: {len(rtl.files) - len(bad)}/{len(rtl.files)} files OK")
    for name, err in bad:
        print(f"  FAIL {name}: {err}")
    if bad or a.parse_only:
        sys.exit(1 if bad else 0)

    print("synth_area ...", flush=True)
    area = N.synth_area(rtl, a.period_ns)
    print(f"success={area.success} area_um2={area.area_um2} seconds={area.seconds:.0f} error={area.error!r}")
    if not area.success:
        print(area.yosys_log_tail[-3000:])
        sys.exit(1)
    top = sorted(area.module_area.items(), key=lambda kv: -kv[1])[:15]
    for mod, um2 in top:
        print(f"  {um2:12.0f}  {mod}")


if __name__ == "__main__":
    main()
