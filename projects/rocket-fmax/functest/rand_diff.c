/* Directed random differential test (rv64gc): the same ELF runs on Spike and on each Rocket build,
   and every "FT:" line must match. Targets the parts rewritten by the Fmax patches:
   - FP: every op in every rounding mode, FMA/div/sqrt/convert on special values, fflags, NaN boxing,
     FP loads (recode) and dependent FP chains (new FMA/convert latencies);
   - integer mul/div on corner values and on values with random leading zeros (early-out);
   - sub-word loads used by the very next instruction (the load bypass), store-then-load, AMOs;
   - compressed code (the whole file is built with C). */
#include "ft.h"

#ifndef ITERS
#define ITERS 2048
#endif
#define EVERY 256

static uint64_t hfp, hint, hld;

/* ---------- FP operands ---------- */
static uint64_t pick_d(void)
{
  static const uint64_t sp[] = {
    0, 0x8000000000000000ull, 0x3ff0000000000000ull, 0xbff0000000000000ull,
    0x7ff0000000000000ull, 0xfff0000000000000ull, 0x7ff8000000000000ull, 0x7ff4000000000000ull,
    0x0000000000000001ull, 0x000fffffffffffffull, 0x0010000000000000ull, 0x7fefffffffffffffull,
    0x43e0000000000000ull, 0xc3e0000000000000ull, 0x41e0000000000000ull, 0xc1e0000000000000ull,
    0x3fe0000000000000ull, 0x3ff8000000000000ull, 0x4330000000000000ull, 0x41dfffffffc00000ull};
  uint64_t r = rnd();
  switch (r & 7) {
    case 0: case 1: return sp[(r >> 3) % (sizeof sp / sizeof sp[0])];
    case 2: return rnd() & 0x800fffffffffffffull;                       /* subnormal */
    case 3: return (rnd() & 0x800fffffffffffffull) | ((uint64_t)(0x3ff + (int)((r >> 8) & 127) - 64) << 52);
    case 4: return (rnd() & 0x800fffffffffffffull) | ((uint64_t)(0x3ff + (int)((r >> 8) & 63)) << 52); /* integer range */
    default: return rnd();
  }
}

static uint32_t pick_s(void)
{
  static const uint32_t sp[] = {0, 0x80000000u, 0x3f800000u, 0xbf800000u, 0x7f800000u, 0xff800000u,
                                0x7fc00000u, 0x7fa00000u, 1, 0x007fffffu, 0x00800000u, 0x7f7fffffu,
                                0x4f000000u, 0xcf000000u, 0x5f000000u, 0xdf000000u, 0x3f000000u, 0x4b000000u};
  uint64_t r = rnd();
  switch (r & 7) {
    case 0: case 1: return sp[(r >> 3) % (sizeof sp / sizeof sp[0])];
    case 2: return (uint32_t)rnd() & 0x807fffffu;
    case 3: return ((uint32_t)rnd() & 0x807fffffu) | ((uint32_t)(127 + (int)((r >> 8) & 63) - 32) << 23);
    case 4: return ((uint32_t)rnd() & 0x807fffffu) | ((uint32_t)(127 + (int)((r >> 8) & 31)) << 23);
    default: return (uint32_t)rnd();
  }
}

static inline void set_rm(uint64_t rm) { asm volatile("csrw frm, %0" :: "r"(rm)); }
static inline uint64_t take_flags(void) { uint64_t f; asm volatile("csrrw %0, fflags, x0" : "=r"(f)); return f; }

#define D2(insn) ({ uint64_t _r; asm volatile("fmv.d.x ft0,%1\n\tfmv.d.x ft1,%2\n\t" insn " ft2,ft0,ft1\n\tfmv.x.d %0,ft2" \
  : "=r"(_r) : "r"(a), "r"(b) : "ft0", "ft1", "ft2"); _r; })
#define D1(insn) ({ uint64_t _r; asm volatile("fmv.d.x ft0,%1\n\t" insn " ft2,ft0\n\tfmv.x.d %0,ft2" \
  : "=r"(_r) : "r"(a) : "ft0", "ft2"); _r; })
#define D3(insn) ({ uint64_t _r; asm volatile("fmv.d.x ft0,%1\n\tfmv.d.x ft1,%2\n\tfmv.d.x ft3,%3\n\t" insn " ft2,ft0,ft1,ft3\n\tfmv.x.d %0,ft2" \
  : "=r"(_r) : "r"(a), "r"(b), "r"(c) : "ft0", "ft1", "ft2", "ft3"); _r; })
#define DX(insn) ({ uint64_t _r; asm volatile("fmv.d.x ft0,%1\n\tfmv.d.x ft1,%2\n\t" insn " %0,ft0,ft1" \
  : "=r"(_r) : "r"(a), "r"(b) : "ft0", "ft1"); _r; })
#define DTOI(insn) ({ uint64_t _r; asm volatile("fmv.d.x ft0,%1\n\t" insn " %0,ft0" : "=r"(_r) : "r"(a) : "ft0"); _r; })
#define ITOD(insn) ({ uint64_t _r; asm volatile(insn " ft2,%1\n\tfmv.x.d %0,ft2" : "=r"(_r) : "r"(i) : "ft2"); _r; })

#define S2(insn) ({ uint64_t _r; asm volatile("fmv.w.x ft0,%1\n\tfmv.w.x ft1,%2\n\t" insn " ft2,ft0,ft1\n\tfmv.x.w %0,ft2" \
  : "=r"(_r) : "r"(sa), "r"(sb) : "ft0", "ft1", "ft2"); _r; })
#define S1(insn) ({ uint64_t _r; asm volatile("fmv.w.x ft0,%1\n\t" insn " ft2,ft0\n\tfmv.x.w %0,ft2" \
  : "=r"(_r) : "r"(sa) : "ft0", "ft2"); _r; })
#define S3(insn) ({ uint64_t _r; asm volatile("fmv.w.x ft0,%1\n\tfmv.w.x ft1,%2\n\tfmv.w.x ft3,%3\n\t" insn " ft2,ft0,ft1,ft3\n\tfmv.x.w %0,ft2" \
  : "=r"(_r) : "r"(sa), "r"(sb), "r"(sc) : "ft0", "ft1", "ft2", "ft3"); _r; })
#define SX(insn) ({ uint64_t _r; asm volatile("fmv.w.x ft0,%1\n\tfmv.w.x ft1,%2\n\t" insn " %0,ft0,ft1" \
  : "=r"(_r) : "r"(sa), "r"(sb) : "ft0", "ft1"); _r; })
#define STOI(insn) ({ uint64_t _r; asm volatile("fmv.w.x ft0,%1\n\t" insn " %0,ft0" : "=r"(_r) : "r"(sa) : "ft0"); _r; })
#define ITOS(insn) ({ uint64_t _r; asm volatile(insn " ft2,%1\n\tfmv.x.w %0,ft2" : "=r"(_r) : "r"(i) : "ft2"); _r; })

#define M(x) do { mix(&hfp, (x)); mix(&hfp, take_flags()); } while (0)

static void fp_iter(void)
{
  uint64_t a = pick_d(), b = pick_d(), c = pick_d(), i = pick_int();
  uint64_t sa = pick_s(), sb = pick_s(), sc = pick_s();
  set_rm(rnd() % 5);
  take_flags();
  M(D2("fadd.d")); M(D2("fsub.d")); M(D2("fmul.d")); M(D2("fdiv.d")); M(D1("fsqrt.d"));
  M(D2("fmin.d")); M(D2("fmax.d")); M(D2("fsgnj.d")); M(D2("fsgnjn.d")); M(D2("fsgnjx.d"));
  M(DX("feq.d")); M(DX("flt.d")); M(DX("fle.d")); M(DTOI("fclass.d"));
  M(D3("fmadd.d")); M(D3("fmsub.d")); M(D3("fnmadd.d")); M(D3("fnmsub.d"));
  M(DTOI("fcvt.w.d")); M(DTOI("fcvt.wu.d")); M(DTOI("fcvt.l.d")); M(DTOI("fcvt.lu.d"));
  { uint64_t r; asm volatile("fmv.d.x ft0,%1\n\tfcvt.w.d %0,ft0,rtz" : "=r"(r) : "r"(a) : "ft0"); M(r);   /* static rm */
    asm volatile("fmv.w.x ft0,%1\n\tfcvt.lu.s %0,ft0,rup" : "=r"(r) : "r"(sa) : "ft0"); M(r); }
  M(ITOD("fcvt.d.w")); M(ITOD("fcvt.d.wu")); M(ITOD("fcvt.d.l")); M(ITOD("fcvt.d.lu"));
  M(D1("fcvt.s.d")); M(S1("fcvt.d.s"));
  M(S2("fadd.s")); M(S2("fsub.s")); M(S2("fmul.s")); M(S2("fdiv.s")); M(S1("fsqrt.s"));
  M(S2("fmin.s")); M(S2("fmax.s")); M(S2("fsgnjn.s"));
  M(SX("feq.s")); M(SX("flt.s")); M(SX("fle.s")); M(STOI("fclass.s"));
  M(S3("fmadd.s")); M(S3("fmsub.s")); M(S3("fnmadd.s")); M(S3("fnmsub.s"));
  M(STOI("fcvt.w.s")); M(STOI("fcvt.wu.s")); M(STOI("fcvt.l.s")); M(STOI("fcvt.lu.s"));
  M(ITOS("fcvt.s.w")); M(ITOS("fcvt.s.wu")); M(ITOS("fcvt.s.l")); M(ITOS("fcvt.s.lu"));
  { /* a single op on an improperly NaN-boxed register: must read as the canonical NaN */
    uint64_t r; asm volatile("fmv.d.x ft0,%1\n\tfadd.s ft2,ft0,ft0\n\tfmv.x.d %0,ft2" : "=r"(r) : "r"(a) : "ft0", "ft2"); M(r); }
  { /* FP loads (recode on the load write port) of raw bits, stored back as 64 bits */
    volatile uint64_t mem[2] = {a, sa | (b << 32)}; uint64_t r0, r1, r2;
    asm volatile("fld ft0,0(%3)\n\tflw ft1,8(%3)\n\tflw ft2,12(%3)\n\t"
                 "fsd ft0,0(%3)\n\tfmv.x.d %0,ft1\n\tfadd.s ft3,ft1,ft2\n\tfmv.x.d %1,ft3\n\tfsgnj.d ft4,ft0,ft0\n\tfmv.x.d %2,ft4"
                 : "=&r"(r0), "=&r"(r1), "=&r"(r2) : "r"((uint64_t *)mem) : "ft0", "ft1", "ft2", "ft3", "ft4", "memory");
    M(r0); M(r1); M(r2); M(mem[0]); }
  { /* dependent chain: every result used right away (FMA/div/sqrt/convert latencies, FP bypass) */
    uint64_t r, t;
    asm volatile("fmv.d.x ft0,%2\n\tfmv.d.x ft1,%3\n\tfmv.d.x ft2,%4\n\t"
                 "fmadd.d ft3,ft0,ft1,ft2\n\tfadd.d ft4,ft3,ft0\n\tfdiv.d ft5,ft4,ft1\n\tfsqrt.d ft6,ft5\n\t"
                 "fmul.d ft7,ft6,ft3\n\tfcvt.s.d ft8,ft7\n\tfmadd.s ft9,ft8,ft8,ft8\n\tfcvt.d.s ft10,ft9\n\t"
                 "fcvt.l.d %1,ft10\n\tfcvt.d.l ft11,%1\n\tfsub.d ft11,ft11,ft10\n\tfmv.x.d %0,ft11"
                 : "=r"(r), "=&r"(t) : "r"(a), "r"(b), "r"(c)
                 : "ft0", "ft1", "ft2", "ft3", "ft4", "ft5", "ft6", "ft7", "ft8", "ft9", "ft10", "ft11");
    M(r); M(t); }
  { /* convert int -> fp -> int straight through */
    uint64_t r; asm volatile("fcvt.d.l ft0,%1\n\tfcvt.l.d %0,ft0" : "=r"(r) : "r"(i) : "ft0"); M(r);
    asm volatile("fcvt.s.wu ft0,%1\n\tfcvt.wu.s %0,ft0" : "=r"(r) : "r"(i) : "ft0"); M(r); }
}

/* ---------- integer mul/div ---------- */
#define I2(insn) ({ uint64_t _r; asm volatile(insn " %0,%1,%2" : "=r"(_r) : "r"(x), "r"(y)); _r; })
#define MI(v) mix(&hint, (v))

static void int_iter(void)
{
  uint64_t x = pick_int(), y = pick_int();
  MI(I2("mul")); MI(I2("mulh")); MI(I2("mulhsu")); MI(I2("mulhu")); MI(I2("mulw"));
  MI(I2("div")); MI(I2("divu")); MI(I2("rem")); MI(I2("remu"));
  MI(I2("divw")); MI(I2("divuw")); MI(I2("remw")); MI(I2("remuw"));
  MI(I2("add")); MI(I2("sub")); MI(I2("sll")); MI(I2("srl")); MI(I2("sra")); MI(I2("slt")); MI(I2("sltu"));
  MI(I2("addw")); MI(I2("subw")); MI(I2("sllw")); MI(I2("srlw")); MI(I2("sraw"));
  { /* results used at once, mul feeding div */
    uint64_t r; asm volatile("mul %0,%1,%2\n\tdiv %0,%0,%2\n\taddi %0,%0,1\n\tremu %0,%1,%0\n\tsub %0,%0,%2" : "=&r"(r) : "r"(x), "r"(y)); MI(r); }
  { /* branch on a compare of the result right away (branch target / mispredict paths) */
    uint64_t r = 0; asm volatile("slt t0,%1,%2\n\tbnez t0,1f\n\taddi %0,%0,3\n1:\tmulh t1,%1,%2\n\tbgez t1,2f\n\taddi %0,%0,5\n2:"
                                 : "+r"(r) : "r"(x), "r"(y) : "t0", "t1"); MI(r); }
}

/* ---------- loads used by the next instruction ---------- */
static uint64_t buf[16] __attribute__((aligned(64)));
#define LU(insn, al) ({ uint64_t _r; uint64_t _o = off & ~(uint64_t)((al) - 1); \
  asm volatile(insn " t0,0(%1)\n\tadd %0,t0,%2" : "=r"(_r) : "r"((char *)buf + _o), "r"(k) : "t0", "memory"); _r; })
#define ML(v) mix(&hld, (v))

static void ld_iter(void)
{
  for (int j = 0; j < 16; j++) buf[j] = (rnd() & 1) ? rnd() : pick_int();
  uint64_t off = rnd() % 120, k = rnd();
  ML(LU("lb", 1)); ML(LU("lbu", 1)); ML(LU("lh", 2)); ML(LU("lhu", 2));
  ML(LU("lw", 4)); ML(LU("lwu", 4)); ML(LU("ld", 8));
  { /* loaded value as an address and as a branch condition right away */
    uint64_t r = 0, o = off & ~7ull; buf[(o >> 3) ^ 1] = (uintptr_t)&buf[(k >> 4) & 15];
    asm volatile("ld t0,0(%1)\n\tld t1,0(t0)\n\tlb t2,3(t0)\n\tbltz t2,1f\n\taddi %0,%0,1\n1:\tadd %0,%0,t1"
                 : "+r"(r) : "r"((char *)buf + (o ^ 8)) : "t0", "t1", "t2", "memory"); ML(r); }
  { /* store then load of the same bytes at once */
    uint64_t r, o = off & ~7ull;
    asm volatile("sb %2,1(%1)\n\tlh t0,0(%1)\n\tsh %2,4(%1)\n\tlwu t1,4(%1)\n\tadd %0,t0,t1\n\tsw %2,0(%1)\n\tlb t0,2(%1)\n\tadd %0,%0,t0"
                 : "=&r"(r) : "r"((char *)buf + o), "r"(k) : "t0", "t1", "memory"); ML(r); }
  { /* AMOs with the old value used at once */
    uint64_t r, o = off & ~7ull;
    asm volatile("amoadd.d t0,%2,(%1)\n\taddi %0,t0,7\n\tamoxor.w t1,%0,(%1)\n\tadd %0,%0,t1\n\tlr.d t0,(%1)\n\tsc.d t1,%0,(%1)\n\tadd %0,%0,t1"
                 : "=&r"(r) : "r"((char *)buf + o), "r"(k) : "t0", "t1", "memory"); ML(r); }
  for (int j = 0; j < 16; j++) ML(buf[j]);
}

int main(void)
{
  for (uint64_t it = 1; it <= ITERS; it++) {
    fp_iter(); int_iter(); ld_iter();
    if (it % EVERY == 0) { report("fp", it, hfp); report("int", it, hint); report("ld", it, hld); }
  }
  return 0;
}
