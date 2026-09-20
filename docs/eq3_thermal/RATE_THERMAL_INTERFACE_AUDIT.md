# Per-stack read-rate to thermal-input interface audit

Date: 2026-09-20
Scope: read-only audit; no solver, MQSim, profile, controller, or campaign run was
started or changed.

## Answer

The existing complete package thermal network can already consume a different
time-varying heat input for every HBF stack without issuing NAND requests.  The
supported boundary is **component energy per fixed thermal interval**, not
bytes/s.  A small, default-disconnected producer can convert an externally
prescribed per-stack rate trace to array/base component energy and feed the
unchanged persistent thermal service.  This preserves GPU, every HBM/HBF die,
every base die, and the shared package/cooling paths.

The unresolved part is the physical conversion from HBF delivered bytes to
watts.  Sandisk and OCP specify bandwidth targets but no HBF idle/full power or
read J/B.  Therefore rate comparisons are immediately possible as normalized
per-watt responses or as an explicitly named engineering proxy.  The user has
now explicitly selected the original 80 W envelope mapping for this conditional
simulation: at 1.6 TB/s, array=64 W and base=16 W, hence 40/10 pJ/B.  Idle
increment remains `UNKNOWN_NOT_MODELLED`.  This closes the engineering input;
it does not turn it into a product measurement.

## Existing producer and consumer chain

| Boundary | Existing symbol/file | Actual behavior | Read-rate use |
|---|---|---|---|
| Power grouping | `tools/eq3_layered_ir.py::_power_groups`, `_normalize_power` | Expands `hbfN.array` equally over its 16 powered die components and keeps `hbfN.base` separate; validates caps and source-to-component energy conservation | Reuse unchanged to expand each stack's array/base watts |
| Offline source generation | `tools/eq3_all_source_cap.py::build` | Converts grouped watts to volume-weighted node energy with a receipt | Reusable if a rate trace is first expressed as grouped power intervals |
| Window replay conversion | `experiments/eq3_maintenance/pilot_energy_replay.py::convert` | Converts contiguous `WINDOW_TOTAL` component joules to native RC events without consuming activity rows twice | Reusable for offline runner comparison |
| Persistent client | `experiments/eq3_maintenance/thermal_client.py::ThermalService.advance` | Accepts `{component_id: energy_j}` for one contiguous 20 ms interval and sends `ENERGY` then `ADVANCE` | Direct consumer for an open-loop rate trace |
| Persistent service | `experiments/eq3_maintenance/thermal/thermal_service.cpp::energy`, `advance_one` | Rejects unknown/duplicate/non-aligned input; computes `power=energy/duration`; distributes it across the component by cell volume; advances the existing sparse RC factorization | No solver or model change is needed |
| Output | persistent service `ADVANCE` response | Returns all entity mean/hotspot temperatures, all registered sensors, and window/cumulative energy conservation | Suitable for per-stack temperature curves and coupling comparisons |

The service protocol is already sufficient:

```text
ENERGY START_NS END_NS COMPONENT_ID ENERGY_J
ADVANCE END_NS
```

For one rate window of duration `dt`, a source-only adapter would calculate,
for each stack `s`,

```text
P_array,s = P_array,idle,s + rate_s * e_array
P_base,s  = P_base,idle,s  + rate_s * e_base
E_die,s,i = P_array,s * dt / 16       (i = 0..15)
E_base,s  = P_base,s  * dt
```

and submit `hbfS.die0..15` plus `hbfS.base`.  The 8HBF model uses the same
component convention for `hbf0..hbf7`; the mixed model uses `hbf0..hbf3` while
retaining HBM and GPU as passive coupled components unless they receive their
own explicit sources.  Rates must be averaged over the 20 ms thermal interval;
sub-window bursts are integrated energy and cannot produce a resolved
sub-20-ms temperature peak.

Adding a new `RATE` command to the thermal process is unnecessary.  Keeping
rate-to-energy outside the solver makes the uncertain power law versioned and
replaceable while preserving the existing solver protocol and factorization.

## Available power evidence and its limits

| Candidate | Values and current consumer | Status for rate-to-heat |
|---|---|---|
| HBF product power | Sandisk Gen1 target is 1.6 TB/s per 16-die stack; OCP v0.7.0 grades are 0.384/1.536/3.072 TB/s. Neither source gives HBF idle watts, full-load watts, or J/B. | `UNKNOWN_BLOCKING` for absolute product temperature; bandwidth alone is not heat |
| Original research excitation envelope | `configs/eq3_thermal/research/calibration_power.json` declares 64 W array plus 16 W base per memory stack. `eq3_layered_ir` consumes these as separate groups; it is synthetic calibration input, not a product value or measured 80/20 partition. | Usable as an explicit `SCENARIO_ASSUMPTION` |
| D3 input domain | `tools/eq3_domain_v2_input.py` applies the frozen common `alpha=0.25`; the resulting per-stack caps are 16 W array plus 4 W base. D3 remains conditional and reference-unqualified. | Inputs within 20 W/stack stay in the executed D3 power domain; this does not confer product calibration |
| HBM3E aggregate proxy | Registered public data derives 45.815--46.941 pJ per delivered byte from aggregate memory-domain `(loaded-idle)/bandwidth`. It has no per-stack samples and no HBF array/base/PHY split. | May be a clearly labelled transfer proxy; must be counted once, not once per stage |
| Later native activity proxy | `ActivityEnergyLedger` uses 0.05 W per active NAND die, 0.01 W during command/data-out intervals, and 2 pJ/B for a separate fabric endpoint. | Cannot convert an arbitrary prescribed bytes/s trace by itself. It requires actual busy intervals and is not a single HBF J/B model |
| HBF idle/full power | No registered HBF value. The HBM3E dataset's idle value is an aggregate platform memory-domain measurement, not HBF idle power. | Keep `UNKNOWN`; zero incremental idle means “not modelled,” not measured zero |

OCP's 16 banks/channel example does not establish a NAND plane identity or a
power partition.  DASH explicitly states that public HBF subarray parallelism
is unavailable and uses its own assumptions (16 dies, 32 planes/die, four
independently accessible subarrays/plane, 3 us read).  Those assumptions may
motivate a separate scenario, but they do not turn OCP bank into MQSim plane or
provide HBF J/B.  See OCP v0.7.0 Table 3/4 and
[DASH Sections II-B, IV-B and VI-A](https://arxiv.org/html/2608.14333v1).

## Immediately usable comparison modes

### 1. Normalized per-watt response

This requires no HBF energy assumption.  Drive `hbfN.array` and `hbfN.base`
independently with a unit source, record temperature rise per applied watt, and
report the response against normalized rate `u_s(t)=rate_s(t)/R_ref`.  Results
are transfer responses of the assumed RC package, for example K/W at a sensor
or the temperature trace produced by a declared unit-power waveform.  They do
not carry an absolute HBF junction-temperature claim.

The network is linear for fixed material/boundary parameters, so component
mean temperature increments can be combined from source responses.  Hotspot
`max` is a nonlinear reduction and must be recomputed from the combined field
or directly advanced through the existing service; hotspot maxima must not be
added.  The completed A1 audit already found full-network entity-mean
superposition consistent for its fixed source replay while retaining all
cross-stack cooling paths.

### 2. Existing 1.6-TB/s research-envelope scenario

A minimal complete engineering mapping is

```text
P_array = 64 W * rate / 1.6e12 B/s
P_base  = 16 W * rate / 1.6e12 B/s
```

This is equivalent to 40 pJ/B assigned to the array and 10 pJ/B assigned to
the base.  These coefficients are derived from the synthetic 80 W envelope,
not measured HBF values.  This mapping is `USER_CONFIRMED` for the present
conditional simulation.  The resulting prescribed powers are:

| Per-stack rate | Array W | Base W | Total W | Domain note |
|---:|---:|---:|---:|---|
| 0.384 TB/s | 15.36 | 3.84 | 19.20 | Inside the executed D3 alpha=0.25 caps |
| 1.536 TB/s | 61.44 | 15.36 | 76.80 | Inside original 64/16 envelope, outside D3 alpha=0.25 domain |
| 1.600 TB/s | 64.00 | 16.00 | 80.00 | Original-envelope endpoint, outside D3 alpha=0.25 domain |
| 3.072 TB/s | 122.88 | 30.72 | 153.60 | Outside both original and D3 domains |

The 3.072-TB/s point must not be clipped to 80 W.  It needs either a new
explicit power-domain/scientific approval or normalized-only reporting.
Likewise, D3 accuracy/status cannot be inherited by the 1.536/1.6-TB/s points
merely because the solver accepts their joules.

### 3. Aggregate HBM3E transfer proxy

Applying the registered 45.815--46.941 pJ/B range once gives the following
dynamic powers, before any unknown HBF idle power:

| Per-stack rate | Proxy dynamic W |
|---:|---:|
| 0.384 TB/s | 17.59--18.03 |
| 1.536 TB/s | 70.37--72.10 |
| 1.600 TB/s | 73.30--75.11 |
| 3.072 TB/s | 140.74--144.20 |

This is useful as a scale comparison with the 80 W research envelope.  It is
not an HBF range.  To send it into the spatial model, a separate declared
array/base allocation is still required; the same aggregate energy cannot be
added to array, base, PHY, and fabric independently.

## Retained MQSim feasibility boundary

The current isolated MQSim profile is not needed for a prescribed-rate thermal
study.  Its existing limitation should nevertheless remain visible so a
thermal input is not described as achieved backend throughput:

- `16 channels * 8 bits * 1600 MT/s = 25.6 GB/s` nominal native bus per stack;
- `256 planes * 4096 B / 10 us = 104.8576 GB/s` is only a loose array
  concurrency arithmetic bound.  It is not executable independent-plane
  throughput because MQSim groups same-die/same-page multiplane commands and
  serializes shared channel transfers;
- global QD256 provides only 64 outstanding requests/stack for a balanced
  four-stack trace; the retained warm read delivered about 11.96 GB/s/stack;
- the existing 512 GB/s completion envelope is shared by the whole engine,
  not a per-stack HBF link.

Changing only queue depth or channel width cannot establish 0.384, 1.536, 1.6,
or 3.072 TB/s/stack with the current 4-KiB/10-us resource model.  A faithful
achieved-throughput model would require separately approved internal bank/
subarray parallelism, pipeline and per-stack completion-resource semantics.
That structural work is unnecessary for the user's stated rate-to-temperature
question.

## Minimal implementation and approval boundary

The smallest implementation is a standalone, default-off trace converter:

1. input contiguous 20 ms windows with explicit `rate_Bps` for every HBF stack;
2. require a named power model (`NORMALIZED_PER_W`,
   `RESEARCH_64_16_AT_1P6TBPS`, or a separately approved proxy), its evidence,
   idle status, and array/base allocation;
3. expand group power to real component joules using existing group weights;
4. write a source ledger containing rate, coefficient, source group, energy,
   input hashes and conservation totals;
5. feed the unchanged `ThermalService.advance` or existing offline replay path.

This needs no thermal equation, event ordering, checkpoint, public core ABI, or
backend change.  It must fail on missing stack rates, unknown coefficients,
out-of-domain power, non-contiguous windows, or energy mismatch.  It must not
silently infer idle power, saturate a high-rate point, or add the same energy to
multiple stages.

A new absolute HBF energy law, an expanded accepted power domain, a live
rate-feedback controller, or a product-bandwidth backend changes the scientific
input or request/control lifecycle and requires its own versioned preflight and
user confirmation.  None is required to produce normalized coupled-package
rate sensitivity with the interfaces already present.

## Frozen names and model identities for the immediate pilot

Use the following explicit profile identity in the rate ledger:

```text
profile_id: EQ3_HBF_RATE_TO_POWER_80W_AT_1P6TBPS_V1
evidence: USER_CONFIRMED_SCENARIO_ASSUMPTION
rate_semantics: PRESCRIBED_DELIVERED_BYTES_PER_S_NOT_BACKEND_ACHIEVED
array_j_per_byte: 4.0e-11
base_j_per_byte: 1.0e-11
idle_power: UNKNOWN_NOT_MODELLED
array_spatial_mapping: EQUAL_OVER_16_THERMAL_DIES_PER_STACK
thermal_window_ns: 20000000
```

For a window `[t0,t1)`, emit `rate_Bps * 4e-11 * dt / 16` joules to every
`hbfN.dieI` and `rate_Bps * 1e-11 * dt` joules to `hbfN.base`.  Name the source
groups `hbfN.array` and `hbfN.base`; these match the existing power-group
registry and avoid inventing a PHY component.  Record the group total before
expansion and require exact component-sum conservation within floating-point
tolerance.

For the smallest same-network pilot, use the existing mixed Q1--Q3 model:

```text
/root/hbfsim-exp/eq3_thermal/generated/campaign-RC2MM-train
```

It contains 64,512 cells, 255 entities and 275 sensors.  The locked SHA-256
values are `ecc7d24ad7a107466eaa7e6cbd37add087a57677aa32283e8850fa10c6c62559`
(`model.txt`), `b8e611d0a9c0db325efdade111fb83c89135434197f55a3f3924f9285cce5a23`
(`rc_grid.json`), `fe700161c57d6d66da4eb0dfadf25d72732a24a6fe9b5b0d79c770c934dcd73b`
(`rc_sensors.json`) and
`f61365623ddca5e55ae07fcb2b0599e2302f483810c090c327b6c1b57f394280`
(`normalized.json`).  It preserves four HBF stacks, four HBM
stacks, GPU, package and shared cooling, so different `hbf0..hbf3` rates expose
both self-heating and coupling.

The separate eight-HBF model is at
`eq3_thermal/plans/isolated-maintenance-campaign-v1/points/Q4-THERMAL-STATIC01/generated_2mm`
(40,960 cells, 287 entities, 307 sensors).  It should be used only when the
question specifically requires eight HBF stacks; it is a distinct generated
geometry and does not inherit P2 qualification.

Linearity applies because the locked model holds heat capacities,
conductances, material properties, Robin boundaries and 300 K reference fixed,
and the service solves a constant sparse linear system with one reused
factorization.  It has no temperature-dependent material law or radiation.
The 300--400 K domain check, hotspot `max` reduction, and any external thermal
control decisions are not linear superposition operators; the service must
still evaluate the combined input and preserve a domain failure without
clamping.

## Implemented OCP Grade2 channel envelope

Latest user requested source-aligned channel counts and rates. The active profile uses16channels/stack,64-bit×16GT/s×75%=96GB/s effective/channel and1.536TB/s total (OCPv0.7.0 Tables2/4). This replaces an unrun equal100GB/s/channel Sandisk aggregate inference. The user-confirmed50pJ/B remains, so1.536TB/s maps to76.8W. channel_map and channel_capacity_Bps are explicit; segment channel_read_Bps drives actual chosen die heat sources. Excess per-channel and aggregate prescribed rates are rejected, not silently capped. No actual UCIe/NAND scheduling is claimed.

Two bounded3.2s OCPGrade2 thermal points have completed on unchanged mixed2mm full package; artifacts are eq3_thermal/plans/rate-thermal-v1/OCP-G2-UNIFORM01 and OCP-G2-CONCENTRATED01. Each184.32J; latter changes only hbf0's1.4..2.2s .384TB/s from16×24GB/s to4×96GB/s. Old unrun rate plans retained. Derived analysis reports matched-time differences instead of mistaking unchanged earlier global peak for absence of spatial effects.
