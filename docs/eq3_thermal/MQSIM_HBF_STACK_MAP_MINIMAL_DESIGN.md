# Minimal MQSim HBF stack-map design

Status: `USER_AUTHORIZED_BASIC_CPU_CONSUMER`; implementation is limited to the
additive CPU service described here. It does not authorize production daemon
routing semantics, public ABI changes, an HBM backend, relay/base arbitration,
SRAM modeling, or maintenance.

## Capability boundary

`MqsimOnlineEngine` constructs one MQSim SSD device. The profile supplies the
physical channel count, one chip per channel, dies per chip, and planes per
die. With the required `CWDP` page allocation scheme, MQSim's existing read and
write mapping paths both select `channel = LPA % channel_count`; block and page
allocation, GC, scheduling, command timing, and completion remain owned by
MQSim.

The minimal model treats configured, disjoint groups of those real MQSim
channels as HBF stack partitions. It is reported as
`ACTUAL_MQSIM_CHANNEL_PARTITIONED_HBF_STACKS`. It is not a claim that an MQSim
channel is inherently a package stack, or that MQSim models an HBF base die,
relay link, HBM, GDDR, or package topology.

## Configuration and bijection

The optional JSON configuration is disabled when `--stack-map` is absent. It
must declare:

- schema version 1, physical kind `HBF`, route `direct`, and address layout
  `GLOBAL_PAGE_STRIPE_V1`;
- the same page size, channel count, dies per channel, and allocation scheme as
  the loaded MQSim profile;
- two or more uniquely named stacks, each with the same nonzero number `K` of
  unique channel IDs;
- channel groups that are disjoint and exactly cover all `C` profile channels;
- `declared_dies` for each stack equal to `K * dies_per_channel`.

For external global page `p`, stack count `S`, and `C = S*K`:

```text
s = p mod S
q = floor(p / S)
k = q mod K
r = floor(q / K)
c = configured_channel_group[s][k]
backend_page = r*C + c
```

This is a full-capacity bijection. The inverse uses the explicit channel table:
find `(s,k)` for actual channel `c`, compute `r=floor(backend_page/C)`, then
`q=r*K+k` and `p=q*S+s`. Channel-to-stack identity therefore comes from the
versioned configuration rather than an address guess.

The stripe is persistent placement across same-kind HBF stacks. It never
temporarily assigns an existing page to whichever stack is currently idle and
does not place HBM and HBF pages through one round-robin namespace. Mixed
topology HBM/KV placement remains `UNSUPPORTED_CAPABILITY`; it must come from a
real HBM backend and an explicit persistent data-placement contract.

Enabled service mode accepts only one profile page per request and requires
explicit `stack`, `stack_local_page`, and `route=direct` metadata. It derives
the persistent external global page as `stack_local_page*S + stack_index` and
then applies the bijection above. If a caller also supplies `logical_address`,
it must exactly match that derived page. The requested stack is validation and
does not dynamically override placement. Multi-page requests return unsupported at the consumer boundary
instead of being split and given a new completion lifecycle. All mapped and
unmapped requests must not share one engine instance. When `--stack-map` is
absent, the service neither parses nor requires stack/route metadata and sends
the original address unchanged.

## Files, symbols, and behavior

| File / symbol | Change | Behavior |
|---|---|---|
| `include/hbfsim/eq3_thermal/mqsim_stack_map.hpp` / `MqsimStackMapAdapter` | Header-only validation and address transform | Default Off returns the original request; Enabled returns original/backend page, resolved stack, and expected channel without submitting or owning work |
| `benchmarks/replay/hbf_mqsim_service.cpp` | Optional `--stack-map`, request mapping before the existing gate, submission ledger, native channel verification | Uses the existing engine, gate, horizon, and completion path; no retry, buffering, new event, or resource reservation |
| `tests/integration/test_mqsim_service.py` | Small eight-stack engineering profile and raw assertions | Proves all eight configured stacks receive real MQSim commands on their configured channels, with unique completion and default-Off parity retained |
| `docs/eq3_thermal/INTERFACE_CONTRACT_AND_CONSUMERS.md` | Capability/consumer entry after validation | Separates channel-partition evidence from package topology claims |

The native command consumer records external page, backend page, expected
stack, configured stack recovered from the actual physical channel, and
channel/die/plane. A demand command whose actual channel is outside the
request's expected group is a hard test/service failure. Background commands
with no external request parent retain null request/expected-stack fields and
are annotated only from their actual channel.

## Non-interference, validation, and rollback

Off mode does not construct a map and submits the original address. Enabled
mode copies the request and changes only `logical_address`; request ID,
arrival, byte count, operation, generation, gate decision, and backend
completion stay unchanged. MQSim still owns FTL mapping, physical allocation,
GC, TSU/PHY arbitration, and command timing.

Fixed validation must cover malformed/overlapping/incomplete groups, profile
mismatch, requested-stack mismatch, multi-page rejection, all eight stacks,
native actual-channel verification, unique completions, and existing gate and
observer Off/On regressions. The small eight-stack profile uses one channel and
one die per stack and is labeled `ENGINEERING_FIXTURE`; it is not evidence for
the research 8/16-die package geometry.

Rollback removes the optional service argument, the header, and the added
integration test. No engine, MQSim patch, FTL, TSU, dispatcher, protocol ABI,
or completion implementation is changed.

## Explicitly unavailable

The current `HbmCache` manages VMM frame residency and eviction; it is not an
HBM command, timing, refresh, energy, or per-stack backend. Relay/DASH two-hop
bus occupancy and base SRAM dual-bank arbitration are also absent from MQSim.
Those capabilities remain `UNAVAILABLE` and must not be represented by this
address adapter or by fixed-duration `CpuService` resources.
