# A2 ideal-independent maintenance replay

This optional experiment wrapper is a fixed-intent counterfactual supplement
for taskbook section 8.3. It is not actual shared-MQSim maintenance and does not
replace the endogenous-trigger A2 arm.

`build_ideal_maintenance_bundle.py` binds one completed source point's profile,
stack map, foreground input, maintenance intents, backend state facts, native
read/program phases, and terminal completions. `ideal_maintenance_replay.py`
proxies one real foreground MQSim service, intercepts exact matching maintenance
calls, and releases the frozen facts on their original simulation timestamps.
No maintenance request reaches the proxied engine and replay commits never
modify its mapping.

The wrapper requires identical input hashes, rejects HBF writes, requires every
maintenance call and submission time to match the frozen source, and fails
closed if the counterfactual control trajectory makes fixed replay incompatible.
Command and transaction identities use `A2R-C<source>` and `A2R-T<source>`;
the source identities remain explicit fields. Counterfactual source and commit
versions are `UNKNOWN_REPLAY`; baseline versions remain provenance only. Age is
reset in the virtual experiment ledger only when the source completion observed
a real mapping commit.

The proxied service receipt continues to report zero actual backend maintenance.
A separate nested receipt reports replay issue/completion conservation and the
fact that the current MQSim mapping was not changed.

## Maintenance energy source-label repair proposal

The service emits `maintenance_request_id`, while
`ActivityEnergyLedger._source` currently checks `maintenance_id`. Consequently,
the PILOT03 maintenance energy is present in the component/window totals but is
labelled `BACKEND_BACKGROUND`. This changes evidence attribution, not energy or
temperature.

The proposed later patch, after the frozen main run, is limited to `_source`:

```python
maintenance = tr.get("maintenance_request_id")
if maintenance in (None, 0, "UNKNOWN"):
    maintenance = tr.get("maintenance_id")
if maintenance not in (None, 0, "UNKNOWN"):
    return "HBF_MAINTENANCE"
```

The fixed reproduction is `repro_maintenance_energy_source_label.py`. Historical
raw rows remain unchanged; they can be derived-reclassified by native maintenance
identity without rerunning thermal integration because the total component
energy is unchanged.

PILOT03 also retained an old derived `cleanup_failed=true` inconsistency for
`COMMITTED_RECLAIM_DEFERRED`. The bundle builder uses the backend completion
object (`mapping_committed=true`, `source_retired=true`, `erase_completed=false`)
and does not copy that obsolete top-level derived flag.
