# Scale probe and P3 adapter preparation

## Existing RC scale behavior — observed, not a leak claim

Standalone probe `tools/eq3_trace_probe.cpp` links the unchanged P1 core.
32 positive-capacity nodes with conservative chain conductance, no boundary
loss, 0.01 J per irregular event, exact event boundaries, delayed completions.
This is SOFTWARE_SCALE_PROBE, not a device/physics experiment.

| Events | Distinct dt / cached factors | Factor payload bytes (excludes allocator/map) | Peak RSS KiB | Solver wall s | Wall s / simulated s |
| --- | --- | --- | --- | --- | --- |
| 256 | 426 | 3,489,792 | 7,092 | 0.004451599 | 0.001738906 |
| 1024 | 815 | 6,676,480 | 10,568 | 0.015040638 | 0.001468812 |

Both verified injected versus stored energy (error <5e-10 J), all completions
delivered, zero pending activities afterward, seen-ID counts exactly256/1024.
Receipts: workspace `eq3_thermal/runs/p2-scale/*valid-id*`, stdout JSON and
independent time/RSS stderr. Actual values are one execution each, no statistical
claim. Two prior setup attempts exited134 before solving because the probe used
reserved activity ID zero; the input generator was corrected to IDs1..N.
Both failed receipts remain, and all four attempts count conservatively against
the shared 12-attempt engineering budget. The core was not changed to hide error.

Static source confirms distinct mechanisms: factor cache O(unique_dt*n²),
seen-ID set O(total distinct events), and preloaded activity scan per interval
O(events×boundaries). The measured tiny fixtures stay within budget and do not
identify which cost dominates at target scale. They do demonstrate that exact
floating-point interval differences can create many factors. A rough payload
projection at n=224 and 815 factors is about327 MB, NOT an observed run.
No solver/cache optimization or extra ROM is justified as a delivery prerequisite
by these small runs; long-trace target envelopes must be gated before scaling.
Do not round boundaries or discard deduplication to reduce these counters.

Rebuild outside the source tree using a C++20 compiler:

```sh
c++ -O2 -std=c++20 -I include tools/eq3_trace_probe.cpp \
  src/eq3_thermal/thermal.cpp src/eq3_thermal/text_format.cpp -o BUILD/trace_probe
```

The probe accepts only256 or1024; additional runs still obey experiment approval
and shared budget, not merely this command's availability.

## P3 minimum causal observer contract — preparation only

Source basis: `include/hbfsim/mqsim_online.hpp`,
`src/mqsim_adapter/mqsim_online.cpp`, `src/host_service/main.cpp`,
`tests/integration/mqsim_observation_test.cpp`, `mqsim_horizon_test.cpp`.

- Existing request Arrival/Admission/Completion observations do not identify
  physical NAND die/plane/operation or refresh provenance. A request-level
  energy approximation must be explicitly LOGICAL_REQUEST_PROXY, never physical.
- Admission to MQSim is not a NAND operation start. Raw MQSim completion and
  aggregate bandwidth-bounded user completion are distinct times; preserve both.
- `ThermalModel::add_activity` rejects start times before its current clock.
  A completion-only observer cannot inject past energy into an already advanced
  model. Offline replay may first buffer complete intervals, sort their causal
  boundaries, then advance. Live shadow needs physical-start notifications or a
  documented lag/watermark, not clock rewind or instantaneous end-time dumping.
- `run_next_completion_until(deadline_ns)` permits progress without completion;
  nullopt is not dropped work. Cooling and future maintenance need explicit time
  advancement when user traffic is absent.
- Required parity: identical request values, ordering, publication and completion
  times in off/read_only/shadow; off constructs no solver; shadow affects no
  admission. Check delayed/failed work, empty queues and terminal horizon.
- Integration may later touch only optional host composition/host observer seams.
  Public ABI, PTX, TMA, future and cache paths remain unchanged this turn.

P3 live adapter is NOT_IMPLEMENTED. P4 actual refresh/resource/energy/wear/age
completion closure is NOT_IMPLEMENTED. OCP maintenance is not automatically an
SSD page-copy+erase. All unsupported nonthermal ablations remain blocked; this
note and existing topology graphs do not implement them.
