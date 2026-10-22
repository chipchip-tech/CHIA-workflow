// Standalone Verilator testbench for rtl/AsconCoreV4.sv (self-test only; the
// CHIA loop does not use it).
//
// Drives the core exactly the way the RoCC wrapper is specified to, so the
// interface assumptions are checked before any agent writes Chisel:
//   INIT    -> rst for one cycle, then mode = M_HASH256 for ONE cycle
//              (bdi_eoi = 0); holding mode would restart the hash in IDLE
//   ABSORB  -> one 64-bit little-endian word on bdi, bdi_valid = (1<<n)-1,
//              bdi_type = D_MSG, bdi_eot = bdi_eoi = last; wait for bdi_ready
//   SQUEEZE -> collect the 4 digest words from bdo (bdo_type = D_HASH)
//
// stdin: one vector per line, "<message hex> <digest hex>" (message >= 1 byte).
// Each vector runs in four flavours: with/without a reset between hashes, and
// with/without random input gaps and output stalls.
// Prints cycles from the mode pulse to the last digest word.

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <random>
#include <string>
#include <vector>

#include "VAsconCoreV4.h"
#include "verilated.h"

static const int M_HASH256 = 3, D_MSG = 3, D_HASH = 5;

struct Tb {
  std::unique_ptr<VAsconCoreV4> top{new VAsconCoreV4};
  uint64_t cycle = 0;

  void tick() {
    top->clk = 0;
    top->eval();
    top->clk = 1;
    top->eval();
    cycle++;
  }
  void settle() {  // evaluate combinational outputs for the current inputs
    top->clk = 0;
    top->eval();
  }
  void idle_inputs() {
    top->key = 0;
    top->key_valid = 0;
    top->bdi = 0;
    top->bdi_valid = 0;
    top->bdi_type = 0;
    top->bdi_eot = 0;
    top->bdi_eoi = 0;
    top->mode = 0;
    top->bdo_ready = 0;
    top->bdo_eoo = 0;
  }
};

static std::vector<uint8_t> from_hex(const std::string &s) {
  std::vector<uint8_t> v;
  for (size_t i = 0; i + 1 < s.size(); i += 2) v.push_back(std::stoi(s.substr(i, 2), nullptr, 16));
  return v;
}

static std::string to_hex(const std::vector<uint8_t> &v) {
  static const char *h = "0123456789abcdef";
  std::string s;
  for (uint8_t b : v) {
    s += h[b >> 4];
    s += h[b & 15];
  }
  return s;
}

// Returns the digest, or empty on timeout. *cycles = mode pulse .. last word.
static std::vector<uint8_t> hash(Tb &tb, const std::vector<uint8_t> &msg, bool reset,
                                 bool stalls, std::mt19937 &rng, uint64_t *cycles) {
  auto *t = tb.top.get();
  const uint64_t limit = tb.cycle + 100000;
  tb.idle_inputs();
  if (reset) {  // INIT: reset the core for one cycle
    t->rst = 1;
    tb.tick();
    t->rst = 0;
  }
  uint64_t start = tb.cycle;
  t->mode = M_HASH256;  // ...then a ONE-cycle start pulse
  tb.tick();
  t->mode = 0;

  for (size_t off = 0; off < msg.size(); off += 8) {
    size_t n = std::min<size_t>(8, msg.size() - off);
    bool last = off + n == msg.size();
    uint64_t w = 0;
    for (size_t i = 0; i < n; i++) w |= uint64_t(msg[off + i]) << (8 * i);
    while (stalls && rng() % 4 == 0) {  // software is slow to issue the next ABSORB
      tb.tick();
      if (tb.cycle > limit) return {};
    }
    t->bdi = w;
    t->bdi_valid = (n == 8) ? 0xff : ((1u << n) - 1);
    t->bdi_type = D_MSG;
    t->bdi_eot = last;
    t->bdi_eoi = last;
    for (;;) {
      tb.settle();
      bool fire = t->bdi_ready;
      tb.tick();
      if (fire) break;
      if (tb.cycle > limit) return {};
    }
    t->bdi = 0;
    t->bdi_valid = 0;
    t->bdi_type = 0;
    t->bdi_eot = 0;
    t->bdi_eoi = 0;
  }

  std::vector<uint8_t> out;
  while (out.size() < 32) {
    t->bdo_ready = stalls ? (rng() % 3 != 0) : 1;
    tb.settle();
    if (t->bdo_valid && t->bdo_ready) {
      if (t->bdo_type != D_HASH) {
        std::fprintf(stderr, "unexpected bdo_type %d\n", t->bdo_type);
        return {};
      }
      for (int i = 0; i < 8; i++) out.push_back(uint8_t(t->bdo >> (8 * i)));
    }
    tb.tick();
    if (tb.cycle > limit) return {};
  }
  t->bdo_ready = 0;
  *cycles = tb.cycle - start;
  // A few idle cycles: the core must stay in IDLE (mode is 0 again).
  for (int i = 0; i < 4; i++) tb.tick();
  return out;
}

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  Tb tb;
  std::mt19937 rng(1);
  tb.idle_inputs();
  tb.top->rst = 1;
  tb.tick();
  tb.tick();
  tb.top->rst = 0;

  int n = 0, fails = 0;
  std::string msg_hex, dig_hex;
  while (std::cin >> msg_hex >> dig_hex) {
    auto msg = from_hex(msg_hex);
    for (int flavour = 0; flavour < 4; flavour++) {
      bool reset = flavour & 1, stalls = flavour & 2;
      uint64_t cycles = 0;
      auto got = to_hex(hash(tb, msg, reset, stalls, rng, &cycles));
      bool ok = got == dig_hex;
      n++;
      if (!ok) fails++;
      if (!ok || (!stalls && reset && (msg.size() >= 1024 || msg.size() <= 2)))
        std::printf("%s len=%zu reset=%d stalls=%d cycles=%lu got=%s\n", ok ? "PASS" : "FAIL",
                    msg.size(), reset, stalls, (unsigned long)cycles,
                    got.empty() ? "<timeout>" : got.c_str());
    }
  }
  std::printf("%d/%d hashes match ascon.py\n", n - fails, n);
  return fails ? 1 : 0;
}
