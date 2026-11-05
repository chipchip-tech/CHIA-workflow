# Sub-word loads from an uncached (MMIO) register used by the very next instruction, against the same
# loads from cached memory. The asic-7 patch bypasses every load through a register computed from the
# dcache's s1 request, which an uncached response does not match (it relies on a replay).
# Register: CLINT mtimecmp of hart 0 (0x2004000), 64-bit read/write on Rocket and Spike.
# Exit code: 0 = pass; 2 + 0x10 * n = mismatch at check n (n = 0xf0 + mcause: a trap).
  .section .text.init
  .globl _start
  .option norvc
_start:
  la t0, mtrap
  csrw mtvec, t0
  li s0, 0x2004000
  li t0, 0x8182838485868788
  sd t0, 0(s0)
  la s1, ref
  sd t0, 0(s1)
  li s2, 0
  .macro CHK insn, off
    addi s2, s2, 1
    \insn t1, \off(s0)
    addi t2, t1, 1
    xor t2, t2, t1
    \insn t3, \off(s1)
    addi t4, t3, 1
    xor t4, t4, t3
    bne t1, t3, fail
    bne t2, t4, fail
  .endm
  CHK lb, 0
  CHK lb, 3
  CHK lb, 7
  CHK lbu, 0
  CHK lbu, 5
  CHK lbu, 7
  CHK lh, 0
  CHK lh, 2
  CHK lh, 6
  CHK lhu, 4
  CHK lhu, 6
  CHK lw, 0
  CHK lw, 4
  CHK lwu, 0
  CHK lwu, 4
  CHK ld, 0
  # a loaded byte as a branch condition and as an address offset right away
  addi s2, s2, 1
  lb t1, 7(s0)
  bgez t1, fail
  lbu t1, 0(s0)                  # 0x88: low 3 bits 0
  andi t1, t1, 7
  add t2, s1, t1
  lbu t3, 0(t2)
  li t4, 0x88
  beqz t1, 1f
  j fail
1:bne t3, t4, fail
  li a0, 0
  j exit
mtrap:                           # a trap: code 2 + 0x10 * (0xf0 + mcause)
  csrr s2, mcause
  addi s2, s2, 0xf0
fail:
  slli a0, s2, 4
  addi a0, a0, 2
exit:
  slli a0, a0, 1
  ori a0, a0, 1
  la t0, tohost
  sd a0, 0(t0)
2:j 2b

  .data
  .align 3
ref: .dword 0

  .section .tohost, "aw", @progbits
  .align 6
  .globl tohost
  .type tohost, @object
  .size tohost, 8
tohost: .dword 0
  .align 6
  .globl fromhost
  .type fromhost, @object
  .size fromhost, 8
fromhost: .dword 0
