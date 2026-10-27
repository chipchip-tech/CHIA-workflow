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

## Your tools

You have exactly two tool groups, both running on the chipyard worker:

- `chipyard_bash_chipyard_bash_run_command`: a shell in `/home/ray/chipyard` (read, `grep`,
  `sed -i`, `git diff`). All reading and editing goes through it.
- `synth_ab_rebuild_verilog`, `synth_ab_list_modules`, `synth_ab_start_ab`,
  `synth_ab_ab_result`: elaborate and synthesize (see below).

There is no other shell or file editor: opencode's own bash/read/edit tools are switched off
because they run on a machine without Chipyard.

## Rules

- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/`
  (`rocket/`, `tile/`, `util/`, ...). Make small, targeted in-place edits (`sed -i`, heredocs).
  Do not rewrite whole files.
- Do NOT edit configs or parameters: no file with "Config" in its name, no cache sizes, no
  FPU/MulDiv/BTB/branch-predictor settings, no default values of `*Params`. Do NOT touch the
  test harness, `generators/chipyard`, `sims/`, or anything outside rocket-chip. Such edits
  are detected and the round is rejected.
- Do NOT run `make`, `sbt`, `verilator` or `yosys` through `chipyard_bash` (each bash command
  is limited to 120 s anyway). Elaborate and synthesize only through the `synth_ab` tool below.
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

## The `synth_ab` tool: check every edit before you finish

You have a second tool, `synth_ab`, that elaborates your edited Chisel and synthesizes one
module of it with the same Yosys + OpenSTA flow and period as the loop, next to the same module
of $BEST_NAME. Use it: earlier rounds without it often handed in edits that made the path
0.8-1.4 ns *longer*. Every call returns within about a minute; when it answers `running`, call
it again.

- `synth_ab_rebuild_verilog()`: runs `make verilog` on your current edits (1-3 min, poll until `ok`).
  It lists the modules your edit changed. Compile errors come back here: fix them first.
- `synth_ab_list_modules(pattern, only_changed)`: modules under `RocketTile` with their size.
- `synth_ab_start_ab(module)`: synthesizes `module` of your edit (child) and of $BEST_NAME (parent),
  returns a handle. A sub-block such as `Rocket`, `FPU`, `FPUFMAPipe`, `MulDiv`, `DCache`,
  `Frontend` takes 1-2 min; its ports get zero input/output delay, so only paths inside it
  count. Pick the smallest module that holds both ends of the path you target.
  `synth_ab_start_ab("RocketTile")` synthesizes the whole tile (3-4 min) and compares it with the full
  result of $BEST_NAME (worst slack $BEST_SLACK ns): that is exactly what the loop judges.
- `synth_ab_ab_result(handle)`: worst slack of child and parent, the delta (positive = your edit
  shortened the worst path; |delta| <= 0.1 ns is synthesis noise) and the child's worst
  endpoint families. Full reports are under `$STAGE_DIR/ab/<handle>/` for `chipyard_bash`.

Workflow for each idea: edit (through `chipyard_bash`) -> `synth_ab_rebuild_verilog` -> `synth_ab_start_ab(<sub-block>)` -> `synth_ab_ab_result`.
If the sub-block got worse or did not move, revert or change the edit
(`git -C /home/ray/chipyard/generators/rocket-chip checkout -- <file>` restores a file).
When the sub-block is clearly better, run `synth_ab_start_ab("RocketTile")` once on the final edit.
Only finish with an edit whose `RocketTile` result beats $BEST_SLACK ns. Several path
families sit within ~0.2 ns of each other, so fixing one may expose the next: look at the
child's endpoint families and, if time allows, shorten the next family too. If nothing you
tried helps, revert everything and say so: an empty round costs less than a slower one.

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
5. Measure it with `synth_ab` as described above; iterate until the `RocketTile` A/B is
   better than $BEST_SLACK ns, or revert.
6. Finish with a short summary: the path you targeted, the file and lines you changed, the
   `synth_ab` results (sub-block and `RocketTile`), and why the behaviour is cycle-identical.
