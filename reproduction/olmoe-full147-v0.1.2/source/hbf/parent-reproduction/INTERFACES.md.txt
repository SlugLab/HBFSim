# Final147 runtime interface entry points

This index describes the implementation actually used by epoch6712. Historical receipts are observations; absolute paths, GPU identity, addresses and process IDs must not become new-host constants. The full [binding contract](evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/interface-binding-contract.md) and [weight integration matrix](evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/weight-integration-matrix.md) retain version-specific history.

## Host ABI and lifetime

The source of truth for the provider interface is [qkv_live_identity_v1.hpp](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-host-v1/source/qkv_live_identity_v1.hpp). `QkvLiveIdentityV1` is104 bytes on the required64-bit host ABI: `struct_size`/`reserved` are32-bit at offsets0/4; `context`/`module`/`association_token` are64-bit at8/16/24; `image_sha256[65]` begins at32, followed by alignment padding. The caller initializes `struct_size`; the provider owns live association tracking and invalidates function/module/context associations as they expire.

`hbfsim_qkv_live_identity_for_entry_v1(uint64_t function, uint64_t expected_context, const char* expected_exact_symbol, QkvLiveIdentityV1* out)` is additive to the older `hbfsim_qkv_live_identity_v1(function, expected_context, out)`. It returns1 only for an exact live module/function/context/entry/image association,0 for no exact proof, and-1 for invalid inputs. See [provider implementation](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-host-v1/source/provider_router_exact.cpp). These are custom status values, not CUDA errors.

The exported agent controls are implemented in [nv_attach_impl_frida_setup.cpp](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-host-v1/source/nv_attach_impl_frida_setup.cpp):

- `int bpftime_nv_qkv_select_weight_storage_v1(CUdeviceptr base, size_t bytes)`:0 success/disarm; -1 opt-in disabled; -2 invalid/overflowing or already-selected span; -3 no current CUDA context; -4 missing registration function or nonexact registered span. `(0,0)` explicitly disarms. Call on the actual model execution thread after registration.
- `int bpftime_nv_qkv_end_selected_call_v1()`: clears the selected scope on every call;0 only for exactly consumed successful selection, otherwise-1. The plugin calls it in `finally` and preserves candidate/end failures.
- `int bpftime_nv_qkv_pin_bound_identity_v1(CUfunction original)`: called after exact PTX binding, outside CUPTI callbacks.0 success; -1 disabled/missing function/unpinned configuration; -2 no context; -3 no exact live identity; -4 inactive/unready implementation; -5 missing patched function or metadata mismatch. Bound identity includes current context, module and association token.

The [model plugin](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-model-v1/combined_model_adapter/__init__.py) uses two uint64/size_t arguments for select, no arguments for end, and signed int results. It is default-off. Each selected real decode call captures its cloned-input native reference, selects the actual full storage, executes the candidate, ends the scope and compares output. Router tuple/bias and lm_head `compute_logits` placement remain explicit; class names alone do not establish an ABI match.

## Actual components and library domains

The [CPU build receipt](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-host-v1/CPU_BUILD_RECEIPT.json) maps exact source→compiler argv→object→archive→DSO. The final setup TU joins the Li7 `nv_attach_impl_router_scoped.cpp` implementation member in the actual nv-attach archive; provider has its own `library_identity_core.cpp`; the gate is the frozen Li7 gate. Rebuilding must include both TUs, not substitute a similarly named object or copy a historical archive.

Agent/private CUDA13 and provider/CUPTI12 domains are separate. Use the recorded dependency versions and configured paths; do not select libraries by whichever `libcudart` happens to appear first. An exported symbol or preload order does not prove invocation: the accepted evidence joins actual Driver API/CBID307, thread, function, image, storage, profile and service records.

## Configuration, paths and acceptance

Combined host configuration keeps separate immutable Li6/Li7 entries and metadata profiles. Source PTX identifies the recovered module; staged PTX includes the existing HBF instrumentation. SHA-named stage links, sidecar module IDs, native-binding manifest and original/staged joins have different roles. Regenerate location-dependent paths at preparation time; never repair a path mismatch by relaxing an identity assertion.

The [validator](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-model-v1/validate_combined_model.py) and [final report](evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/final147-20260926-v1/REPORT.md) distinguish source conversion, instrumented-kernel output and actual model connection. Final147 is an actual one-run fixed-request result:147 registered storages,49 selected new decode outputs, old98 address/module/service closure. Registered extent is not every byte touched; other shapes, experts and all prefill consumers are not certified.

The package contains source and historical build/run evidence. Publishing it does not itself prove a clean-machine rebuild or a new GPU execution. Upstream lifter generality limits discovered after the run are recorded separately in [the five-PR review](upstream-review/REVIEW_SUMMARY.md).
