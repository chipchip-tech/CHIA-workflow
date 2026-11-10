# Round {{ROUND}} of batch `{{RUN}}` — target clock period {{PERIOD}} ns

Current best design (all previously accepted changes are already in the fork):
- post-route WNS {{BEST_WNS}} ns (Fmax ≈ {{BEST_FMAX}} MHz), TNS {{BEST_TNS}} ns, best of place directives {{DIRECTIVES}}.
- resources: LUT {{LUT}}, FF {{FF}}, BRAM {{BRAM}}, DSP {{DSP}} (device: 63400 LUT, 126800 FF, 135 BRAM, 240 DSP).
- benchmark mcycle geomean {{GEOMEAN}} (budget ≤ {{BUDGET}}).

A change is accepted only if WNS improves by more than {{ACCEPT}} ns, the gate passes and cycles stay
within budget. Otherwise it is reverted.

## Top paths of the current best implementation

| slack | levels | delay | start | end |
|---|---|---|---|---|
{{PATHS}}

Full reports (readable with your tool):
- {{PATHS_RPT}}
- {{TIMING_RPT}}
- {{UTIL_HIER_RPT}}

Hierarchy hint: `chiptop0/system/subsystem_l2_wrapper/l2/inclusive_cache_bank_sched` is
`InclusiveCacheBankScheduler` in `generators/sifive-cache/design/craft/inclusivecache/src/` (Scheduler.scala,
MSHR.scala, ...). Rocket core code is in `generators/rocket-chip/src/main/scala/rocket/`.

## Earlier rounds of this project

{{HISTORY}}

Pick ONE new idea (do not repeat a rejected idea unless you fix the reason it failed), implement it,
then give your report.
