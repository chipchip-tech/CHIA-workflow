# C.EBREAK in U-mode and S-mode must raise a breakpoint (mcause 3), whatever halfword follows it.
# Suspected bug in the asic-7 patch: the CSR decoder reads the raw instruction bits, and for a
# compressed instruction bits 31..20 come from the next halfword, so in U/S-mode C.EBREAK could
# trap as an illegal instruction (mcause 2) when that halfword's bits 13..12 name a higher mode.
# Exit code: 0 = pass (tohost 1); 0x100 + mcause = wrong trap; 0x200 + count = wrong number of traps.
  .section .text.init
  .globl _start
  .option norvc
_start:
  la t0, mtrap
  csrw mtvec, t0
  li t0, -1
  csrw pmpaddr0, t0
  li t0, 0x1f                    # NAPOT, RWX over the whole space
  csrw pmpcfg0, t0
  csrw satp, zero
  csrw medeleg, zero
  csrw mideleg, zero
  li s0, 0                       # breakpoints seen
  li s1, 0                       # 0: U-mode, 1: S-mode
run:
  li t0, (3 << 11)
  csrc mstatus, t0
  slli t1, s1, 11
  csrs mstatus, t1               # MPP = s1
  la t0, ublock
  csrw mepc, t0
  mret

  .align 2
mtrap:
  csrr t0, mcause
  li t1, 8                       # ecall from U
  beq t0, t1, done_mode
  li t1, 9                       # ecall from S
  beq t0, t1, done_mode
  li t1, 3
  bne t0, t1, wrong
  addi s0, s0, 1
  csrr t0, mepc
  addi t0, t0, 2                 # skip the 16-bit c.ebreak
  csrw mepc, t0
  mret
done_mode:
  addi s1, s1, 1
  li t1, 2
  blt s1, t1, run
  li t1, 8                       # 4 breakpoints per mode
  bne s0, t1, wrong_count
  li a0, 0
  j exit
wrong:
  addi a0, t0, 0x100
  j exit
wrong_count:
  addi a0, s0, 0x200
exit:
  slli a0, a0, 1
  ori a0, a0, 1
  la t0, tohost
  sd a0, 0(t0)
1:j 1b

  .align 2
ublock:
  .option push
  .option rvc
  c.ebreak
  c.lui a5, 0xfffe0             # bits 13..12 = 11 (M-mode CSR address range)
  c.ebreak
  c.addi a5, -32                # bits 13..12 = 01 (S)
  c.ebreak
  c.addiw a5, 1                 # bits 13..12 = 10 (hypervisor)
  c.ebreak
  c.nop                         # bits 13..12 = 00
  .option pop
  ecall

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
