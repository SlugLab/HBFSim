# HBFSim v0.1.2 — Optimized Edition

This package brings together the tested optimization settings, source code,
build instructions and performance data. The OLMoE full147 configuration is
optional. Default simulator physics remain unchanged; measured improvements
apply to the documented input and validation scope.

- [Release notes and measured comparison](../../docs/releases/v0.1.2.md).
- [STRATEGY.json](STRATEGY.json): tested settings and observed component identities.
- [REBUILD.md](REBUILD.md): source/parent dependencies, CUDA-domain separation,
  reconstruction steps, and explicit verification gaps.
- [SOURCE_INDEX.json](SOURCE_INDEX.json): included source files and
  source-to-object/archive/plugin lineage.
- [evidence/RESULTS_INDEX.json](evidence/RESULTS_INDEX.json): original identities,
  validation scope, original batch failure and archive/release evidence.
- [evidence/SUMMARY.csv](evidence/SUMMARY.csv) and
  [evidence/REGIONS19.csv](evidence/REGIONS19.csv): exact host timings.
- [FOLLOWUP.md](FOLLOWUP.md): next performance questions, evaluated separately from
  this version.

Run `python3 verify_packet.py` from this directory to check packet membership
and identities. Publication CPU checks are separately recorded; they do not
repeat or substitute for model correctness, device timing or physical
calibration. Acquire the licensed model and vendor-generated inputs separately.

Historical source bytes remain exact. Absolute paths inside archived sources
or provenance, if any, describe the original experiment and are not portable
execution defaults. Do not run historical GO, INTENT, process IDs, reservations,
old deadlines or source-bound launch plans. No such launch authorization is
provided by this packet.
