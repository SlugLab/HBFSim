# Persistent coupled thermal service

This directory is an experiment-only consumer of the locked EQ3 2 mm thermal
network. It does not change the default HBFSim/MQSim targets or the public
thermal ABI. The service reuses the campaign runner's sparse implicit-Euler
equation, temperature translation, Eigen `SimplicialLDLT`/AMD backend, and
endpoint boundary-energy accounting. It factors the fixed matrix once and
keeps temperature and energy state in one process.

## Locked production input

The current `ENGINEERING_MODEL_LOCK` candidate is the complete 2 mm network
under `eq3_thermal/generated/campaign-RC2MM-train`:

- `model.txt`: `ecc7d24ad7a107466eaa7e6cbd37add087a57677aa32283e8850fa10c6c62559`
- `rc_grid.json`: `b8e611d0a9c0db325efdade111fb83c89135434197f55a3f3924f9285cce5a23`
- `rc_sensors.json`: `fe700161c57d6d66da4eb0dfadf25d72732a24a6fe9b5b0d79c770c934dcd73b`
- 64,512 thermal nodes, 255 physical/package entities after excluding
  `__background__`, and 275 registered sensors.

These hashes are runtime requirements, not labels: the service computes and
checks each SHA-256 before factorization. It also checks grid/model node order,
cell capacity, component ownership, and every sensor index/weight. Different
explicit inputs can be used by fixed small tests, but a campaign must bind its
own lock receipt and must not silently find a model by path.

## Protocol

Input is one command per line:

```text
ENERGY START_NS END_NS COMPONENT_ID ENERGY_J
ADVANCE TARGET_NS
QUIT
```

`ENERGY` accepts one already-aggregated component energy for a fixed thermal
window. Multiple components may use the same interval. A duplicate
component/interval is rejected to prevent double counting. Submitted start,
end, and `ADVANCE` target times must align to the configured 20 ms step;
sub-step physical events are integrated by the coordinator before submission.
Committed time cannot be changed. A trailing partial step is explicitly
unsupported in v1.

Every response is a single JSON line. `ADVANCE` returns 255 entity mean/hotspot
temperatures, all 275 registered sensor values, window and cumulative energy
balances, and independently measured advance and observation timing. The
timing block also reports serialization-probe time and the measured stdout
write time of the preceding response, because a response cannot truthfully
contain its own completed write duration.

On a domain or numerical failure, the failed trial is not committed. The error
reports last-valid and trial times/extrema, offending nodes when calculable,
completed-interval energy, and `UNKNOWN_NOT_INTEGRATED` for failed-step energy.
There is no clamping or retry. `QUIT` rejects unapplied accepted energy.

## Isolated build and fixed checks

Configure this directory into its own build directory and pass the explicitly
reviewed thermal archive. The target never searches default build outputs:

```text
cmake -S experiments/eq3_maintenance/thermal -B <isolated-build> \
  -DEQ3_THERMAL_ARCHIVE=<matching-build>/libhbfsim_eq3_thermal.a
cmake --build <isolated-build> -j2
python3 experiments/eq3_maintenance/thermal/tests/test_thermal_service.py \
  --binary <isolated-build>/eq3_maintenance_thermal_service
```

The full-model paired test is deliberately separate because it factors the
64,512-node model. It feeds the same one-step node energy to the existing
campaign runner and the persistent service, then compares all 275 sensors and
both energy receipts:

```text
python3 experiments/eq3_maintenance/thermal/tests/pair_with_campaign_runner.py \
  --service <isolated-build>/eq3_maintenance_thermal_service \
  --campaign-runner <reviewed-build>/eq3_campaign_rc_runner \
  --runner-source tools/eq3_campaign_rc_runner.cpp \
  --model <locked>/model.txt --grid <locked>/rc_grid.json \
  --sensors <locked>/rc_sensors.json --output <new-receipt>.json
```

Run the full check only through the campaign's coordinated CPU/resource entry.
