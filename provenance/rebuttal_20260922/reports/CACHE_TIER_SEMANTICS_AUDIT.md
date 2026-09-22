# HBFSim weight-cache semantics audit

Date: 2026-09-22  
Scope: read-only audit of the giga rebuttal worktree and the epoch-3320 configuration. No GPU run or source change was made.

## Conclusion

The current vLLM weight-binding path does **not** implement the policy “GDDR/HBM-cache hit uses cache latency; miss uses HBF latency.” It registers already allocated CUDA parameter storage as `timing_backed` and injects an HBF service delay for covered loads. The hybrid selector chooses reference-service requests versus a fast analytic approximation; that choice is sampling/calibration, not a cache hit/miss result.

HBFSim has a separate software-managed frame cache for `capacity_unbacked` mappings. That path has resident/reclaimed hits, misses, backing reads, H2D fills, eviction, dirty tracking, and writeback. It is used by the R3 capacity experiment, not by the epoch-3320 all-weight timing registration. It also must not be described as observing the GPU's native L2/GDDR cache tags.

## Source evidence

- `adapters/vllm/hbfsim_loader.py:275-303` discovers CUDA storages, calls `register_storage(address, bytes)`, and emits mode `timing_backed`. It does not create a file-backed logical mapping or move parameter payload into a software cache.
- `src/cuda_runtime/context.cpp:1489-1544` accepts only `HBFSIM_RANGE_MODE_TIMING` in `hbfsim_register_device` and registers the range with launch-gate policy `TimingBacked`.
- `src/cuda_runtime/context.cpp:1550-1574` provides the distinct `hbfsim_map_file` API and requires `HBFSIM_RANGE_MODE_CAPACITY` for file-backed logical mappings.
- `src/cuda_runtime/device/hbf_device.cu:459-606` implements hybrid timing. Requests selected by the warmup/sample rule use the reference queue (`475-480`); the remaining requests use read/program latency plus an aggregate-bandwidth channel tail (`553-606`). There is no page-tag lookup, resident state, replacement, fill, or writeback decision in this timing path.
- `src/cuda_runtime/device/hbf_device.cu:918-923` dispatches timing-backed range mode 1 to `resolve_fast_or_hybrid`; the other modeled mode goes through the service resolver. This is a mode distinction, not a cache-hit branch inside timing mode.
- `src/cuda_runtime/capacity_runtime.cpp:24-34,111-148` is the production runtime consumer of `profile.hbm_cache_bytes`: it computes the frame count and constructs the VMM frame pool, cache, backing router, copy callbacks, page service, and worker.
- `src/host_service/capacity_page_service.cpp:97-126` records resident and reclaimed hits and then a miss. Its miss path reads backing data, performs H2D fill, and publishes a frame (`160-209`); lack of a free frame triggers eviction and dirty writeback (`127-157`).
- `src/cuda_runtime/context.cpp:1802-1849` exposes these counters only through the capacity statistics API when a capacity runtime exists.
- `configs/profiles/nominal.json` declares `hbm_cache_bytes=8589934592`, warmup 1024, sample rate 0.01, and `time_scale=100`. Repository-wide production references show `hbm_cache_bytes` is consumed by `CapacityRuntime`; its presence in this shared profile does not activate a timing-backed weight cache.

## Experiment interpretation

- Epoch 3320 registered all 147 unique CUDA parameter storages as `timing_backed`. Those tensors remained ordinary CUDA allocations physically resident in GPU memory. Registration changed modeled timing for covered instrumented accesses; it did not establish an 8 GiB cache in front of HBF.
- D0 versus nominal is a matched zero-delay versus configured-delay comparison. It is not a cache-hit versus cache-miss experiment.
- Native GPU L2/cache behavior may still occur underneath ordinary loads, but this campaign neither observes hardware hit/miss state nor uses it to choose the injected delay.
- R3's 2 GiB frame pool is a software payload cache for `capacity_unbacked` logical addresses. It is separate from the all-weight timing experiment and was not a hidden cache tier for those weights.

## What would be required

Supporting the requested policy would require either (1) mapping compatible weight payloads through the capacity API and defining the desired hit latency on that path, or (2) adding a per-context cache-tag/replacement/fill model to `timing_backed` service and selecting delay from that modeled state. GPU physical residency or a profile field alone is insufficient evidence of such behavior.
