# HF native, capture, and repeat runner implementation plan

> For the implementation worker: use the executing-plans and TDD workflows,
> with one implementation worker and separate reviews. This task prepares a plan
> only and performs no model loading or GPU work. The user's phase-two objective
> already authorizes the bounded real triplet after implementation, CPU tests,
> reviews, and resource gates pass; no renewed human approval is required.
> Do not edit installed packages, the HF cache, GGUF
> loaders, core interception code, the matrix, or the formal registry.

**Goal:** Produce an immutable, independently checked three-arm bundle for one
32-input-token / 8-output-token request on the current local HF BF16 checkpoint,
with returned expert routes bound to the owned capture and repeat processes.

**Architecture:** A standard-library parent freezes inputs and owns one GPU
guard across three sequential, separate processes. A small lazy-import worker
runs the installed vLLM API and records its actual returned objects. Existing
metadata, inventory, compatibility, and trace validators remain unchanged; an
outer validator reconciles their artifacts and keeps consistency checks separate
from origin evidence.

**Tech stack:** Python, installed vLLM 0.15.1, NumPy route arrays, the existing
ResourceGuard/run_child process lifecycle, and CPU-only unittest fixtures.

## Source-grounded starting point

The active checkout is `/root/hbfsim-exp/eval-base-integration`. Installed
distribution metadata reports vLLM `0.15.1` at
`/opt/miniconda3/lib/python3.13/site-packages/vllm`; this plan did not import
vLLM or torch. Use this installation, not a historical phase3 environment.

The checkpoint is `/root/hbfsim-exp/phase3/models/Qwen3-30B-A3B`.
The input must include a published bundle accepted by
`verify_hf_metadata.validate_refresh`. The parent completed the real refresh at
`results/manifests/hf-qwen3-30b-a3b-metadata-20260905` in this checkout, using
source commit `16d5261`, and reported frozen/current-input checks passing. This
planning task read only its frozen receipt and marker, observing:

```text
receipt_sha256=d40980fea11f3b9b2ca12c26e282fee40ae196e7e004d10889432416f292c8cb
complete_sha256=27e36639663b7290749f61cc17e284b3b313b2998ddb220d3633f4251d93039d
metadata_identity_sha256=6bd086d9258aeec88aa3294df6133c289c0a8d5b557f7ce03b5c7dc6644d7494
observation_identity_sha256=7db277cf3ca52ad295b50be84f90383ba9210b778333ffd623dd53f731b319b2
```

The real runner must revalidate this bundle and current inputs at its own gates;
the existing receipt does not waive the before/after checks below.
Its frozen donor retains historical fingerprint
`af52de6efe45aa0e0fe9fe393985a25daa16306c440effe493a12ba10e03dda9`.
Its current metadata describes BF16, 48 layers, 128 experts/layer, top-k 8,
6,144 experts, and 9 MiB/expert. Historical payload SHA values remain historical.
Read `L=summary.layers`, `E=summary.experts_per_layer`, and `k=summary.top_k`
from the validated receipt and reconcile them with the frozen config/inventory.
48/128/8 are observations for this receipt, never fallback Qwen defaults.

The parent task reported a foreign llama-server using 89,792 of 97,887 MiB
on the selected GPU when this plan was requested. This is a dated blocker
observation, not a new probe by this planning task. No model arm may launch
while that foreign process remains. The 61,064,245,248-byte BF16 tensor inventory
alone also exceeds the roughly 8 GiB remaining; that inventory total is not an
estimate of the complete runtime memory footprint.

Installed source establishes the following interfaces and constraints. Paths in
the first column are relative to the installed vLLM directory above.

| Source | Contract used by this plan |
| --- | --- |
| `entrypoints/llm.py:187`, `:367` | `LLM(enable_return_routed_experts=..., kv_cache_memory_bytes=..., **kwargs)` and `generate(prompts, sampling_params, use_tqdm=False)` are supported. |
| `inputs/data.py:56`; `sampling_params.py:131` | A tokenized prompt is `{"prompt_token_ids": [...]}`; explicit sampling controls include seed, temperature, min/max tokens, ignore_eos, and detokenize. |
| `outputs.py:22`, `:110` | Preserve `RequestOutput.request_id`, prompt IDs, finished state, and `CompletionOutput.index`, token IDs, finish/stop reason, and `routed_experts`. Routes are on the completion, not the request. |
| `v1/core/sched/scheduler.py:1493` | `_get_routed_experts` takes `request.num_tokens - 1` positions from KV slot mappings. There is no route for a final generated token that is never fed back. |
| `model_executor/layers/fused_moe/layer.py:527`; `v1/worker/gpu_model_runner.py:5911` | FusedMoE can bind its callback before the real capturer is created during KV-cache initialization. The existing deferred binding must surround construction and generation. |
| `model_executor/layers/fused_moe/routed_experts_capturer.py:95`, `:250`, `:306` | Capturer/reader are singletons; returned arrays are copies from an instance-specific shared-memory buffer. Three fresh processes avoid singleton reuse. |
| `v1/engine/llm_engine.py:155`; `v1/executor/abstract.py:66` | `VLLM_ENABLE_V1_MULTIPROCESSING=0` plus `distributed_executor_backend="uni"` keeps the engine/worker in the process containing the binding. |
| `v1/worker/gpu_worker.py:296`; `v1/worker/utils.py:250` | Explicit KV bytes still trigger a dummy `profile_run`; startup still checks free memory against `ceil(total_memory * gpu_memory_utilization)`. Fixed KV is not a cap on all allocations. |
| `v1/worker/gpu_model_runner.py:4050`, `:4131`; `config/vllm.py:657` | Model loading records time; eager selects optimization level O0. Disabling request statistics does not remove all internal timings or initialization work. |
| `envs.py:537`, `:654`, `:742`, `:1068`; `usage/usage_lib.py:55` | Project-local cache/config roots, usage opt-out, and in-process execution must be configured before runtime imports. |

Also inspect and bind the current checkout sources
`adapters/vllm_capacity/{trace_collector,routed_capture_compat,trace_validation,model_inventory}.py`
and `scripts/eval/{verify_hf_metadata,resource_guard,run_matrix,run_manifest}.py`.
`routed_capture_compat.py` identifies its selective donor as
`37144843906b3bd71f3fbac1fecc6b5080d82b95`.

## Fixed request and runtime configuration

Freeze this concrete control in `inputs/request.json` before any arm. It is a
fixed token control, not a representative user prompt or a routing workload
distribution. The literal IDs avoid tokenization or chat-template variation:

```json
{
  "schema_version": 1,
  "prompt_id": 0,
  "sequence_id": 0,
  "prompt_source": "FIXED_TOKEN_CONTROL",
  "prompt_token_ids": [1000,1001,1002,1003,1004,1005,1006,1007,1008,1009,1010,1011,1012,1013,1014,1015,1016,1017,1018,1019,1020,1021,1022,1023,1024,1025,1026,1027,1028,1029,1030,1031],
  "output_len": 8
}
```

Validate exactly 32 non-boolean integer IDs within the verified vocabulary, one
prompt, one completion, and exactly eight generated IDs. Do not trim, pad,
retokenize, retry with new seeds, or silently substitute another prompt.

The worker uses the following explicit arguments. `checkpoint` is the exact
checkpoint view bound by the metadata receipt; `capture_enabled` is the sole
intentional inference-configuration difference between arms.

```python
llm_kwargs = dict(
    model=checkpoint, tokenizer=checkpoint,
    runner="generate", dtype="bfloat16", quantization=None,
    load_format="safetensors", safetensors_load_strategy="lazy",
    trust_remote_code=False, skip_tokenizer_init=True,
    tensor_parallel_size=1, pipeline_parallel_size=1, data_parallel_size=1,
    distributed_executor_backend="uni", enable_expert_parallel=False, seed=0,
    attention_config={"backend": "TRITON_ATTN"},
    enforce_eager=True, enable_prefix_caching=False,
    enable_chunked_prefill=False, async_scheduling=False,
    speculative_config=None, max_model_len=64,
    max_num_seqs=1, max_num_batched_tokens=64,
    kv_cache_memory_bytes=64 * 1024 * 1024,
    gpu_memory_utilization=0.9, swap_space=0, cpu_offload_gb=0,
    generation_config="vllm", disable_log_stats=True,
    use_tqdm_on_load=False,
    enable_return_routed_experts=capture_enabled,
)
sampling_kwargs = dict(
    n=1, temperature=0.0, top_p=1.0, top_k=-1, seed=0,
    max_tokens=8, min_tokens=8, ignore_eos=True,
    stop=None, stop_token_ids=None, detokenize=False,
    truncate_prompt_tokens=None, output_kind=RequestOutputKind.FINAL_ONLY,
)
returned = llm.generate(
    [{"prompt_token_ids": frozen_request["prompt_token_ids"]}],
    SamplingParams(**sampling_kwargs), use_tqdm=False,
)
```

Record both requested arguments and effective engine configuration before
generation: actual dtype, model/config class, checkpoint view, generation
settings, parallel/executor mode, scheduler limits, KV configuration, resolved
attention/MoE backend, compilation mode, and route-return flag. Reject a required
setting that is changed or unsupported; do not fall back to another dtype,
loader, executor, or checkpoint. Compare stable effective settings across arms;
instance IDs and private working paths are recorded differences, not equalities.
64 MiB bounds the requested KV allocation. It does not assert exact total VRAM
use or remove vLLM's initialization passes.

## Process and environment boundary

Use **GPU_EXCLUSIVE** for the complete triplet. The existing guard's shared mode
still rejects foreign compute processes, and the installed runtime records
initialization timings even with statistics disabled. Do not weaken the guard
or infer shared safety from low token count. Acquire the canonical project GPU
lock for the selected physical UUID and retain it through all arm exits and the
final resource check. vLLM's own exact free-memory check remains enabled. No
driver setting changes, foreign kills, server shutdowns, or GPU resets are part
of this runner.

Create three fresh owned processes in order: `native`, `capture`, `repeat`.
Reuse `run_matrix.run_child` rather than adding an environment-only bypass or
another process-group manager. Its acknowledgement pipe binds boot/PID/start
identity before execution, observes descendants, retains a leader identity while
checking exit, and limits termination to owned processes. Use a finite 900-second
deadline per arm and one-second resource checks. Do not overlap arms, and do not
start the next arm if a prior child or descendant remains.

Add a private keyword-only `bootstrap_no_site=False` to `run_child`. The HF
caller will explicitly select true, making the existing acknowledgement wrapper
start with `-S -B`; the default argv and scheduler CLI/registry stay unchanged.
Require a real boolean before any launch side effect. This suppresses automatic
site processing and bytecode writes in that intermediate interpreter, while
preserving its trusted project imports and exact ownership handshake. The final
HF worker will separately use `-I -S -B` with controlled import paths. These
flags are not a filesystem/network sandbox, and the bootstrap option alone does
not sanitize `PYTHONPATH`; HF's prepared environment already excludes it.
CPU subprocess fixtures must prove default compatibility, skipped startup hook,
unchanged child identity acknowledgement and no target execution after a failed
guard/status callback. No real inference package is imported by those controls.

Construct an allowlisted environment in the parent before either the worker or
any of its imports run. Do not repurpose HOME or CODEX_HOME. Use fresh arm-private
working directories below
`ROOT/results/tmp/hf-routing/<attempt-id>/<arm>/`; these remain outside the sealed
evidence bundle and their ownership/path is recorded. All arms start with empty,
separate runtime caches. Set:

```text
CUDA_VISIBLE_DEVICES=<selected physical GPU UUID>
VLLM_ENABLE_V1_MULTIPROCESSING=0
VLLM_USE_FLASHINFER_MOE_FP16=0
VLLM_USE_FLASHINFER_SAMPLER=0
VLLM_NO_USAGE_STATS=1
VLLM_DO_NOT_TRACK=1
DO_NOT_TRACK=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HUB_DISABLE_TELEMETRY=1
HF_HUB_DISABLE_IMPLICIT_TOKEN=1
PYTHONNOUSERSITE=1
PYTHONDONTWRITEBYTECODE=1
TOKENIZERS_PARALLELISM=false
XDG_CACHE_HOME=<arm-work>/cache
VLLM_CACHE_ROOT=<arm-work>/cache/vllm
VLLM_CONFIG_ROOT=<arm-work>/config/vllm
VLLM_ASSETS_CACHE=<arm-work>/cache/vllm/assets
HF_HOME=<arm-work>/cache/hf
HF_HUB_CACHE=<arm-work>/cache/hf/hub
HF_XET_CACHE=<arm-work>/cache/hf/xet
HF_MODULES_CACHE=<arm-work>/cache/hf/modules
TORCH_HOME=<arm-work>/cache/torch
TORCH_EXTENSIONS_DIR=<arm-work>/cache/torch-extensions
TORCHINDUCTOR_CACHE_DIR=<arm-work>/cache/inductor
TVM_FFI_CACHE_DIR=<arm-work>/cache/tvm-ffi
TRITON_HOME=<arm-work>/cache/triton-home
TRITON_CACHE_DIR=<arm-work>/cache/triton
TRITON_DUMP_DIR=<arm-work>/cache/triton-dump
TRITON_OVERRIDE_DIR=<arm-work>/cache/triton-override
FLASHINFER_WORKSPACE_BASE=<arm-work>/cache/flashinfer-workspace
FLASHINFER_DISABLE_JIT=1
FLASHINFER_LOGLEVEL=0
FLASHINFER_LOGDEST=stderr
FLASHINFER_AUTOTUNER_LOAD_FROM_FILE=0
CUDA_CACHE_PATH=<arm-work>/cache/cuda
TMPDIR=<arm-work>/tmp
```

The installed huggingface_hub `constants.py:137–171` and transformers
`utils/hub.py:92` establish the HF roots/offline settings; torch's
`_inductor/runtime/cache_dir_utils.py:15–34` establishes the compiler cache roots.
The backend/cache closure below is also required: HF offline flags alone do not
disable FlashInfer's separate artifact downloader.
Do not inherit LD_PRELOAD, LD_AUDIT, PYTHONPATH, HF credentials, or interception,
profiling, distributed-launch, and compiler overrides. Freeze the small permitted
platform environment and its exact values. Reject unexpected import origins or
pre-imported vLLM/torch in the worker's real path. The local checkpoint is passed
by path; offline cache misses fail instead of downloading files. No cache is
redirected to the existing owner's cache. Checkpoint reads by the model loader
occur in the already authorized real execution only after the implementation,
review, and resource gates pass; this planning task performs none.

### Backend selection closes the FlashInfer downloader path

Use the explicit Triton attention configuration above, Triton unquantized MoE,
and the native sampler in all three arms. These are supported installed choices,
not an installed-library patch:

- `vllm/config/attention.py:17,65` parses `backend="TRITON_ATTN"`;
  `vllm/platforms/cuda.py:313–329` validates that explicit choice and raises if
  invalid instead of trying an automatic alternative. The installed
  `v1/attention/backends/triton_attn.py:255–343` supports BF16, head sizes at least
  32 (the verified target has 128), and this CUDA capability.
- `vllm/model_executor/layers/fused_moe/oracle/unquantized.py:50–98` selects
  `UnquantizedMoeBackend.TRITON` on CUDA with expert parallelism disabled and
  `VLLM_USE_FLASHINFER_MOE_FP16=0`. That switch also covers this unquantized BF16
  model. Bind the selected method/backend on every MoE layer; do not infer it
  from the environment string alone.
- `vllm/v1/sample/ops/topk_topp_sampler.py:35–59` selects its native path when
  `VLLM_USE_FLASHINFER_SAMPLER=0`. No FlashInfer attention, sampler, quantized
  GEMM, or MoE execution is allowed in this fixed BF16/no-EP/no-speculation unit.
  A changed or unsupported choice fails the arm; there is no auto fallback.

FlashInfer can still be **imported**: the MoE selector checks
`has_flashinfer_cutlass_fused_moe()` before evaluating the disabled flag.
Installed `flashinfer/jit/env.py:51–55` defaults its workspace to Path.home(),
and `jit/core.py:18–19,46–51` makes directories and creates its JIT log at import.
Setting only XDG_CACHE_HOME is insufficient. The workspace variable above moves
that log, generated source, JIT cache, and JIT locks under the arm directory.
Require the installed `flashinfer/data/csrc` directory to already exist before
import, since core.py also calls mkdir on that packaged source directory. Never
create a missing installed source directory to repair the environment.

Installed `flashinfer/jit/env.py:64–93` prefers the **installed flashinfer_cubin
package** over `FLASHINFER_CUBIN_DIR`; its
`flashinfer_cubin/__init__.py:23–29` returns the package's own `cubins/` path.
Therefore this plan does not pretend that setting FLASHINFER_CUBIN_DIR relocates
the effective cubin directory. Read-only source inspection found matching
FlashInfer and cubin build versions `0.6.1`; `flashinfer_jit_cache` and the fallback
`flashinfer/data/aot` were absent, while packaged csrc/cubins directories existed.
Freeze those package identities and path observations. Recheck them before each
arm; a version mismatch, unexpected AOT/JIT-cache package, or missing csrc/cubin
package fails preflight. Preserve version and cubin checksum checks: never set
FLASHINFER_DISABLE_VERSION_CHECK or FLASHINFER_CUBIN_CHECKSUM_DISABLED.

The installed downloader is not read-only on a cache miss:
`flashinfer/jit/cubin_loader.py:203–220` returns a valid packaged cubin directly,
but missing/corrupt data calls `download_file`; lines 81–124 create a destination
`.lock`, copy/download to a temporary file, and replace the destination. With the
installed package precedence, that destination can be site-packages. HF offline
variables do not affect these requests. Even
`flashinfer/artifacts.py:get_artifacts_status` calls `get_subdir_file_list`, which
fetches checksums and URL listings; do not invoke artifact discovery, status,
prefetch, downloader, or availability CLI commands as an offline preflight.

For this selected backend, the cubin execution/artifact path is unused. The
installed `vllm/utils/flashinfer.py:246–258` availability check returns directly
when the verified cubin package is present, so it need not probe NVIDIA's server.
`FLASHINFER_DISABLE_JIT=1` additionally makes an unexpected FlashInfer build fail
at `jit/core.py:287–295`; it is not a general offline/download control. Do not
redirect the repository URL, hide the package, bypass checks, monkeypatch the
downloader, install artifacts, or accept package-cache writes as a fallback.
If the pinned backend/source path unexpectedly requires FlashInfer artifacts,
retain a failed arm and diagnose that incompatibility before any new backend
choice. This is an audited runtime configuration, not an OS network sandbox or
a promise about arbitrary future package code.

Other actual import/backend cache paths also need the environment above:

| Installed source | Required boundary |
| --- | --- |
| `torch/utils/cpp_extension.py:2513–2534` | TORCH_EXTENSIONS_DIR controls extension output; TORCH_HOME alone does not. |
| `tvm_ffi/_optional_torch_c_dlpack.py:114–141`; `tvm_ffi/cpp/extension.py:493` | TVM_FFI_CACHE_DIR controls an optional torch DLPack extension compiled during import and generic TVM-FFI extension builds. Default is another home cache. |
| `triton/knobs.py:341–355` | TRITON_HOME plus cache/dump/override roots are private; inherited remote-cache-manager and kernel-override/dump flags are excluded. |
| `flashinfer/api_logging.py:47–61` | FLASHINFER_LOGLEVEL=0 keeps API/tensor dumping off, LOGDEST=stderr confines logs, and inherited dump options are excluded. |
| `flashinfer/autotuner.py:390` | Disable its file-config opt-in; no FlashInfer autotuning or generated tuning-file persistence is part of this run. |
| `vllm/model_executor/layers/fused_moe/fused_moe.py:1033–1084` | Existing tuned configuration JSON is read-only; bind the selected packaged file or its absence. Exclude inherited VLLM_TUNED_CONFIG_FOLDER. The fallback uses installed defaults, not a tuning/downloading command. |

Include these selector/cache/downloader sources and package build metadata in the
runtime-source manifest. Before LLM construction, check resolved cache paths
after permitted imports against the prepared paths and record the effective
FlashInfer package/csrc/cubin/AOT locations separately as read-only inputs. Their
existence grants no permission to write them. Backend identity and no unexpected
artifact requirement are mandatory runtime checks, not optional diagnostics.

The installed capturer also creates named POSIX shared memory. Record its
instance/rank identity and use its own reader/capturer cleanup before worker
exit. TMPDIR places its lock files in the private work directory; POSIX shared
memory itself is an explicit runtime-managed exception to project-local files.
Never remove shared-memory objects by a broad prefix or touch foreign objects.

Before construction, an additional process-local ownership scope must replace
only the capturer module's `shared_memory` binding with a forwarding proxy.
The installed helper catches `FileExistsError` and can unlink a differently sized
preexisting object; post-construction name checks would be too late. The proxy
allows one exclusive creator, converts a collision to a distinct `RuntimeError`,
and allows one reader attachment only to that successful creator's exact name.
Keep the original class and return its concrete handles. Reader construction
uses the installed default `create=False,size=0`; the creator requires a positive
bounded size. Validate the numeric instance/rank-zero name and reconcile it with
the constructed configuration. Check the imported temporary/lock prefix against
the fresh private arm directory before installing the scope, because the native
lock-file open precedes the shared-memory constructor call.

Keep each pending original-class object before calling its initializer, so an
exception after allocation but before return does not lose the handle. Record
successful exclusive creation from its live descriptor identity, never from an
attempted name alone. Retain handles independently of singleton assignment and
reconcile creator/reader descriptor identities and the exact namespace entry.
Attempt reader, capturer and engine cleanup independently; verify saved handles
closed and the exact owned name absent. Handle partial initialization using only
recorded owned handles. Restore the module binding in `finally` only while it
still points to this scope. No installed file or global multiprocessing module
is patched. CPU controls must preserve preexisting same/different-size segments,
reject wrong-name/early/duplicate calls, and cover partial construction and
cleanup failures. This process-local ownership scope detects ordinary namespace
replacement; it does not claim atomic identity-checked unlink or protection
against hostile concurrent name replacement, including resource-tracker cleanup.

### Selected MoE tuning inputs and passive runtime observation

The frozen `fused_moe.py:1018–1084,1292–1323` selects the filename from
`E, _, N = w2_shape`, device name with spaces replaced by underscores, and the
quantization selector. The frozen unquantized weight constructor and Qwen model
derive `w13=[E,2*moe_intermediate_size,hidden_size]` and
`w2=[E,hidden_size,moe_intermediate_size]` for this TP=1 CUDA control. BF16 with
the unquantized config yields no dtype suffix (`config.py:40–73,373–386`).
Derive these dimensions from the verified config and reconcile actual loaded
weight shapes/dtypes before generation. Bind the worker's observed device name;
a filename derived from a parent-declared name alone is not device evidence.

Freeze only the exact selected packaged JSON or its absence, including its
canonical directory identity. Do not enumerate tuning files or invoke tuning,
kernel compilation, `get_moe_configs`, or its cache-clear method to gather this
observation. Bound reads, preserve exact bytes, and recheck before/after arms.
Runtime-source snapshots need an explicit finite extension for
`fused_moe/__init__.py` and `layers/batch_invariant.py`; preserve validation of
the existing 131-artifact snapshot as a historical input version. Supplemental
source bytes are already recorded in `tuning-source-audit-attempt-001/` and
`tuning-source-audit-attempt-002/` under the HF runner gold directory; neither
audit executed an inference import. The explicit 133-source extension and exact
selected-file/absence freezer are now committed (`cf37966`, `351cd8a`); the real
input attempt records absence, with the device name declared from the dated
probe. Actual loaded-state observation remains the next separate unit.

The additional sources expose mutable bypass state: `fused_moe._config` must
remain `None`, and `batch_invariant.VLLM_BATCH_INVARIANT` must be exactly `False`.
Check their already-loaded module origins and direct fields, the bound function
aliases used by `fused_moe.py`, and the absent user-config environment override.
Record the selected-file/default-config boundary separately from effective
kernel configuration. These checks do not authenticate native binaries or
claim to inspect the private contents of a Python LRU cache.

Implement that unit privately in `scripts/eval/hf_moe_tuning_runtime.py`, with
CPU fixtures and no CLI or inference imports. Retain the fixed already-loaded
module objects and their frozen origins before construction; require package
`_config is None`, batch-invariant state exactly false, and the actual Python
function aliases/global dictionaries used by `try_get_optimal_moe_config` and
`get_moe_configs`. Never call either configuration selector or its cache APIs.
Retain the installed LRU wrapper and underlying function. Construction normally
wraps and prepopulates `vllm.envs.__getattr__`; allow that expected transition
without reading, clearing or claiming authentication of private cache contents.

After the existing runtime contract observer succeeds, traverse its same exact
model/worker/MoE chain. Check only metadata of each actual BF16 Parameter:
`w13=[E,2I,H]`, `w2=[E,H,I]`, selected CUDA device index, and receipt-derived
dimensions. Inspect `method.moe_quant_config` directly and require identity
with the retained unquantized constant and `kernel.fused_experts.quant_config`.
Do not read the layer's `moe_quant_config` property: it may initialize state.
Require the raw six fields of each `_a1/_a2/_w1/_w2` descriptor to be `None`,
and reconcile the stored no-bias, gated activation and TP1 MoE configuration.
Recheck retained modules, aliases, overrides and observed weight/config bindings
before returning detached primitives. No tensor payload access, platform/GPU
query, tuner, compilation, download or cache creation is part of this observer.

The selected device name is still a declared input here; the later guarded
worker authenticates the actual device. Keep effective kernel configuration,
private cache contents, native binary authentication and scientific validation
explicitly unproved. Injected objects always produce MOCK. Cover hostile or
mutating fixture properties, wrong shapes/dtypes/devices, changed override and
alias state, quantized descriptors, and module/config rebinding with semantic
RED/GREEN tests. Integrate this observer into the private arm only after both
independent reviews; original-input rechecks and guarded parent publication stay
separate.

## Worker return and capture contract

`native` uses `enable_return_routed_experts=False` with no compatibility binding.
`capture` and `repeat` use the flag set to true and explicitly install
`install_vllm_routed_experts_deferred_binding()` before constructing LLM. Keep
the binding context alive through generation and close it in `finally`; do not
edit the installed file or patch a process other than the owned worker. Record
the restored state and counters as diagnostics. Counters alone cannot prove
callback coverage: calls to an already initialized real singleton bypass them.

The worker must take the returned objects directly from this one `generate`
call. It accepts no caller-supplied routes in the real CLI and no alternate LLM
factory/runner via configuration. Preserve, without coercing IDs:

- returned request ID; full returned prompt IDs and finished state;
- output index, full output IDs, finish/stop reason, and cached-token count;
- actual returned route dtype, shape, and integer array values for capture/repeat;
- exact input/config/metadata binding hashes and worker boot/PID/start identity.

Require a nonempty string request ID, exactly one finished request and completion
with index zero, and the expected prompt/output lengths. Native must have no
returned routes. Derive the expected shape `[32 + 8 - 1, L, k]` and ID bound
`0 <= expert_id < E` from the verified receipt/config/inventory. Require positive
non-boolean integers and `k <= E`; never supply defaults for missing fields.
For the observed receipt the shape is `[39,48,8]`. Capture/repeat must have
integer, non-boolean routes; reject missing, float, object, shortened, extra, duplicate-top-k, or
out-of-range values. Save a copied NumPy array with `allow_pickle=False` and record
its original dtype/shape; do not cast bad floats or regenerate missing rows.
Retain request IDs verbatim per arm; cross-arm joining uses the frozen prompt
ordinal, not a fabricated common vLLM request ID.

Feed exactly those captured arrays into `JsonlTraceCollector`, using the unchanged
`ModelInventory` constructed from the frozen donor. `TraceRequest` uses the real
returned request ID and frozen prompt/sequence ordinals. Compute counts as
`39*L` layer events, `32*L` prefill events, `7*L` decode events, and `39*L*k`
expert accesses. Tensor counts/bytes come from the selected inventory descriptors;
this verified Qwen inventory has gate/up/down, hence three tensors per expert.
Do not infer these constants from the checkpoint directory's model name.
The resulting counts for the observed target are:

| Quantity | Expected |
| --- | ---: |
| Returned route positions | 32 prefill + 7 decode = 39 |
| Layer events | 39 × 48 = 1,872 |
| Prefill / decode layer events | 1,536 / 336 |
| Expert accesses | 1,872 × 8 = 14,976 |
| Tensor accesses | 14,976 × 3 = 44,928 |

An event's expert bytes describe logical tensor accesses, not physical transfer
bytes. Preserve the collector's `post-request JSONL materialization` timestamp
semantics, null GPU timestamp/compute gap, and unavailable top-k weights. Do not
derive an arrival schedule or a compute interval from host serialization time.
The eighth output token gets no invented decode-forward route.

## Metadata and runtime identity binding

Freeze the exact complete metadata bundle under `inputs/metadata-refresh/`,
including donor.json, normalized tensors, receipt, and COMPLETE marker. Preserve
its bytes and validate that frozen copy. Require the declared checkpoint to
equal its bound checkpoint view; reject GGUF, wrong dtype/model geometry, missing
publication, or mismatched donor. The three arm bindings must contain:

```text
metadata receipt SHA-256 and COMPLETE marker SHA-256
metadata_identity_sha256 and observation_identity_sha256
legacy_inventory_sha256 and historical ModelFingerprint
request SHA-256 and common configuration SHA-256
worker/parent/collector/compatibility/validator source hashes
interpreter identity/hash and installed-runtime source manifest SHA-256
GPU UUID, requested arm, actual child identity, and exact argv/environment
```

Call `check_current_inputs` on the frozen metadata bundle immediately before and
after each arm, and again before publishing the outer marker. Save all bounded
check reports; compare complete bound identities rather than only their status
strings. The helper itself checks the detailed source identities even though its
return object contains fewer fields than the receipt. Recheck frozen receipt,
marker, and donor bytes around each helper call so a different valid bundle
cannot be substituted. Freeze and validate from the same byte snapshots where
possible; reject changes during validation/publication. No weight-payload rehash
or legacy schema mutation is added by the runner.

Hash a declared, finite runtime-source set before any arm and verify identities
again after each arm: the source files in the grounding table, Qwen3 MoE model
implementation, model loader and weight iterator, and the actual imported helper
modules. Freeze the exact source manifest plus installed package metadata/RECORD
for vLLM, torch, transformers, safetensors, NumPy, and Triton. Record actual module
`__file__` paths in each worker and reject shadow imports. Hash the resolved
interpreter executable using bounded streaming reads; record package versions,
Python version, git commit/dirty-patch hash, and all wrapper sources. Source and
tool hashing limits are separate from the metadata helper's checkpoint read
limits: cap source/distribution metadata at 16 MiB/file and 128 MiB total,
and the interpreter at 512 MiB, read in 1 MiB chunks. Fail on exceeding a cap.
A package RECORD
entry is advertised build metadata, not a fresh hash of every installed binary.
Declare this source-manifest scope instead of claiming complete runtime binary
authentication. Do not hash model shards to fill that gap.

The resulting binding can establish that this trusted owned worker obtained the
array from the selected installed API under the recorded inputs. It does not
cryptographically attest GPU execution or newly authenticate checkpoint payload.
Keep `weight_payload_rehashed=false`, the historical-only payload identity
boundary, and `scientific_validation_passed=false` visible in every outer report.

## Standalone artifact lifecycle and independent validation

New output directories must resolve inside the active checkout and outside
`results/runs`, the checkpoint/cache trees, all input bundles, and their symlink
aliases. Refuse an existing destination before acquiring resources or launching
workers. Apply this rule to all standalone runs; injected CPU paths additionally
force TEST_ONLY/MOCK before any output creation. A caller's `test_only=False`
cannot override evidence from a test metadata receipt or injected dependency.

Suggested real artifact root is
`ROOT/results/artifacts/hf-routing/<new-attempt-id>/`. Keep test evidence under
`ROOT/results/gold/hf-routing-runner/`; its location confers no scientific status.
The sealed artifact tree is:

```text
inputs/request.json
inputs/config.json
inputs/metadata-refresh/<exact complete frozen bundle>
runtime-sources.json
runtime-source/<declared frozen source and distribution metadata files>
native/{raw.return.json,summary.json,worker.json,stdout.log,stderr.log}
capture/{raw.return.json,raw.routes.npy,raw.trace.jsonl,summary.json,worker.json,stdout.log,stderr.log}
repeat/{raw.return.json,raw.routes.npy,raw.trace.jsonl,summary.json,worker.json,stdout.log,stderr.log}
metadata-checks/<pre/post arm and final checks>.json
raw.gpu.jsonl
status.json
validation.json
manifest.json
COMPLETE.json
```

Before launch, write a durable prepared manifest with exact argv, bindings, and
declared artifact names. Retain state transitions, child identities, return codes,
raw stdout/stderr, and resource snapshots. The worker's raw return record is
published before deriving its trace/summary. Each summary is assembled in the
existing validator format: `protocol`, `requests` with actual token IDs, and
`trace` containing the unmodified collector summary for capture/repeat.
Cap raw-return and NumPy files at 1 MiB each, manifest/report files at 8 MiB each,
and each JSONL trace or stdout/stderr log at 64 MiB; check log growth from the
parent's existing lifecycle callback and fail the owned arm on overflow. The
metadata bundle retains the metadata verifier's own limits. Reject unexpected
NumPy element counts before allocating/decoding an advertised large shape.

After all workers have exited, a separate public `validate_capture(out)` must:

1. Verify confined regular-file artifacts, complete exact inventories, hashes,
   strict bounded JSON/NumPy decoding, and the frozen metadata bundle. Explicitly
   check manifest/status/marker files: the shared inventory helper convention
   excludes manifest.json and status.json and cannot supply this check alone.
2. Recompute all three request/token identities from raw return snapshots,
   reconcile them with summaries, and compare native/capture/repeat token IDs.
3. Recompute captured expert IDs and tensor descriptors from raw arrays plus the
   frozen inventory, then compare every JSONL event, ordering, count, and phase.
   Call existing `validate_trace` as an additional tensor-consistency check.
4. Compare capture versus repeat routes exactly; recompute the stable route
   identity rather than comparing supplied route hashes alone.
5. Reconcile requested/effective configs, raw worker/process records, source
   identities, all pre/post metadata checks, clean child exits, and guard records.
   Report `capture_origin_binding` separately from `route_token_consistency`.
6. Enforce evidence propagation. Injected runners, readers, clocks, probes,
   arrays, factories, or TEST_ONLY metadata yield outer `provenance=MOCK` and
   `capture_origin_binding=TEST_ONLY`; arbitrary valid arrays never yield a real
   origin claim. A real owned run can report `OWNED_VLLM_RETURN_BOUND`, scoped to
   returned routing metadata, while scientific/hardware validation stays false.

This validator is frozen-artifact-only and launches neither a model nor a GPU
probe. Missing controls yield INCOMPLETE; any mismatch yields FAIL. A schema or
consistency PASS alone cannot establish origin, change the collector's raw
UNVALIDATED evidence, generate REAL_QWEN_TRACE gold, or authorize registry gates.
Do not report latency, tokens/sec, GPU timing, or transferred bytes. Raw runtime
logs can contain initialization times; those remain unexported diagnostics.

Publish a provisional final manifest/report, perform the same internal validator
without a completion marker, recheck current metadata and frozen input/source
identities, then publish an exclusive fsynced `COMPLETE.json` through a temporary
file plus hard link. The marker binds exact manifest and validation-report bytes.
Public validation requires that marker and exact final inventory. Never expose
success from provisional documents or overwrite any completed output.

Failure/interruption/timeout/contamination keeps a durable failure manifest,
status and raw diagnostics, without COMPLETE or DONE. Stop only verified owned
processes through the existing lifecycle; do not seal success while ownership is
uncertain. A new attempt uses a new directory. This private artifact lifecycle
does not extend scheduler state enums, emit result CSV rows, or create gate
receipts. Environmental blockers remain blockers, not successful controls.

## Bounded implementation file map

| File | Change and responsibility |
| --- | --- |
| `scripts/eval/run_hf_routing.py` (new) | `make_plan(metadata_refresh, out, gpu_uuid)` validates/fixes the request and settings; `execute(plan, *, child_runner=None, gpu_probe=None)` freezes artifacts and owns three arm processes; `validate_capture(out)` independently checks the finished frozen bundle. Injections always force MOCK. CLI has prepare/run/validate operations; prepare imports no GPU runtime and launches nothing. |
| `adapters/vllm_capacity/hf_routing_worker.py` (new) | Private worker CLI and `run_arm(plan, arm, out)`; verify frozen bindings/environment first, lazy runtime imports, native/compat contexts, one generate call, strict returned-object capture, existing collector, cleanup and durable raw results. No real CLI option supplies a route array or replaces the runtime factory. |
| `scripts/eval/test_run_hf_routing.py` (new) | Metadata fixtures, owned CPU child fixtures, output isolation, artifact publication/race, raw reconciliation, provenance and triplet lifecycle regressions. |
| `adapters/vllm_capacity/tests/test_hf_routing_worker.py` (new) | Fake imported runtime modules exercise kwargs/import order/context restoration and strict returned-object validation. Fake modules never import CUDA or open checkpoint shards. |

Reuse the existing helpers listed above without changing their schema or public
semantics. No CMake, scheduler registry, exporter, renderer, profile-loader,
interception, or installed vLLM changes are required by this unit. Implementation
and review precede the already authorized bounded real execution. Proceed when
those gates and the exclusive-resource preflight pass; do not add another human
approval step.

## RED-to-GREEN tasks and semantic controls

- [ ] Add failing tests for `make_plan` and output isolation: unpublished/wrong
  receipt, different donor/checkpoint/BF16 geometry, symlink/input/formal-root
  destinations, existing outputs, malformed 31/33-token or boolean-token input.
  Verify failures occur before mkdir, runtime import, guard acquisition, or child
  acknowledgement as applicable. Then implement only preparation/freezing.
- [ ] Add an import-order fake that records environment values at its first
  import and returns a fake LLM. Have its FlashInfer/torch-extension/TVM-FFI cache
  stubs resolve and create a sentinel immediately at import; RED must expose the
  omitted FLASHINFER_WORKSPACE_BASE/TORCH_EXTENSIONS_DIR/TVM_FFI_CACHE_DIR paths,
  and GREEN must place every sentinel under the private arm work directory.
  Test inherited conflicting roots, remote Triton cache-manager overrides, and
  FlashInfer dump/version/checksum-bypass settings; the prepared allowlist must
  replace or reject them before import without touching their original paths.
  Require exact kwargs above, one generate call,
  use_tqdm false, true capture flags only on capture/repeat, and binding installed
  before fake construction and restored after both return and exception. Add a
  separate negative for multiprocess/Ray or a different import path. Implement
  the worker with lazy imports and a test-only factory inaccessible from real CLI.
- [ ] Add a cache-priority fixture with an installed cubin package and a conflicting
  FLASHINFER_CUBIN_DIR. The resolved package path must be recorded as read-only,
  never reported as redirected. Missing csrc/cubin package, version mismatch, or
  an unbound AOT/JIT-cache package must fail before model construction. Stub the
  artifact downloader/network entry points to raise if called; selected Triton
  attention, CUDA no-EP unquantized Triton MoE, and native sampling must reach no
  such entry point. Simulate a missing/corrupt cubin and backend fallback: reject
  the unsupported path without creating a package `.lock` or attempting a fetch.
  Include FlashInfer capability probing in the import-order fixture even though
  its execution backend is disabled. Do not invoke the real artifact-status CLI
  to supply test data, because its source performs network/file operations.
- [ ] Use small explicit TEST_ONLY metadata geometry for portable CPU worker and
  parent tests; derive L/E/k and route expectations from that fixture's verified
  receipt. A tiny-vocabulary fixture supplies its own valid 32-token control and
  remains MOCK; it cannot replace the real request artifact. Check e.g. L=2,E=2,
  k=2 gives 78 layer events, 64/14 prefill/decode events, 156 expert accesses and
  468 gate/up/down tensor accesses. Missing or mismatched receipt fields fail
  rather than falling back to 48/128/8. No real weight files are needed.
- [ ] Use a full-dimension CPU route fixture
  `ids[t,l,k] = (t + 3*l + k) % 128` for t=0..38, l=0..47, k=0..7. Assert 1,872
  events, 1,536/336 phase counts, 14,976 expert and 44,928 tensor accesses; inspect
  first/last token-layer joins and real request IDs. Reject 38/40 positions,
  wrong layers/k, missing routes, fractions/bools, duplicate IDs, negative/128 IDs,
  duplicate output objects, unexpected native routes and wrong output length.
- [ ] Add three distinct owned CPU subprocess fixtures to prove sequential fresh
  boot/PID/start identities and stable inputs. Change one generated token in
  native or repeat, then one selected expert only in repeat. Each must fail the
  correct consistency check. A complete matching fixture must remain MOCK even
  with user-requested production labels. Fake success dictionaries must not bypass
  independent raw reconstruction.
- [ ] Mutate the checkpoint observation between arms through metadata-only test
  fixtures; substitute another internally valid receipt/donor; restore mtime after
  content mutation; alter a runtime source between spawn and completion. Reject
  before starting a subsequent arm or publishing success. Count helper reads and
  forbid payload-byte reads in parent preparation/checking paths.
- [ ] Reseal hashes after altering a trace expert, raw array, token summary,
  effective dtype/route flag, worker identity, metadata-check result or phase
  count. Independent validation must reject semantic inconsistencies even with
  internally consistent artifact hashes. CPU fixture origin cannot be promoted by
  editing a single provenance field; all contributing source labels reconcile.
- [ ] Reproduce output/receipt races and lifecycle failures: injected failure on
  second arm, nonzero child, signal, deadline, foreign GPU appearing in fake
  periodic/end snapshots, live descendant, and output alias traversal. Assert
  no third launch after failure, failure diagnostics retained, no marker/DONE,
  and no foreign kill. Test a consumer invoked at provisional-manifest write:
  it must reject until exclusive final marker publication. Test missing/extra
  artifact and marker, symlink files, stale digests, and no overwrite.
- [ ] Run the new CPU suites and existing collector/compatibility controls, saving
  initial meaningful RED and targeted GREEN logs under
  `results/gold/hf-routing-runner/`. Example focused commands from checkout root:

  ```text
  python -m unittest discover -s scripts/eval -p test_run_hf_routing.py -v
  python -m unittest discover -s adapters/vllm_capacity/tests -p test_hf_routing_worker.py -v
  python -m unittest discover -s adapters/vllm_capacity/tests -p test_trace_collector.py -v
  python -m unittest discover -s adapters/vllm_capacity/tests -p test_routed_capture_compat.py -v
  ```

- [ ] Obtain separate spec and quality review of the frozen implementation. A
  passing CPU suite establishes adapter behavior only. Do not add a GPU test to
  normal unittest discovery or launch real weights as an automatic test step.

## Later use and remaining gates

The first authorized real execution is exactly this triplet, after a successful
current metadata refresh and a fresh exclusive-resource preflight. Preserve a
failure if greedy token or route repetition differs; do not relax comparison or
retry until a convenient match occurs. A successful artifact bundle supports a
bounded statement about one real HF routing control under a recorded runtime.
Its host serialization times cannot drive a GPU timing analysis or a causal
decode DAG.

A subsequent **HF input adapter** is required to bind this trace, its unchanged
BF16 donor, refreshed metadata/observation identities, and an explicit residency
budget into capacity/projection inputs. The existing GGUF F16 inventory, layout,
fingerprint, tokenizer assumptions, and budget evidence cannot be reused as HF
identity merely because expert dimensions or nominal precision sizes match.
No projection, profile calibration, storage pairing, production TMA issue/wait
proof, formal GOLD/VALIDATED_MODEL gate, or registry row follows automatically
from this runner. Those remain separately reviewed consumers and evidence gates.

## Worker protocol checkpoint

Commit `2ba779a` adds only strict protocol/config/environment and returned-object
serialization helpers, with 10 CPU controls and independent spec/quality PASS.
The array owner mutation regression proves that validation uses the exact private
copy later saved, not a mutable returned owner. Evidence is under
`results/gold/hf-routing-runner/worker-protocol-attempt-001/`.
Runtime imports/cache observations, backend inspection, model construction,
generation, exact owned shared-memory cleanup and parent orchestration remain
unimplemented at that protocol checkpoint. The additive helper checkpoints below
do not enable an executable worker entry point.

Commit `059fafc` adds the private `hf_runtime_contract.observe_runtime` helper.
It inspects the constructed in-process engine/executor/worker/runner aliases,
raw execution model, resolved configuration, each attention/MoE backend and
capture-callback configuration, native sampler binding, and actual KV allocation.
KV usable capacity excludes the installed BlockPool's reserved null block.
Mutable settings are copied into the report. Ten CPU tests and separate spec/
quality reviews pass; counterexamples are retained under
`results/gold/hf-routing-runner/runtime-contract-attempt-001/`.
This closes the observation helper only. The worker does not yet import or call
it, and it supplies configuration evidence rather than execution/capture proof.

Commit `f150d18` adds private `hf_owned_routes.OwnedRouteMemory`, the module-local
ownership scope described above, with 11 CPU namespace/descriptor controls and
separate spec/quality PASS. No actual POSIX shared memory was allocated by these
tests. Missing native cleanup methods, descriptor-identity failure, and cleanup
interrupts preserve failed status while independent owned cleanup is attempted.
An unexpected incomplete teardown cannot report closed. Evidence:
`results/gold/hf-routing-runner/owned-memory-attempt-001/`.
The combined adapter-directory CPU phase passes 57/57 tests in 0.236 s
(0.416 s process wall time), recorded in
`results/gold/hf-routing-runner/helpers-phase-attempt-001/`.
Runtime source/cache preflight, controlled imports, model construction/generation,
collector publication and parent triplet validation remain pending.

## Runtime source checkpoint

Commit `e46147f` adds the finite read-only `hf_runtime_sources` helper. It freezes
88 selected package Python files, three stdlib files, and four distribution
metadata files for each of ten pinned packages (131 artifacts). Per-file and
aggregate source limits are16/128 MiB; directory discovery stops at entry4097,
and reads use at most1 MiB chunks. The separately bounded interpreter identity
is not counted as source metadata. Duplicate .dist-info/.egg-info discovery
matches the installed Python first-hyphen convention. Frozen validation opens
no original source, joins common ancestor identities and recomputes all selected
artifact and metadata claims; explicit source/interpreter overrides remain MOCK.
It never follows RECORD targets or authenticates every native runtime binary.

Fifteen source controls and the27-test shared metadata compatibility suite pass,
with independent specification and quality PASS. Evidence is in
`results/gold/hf-routing-runner/runtime-sources-attempt-001/`. The original
combined scan/ancestor RED contained an incomplete fake directory entry; only
its ancestor failure is meaningful. The separately labelled reconstruction of
the pre-fix enumeration algorithm demonstrates5000 consumed entries versus the
4097 stop contract; the corrected fixture and current implementation pass.

The real selected-source freeze at2026-09-05T17:46:01Z took0.289s, saved
7,961,389 source/metadata bytes and rechecked unchanged originals. Manifest SHA256:
`4259d8c28a5a1b104bf6caa809c6599adc3c669505a511b45cfca719989d42d3`.
The interpreter SHA256 is
`2e963eaa2dd6751b97f96c1532ad79fb4a318b99f7d9c0e112f0a943f5a814ce`.
Artifacts and exact implementation hashes are in
`results/gold/hf-routing-runner/runtime-sources-real-attempt-001/`.
No inference package, model, GPU or storage payload was used. This is a selected
runtime-input snapshot, not worker execution or capture-origin evidence. Runtime
cache/import observations, selected MoE tuning-file identity, actual worker
construction/generation/trace cleanup and the parent triplet remain pending.

## Passive runtime import/cache checkpoint

Commit `c12d3b0` adds `hf_runtime_imports.observe_runtime_imports`. It validates
existing selected module origins against the frozen source buffers, observes
runtime versions and exact cache/log/temp paths, and requires the complete
FlashInfer import group. It never imports a missing runtime or invokes CUDA,
discovery, artifact status, download, compiler or cache-creation APIs.
Explicit module/environment injections and fixture snapshots remain MOCK.
The supplied device capability is a separate owned-worker input, not a device
observation authenticated by this helper.

The helper reads `tempfile.tempdir`, Torch hub override and usage-status cache
without invoking their potentially stateful helpers. Triton's import-capable
manager descriptors are never read: existing override state and the already
loaded native `getenv` reader must show no manager override. This also catches
a live C-environment setting omitted from an injected/Python environment view.
Its selected path/bool descriptors remain finite. FlashInfer's private logfile
must match both the declared filename and the active open stream; package cubin
paths remain read-only inputs, and an inactive relative dump path is not claimed
to be a private cache. A null optional DLPack library has an undetermined cause.

Independent specification/quality reviews pass. Eleven CPU tests retain REDs
for module substitution, redirected/absent/closed log streams and the live
manager environment. The source/import phase passes26/26 tests in1.528s; existing
adapter tests pass57/57 in0.455s (combined83 tests, process wall1.695+0.639s).
Evidence: `results/gold/hf-routing-runner/runtime-imports-attempt-001/` and
`results/gold/hf-routing-runner/imports-phase-attempt-001/`.
No real runtime import, model construction/generation, POSIX shared-memory
allocation or GPU acquisition was performed. Pre-import isolation/guards,
selected MoE tuning inputs, worker execution/cleanup, parent triplet ownership
and independent raw/trace validation remain pending.
