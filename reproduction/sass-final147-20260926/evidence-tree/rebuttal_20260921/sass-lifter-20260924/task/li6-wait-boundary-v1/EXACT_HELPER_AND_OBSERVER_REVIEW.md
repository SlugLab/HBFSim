# Exact Li6 helper provenance and safe progress observation

CPU-only static review, 2026-09-25. No live process, GPU, build, PTX, helper,
agent, gate, or runtime source was changed or sampled.

**Exact executable helper provenance (DOC_DERIVED).** The Li6 source PTX is
`../qkv-output-repair-v1/cpu-build-v1/qkv.quarantine.ptx` SHA-256 `6db074...`.
`../qkv-hbf-replay-v1/stage-v1/STAGE_RECEIPT.json` identifies the staged
filename by that *source* hash and its content by SHA-256 `7219c1...`;
`pass-manifests.jsonl` records this exact Li6 entry, 243 static rewritten
instructions, zero unsupported instructions, target `sm_120`, and 19 declared
parameters. The `kEmbeddedDevicePtx` string extracted from
`rebuttal_20260921/first-fault-compact-build-v2/generated/hbf_device_ptx.hpp`
is 297,388 bytes, SHA-256
`f9efe0858b1aca808c263074ad7bfa2c4826e9d254476c5f5343a8e1f0a699c7`,
and occurs **byte-for-byte** in the staged Li6 PTX at offset 333. This is
stronger than identifying a similar source file by name. The build's
`build.ninja:4688-89` records CUDA 13.0 `nvcc --ptx`, compute_120,
relocatable-device-code, futures-on flags, and input
`first-fault-compact-source-v2/src/cuda_runtime/device/hbf_device.cu`
(SHA-256 `da691223...`) plus `.cuh` and ABI header, then embeds generated PTX
in that header. The frozen Li6 bundle manifest resolves `libptxpass_hbf.so`
SHA `0ab365ca...`, `libhbfsim.so` SHA `a3db7e54...`, and `hbfsimd` SHA
`b1a3c30d...` through the workspace runtime bundle to
`first-fault-compact-runtime-v2`. Thus the exact helper's primary source is
`first-fault-compact-source-v2`, not a later similarly named B-weight copy.

**Existing observation APIs and limits (DOC_DERIVED).** The frozen gate
`qkv-real-model-agent-abi-v2/launch_gate.cpp` exports
`hbfsim_access_accounting_snapshot_v2(char*,size_t)` and
`hbfsim_request_accounting_snapshot_v1(char*,size_t)`. Both are terminal
snapshot paths; `access_snapshot` calls `cuCtxSynchronize`, reads device
counters, disables the session, and synchronizes again. The frozen
`libhbfsim` `hbfsim_get_stats` path also calls `cudaDeviceSynchronize` and
`cudaMemcpy` in GPU-exclusive producer mode (`first-fault-compact-source-v2/
src/cuda_runtime/context.cpp:1870-1900`). Calling any of these during the
stalled launch would itself block or alter lifecycle; they are **not**
in-flight progress probes.

The runtime creates a sealed `memfd:hbfsim-control` in `context.cpp:428-435,
841-866`, passes its FD as daemon `--control-fd` in `context.cpp:741-756`,
and maps it in `host_service/main.cpp:105-123`. Its ABI-5
`SharedControlHeader` is 384 bytes with magic/version/header-size, ring
capacity, heartbeat/shutdown/fault, host `request_consumer`, and host
`completion_producer`. An external **read-only** observer could, after
verifying daemon PID/start/command, exact FD link and header magic/ABI/size,
read that FD via `/proc/<daemon-pid>/fd/<fd>` at a few bounded times without
CUDA calls or runtime mutation. Sampling host consumer/completion deltas plus
heartbeat distinguishes daemon liveness and reference-service progress. In
GPU-exclusive mode, request producer/completion consumer and fast counters
live in a separate `DeviceTimingState` on GPU; host header values cannot
establish total kernel progress. A static host count or live heartbeat alone
does not establish deadlock or service closure. Cross-process snapshots can
be concurrent/torn; record raw values and treat only validated monotonic
deltas as directional evidence.

The gate also exports `hbfsim_first_fault_begin_v1(uint64_t,const char*)`,
`hbfsim_first_fault_snapshot_v1(char*,size_t)`, and
`hbfsim_first_fault_abort_v1()`. Begin creates an `O_EXCL` mapped backing,
pins it with `cudaHostRegisterMapped`, binds tracked modules and writes their
identity; the failure writer uses a compact word and release-ready full
record. External read-only access to the backing is no-sync, but first-fault
capture is **not active** in the frozen Li6 plan, and enabling it changes
host diagnostic lifecycle. Its absence would not prove forward progress;
positive validated compact/full records can locate reserve, completion or
fast-target failure. Do not call snapshot/abort or add begin to a run
without checking module/context binding and teardown for that exact plan.

**Small next discriminator (INFERRED).** A head-only model selection, with
the frozen agent's already supported `HBFSIM_LI6_REPRESENTATIVE_V1=lm_head`,
could separate a large-head/service issue from prior o_proj state. The
current two-selection plugin insists on both calls and must be isolated in a
host-only copy for head-only request→compute_logits binding. If head-only
passes while the combined selection stalls, prior-state interaction becomes
more likely; if it stalls similarly, the head path remains implicated. Neither
alone proves a helper defect or repairs the combined two-selector lifecycle.
