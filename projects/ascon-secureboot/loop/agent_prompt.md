You are working inside a Chipyard checkout at /home/ray/chipyard (branch
chia_artifact, Rocket core, Chisel). Use the `chipyard_bash` tool to read and
edit files. This is attempt {{ITER}} of at most {{MAX_ITERS}}.

# Goal

Add a RoCC accelerator that computes Ascon-Hash256 (NIST SP 800-232) on top of
an existing SystemVerilog Ascon core, and a Chipyard config that adds it to
Rocket:

    chipyard.AsconRocketConfig = RocketConfig + this RoCC on custom0

A bare-metal boot loader (written by someone else, you cannot see or change
it) hashes a 4 KiB firmware image with this accelerator and compares the
digest with a C implementation.

# Hard rules

1. Do NOT run make, sbt, mill, verilator, gcc or any build/simulation. The
   loop builds `AsconRocketConfig` and runs the tests after you finish, and
   feeds the errors back to you.
2. Edit files ONLY under generators/chipyard/. Do not touch
   generators/rocket-chip or any other submodule (read them as reference).
3. Do NOT edit generators/chipyard/src/main/resources/vsrc/AsconCoreV4.sv.
4. No network access is needed; do not try to download anything.
5. No git commit / stash / reset / checkout: the loop diffs the tree against
   HEAD after every attempt.
6. Finish with a short summary: files created/changed and what each does.

# The Ascon core you must wrap (already in the tree)

File: generators/chipyard/src/main/resources/vsrc/AsconCoreV4.sv
Top module `AsconCoreV4` (64-bit data bus, 1 round per cycle). Ports:

    input  clk                    input  [63:0] key        (tie 0)
    input  rst   (active high)    input         key_valid  (tie 0)
                                  output        key_ready  (unused)
    input  [63:0] bdi             output [63:0] bdo
    input  [7:0]  bdi_valid       output        bdo_valid
    output        bdi_ready       input         bdo_ready
    input  [3:0]  bdi_type        output [3:0]  bdo_type
    input         bdi_eot         output        bdo_eot
    input         bdi_eoi         input         bdo_eoo    (tie 0)
    input  [3:0]  mode            output        auth, auth_valid (unused)

Values: mode M_HASH256 = 3 (0 = no request); bdi_type D_MSG = 3;
bdo_type D_HASH = 5. A Chisel BlackBox must use exactly these port names
(BlackBox ports are emitted without an io_ prefix) and
`addResource("/vsrc/AsconCoreV4.sv")` (see HasBlackBoxResource in
generators/chipyard/src/main/scala/example/GCD.scala).

Behaviour of the core in hash mode (verified in simulation, rely on it):

* bdi handshake: a word is consumed in a cycle where bdi_valid != 0 and
  bdi_ready = 1. bdi_ready is 0 while the core permutes (12 cycles after the
  start pulse and after every full word).
* Byte i of the 8-byte word is bdi[8i+7:8i]; bdi_valid = (1<<nbytes)-1. A word
  with bdi_valid != 0xFF ends the message, so only the LAST word may be
  partial. The core does the padding itself.
* `mode` is level-sensitive: whenever the core is idle and mode != 0 it STARTS
  a new hash. Drive mode = 3 for exactly ONE cycle to start, then 0. Keep
  bdi_eoi = 0 during that start cycle (bdi_eoi = 1 there means "empty
  message").
* With bdi_eot = bdi_eoi = 1 on the last word, the core finalizes and then
  outputs the 256-bit digest as 4 sequential 64-bit words on bdo
  (bdo_valid = 1, bdo_type = 5), with 12 idle cycles between words. Word k
  holds digest bytes 8k..8k+7, little-endian. The core returns to idle only
  after all 4 words were taken (bdo_valid && bdo_ready).
* Pulsing rst for one cycle returns it to idle from any state.
* A whole 4 KiB hash takes about 6700 core cycles.

# RoCC interface (fixed; the boot loader uses exactly this)

custom0 (opcode 0x0B). funct3 = {xd, xs1, xs2}.

| funct7 | name    | funct3 | operands | semantics |
|---|---|---|---|---|
| 0 | ASCON_INIT    | 000 (no regs)  | - | reset the core (rst one cycle), clear the digest buffer, then start a new hash (mode = 3 for one cycle) |
| 1 | ASCON_ABSORB  | 011 (rs1, rs2) | rs1 = data, rs2 = nbytes \| (last << 8) | push one 64-bit word into bdi: bdi_valid = (1<<nbytes)-1, bdi_type = 3, bdi_eot = bdi_eoi = last. nbytes is 1..8, < 8 only when last = 1. No response (xd = 0). |
| 2 | ASCON_SQUEEZE | 110 (rd, rs1)  | rs1 = index 0..3 | rd <- digest word rs1[1:0]; block until that word is available |

Implementation requirements:

* Backpressure through io.cmd.ready / io.busy. Never drop or duplicate a
  word: hold an ABSORB (deassert cmd.ready, or keep a one-word buffer) until
  the core took it.
* Capture the 4 digest words into a 4 x 64-bit buffer as the core produces
  them (keep bdo_ready = 1 so the core can finish and go idle). SQUEEZE reads
  from this buffer; respond (io.resp.valid, resp.bits.rd = cmd.bits.inst.rd)
  only once that word is captured, and hold the response until resp.ready.
* io.busy = 1 while a command, a pending word, a pending response or a hash
  in progress exists. io.interrupt := false.B. Do not use memory:
  io.mem.req.valid := false.B.
* The core's rst = module reset OR the one-cycle INIT reset.

The boot loader does, per hash: INIT; ABSORB x 512 (the last one with
nbytes = 8, last = 1); SQUEEZE 0, 1, 2, 3. A second test hashes 4093 bytes
(511 full words, then a last word with nbytes = 5), but first starts a hash
and abandons it after 2 ABSORBs by issuing INIT again, and reads the digest
with SQUEEZE 3, 2, 1, 0. In C:

    asm volatile(".insn r 0x0B, 0, 0, x0, x0, x0");                 // INIT
    asm volatile(".insn r 0x0B, 3, 1, x0, %0, %1" :: "r"(w), "r"(n | (last << 8)));
    asm volatile(".insn r 0x0B, 6, 2, %0, %1, x0" : "=r"(d) : "r"(k)); // SQUEEZE

# What to write (suggested layout)

* generators/chipyard/src/main/scala/example/AsconRoCC.scala
  - `class AsconCoreV4 extends BlackBox with HasBlackBoxResource`
  - `class AsconRoCC(opcodes: OpcodeSet)(implicit p: Parameters) extends LazyRoCC(opcodes)`
    with `override lazy val module = new AsconRoCCModuleImp(this)`
  - `class AsconRoCCModuleImp(outer: AsconRoCC)(implicit p: Parameters) extends LazyRoCCModuleImp(outer)`
* a config fragment `chipyard.config.WithAsconRoCC` modelled on
  `WithAccumulatorRoCC` in
  generators/chipyard/src/main/scala/config/fragments/RoCCFragments.scala
  (BuildRoCC, OpcodeSet.custom0)
* generators/chipyard/src/main/scala/config/AsconConfigs.scala:

      class AsconRocketConfig extends Config(
        new chipyard.config.WithAsconRoCC ++
        new freechips.rocketchip.rocket.WithNHugeCores(1) ++
        new chipyard.config.AbstractConfig)

  (in `package chipyard`, the same as RocketConfig in RocketConfigs.scala).

The RoCC API of this rocket-chip version is in
generators/rocket-chip/src/main/scala/tile/LazyRoCC.scala (read only); the
AccumulatorExample there is a good reference.

# Feedback from the previous attempt

{{FEEDBACK}}
