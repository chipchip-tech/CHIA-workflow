Raise the clock frequency of the Rocket core (Chipyard `$CONFIG_DESC`, module `RocketTile`) for an
ASIC: Yosys + OpenSTA on the sky130_fd_sc_hd standard-cell library. This is round $ITER of
$MAX_ITERS. As in CHIA's `timing_opt` example, you read the timing report, edit the Chisel and check
your edits yourself with a synthesis A/B tool, in one session of about $LLM_MIN minutes.

## Goal: iron-law performance

Maximize **frequency x IPC** (equivalently, minimize `CPI x cycle_time`). Shortening the critical
path by restructuring logic is best, because it costs no IPC. **Adding a pipeline stage is allowed**
when it is the easiest way to cut a long path, as long as the IPC it costs is small.

How the loop judges the round after you finish:

1. Build `$CONFIG_DESC` (Chisel -> Verilog -> Verilator). A compile error goes to a build-fix session.
2. Synthesize `RocketTile` with Yosys (`synth -flatten`, sky130_fd_sc_hd, `abc -D` at a fixed clock
   period of $PERIOD_NS ns) and time it with OpenSTA. Fmax = 1000 / (period - worst slack). SRAM
   macros (`*_ext`) are black boxes and not timed. A round whose Fmax cannot beat the current best
   by more than $MIN_PERF_GAIN, even with no cycle cost, is rejected here.
3. Run riscv-tests `rv64ui/um/ua/uf/ud-p-*` (all must pass) and the benchmarks $BENCHES. Every
   benchmark must pass with the same `minstret` (instruction count) as the baseline. A functional
   failure goes to a debug session (at most $DEBUG_RETRIES) with the failing test's log.
4. Cycle cost = geometric mean over the benchmarks of `mcycle / mcycle of the baseline`. It must stay
   at most **$MAX_IPC_LOSS above the baseline** (cumulative, over all accepted rounds; the current
   best uses $BEST_IPC_LOSS of it).
5. Accept only if `Fmax / cycle cost` beats the current best by more than $MIN_PERF_GAIN.

Current best ($BEST_NAME): worst slack $BEST_SLACK ns at $PERIOD_NS ns, Fmax $BEST_FMAX MHz, TNS
$BEST_TNS ns, area $BEST_AREA um2; cycle cost $BEST_IPC_LOSS above the baseline, performance
$BEST_PERF (Fmax / cycle cost).

## What makes a path long in this flow

- The whole tile is flattened and mapped by one ABC run with a delay target. Gate count in series
  and fanout (ABC inserts buffers and sizes cells) set the delay; there are no wires in this
  estimate.
- Adders and comparators become Brent-Kung prefix trees; Yosys maps `*` to a plain array multiplier
  (no Booth, no Wallace tree), so a multiplier is deep. Wide one-hot or priority muxes,
  bypass/forwarding muxes and equality compares across many sources are deep too.
- Paths that start or end at an SRAM pin are not timed (black boxes), so the SRAM itself is never
  the critical path here; logic between registers is.
- Synthesizing the whole tile at once moves other paths by 1-2 ns when you edit one block: a gain
  of the targeted family can be hidden or exaggerated by the worst slack alone.

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
- Do NOT run `make`, `sbt`, `mill`, `verilator` or `yosys` through the shell: elaborate and
  synthesize only through `synth_ab`.
- Do not `git commit`, `git stash`, `git checkout` or `git restore`: the accepted changes of
  $BEST_NAME are in the working tree, not committed. To undo your own edit of a file, apply the
  reverse of its hunks (`git -C /home/ray/chipyard/generators/rocket-chip diff -- <file>` shows them,
  together with the accepted ones in `$STAGE_DIR/best.patch`).
- Chisel: `:=` for connections (`<>` only between Decoupled/Valid), keep existing imports, Scala `if`
  for elaboration-time choices and `when` for hardware, `RegInit` where a reset value is needed,
  `RegEnable`/`RegNext` for pipeline registers.

## Inputs (through the shell)

- `$STAGE_DIR/timing_paths.rpt`: OpenSTA report of the 10 worst paths of $BEST_NAME, cell by cell
  with fanout. The design is flattened: `fpuOpt.divSqrt_2.x_reg` is register `x` in instance
  `fpuOpt.divSqrt_2`; `_reg` = flip-flop (`core` = the Rocket pipeline, `fpuOpt` = the FPU,
  `frontend`, `dcache`, `ptw`, ...).
- `$STAGE_DIR/timing_endpoints.rpt`: the worst 2000 endpoints with their slack.
- `$STAGE_DIR/timing_families.txt`: the same, grouped by register (bit index removed).
- `$STAGE_DIR/synth_stat.txt`: Yosys cell counts and area.
- `$STAGE_DIR/best_verilog/`: Verilog of $BEST_NAME; `// @[...scala:line:col]` points to the Chisel.
- `$STAGE_DIR/best.patch`: the changes already accepted (applied in the tree).
- `$STAGE_DIR/history.md`: earlier rounds and why they were rejected.

Useful commands:

    grep -E '^(Startpoint|Endpoint)|slack' $STAGE_DIR/timing_paths.rpt | head -60
    sed -n '/^Startpoint/,/slack/p' $STAGE_DIR/timing_paths.rpt | head -150
    head -40 $STAGE_DIR/timing_families.txt

Several endpoint families sit close to the worst one: cluster them by structure before you choose.
One edit that removes a whole family is worth more than one that only moves the worst path, and
the Fmax only rises once every family above the new worst slack is shortened.

## The worst path, located for you

$TARGET

## `synth_ab`: check every edit before you finish

It elaborates your edited Chisel and synthesizes one module of it with the loop's Yosys + OpenSTA
flow and period, next to the same module of $BEST_NAME. Every call returns within about a minute;
when it answers `running`, call it again.

- `$AB_REBUILD()`: `make verilog` on your current edits (1-3 min, poll until `ok`). It lists the
  modules your edit changed. Compile errors come back here: fix them first.
- `$AB_LIST(pattern, only_changed)`: modules under `RocketTile` with their size.
- `$AB_START(module)`: synthesizes `module` of your edit (child) and of $BEST_NAME (parent), returns
  a handle. A sub-block (`FPU`, `FPUFMAPipe`, `DivSqrtRecFN_small`, `Rocket`, `MulDiv`, `Frontend`,
  ...) takes 1-2 min; its ports get zero input/output delay, so only paths inside it count, and
  paths that cross into another block (e.g. `dcache` -> `core`) are not seen. Pick the smallest
  module that holds both ends of the path you target. `$AB_START("RocketTile")` synthesizes the
  whole tile (3-4 min) and compares it with the full result of $BEST_NAME ($BEST_SLACK ns): that is
  the number the loop uses.
- `$AB_RESULT(handle)`: worst slack of child and parent, the delta (positive = shorter; |delta| <=
  0.1 ns is synthesis noise) and a table of the worst endpoint families with their slack on both
  sides and the delta per family. Full reports are under `$STAGE_DIR/ab/<handle>/`.

The worst slack alone can hide your gain: when another family is the worst on both sides, it stays
the same while the family you shortened moved. Judge an edit by the row of the family you targeted
in the table, and check that no other family got worse.

Before you finish, run `$AB_START("RocketTile")` once on the final edit, and finish with it only if
the families you targeted got shorter there (state their rows of the table) and no other family
fell below $BEST_SLACK ns.

You must try: finishing without an edit that you have measured wastes the round. A rejected edit
costs nothing, because the loop rejects it and keeps $BEST_NAME. You cannot measure cycle cost
yourself, but the loop measures it on the benchmarks and rejects the round if it is too high. So a
pipeline stage whose IPC cost you only estimate is still worth trying. Give up only after at least
two different edits, each checked with `$AB_START("RocketTile")`, did not shorten the targeted paths.
Then undo your edits and list what you tried, with the `synth_ab` numbers.

## Steps

1. Read the worst paths (`timing_paths.rpt`, `timing_families.txt`) and cluster them by the
   structure they go through.
2. Find that logic in the Verilog, then in the Chisel.
3. Plan: restructure first (precompute, duplicate, reorder or flatten mux trees, move logic across
   an existing register, take the late-arriving signal out of the deep part of the cone, cheaper
   adders/comparators). If the path cannot be cut that way, plan a pipeline stage and list every
   consumer that must follow the new latency.
4. Implement with small in-place edits; check them with `sed -n` and `git diff`.
5. `$AB_REBUILD` -> `$AB_START(<sub-block>)` -> `$AB_RESULT`; iterate. Then `$AB_START("RocketTile")`.
6. Finish with a short summary: the path family you targeted, the files/lines you changed, the
   `synth_ab` numbers (sub-block and `RocketTile`, with the rows of the family table), whether you
   added latency (where, and which consumers you updated) and the IPC cost you expect on the
   benchmarks.

## Earlier rounds

$HISTORY
