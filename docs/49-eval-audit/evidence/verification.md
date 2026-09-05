# Phase 1 verification — 2026-09-05

- Existing CPU optional-tools CTest: **42/42 passed**, 29.40 seconds. This re-ran existing built binaries; no fresh production build or GPU execution is claimed. See [cpu-ctest.log](cpu-ctest.log).
- Evaluation pipeline tests: **20/20 passed**, 4.933 seconds. Covers schema/unit/range errors, exact sibling pairing, heterogeneous slice rejection, incomplete heatmaps, automatic MOCK watermark, strict/final-path MOCK rejection, artifact hash checks, and the same renderer accepting explicitly synthetic PROJECTED test fixtures in temporary directories. See [pipeline-tests.log](pipeline-tests.log). Such fixtures are neither measured results nor research predictions.
- Original SM120 branch transform compiled and reproduced the predicated-consumer counterexample on CPU. See [async-probe.json](async-probe.json); this is static evidence, not an observed GPU failure.
- Six MOCK figure bundles (PNG/PDF/SVG), 2,198 MOCK rows. The renderer manifest binds its input and plotting source hashes. All six PNG previews were visually inspected.
- Matrix: 2,848 conditions / 20,485 planned repeats; all costs are planning estimates.
- Production diff remains empty. No runtime integration, hardware performance acquisition, model download, commit or push was performed in this phase. GPU experiments remain blocked by unavailable driver communication.

See [delivery-checks.json](delivery-checks.json) for final mechanical checks and [artifact-hashes.json](artifact-hashes.json) for the uncommitted review snapshot's file hashes.
