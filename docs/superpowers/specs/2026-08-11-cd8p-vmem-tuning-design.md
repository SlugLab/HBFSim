# CD8P vmem End-to-End Tuning Design

Date: 2026-08-11 UTC

## Goal

Tune HBFSim's fast and hybrid timing paths to reproduce the measured
end-to-end behavior of the software vmem path in
`/home/victoryang00/nvme-mem2nvm`, including page faults, 4 KiB NVMe reads,
cache insertion, and the userspace scan cost represented by the source
benchmark.

The result is a named, provenance-bearing `cd8p-vmem-p50` profile, a
deterministic tuning tool, a comparison report, and CPU plus real-GPU proof
that the empirical model follows the source curve.

## Source Evidence and Proof Boundary

The immutable tuning source is:

```text
/home/victoryang00/nvme-mem2nvm/docs/superpowers/results/2026-07-30-vmem-sw-performance.csv
sha256 4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f
```

It contains 11 samples for each of six SSD cold-fault read sizes and 11
samples for the bounded 4 KiB fsync path. The relevant measured P50 cumulative
read latencies are:

| Transfer | Pages | P50 latency |
|---:|---:|---:|
| 4 KiB | 1 | 11.133 us |
| 16 KiB | 4 | 41.495 us |
| 64 KiB | 16 | 168.606 us |
| 256 KiB | 64 | 2,824.351 us |
| 1 MiB | 256 | 10,767.793 us |
| 2 MiB | 512 | 20,254.374 us |

The bounded 4 KiB fsync P50 is 408.305 us. P95 values are retained as a
validation envelope, not converted into per-page service times, because each
transfer size has an independent sample distribution.

These numbers characterize the complete single-host, single-thread vmem
cold path. They do not isolate raw NAND latency, hardware CXL latency, or a
multi-queue saturation limit. The tuned profile must therefore be named and
documented as an end-to-end vmem model rather than a CD8P media-only model.

The current live `/dev/vmem0` has a full 4 GiB dirty cache. This work consumes
the committed CSV only. It must not run a new vmem workload, flush the live
device, unload its module, or access the raw namespace.

## Rejected Simplifications

A constant 11.133 us per 4 KiB page matches the first point but underestimates
the measured 256 KiB, 1 MiB, and 2 MiB paths by approximately 75%, 74%, and
72%. A single latency-plus-bandwidth line also erases the measured regime
change between 64 KiB and 256 KiB. Neither is an acceptable representation of
the requested complete vmem path.

Inferring physical NAND geometry from this CSV is also rejected. Page-fault,
driver, cache, CPU scan, and device costs are not separately identifiable in
the measurements.

## Profile Schema

The existing profile gains an optional `empirical_vmem` object. Profiles that
omit it retain their current behavior and ABI values.

```json
{
  "empirical_vmem": {
    "source_kind": "nvme-mem2nvm-vmem-sw-cold-fault",
    "source_sha256": "4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f",
    "source_capacity_bytes": 1920383410176,
    "quantile": "p50",
    "sample_count": 11,
    "read_curve": [
      {"pages": 1, "cumulative_ns": 11133, "p95_ns": 38238},
      {"pages": 4, "cumulative_ns": 41495, "p95_ns": 43033},
      {"pages": 16, "cumulative_ns": 168606, "p95_ns": 1247765},
      {"pages": 64, "cumulative_ns": 2824351, "p95_ns": 3860958},
      {"pages": 256, "cumulative_ns": 10767793, "p95_ns": 11968167},
      {"pages": 512, "cumulative_ns": 20254374, "p95_ns": 22163673}
    ],
    "program_p50_ns": 408305,
    "program_p95_ns": 596336
  }
}
```

Validation requires exactly six strictly increasing page breakpoints, strictly
increasing P50 cumulative latency, P95 greater than or equal to P50 at every
point, `page_bytes == 4096`, a 64-character lowercase hexadecimal SHA256, and
positive program latency. The final breakpoint is limited to 1023 pages so it
fits the device burst-state representation.

The named profile uses the source 4 KiB page size and 4 GiB cache. It retains
the existing nominal synthetic MQSim topology. Its effective capacity is the
largest whole nominal-geometry unit below the physical namespace capacity:
1,919,850,381,312 bytes. The 533,028,864-byte reduction is 0.0278% and is
reported explicitly; the exact 1,920,383,410,176-byte source capacity remains
in calibration metadata.

The required scalar fields are fixed rather than inherited implicitly:

```text
read_latency_ns                 11133
program_latency_ns             408305
queue_depth                    1
aggregate_bandwidth_bytes_per_s 103540697
hbm_cache_bytes                4294967296
time_scale                     1
timing_tolerance_ns            27105
```

The aggregate bandwidth is the 2 MiB P50 effective bandwidth in bytes per
second, and the tolerance is the 4 KiB P95-minus-P50 read interval. The
empirical fast path uses the cumulative curve instead of adding these scalar
latency and bandwidth fields. They remain meaningful for reporting, legacy
consumers, and the unchanged reference path.

## Tuning Tool

`scripts/tune_vmem_profile.py` accepts the source CSV, a base profile, a named
output profile, and a report path. It performs these operations without
opening any device:

1. Verify the source SHA256 when an expected digest is supplied.
2. Recognize the source's RAM hot/cold, SSD cache-hot, cached-write, cold-read,
   and fsync metric names; ignore the known non-target metrics and reject any
   unknown name, missing target row, duplicate target key, non-finite value,
   or non-positive value.
3. Require 11 distinct sample numbers for every required metric and size.
4. Compute nearest-rank P50 and P95 values from the raw samples.
5. Check cumulative monotonicity and derive the aligned effective capacity.
6. Emit the named profile and a comparison report atomically.

The report compares three deterministic predictions at all six source sizes:

- the current nominal scalar equation projected onto the transfer size;
- a constant 4 KiB P50 page model;
- the empirical burst curve.

It records absolute and relative error, source/profile digests, capacity
alignment, sample counts, and the exact command. The empirical curve must
reproduce every P50 breakpoint exactly; this is a calibration check, not an
independent accuracy claim.

## Runtime Model

The host/device control ABI advances to version 4. The shared header carries
the six page breakpoints, six P50 cumulative nanosecond values, a curve-enabled
flag, and one atomic packed burst state. Non-empirical profiles zero these
fields and follow the existing fast/hybrid path bit-for-bit.

The burst state uses one 64-bit word:

```text
bits 63..11  previous global media page plus one
bit  10      previous operation (0 read, 1 write)
bits 9..0    consecutive run length, saturated at 1023
```

Zero is the uninitialized state. A compare-exchange loop serializes state
updates. A request extends the run only when its operation matches and its
global media page immediately follows the previous page. Random, repeated,
reverse, cross-operation, or interrupted access resets the run to one. Global
media page numbers are unique because HBFSim assigns non-overlapping file
offsets to registered ranges.

For a read run of length `n`, the helper linearly interpolates cumulative
latency between adjacent breakpoints using integer ceiling division. Beyond
512 pages it extrapolates using the final segment slope. The service injected
for the current page is:

```text
cumulative(n) - cumulative(n - 1)
```

The empirical path serializes this service on the existing fast channel tail,
so an isolated sequential burst reaches the measured cumulative latency at
each breakpoint. A write injects the 4 KiB fsync P50 and resets or extends only
a write run; no multi-page write scaling is claimed.

`fast` uses the empirical curve for every modeled request. `hybrid` uses it for
unsampled requests and preserves the existing detailed MQSim route for sampled
requests. `reference` remains the synthetic MQSim model. Exact curve
validation therefore uses `fast`; hybrid results report their sampled count
instead of claiming exact empirical replay.

## Failure Policy

Malformed empirical metadata fails profile loading. A curve-enabled control
block with an invalid count, non-monotonic points, an unrepresentable page
index, or inconsistent ABI fails closed with `Unsupported`; it never falls
back silently to nominal timing. Integer addition, interpolation, packing, and
time scaling use checked or saturating arithmetic.

The tuner writes temporary files in the destination directory and renames
them only after validation, so a failure cannot leave a partially updated
profile or report.

## Verification

Implementation follows test-driven development:

- Python tests first cover CSV validation, nearest-rank quantiles, SHA
  mismatch, sample completeness, capacity alignment, atomic output, and the
  comparison report.
- C++ profile tests first cover optional-field compatibility and every invalid
  curve condition.
- Host/device helper tests first cover interpolation, extrapolation, burst
  extension/reset, saturation, overflow, and ABI layout equality.
- Existing non-empirical profile tests prove behavior remains unchanged.
- CPU benchmark replay checks all six cumulative P50 breakpoints exactly.
- A real-GPU automatic-PTX microbenchmark runs sequential and random 4 KiB
  page access under the generated profile, requires zero unsafe launches, and
  compares modeled totals with the empirical curve.
- The complete CTest and vLLM adapter suites remain regression gates.

The proof artifact reports source observations, predictions, errors, GPU
identity, exact output checksums, request counts, modeled timing, coverage,
and explicit boundaries. Passing a parser, build, or CPU replay is not
reported as real-GPU validation.

## Deliverables

- `scripts/tune_vmem_profile.py`
- `configs/profiles/cd8p-vmem-p50.json`
- `configs/tuning/cd8p-vmem-comparison.json`
- profile/schema and control-ABI support for the empirical curve
- focused Python, CPU, ABI, and GPU tests
- README usage and a dated proof checkpoint
