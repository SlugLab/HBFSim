# Read-only storage acquisition contract

`collect_storage.py` requires a frozen split view, explicit dedicated/exclusive
storage authorization and bounded aligned `read_region`. Standalone execution
acquires the canonical project lock. A scheduler worker can borrow its parent's
lock only after validating durable RUNNING producer/matrix/condition/replicate/
task/storage bindings, exact boot/PID/start identities and actual parent FLOCK
ownership. Environment variables alone cannot authorize I/O.

Payload access uses `O_RDONLY|O_DIRECT|O_NOFOLLOW` and aligned anonymous buffers.
Fixed requested arrivals remain separate from actual submission and lag;
closed-loop QD refills only after observed completion. Formal warmup, steady
window and completion minima are enforced; pilots cannot claim those gates.

Workers start with payload-free tasks before the acquisition epoch. This closes
the executor edge where submission enqueues a task and then fails during thread
startup, leaving an untracked read. Periodic/end identity and foreign-I/O checks
remain active. Interruption diagnostics and outstanding IDs are durable before
existing reads drain; a blocked kernel syscall is not claimed cancellable.
Outer supervision retains ownership, without signaling unrelated processes.

Test injections remain TEST_ONLY and cannot write under formal `results/runs`,
including aliases. All outputs stay in this checkout. Raw acquisition does not
establish scientific validation or scheduler DONE. This host's selected path is
on shared root storage, so physical payload remains blocked. Only CPU fixtures
and anonymous-memory descriptors were used. The 34-test suite and independent
reviews passed; evidence is in `results/gold/storage-collector/`.

`storage_arrivals.py` converts a completed acquisition ledger into identical
MQSim arrivals. It retains actual syscall submission times and absolute file
offsets, with a positive replay-ID mapping. Even a physical closed-loop source
uses fixed observed arrivals in this paired replay; recomputing arrivals from
model completions would change the comparison. Requested times and physical
completion/latency remain in a separate map. Frozen ledger bytes, source modes
and provenance are validated before publishing VALIDATED_ARRIVALS; this does
not calibrate or validate a media profile.
