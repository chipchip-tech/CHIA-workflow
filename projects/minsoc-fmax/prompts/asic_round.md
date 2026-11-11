# Round {{ROUND}} of batch `{{RUN}}` — ASIC {{PDK}}, target clock period {{PERIOD}} ns

Current best design (all previously accepted changes are already in the fork):
- STA WNS {{BEST_WNS}} ns (Fmax ≈ {{BEST_FMAX}} MHz), TNS {{BEST_TNS}} ns, cell area {{AREA}} µm² (without SRAM).
- benchmark mcycle geomean {{GEOMEAN}} (budget ≤ {{BUDGET}}).
- paths through cells matching `{{EXCLUDE}}` are excluded from timing in this batch (ignore them).

A change is accepted only if WNS improves by more than {{ACCEPT}} ns, the gate passes and cycles stay
within budget. Otherwise it is reverted. Note that WNS is the single worst path: after one block is fixed the
next-worst block (possibly elsewhere) sets WNS, so look at TNS and at every distinct start/end group below.

## Top paths of the current best design

| slack (ns) | arrival (ns) | start | end |
|---|---|---|---|
{{PATHS}}

## OpenROAD report of those paths (cell by cell, with fanout; may be truncated)

```
{{REPORT}}
```

## Earlier ASIC rounds of this project

{{HISTORY}}

Pick ONE new idea (do not repeat a rejected idea unless you fix the reason it failed), implement it,
then give your report.
