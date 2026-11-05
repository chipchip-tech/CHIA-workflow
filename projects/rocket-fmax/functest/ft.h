/* Small runtime for the functional tests: HTIF output (SYS_write through tohost, as the
   riscv-tests benchmarks do), hex printing, exit. Every output line starts with "FT:". */
#ifndef FT_H
#define FT_H
#include <stdint.h>

volatile uint64_t tohost __attribute__((section(".tohost"), aligned(64)));
volatile uint64_t fromhost __attribute__((section(".tohost"), aligned(64)));

static void htif_write(const char *buf, unsigned long n)
{
#ifdef FT_CONSOLE   /* HTIF console putchar (QEMU's spike machine has no syscall proxy): local checks only */
  for (unsigned long i = 0; i < n; i++) {
    while (tohost) ;
    tohost = (1ull << 56) | (1ull << 48) | (uint8_t)buf[i];
    while (tohost) ;
    fromhost = 0;
  }
  return;
#endif
  volatile uint64_t magic[8] __attribute__((aligned(64)));
  magic[0] = 64; magic[1] = 1; magic[2] = (uintptr_t)buf; magic[3] = n;
  __sync_synchronize();
  tohost = (uintptr_t)magic;
  while (fromhost == 0) ;
  fromhost = 0;
  __sync_synchronize();
}

static char ft_line[160];
static int ft_len;
static void pc_(char c) { if (ft_len < (int)sizeof(ft_line)) ft_line[ft_len++] = c; }
static void ps(const char *s) { while (*s) pc_(*s++); }
static void phex(uint64_t x) { for (int i = 60; i >= 0; i -= 4) pc_("0123456789abcdef"[(x >> i) & 15]); }
static void pdec(uint64_t x) { char b[24]; int n = 0; do { b[n++] = '0' + x % 10; x /= 10; } while (x); while (n) pc_(b[--n]); }
static void pend(void) { pc_('\n'); htif_write(ft_line, ft_len); ft_len = 0; }

void ft_exit(int code)
{
  ft_len = 0; ps("FT: exit "); pdec(code); pend();
  tohost = ((uint64_t)code << 1) | 1;
  for (;;) ;
}

void ft_trap(uint64_t cause, uint64_t epc, uint64_t tval)
{
  ft_len = 0; ps("FT: trap mcause="); phex(cause); ps(" mepc="); phex(epc); ps(" mtval="); phex(tval); pend();
  tohost = (3 << 1) | 1;
  for (;;) ;
}

/* xorshift64 and an FNV-style mix: the same sequence and hash on every machine */
#ifndef SEED
#define SEED 0x9E3779B97F4A7C15ull
#endif
static uint64_t rs = SEED;
static inline uint64_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }
static inline void mix(uint64_t *h, uint64_t x) { *h ^= x; *h *= 0x100000001b3ull; *h ^= *h >> 29; }

static void report(const char *cat, uint64_t it, uint64_t h)
{
  ps("FT: "); ps(cat); pc_(' '); pdec(it); pc_(' '); phex(h); pend();
}

/* integer operands: corner values, random bits, random bits with a random number of leading zeros
   (the divider's early-out), small values */
static uint64_t pick_int(void)
{
  static const uint64_t sp[] = {0, 1, 2, 3, ~0ull, ~1ull, 0x8000000000000000ull, 0x7fffffffffffffffull,
                                0x80000000ull, 0x7fffffffull, 0xffffffffull, 0xffffffff80000000ull,
                                0x100000000ull, 0xffffffff7fffffffull, 0x8000000000000001ull, 10};
  uint64_t r = rnd();
  switch (r & 3) {
    case 0: return sp[(r >> 2) % (sizeof sp / sizeof sp[0])];
    case 1: return rnd() >> ((r >> 8) & 63);
    case 2: return (r >> 12) & 0xff ? rnd() : (uint64_t)(int64_t)(int8_t)(r >> 20);
    default: return rnd();
  }
}

#endif
