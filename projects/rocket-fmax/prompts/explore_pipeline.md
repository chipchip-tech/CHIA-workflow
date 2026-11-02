Explore pipeline changes of the Rocket core (Chipyard `$CONFIG_DESC`, module `RocketTile`) for a
later ASIC timing phase. This is round $ITER of $MAX_ITERS of the **simulation phase**: one session
of about $LLM_MIN minutes. You make **one** pipeline change that should cut one of the worst timing
path families. The loop measures only correctness and cycle cost here, with no synthesis. Every
change that passes becomes a **candidate**. The ASIC phase later synthesizes the candidates and keeps
the ones that pay for their cycles.

## Why pipeline changes

The current start point ($BEST_NAME) reaches $BEST_FMAX MHz at $PERIOD_NS ns (worst slack
$BEST_SLACK ns, Yosys + OpenSTA, sky130_fd_sc_hd, flow $FLOW). Several endpoint families sit close
to the worst one (`$STAGE_DIR/timing_families.txt`). Earlier rounds that only restructured logic
(prefix adders, carry-save trees, Booth) could not shorten them all, because synthesis already maps
`+` and `*` well. Reaching the target needs more cycles of slack on the long paths: an extra
pipeline register, a longer unit latency, or a shorter step per cycle in an iterative unit.

## How the loop judges the round after you finish

1. Build `$CONFIG_DESC` (Chisel -> Verilog -> Verilator). A compile error goes to a build-fix session.
2. Run riscv-tests `rv64ui/um/ua/uf/ud-p-*` (all must pass) and the benchmarks $BENCHES. Every
   benchmark must pass with the same `minstret` (instruction count) as the baseline. A functional
   failure goes to a debug session (at most $DEBUG_RETRIES) with the failing test's log.
3. Cycle cost = geometric mean over the benchmarks of `mcycle / mcycle of the baseline`. It must stay
   at most **$MAX_IPC_LOSS above the baseline** (the start point already uses $BEST_IPC_LOSS of it).
4. If both pass, your change is saved as a candidate with its cycle cost. Every round starts again
   from $BEST_NAME, so each candidate is one idea on its own.

## Your tools

- `$BASH_TOOL`: a shell in `/home/ray/chipyard` (read, `grep`, `sed -i`, `git diff`). All reading
  and editing goes through it. Each command is limited to 120 s.
- `$AB_REBUILD()`: `make verilog` on your current edits (1-3 min; call again while it says `running`).
  It returns compile errors and the modules your edit changed. Run it before you finish, and finish
  only with an edit that elaborates. `$AB_LIST(pattern, only_changed)` lists modules. There is no
  synthesis in this phase: `start_ab` returns an error.$OTHER_TOOLS

## Rules

- The processor must stay **functionally correct**: ISA-visible behaviour, memory ordering,
  exceptions, interrupts, CSRs and architectural register state are all preserved.
- **Propagate the extra latency to every consumer** of the delayed signal: bypass/forwarding,
  scoreboard and hazard checks (e.g. the FPU/long-latency scoreboards in `RocketCore.scala`),
  writeback arbitration and port conflicts, kill/flush and replay, `ready`/`valid` handshakes,
  "in flight" counters. A missed consumer usually passes most tests and fails a few.
- Latency parameters of a unit (e.g. the FPU's `*Latency` values in `FPUParams`) may be changed if
  every consumer follows them. Do not shrink structures (entries, widths, ways, ports) and do not
  remove features.
- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/`, with small
  in-place edits. No file with "Config" in its name, nothing in `generators/chipyard`, `sims/` or
  outside rocket-chip: such edits reject the round.
- Do NOT run `make`, `sbt`, `mill`, `verilator` or `yosys` through the shell.
- Do not `git commit`, `git stash`, `git checkout` or `git restore`: the start point's changes are in
  the working tree, not committed (`$STAGE_DIR/best.patch`).
- Choose a family or block that no earlier candidate covers (see "Earlier rounds"), so the
  candidates cover different families.

## Inputs (through the shell)

- `$STAGE_DIR/timing_families.txt`: the worst endpoint families of $BEST_NAME (bit index removed).
- `$STAGE_DIR/timing_paths.rpt`: the 10 worst paths, cell by cell. Names are instance paths:
  `fpuOpt.dfma.fma.x_reg` is register `x` in instance `fpuOpt.dfma.fma`.
- `$STAGE_DIR/best_verilog/`: Verilog of $BEST_NAME; `// @[...scala:line:col]` points to the Chisel.
- `$STAGE_DIR/best.patch`, `$STAGE_DIR/history.md`.

## The worst path, located for you

$TARGET

## Steps

1. Read `timing_families.txt` and pick one family (or a group that shares one structure) that no
   earlier candidate covers.
2. Find where the path starts and ends in the Chisel, and choose where an extra register cuts it.
3. List every consumer that must follow the new latency, then implement with small edits.
4. `$AB_REBUILD()` until it says `ok`. Fix compile errors.
5. Finish with a short summary. Start it with `TARGET: <family>`, then list the files and lines you
   changed, the latency you added (where, and which consumers you updated), and the cycle cost you
   expect on the benchmarks.

## Earlier rounds

$HISTORY
