#!/usr/bin/env bash
# Self-test outside CHIA (no cluster needed):
#   1. sw/boot.c built natively with -DSW_ONLY: boot_good -> BOOT OK,
#      boot_tamper -> BOOT FAIL, digests equal sw/expected.json
#   2. ascon_hash256_sw() vs ascon.py for lengths 0..33, 64, 1023, 1024, 4096
#   3. rtl/AsconCoreV4.sv in Verilator, driven per the RoCC interface spec,
#      vs ascon.py (skipped when verilator is not installed)
#
# Usage: scripts/selftest.sh [path/to/ascon.py]   (default: clone ascon-verilog)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
CC=${CC:-gcc}

ASCON_PY=${1:-}
if [[ -z "$ASCON_PY" ]]; then
  COMMIT=$(python3 -c "import sys; sys.path.insert(0, '$ROOT/scripts'); from prepare_rtl import PINNED_COMMIT; print(PINNED_COMMIT)")
  git clone -q https://github.com/rprimas/ascon-verilog.git "$WORK/ascon-verilog"
  git -C "$WORK/ascon-verilog" checkout -q "$COMMIT"
  ASCON_PY=$WORK/ascon-verilog/ascon.py
fi

echo "== 1. boot.c (native, -DSW_ONLY)"
want() { python3 -c "import json; print(json.load(open('$ROOT/sw/expected.json'))['digest']['$1'])"; }
for t in boot_good boot_tamper; do
  flags=(-DSW_ONLY); [[ $t == boot_tamper ]] && flags+=(-DTAMPER)
  $CC -O2 -Wall -Wextra -Werror "${flags[@]}" -I"$ROOT/sw" "$ROOT/sw/boot.c" -o "$WORK/$t"
  out=$("$WORK/$t")
  echo "$out" | sed 's/^/   /'
  expect=$([[ $t == boot_good ]] && echo "BOOT OK" || echo "BOOT FAIL")
  grep -qx "$expect" <<<"$out" || { echo "FAIL: $t did not print $expect"; exit 1; }
  grep -qx "DIGEST_SW=$(want $t)" <<<"$out" || { echo "FAIL: $t digest != expected.json"; exit 1; }
done

echo "== 2. ascon_hash256_sw vs ascon.py"
python3 "$ROOT/tb/gen_vectors.py" --ascon-py "$ASCON_PY" --with-empty > "$WORK/vec_sw.txt"
$CC -O2 -Wall -DSW_ONLY -I"$ROOT/sw" "$ROOT/tb/sw_kat.c" -o "$WORK/sw_kat"
"$WORK/sw_kat" < "$WORK/vec_sw.txt"

echo "== 3. AsconCoreV4.sv in Verilator"
if ! command -v verilator >/dev/null; then
  echo "   verilator not found, skipped"; exit 0
fi
verilator --version
python3 "$ROOT/tb/gen_vectors.py" --ascon-py "$ASCON_PY" > "$WORK/vec_hw.txt"
verilator --cc --exe --build -j 0 -O2 -Wno-fatal -Wno-UNOPTFLAT --top-module AsconCoreV4 \
  -Mdir "$WORK/obj" "$ROOT/rtl/AsconCoreV4.sv" "$ROOT/tb/tb_ascon_core.cpp" > "$WORK/verilator.log" 2>&1 \
  || { tail -40 "$WORK/verilator.log"; exit 1; }
"$WORK/obj/VAsconCoreV4" < "$WORK/vec_hw.txt"
echo "selftest: all passed"
