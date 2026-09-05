# Bounded MQSim clock for causal replay

The base `run_next_completion` advances through media events until a completion
record exists. Its returned reported deadline can be later than the raw media
clock because of the configured bandwidth bound. It cannot by itself coordinate
a compute event that must issue new requests before that next completion.

Add `run_next_completion_until(deadline_ns)` as a separate opt-in API. It must
return one completion only when its reported deadline is reached, or return no
completion with the media clock exactly at the caller's horizon. Never advance
past that horizon. Original `run_next_completion` and all service/address/QD
semantics remain unchanged. Reject a horizon before the current clock.

Implement clock markers with the existing MQSim event interface, without a
second engine or edits to third-party MQSim. A marker has no request, byte,
admission, bandwidth or service effect. The patched engine processes a complete
timestamp bucket per step, so same-time events retain its existing ordering.
Cancel unused markers; keep the optional marker target alive with the engine.
MQSim ignores cancelled callbacks but retains their timestamp buckets. After
external-clock opt-in, an empty legacy poll is therefore an explicit no-op;
only the horizon API advances an idle external clock. Legacy-only callers are
unchanged, and mixed callers with pending real work still use the original pump.

Gold controls: fixed-arrival completion parity against the legacy API; compute
horizon reached before a media completion, followed by a new request submitted
at that exact time; bandwidth-adjusted completion never returned early; count,
byte and optional observation conservation; idle and backward-clock behavior.
This API supplies clock coordination only. It is not a completed compute DAG,
prefetch implementation, physical SSD calibration or GPU correctness proof.
