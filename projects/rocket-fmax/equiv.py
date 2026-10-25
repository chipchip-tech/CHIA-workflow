"""Equivalence check of an accepted patch against the baseline (pure Python,
no CHIA imports; the nodes in fmax_nodes.py run the tools).

Two independent checks per changed module ("unit"), baseline = gold, patched = gate:
  formal: Yosys equiv_make / equiv_simple -seq / equiv_induct on both versions
          (proves combinational rewrites; multipliers and moved registers may
          stay unproven).
  sim:    Verilator lockstep of both versions with the same random inputs,
          every output compared bit for bit on both clock phases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.M)
_INST_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+(?:#\s*\(|[A-Za-z_]\w*\s*\()", re.M)
_PORT_RE = re.compile(r"^\s*(input|output)\s+(?:wire\s+|logic\s+|reg\s+)?(?:signed\s+)?"
                      r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)", re.M)
_KEYWORDS = {"module", "input", "output", "wire", "reg", "logic", "assign", "always", "initial",
             "if", "else", "begin", "end", "for", "case", "casez", "endcase", "function", "task"}

# Modules shared by both versions as-is (never part of an agent edit).
SHARED_RE = re.compile(r"(_ext|^EICG_wrapper)$")


def split_modules(text: str) -> dict[str, str]:
    """{module name: module text} for every module defined in `text`."""
    out = {}
    for m in _MODULE_RE.finditer(text):
        end = text.find("endmodule", m.end())
        out[m.group(1)] = text[m.start(): end + len("endmodule") if end >= 0 else len(text)]
    return out


def modules_of(files: dict) -> dict[str, str]:
    mods: dict[str, str] = {}
    for _, text in sorted(files.items()):
        for name, mtext in split_modules(text).items():
            mods.setdefault(name, mtext)
    return mods


def normalize(mtext: str) -> str:
    """Module text without comments (`// @[file:line]` moves with every edit)
    and with whitespace collapsed."""
    t = re.sub(r"/\*.*?\*/", " ", mtext, flags=re.S)
    t = re.sub(r"//[^\n]*", " ", t)
    return " ".join(t.split())


def children(mtext: str, defined) -> set[str]:
    return {c for c in _INST_RE.findall(mtext) if c in defined and c not in _KEYWORDS}


def hierarchy(top: str, mods: dict[str, str]) -> list[str]:
    seen, todo = [], [top]
    while todo:
        m = todo.pop()
        if m in seen or m not in mods:
            continue
        seen.append(m)
        if not SHARED_RE.search(m):
            todo.extend(sorted(children(mods[m], mods)))
    return seen


def ports(mtext: str) -> list[tuple[str, str, int]]:
    """[(direction, name, width)] from an ANSI module header (firtool style)."""
    head = mtext[: mtext.find(");") + 2]
    return [(d, n, int(hi) - int(lo) + 1 if hi else 1)
            for d, hi, lo, n in _PORT_RE.findall(head)]


@dataclass
class Unit:
    top: str                   # module name in both versions
    gold: str                  # baseline hierarchy, every module renamed g_<name>
    gate: str                  # patched hierarchy, every module renamed n_<name>
    shared: str                # SRAM / clock-gate models, unrenamed
    inputs: list               # [(name, width)] without clock/reset
    outputs: list              # [(name, width)]
    clock: str | None
    reset: str | None
    changed: list = field(default_factory=list)     # changed modules inside the unit
    note: str = ""


@dataclass
class ChangeSet:
    changed: list              # modules in both versions whose text differs
    added: list                # only in the patched version
    removed: list              # only in the baseline
    units: list                # [Unit]
    skipped: list              # [(module, why)]


def _rename(mods: dict[str, str], names: set[str], prefix: str) -> str:
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    if not alt:
        return ""
    head = re.compile(rf"^(\s*module\s+)({alt})\b", re.M)
    inst = re.compile(rf"^(\s*)({alt})(\s+(?:#\s*\(|[A-Za-z_]\w*\s*\())", re.M)
    out = []
    for n in sorted(names):
        t = head.sub(lambda m: m.group(1) + prefix + m.group(2), mods[n])
        out.append(inst.sub(lambda m: m.group(1) + prefix + m.group(2) + m.group(3), t))
    return "\n\n".join(out) + "\n"


def make_unit(top: str, gold_mods: dict, gate_mods: dict, changed: set[str]) -> Unit:
    gh, nh = hierarchy(top, gold_mods), hierarchy(top, gate_mods)
    g_own = {m for m in gh if not SHARED_RE.search(m)}
    n_own = {m for m in nh if not SHARED_RE.search(m)}
    shared = sorted({m for m in gh + nh if SHARED_RE.search(m)})
    shared_text = "\n\n".join(gold_mods.get(m) or gate_mods[m] for m in shared)
    ps = ports(gold_mods[top])
    clock = next((n for d, n, w in ps if d == "input" and n == "clock"), None)
    reset = next((n for d, n, w in ps if d == "input" and n == "reset"), None)
    return Unit(top=top, gold=_rename(gold_mods, g_own, "g_"), gate=_rename(gate_mods, n_own, "n_"),
                shared=shared_text,
                inputs=[(n, w) for d, n, w in ps if d == "input" and n not in (clock, reset)],
                outputs=[(n, w) for d, n, w in ps if d == "output"],
                clock=clock, reset=reset, changed=sorted(changed & (g_own | n_own)))


def find_units(gold_files: dict, gate_files: dict, top: str) -> ChangeSet:
    """Changed modules of the `top` hierarchy and the units to check: the
    top-most changed modules, moved up to a parent while the ports differ."""
    gm, nm = modules_of(gold_files), modules_of(gate_files)
    gh, nh = set(hierarchy(top, gm)), set(hierarchy(top, nm))
    changed = sorted(m for m in gh & nh if normalize(gm[m]) != normalize(nm[m]))
    added, removed = sorted(nh - gh), sorted(gh - nh)
    parents: dict[str, set[str]] = {}
    for m in nh:
        if not SHARED_RE.search(m):
            for c in children(nm[m], nm):
                parents.setdefault(c, set()).add(m)

    def below(m):
        return set(hierarchy(m, nm)) - {m}

    roots, skipped = set(changed), []
    for _ in range(64):
        roots = {r for r in roots if not any(r in below(o) for o in roots if o != r)}
        bad = {r for r in roots if r not in gm or ports(gm[r]) != ports(nm[r])}
        if not bad:
            break
        for r in bad:
            roots.discard(r)
            up = parents.get(r, set())
            if not up:
                skipped.append((r, "cổng khác baseline và không có module cha để so"))
            roots |= up
    units = [make_unit(r, gm, nm, set(changed) | set(added)) for r in sorted(roots)]
    return ChangeSet(changed=changed, added=added, removed=removed, units=units, skipped=skipped)


# ---------------------------------------------------------------------------
# formal (Yosys)
# ---------------------------------------------------------------------------

FORMAL_SCRIPT = """\
read_verilog -sv -DSYNTHESIS gold.sv
read_verilog -sv -DSYNTHESIS gate.sv
{read_shared}
hierarchy -check
proc
memory -nomap
memory_map
opt_clean
flatten g_{top}
flatten n_{top}
async2sync
opt_clean
equiv_make g_{top} n_{top} equiv
hierarchy -top equiv
equiv_simple -seq {seq}
equiv_induct -seq {seq}
tee -o equiv_status.txt equiv_status
"""


def formal_script(unit: Unit, seq: int = 4) -> str:
    return FORMAL_SCRIPT.format(top=unit.top, seq=seq,
                                read_shared="read_verilog -sv -DSYNTHESIS shared.sv" if unit.shared else "")


def parse_equiv_status(text: str) -> dict:
    """{"total", "proven", "unproven"} from `equiv_status` output."""
    tot = re.search(r"Found (\d+) \$equiv cells", text)
    m = re.search(r"Of those cells (\d+) are proven and (\d+) are unproven", text)
    if m:
        return {"total": int(m.group(1)) + int(m.group(2)), "proven": int(m.group(1)),
                "unproven": int(m.group(2))}
    if tot and re.search(r"Equivalence successfully proven", text):
        return {"total": int(tot.group(1)), "proven": int(tot.group(1)), "unproven": 0}
    return {"total": int(tot.group(1)) if tot else None, "proven": None, "unproven": None}


# ---------------------------------------------------------------------------
# lockstep simulation (Verilator)
# ---------------------------------------------------------------------------

def _pad(n: int) -> int:
    return max(65, n)          # always > 64 bits: a VlWide array on the C++ side


def tb_verilog(unit: Unit) -> str:
    win = _pad(sum(w for _, w in unit.inputs))
    nout = _pad(len(unit.outputs))
    lines = ["module equiv_tb(", "  input clock,", "  input reset,",
             f"  input [{win - 1}:0] rnd,", f"  output [{nout - 1}:0] mism", ");"]
    conns, pos = [], 0
    if unit.clock:
        conns.append(f".{unit.clock}(clock)")
    if unit.reset:
        conns.append(f".{unit.reset}(reset)")
    for n, w in unit.inputs:
        conns.append(f".{n}(rnd[{pos + w - 1}:{pos}])")
        pos += w
    for n, w in unit.outputs:
        lines.append(f"  wire [{w - 1}:0] g__{n}, n__{n};")
    lines.append(f"  g_{unit.top} gold ({', '.join(conns + [f'.{n}(g__{n})' for n, _ in unit.outputs])});")
    lines.append(f"  n_{unit.top} gate ({', '.join(conns + [f'.{n}(n__{n})' for n, _ in unit.outputs])});")
    for i, (n, _) in enumerate(unit.outputs):
        lines.append(f"  assign mism[{i}] = |(g__{n} ^ n__{n});")
    if len(unit.outputs) < nout:
        lines.append(f"  assign mism[{nout - 1}:{len(unit.outputs)}] = '0;")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


TB_CPP = r"""
#include "Vequiv_tb.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>

static const char* OUT_NAMES[] = {%(names)s};
static const int N_OUT = %(nout)d, W_IN = %(win)d, W_MISM = %(wmism)d;

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  uint64_t cycles = argc > 1 ? strtoull(argv[1], 0, 10) : 1000000;
  uint64_t seed = argc > 2 ? strtoull(argv[2], 0, 10) : 1;
  std::mt19937_64 rng(seed);
  auto* t = new Vequiv_tb;
  const int nw = (W_IN + 31) / 32, nm = (W_MISM + 31) / 32;
  auto word = [&]() -> uint32_t {        // random, with extra all-0 / all-1 words
    uint64_t r = rng();
    switch (r & 7) { case 0: return 0u; case 1: return 0xffffffffu; default: return (uint32_t)(r >> 32); }
  };
  uint64_t reports = 0, bad_cycles = 0;
  auto check = [&](uint64_t c, const char* phase) -> bool {
    bool bad = false;
    for (int i = 0; i < nm; i++) if (t->mism[i]) bad = true;
    if (!bad) return false;
    if (reports++ >= 20) return true;
    printf("MISMATCH cycle %%llu (%%s):", (unsigned long long)c, phase);
    for (int i = 0; i < N_OUT; i++) if ((t->mism[i / 32] >> (i %% 32)) & 1) printf(" %%s", OUT_NAMES[i]);
    printf("\n");
    return true;
  };
  for (int i = 0; i < nw; i++) t->rnd[i] = 0;
  t->clock = 0; t->reset = 1; t->eval();
  for (uint64_t c = 0; c < cycles; c++) {
    t->reset = (c < 16) || (rng() %% 8192 == 0);
    if (rng() %% 4) for (int i = 0; i < nw; i++) t->rnd[i] = word();   // else hold the inputs
    t->clock = 0; t->eval(); bool bad = check(c, "clock low");
    t->clock = 1; t->eval(); bad = check(c, "clock high") || bad;
    if (bad) bad_cycles++;
  }
  printf("DONE cycles=%%llu seed=%%llu bad_cycles=%%llu\n", (unsigned long long)cycles,
         (unsigned long long)seed, (unsigned long long)bad_cycles);
  t->final();
  delete t;
  return bad_cycles ? 1 : 0;
}
"""


def tb_cpp(unit: Unit) -> str:
    names = ", ".join(f'"{n}"' for n, _ in unit.outputs) or '""'
    return TB_CPP % {"names": names, "nout": len(unit.outputs),
                     "win": _pad(sum(w for _, w in unit.inputs)), "wmism": _pad(len(unit.outputs))}


VERILATOR_CMD = ("verilator --cc --exe --build -O2 -j {jobs} --top-module equiv_tb -DSYNTHESIS "
                 "-Wno-fatal -Wno-lint -Wno-style -Wno-UNOPTFLAT -Wno-MULTIDRIVEN -Wno-TIMESCALEMOD "
                 "--x-assign 0 --x-initial 0 -Mdir obj -o equiv_sim "
                 "gold.sv gate.sv {shared} tb.sv tb.cpp")


def parse_sim(out: str) -> dict:
    m = re.search(r"DONE cycles=(\d+) seed=(\d+) bad_cycles=(\d+)", out)
    if not m:
        return {"cycles": None, "bad_cycles": None,
                "mismatches": re.findall(r"^MISMATCH.*$", out, re.M)[:20]}
    return {"cycles": int(m.group(1)), "bad_cycles": int(m.group(3)),
            "mismatches": re.findall(r"^MISMATCH.*$", out, re.M)[:20]}
