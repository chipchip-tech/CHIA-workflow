Shorten the critical path of the Rocket core (Chipyard `$CONFIG_DESC`, module `RocketTile`)
**without changing its cycle-by-cycle behaviour**. This is round $ITER of $MAX_ITERS.

## How your change is judged

After you finish, the loop (not you) will:

1. Build `$CONFIG_DESC` (Chisel -> Verilog -> Verilator).
2. Run riscv-tests `rv64ui/um/ua/uf/ud-p-*` (all must pass) and the benchmarks dhrystone,
   median, multiply, qsort, towers, vvadd. Each benchmark must report **exactly** the same
   `mcycle` as the baseline. One cycle of difference rejects the change.
3. Synthesize `RocketTile` with Yosys (sky130_fd_sc_hd, tt_025C_1v80, `abc -D`) and run OpenSTA
   at a fixed clock period of $PERIOD_NS ns. SRAM macros (`*_ext`) are black boxes. Their pins
   are not timed, so paths into or out of the cache data/tag arrays are not counted.
4. Accept the change only if the worst slack is better than the current best ($BEST_NAME:
   worst slack $BEST_SLACK ns, ~$BEST_FMAX MHz). Otherwise every edit is reverted and the next
   round starts again from $BEST_NAME.

So the change must be **IPC-neutral**: restructure logic (precompute, duplicate, reorder
mux trees, move logic off the critical path, replace an adder or comparator with cheaper
equivalent logic), but do not add pipeline stages, do not add or remove stall/kill/replay
cycles, and do not change what any instruction does or when it retires.

## Rules

- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/`
  (`rocket/`, `tile/`, `util/`, ...). Make small, targeted in-place edits (`sed -i`, heredocs).
  Do not rewrite whole files.
- Do NOT edit configs or parameters: no file with "Config" in its name, no cache sizes, no
  FPU/MulDiv/BTB/branch-predictor settings, no default values of `*Params`. Do NOT touch the
  test harness, `generators/chipyard`, `sims/`, or anything outside rocket-chip. Such edits
  are detected and the round is rejected.
- Do NOT run `make`, `sbt`, `verilator`, `yosys` or any build or synthesis: the loop does that,
  and builds started by you only waste your time budget. Each bash command is limited to
  120 s.
- Do not `git commit` or `git stash`. The loop diffs the working tree itself.
- Keep Chisel legal: `:=` for connections (`<>` only between Decoupled/Valid), keep the
  existing imports, use Scala `if` for elaboration-time choices and `when` for hardware.

## Inputs (on the chipyard worker, through `chipyard_bash`)

- `$STAGE_DIR/timing_paths.rpt`: OpenSTA `report_checks` for the 10 worst endpoints of
  $BEST_NAME, with nets and fanout. The design is flattened, so a name like
  `core.ex_reg_rs_bypass_0_reg` means register `ex_reg_rs_bypass_0` in instance `core`
  (`RocketTile.core` = `Rocket` in `rocket/RocketCore.scala`). `_reg` marks a flip-flop.
- `$STAGE_DIR/timing_endpoints.rpt`: the worst 2000 endpoints, one line each with its slack.
- `$STAGE_DIR/timing_families.txt`: the same endpoints grouped by register (bit index removed).
- `$STAGE_DIR/best_verilog/`: Verilog of the `RocketTile` hierarchy of $BEST_NAME. Its
  `// @[...scala:line:col]` comments point back to the Chisel source.
- `$STAGE_DIR/best.patch`: the changes already accepted (empty for the baseline).
  They are already applied in the source tree.
- `$STAGE_DIR/history.md`: what earlier rounds tried and why they were rejected.

Useful commands:

    grep -E '^(Startpoint|Endpoint)|slack' $STAGE_DIR/timing_paths.rpt | head -60
    sed -n '/^Startpoint/,/slack/p' $STAGE_DIR/timing_paths.rpt | head -150
    grep -rn 'ex_reg_rs_bypass' /home/ray/chipyard/generators/rocket-chip/src/main/scala/rocket/ | head
    grep -n '@\[' $STAGE_DIR/best_verilog/Rocket.sv | grep -i bypass | head

## Current best

Worst path: $CRIT

Worst endpoint families (register, number of failing bits):

```
$ENDPOINTS
```

## Earlier rounds

$HISTORY

## What to do

1. Read the worst paths and group them by the structure they go through.
2. Find that logic in the Verilog, then in the Chisel source.
3. Pick one structure and restructure it so that the result is the same in every cycle but
   the logic depth on the worst path is shorter.
4. Check your edit with `sed -n` (syntax, imports, widths) and `git -C
   /home/ray/chipyard/generators/rocket-chip diff`.
5. Finish with a short summary: the path you targeted, the file and lines you changed, and
   why the behaviour is cycle-identical.
