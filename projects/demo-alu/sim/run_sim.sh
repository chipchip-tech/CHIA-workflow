#!/usr/bin/env bash
# run_sim.sh - build và chạy testbench của Demo 1 bằng Verilator (bước Sim).
#
#   ./run_sim.sh <NoCG|CG> [plusargs...]
#   ví dụ: ./run_sim.sh CG +seed=7 +cycles=50000 +en_pct=30
#
# Mặc định ghi waveform vào build/<biến thể>/wave.vcd (tắt bằng NO_VCD=1).
# Log ở build/<biến thể>/sim.log. Thoát 0 khi dòng RESULT là PASS.
# SIM=iverilog để chạy bằng Icarus Verilog thay cho Verilator.
set -euo pipefail
cd "$(dirname "$0")"
V=${1:-}; shift || true
case "$V" in
    NoCG) DUT=NoCG_alu; SRCS=(../rtl/NoCG_alu.v) ;;
    CG)   DUT=CG_alu;   SRCS=(../rtl/CG_alu.v ../rtl/icg_sim.v) ;;
    *) echo "Dùng: $0 <NoCG|CG> [plusargs...]" >&2; exit 2 ;;
esac
B=build/$V; mkdir -p "$B"
ARGS=("$@")
[ "${NO_VCD:-0}" = 1 ] || ARGS+=("+vcd=$B/wave.vcd")

if [ "${SIM:-verilator}" = iverilog ]; then
    iverilog -g2005 -DDUT="$DUT" -s tb_alu -o "$B/sim.vvp" tb_alu.v "${SRCS[@]}"
    vvp -n "$B/sim.vvp" "${ARGS[@]}" | tee "$B/sim.log"
else
    verilator --binary --timing --trace -Wall -Wno-fatal -DDUT="$DUT" --top-module tb_alu \
        -Mdir "$B/obj" -o Vtb tb_alu.v "${SRCS[@]}" > "$B/build.log" 2>&1 \
        || { tail -30 "$B/build.log"; exit 1; }
    "$B/obj/Vtb" "${ARGS[@]}" | tee "$B/sim.log"
fi
grep -q '^RESULT: PASS' "$B/sim.log"
