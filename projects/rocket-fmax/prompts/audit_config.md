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
`mcycle`, AND it is not needed by the two flows this SoC is headed for:

- FPGA: Digilent Arty A7-100T (`chipyard.fpga.arty100t`, `WithArty100TTweaks ++
  WithBroadcastManager ++ <this config>`): UART console, UART-TSI, JTAG, DDR over the mem port.
- ASIC: OpenROAD flow scripts (sky130), chip set up like `chipyard.ChipLikeRocketConfig`:
  serial-TL to the bring-up host, JTAG, boot-select pin, clock tap, clock/reset.

Typical places to look: on-chip scratchpads, unused bus-error devices, extra peripherals
the default config adds but nothing uses (GPIO, SPI, I2C, PWM, timers), extra ports,
unused buses, oversized buffers or FIFOs that a fragment can shrink.

### Protected blocks (never propose removing these)

The FPGA and ASIC flows need them even though the Verilator harness does not. The loop
refuses any fragment that removes one (`WithNoDebug`, `WithDMIDTM`, `WithNoUART`,
`WithNoSerialTL*`, `WithNoMemPort`, `WithNMemoryChannels(0)`, `WithNoBootROM`, `WithNoCLINT`,
`WithNoPLIC`, `WithNoCustomBootPin`, `WithNoBootAddrReg`, `WithNoClockTap`,
`WithNoResetSynchronizers`) and also refuses any build whose `ChipTop` lost a port or module
of these blocks, so a fragment that removes one indirectly is wasted too:

```
$PROTECTED
```

Out of scope: the core and its caches (`WithNHugeCores`), the L2 (`WithInclusiveCache`),
the bus topology and anything that changes performance.

For each candidate also say whether a real chip would still need it.

## Inputs (on the chipyard worker, through `chipyard_bash`; each command is limited to 120 s)

Use the `chipyard_bash` tool for every command. Your built-in `bash` tool runs in the opencode
container, not on the chipyard worker: these files are not there, and nothing it writes is read.

- `$STAGE_DIR/area_by_block.txt`: Yosys area (sky130hd) of the generated `ChipTop`, per
  instance down to depth 6. `RocketTile` and the SRAM macros (`*_ext`) are black boxes.
- `$STAGE_DIR/sram_macros.txt`: every SRAM macro and its size in bits.
- `$STAGE_DIR/modules.txt`: every module under `ChipTop`.
- `$STAGE_DIR/baseline_cycles.txt`: benchmark `mcycle` of the baseline.
- `$STAGE_DIR/protected_blocks.txt`: the protected blocks above and where they are in `ChipTop`.
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
  WithNoBusErrorDevices" /home/ray/chipyard/generators --include=*.scala` (or similar) and give the fully
  qualified name. Only plain expressions of the form `new a.b.ClassName` or
  `new a.b.ClassName(args)` are accepted, e.g. `new chipyard.config.WithNoBusErrorDevices`. Keep in mind
  that a fragment placed in front of `RocketConfig` overrides the same key set further down.
- Never propose a protected block, not even as a note-only candidate.
- If a block can only be removed by changing Scala code (not a config fragment), put it in
  `notes` of a candidate with an empty `removal`, or mention it in your final reply.
- At most $MAX candidates, the most promising (largest area or clearest leftover) first.

## Output

Write the candidates **with `chipyard_bash`** to `$CANDIDATES` (outside the chipyard tree, so
writing it is allowed), for example with a heredoc, as a JSON list:

```json
[
  {
    "id": "scratchpad",
    "block": "ChipTop.system... (64 KiB mbus scratchpad)",
    "category": "memory",
    "comes_from": "new testchipip.soc.WithMbusScratchpad(...) in AbstractConfig.scala:120",
    "removal": ["new testchipip.soc.WithNoScratchpads"],
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
End the reply with the same JSON list again, in a ```json block.
