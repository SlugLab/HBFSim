# Final combined-component interface gap

Status: READ_ONLY_DESIGN_FINDING; representative GPU dependencies incomplete.
Decision owner: GPT-6 Astra. No implementation or GPU expansion has started.

The exact target ledger is `TARGET_LEDGER.json`: 147 deduplicated storage
objects, 13,838,323,712 bytes. It preserves the accepted 99-object 6603 scope
and 48 pending objects. Addresses are deliberately excluded from reusable
configuration; each actual run must observe new addresses and byte intervals.

Current `task/router-li7-host-adapter-v1/source/nv_attach_impl_frida_setup.cpp`
uses process-wide `HBFSIM_QKV_TARGET_KIND=router_li7` to choose active name,
ABI-map/source/staged identities and geometry. `qkv_bound()` stores one bound
original/patched/context/token record. Thus a successful standalone router run
will not by itself establish compatibility with simultaneous Li6 consumers.
This is a host binding/configuration gap, not evidence of a PTX failure.

The preferred bounded extension, subject to representative evidence, is two
explicit immutable exact profiles (Li6 and Li7), with independent bound
identities, selected using verified function identity and the already selected
registered storage. Preserve existing select/end public calls, thread-local
one-call scope, original aggregate→candidate ABI conversion, exact image/name/
context/token checks, fail-closed mismatch behavior, and default-off behavior.
Every profile retains its own source/staged roles, map hash, manifest and
sidecar joins. Do not switch process environment during model execution.

The final choice and concrete diff remain pending until all category
representatives pass. A general kernel matcher, mutable global target switch,
or separate-process results summed as a combined regression would not satisfy
the requested final state. New device memory rewriting or helper algorithm
changes remain outside this host-only proposal and need explicit user approval.

For same-class layer expansion, use exact observed tensor profiles and live
registration intervals, preserving per-layer activation/native/candidate raw
outputs, exact dispatched patched function, thread/request scope and positive
storage-address coverage. Preserve untriggered/native prefill consumers as
uncovered; the ledger does not turn registration into instrumentation.

## Meaning of the full target footprint

The 13,838,323,712-byte total is the deduplicated target storage extent, not a
claim that a two-token request reads every byte. In particular, an expert-weight
storage contains multiple experts and the fixed request selects only its actual
experts. Final evidence must report registered byte intervals separately from
observed/instrumented/admitted/completed access bytes, preserve exact consumer
scope, and leave untriggered/native consumers explicit. Reusing accepted old98
evidence does not create an exhaustive per-byte or every-expert read claim.
