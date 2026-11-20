#!/usr/bin/env bash
# run_sim.sh - build và chạy testbench của demo NoCG (NoCG_alu) bằng Verilator (bước Sim).
#
#   ./run_sim.sh [plusargs...]
#   ví dụ: ./run_sim.sh +seed=7 +cycles=50000 +en_pct=30
#
# Mặc định ghi waveform vào build/wave.vcd (tắt bằng NO_VCD=1).
# Log ở build/sim.log. Thoát 0 khi dòng RESULT là PASS.
# SIM=iverilog để chạy bằng Icarus Verilog thay cho Verilator.
set -euo pipefail
cd "$(dirname "$0")"
DUT=NoCG_alu
SRCS=(../rtl/NoCG_alu.v)
B=build; mkdir -p "$B"
ARGS=("$@")
[ "${NO_VCD:-0}" = 1 ] || ARGS+=("+vcd=$B/wave.vcd")

if [ "${SIM:-verilator}" = iverilog ]; then
    iverilog -g2005 -s tb_$DUT -o "$B/sim.vvp" tb_$DUT.v "${SRCS[@]}"
    vvp -n "$B/sim.vvp" "${ARGS[@]}" | tee "$B/sim.log"
else
    verilator --binary --timing --trace -Wall -Wno-fatal --top-module tb_$DUT \
        -Mdir "$B/obj" -o Vtb tb_$DUT.v "${SRCS[@]}" > "$B/build.log" 2>&1 \
        || { tail -30 "$B/build.log"; exit 1; }
    "$B/obj/Vtb" "${ARGS[@]}" | tee "$B/sim.log"
fi
grep -q '^RESULT: PASS' "$B/sim.log"
