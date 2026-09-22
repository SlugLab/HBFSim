# Accepted fix provenance for rebuttal results

This index lists only fixes present in the validated R1/R2 path or required by the prepared R3 path. It deliberately excludes the failed warp-group-round and reserve-progress/OR scheduling candidates.

## Version-aware CUDA runtime forwarding

- Stable source: `/root/hbfsim-exp/rebuttal_20260921/resolver-source-v3/src/cuda_runtime/launch_gate.cpp`.
- Review patch: `/root/hbfsim-exp/rebuttal_20260921/resolver-build-domain-v3/domain-v3-vs-diag-v2.patch`.
- CPU receipt: `/root/hbfsim-exp/rebuttal_20260921/resolver-build-domain-v3/CPU_RECEIPT.txt`.
- Fix explanation: local `VERSIONED_RUNTIME_FIX.md`, SHA-256 `88a3c3bbd7069ccfb8a252a0963b14c133de251fcd50c04d595cc0a320c6fcf7`.
- Reviewed gate DSO SHA-256: `a34ccc14adddc31f3b29db15537210f98a18ef42fe709321fab66d8932f5abf6`.
- Scope: CUDA 12 and CUDA 13 runtime launch/query entry points remain in their own runtime domain. This removed the gate-only CUDA 700 failure in the controlled full-model comparison.

## Joint access-accounting and zero-delay session

- Stable source snapshot: `/root/hbfsim-exp/rebuttal_20260921/resolver-source-joint-v4`.
- Launch-gate source SHA-256: `40369dea9523e09e97a44b0f98fb399531b877c929c4e4ca2da13a583deb8334`.
- CPU receipt: local `final-r1/JOINT_V4_CPU_RECEIPT.md`, SHA-256 `1a42e3965045a4d5c5596cde1dc658d3e13c5870220c58ae25796837cbb82026`.
- Remote build receipt: `/root/hbfsim-exp/rebuttal_20260921/resolver-build-joint-v4/CPU_RECEIPT.md`.
- Scope: one epoch/module set, launch exclusion around begin/snapshot, paired rollback/abort ownership, late-module detection, and conservative poison/quarantine on cleanup failure.

## Legal GPU atomic sidecar

- Stable source snapshot: `/root/hbfsim-exp/rebuttal_20260921/atomic-sidecar-source-v1`.
- Build: `/root/hbfsim-exp/rebuttal_20260921/atomic-sidecar-build-v3`.
- Validated R1/R2 runtime manifest: `/root/hbfsim-exp/rebuttal_20260921/runtime-atomic-sidecar-v1/SHA256SUMS`.
- Core runtime SHA-256: `a62567d085fd55952fc6127fa6613c2c102e29325571a767c7044824ccac5da1`.
- Device-helper PTX identity is bound by the complete `runtime-atomic-sidecar-v1/SHA256SUMS` manifest; this index does not abbreviate it into a second authoritative value.
- Source audit: 35/35 PASS, SHA-256 `a40c482e160cbf7c10677661b5f66a5ca2ef0cfce70328683c613febe20b3aa7`.
- PTX audit: 5/5 PASS, SHA-256 `0a40f2339141bee2060a249e1e5ab0ea9f5189bf9daddfc3855e352203f69644`.
- Scope: on hardware with `HostNativeAtomicSupported=0`, GPU-owned request/timing RMW state resides in one context-shared device allocation. CPU public producer/consumer and timing-future APIs fail closed in the limited GPU-exclusive mode. The current capacity protocol is read-only.

## Final six-cell R2 bundle

The final matched six-cell R2 sweep uses `/root/hbfsim-exp/rebuttal_20260921/first-fault-compact-runtime-v2`, built from the accepted runtime-domain, joint-session, and atomic-sidecar fixes with the compact diagnostic compiled in but disabled by default. It is not byte-identical to the earlier `runtime-atomic-sidecar-v1` used by the final R1 D0/nominal arms. The independent six-cell configuration review records these exact R2 hashes:

- core `libhbfsim.so.0.1.0`: `a3db7e5438ecd823554cb8b22438dce5edfb1742a27bbe6df5f49b8d5cf06afb`;
- helper `hbf_device.ptx`: `01df14763d7df25ca0f03a375e8f991e4f158ad61f9c2a3f8881d4792c5d4e56`;
- PTX pass: `0ab365ca22940995a568cce6d8962f034709b6090d7074c417068379efb09a58`;
- launch gate: `d41509ef4922e1dd99880bb88c3a840f850ea0418e445874d56fe8a46e802ef0`;
- vLLM extension: `3697042d627105028b4d84cdf351da1d39b6d94ea067f24b4ccb4ad1a327b794`;
- daemon: `b1a3c30dd91e4a3804bd67780bbc59209fb38f74a6f30282ab3ebf7eea5850e5`;
- bpftime agent: `27ea313983c325429d33efa896dcefcb519f14e5886226628c358c1f576d3089`.

The common R2 matrix uses `request_timeout_ns=60,000,000,000` with linked heartbeat/lifecycle protection. The exact configuration-equivalence receipt is `R2_COMMON_CONFIGURATION_REVIEW.md`, SHA-256 `ab9ab1aad7e472824e97d81cc782365bf5758efd5defd2443081cf332bb26ac2`. R1 remains bound to its own `runtime-atomic-sidecar-v1/SHA256SUMS`; the old atomic 1 MiB result is not substituted for the final matched 1 MiB/60 s cell.

## R3 host correction (prepared, not scientific evidence)

- Isolated runtime: `/root/hbfsim-exp/rebuttal_20260921/r3-ring64-runtime-host-v2`.
- Host patch: `RING64_HOST.patch`, SHA-256 `78eabd0cdfcfe15030ea83d023a6ae4fc2aee560fe58ab02178a738134d5063c`.
- Host benchmark SHA-256: `6245ad6445965d0fad3d43a9d8bd17b5e1308d14d1cc681527ce7a67db00659c`.
- Change: ring capacity 65,536 to 64 plus explicit context-status logging. No page protocol or kernel change.
- State: READY_NOT_RUN. CPU/ELF/hash success is not an R3 measurement.

## Explicit exclusions

The following remain failure/diagnostic history and are not part of the accepted scientific mechanism: `warp-group-round-*`, `reserve-progress-*`, and the OR/positive-sequence retry candidate. First-fault **enabled** diagnostic observations are used only to classify preserved failures. The default-off `first-fault-compact-runtime-v2` binary is the actual final R2 bundle and is therefore retained in provenance rather than excluded. All raw failures remain preserved.
