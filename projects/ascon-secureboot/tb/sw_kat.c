// Native check of ascon_hash256_sw() from sw/boot.c against ascon.py vectors.
// stdin: lines "<message hex|-> <digest hex>" from tb/gen_vectors.py.
#define main boot_main
#include "../sw/boot.c"
#undef main

#include <stdlib.h>

static uint8_t buf[8192];

int main(void) {
  static char mhex[2 * sizeof(buf) + 2];
  char dhex[80];
  int n = 0, fails = 0;
  while (scanf("%16385s %79s", mhex, dhex) == 2) {
    size_t len = 0;
    if (strcmp(mhex, "-") != 0)
      for (; mhex[2 * len]; len++) {
        char b[3] = {mhex[2 * len], mhex[2 * len + 1], 0};
        buf[len] = (uint8_t)strtoul(b, NULL, 16);
      }
    uint8_t d[32];
    char got[65];
    ascon_hash256_sw(buf, len, d);
    for (int i = 0; i < 32; i++) sprintf(got + 2 * i, "%02x", d[i]);
    n++;
    if (strcmp(got, dhex) != 0) {
      fails++;
      printf("FAIL len=%zu got=%s want=%s\n", len, got, dhex);
    }
  }
  printf("%d/%d SW digests match ascon.py\n", n - fails, n);
  return fails ? 1 : 0;
}
