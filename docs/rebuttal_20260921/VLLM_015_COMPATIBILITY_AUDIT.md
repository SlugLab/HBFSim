# vLLM 0.15.1 Host Compatibility Audit

Date: 2026-09-21
Scope: read-only source/API audit for the restored giga host route. No build or GPU run was performed.

## Route under review

The historical host environment being restored is Python 3.13.9, vLLM 0.15.1,
PyTorch 2.9.1+cu128, Triton 3.5.1, FlashInfer 0.6.1, and the CUDA 13.0.88
native toolchain. The current adapter sources were compared with the official
vLLM v0.15.1 and Triton v3.5.1 tagged source.

## Required minimal changes

1. Do not pass `moe_backend` to vLLM 0.15.1. Its `EngineArgs` has no such
   field, so the current `moe_backend=args.moe_backend` forwarding in
   `adapters/vllm/run.py` is an incompatible later-version assumption.
   Omit the keyword on 0.15.1, preferably by testing the installed
   `EngineArgs` signature rather than comparing version strings.
2. Keep FlashInfer MoE opt-ins disabled for this Triton experiment. In
   v0.15.1, the `VLLM_USE_FLASHINFER_MOE_*` variables default false.
   Do not use the attention backend setting as proof of the MoE backend:
   `attention_backend="FLASHINFER"` selects attention only.
3. Register the Triton load hook with the Triton 3.5.1 HookChain:
   `triton.knobs.runtime.kernel_load_end_hook.add(binder.on_kernel_load)`.
   Direct assignment replaces the HookChain and is not the supported 3.5.1
   registration contract. Retain the callback reference and remove it during
   teardown if more than one engine may be created in the same process.
4. Launch the adapter through the restored environment's absolute Python
   executable. The existing `run_timing.sh` and container smoke launcher use
   bare `python3` and/or old container paths and therefore are not a valid
   host-route receipt.

## Compatible current assumptions

- vLLM 0.15.1 supports `enable_prefix_caching=False`. Preserve it for the
  matched deterministic experiment.
- It also supports `load_format`, `model_loader_extra_config`,
  `max_num_batched_tokens`, `disable_log_stats`, `enforce_eager`,
  `seed`, and `attention_backend="FLASHINFER"`.
- `VLLM_ENABLE_V1_MULTIPROCESSING=0` and `VLLM_NO_USAGE_STATS=1` are
  present in the v0.15.1 environment contract.
- Token prompt dictionaries with `prompt_token_ids` are accepted.
- The custom loader uses the v0.15.1 `register_model_loader`,
  `BaseModelLoader`, and `DefaultModelLoader` interfaces and matching
  load method shapes.
- vLLM v0.15.1 defines the unquantized Triton kernel as
  `fused_moe_kernel` and its BF16 unquantized path calls
  `invoke_fused_moe_triton_kernel`. The current binder name filter is
  therefore plausible for this pinned version.
- Triton 3.5.1's load-end callback arguments match the binder's five-argument
  callback shape, and its metadata group is a mapping.

## Evidence required before R1

Compatibility does not prove exact binding. A same-shape warmup must produce a
binding receipt for `fused_moe_kernel` with `bound_count > 0`, the selected
PTX variant hash/path, and no ambiguous or failed binding. The request must
then begin after warmup and must fail incomplete if the instrumented module
registry changes during the request.

Record the installed package versions, restored Python absolute path, relevant
MoE environment variables, attention backend, binding JSONL, and native
library hashes in the run manifest. A successful import or engine creation is
only a preflight check.

## Tagged-source references

- vLLM v0.15.1 `vllm/engine/arg_utils.py` and `vllm/entrypoints/llm.py`
  establish the accepted LLM/EngineArgs inputs.
- vLLM v0.15.1 `vllm/envs.py` defines multiprocessing, usage-stat, and
  FlashInfer MoE environment controls.
- vLLM v0.15.1
  `vllm/model_executor/layers/fused_moe/fused_moe.py` defines
  `fused_moe_kernel` and the Triton invocation path.
- vLLM v0.15.1 model-loader base/default/loader modules establish the custom
  loader registration and method contracts.
- Triton v3.5.1 `python/triton/knobs.py` defines
  `kernel_load_end_hook` as a HookChain with `add` and `remove`.
