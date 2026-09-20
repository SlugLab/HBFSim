# Four-topology system/thermal sensitivity freeze candidate

Status: **INPUT METHOD APPROVED; NOT LAUNCHED; DEPENDS ON THE SERIAL BASE MATRIX.**
The user authorized a bounded sensitivity study. This file freezes the
conditional engineering method and prevents an accidental seven-dimensional
grid. It does not claim device calibration or approve a new physical model.

## Representative scenario and comparison

The primary OAT scene is mixed-direct Q1, W1 continuous offered read pressure,
1.536 TB/s per HBF stack, 20 s active plus 10 s recovery. The controlled arms
are `guard_only` and `read_rate_feedback_thermal_guard_v1`. A third,
control-disabled execution of every physical combination reports which
temperature constraint appears first without treating the two policies as
equivalent. It preserves the same sources and service resources; it only fixes
future budgets to baseline.

HBM temperature limits cannot affect Q1 foreground service because this Q1
workload offers bytes only to HBF and has no relay dependency. The mixed Q1
HBM-limit rows are therefore `OBSERVATIONAL_ONLY`: they may alter reported
first-constraint classification, not delivery. The two non-nominal HBM levels
are additionally run on relay Q2 with identical offered HBF demand, where the
partner HBM endpoint is an actual joint-admission consumer. This adds six
mechanism points without multiplying every axis by topology.

## Seven axes and evidence boundaries

| Axis | Low / nominal / high | Actual consumer | Evidence and claim boundary |
| --- | --- | --- | --- |
| Retention Ea | 1.01 / 1.04 / 1.08 eV | `ReliabilityLedger` equivalent-age integral and maintenance driver, once ready | HeatWatch 95% fit interval for old 30–40-layer charge-trap MLC, 20–70°C and 1k–10k P/E. `PROXY`, not target-HBF uncertainty or an RBER/lifetime model. Deferred now. |
| Ambient | 300 / 310 / 320 K | Derived full RC model: all node initial temperatures plus top/bottom ambient move together | `SCENARIO_ASSUMPTION`. This is a matched temperature translation; it does not introduce an initial-to-ambient cooling transient. |
| External boundary resistance | 0.5 / 1 / 1.5 × nominal `1/h`, on both top and bottom | Derived full RC model recomputes `A/(dz/(2k)+scale/h)` | `CONDITIONAL_ABLATION`. Material `k`, heat capacities and internal edges stay fixed. It is not a TIM axis and not independent per-edge scaling. |
| HBF read energy | 0.5 / 1 / 1.5 × the 40 pJ/B array + 10 pJ/B base scenario | `EnergyMapper` read array and HBF base terms | `SCENARIO_ASSUMPTION`; preserves the 4:1 split. It does not scale HBM, relay endpoint, GPU, program or erase energy. Nominal is user-confirmed engineering input, not product measurement. |
| GPU external heat | 0 / 100 / 200 W | `run_system_point` supplies per-window energy to the actual GPU thermal component | Reuses the registered 0–200 W conditional envelope; independent of throttled memory delivery. It is not token-causal GPU utilization. |
| HBF guard limits | Light/Severe shifted −5 / 0 / +5 K from 353.15/363.15 K; Shutdown fixed 378.15 K | Thermal guard and next-window budgets | Research-policy sensitivity. The OCP 105°C envelope is never raised; none of the triplets is a product control recommendation. |
| HBM guard limits | Same Light/Severe shifts; Shutdown fixed 378.15 K | Observational only in Q1; actual paired endpoint gate in relay extension | Research-policy sensitivity. Values above the 95°C primary HBM evidence domain remain declared excursion scenarios, not HBM4 product limits. |

GPU guard limits stay fixed at 363.15/373.15/383.15 K. The thermal solver's
400 K domain is unchanged. A trial crossing 400 K is retained as
`DOMAIN_FAILURE`; it is not clamped, retried with a different input, or used to
stop independent points.

## Point construction

For the six presently executable axes, OAT contains
`1 + 6 × (3−1) = 13` distinct physical combinations, including the shared
baseline. Three interactions are preregistered because they test coupled
mechanisms that an additive OAT cannot resolve:

1. 320 K ambient × 1.5 external resistance;
2. 1.5 HBF read-energy scale × 200 W GPU external heat;
3. conservative HBF and HBM Light/Severe thresholds together.

Each of these 16 combinations has two controlled arms and one uncontrolled
first-constraint arm: 48 mixed-direct points. The two HBM threshold endpoints
have the same three arms on relay: six more points. **Current executable total:
54 deterministic points.** No repetitions or confidence intervals are created
for this deterministic model.

Ea remains a separate six-point block. Before those points become runnable,
the maintenance consumer must be connected and fixed-tested. The block uses
1.01/1.04/1.08 eV across the two controlled strategies, an exact initial age
near `DAY−2 s`, and the same 4 GiB/stack aged subset. A separate fresh/null
comparator must be retained. These points cannot reuse or be paired as though
they were the fresh read-rate baseline. Outputs remain conditional equivalent
age and maintenance traffic; RBER, ECC, failure probability and lifetime stay
unavailable.

## Model inputs and preparation contract

The existing mixed full 2 mm source is reused for 300 K/R1. The registered
single-axis models supply 310 K/R1, 320 K/R1, 300 K/R0.5 and 300 K/R1.5. The
interaction additionally requires the explicitly derived 320 K/R1.5 model;
it cannot be approximated by either single-axis directory. That model contains
64,512 nodes and 188,480 retained edges and is `DERIVED_NOT_SOLVED`.

`experiments/eq3_system_thermal/prepare_sensitivity.py` writes immutable point
configs, hashes, model paths and `SENSITIVITY_INDEX.json`. It refuses a missing
thermal variant, refuses an output directory that already exists, and never
launches a process. `DEFERRED_EA.json` is machine-readable and has
`runnable=false`.

The generated index status is `PENDING_DEPENDENCIES_BASE_MATRIX`. Per-point
limits remain one CPU, GPU 0, 8 GiB address space, 600 s and 1 GiB retained
output. The completed pilots bound a 30 s point at 98.34 s and 496,762,063
bytes; the base projection is 29,805,723,750 bytes. The sensitivity allocation
is 27 GiB inside the existing parent-stage 80 GiB total, with a 6 h shared
stage wall bound, 32 GiB available-RAM reserve and 100 GiB free-disk reserve.

`launch_sensitivity.py` is separate from the frozen base launcher. It refuses
to start until `BASE_DONE.json` says `COMPLETED`, then rechecks every config,
runtime source, model and thermal binary hash. It persists launch metadata
before each process. An explicit thermal-service `DOMAIN_FAILURE` is retained
and the launcher continues independent points. A numerical/protocol failure,
identity mismatch, resource limit or watchdog still stops the stage. No
failure path relaxes 400 K.

## Fixed validation

The preparation fixture generates all 54 configs in a temporary directory and
checks unique identities, 48 mixed plus 6 relay points, 18 uncontrolled arms,
actual relay consumer labels, fixed HBF/HBM shutdown, fixed GPU limits, fixed
400 K domain, source/model/resource locks, and non-runnable six-point Ea
metadata. A second test permits continued execution only for an explicit
thermal `DOMAIN_FAILURE` and rejects a numerical failure. Result: **2 tests,
PASS**. No thermal or native backend process ran.
