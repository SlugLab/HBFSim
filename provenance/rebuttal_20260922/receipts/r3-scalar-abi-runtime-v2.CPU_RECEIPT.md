# R3 ring64 host candidate v2

Status: READY_NOT_RUN (CPU/ELF only; no GPU launched here).

Base runtime: /root/hbfsim-exp/rebuttal_20260921/r3-exactbind-runtime-v1
Base source SHA256: 154d51f7074dbf35ebd5c3164374526767bec9e1c0a699e024a8657284029a53
Candidate source SHA256: 47029f095eea39863a4e2bf37b2e81235cf4f84a03a550cdc1a087f3160937a0

Scoped source changes:
- hbfsim_options.ring_capacity: 65536 -> 64, within ABI maximum 4096.
- Preserve reference mode and request_timeout_ns=30000000000.
- Print hbfsim_context_create result, ring capacity, and timeout before fail-closed exit.
- Exact PTX binding code, page protocol, kernel, profile, stats and payload validation are byte-identical to exactbind-v1.

Build:
- /usr/bin/g++-15, -std=c++20 -O2
- CUDA 13.0 headers and libraries from env-restore-v1/toolchain-download-v1
- static core: atomic-sidecar-build-v3/libhbfsim_core.a SHA256 db9b9d58b514a0cf0ce12c98344d97537cbc7d416a43316aedc2a2b30cebddd3
- no GPU execution

Runtime path: /root/hbfsim-exp/rebuttal_20260921/r3-ring64-runtime-host-v2
Binary: /root/hbfsim-exp/rebuttal_20260921/r3-ring64-runtime-host-v2/r3_public_capacity_bench
Patch: /root/hbfsim-exp/rebuttal_20260921/r3-ring64-runtime-host-v2/RING64_HOST.patch
