/* Bare-metal layout for the functional tests: Rocket (chipyard) and Spike both start at 0x80000000
   in M-mode, HTIF through the tohost/fromhost symbols. */
OUTPUT_ARCH("riscv")
ENTRY(_start)
SECTIONS
{
  . = 0x80000000;
  .text.init : { *(.text.init) }
  . = ALIGN(0x1000);
  .tohost : { *(.tohost) }
  . = ALIGN(0x1000);
  .text : { *(.text*) }
  .rodata : { *(.rodata*) *(.srodata*) }
  .data : { *(.data*) *(.sdata*) }
  .bss : { _bss_start = .; *(.bss*) *(.sbss*) *(COMMON); _bss_end = .; }
  . = ALIGN(16);
  . = . + 0x10000;
  _stack_top = .;
  _end = .;
}
