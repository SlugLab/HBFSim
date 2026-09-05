# Evaluation implementation blockers

Status is specific to this checkout and this execution; historical PASS records
do not close the current gold gates.

| ID | State | Evidence and impact | Next action |
|---|---|---|---|
| WORKSPACE | RESOLVED | `/root/hbfsim-exp` is an experiment container, not a Git root. After user-authorized branch discovery, `/root/hbfsim-exp/eval-base-integration` contains the exact frozen base and all required 49-audit files. | Use only this nested checkout for implementation. |
| BASE-CONFIG | CONFIGURATION IDENTIFIED | Fresh CUDA-OFF configure with libbpf discovery fails: `CMake can not determine linker language for target: hbfsim_llama_probe_module`. Frozen optional-tools cache already sets `CMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON`. | Reproduce that exact CPU configuration without a source patch. Retain the failed attempt in `results/gold/base/configure.log`. |
| GPU-DRIVER | OPEN | Current `nvidia-smi` and compute-process query cannot communicate with the NVIDIA driver. Evidence: `results/gold/base/gpu-readiness.txt`. | Do not repair system driver. Continue CPU work; GPU semantic gold and timing pilots require a working authorized GPU. |
| ASYNC-INTEGRATION | OPEN | Donor has the predicated-consumer defect and incompatible control/request layouts; base coverage fixes must survive. | Counterexamples first, then concept-by-concept integration. No direct donor merge. |
| STORAGE-TARGET | OPEN | No dedicated benchmark file/device has yet been established for this campaign. | Read-only identity and ownership preflight; no payload I/O or write/trim/format without the required authorization. |
| CHECKPOINT | NOT VERIFIED | Existing external Qwen inventory is a historical record, not a current local checkpoint check. | Verify actual config/index/tensor headers before inventory or rho claims. |

Starting runtime SHA: `fc829992ecdc3ca68881656722b67a31067c5d33`.
Remote fetch succeeded; origin base, async donor, and capacity donor still match
the frozen audit. Source and graph evidence are in `results/gold/integration/`.

No experiment result is implied by this blocker log. Failed configuration is
not a test PASS, GPU tools on disk are not GPU availability, and unverified
inventory is not measured model identity.
