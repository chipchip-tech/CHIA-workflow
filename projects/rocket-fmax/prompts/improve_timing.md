Raise the clock frequency of the Rocket core (Chipyard `$CONFIG_DESC`, module `RocketTile`) on an
FPGA: AMD Artix-7 `$PART` (Arty A7-100T board, speed grade -1), measured **after place and route**
with Vivado. This is round $ITER of $MAX_ITERS. As in CHIA's `timing_opt` example, you read the timing
report, edit the Chisel and check your edits yourself with a synthesis A/B tool, in one session of
about $LLM_MIN minutes.

## Goal: iron-law performance

Maximize **frequency x IPC** (equivalently, minimize `CPI x cycle_time`). Shortening the critical
path by restructuring logic is best, because it costs no IPC. **Adding a pipeline stage is allowed**
when it is the easiest way to cut a long path, as long as the IPC it costs is small.

How the loop judges the round after you finish:

1. Build `$CONFIG_DESC` (Chisel -> Verilog -> Verilator). A compile error goes to a build-fix session.
2. In parallel:
   - Vivado on `RocketTile` alone (out of context, clock period $PERIOD_NS ns, I/O with zero delay,
     SRAMs mapped to block RAM / LUT RAM): `synth_design`, then place + route with each of the
     strategies $STRATEGIES; the best post-route worst slack counts. Fmax = 1000 / (period - worst
     slack). Different strategies on the same netlist differ by up to ~8 %, so only clear gains count.
   - riscv-tests `rv64ui/um/ua/uf/ud-p-*` (all must pass) and the benchmarks $BENCHES. Every
     benchmark must pass with the same `minstret` (instruction count) as the baseline. A functional
     failure goes to a debug session (at most $DEBUG_RETRIES) with the failing test's log.
3. Cycle cost = geometric mean over the benchmarks of `mcycle / mcycle of the baseline`. It must stay
   at most **$MAX_IPC_LOSS above the baseline** (cumulative, over all accepted rounds; the current
   best uses $BEST_IPC_LOSS of it).
4. Accept only if `post-route Fmax / cycle cost` beats the current best by more than $MIN_PERF_GAIN.

Current best ($BEST_NAME): post-route worst slack $BEST_SLACK ns at $PERIOD_NS ns, Fmax $BEST_FMAX MHz
(strategy `$BEST_STRATEGY`); its `synth_design` alone gives $BEST_SYNTH_SLACK ns ($BEST_SYNTH_FMAX MHz).
Cycle cost $BEST_IPC_LOSS above the baseline, performance $BEST_PERF (Fmax / cycle cost). The target
of this project is Fmax > 100 MHz (worst slack >= 0 ns at 10 ns).

## What makes a path long on this FPGA

- Logic is built from 6-input LUTs. What costs is the number of **logic levels** (LUTs in series),
  and the wire after each of them: post route, wires are often 60-70 % of a path, about 0.5-0.8 ns
  per level on a -1 part. A 10 ns cycle holds about 10-12 levels plus a block RAM or a DSP.
- Adders, subtractors and comparators map to CARRY4 chains (fast per bit); wide one-hot or priority
  muxes, bypass/forwarding muxes and equality compares across many sources cost levels.
- High fanout costs wire: a select or valid signal that drives 64 data bits of several muxes is slow.
  Vivado already replicates drivers (`_replica` in the names); fewer levels before the high-fanout
  signal helps more.
- Block RAM output arrives late (about 2 ns clock-to-out without its output register); logic right
  after a RAM read is on the critical path more easily than on an ASIC. Multipliers map to DSP48
  blocks (25x18).
- `synth_design` estimates wires before placement: its slack is often 1-2 ns worse or better than
  post-route, and the worst path can change after routing. Use `synth_ab` to compare an edit with
  the best, not as the final number.

## Your tools

Both run on the chipyard worker:

- `$BASH_TOOL`: a shell in `/home/ray/chipyard` (read, `grep`, `sed -i`, `git diff`). All reading
  and editing goes through it. Each command is limited to 120 s.
- `synth_ab`: `$AB_REBUILD`, `$AB_LIST`, `$AB_START`, `$AB_RESULT` (see below). Its replies may name
  them `synth_ab_<method>`: those are the same tools.$OTHER_TOOLS

## Rules

- The processor must stay **functionally correct**: ISA-visible behaviour, memory ordering,
  exceptions, interrupts, CSRs and architectural register state are all preserved.
- If you add a pipeline stage, **propagate the extra latency to every consumer** of the signal:
  bypass/forwarding, scoreboard and hazard checks, writeback arbitration, kill/flush and replay,
  `ready`/`valid` handshakes. A missed consumer usually passes most tests and fails a few.
- Do not shrink structures (entries, widths, ways, ports) and do not remove features to shorten a
  path. Latency parameters of a unit (e.g. the FPU's `*Latency` values) may be changed if every
  consumer follows them.
- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/`, with small
  in-place edits (`sed -i`, heredocs). No file with "Config" in its name, nothing in
  `generators/chipyard`, `sims/` or outside rocket-chip: such edits reject the round.
- Do NOT run `make`, `sbt`, `mill`, `verilator`, `yosys` or `vivado` through the shell: elaborate
  and synthesize only through `synth_ab`.
- Do not `git commit`, `git stash`, `git checkout` or `git restore`: the accepted changes of
  $BEST_NAME are in the working tree, not committed. To undo your own edit of a file, apply the
  reverse of its hunks (`git -C /home/ray/chipyard/generators/rocket-chip diff -- <file>` shows them,
  together with the accepted ones in `$STAGE_DIR/best.patch`).
- Chisel: `:=` for connections (`<>` only between Decoupled/Valid), keep existing imports, Scala `if`
  for elaboration-time choices and `when` for hardware, `RegInit` where a reset value is needed,
  `RegEnable`/`RegNext` for pipeline registers.

## Inputs (through the shell)

- `$STAGE_DIR/route_paths.rpt`: Vivado `report_timing` of the 10 worst post-route paths of
  $BEST_NAME, cell by cell (`-input_pins`). Names are hierarchical with `/`:
  `dcache/s2_data_reg[13]/C` is bit 13 of register `s2_data` in instance `dcache` of `RocketTile`
  (`core` = the Rocket pipeline, `fpuOpt` = the FPU, `frontend`, `ptw`, ...). Each path lists its
  `Logic Levels` (e.g. `14 (CARRY4=3 LUT6=8 ...)`) and the logic/route split of its delay.
- `$STAGE_DIR/route_families.txt`: the worst 500 post-route endpoints grouped by register (bit index
  removed); `route_endpoints.txt` the endpoints themselves.
- `$STAGE_DIR/synth_paths.rpt`, `synth_families.txt`: the same after `synth_design` only (what
  `synth_ab` measures).
- `$STAGE_DIR/route_utilization.rpt`: LUTs, registers, block RAM, DSPs.
- `$STAGE_DIR/best_verilog/`: Verilog of $BEST_NAME; `// @[...scala:line:col]` points to the Chisel.
- `$STAGE_DIR/best.patch`: the changes already accepted (applied in the tree).
- `$STAGE_DIR/history.md`: earlier rounds and why they were rejected.

Useful commands:

    grep -E '^(Slack|  Source|  Destination|  Data Path Delay|  Logic Levels)' $STAGE_DIR/route_paths.rpt
    sed -n '/^Slack/,/^Slack/p' $STAGE_DIR/route_paths.rpt | head -200
    head -20 $STAGE_DIR/route_families.txt

Several endpoint families sit close to the worst one: cluster them by structure before you choose.
One edit that removes a whole family is worth more than one that only moves the worst path.

## The worst post-route path, located for you

$TARGET

## `synth_ab`: check every edit before you finish

It elaborates your edited Chisel and runs Vivado `synth_design` (same part and period) on one module
of it, next to the same module of $BEST_NAME. Every call returns within about a minute; when it
answers `running`, call it again.

- `$AB_REBUILD()`: `make verilog` on your current edits (1-3 min, poll until `ok`). It lists the
  modules your edit changed. Compile errors come back here: fix them first.
- `$AB_LIST(pattern, only_changed)`: modules under `RocketTile` with their size.
- `$AB_START(module)`: synthesizes `module` of your edit (child) and of $BEST_NAME (parent), returns
  a handle. A sub-block (`Rocket`, `FPU`, `DCache`, `MulDiv`, `Frontend`, ...) takes 1-2 min; its
  ports get zero input/output delay, so only paths inside it count, and paths that cross into
  another block (e.g. `dcache` -> `core`) are not seen. `$AB_START("RocketTile")` synthesizes the
  whole tile (2-3 min) and compares it with the `synth_design` of $BEST_NAME ($BEST_SYNTH_SLACK ns).
- `$AB_RESULT(handle)`: worst slack of child and parent, the delta (positive = shorter; |delta| <=
  0.1 ns is noise) and the child's worst endpoint families. Full reports (Vivado `report_timing`)
  are under `$STAGE_DIR/ab/<handle>/`.

Before you finish, run `$AB_START("RocketTile")` once on the final edit, and finish with it only if
the paths you targeted got shorter there (state the numbers). The loop then places and routes it.

You must try: finishing without an edit that you have measured wastes the round. A rejected edit
costs nothing, because the loop rejects it and keeps $BEST_NAME. You cannot measure cycle cost
yourself, but the loop measures it on the benchmarks and rejects the round if it is too high. So a
pipeline stage whose IPC cost you only estimate is still worth trying. Give up only after at least
two different edits, each checked with `$AB_START("RocketTile")`, did not shorten the targeted paths.
Then undo your edits and list what you tried, with the `synth_ab` numbers.

## Steps

1. Read the worst post-route paths (`route_paths.rpt`, `route_families.txt`) and cluster them by
   the structure they go through; note their logic levels and what drives the high-fanout nets.
2. Find that logic in the Verilog, then in the Chisel.
3. Plan: restructure first (fewer levels: precompute, duplicate, reorder or flatten mux trees, move
   logic across an existing register, take the late-arriving signal out of the deep part of the
   cone). If the path cannot be cut that way, plan a pipeline stage and list every consumer that
   must follow the new latency.
4. Implement with small in-place edits; check them with `sed -n` and `git diff`.
5. `$AB_REBUILD` -> `$AB_START(<sub-block>)` -> `$AB_RESULT`; iterate. Then `$AB_START("RocketTile")`.
6. Finish with a short summary: the path family you targeted, the files/lines you changed, the
   `synth_ab` numbers (sub-block and `RocketTile`), whether you added latency (where, and which
   consumers you updated) and the IPC cost you expect on the benchmarks.

## Earlier rounds

$HISTORY
