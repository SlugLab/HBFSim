# Causal geometry and byte scope

Status: read-only audit, 2026-09-20. No solver, native backend, causal service, or
runner was changed by this audit.

## Geometry evidence

| Item | Value used by the OCP-oriented profile | Evidence class and exact limit |
|---|---:|---|
| NAND page | 4,096 B | `SPECIFIED`: OCP HBF v0.7.0 §4.1 specifies 4 KiB pages and that reads do not cross a page. |
| Host channels | 16 per stack | `SPECIFIED`: OCP §4.3. |
| Dies | 16 per cube | `SPECIFIED`: OCP Table 3. |
| Banks | 16 per channel | `SPECIFIED` as banks. OCP does not establish a universal bank-to-NAND-plane identity. |
| Backend mapping | 16 channels/stack × 1 MQSim die/channel × 16 planes/die | `SCENARIO_ASSUMPTION`: `campaign_inputs.configuration(..., geometry="ocp4k16bank")` projects the 16 OCP banks onto MQSim planes (`BANK_AS_MQSIM_PLANE_V1`). It is not a product plane specification. |
| Pages per block | 256 | `SCENARIO_ASSUMPTION`: OCP §5.7 leaves R3/pages per NAND block product-defined. The value has a real MQSim allocator consumer, but is not OCP-specified. |
| Logical capacity | 512 GiB/stack | `SPECIFIED` capacity represented by the full-capacity profile. It does not specify physical spare, bad-block reserve, or overprovisioning. |
| Maintenance aged subset | 4 GiB/stack = 4,096 × 1 MiB blocks | `SCENARIO_ASSUMPTION`: 1/128 = 0.78125% of a 512 GiB logical stack. This is an explicit aged subset, not total capacity or a claim of physical block coverage. |
| Maintenance spare pool | 16 blocks/channel, 256 blocks/stack in the aggregate driver | `SCENARIO_ASSUMPTION`: bounded metadata ownership for the aggregate service. It is not observed product spare capacity. |

The executed full-capacity native geometry evidence and its limits are recorded
in `ISOLATED_GEOMETRY_AND_THERMAL_EVIDENCE.md`. In particular, CWDP and the
page-level FTL choose physical locations; a logical ordinal is not a physical
block address.

## Current causal byte semantics

`causal_workload._tensor_groups` derives logical tensor payload regions from the
registered Qwen architecture metadata. It aligns the next region's logical
address to 1 MiB, but those address gaps are not offered as traffic.
`CausalExecutor._stripe_parts` then distributes exact logical bytes in 4 KiB
round-robin units and assigns the single residual tail to one target. The sum of
all child jobs equals the tensor's logical payload exactly for both 64- and
128-target layouts.

The current service job has one `bytes` value for media, base, buffer and fabric
phases. Consequently a partial final page currently consumes only its logical
tail bytes at every phase. There is no 4 KiB NAND-media rounding fact in the
causal receipt. Current output must therefore be labelled
`LOGICAL_PAYLOAD_BYTES_MODELLED_AS_MEDIA_BYTES`; it is not an observation that
physical NAND transferred a partial page.

For a full scan of the registered original Qwen2.5 payloads, rounding each
logical tensor group to a 4 KiB NAND-media page gives:

| Model | Logical payload | Tensor groups | Page-rounded `physical_payload_bytes` proxy | Padding | Relative padding | 64/128 target effect |
|---|---:|---:|---:|---:|---:|---|
| Qwen2.5-7B-Instruct | 15,231,233,024 B | 59 | 15,231,262,720 B | 29,696 B | 1.949678 ppm | Same padding for 64 and 128 targets; the current algorithm has one residual child per partial tensor group. |
| Qwen2.5-72B-Instruct | 145,412,407,296 B | 163 | 145,412,407,296 B | 0 B | 0 | Same for 64 and 128 targets. |

This calculation uses the full logical tensor groups in
`qwen2_5_weight_models.json`. Selected-row embeddings and other sub-tensor
accesses require their own request-level rounding calculation.
`physical_payload_bytes` means only the 4 KiB-aligned payload proxy. Actual
NAND page transfer, OOB/ECC bytes, protocol framing, internal movement, and
wire bytes remain `UNKNOWN`; the calculation is not an observed physical NAND
transaction count and does not establish full-capacity physical allocation.

At 50 pJ/B (the user-confirmed 80 W at 1.6 TB/s read envelope), the 7B tail
padding adds 1.4848 µJ to a 0.7615616512 J full scan. At 500 pJ/B it adds
14.848 µJ to 7.615616512 J. The relative correction remains 1.949678 ppm, so
page-tail rounding is not thermally material for these full scans. A 500 pJ/B
coefficient applied to the entire payload is a 10× energy assumption and is
scientifically material; the page-rounding correction at that coefficient is
not. The 72B full scan has no page-tail correction under this grouping.

## Minimum correct adapter recommendation

Do not simply round the existing job's `bytes` to 4 KiB. That would also charge
padding to base/fabric delivery, inflate useful completion bytes, cache volume,
and dependency accounting. It would change more than NAND-media scope.

The smallest correct default-off extension is:

1. Keep the existing logical job and its identity, link bytes, useful-byte
   completion, cache accounting, and dependency completion unchanged.
2. For each NAND source child with a partial page, generate one parent-linked
   `media_page_padding_read` fact/job of `4096 - logical_tail_bytes` on the same
   explicit stack/channel. It consumes only the NAND media resource and emits
   only `media_read` activity; it consumes no base, buffer, relay, or GPU-link
   bytes and produces no useful completion.
3. Aggregate logical completion only after both the original logical job and
   its padding media work have completed. Record `logical_bytes`,
   `physical_media_bytes`, `padding_bytes`, `page_bytes=4096`, and parent ID in
   receipts. Retry padding must be charged once per actual retry attempt, not
   once per logical consumer.
4. Reject the option when page size, physical channel, or operation scope is
   unknown. HBM jobs remain byte-granular and are not NAND-page rounded.
5. Enable it only in a new explicit profile. Off mode must preserve current
   receipts and timing byte-for-byte.

This requires a narrow operation/resource addition to `CausalTopologyService`
and a parent-completion adapter in `run_causal_point`; the present service
cannot represent media-only padding through an external wrapper because its
single byte count is applied to every phase. It should be implemented only
under a separate reviewed change, with fixed checks for: partial and aligned
pages, 64/128 target conservation, unchanged useful/link bytes, increased media
bytes only, retry deduplication, completion dependency, and off-mode parity.
