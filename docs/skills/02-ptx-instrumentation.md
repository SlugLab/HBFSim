# PTX instrumentation

## Purpose

Locate parsing, rewriting, helper embedding and coverage decisions precisely enough to preserve the existing fail-closed boundary during a transform change.

## Scope

This describes B's line-oriented load/store transform and statement-oriented unsupported scan. The phase-two C2 optional parser work described below does not install a production future/TMA transform or runtime. Snapshot convention: [reading order](00-reading-order.md).

## Key concepts

A rewritten operation uses the resolver's returned address. Its status check can trap before the native operation. Supported textual syntax, module transform success, gate admission and dynamically covered bytes are distinct properties. `unsupported_instructions == 0` alone is not a full-coverage proof.

## Important files

[Shared comment scanner](../../src/ptxpass_hbf/ptx_source.hpp), [optional IR](../../src/ptxpass_hbf/ptx_ir.cpp), [optional async parser](../../src/ptxpass_hbf/ptx_async_op.cpp), [Memory-op parser](../../src/ptxpass_hbf/ptx_memory_op.cpp), [parser types](../../src/ptxpass_hbf/ptx_memory_op.hpp), [transform](../../src/ptxpass_hbf/transform.cpp), [transform types](../../src/ptxpass_hbf/transform.hpp), [plugin](../../src/ptxpass_hbf/plugin.cpp), [CLI](../../src/ptxpass_hbf/main.cpp), [PTX embedding](../../cmake/EmbedDevicePtx.cmake), [coverage gate](../../src/cuda_runtime/coverage.cpp), and [bpftime patch](../../patches/bpftime/0001-exact-module-load-provenance.patch).

## Important structs/classes/functions

- `parse_memory_op` recognizes its supported `ld`/`st` global syntax, predicate, `%rd...` address and checked constant offset; `access_bytes` derives element/vector width.
- `transform_ptx` selects the requested kernel, excludes helper functions, creates fresh scratch registers, emits the resolver/status/fault sequence, and rewrites the original address operand.
- `code_without_comments`, `joined_statement`, `statement_is_open` and `unsupported_memory_instruction` classify unsupported multi-line statements without merging `.loc` directives into following instructions.
- `append_device_helper` embeds `kEmbeddedDevicePtx`, checks the baseline target and rejects reserved helper-name collisions except an exact trusted existing helper.
- `CoverageGate::check_launch` applies module metadata and the policy of actual registered argument addresses. The plugin prepares per-kernel manifest metadata; the gate consumes it.

## Call path / data path

Build: device CUDA source → generated helper PTX → `EmbedDevicePtx.cmake` strips module directives → generated `hbf_device_ptx.hpp` → transformer binary/plugin.

At module transformation: original PTX → `transform_ptx` → selected native operation gets address/status helper call → embedded helper definition is included → module identity and coverage are attached → launch gate validates the loaded variant. At execution, the original operation depends on the resolved address, so B pays delay before the operation.

## CPU-side vs GPU-side execution context

Parser, transform, hashing, JSON manifest creation and module-load interception run on the CPU. Injected `__hbfsim_resolve` and `__hbfsim_fault` execute on the GPU in application lanes. The build-generated PTX is code, not a daemon or separate GPU kernel.

## Invariants

The original predicate must guard both injected work and the native operation. A predicated resolver uses a non-`.uni` call; the original opcode and qualifiers are retained while its address changes. Helpers prefixed `__hbfsim_` and `__bpftime_` are excluded to prevent recursion. Unsupported global async copies, including tensor bulk forms, remain counted until their complete semantic implementation replaces that rejection. Target/ABI collisions must not silently fall back.

## Supported behavior

The parser supports its scalar/vector global load/store forms, optional volatile spelling, integer/bit/floating widths, and checked signed constant offsets on a `%rd` address. The unsupported scanner handles line/block comments, multiline statements and `.loc` separation. This is the implemented syntactic subset, not a promise to accept every valid PTX representation of an equivalent operation.

Phase-two C2 adds `parse_module` with `Module`/`Function`/`Instruction` records and `parse_async_instruction` in the optional `hbfsim_eval_ptx` library. They parse/analyze CPU artifacts and are not linked into `hbfsim_core`. `code_without_comments` is extracted verbatim to `ptx_source.hpp` for both paths. The new IR skips `.loc` and rejects packed declaration/instruction shapes and unterminated comments. Parser recognition of a TMA opcode is not runtime support; validation status belongs in the phase-specific logs.

The later C6.2 unit (`49ee96b`) connects the bounded ordinary scalar emitter to
explicit `timing_load_future_v1` dispatch only in complete ON/CUDA13/sm120 builds.
It recomposes same-mode selected kernels from original bytes, binds exact helper
and per-kernel manifests, and rejects mixed modes or reserved-symbol forgery.
Future parameter metadata comes from the masked selected entry's validated
spans; comments and prefix-named entries cannot redirect its parameter layout.
Actual launch geometry and cumulative trace capacity are checked by the loader
and coverage gate. Synchronous parsing/defaults are preserved. These changes
have CPU/compile proof; optimized SASS dependency and GPU gold remain separate.

## Explicitly unsupported behavior

B does not model global atomics/reductions, ordinary global `cp.async`, tensor/bulk async global transfers, texture/surface accesses, general pointer expressions, cubin-only rewriting or deferred first-consumer waits. Pure `cp.async.commit_group`/`wait_group` synchronization forms do not themselves touch memory and are intentionally not counted as unsupported *memory* operations; that does not make their copy family supported. Capacity/legacy strict consumers of unsupported paths must be rejected; permitted timing-backed opaque paths remain unmodeled.

## Common failure modes

Restoring the donor's older unsupported regex; mistaking a helper function name for trusted provenance; losing an instruction behind `.loc` or a block comment; selecting the wrong same-name Triton variant; recording “modified” as complete coverage; or assuming PTX textual placement proves optimized SASS placement. A new accepted syntax needs both parser/transform reasoning and end-to-end gate metadata review.

## Tests proving the behavior

Existing [PTX transform tests](../../tests/cpu/ptx_transform_test.cpp) and [async copy coverage regressions](../../tests/cpu/ptx_async_copy_coverage_test.cpp) cover syntax, exclusions and async rejection. [Plugin](../../tests/integration/test_ptxpass_plugin.py), [device helper PTX](../../tests/integration/test_device_helper_ptx.py), [coverage-manifest flow](../../tests/integration/coverage_manifest_flow_test.cpp) and [bpftime patch](../../tests/integration/test_bpftime_patch.py) tests cover adjoining boundaries. CUDA-enabled tests may need the embedded helper; a CPU-only transform test does not prove assembly or live execution. Tests are identified here, not rerun by this documentation change.

## What not to change casually

Comment-safe/multiline classifier regressions, predicate guarding, return layout, reserved helper names, exact variant identity and target checks. A future port must preserve base coverage behavior and update parser, analysis, runtime, control, loader and embedded PTX together. Do not globally disable optimization to conceal an unresolved PTX/SASS relationship.

## Related docs

[Memory semantics](03-cuda-memory-semantics.md), [async/TMA](04-cuda-async-cpasync-tma.md), [ABI](05-device-helper-and-control-abi.md), [source ledger B01–B02](../49-eval-audit/source-ledger.md), and [historical exact-load lifecycle](../superpowers/plans/2026-08-10-task6-exact-load-lifecycle.md).

Phase-two C4 adds optional [ordinary-load future analysis](../../src/ptxpass_hbf/ptx_analysis.cpp).
`analyze_futures` admits a declared straight-line scalar subset and rejects
branches, calls, predicated exits, unknown def/use families, atomics and async
copies. Conditional consumption/drains retain may-pending state; unconditional
consumption clears it. Register overwrite adds a pre-clobber drain. The future
emitter must still track an issue-valid token, because predicates may change
before consumption; analysis alone does not authorize any runtime launch.
Only thread/warp bounds are computed; CTA/cluster geometry is not guessed.
