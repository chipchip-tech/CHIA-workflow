#!/usr/bin/env python3
"""golden_sig.py - chữ ký mong đợi (EXP_SIG) của fpga_top.v, tính bằng mô hình Python độc lập với RTL.

  python3 golden_sig.py [N_CYCLES] [SEED_HEX]      mặc định 1048576 ACE12025

Mô hình khớp từng bước với fpga_top.v: LFSR Galois 32 bit sinh kích thích mỗi chu kỳ, phép được gửi khi
lfsr[6:0] < 38 (khoảng 30 %) và chu kỳ < N_CYCLES-4; kết quả của ALU (theo đúng thứ tự gửi) được dồn vào MISR 32 bit.
Đổi N_CYCLES hay SEED trong fpga_top.v thì phải tính lại EXP_SIG bằng script này.
"""
import sys

LFSR_POLY = 0x00400007          # x^32 + x^22 + x^2 + x + 1 (Galois)
MISR_POLY = 0x04C11DB7
M32 = 0xFFFFFFFF


def alu(op: int, a: int, b: int) -> tuple[int, int]:
    """Trả (y, carry) như tb_*_alu.v."""
    if op == 0:
        s = a + b
        return s & 0xFF, s >> 8
    if op == 1:
        return (a - b) & 0xFF, int(a < b)
    if op == 2:
        return a & b, 0
    if op == 3:
        return a | b, 0
    if op == 4:
        return a ^ b, 0
    if op == 5:
        return (a << (b & 7)) & 0xFF, 0
    if op == 6:
        return a >> (b & 7), 0
    sa = a - 256 if a & 0x80 else a
    sb = b - 256 if b & 0x80 else b
    return int(sa < sb), 0


def golden(n_cycles: int, seed: int) -> tuple[int, int]:
    lfsr, sig, n_ops = seed, 0, 0
    for _ in range(n_cycles - 4):
        if (lfsr & 0x7F) < 38:
            y, c = alu((lfsr >> 8) & 7, (lfsr >> 16) & 0xFF, (lfsr >> 24) & 0xFF)
            word = (c << 9) | (int(y == 0) << 8) | y
            sig = (((sig << 1) & M32) ^ (MISR_POLY if sig >> 31 else 0) ^ word) & M32
            n_ops += 1
        lfsr = ((lfsr << 1) & M32) ^ (LFSR_POLY if lfsr >> 31 else 0)
    return sig, n_ops


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1048576
    seed = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0xACE12025
    sig, ops = golden(n, seed)
    print(f"N_CYCLES={n} SEED=32'h{seed:08X} ops={ops} EXP_SIG=32'h{sig:08X}")
