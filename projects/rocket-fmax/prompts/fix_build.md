Round $ITER of the Rocket timing loop: an agent edited the Chisel of rocket-chip to shorten the
critical path of `RocketTile` without changing its cycle-by-cycle behaviour. Building
`$CONFIG_DESC` with that edit **failed**. Your job (build fix $FIX of at most $MAX_FIX) is to fix
the compile errors so the edit builds, while keeping what the edit does.

## Rules

- Fix the cause of the errors below, in the files the edit touched. Keep the edit's idea and its
  logic structure: do not undo it, do not replace it by the original code, do not gate it behind
  `false`. A fix that reverts the edit wastes the round.
- The behaviour must stay cycle-identical to $BEST_NAME: no new pipeline stages, no new or removed
  stall/kill/replay cycles.
- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip/src/main/scala/`, with small
  in-place edits (`sed -i`, heredocs). No file with "Config" in its name, no parameters, nothing
  outside rocket-chip.
- Do NOT run `make`, `sbt`, `verilator` or `yosys` through `chipyard_bash` (each command is limited
  to 120 s). Do not `git commit`, `git stash` or `git checkout` the edited files.
- Keep Chisel legal: `:=` for connections, keep existing imports, Scala `if` for elaboration-time
  choices and `when` for hardware, explicit widths where Chisel cannot infer them. Scala does not
  allow a default parameter value to refer to another parameter of the same parameter list.

## Tool

`chipyard_bash_chipyard_bash_run_command`: a shell in `/home/ray/chipyard`. Use only this tool:
opencode's own `bash`, `read`, `edit`, ... (if you see them) run in another container that has no
Chipyard.$AB_NOTE

## What the edit was meant to do (the editing agent's summary)

$NOTES

## Build errors (sbt/firtool, ANSI colours removed)

```
$ERRORS
```

## Current diff of rocket-chip

This diff includes the changes already accepted in earlier rounds (`$STAGE_DIR/best.patch`). Those
build fine: only the hunks that are not in `best.patch` belong to this round's edit.

```diff
$PATCH
```

## Steps

1. Read the first error and the lines it points to (`sed -n` on the file), then the related
   hunks of the diff.
2. State the root cause in one sentence, then fix it. Fix every error you can see, not only the
   first.
3. Check the edited lines with `sed -n` and `git -C /home/ray/chipyard/generators/rocket-chip diff`.
4. Finish with: root cause, the files and lines you changed, and why the edit's behaviour is
   unchanged.
