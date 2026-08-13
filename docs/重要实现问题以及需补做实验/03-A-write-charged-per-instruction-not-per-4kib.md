# A write is charged one full program time per instruction, not once per 4 KiB unit

**Severity: A.** For a store issued from a kernel into a registered range, filling one
4 KiB page costs 8 to 32 times what the OCP specification says the device charges,
depending on how wide each store is. Any result that involves writing is affected
today.

**How sure we are.** Read from the primary source: the code lines, the profile value
408,305, and the OCP specification text on page 58. Our own inference: the 8 to 32
range and the two millisecond figures, which are arithmetic on top of those facts.

**Does this conflict with the OCP specification? Yes.** Section 5.4.1 item 3, page 58,
requires the base die to accumulate a complete 4 KiB write before anything reaches the
NAND core die, so the program cost is paid once per 4 KiB unit, not once per write
command.

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

`empirical_request_service` at `src/cuda_runtime/device/hbf_device.cuh` lines 460 to
485. Lines 474 to 480 branch on the operation: `operation == 0` goes to the measured
curve, and `operation == 1` returns `header.program_latency_ns` directly. The returned
value does not depend on the byte count, on alignment, on the order of the addresses,
or on any state of the block being written.

Above that, `media_descriptor` at lines 274 to 289 of the same file rounds any access
down to a whole page and sets `bytes` to `range.page_bytes`, and the measured-curve path
at `src/cuda_runtime/device/hbf_device.cu` lines 322 to 327 requires
`media.bytes == 4096 && range.page_bytes == 4096` with a page-aligned address. Within one
warp, lanes hitting the same range and the same logical page are merged into one request
at `src/cuda_runtime/device/hbf_device.cu` lines 504 to 511.

Putting those together with the `cd8p-vmem-p50` profile, where `program_latency_ns` is
408,305:

- A 4 KiB page filled by ordinary 4-byte stores needs 32 warp-wide store instructions
  (32 lanes × 4 bytes = 128 bytes each), so 32 requests: 32 × 408,305 = 13,065,760 ns,
  about **13.07 ms**.
- The same page filled by 16-byte vector stores such as `.v4.b32` needs 8 instructions:
  8 × 408,305 = 3,266,440 ns, about **3.27 ms**.
- Under the specification the same page is one accumulation followed by one program:
  **408,305 ns**.

## Why it looks questionable to us

OCP specification v0.7.0, section 5.4.1, page 58:

```
All writes are non-posted writes. A write command received with any length receives
only one response sent to the host irrespective of length size
```

```
The write command is completed to the host only after the data is written to the
Core die.
```

```
The write granularity to the Core die is 4KiB. The Base die controller first
accumulates the complete 4KiB write commands and data is sent to the Core die.
```

The base die accumulates the pieces and programs the NAND once. The current code
charges a full NAND program time for every piece.

Note where this does **not** apply. The capacity mode issues its write-back as one
request per evicted page — `src/host_service/capacity_page_service.cpp` line 131 sets
`CapacityMediaProgram`, and `src/host_service/request_dispatcher.cpp` lines 107 to 120
turn it into a single `operation = Write` request for the page — so that path is charged
once per page and is not affected. The overestimate applies to stores issued from a
kernel into a registered range.

## Which direction the effect goes

Upward, by a factor between 8 and 32 for kernel-issued writes, with the factor set by
the width of each store instruction. The factor is arithmetic, not a measurement, and it
assumes the page is filled by stores of uniform width from full warps.

## Where we may have read it wrong

1. **The `operation == 1` branch may be a placeholder** left in place because no
   measured write workload has been run yet. If no reported result contains a
   kernel-issued write, nothing published so far is wrong, and the fix is ahead of the
   first write experiment rather than behind it. We do not know which of the runs under
   `docs/proofs/` contain kernel-issued writes into a registered range, and that is one
   of the questions below.
2. **The intended usage may be one store instruction per page.** If the write workloads
   you have in mind always write a full page from one warp-wide instruction, the merge at
   lines 504 to 511 already produces one request per page, and the charge is right.
3. We did not find a separate write accumulation path elsewhere; we searched
   `src/cuda_runtime/` and `src/host_service/` and found none, but a different naming
   would not have shown up.

## What it would take to fix

Small and self-contained, and we can prepare it if you want.

Give each open 4 KiB unit a 64-bit coverage mask — 4 KiB divided by the 64-byte write
granularity is exactly 64 pieces, one bit each. Charge `program_latency_ns` when the mask
fills, and charge nothing before that. Bound the number of open units the way the
specification does: section 5.4.1 item 7, page 58, makes the number of outstanding 4 KiB
write units per host channel product-defined and gives
`For example, it is 64 or 128 or any other number`. At 32 channels times 128 units, that
is 4,096 entries of 16 bytes, 64 KB, which belongs in a new region after the existing
range, ring and page regions rather than in the shared header.

Two things constrain the layout, and both are already known. The shared control header is
pinned at 384 bytes by `static_assert(sizeof(SharedControlHeader) == 384)` at
`src/host_service/control_layout.hpp` line 155, and its last field `empirical_flags` ends
at offset 344, leaving 40 bytes of `alignas(64)` tail padding — enough for an 8-byte
offset to the new region without moving anything. Adding a region changes
`control_region_bytes` at `src/host_service/control_layout.hpp` lines 117 to 128 and the
per-region expected-offset computation at `src/cuda_runtime/device/hbf_device.cu` lines
459 to 479, and both mirrors of the header have to change together
(`src/cuda_runtime/device/hbf_device.cuh` lines 33 to 76 and
`src/host_service/control_layout.hpp` lines 81 to 124), with `kControlAbiVersion` at
`src/cuda_runtime/device/hbf_device.cuh` line 15 going from 4 to 5 and the two layout
tests updated.

Our estimate: about 150 to 250 lines including tests. The accumulation logic itself can
be a `constexpr` function next to `empirical_request_service` and tested on the host, in
the same way `tests/cpu/device_helper_abi_test.cpp` tests the existing device helpers, so
no GPU is needed to test it.

## What we would like you to confirm

1. Do any results under `docs/proofs/` involve kernel-issued writes into a registered
   range? If yes, which ones, so the factor can be disclosed there.
2. Was the `operation == 1` branch a placeholder, and do you want the accumulation
   model built before 2026-09-15?
3. If you want it built, do you want us to prepare the change, or do you prefer to
   write it yourself given that it touches the shared layout and the ABI version?

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.
