// boot.c: bare-metal integrity-check "secure boot" for Rocket + Ascon RoCC.
//
// Hashes the firmware image in image.h with Ascon-Hash256 (NIST SP 800-232),
// compares it with the stored digest and prints BOOT OK / BOOT FAIL.
//
//   (1) ascon_hash256_sw   : portable C, the software baseline
//   (2) ascon_hash256_rocc : the RoCC accelerator on custom0, via .insn
//   (3) rdcycle around both
//
// Build flags:
//   -DSW_ONLY  no RoCC instruction at all (runs on the stock RocketConfig);
//              the verdict then comes from the SW digest.
//   -DTAMPER   flip one bit of the image before hashing (boot_tamper test).
//   -DPARTIAL  boot_partial test: hash only the first PARTIAL_SIZE bytes (the
//              last RoCC word is partial) against partial_digest, abandon a
//              hash half-way with a second INIT first, and SQUEEZE 3, 2, 1, 0.
//
// Output lines parsed by the loop:
//   TEST=<boot_good|boot_tamper|boot_partial> MODE=<SW_ONLY|ROCC>
//   DIGEST_SW=<64 hex>   DIGEST_HW=<64 hex|NA>
//   CYCLES_SW=<n>        CYCLES_ROCC=<n|NA>
//   BOOT OK | BOOT FAIL
//
// Exit code: 0, or 3 when DIGEST_HW != DIGEST_SW (accelerator is wrong).
//
// It also compiles natively (x86 gcc) for scripts/selftest.sh: there rdcycle
// reads 0 and the RoCC path is unavailable, so build with -DSW_ONLY.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "image.h"

#if defined(TAMPER)
#define TEST_NAME "boot_tamper"
#elif defined(PARTIAL)
#define TEST_NAME "boot_partial"
#else
#define TEST_NAME "boot_good"
#endif

#ifdef PARTIAL
#ifndef PARTIAL_SIZE
#error "image.h has no PARTIAL_SIZE: regenerate it with scripts/gen_image.py"
#endif
#define HASH_LEN PARTIAL_SIZE
#define STORED_DIGEST partial_digest
#else
#define HASH_LEN IMAGE_SIZE
#define STORED_DIGEST image_digest
#endif

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static inline uint64_t rdcycle64(void) {
#ifdef __riscv
  uint64_t c;
  asm volatile("rdcycle %0" : "=r"(c));
  return c;
#else
  return 0;
#endif
}

static inline uint64_t load64_le(const uint8_t *p, size_t n) {
  uint64_t w = 0;
  for (size_t i = 0; i < n; i++) w |= (uint64_t)p[i] << (8 * i);
  return w;
}

static inline void store64_le(uint8_t *p, uint64_t w) {
  for (int i = 0; i < 8; i++) p[i] = (uint8_t)(w >> (8 * i));
}

static void print_digest(const char *label, const uint8_t d[32]) {
  printf("%s=", label);
  for (int i = 0; i < 32; i++) printf("%02x", d[i]);
  printf("\n");
}

static int digest_eq(const uint8_t a[32], const uint8_t b[32]) {
  uint8_t diff = 0;
  for (int i = 0; i < 32; i++) diff |= a[i] ^ b[i];
  return diff == 0;
}

// ---------------------------------------------------------------------------
// (1) Ascon-Hash256 in software (SP 800-232, little-endian lanes)
// ---------------------------------------------------------------------------

#define ASCON_HASH256_IV 0x0000080100cc0002ULL

static inline uint64_t ror64(uint64_t x, int n) { return (x >> n) | (x << (64 - n)); }

static void ascon_p12(uint64_t s[5]) {
  uint64_t x0 = s[0], x1 = s[1], x2 = s[2], x3 = s[3], x4 = s[4];
  for (int r = 0; r < 12; r++) {
    // constant addition: 0xf0, 0xe1, ..., 0x4b
    x2 ^= (uint64_t)(((0xfu - r) << 4) | r);
    // substitution layer (bitsliced 5-bit S-box)
    x0 ^= x4;
    x4 ^= x3;
    x2 ^= x1;
    uint64_t t0 = ~x0 & x1, t1 = ~x1 & x2, t2 = ~x2 & x3, t3 = ~x3 & x4, t4 = ~x4 & x0;
    x0 ^= t1;
    x1 ^= t2;
    x2 ^= t3;
    x3 ^= t4;
    x4 ^= t0;
    x1 ^= x0;
    x0 ^= x4;
    x3 ^= x2;
    x2 = ~x2;
    // linear diffusion layer
    x0 ^= ror64(x0, 19) ^ ror64(x0, 28);
    x1 ^= ror64(x1, 61) ^ ror64(x1, 39);
    x2 ^= ror64(x2, 1) ^ ror64(x2, 6);
    x3 ^= ror64(x3, 10) ^ ror64(x3, 17);
    x4 ^= ror64(x4, 7) ^ ror64(x4, 41);
  }
  s[0] = x0; s[1] = x1; s[2] = x2; s[3] = x3; s[4] = x4;
}

static void ascon_hash256_sw(const uint8_t *msg, size_t len, uint8_t out[32]) {
  uint64_t s[5] = {ASCON_HASH256_IV, 0, 0, 0, 0};
  ascon_p12(s);
  while (len >= 8) {
    s[0] ^= load64_le(msg, 8);
    ascon_p12(s);
    msg += 8;
    len -= 8;
  }
  s[0] ^= load64_le(msg, len) ^ (0x01ULL << (8 * len));  // last block + padding
  ascon_p12(s);
  for (int k = 0; k < 4; k++) {
    store64_le(out + 8 * k, s[0]);
    if (k < 3) ascon_p12(s);
  }
}

// ---------------------------------------------------------------------------
// (2) Ascon-Hash256 on the RoCC accelerator (custom0 = opcode 0x0B)
//
//   .insn r opcode, funct3, funct7, rd, rs1, rs2   with funct3 = {xd,xs1,xs2}
//   INIT    funct7=0 funct3=000 : reset the core, start a new hash
//   ABSORB  funct7=1 funct3=011 : rs1 = 8 message bytes (little-endian),
//                                 rs2 = nbytes | (last << 8), nbytes 1..8,
//                                 nbytes < 8 only together with last = 1
//   SQUEEZE funct7=2 funct3=110 : rd <- digest word rs1 (0..3), little-endian;
//                                 blocks until that word is available
// ---------------------------------------------------------------------------

#ifndef SW_ONLY
static inline void rocc_init(void) {
  asm volatile(".insn r 0x0B, 0, 0, x0, x0, x0" ::: "memory");
}

static inline void rocc_absorb(uint64_t data, uint64_t ctl) {
  asm volatile(".insn r 0x0B, 3, 1, x0, %0, %1" ::"r"(data), "r"(ctl) : "memory");
}

static inline uint64_t rocc_squeeze(uint64_t idx) {
  uint64_t r;
  asm volatile(".insn r 0x0B, 6, 2, %0, %1, x0" : "=r"(r) : "r"(idx) : "memory");
  return r;
}

// Requires len >= 1 (the interface cannot express an empty message).
// reverse != 0 reads the digest words in the order 3, 2, 1, 0.
static void ascon_hash256_rocc(const uint8_t *msg, size_t len, uint8_t out[32], int reverse) {
  rocc_init();
  while (len > 8) {
    uint64_t w;
    memcpy(&w, msg, 8);  // image is 8-byte aligned; RV64 is little-endian
    rocc_absorb(w, 8);
    msg += 8;
    len -= 8;
  }
  rocc_absorb(load64_le(msg, len), len | (1u << 8));
  for (uint64_t j = 0; j < 4; j++) {
    uint64_t k = reverse ? 3 - j : j;
    store64_le(out + 8 * k, rocc_squeeze(k));
  }
}
#endif

// RoCC instructions trap as illegal while mstatus.XS == 0.
static void enable_rocc(void) {
#ifdef __riscv
  unsigned long xs = 3ul << 15;
  asm volatile("csrs mstatus, %0" ::"r"(xs));
#endif
}

// ---------------------------------------------------------------------------

int main(void) {
  uint8_t d_sw[32];
  int rc = 0;

  enable_rocc();
#ifdef TAMPER
  image[TAMPER_OFFSET] ^= TAMPER_MASK;
#endif

#ifdef SW_ONLY
  printf("TEST=%s MODE=SW_ONLY\n", TEST_NAME);
#else
  printf("TEST=%s MODE=ROCC\n", TEST_NAME);
#endif

  uint64_t t0 = rdcycle64();
  ascon_hash256_sw(image, HASH_LEN, d_sw);
  uint64_t t1 = rdcycle64();
  print_digest("DIGEST_SW", d_sw);

#ifdef SW_ONLY
  printf("DIGEST_HW=NA\n");
  printf("CYCLES_SW=%lu\n", (unsigned long)(t1 - t0));
  printf("CYCLES_ROCC=NA\n");
  const uint8_t *verdict = d_sw;
#else
  uint8_t d_hw[32];
#ifdef PARTIAL
  // Start a hash and abandon it mid-message: the INIT at the start of
  // ascon_hash256_rocc must restart the core from scratch.
  rocc_init();
  rocc_absorb(0x0123456789abcdefULL, 8);
  rocc_absorb(0xfedcba9876543210ULL, 8);
  const int reverse = 1;
#else
  const int reverse = 0;
#endif
  uint64_t t2 = rdcycle64();
  ascon_hash256_rocc(image, HASH_LEN, d_hw, reverse);
  uint64_t t3 = rdcycle64();
  print_digest("DIGEST_HW", d_hw);
  printf("CYCLES_SW=%lu\n", (unsigned long)(t1 - t0));
  printf("CYCLES_ROCC=%lu\n", (unsigned long)(t3 - t2));
  if (!digest_eq(d_hw, d_sw)) {
    printf("ERROR: DIGEST_HW != DIGEST_SW\n");
    rc = 3;
  }
  const uint8_t *verdict = d_hw;
#endif

  printf(digest_eq(verdict, STORED_DIGEST) ? "BOOT OK\n" : "BOOT FAIL\n");
  return rc;
}
