/* Like rand_diff.c, for Zba/Zbb/Zbs (built with -march=..._zba_zbb_zbs; run only when the baseline
   passes rv64uzbb). Targets the rewritten ALU: trailing/leading-zero count, CPOP split over EX and MEM
   (its result used by the next instructions), the per-byte last mux, max/min, word forms. */
#include "ft.h"

#ifndef ITERS
#define ITERS 2048
#endif
#define EVERY 256

static uint64_t hb;
#define B2(insn) ({ uint64_t _r; asm volatile(insn " %0,%1,%2" : "=r"(_r) : "r"(x), "r"(y)); _r; })
#define B1(insn) ({ uint64_t _r; asm volatile(insn " %0,%1" : "=r"(_r) : "r"(x)); _r; })
#define BI(insn, imm) ({ uint64_t _r; asm volatile(insn " %0,%1," #imm : "=r"(_r) : "r"(x)); _r; })
#define MB(v) mix(&hb, (v))

static void zb_iter(void)
{
  uint64_t x = pick_int(), y = pick_int();
  if (rnd() & 1) x = (rnd() & 1) ? x << (rnd() & 63) : x >> (rnd() & 63);   /* runs of zeros at either end */
  MB(B2("andn")); MB(B2("orn")); MB(B2("xnor"));
  MB(B1("clz")); MB(B1("ctz")); MB(B1("cpop")); MB(B1("clzw")); MB(B1("ctzw")); MB(B1("cpopw"));
  MB(B2("max")); MB(B2("maxu")); MB(B2("min")); MB(B2("minu"));
  MB(B1("sext.b")); MB(B1("sext.h")); MB(B1("zext.h")); MB(B1("orc.b")); MB(B1("rev8"));
  MB(B2("rol")); MB(B2("ror")); MB(B2("rolw")); MB(B2("rorw"));
  MB(BI("rori", 0)); MB(BI("rori", 13)); MB(BI("rori", 63)); MB(BI("roriw", 0)); MB(BI("roriw", 31));
  MB(B2("sh1add")); MB(B2("sh2add")); MB(B2("sh3add")); MB(B2("add.uw"));
  MB(B2("sh1add.uw")); MB(B2("sh2add.uw")); MB(B2("sh3add.uw")); MB(BI("slli.uw", 0)); MB(BI("slli.uw", 31));
  MB(B2("bclr")); MB(B2("bset")); MB(B2("binv")); MB(B2("bext"));
  MB(BI("bclri", 63)); MB(BI("bseti", 5)); MB(BI("binvi", 31)); MB(BI("bexti", 47));
  { /* CPOP/CLZ results used by the next one, two and three instructions */
    uint64_t r;
    asm volatile("cpop t0,%1\n\tadd %0,t0,%2\n\tcpopw t1,%2\n\tslli t2,t1,1\n\tadd %0,%0,t2\n\tcpop t0,%0\n\tnop\n\tadd %0,%0,t0"
                 : "=&r"(r) : "r"(x), "r"(y) : "t0", "t1", "t2"); MB(r);
    asm volatile("ctz t0,%1\n\tclz t1,%2\n\tsll %0,%2,t0\n\tsrl %0,%0,t1\n\tcpop t0,%0\n\tbeqz t0,1f\n\taddi %0,%0,9\n1:\tmax %0,%0,t0"
                 : "=&r"(r) : "r"(x), "r"(y) : "t0", "t1"); MB(r);
    asm volatile("cpop t0,%1\n\tsd t0,0(sp)\n\tld t1,0(sp)\n\tsub %0,t1,t0\n\tcpopw t0,%1\n\taddw %0,%0,t0\n\tcpop t0,t0\n\tor %0,%0,t0"
                 : "=&r"(r) : "r"(x) : "t0", "t1", "memory"); MB(r); }
}

int main(void)
{
  for (uint64_t it = 1; it <= ITERS; it++) {
    zb_iter();
    if (it % EVERY == 0) report("zb", it, hb);
  }
  return 0;
}
