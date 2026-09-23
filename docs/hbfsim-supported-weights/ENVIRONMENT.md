# Environment used by the supported-weight run

This records the successful experiment. Build commands for the newly assembled source tree must be verified separately before being presented as a clean reproduction.

## Runtime cohort

| Component | Version / identity |
|---|---|
| GPU | NVIDIA GeForce RTX 5090, sm_120, 32,607 MiB reported memory |
| NVIDIA driver | 610.57.04 |
| Host | Linux x86_64, glibc 2.43 |
| Python | 3.13.9 |
| Torch | 2.9.1+cu128; source `5811a8d7da873dd699ff6687092c225caffcf1bb` |
| vLLM | 0.15.1; source `1892993bc18e243e2c05841314c5e9c06a80c70d` |
| Triton | 3.5.1 |
| FlashInfer Python / cubin packages | 0.6.1 / 0.6.1 |
| Transformers / safetensors | 4.57.1 / 0.8.0 |
| Native framework/JIT nvcc | CUDA 12.8.93 |
| NVRTC | 12.8.93 |
| cuDNN | 9.10.2.21 |
| cuSPARSELt | 0.7.1 |
| Model | OLMoE-1B-7B-0924, frozen local checkpoint ending `6d84c485` |

The Torch overlay replaces `libtorch_cuda.so`; it does not replace the entire installed Torch package. The vLLM overlay replaces `_C.abi3.so` and preserves the installed `_moe_C.abi3.so`. The latter's identity is required by the validated auxiliary-kernel path. Expert compute remains Triton `fused_moe_kernel`. Attention uses native FlashInfer and is outside the selected weight coverage.

The eight actual checkpoint/tokenizer/config files are content-pinned in [MODEL_FILE_IDENTITY.json](evidence/MODEL_FILE_IDENTITY.json), totaling 13,841,130,787 bytes. All files were streamed through SHA256 and checked for unchanged size/mtime across the read. The directory's short revision suffix is not presented as a recovered full upstream commit; use the recorded file identities to verify the exact input.

Successful runtime DSO hashes:

- Torch CUDA: `e3bdd95609b45a5ac3049b037beebfa4885044aac11408d7ccf58fe06739f9fe`
- vLLM `_C`: `4cb19bba649cc1364f2f52ef281d5ec879e86c555800808355bea7d8e0950d1a`
- Preserved `_moe_C`: `44942f2b9909ee5b687e928055c2de802b76d9e3fb11812be9f421288ff273ef`

## Separate build domains

1. **Native framework modules and FlashInfer JIT:** CUDA12.8 with G++13 as the CUDA host compiler; Torch ordinary C/C++ also uses GCC/G++13. The successful vLLM Ninja rules use `/usr/bin/c++` (G++15.2) for ordinary C++ and `-ccbin=/usr/bin/g++-13` for CUDA translation units. These compiler roles must not be conflated. C++11 ABI=1, ordinary architecture `12.0+PTX`. Pin CUDA_HOME, CUDAToolkit_ROOT, compiler paths, runtime dependencies, and FlashInfer's explicit nvcc selection. System nvcc12.4 is not the validated compiler for this GPU. Ordinary compute_120 PTX retention does not prove retention for every architecture-specific kernel.
2. **HBF runtime/pass:** G++15 and CUDA13 build tools, Release, sm_120, timing futures ON, `-fconstexpr-loop-limit=1048576`.
3. **Native launch gate and its static core:** G++15/CUDA13 headers, timing futures OFF for the actual core build, matching coverage headers, the CUDA symbol-version script, OpenSSL, CUDA driver/runtime, and MQSim. This compiler domain is not permission to redirect a CUDA12 runtime entry to a CUDA13 runtime API.
4. **bpftime agent/compiler:** the locked GCC/G++13, CUDA13, LLVM20 configuration, bpftime `ec26daecc8e787fb80fd95dd596a576404a5e36e` plus its complete host-interface patch, pinned submodules and Frida 16.1.2 devkits. Its latest three-file incremental diff alone is insufficient to reconstruct the tested implementation.

Torch's successful target build was `torch_cuda`, using the pinned `cpp-httplib` commit `89c932f313c6437c38f2982869beacc89c2f2246`. The actual configuration set `CUDNN_LIBRARY_PATH` and `CUSPARSELT_LIBRARY_PATH`, rather than similarly named ignored variables. It used OpenBLAS for CPU BLAS; this is not a claim of CPU BLAS parity with the installed wheel. The vLLM build retained PTX in the native modules and used the matching NVRTC12.8 successor configuration. A built `_moe_C` candidate was not substituted into the final validated overlay.

## Runtime choices that must survive reproduction

- Register full unique storage ranges, deduplicating parameter aliases; only the selected supported categories enter HBF.
- Keep the native binding manifest, exact CUDA12 entry identity, container SHA, raw PTX SHA, scoped provenance, and staged PTX SHA consistent.
- Use partial coverage policy for the intentionally unmodeled paths; do not count native execution as modeled coverage.
- Stage the four MoE variants from the actual short native run, not the older two-variant fixture.
- Compile only norm raw `553830cec46afe2195fdd9ae660b700b32ce3460270eb1f07c3801985ba8ef2e` at O0 with maximum register count 64. Preserve the normal optimization policy for other raw modules.
- Point explicitly to the vLLM extension and its matching core library. Preserve the selected attention/MoE backends, tuned MoE configuration, and isolated JIT caches.
- The successful nominal profile has 16 KiB simulated pages, 1 TiB logical capacity and 8 GiB configured cache. These simulation settings are not physical GPU-memory reservations.
- The actual run allowed 10,800 seconds overall and 300,000,000,000 ns per service request. Resource reservations and monitoring are external lifecycle controls, not hard-coded process IDs to copy into another run.

Fresh builds may have different ELF, PTX and provenance hashes. Regenerate identities from the actual new files and rerun the relevant interface/output/accounting validation; never copy an old expected hash onto new bytes.

## Framework build provenance

The exact Torch configure argv/environment, original build START and COMPLETE receipts, vLLM target build argv and terminal no-op check are preserved under [evidence/framework-build](evidence/framework-build/INDEX.json). Paths identify the actual experiment and must be remapped consistently when reproducing elsewhere. These are provenance records, not a script that starts a build automatically.

The successful Torch command built `torch_cuda`; the successful vLLM command built `_C.abi3.so` and `_moe_C.abi3.so` with four workers. The validated model overlay subsequently selected only rebuilt `_C.abi3.so`, retaining the installed `_moe_C.abi3.so` as described above. Repeating the build does not imply that every output should replace an installed library.

[vLLM compiler roles](evidence/framework-build/VLLM_COMPILER_ROLES.json) records the actual generated Ninja compiler commands and key cache fields. The generic CMake CUDA architecture cache value is not the authoritative kernel architecture: vLLM generates per-target architecture flags, and the retained ordinary compute_120 PTX is established by those flags and extracted module evidence. Do not substitute that generic cache value for the model's target architecture.

[vLLM third-party source pins](evidence/framework-build/VLLM_FETCHCONTENT_PINS.json) records the actual clean CUTLASS, FlashMLA, qutlass, Triton-kernels and flash-attention source commits. The PTX-retention patch is preserved beside the build receipts. Resolve these exact source dependencies rather than downloading their latest default branches.
