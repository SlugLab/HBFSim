# Startup write fixed diagnostic

This default-disconnected executable reuses the existing isolated
`MqsimOnlineEngine` only. It performs real foreground page programs, drains
them, reads the same mappings, and optionally submits page maintenance. It does
not change or call the campaign JSON service or closed-loop coordinator.

The result is `BACKEND_FIXED_TRACE_DIAGNOSTIC`. Native placement and command
facts are real simulator outputs; model payload bytes, host-ingress energy and
closed-loop temperature feedback are unavailable. A model metadata extent may
select addresses, but no tensor shard is loaded.

Build against an already validated isolated backend build:

```text
cmake -S experiments/eq3_maintenance/startup -B <new-build-dir> \
  -DEQ3_BACKEND_BUILD=<existing-isolated-backend-build>
cmake --build <new-build-dir> --parallel 1
```

The output directory is create-only. Use a frozen profile and stack map, bind a
source label and SHA-256 in the outer preflight, and retain the emitted CSV and
JSON files as raw evidence.

`--outstanding-window N` is a rolling window: every returned completion
immediately permits one refill until all writes or reads have been issued.
`--verify-pages N` defaults to all loaded pages. A smaller value chooses an
evenly stratified read sample containing the first and last loaded page (one
sample selects the last page); the receipt reports the exact coverage. Sampling
reads never changes the number of actual startup programs.
