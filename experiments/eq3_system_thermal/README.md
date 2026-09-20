# EQ3 causal workload and conditional reliability modules

This directory is default-off and independent of the old campaigns and native
MQSim scheduler.  It introduces no thread, build dependency or 20 ms clock.

## Online causal API

```python
trace = build_architecture_trace(config)
executor = CausalExecutor(trace, placement_config, placement_provider=None)
jobs = executor.poll(now_ns)
# submit each job to the actual topology service; do not complete it locally
executor.complete(job_id, actual_completion_ns, completed_bytes)
next_exact_compute_event = executor.next_internal_event_ns()
result = executor.result(now_ns)
```

`poll()` returns explicit `stack`, `channel`, `route`, byte count, identity and
logical issue time.  The caller may advance at the minimum of a thermal window,
an external completion and `next_internal_event_ns()`.  A dependency unlocks
only after `complete()` consumes a byte-complete external result.  Thus thermal
20 ms integration windows and causal compute time remain separate.  A token is
complete only when its final dependency and exact-ns output compute complete.

The compact graph uses official 7B/72B architecture and payload metadata.  The
dependency order, compute times, batching, prefetch policy, cache and migration
are explicit scenarios.  `batch_size` coalesces one tensor read across the
batch interval while retaining every token identity.  The executor has a real
byte-capacity LRU, distinguishes issue from consumption, and can emit basic
migration jobs whose completion changes later placement.  Its label is always
`SYNTHETIC_ARCHITECTURE_DEPENDENCY_FROM_OFFICIAL_METADATA`.

## Reliability API

`ReliabilityLedger` consumes contiguous block temperature intervals plus actual
program, erase and refresh terminal facts.  It returns conditional equivalent
age and distinct successful program/erase counts.  Only a successful refresh
commit clears age.  Retry/ECC overhead is accepted only as a separately named
scenario fact, never inferred from age or temperature.  See
`PARAMETER_SOURCES.md` for all evidence boundaries.
