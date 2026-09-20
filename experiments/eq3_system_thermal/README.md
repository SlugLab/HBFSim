# Isolated four-topology conditional thermal system

Default-off CPU experiment modules. The original MQSim source, default backend,
production scheduler/ABI and GPU paths are unchanged. Results are
`CONDITIONAL_SIMULATED`, not measured HBF throughput, calibrated temperature,
error probability or token performance. Original P2 failures and400K domain
checks remain; no model freeze or blind-test opening is implied.

## Actual consumers

- `run_system_point.py`: fixed20ms byte-cohort service, four real route variants,
  per-source energy, existing complete coupled thermal network and future gates.
  This is the frozen60-point rate matrix consumer. Tokens areUNAVAILABLE.
- `run_endpoint_guard_point.py`: same isolated chain plus HBM endpoint recovery
  adapter. HBF feedback is unchanged; an HBM forwarding endpoint with no local
  demand must not retain a half quota after its temperature state recovers.
- `run_maintenance_point.py`: rate foreground and real aggregate
  read/program/version-commit/erase jobs compete in the same resource service;
  temperature-history age produces future maintenance. The explicitly ideal
  independent-resource ablation preserves maintenance/Shutdown semantics.
- `run_causal_point.py`: exact subwindow event service and a dependency executor.
  Actual service completion unlocks compute; only final compute completes a
  simulated token. Thermal/control windows stay20ms. Same-ledger maintenance is
  supported in physical-channel mode. Uniform-group mode explicitly rejects
  physical maintenance extent claims.

All are behavioral fluid consumers. The small isolated native MQSim receipts
validate lifecycle/arbitration correspondence, notTB/s performance. See
`docs/eq3_thermal/NATIVE_PROXY_COMPARISON.md` and
`docs/eq3_thermal/SYSTEM_THERMAL_INTERFACE_COVERAGE.md` for evidence boundaries.

## Routes, capacity and energy

OCPGrade2 scenario:16channels×96GB/s perHBF=1.536TB/s, with explicit channel→die
mapping. Offered demand can exceed this; delivered bytes remain constrained by
media, source/partner base buffers, direct/relay links, endpoint quotas and
thermal guard. DASH direct/relay routes share media supply and unique byte
identities. Relay uses both hops and partnerHBM resources. No phantomHBM array
access is added to forwarding.

The energy baseline is user-confirmed40pJ/B HBFarray+10pJ/B HBFbase. HBM,
forwarding, program and erase coefficients are separate documented engineering
proxies. Each activity phase is counted once. Unknown idle/selfheat is excluded
from this incremental scenario, not claimed physically zero. External GDDR
thermal/service areUNAVAILABLE in8HBF package-only experiments.

Uniform causal mode groups16 physical channels into aggregate capacity and
spreads energy over all16 die. DASH children retain one shared media group.
Fixed tests compare capacity/bytes/per-die energy with physical mode. Nonuniform
experiments use the physical16-channel rate consumer. Buffers use finite
occupancy bounds and continuous fluid turnover, not a per-page NAND timing model.

## Causal trace and mechanisms

`tiny_cpu_trace.py` records an actual deterministic random-weight NumPy
Qwen2-style forward. `build_architecture_trace(dependency_mode='tiny_cpu_template')`
validates its operation/access order, GQA shapes and canonical checksum, then
regenerates target7B/72B layer counts, BF16 tensor payloads, scenario addresses
and analytical MAC counts from registered official architecture metadata.
Generated tasks carry the observed dependency template identities. Explicit
compute durations remain scenario inputs; tinyCPU elapsed time is never
scaled intoGPU timing. `synthetic_metadata_dag` remains a separate classified
mode. KV/activation traffic and pretrained model quality are not modeled.

`CausalExecutor.poll(now)` returns jobs once. The consumer submits these to
`CausalTopologyService`, advances to the next service/compute/window event and
calls`complete` only from actual byte-complete service receipts. Streaming DAG
retirement bounds memory while preserving arrival times. Coalescing, one-layer
prefetch, issue-stall/consumption-wait, finiteLRU with actualHBM fill/read,
version-safe migration with program/commit/erase and0/1/4 named retry scenarios
have actual consumers. Retry completion does not duplicate useful bytes.

Uniform causal workload payloads are continuous byte traffic, not NAND
page+OOB/ECC-encoded wire bytes. Page/block/plane evidence and tiny tail-padding
approximation are in`docs/eq3_thermal/GEOMETRY_AND_BYTE_SCOPE.md`.

## Retention and maintenance

`ReliabilityLedger` integrates contiguous temperature intervals into equivalent
age at358.15K, using separately sourced Ea proxies1.01/1.04/1.08eV. The24h
cadence is not compressed. `wall_only` leaves Ea observational;
`equivalent_age_or_wall` makes it causal through the declared conservative
refresh policy, not a predicted device failure threshold.

Only successful per-extent program+version commit resets that extent's age.
Failed/conflicted destinations need cleanup erase; failed erases quarantine
blocks. Source data stays valid until commit and newer writes cannot be
superseded. Finite spares are channel-owned. Successful physical block erase
increments wear counts; there is no unsupported damage/lifetime model.

The thermal rate maintenance scenario uses a4GiB aged subset perHBF,
4096×1MiB blocks with256×4KiB pages and16spares/channel. It is not a whole512GiB
capacity simulation. Shared/ideal arms receive identical initial age. Rate
age integration uses observed window endpoint midpoint temperature; exact
causal maintenance uses the previous known die temperature until its next
thermal observation, integrating to the exact commit before resetting age.

## Execution and evidence

Stage artifacts are outside source under`eq3_thermal/plans/four-topology-system-v1`.
Inputs, actual consumer/model/binary locks and finite resources precede every
run. CPU experiments are serial, BLAS1/GPU0. Watchdogs preserve failedraw;
status files and estimated completion checks avoid frequentAI polling.
`prepare_*` writes inputs only. `freeze_extension.py` binds reviewed extensions
to the serial launcher. Pilots gate main runs. Scope authorization comes from
the user's explicit plan, not generated approval files.

Fixed software tests prove specific contracts; actual run receipts determine
thermal closure and system results. No qualified thermal fast path exists;
service native/proxy comparisons are not substitutes for a thermalROM ablation.
