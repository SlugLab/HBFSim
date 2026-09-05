# Thermal Reliability Integration Assessment

Source branch: `feature/thermal-reliability`  
Source SHA: `0069b2eec4d8b9d37cdbc9fbf84035d15572b0dd`  
Base hybrid SHA: `b41142288c1d1ca13be4219c320dbfa621a0300f`  
Audit date: 2026-09-05 UTC

## Existing functionality

The source implements deterministic modeled HBF junction temperature, live/trace/constant GPU-temperature inputs, threshold modes (normal/light/severe/shutdown), retention damage, read-disturb and PEC accounting, refresh scheduling, MQSim refresh work, fast-path debt/contention, device admission and terminal reports. Hybrid currently has calibration/LogP scripts and historical proofs, not this integrated reliability controller.

The source's `docs/proofs/2026-08-16-thermal-reliability.md` records historical validation; it is not evidence for a combination with today's queue-depth fix, PR4 and optional evaluation tools. Its live gate `tests/integration/test_thermal_timing_live.py` enforces exclusive GPU availability. No thermal tests were represented as passing on eval_base.

## Dependency graph

```text
Profile thermal_reliability (optional object)
  -> deterministic model / temperature state / threshold controller
  <- GPU telemetry or explicitly labeled trace/constant source
  -> retention damage / PEC / refresh debt
  -> range-to-media-offset refresh scheduler
  -> daemon + RequestDispatcher background work + MQSim
  -> shared control ABI v10 + device service/admission + fast contention
  -> terminal thermal report / schema / lifecycle tests
```

| Module | Source files | Introducing / corrective commits | Dependencies and impact | Default / tests |
|---|---|---|---|---|
| profile/model | include/hbfsim/profile.hpp; src/profile/profile.cpp; src/thermal/thermal_reliability.cpp | ba4ac03, 7eb23eb | geometry, temperature source, modeled state/retention math | absent optional profile disables model; thermal_reliability_test |
| temperature state/telemetry | src/cuda_runtime/thermal_telemetry.cpp; src/host_service/thermal_controller.cpp | 927f5a6, 976e740 | live/trace/constant identity and generation; controller to shared state | telemetry/controller tests |
| LTT/STT/RTT thresholds | include/hbfsim/thermal_reliability.hpp; src/thermal/thermal_reliability.cpp | 7eb23eb | normal/light/severe/shutdown and service fractions; modeled HBF temperature | deterministic model and consistency tests |
| retention/refresh/PEC | src/host_service/refresh_scheduler.cpp | ba71823, 147046f, 6a42ba0, d7adb10 | registered range media offsets, completed programs, refresh lifecycle | refresh_scheduler and thermal capacity tests |
| refresh to MQSim | src/host_service/main.cpp; request_dispatcher.cpp; src/mqsim_adapter/mqsim_online.cpp | c7e65b2, cb9427e | background request pairing, media engines, capacity/timing ordering | background_dispatch, contention tests |
| fast-path contention | src/cuda_runtime/device/hbf_device.cu/.cuh | 50fe572, a4bb006 | refresh debt, channel and thermal admission shared state | thermal_device_reference/consistency tests |
| ABI | src/host_service/control_layout.hpp and all producer/consumers | 18a973d | ABI 4 -> 10 plus inherited future/TensorMap/channel state | protocol and device_helper_abi tests |
| reports | src/reporting/thermal_report.cpp; configs/schema/thermal-reliability-summary.schema.json | bd49633, abfea74, 0069b2e | valid terminal states; measured vs modeled provenance | thermal report schema/lifecycle tests |

Paths/tests and commits in this table refer to the source branch, not files imported here. The schema gate is deferred with the subsystem.

## Changes relative to eval_base

At frozen hybrid the common ancestor is `0b34feb6459fb87eed8b40987e306d112118d363`; source is 61 commits ahead and 27 behind. The source-side delta spans 224 files, 46,377 inserted and 386 deleted lines. eval_base adds docs, PR4, offline tools and a test fixture; it does not reduce this architectural dependency closure. The full branch audit lists patch equivalence and no duplicate integration is attempted.

## SM120 dependency

`feature/sm120-exact-stage1` HEAD `f4dc28b2671c01939d98e4a968e6fb37b2e364d9` is an ancestor of the thermal source. Thermal has 21 additional commits on that complete SM120 lineage. The inherited code contains stable PTX IR, ordinary async futures, TensorMap provenance, TMA operations, calibrated channels, exact admission and fake-driver ABI updates. These are architecture changes, not necessary dependencies of evaluation documentation. No SM120 patches are cherry-picked.

## MQSim dependency

Refresh inserts read/program work and shares media contention. Correct range-to-media-offset mapping and completion-based PEC accounting are central. The source diverges before hybrid's queue-depth fix; it needs a new combined admission/refresh test rather than a whole-branch merge that risks losing the fix. Existing MQSim patch pins alone do not prove refresh correctness.

## ABI impact

`kControlAbiVersion` is 4 on hybrid/eval_base and 10 on thermal. The added control layout includes async-future, TensorMap, calibrated-channel, telemetry and refresh fields. Optional configuration does not make those ABI changes optional. All producers, consumers, device PTX and fake drivers would need coordinated validation.

## Baseline impact

Thermal can be configured off in its own branch, but that does not prove behavior identical to current hybrid: the lineage changes the device resolver, dispatcher, coverage machinery and ABI even before thermal activation. No combined off-mode parity was obtained, so isolation/baseline gates fail admission here.

## Tests and gate decisions

| Gate | Result | Evidence |
|---|---|---|
| A minimal isolation | NOT SATISFIED | 224-file delta with complete SM120 ancestry |
| B optional | source-level only | optional thermal_reliability profile object; code/ABI still different |
| C thermal-off baseline | NOT VERIFIED | no combined parity, different architecture |
| D ABI compatibility | NOT SATISFIED | control ABI 4 -> 10 |
| E MQSim correctness | NOT VERIFIED in combination | source tests exist; current queue-depth combination not replayed |
| F deterministic model | source tests exist | not imported/executed here |
| G telemetry provenance | source separates inputs | live hardware unavailable here |
| H report schema | source tests/fixes exist | no combined report proof |
| I combined regression | NOT VERIFIED | runtime prefetch also deferred |

## Risks

Importing all ancestry would change launch admission, coverage reporting, async/TMA semantics, media work and runtime ABI. Compilation alone cannot establish equivalence, physical temperature accuracy or workload slowdown. Treat historic proof numbers as dated evidence.

## Full-chain temperature branch

`全链路温度模拟` at `fd11c3d98cde74977be7ec504c64429369b6fd3a` has eight commits and no common ancestor with hybrid, SM120 or thermal reliability. Compare endpoint trees, not a nonexistent three-dot diff. It is a separately published full-source snapshot, not a merge successor of the thermal branch.

It contains `src/package_thermal`, package-thermal profiles/schema, 3D-ICE/RC/ROM state-space workflows, device access observation, offline media replay, an external HBM-power adapter and `RetentionRefreshModel`. Its optional package subsystem is a different direction from the ABI-v10 runtime controller. Published data flow is access observation -> offline MQSim -> activity/energy bins -> package thermal ROM -> Arrhenius age -> refresh demand/envelope. It explicitly does not feed refresh energy back into already emitted power bins or claim per-access live injection. Its original frozen stack is Python 3.13.9/vLLM 0.15.1/Torch 2.9.1+cu128/native CUDA 13; no automatic stack port is attempted.

The endpoint comparison against thermal/eval_base shows independent contents, not simply older/later thermal patches. Decision: DEFER; document the relationship only.

## Integration decision

**DEFER** code. Assessment and scope documentation are included; this does not label the feature implemented or validated.

## Reason

Isolation, ABI and combined-parity gates are unsatisfied. Keeping hybrid behavior and fail-closed coverage takes priority over importing an unverified architectural bundle.

## Future integration path

Start `eval/thermal-reliability` from eval_base. First isolate deterministic model/report inputs, then design an explicit ABI transition or boundary-preserving service interface. Prove every producer/consumer, media-offset/refresh pairing, queue saturation and completion-based accounting. Run thermal-off matched parity plus timing/capacity/PTX/refresh combinations, report-schema validation and hardware-exclusive live checks. Separately decide whether the package/ROM offline direction should remain an independent tool; do not mix its modeled thermal projections with measured GPU telemetry.
