Raise the clock frequency of the control bus of a Rocket SoC (Chipyard `$CONFIG_DESC`, module
`PeripheryBus_cbus`, built from rocket-chip TileLink widgets) for an ASIC: Yosys + OpenSTA on the
sky130_fd_sc_hd standard-cell library. The Rocket tile and the L2 were already optimized and are fixed
for this run. The whole SoC was measured at a 10 ns clock (target 100 MHz): with the reset fanout set
aside, the only paths that fail are in the bus atomics, from the registers of `TLAtomicAutomata`
(`cbus.atomics.cam_a_0_*`, `cam_d_0_data`) through the AMO ALU into the queue of the `TLBuffer` behind it
(`cbus.buffer.nodeOut_a_q.ram_ext.Memory[*]_reg`), -2.2 ns; the same widget on the pbus fails by -1.4 ns.
The next worst path of the SoC (L2) has +0.45 ns. This is round $ITER of $MAX_ITERS. As in CHIA's
`timing_opt` example, you read the timing report, edit the Chisel and check your edits yourself with a
synthesis A/B tool, in one session of about $LLM_MIN minutes.

`TLAtomicAutomata` (`src/main/scala/tilelink/AtomicAutomata.scala`) turns an AMO on a device that cannot
do it into a Get and a Put: it keeps the request in a CAM (`cam_a`), waits for the Get's data
(`cam_d`), computes the AMO (logical / arithmetic, with the byte mask) and sends the Put. The AMO
result is computed in the same cycle as the Put leaves on channel A, and the TLBuffer queue behind it
captures it. It is used for every AMO to a cbus/pbus device (CLINT, PLIC, debug, UART, ...), which are
rare: **an extra cycle there costs no measurable IPC**. One edit in that class fixes both buses.

## Goal: iron-law performance

Maximize **frequency x IPC**. Here the obvious fix is to register the AMO result (or part of the ALU)
before the Put goes out, adding a state/cycle to the automaton; restructuring the ALU is also fine.
Do not move the problem: the path must end in a register inside a block, not be pushed into the
xbar or the next widget.

How the loop judges the round after you finish:

1. Build `$CONFIG_DESC` (Chisel -> Verilog -> Verilator). A compile error goes to a build-fix session.
2. Synthesize `PeripheryBus_cbus` with Yosys (flow $FLOW: `$FLOW_SYNTH`, sky130_fd_sc_hd, `abc -D` at a
   fixed clock period of $PERIOD_NS ns) and time it with OpenSTA. Its ports get zero input/output delay.
   Fmax = 1000 / (period - worst slack). The **family score** is the sum of the worst slacks
   of the $FAMILY_K worst endpoint families (`timing_families.txt`), each clipped at 0. A round that
   can pass neither test of step 5, even with no cycle cost, is rejected here.
3. Run riscv-tests `rv64ui/um/ua/uf/ud-p-*` (all must pass) and the benchmarks $BENCHES on the
   whole SoC (Verilator), plus `rv64ua-p-mmio-amo`: the loop's test of the cbus atomics, every
   amo*.d / amo*.w on the CLINT `mtimecmp` register, each checked for the returned old value and the
   new value. Every benchmark must pass with the same `minstret` as the baseline. A functional
   failure goes to a debug session (at most $DEBUG_RETRIES) with the failing test's log.
4. Cycle cost = geometric mean over the benchmarks of `mcycle / mcycle of the baseline`. It must stay
   at most **$MAX_IPC_LOSS above the baseline** (the current best uses $BEST_IPC_LOSS of it).
5. Accept if `Fmax / cycle cost` beats the current best by more than $MIN_PERF_GAIN, **or** if the
   family score rises by at least $MIN_FAMILY_GAIN ns while the worst slack drops by at most
   $WORST_TOL ns.

Current best ($BEST_NAME): worst slack $BEST_SLACK ns at $PERIOD_NS ns, Fmax $BEST_FMAX MHz, TNS
$BEST_TNS ns, family score $BEST_FAMILY ns, area $BEST_AREA um2; cycle cost $BEST_IPC_LOSS above the baseline, performance
$BEST_PERF (Fmax / cycle cost).

## What makes a path long in this flow

$FLOW_NOTES
- Gate count in series and fanout (ABC inserts buffers and sizes cells) set the delay; there are no
  wires in this estimate.
- Adders and comparators become Brent-Kung prefix trees; Yosys maps `*` to a plain array multiplier
  (no Booth, no Wallace tree), so a multiplier is deep. Wide one-hot or priority muxes,
  bypass/forwarding muxes and equality compares across many sources are deep too.
- Small queues that Chisel builds from flip-flops (the TLBuffer queue `nodeOut_a_q.ram_ext.Memory[i][j]_reg`)
  are logic and are timed: the data you enqueue goes through the write mux of every entry.

## Your tools

Both run on the chipyard worker:

- `$BASH_TOOL`: a shell in `/home/ray/chipyard` (read, `grep`, `sed -i`, `git diff`). All reading
  and editing goes through it. Each command is limited to 120 s.
- `synth_ab`: `$AB_REBUILD`, `$AB_LIST`, `$AB_START`, `$AB_RESULT` (see below). Its replies may name
  them `synth_ab_<method>`: those are the same tools.$OTHER_TOOLS

## Rules

- The bus must stay **functionally correct**: the TileLink protocol (every request answered once,
  beats in order, `ready`/`valid` rules: no combinational dependence of `valid` on `ready`), the AMO
  semantics (all logical and arithmetic AMOs, `.w` and `.d`, byte masks, signed/unsigned min/max,
  the returned old value) and the automaton's CAM/ordering rules (a second request to the same
  address waits). A broken widget often hangs the simulation instead of failing a check.
- If you add a pipeline stage, **propagate the extra latency to every consumer** of the signal:
  `ready`/`valid` handshakes, the automaton's state machine (`cam_s`), the CAM's free/busy flags,
  the D-channel response bookkeeping. Register a `valid` and its data together, and keep backpressure right
  (a stage needs a skid or must stall its producer).
- Do not shrink structures, do not remove features (e.g. drop arithmetic AMOs, or remove the atomics
  or a buffer from the bus) and do not change configs to shorten a path.
- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/tilelink/` or
  `.../src/main/scala/prci/`, with small in-place edits (`sed -i`, heredocs). No file with "Config" in its
  name, nothing else in rocket-chip (the tile is fixed), `generators/chipyard`, `sims/` or elsewhere:
  such edits reject the round. A widget in `tilelink/` is shared by every bus that uses it: keep it
  correct for all its parameters.
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
  with fanout. Names are instance paths: `atomics.cam_d_0_data[3]_reg` is register `cam_d_0_data` in
  instance `atomics` (`TLAtomicAutomata`, `AtomicAutomata.scala`); `buffer` = `TLBuffer` (`Buffer.scala`),
  `out_xbar` = `TLXbar`; `_reg` = flip-flop.
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
- `$AB_LIST(pattern, only_changed)`: modules under `PeripheryBus_cbus` with their size.
- `$AB_START(module)`: synthesizes `module` of your edit (child) and of $BEST_NAME (parent), returns
  a handle. A sub-block (`TLAtomicAutomata_cbus`, a `TLBuffer_*`, ...; see `$AB_LIST`) takes about a minute; its ports get zero input/output delay, so only paths inside it count, and
  paths that cross into another block (e.g. `atomics` -> `buffer`) are not seen. Pick the smallest
  module that holds both ends of the path you target. `$AB_START("PeripheryBus_cbus")`
  synthesizes the whole control bus and compares it with the full result of $BEST_NAME ($BEST_SLACK ns): that
  is the number the loop uses.
- `$AB_RESULT(handle)`: worst slack of child and parent, the delta (positive = shorter), the family
  score of both sides (whole bus) and a table of the worst endpoint families with their slack on
  both sides and the delta per family. Full reports are under `$STAGE_DIR/ab/<handle>/`.
- Verilog already synthesized (in this round, an earlier round or an earlier run) is not
  synthesized again: the reply starts with `ALREADY MEASURED` and gives that result. Take it as the
  answer to that idea and try something else.

The worst slack alone can hide your gain: when another family is the worst on both sides, it stays
the same while the family you shortened moved. Judge an edit by the row of the family you targeted
in the table, and check that no other family got worse.

Before you finish, run `$AB_START("PeripheryBus_cbus")` once on the final edit, and finish with it only if
the families you targeted got shorter there (state their rows of the table and the family score)
and the worst slack did not drop below $BEST_SLACK ns by more than $WORST_TOL ns.

You must try: finishing without an edit that you have measured wastes the round. A rejected edit
costs nothing, because the loop rejects it and keeps $BEST_NAME. You cannot measure cycle cost
yourself, but the loop measures it on the benchmarks and rejects the round if it is too high. So a
pipeline stage whose IPC cost you only estimate is still worth trying. Give up only after at least
two different edits, each checked with `$AB_START("PeripheryBus_cbus")`, did not shorten the targeted paths.
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
5. `$AB_REBUILD` -> `$AB_START(<sub-block>)` -> `$AB_RESULT`; iterate. Then `$AB_START("PeripheryBus_cbus")`.
6. Finish with a short summary: the path family you targeted, the files/lines you changed, the
   `synth_ab` numbers (sub-block and `PeripheryBus_cbus`, with the rows of the family table), whether you
   added latency (where, and which consumers you updated) and the IPC cost you expect on the
   benchmarks.

## Earlier rounds

$HISTORY
