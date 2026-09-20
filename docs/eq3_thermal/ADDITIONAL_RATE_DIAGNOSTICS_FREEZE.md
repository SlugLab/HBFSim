# Additional rate diagnostics freeze

Status: `PENDING_DEPENDENCIES_BASE_MATRIX`; no thermal solve has started.

The prepared stage contains nine new feedback-policy points, each with 20 s of
input and 10 s of recovery at 1.536 TB/s offered per HBF stack:

- four topologies with the same total offered bytes placed on the first half of
  each stack's channels;
- four topologies with the same aggregate offered bytes concentrated on
  `hbf0`;
- one mixed-direct point with the same uniform input and the registered
  no-cross-domain-lateral thermal ablation.

The corresponding four uniform feedback points are exact frozen-base
comparators and are not rerun.  The index also registers read-only comparisons
between mixed 4x1.536 and all-HBF 8x0.768 TB/s across three policies (same total
offered demand), and between mixed/all-HBF at 1.536 TB/s per stack (different
total demand and package geometry).  These byte-pressure scenarios do not
represent token throughput or native MQSim throughput.

All nine new points use the isolated shared-HBM endpoint adapter.  The index
locks the entry point, adapter, base runner, service, energy mapping, thermal
client, binary, configs, and model files.  Execution remains blocked on the
base matrix completion receipt.  The new output allocation is 4.5 GiB; with
30 GiB base and 27 GiB sensitivity allocations, the 80 GiB parent budget leaves
18.5 GiB for maintenance and causal work.

Prepared evidence:

- `plans/four-topology-system-v1/rate-diagnostics-v1/RATE_DIAGNOSTICS_INDEX.json`
- `plans/four-topology-system-v1/sensitivity-v2/SENSITIVITY_INDEX.json`

The original `sensitivity-v1` inputs remain preserved and unexecuted.  Version
2 changes only the future runner binding needed for the endpoint policy fix.
