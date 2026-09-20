# OCP HBF page, capacity, block, and plane alignment

Status: **read-only feasibility audit; no campaign input or runtime source was
changed by this audit**.  The currently paused campaign remains historical
evidence under its original 16 KiB engineering profile.  This document defines
an executable 4 KiB profile for a new experiment version; it does not relabel
old runs.

## Evidence and limits

The primary source is the registered local copy of *High Bandwidth Flash
(HBF), High-Level Base Die Specification*, version 0.7.0, 3 August 2026:

- file: `docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf`
- SHA-256: `307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3`
- evidence class: `DOC_DERIVED`

The relevant requirements are:

| Item | OCP v0.7 evidence | Classification |
|---|---|---|
| NAND page | Section 4.1, p.15 specifies a 4 KiB NAND page. Reads are 64 B through 4 KiB in 64 B multiples and may not cross a 4 KiB NAND-page boundary; writes use a 4 KiB burst at a 4 KiB-aligned address. | `SPECIFIED` |
| Stack/cube | Section 4.1 and Table 3, p.16 specify 16 NAND dies per cube, 16 banks per channel, a 4096 B page, and 512 GiB total cube size. Section 4.3 specifies 16 independent host channels per cube. | `SPECIFIED` |
| Read command granularity | Section 5.3.1, p.56 permits a complete 4 KiB read as 64 separate 64 B commands with different AXI IDs or a supported burst. It also says same-bank sense requests are strictly ordered and that each bank has two page-cache buffers. | `SPECIFIED`, but the present MQSim adapter models a page request rather than the AXI beat protocol |
| Write/block behavior | Section 5.4.1, p.58 says the Core-die write granularity is 4 KiB and the NAND block size is defined by the product specification. | 4 KiB is `SPECIFIED`; pages per block are `UNKNOWN_PRODUCT` |
| Address fields | Section 5.7, p.62 defines R1 as dies, R2 as banks per die, R3 as pages in a bank NAND block, and R4 as 64 B units per NAND page. It does not publish a numeric R3 value for this product. | R3/pages-per-block is `UNKNOWN_PRODUCT` |
| Bank versus plane | Section 11.1, p.112 uses the phrase “banks or planes in die” and describes maximum parallelism in terms of planes, but does not define every OCP bank as exactly one NAND plane. | Direct bank=plane identity is **not specified** |

Section 4.6's worked address example uses 16 banks per die and four dies.  It is
an address-mapping example, and must not override Table 3's 16-die cube or be
treated as a unique physical organization.

## Existing implementation consumers

The relevant profile fields are active backend geometry, not descriptive
metadata.

| Interface/profile field | Actual producer or consumer | Observed behavior |
|---|---|---|
| `page_bytes` | `src/profile/profile.cpp::validate_profile` and `calculate_blocks_per_plane` | Accepts power-of-two values at least 512 B. 4096 B is already legal. Capacity must contain an integral number of complete blocks per plane. |
| one-page stack request | `include/hbfsim/eq3_thermal/mqsim_stack_map.hpp::MqsimStackMapAdapter::map` and `map_stack_page` | When stack mapping is enabled, a request must be exactly one aligned `profile.page_bytes` page. A 16 KiB request under a 4 KiB profile is rejected at this boundary. |
| stack channel/die declaration | `MqsimStackMapAdapter` constructor | Each stack group must contain `channels / stack_count` channels and must declare `channels_per_stack * dies_per_channel` dies. Thus 16 channels per stack with one die per channel maps cleanly to 16 declared thermal dies. |
| `dies_per_channel`, `planes_per_die`, `pages_per_block`, derived blocks/plane, `page_bytes` | `experiments/eq3_maintenance/backend/src/mqsim_online_maintenance.cpp::configure_mqsim` | Writes the fields to MQSim's `Flash_Parameter_Set`. `Chip_No_Per_Channel` remains one. |
| flash geometry | `third_party/mqsim/src/exec/SSD_Device.cpp` | Passes the geometry to the Flash chip/PHY, FTL, TSU, block manager, page-level address mapping, cache manager, and GC/wear-leveling units. |
| blocks and pages | `third_party/mqsim/src/ssd/Flash_Block_Manager_Base.cpp` and page-level address mapping/GC units | Allocates per-plane bookkeeping; pages per block and blocks per plane change free-page pools, mapping, allocation, and GC/erase behavior. |
| native identities | `experiments/eq3_maintenance/backend/service/hbf_mqsim_maintenance_service.cpp` | Exposes native command, transaction, external-request, byte, channel, die, plane, block, and page facts. Maintenance work is also one profile page. |
| thermal die placement | `experiments/eq3_maintenance/energy_ledger.py::ActivityEnergyLedger._placement` | Maps a channel group entry to a thermal-die offset `channel_index * dies_per_channel`, then adds the native die. With 16 channels/stack and one die/channel, all `die0..die15` remain distinct thermal entities. |

There is therefore no need to rewrite backend scheduling to use a 4 KiB page,
16 channels per stack, or a parameterized plane/block geometry.  The geometry
already reaches the real MQSim allocator and arbitration structures.  This is
configuration-only at the backend boundary, followed by experiment-local
request generation and metadata adaptation.

## Recommended executable profile

For a new, separately identified campaign profile, use:

| Field | Value | Evidence / meaning |
|---|---:|---|
| `page_bytes` | 4096 | OCP v0.7 normative page size (`SPECIFIED`). |
| channels per HBF stack | 16 | OCP v0.7 host-channel count (`SPECIFIED`). Total `channels = 16 * hbf_stack_count`. |
| `dies_per_channel` | 1 | Engineering projection that makes the existing stack mapper expose all 16 specified dies exactly once. It is not a claim that OCP mandates one die per host channel. |
| `planes_per_die` | 16 | `BANK_AS_MQSIM_PLANE_V1`, an explicit `SCENARIO_ASSUMPTION` that projects the specified 16 banks/channel onto MQSim plane resources. It is an executable first model, not a standards claim or a unique product organization. |
| `pages_per_block` | 256 | Retained `SCENARIO_ASSUMPTION`. OCP explicitly leaves NAND block size/product R3 unspecified. At 4 KiB/page this makes a simulated block 1 MiB. |
| `declared_dies` per stack | 16 | Required by the existing mapper for 16 channels/stack and one die/channel, and agrees with the OCP cube die count. |

All existing latency, bandwidth, queue, allocation-policy, GC, and energy
parameters should remain separately sourced and unchanged by this alignment.
The OCP page and capacity statements do not calibrate them.

This profile materially increases modeled arbitration resources. Four HBF
stacks produce 64 MQSim channels and 1024 MQSim planes; eight HBF stacks
produce 128 channels and 2048 planes. No new scheduler code is needed, but a
fixed geometry/identity test and a minimum resource-measured pilot are required
before interpreting performance. In particular, the 16-plane projection can
increase parallelism relative to the prior one-plane fixture.

## Request alignment

The simplest OCP-aligned workload emits native 4 KiB requests directly. This
uses the existing one-page stack-map contract without changing default backend
scheduling.

If an analysis must retain a logical 16 KiB host transaction, the experiment
adapter must create four aligned 4 KiB child requests before submission:

```text
parent global byte address A, where A % 16384 == 0
child j address = A + 4096*j, bytes = 4096, j in [0,3]
```

Each child is a real MQSim request with its own backend request, transaction,
and NAND command identities. The four children retain the parent's stable ID,
child index, and parent byte count in experiment metadata. The parent completes
at `max(child completion time)` only in derived workload accounting. The
backend does not provide atomic all-four admission or a single native 16 KiB
command, so those properties must not be claimed.

The current coordinator's request-metadata allowlist does not include parent
and child fields. A thin experiment-local metadata addition is needed if the
16 KiB bundle is retained; otherwise those fields are discarded before the
request ledger is written. Direct 4 KiB generation avoids this compatibility
layer and is the recommended first profile.

The stack-local ordinal must be expressed in 4 KiB pages. When converting an
old 16 KiB page ordinal `p`, use `4*p+j` for child `j`. The current mapper then
stripes those four page ordinals through the configured channels while keeping
the requested stack identity. Do not apply modulo reduction to a small working
set and describe it as a full-capacity address.

Minimum fixed assertions for either path are: page alignment; exactly 4096 B
per submitted request; expected stack; physical channel/die/plane in declared
ranges; one unique request and transaction identity per child; distinct native
read-command identities where the backend issues four reads; exactly one
completion per child; and 16384 B total for a four-child logical bundle.

## Product capacity and finite active namespace

The OCP product capacity is:

```text
512 GiB/stack = 549,755,813,888 B
              = 134,217,728 addressable-equivalent 4 KiB pages/stack
average attribution over 16 dies = 8,388,608 pages/die
```

The per-die value is capacity arithmetic only. It is not an observed physical
page count: factory bad blocks, spare capacity, overprovisioning, and the
physical plane/block organization are unknown.

Under `BANK_AS_MQSIM_PLANE_V1` and the 256-page/block assumption, a full
512 GiB profile happens to produce 2048 simulated blocks/plane:

```text
134,217,728 / (16 channels * 1 die/channel * 16 planes/die * 256 pages/block)
= 2048 blocks/plane
```

This result is conditional on both engineering assumptions; it is not an OCP
block-count specification.

The paused pilot's finite profile contains 145,492,017,152 B across four stacks
(135.5 GiB total, 33.875 GiB/stack). At 4 KiB/page the same byte extent would be
8,880,128 pages/stack, or 555,008 pages/die by even attribution. It is only
6.6162109375% of the specified 512 GiB per-stack capacity. More importantly,
8,880,128 pages are not divisible by the proposed complete-plane block quantum:

```text
16 channels * 1 die/channel * 16 planes/die * 256 pages/block
= 65,536 pages/stack per blocks/plane increment
```

For a valid finite MQSim geometry near the existing active extent, use
8,912,896 pages = 34 GiB per stack, yielding 136 blocks/plane. For four stacks
this is 136 GiB total. This finite namespace is a deliberate scenario and must
be recorded separately from the 512 GiB product identity. It adds 128 MiB per
stack relative to the paused profile and must not be described as byte-identical
to it.

In general, round the required per-stack active bytes upward to a multiple of
`4096 * 65,536` bytes for this exact geometry. The full 512 GiB capacity can be
kept in the product registry while the smaller, integral namespace is used for
bounded simulation.

## Maintenance and comparability effects

Existing maintenance submission consumes one `profile.page_bytes` page. A
change from 16 KiB to 4 KiB therefore changes the byte coverage of an unchanged
maintenance-job count: 64 jobs cover 256 KiB rather than 1 MiB. A new campaign
must define maintenance intent in bytes or explicitly use four times as many
page operations when byte-equivalent coverage is required. Old and new results
are not maintenance-volume comparable without this normalization.

Changing from 16 KiB pages with 256 pages/block to 4 KiB pages with 256
pages/block also changes the modeled erase/GC block from 4 MiB to 1 MiB. This is
a real behavior change in MQSim. Since OCP leaves pages per block unknown, any
result sensitive to GC, erase, or block allocation remains
`CONDITIONAL_SIMULATED` under the 256-page assumption.

## Readiness decision

Classification: **SUPPORTED_WITH_EXPERIMENT_PROFILE**.

- A 4096 B page, 16 channels per stack, one die per channel, 16 declared dies,
  parameterized planes, and parameterized pages/block are all consumed by the
  existing backend. No scheduling or public-ABI refactor is required.
- Native 4 KiB request generation is the minimum path. A logical 16 KiB bundle
  needs only an experiment-local four-child adapter and metadata fields; it
  does not become one atomic backend command.
- `BANK_AS_MQSIM_PLANE_V1` and `pages_per_block=256` must remain visible
  scenario assumptions. Bank/plane equivalence, physical blocks/plane, spare
  capacity, overprovisioning, and product bad-block counts remain unknown.
- Results from the new geometry require a new profile/scope identity and cannot
  overwrite or retroactively relabel the paused 16 KiB evidence.

Before a new pilot, validate profile integrality; stack/channel/die coverage;
four-page identities if bundling is used; command/completion uniqueness; energy
placement across all 16 thermal dies; maintenance byte normalization; and peak
RAM/runtime for the enlarged channel/plane count. These are bounded fixed or
minimum-pilot checks, not a request to restart the paused campaign.

## Bounded backend verification

`GEOMETRY4K-FULLCAP01` subsequently exercised the existing isolated MQSim
backend with four complete 512 GiB logical stacks and no thermal solve. The
frozen binary hash was
`c64610b0f281397975e649b32f44161b00240f12d6a49b9b4d4776b1f554c257`.

- The service instantiated 64 channels and reported 1024 parallel units.
- 1024 unique native 4 KiB reads completed once each and covered all
  4 × 16 channel-derived thermal-die identities × 16 projected planes.
- Four one-page maintenance operations retained real source/destination
  channel and plane addresses and completed read, destination program, mapping
  commit, old-source retirement, and final completion.
- The final receipt reported no pending foreground or maintenance work.
- Execution took 12.31 s and 21,511,976 KiB maximum RSS under a 48 GiB address
  limit, with no swap. No thermal, fabric, HBM, GPU-link, or GDDR work ran.

The point, raw protocol transcript, native facts, resource record, and artifact
hashes are under
`eq3_thermal/plans/isolated-maintenance-campaign-v1/points/GEOMETRY4K-FULLCAP01`.
This verifies actual consumption of the configured geometry. It does not raise
the bank-to-plane or 256-page/block assumptions above `SCENARIO_ASSUMPTION`.
