# Round {{ROUND}} of batch `{{RUN}}` — ASIC {{PDK}}, target clock period {{PERIOD}} ns

Current best design (all previously accepted changes are already in the fork):
- STA WNS {{BEST_WNS}} ns (Fmax ≈ {{BEST_FMAX}} MHz), TNS {{BEST_TNS}} ns, family score {{BEST_FAM}} ns,
  cell area {{AREA}} µm² (without SRAM).
- baseline benchmark mcycle geomean {{GEOMEAN}} (budget ≤ {{BUDGET}}).
- paths through cells matching `{{EXCLUDE}}` are excluded from timing in this batch (ignore them).

A change is accepted if the build passes and EITHER the WNS improves by more than {{ACCEPT}} ns, OR the
**family score** rises by at least {{FAM_GAIN}} ns while the WNS gets worse by at most {{WORST_TOL}} ns.
Otherwise it is reverted. {{GATE_NOTE}}

## Think in families of paths, not in the single worst path

A *family* is one register with its bit index removed (`core.ex_reg_rs_msb_1[*]`). The family score is the
sum of the worst slacks of the 8 worst families, each clipped at 0. The WNS only moves once **every** family
above the new worst slack is shortened, so:
- several families usually go through the same structure (the same decode/bypass mux, the same queue
  write mux, the same adder). Cluster them by the logic they share before choosing.
- one edit that removes a whole cluster of families is worth more than one that only moves the worst path.
- do not move the problem: the path must end in a register, not be pushed into the next block.

## Worst endpoint families of the current best design

```
{{FAMILIES}}
```

## Worst path of each family (start / end)

| slack (ns) | arrival (ns) | start | end |
|---|---|---|---|
{{PATHS}}

## OpenROAD report: the worst path of each of the worst families (cell by cell, with fanout; may be truncated)

```
{{REPORT}}
```

## Earlier ASIC rounds of this project

Rounds may come from several batches at different clock periods (`@ N ns`). For rejected rounds, read
"worst families after the change": a change that shortens one path often lengthens a path downstream of it.
Example seen at 7.5 ns: retiming `frontend.fq` / `ShiftQueue` exposed `core.ex_reg_rs_msb_*` (ibuf -> decode ->
bypass) 1-1.6 ns worse than the old worst path. Check what your change feeds before you submit it.

{{HISTORY}}

Pick ONE new idea (do not repeat a rejected idea unless you fix the reason it failed). It may touch several
families if they share the structure you change. Implement it, then give your report; in WHY, name the
families you target.
