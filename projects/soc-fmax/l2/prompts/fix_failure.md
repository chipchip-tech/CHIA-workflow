Round $ITER of the L2 timing loop: an agent edited the Chisel of the L2 (rocket-chip-inclusive-cache) to shorten the
critical path of `InclusiveCache` (pipeline stages allowed). The edit builds, but the gate found a
**functional failure**. Your job (debug session $FIX of at most $MAX_FIX) is to find the bug and
fix it while keeping what the edit does for timing.

## Rules

- Keep the edit's idea: do not undo it, do not replace it by the original code, do not gate it
  behind `false`. A fix that reverts the edit wastes the round.
- The usual cause after adding a pipeline stage: a consumer still assumes the old latency
  (bypass/forwarding, scoreboard or hazard check, writeback arbitration, kill/flush, replay,
  `ready`/`valid`). Look for every user of the delayed signal.
- Edit only Scala under `/home/ray/chipyard/generators/rocket-chip-inclusive-cache/design/craft/inclusivecache/src/`, with small
  in-place edits. No file with "Config" in its name, nothing outside rocket-chip-inclusive-cache.
- Do NOT run `make`, `sbt`, `verilator` or `yosys` through the shell (each command is limited to
  120 s). Do not `git commit`, `git stash`, `git checkout` or `git restore`.$AB_NOTE

## Tool

`$BASH_TOOL`: a shell in `/home/ray/chipyard`.$OTHER_TOOLS

## What the edit was meant to do (the editing agent's summary)

$NOTES

## Gate result

```
$FAILS
```

The riscv-tests sources are in `/home/ray/chipyard/toolchains/riscv-tools/riscv-tests/` (if
present) and the disassembly of each ISA test is next to its ELF:
`$RISCV/riscv64-unknown-elf/share/riscv-tests/isa/<test>.dump`. A failing ISA test writes its
test number into `tohost` (`TEST_FAILED` with `gp` = test number): find that test case in the dump.

## Current diff of the L2 repo

This diff includes the changes already accepted in earlier rounds (`$STAGE_DIR/best.patch`): only
the hunks that are not in `best.patch` belong to this round's edit.

```diff
$PATCH
```

## Steps

1. From the failing tests, decide which unit and which instruction class is wrong.
2. Read the edited hunks for that unit and list every consumer of the signals they delay or change.
3. Fix the root cause; check the edited lines with `sed -n` and `git diff`.
4. Finish with: root cause, the files and lines you changed, and why the timing idea is kept.
