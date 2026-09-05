# Server experiment safety and resource ownership

## Purpose

Preserve the user's workspace, device and process boundaries while preparing reviewable experiments and retaining useful progress when hardware-dependent work is blocked.

## Scope

The current approved work is inside `/root/hbfsim-exp`, including this authorized integration worktree. Documentation and CPU-only preparation are permitted; this knowledge-pack task authorizes no GPU workload, storage payload acquisition, other-clone edits or system changes. Future collectors must apply the current user contract, not inherit permissions from a historical runbook.

## Key concepts

Read-only identity discovery is different from read-only payload benchmarking. A device model/name is not permission to use it. A child process created by this run is different from another user's process. A free-memory number does not establish exclusive GPU availability. A historical driver failure is not current hardware state unless rechecked.

## Important files

[Environment runbook](../eval/runbooks/environment_setup.md), [mechanism runbook](../eval/runbooks/mechanism_validation.md), [hardware contract](../49-eval-audit/hardware-groundtruth-contract.md), [execution plan](../49-eval-audit/execution-plan.md), [context lifecycle](../../src/cuda_runtime/context.cpp), [existing microbenchmark runner](../../scripts/run_microbench.py), [existing thermal collector](../../scripts/thermal/collect.py), and [phase-two blockers](../50-implementation-blockers.md).

## Important structs/classes/functions

`spawn_daemon`, `process_status` and `reap_or_terminate` manage the context's own `hbfsimd` child. `release_context` coordinates shutdown and resource quarantine. `scripts/thermal/collect.py:discover_cd8p` checks model plus holders/mounts; `gpu_sample` reads telemetry. Its `main` also launches GPU heating and raw-device fio, so invoking that collector is a workload, not identity-only inspection. `scripts/run_microbench.py:run_case` starts benchmark children with a timeout; the runner is not a substitute for the phase-two resource guard.

## Call path / data path

Before work, record cwd, Git root, dirty state, branch and full SHA and verify the authorized checkout. For an eventual device run: identify exact device/backing/owner → check the user-authorized resource boundary → freeze local environment/output paths → apply preflight and resource guard → launch only owned children → retain status/raw artifacts → stop/reap owned children on failure. On a blocker, record observed evidence/impact and continue independent CPU work; do not repair the system to manufacture readiness.

## CPU-side vs GPU-side execution context

Documentation, source inspection, builds, parser tests and model-only replay do not require initializing a GPU. Identity tools observe the host/device without launching kernels or touching payload. GPU benchmark code, vLLM, heating and any file/device payload collector require their separate resource readiness gates. The parent page worker and `hbfsimd` are user-space processes, not system services.

## Invariants

- Modify only the authorized workspace; preserve unrelated dirty work and the exact frozen baseline.
- Do not reset/clean the repository, remove broad directories, change drivers, reset GPUs, alter system CUDA/Python, edit `/etc`/systemd, or touch the private paper submodule.
- Do not kill/pkill another user's process or reclaim their GPU/model/cache. Own-child cleanup must use the recorded process identity and bounded lifecycle.
- Prefer the frozen local environment. If a Python-only dependency is truly needed, use the user-specified workspace `.venv-eval`; never a global install.
- Formal storage acquisition needs an explicitly permitted device/file/backing identity. No format, wipe, full-device preconditioning, raw writes or payload benchmark is implied by discovery.
- Driver unavailability, another user's GPU job, absent checkpoint, unknown SSD authorization, unclosed async ABI, contaminated heldout or conflicting figure/matrix contract is a blocker, not a reason to invent results.

## Supported behavior

Workspace/provenance checks, source-only review, bounded CPU tests and local artifacts, read-only device identity checks within authorization, and cleanup of this context's own daemon. Existing collector guards are useful source references but cover their narrow historical scenarios only. The planned resumable scheduler/resource guard must be implemented and verified before it is treated as enforcement.

## Explicitly unsupported behavior

Blind raw-device acquisition by first matching model; using a mount check as ownership authorization; GPU memory reclamation/reset; changing other worktrees or global installations; undocumented resource occupancy; restarting ideal-size experiments automatically after a failed pilot; or presenting skip/block status as PASS.

## Common failure modes

Copying historical `/dev/shm` or external model paths into current commands without checking the approved boundary; assuming a collector is safe because its name says read-only; using a raw namespace instead of the authorized dedicated file; running a GPU smoke merely to see whether the driver recovered; or leaving spawned benchmark children behind after timeout.

## Tests proving the behavior

Existing [context lifecycle tests](../../tests/cpu/context_lifecycle_test.cpp), [daemon protocol tests](../../tests/integration/daemon_protocol_test.cpp), [attach-loader shutdown tests](../../tests/integration/test_attach_loader_shutdown.py), and [thermal tool tests](../../tests/integration/test_thermal_logp.py) exercise their specific software contracts. They do not prove a general multi-user resource guard or authorize device access. Source inspection for this document launched no collector, GPU workload or storage payload I/O. New resource-guard tests are a required phase-two task, not an existing capability claimed here.

## What not to change casually

User resource boundaries, source provenance, safe defaults, child ownership checks, failure statuses and artifact retention. Historical skill/plan instructions about pushing, system paths or live proofs do not override the current instruction to stay in this workspace and stop at the current review boundary. No push is authorized by these documents.

## Related docs

[Reading order](00-reading-order.md), [evaluation protocol](09-evaluation-protocol.md), [capacity lifecycle](07-capacity-address-translation.md), [execution plan blockers](../49-eval-audit/execution-plan.md), and [current capability audit](../49-eval-audit/current-capability-audit.md).
