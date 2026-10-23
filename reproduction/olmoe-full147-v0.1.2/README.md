# Frozen OLMoE full147 HBF workflow, v0.1.2

This packet freezes the accepted optimization combination and its supporting
source and evidence. It is an **opt-in reproduction packet**, not a change to
the default simulator physics or a claim that every HBF path is faster.

- [Release notes and measured comparison](../../docs/releases/v0.1.2.md).
- [STRATEGY.json](STRATEGY.json): frozen switches and observed binary identities.
- [REBUILD.md](REBUILD.md): source/parent dependencies, CUDA-domain separation,
  reconstruction steps, and explicit verification gaps.
- [SOURCE_INDEX.json](SOURCE_INDEX.json): exact included source membership and
  source-to-object/archive/plugin lineage.
- [evidence/RESULTS_INDEX.json](evidence/RESULTS_INDEX.json): original identities,
  validation scope, original batch failure and archive/release evidence.
- [evidence/SUMMARY.csv](evidence/SUMMARY.csv) and
  [evidence/REGIONS19.csv](evidence/REGIONS19.csv): exact host timings.
- [FOLLOWUP.md](FOLLOWUP.md): remaining optimization questions, separated from
  the frozen version.

Run `python3 verify_packet.py` from this directory to check packet membership
and identities. Publication CPU checks are separately recorded; they do not
repeat or substitute for model correctness, device timing or physical
calibration. Acquire the licensed model and vendor-generated inputs separately.

Historical source bytes remain exact. Absolute paths inside archived sources
or provenance, if any, describe the original experiment and are not portable
execution defaults. Do not run historical GO, INTENT, process IDs, reservations,
old deadlines or source-bound launch plans. No such launch authorization is
provided by this packet.
