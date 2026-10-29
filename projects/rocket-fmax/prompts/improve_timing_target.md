Shorten the critical path of the Rocket core (Chipyard `$CONFIG_DESC`, module `RocketTile`)
**without changing its cycle-by-cycle behaviour**. This is round $ITER of $MAX_ITERS,
edit session $SESSION of at most $MAX_SESSIONS in this round.

This session is short (about $EDIT_MIN minutes). Do one thing: **edit the Chisel for the target
below**, check the edit by reading it, and finish. Do not explore the rest of the core.

**Budget: read for at most about 15 tool calls, then edit.** The Chisel at both ends of the path is
quoted below, so little reading is needed. The loop counts your commands while you have not edited:
after $BUDGET_SOFT each answer carries a budget note, after $BUDGET_READONLY a command that changes no
file gets no output, and after $BUDGET_HARD nothing runs and the session ends. A session with no edit
wastes one of the round's $MAX_SESSIONS sessions. An imperfect edit that compiles is fine: the loop
measures it and, if it is not better, gives you the A/B report in the next session to improve it. Do
not use tool calls as a notepad (a command with only `pass`, comments or an empty heredoc is not run);
think in your reply instead.

## Target (chosen by the loop)

$TARGET

## How your edit is judged

When you finish, the loop (not you) will:

1. Build `$CONFIG_DESC` (Chisel -> Verilog -> Verilator). If the build fails, a separate
   session fixes the compile errors.
2. A/B: synthesize the smallest module that holds the target and everything your edit changed,
   once from your edit and once from the current best ($BEST_NAME), with Yosys + OpenSTA
   (sky130_fd_sc_hd, fixed clock period $PERIOD_NS ns). What counts is the worst slack of the
   endpoints inside the instance named in the target section above (paths elsewhere in that module are reported too). If it is
   not better by more than $AB_MIN_GAIN ns, you get the A/B report in a new session to improve the edit (at most
   $MAX_SESSIONS sessions in this round); after the last one the round is rejected.
3. If the A/B is better: riscv-tests `rv64ui/um/ua/uf/ud-p-*` (all must pass) and the
   benchmarks dhrystone, median, multiply, qsort, towers, vvadd, each with **exactly** the
   baseline `mcycle`, then synthesis of the whole `RocketTile`. The round is accepted only if the
   worst slack beats $BEST_NAME (worst slack $BEST_SLACK ns, ~$BEST_FMAX MHz).

So the change must be **IPC-neutral**: restructure logic (precompute, duplicate, reorder mux
trees, move logic off the critical path, replace an adder or comparator with cheaper equivalent
logic, e.g. a hand-written parallel-prefix adder or a carry-save stage instead of `+`), but do
not add pipeline stages, do not add or remove stall/kill/replay cycles, and do not change what
any instruction does or when it retires. Moving logic across an existing register is fine only
if every cycle's outputs stay the same.

## Your tool

Only `$BASH_TOOL`: a shell in `/home/ray/chipyard`. Do all reading
and editing through it.$OTHER_TOOLS

## Rules

- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/`. Make small,
  targeted in-place edits (`sed -i`, heredocs). Do not rewrite whole files.
- Do NOT edit configs or parameters (no file with "Config" in its name, no sizes, no `*Params`
  defaults), the test harness, `generators/chipyard`, `sims/`, or anything outside rocket-chip.
- Do NOT run `make`, `sbt`, `mill`, `verilator`, `yosys` or any build or synthesis: the loop
  does that right after you finish. Each bash command is limited to 120 s.
- Do not `git commit`, `git stash`, `git checkout` or `git restore`: the accepted changes of
  $BEST_NAME are in the working tree, not committed, and those commands would drop them. To undo
  your own edits of this round use `git -C /home/ray/chipyard/generators/rocket-chip apply -R
  <file>` with the round diff named below.
- Keep Chisel legal: `:=` for connections, keep the existing imports, Scala `if` for
  elaboration-time choices and `when` for hardware, widths that match.

## Inputs (through `chipyard_bash`)

- `$STAGE_DIR/timing_paths.rpt`: OpenSTA paths of the 10 worst endpoints of $BEST_NAME
  (flattened names: `fpuOpt.dfma.x_reg` = register `x` in instance `fpuOpt.dfma`).
- `$STAGE_DIR/timing_families.txt`: worst endpoints grouped by register.
- `$STAGE_DIR/best_verilog/`: Verilog of $BEST_NAME; `// @[...scala:line:col]` comments point
  back to the Chisel source. Find a module with `grep -l '^module <Name>' $STAGE_DIR/best_verilog/*`.
- `$STAGE_DIR/history.md`: earlier rounds of this run.

Useful commands:

    sed -n '/^Startpoint/,/slack/p' $STAGE_DIR/timing_paths.rpt | head -120
    grep -n '@\[' $STAGE_DIR/best_verilog/<file> | grep -i <signal> | head

## Current best

Worst path: $CRIT

```
$ENDPOINTS
```

## Earlier rounds

$HISTORY

$SESSION_NOTE

## What to do

1. Start from the quoted Chisel and the worst path report; read more only where they are not enough.
2. Make one focused, behaviour-preserving restructuring that shortens that path.
3. Re-read your edit with `sed -n` (syntax, imports, widths) and check the round's diff.
4. Finish with a short summary: what you changed (file, lines), which path it should shorten,
   and why every cycle's behaviour is unchanged.
