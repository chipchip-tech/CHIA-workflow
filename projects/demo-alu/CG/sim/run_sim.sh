#!/usr/bin/env bash
# run_sim.sh - build và chạy testbench của demo CG (CG_alu) bằng Verilator (bước Sim).
#
#   ./run_sim.sh [plusargs...]
#   ví dụ: ./run_sim.sh +seed=7 +cycles=50000 +en_pct=30
#
# Mặc định ghi waveform vào build/wave.vcd (tắt bằng NO_VCD=1).
# Verilator: build có coverage (--coverage) và functional coverage fcov_$DUT.sv (bind vào DUT, cố định); khi xong
# ghi build/coverage.dat và in dòng FCOV. Vòng mô phỏng ở sim_main.cpp (cố định).
# Log ở build/sim.log. Thoát 0 khi dòng RESULT là PASS.
# SIM=iverilog để chạy bằng Icarus Verilog thay cho Verilator.
# BUILD_ONLY=1: chỉ build (khối chipyard của CHIA). SKIP_BUILD=1: chỉ chạy binary đã build (khối verilator).
set -euo pipefail
cd "$(dirname "$0")"
DUT=CG_alu
SRCS=(../rtl/CG_alu.v ../rtl/icg_sim.v)
B=build; mkdir -p "$B"
ARGS=("$@")
[ "${NO_VCD:-0}" = 1 ] || ARGS+=("+vcd=$B/wave.vcd")

if [ "${SIM:-verilator}" = iverilog ]; then
    [ "${SKIP_BUILD:-0}" = 1 ] || iverilog -g2005 -s tb_$DUT -o "$B/sim.vvp" tb_$DUT.v "${SRCS[@]}"
    [ "${BUILD_ONLY:-0}" = 1 ] && exit 0
    vvp -n "$B/sim.vvp" "${ARGS[@]}" | tee "$B/sim.log"
else
    if [ "${SKIP_BUILD:-0}" != 1 ]; then
        verilator --cc --exe --build -j 4 --prefix Vtb --timing --trace --coverage -Wall -Wno-fatal \
            --top-module tb_$DUT -Mdir "$B/obj" -o Vtb tb_$DUT.v "${SRCS[@]}" fcov_$DUT.sv "$PWD/sim_main.cpp" \
            > "$B/build.log" 2>&1 \
            || { tail -30 "$B/build.log"; exit 1; }
    fi
    [ "${BUILD_ONLY:-0}" = 1 ] && exit 0
    rm -f "$B/coverage.dat"
    "$B/obj/Vtb" "${ARGS[@]}" "+cov_file=$B/coverage.dat" | tee "$B/sim.log"
fi
grep -q '^RESULT: PASS' "$B/sim.log"
