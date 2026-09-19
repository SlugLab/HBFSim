# Independent EQ3 thermal P1

## P2 continuation status

P1 remains a verified standalone software fixture, not a calibrated package.
See [old-artifact applicability audit](HISTORICAL_SUITABILITY_AUDIT.md) before
reusing any historical floorplan/golden/ROM. The new small reference
[numerical report](P2_NUMERICAL.md) records a **failed** lumped-RC heldout test,
not a physics validation PASS. [GPU diagnosis](GPU_DIAGNOSTIC.md) distinguishes
default agent device isolation from successful authorized host enumeration.
[Source ledger](SOURCE_AND_GAP_LEDGER.md) and
[reference manifest](reference_manifest.json) separate numerical, proxy and
vendor evidence. [Approval contract](experiment_approval.md) applies before
formal or GPU runs. This turn made no existing runtime or core changes.

The next reference-only four-point refinement plan is persisted outside the
Git checkout under workspace `eq3_thermal/plans/p2-reference-refinement-v1`.
It binds a frozen code HEAD without a self-referential manifest commit.
Its status is PENDING_USER_APPROVAL; script availability is not authorization.

## P1 entry point

Status: CPU numerical/test-fixture implementation; no live HBFSim thermal
integration, calibrated ROM, physical HBF validation or closed-loop refresh yet.
The existing top-level build is unchanged. Baseline runtime links no new code.

From this checkout, use an available CMake >=3.16 and C++20 compiler. On the
current host select `/usr/bin/cmake` because PATH contains an unrelated broken
Xilinx copy. This launcher choice is not a source requirement on other hosts.

```sh
cmake -S src/eq3_thermal -B /your/new/build -DCMAKE_BUILD_TYPE=Release
cmake --build /your/new/build --parallel 2
ctest --test-dir /your/new/build --output-on-failure
python3 -B -m unittest discover -s tools -p test_eq3_thermal_config.py -v
python3 tools/eq3_thermal_config.py \
  --topology configs/eq3_thermal/topologies/mixed_direct_8.json \
  --output /your/new/run
/your/new/build/hbfsim_eq3_thermal_cli --mode shadow \
  --model /your/new/run/model.txt --events /your/new/run/events.txt \
  --step-s 0.01 --end-s 0.2
```

Use a new output directory per run. The emitted CSV lists individual nodes and
group hotspot/mean temperatures on the same simulation time. `gpu`, `hbmN` and
`hbfN` groups are modeled sensors; `gddr` is physically GDDR, even when its logical
role is fast memory. These are synthetic pulses, not workload or product heat
predictions. Event source fields describe supplied fixtures; a `refresh` source
does not implement a maintenance scheduler, resource contention or wear.

Four topology JSON files are supplied. Edit counts/profile IDs in a copied
configuration to test 4+4, other stack counts, HBF 8/16 die, or HBM 12/16 die.
Generated data and thermal graphs are separate. Relay targets a base-die node,
does not imply a DRAM read/write, and both HBF paths share named NAND/TSV resources.
The graph declares sharing but does not yet simulate arbitration or validate
GPU-facing port/area budgets. No topology throughput comparison is supported.

`devices.json`, `thermal_fixture.json`, `power_fixture.json`, `reliability.json`
and `control.json` keep separate responsibilities. OCP entries with unknown die
counts fail generation; use explicitly named assumed fixtures until a specific
product die count is selected. Local OCP Table4 maximum heights are verified
as 8/16/16; maxima are not actual device heights. The source ledger preserves
OCP bandwidth disagreement and the user's powered-on 85C/24h condition.
Thermal RC values are arbitrary positive numerical fixtures; geometry/materials
are unavailable and no physical floorplan is asserted. Device capacities with
unverified byte convention remain null.

Only off/read_only/shadow standalone modes are meaningful in P1; active must
fail explicitly. Off/read_only do not construct a solver. Restoring old HBFSim
requires no runtime switch: use the untouched baseline checkout/build. There is
no thermal plugin linked into it, no shared ABI change, no driver modification.

Reference/fast memory simulation and thermal reference/fast are different axes.
3D-ICE/ROM selection and three closed-loop EQ3 scenarios are NOT_IMPLEMENTED in
this P1 entry. Do not replace missing reference or token metrics with fake data.
P2–P5 gates and follow-up scope are in design.md and workspace CODEX_TODO.md.
