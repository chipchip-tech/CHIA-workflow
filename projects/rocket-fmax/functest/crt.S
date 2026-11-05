# Start-up for the C functional tests: stack, FPU on, bss zeroed, trap handler, main(), exit via tohost.
  .section .text.init
  .globl _start
_start:
  la sp, _stack_top
  la t0, ft_trap_entry
  csrw mtvec, t0
  li t0, (1 << 13)          # mstatus.FS = initial
  csrs mstatus, t0
  csrwi fcsr, 0
  la t0, _bss_start
  la t1, _bss_end
1:bgeu t0, t1, 2f
  sd zero, 0(t0)
  addi t0, t0, 8
  j 1b
2:call main
  call ft_exit
3:j 3b

  .align 2
ft_trap_entry:               # any trap is a failure: report it (C, does not return)
  csrr a0, mcause
  csrr a1, mepc
  csrr a2, mtval
  la sp, _stack_top
  call ft_trap
4:j 4b
