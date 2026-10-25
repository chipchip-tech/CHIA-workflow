CONFIG AUDIT. Review the Chipyard SoC configuration `chipyard.RocketConfig` and find blocks
that this SoC does not need: things UC Berkeley put into the default configuration (for
bring-up, debug, other projects or demos) and never removed. You only read and report. The
loop will build and test every candidate you propose.

## The system

- `RocketConfig` = `WithNHugeCores(1) ++ chipyard.config.AbstractConfig`
  (`/home/ray/chipyard/generators/chipyard/src/main/scala/config/RocketConfigs.scala` and
  `.../config/AbstractConfig.scala`). Fragments come from `generators/chipyard`,
  `generators/testchipip`, `generators/rocket-chip`, `generators/rocket-chip-blocks` (sifive
  blocks), `generators/rocket-chip-inclusive-cache` and others under
  `/home/ray/chipyard/generators/`.
- The digital top is `generators/chipyard/src/main/scala/DigitalTop.scala`; IO and test
  harness binders are in `generators/chipyard/src/main/scala/{iobinders,harness}/`.
- Programs are run by the Verilator test harness: they are loaded over TSI (serial TileLink)
  into the backing DRAM and they report results (including `mcycle`) through HTIF `tohost`.
  Whatever that path needs is required.

## What counts as "not needed"

A block is a candidate when removing it (with an existing config fragment) still lets the
SoC run every riscv-test and benchmark through the current harness with exactly the same
`mcycle`. Typical places to look: debug module / JTAG / DMI / system-bus access, extra
peripherals (UART, GPIO, SPI, I2C, PWM, timers), boot-select pins and boot-address
registers, clock taps, chip-id pins, on-chip scratchpads, extra ports, unused buses.

Out of scope: the core and its caches (`WithNHugeCores`), the L2 (`WithInclusiveCache`),
the bus topology and anything that changes performance.

For each candidate also say whether a real chip would still need it (for example JTAG debug
is needed for bring-up of real silicon even if the simulation does not use it).

## Inputs (on the chipyard worker, through `chipyard_bash`; each command is limited to 120 s)

- `$STAGE_DIR/area_by_block.txt`: Yosys area (sky130hd) of the generated `ChipTop`, per
  instance down to depth 6. `RocketTile` and the SRAM macros (`*_ext`) are black boxes.
- `$STAGE_DIR/sram_macros.txt`: every SRAM macro and its size in bits.
- `$STAGE_DIR/modules.txt`: every module under `ChipTop`.
- `$STAGE_DIR/baseline_cycles.txt`: benchmark `mcycle` of the baseline.
- Generated Verilog of the baseline:
  `/home/ray/chipyard/sims/verilator/generated-src/chipyard.harness.TestHarness.RocketConfig/gen-collateral/`
  (it may be missing; the Scala sources are the reference).

Top of the area table:

```
$AREA
```

Baseline cycles:

```
$CYCLES
```

## Rules

- Do NOT edit, create or delete any file under `/home/ray/chipyard` (the loop checks the
  tree and reverts every change). Do NOT run `make`, `sbt`, `verilator`, `yosys` or any build.
- Every fragment you propose must already exist. Find its definition with `grep -rn "class
  WithNoUART" /home/ray/chipyard/generators --include=*.scala` (or similar) and give the fully
  qualified name. Only plain expressions of the form `new a.b.ClassName` or
  `new a.b.ClassName(args)` are accepted, e.g. `new chipyard.config.WithNoUART`. Keep in mind
  that a fragment placed in front of `RocketConfig` overrides the same key set further down.
- If a block can only be removed by changing Scala code (not a config fragment), put it in
  `notes` of a candidate with an empty `removal`, or mention it in your final reply.
- At most $MAX candidates, the most promising (largest area or clearest leftover) first.

## Output

Write the candidates to `$CANDIDATES` (outside the chipyard tree, so writing it is allowed),
for example with a heredoc, as a JSON list:

```json
[
  {
    "id": "uart",
    "block": "ChipTop.system.uartClockDomainWrapper (TLUART)",
    "category": "peripheral",
    "comes_from": "new chipyard.config.WithUART in AbstractConfig.scala:85",
    "removal": ["new chipyard.config.WithNoUART"],
    "evidence": "why the SoC does not need it: who uses it, what the harness does with it",
    "needed_for_tapeout": "no / yes / maybe, and why",
    "expected_area_um2": 12345,
    "notes": ""
  }
]
```

`category` is one of `debug`, `peripheral`, `boot`, `clocking`, `memory`, `port`, `other`.

Finish with a short report: the candidates in order, and anything you found that cannot be
removed with an existing fragment but looks like a leftover.
