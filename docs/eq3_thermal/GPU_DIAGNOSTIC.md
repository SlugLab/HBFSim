# GPU diagnosis — 2026-09-19

**DOC_DERIVED: device-access discrepancy reproduced and localized to execution
context. The host has an RTX 5090; no driver repair is indicated.**

The same read-only `tools/eq3_gpu_diagnostic.py` ran in the default agent context
and through the existing targeted approval mechanism. No global sandbox setting,
driver, device permission, power limit, clock, process or environment was changed.
Only driver initialization/enumeration occurred, not a compute kernel.

| Layer | Default agent context | Targeted authorized context |
| --- | --- | --- |
| HOST_ENUMERATION | User reports successful host enumeration | Confirmed NVIDIA GeForce RTX 5090, one device |
| Device visibility | `/dev/nvidia*` absent (ls exit 2) | Device nodes visible |
| AGENT_NVML | Init code 9; basic smi and -L exit 9 | Init/count code 0; count 1; both smi calls exit 0 |
| CUDA_ENUMERATION | libcuda opens; cuInit returns 100 | cuInit/count return 0; count 1 |
| TOOLCHAIN | PATH nvcc 12.4.131 | Same PATH nvcc; existing alternate toolkit 13.1.115 advertises sm_120 |
| FRAMEWORK | Private CPU venv has no torch, tensorflow or jax | Same; not installed, GPU framework execution NOT_TESTED |
| GPU_SMOKE_AUTHORIZATION | NOT_APPROVED | NOT_APPROVED; compute budget zero |

Local receipts: workspace `eq3_thermal/runs/p2-diagnostics/agent-default.json`
and `agent-escalated.json`; optional query receipts have labels
`telemetry-capabilities`, `available-toolkit`, `available-toolkit-targets`.
They retain commands, stdout/stderr, status, timestamps, namespace and boot
identities, whitelist-only environment and actually loaded library paths.
Keep these host/process identifiers local; they are not portable requirements.

Same boot identity, kernel, UID/GID and cgroup; different mount/PID/user namespace
identities. Both loaded the same real 610.57.04 NVML/libcuda paths, not CUDA stubs.
LD_PRELOAD, LD_LIBRARY_PATH and GPU visibility/capability variables were unset in
both. Device visibility and initialization changed with execution context.
This rules out absent host hardware and wrong PATH binary as explanations of
this reproduced failure. Exact sandbox policy implementation is not established;
do not infer a particular Docker misconfiguration or change host security.

Earlier observations were searched under environment/run/governance records.
`ENVIRONMENT_BASELINE.md`, `INITIAL_AUDIT.md`, `EQ3_BASELINE_RESULTS.md` and the
CPU-v1 manifest retain failure summaries, but an original P0 per-command
stdout/stderr/exit receipt was NOT_ACQUIRED. The new failure receipt is a
reproduction, not a fabricated replacement for that missing historical record.

## Telemetry and coexistence limits

Authorized read-only query observed 34 C GPU temperature, board-domain power,
memory temperature N/A, memory power N/A. These missing fields stay unavailable,
not zero. The NVIDIA-SMI CUDA UMD heading 13.3 is not installed nvcc version.
The device reported a 600 W current power limit, not the generic product-page
575 W TGP; neither is a measured GPU-die-only heat input. No limit was changed.
An existing VLLM service occupied about 27,732 MiB at enumeration. This is an
external workload, not this task. Do not stop it or run competing calibration.

RTX 5090 is Blackwell, cc 12.0, 32 GB GDDR7, not HBM:
[NVIDIA specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/).
The existing alternate compiler supports sm_120, but project kernel/framework
compatibility remains NOT_TESTED. Older-toolkit PTX compatibility is distinct
from native cubin support:
[Blackwell guide](https://docs.nvidia.com/cuda/blackwell-compatibility-guide/index.html).
Container compute and utility capabilities are separate concepts, not a proven
cause here:
[NVIDIA container documentation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).

Minimal next step: keep CPU work in its frozen environment. If physical sampling
is later desired, first obtain a specific approved GPU preflight and an exclusive
or explicitly coordinated device window, then use the existing targeted access
mechanism and separately versioned private build. No driver installation needed.
