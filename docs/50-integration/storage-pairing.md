# CPU storage pairing adapter

`scripts/eval/replay_storage_pair.py` takes a completed read-only collector bundle
and runs the existing native concurrent MQSim binary. It writes a frozen raw
comparison with per-request timing and separate physical/model populations. It
does not open the benchmark payload, collect new physical data, fit a profile,
launch GPU work, register a matrix producer, or export formal result rows.

The implementation follows the two arrival experiments in
`docs/49-eval-audit/hardware-groundtruth-contract.md` and the Phase B adapter work
in that directory's execution plan. Those frozen audit files are unchanged.

## Two explicit modes

| Mode | Required physical source | Native replay | Preserved identity |
| --- | --- | --- | --- |
| `observed_arrivals` | Completed fixed-arrival or closed-loop acquisition | `fixed_arrival_trace` | Exact `actual_submit_ns`, absolute file offset, bytes and read operation. No clock-origin shift. Equal timestamps retain collector issue-record order. |
| `closed_loop_policy` | Completed `closed_loop_qd` acquisition | `closed_loop_qd` | Frozen request order, offsets, bytes, operations and QD. Derived model input has `issue_ns=0` and `consume_deadline=0`; native completion replenishes the next request. |

An observed-arrival replay of a physical closed-loop source sets
`observed_closed_loop_diagnostic=true`. It is not the matrix's native closed-loop
comparison. In policy mode the two arms' actual timestamps can differ, as required
by completion-driven replenishment. Policy order comes from the frozen request
list, not from thread-dependent ordering of actual syscall submissions.

The adapter checks policy QD against the topology emitted by the native binary.
It does not parse or reinterpret profiles in Python. A profile/QD mismatch leaves
a failed receipt and retained replay diagnostics.

`layer=0`, step, sequence and immediate consume deadlines are replay bookkeeping.
They do not represent a decoded model DAG, a live serving process, or a measured
GPU consume event.

## Invocation and output

Run from the experiment checkout with an existing collector bundle and its exact
frozen profile. The output must be a new directory inside that checkout.

```sh
python3 scripts/eval/replay_storage_pair.py run \
  --collection /absolute/path/to/completed-collection \
  --profile /absolute/path/to/frozen-profile \
  --binary /absolute/path/to/build-eval-implementation/hbf_concurrent_trace_timing \
  --out /absolute/path/to/experiment/new-pair \
  --cell-id flash_fidelity-00211 --replicate 1 \
  --mode observed_arrivals

python3 scripts/eval/replay_storage_pair.py validate \
  --out /absolute/path/to/experiment/new-pair
```

The example cell is not a registration or permission to perform storage I/O.
Select the exact physical cell and replicate recorded by the supplied collection.
For policy mode, change only the mode argument when the source is a closed-loop
collection with matching profile QD.

- `arrivals/` contains the existing converter's input snapshots, observations,
  arrival stream, mapping and receipt.
- `bindings/` contains verified split and, for heldout sources, profile-freeze
  snapshots. Original source identities are checked when freezing. Subsequent
  comparison validation uses the frozen copies and does not reopen old paths.
- `profile.json`, `raw.model-input.jsonl` and `raw.request-map.json` bind the exact
  mode-specific native input and original collector IDs.
- `replay/` contains the existing wrapper's native output, exact argv, binary and
  input hashes, source patch, environment, stdout, stderr and replay receipt.
- `raw.pair.json` contains the comparison, both populations, signed relative
  errors and joined request ledger.
- `pair-manifest.json` seals the exact artifact inventory and records
  `VALIDATED_PAIR`, `FAILED` or `INTERRUPTED` in its **validation** field. It is not
  a scheduler status. Failure diagnostics remain in `failure.json` and nested
  tool outputs. No `status.json` or scheduler `DONE` is published by this adapter.

The public Python entry points are `run_pair` and `validate_pair`. The validator
reconstructs mode-specific inputs, verifies native lifecycle accounting, and
recomputes the comparison from frozen raw ledgers. Replacing a summary and
updating its checksum is insufficient to pass. Successful publication occurs
only after semantic validation of the same final byte snapshot used for hashing.

## Statistics and evidence boundary

Both arms use the collector's frozen half-open interval
`[int(warmup_seconds * 1e9), start + int(steady_seconds * 1e9))`.
Each arm independently selects requests whose own completion falls in that
interval. The output retains the selected replay IDs and the complete joined
ledger, including each arm's membership flag.

- Physical latency is syscall completion minus actual syscall submission.
- Model latency is reported completion minus native issue. Raw media completion
  remains separate in the joined ledger.
- P50/P99 are nearest-rank percentiles of each arm's own completion population.
- Throughput is completed bytes divided by the interval, expressed in decimal
  GB/s; IOPS uses completed requests and the same interval.
- An empty population has zero completed bytes/requests and rate, but unavailable
  (`null`) percentiles. Signed relative error is `100 * (model - physical) /
  physical`; unavailable metrics or a zero physical denominator produce `null`.

Drain completions remain in the ledger and conservation checks. They do not
extend the steady interval. These are per-run statistics, not pooled percentiles
or a knee estimate. Fractional host/device QD observations remain in their source
raw artifacts; this adapter does not change the formal metric schema.

Physical syscall latency includes an SSD host stack that the current media
adapter does not reproduce. The comparison does not infer NAND `tR`, fit a host
residual, or claim whole-device fidelity.

`TEST_ONLY` collection input always yields outer `MOCK`, even though the native
replay wrapper describes its modeled output as `PROJECTED`. Fixture output under
canonical `results/runs`, including symlink aliases, is rejected before output
creation. Real collection input yields an outer `PROJECTED` comparison referencing
the physical `MEASURED` syscall scope. All outputs keep
`scientific_validation_passed=false`, `hardware_validated=false`, and
`formal_export_eligible=false`.

Formal export still requires reviewed exact-condition registry entries, semantic
validators, immutable evidence for the matrix's `G3-frozen-device-profile` and
`G4-arrival-replay` gates, and the required independent physical repeats and
heldout validation. Physical and model result rows would need separate run IDs
with an explicit pairing relation. None of those gates is supplied by this tool.

## CPU verification

`scripts/eval/test_replay_storage_pair.py` uses explicitly labeled metadata
fixtures and the existing native CPU binary. It performs no storage payload I/O.
Tests cover both arrival modes, exact timestamps/order/extents, QD and frozen
identity mismatches, heldout validation IDs, independent completion populations,
empty-window metrics, fixture isolation, immutable output, and failure closure.
Red/green evidence is retained under `results/gold/storage-pair/`.

The comparison currently snapshots raw ledgers and frozen bundles in memory.
Large acquisition bundles therefore require correspondingly sized CPU memory;
it does not silently subsample requests or replace raw observations with summaries.
