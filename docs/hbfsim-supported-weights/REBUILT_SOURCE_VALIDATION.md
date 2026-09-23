# Rebuilt source validation

The isolated integration tree starts at upstream main `17d0fc58c0444789650ab40b4641802b5b6c2db8`. It carries the successful HBF source, scoped native gate and CVTA-aware parameter metadata implementation. It does not import the unrelated thermal branch changes.

The following command actually completed both configure/build profiles on giga. The output directory must be new; substitute your absolute source, toolchain and output paths.

```sh
python3 scripts/reproduce_hbfsim_cpu_build.py \
  --source /path/to/HBFSim \
  --output /path/to/new-build \
  --cuda-root /path/to/cuda13-compatible-view \
  --llvm-root /path/to/llvm-20 \
  --jobs 2 --deadline-seconds 3600
```

This is a CPU compilation, not a new GPU model validation. The `runtime-futures-on` profile builds the runtime, daemon, PTX pass and vLLM extension. The separate `gate-core-futures-off` profile builds the native launch gate with the matching core headers, CUDA symbol version map and system CUDA12 runtime. The receipt records each actual command and output hash.

The fresh CUDA host compiler is explicitly G++13, while ordinary C++ compilation uses G++15. On the tested glibc2.43 host, unmodified CUDA13 headers fail because `rsqrt` and `rsqrtf` declarations disagree with system exception specifications. The validated isolated toolkit view changes only those two declarations to add `noexcept(true)`; the original toolkit remains untouched. Its creation recipe and source hashes must accompany deployment. Switching only the host compiler did not fix this issue.

Actual rebuilt artifacts:

| Artifact | SHA256 |
|---|---|
| Runtime | `5acdc2328f6a79ac447a0506c09b72620aa16a8af408613a9602a57dc8a6700f` |
| Daemon | `6e6a54c94de72d06072ee48de9b853933ddffc687edd61b6bf9fa7927869e339` |
| CVTA-aware PTX pass | `1ffe053ee123b8c2689a7fc18cdb616cf6be3a35f1e8fce179475ec15c17cfcf` |
| vLLM extension | `e49eeac31969f099cedafd39765ce68325dfb8c96cf8e9ab6fe999b087799453` |
| Native launch gate | `abde17316e71e18382462947cb4647e29b9b1657ef715d1e3fe9b056fdd6e889` |

The rebuilt gate is byte-identical to the gate used by the successful 98-storage experiment. The other rebuilt artifacts have new identities; their relevant interface tests and representative model validation subsequently passed, as detailed below. The original full98 result is preserved independently in [RESULTS.md](RESULTS.md).
## Additional actual CPU validation

- [Gate interface result](evidence/GATE_CPU_RESULT.json): 12 cases passed against controlled fake CUDA12/13 runtime and inert driver interfaces, without GPU work. This checks forwarding and metadata behavior; it is not model execution.
- [Norm pass result](evidence/NORM_PASS_CPU_RESULT.json): both joint normalization entries were transformed (66 and 60 rewrite sites), unsupported count zero. Pointer indices are `[0,1,7]` and `[0,2,3]`. The pass manifest SHA `21fa004ce807b3c5ed254fe03c2313c609c4f763684f89c880b6e51dde4563b1` matches the successful historical manifest. The new staged PTX has a different private generated helper symbol name.
- Agent [configure](evidence/AGENT_CONFIGURE_RECEIPT.json), [build](evidence/AGENT_BUILD_RECEIPT.json), and [environment check](evidence/AGENT_ENV_CHECK_RECEIPT.json) all passed from clean pinned bpftime source with the complete patch. New agent SHA: `519ef5617b5e0cbe28ce0b91d2a14decbe3e2dc99498bf874ec3789dd36caba8`; new PTX compiler SHA: `ee904bc6482648c9dcdfb43ad44e2c0bc396e8a15c42da21d87d7bb95b0b429c`.

These records preserve new binary identities. The new combination subsequently passed its representative model run; the archived all98 pass is not relabeled as a run of these new binaries.

## Aggregate profile and native baseline

The separate aggregate metadata target was subsequently built from the main integration source and fresh archives with timing futures ON and system CUDA12 linkage: [AGGREGATE_CLEAN_BUILD_RESULT.json](evidence/AGGREGATE_CLEAN_BUILD_RESULT.json). Its DSO SHA is `a6aa66064cc1251ae698845acb79e74b167cba394b576c26d30ca9f986867b3a`. The earlier `1dd173...` build that linked frozen historical archives remains a diagnostic, not the final source-reproduction artifact.

A fresh native baseline at GPU memory utilization 0.75 completed successfully with output `[70,13]`, identical to the archived baseline. See its [result](evidence/NEW_NATIVE_075_RESULT.json) and [finish receipt](evidence/NEW_NATIVE_075_FINISH.json). It uses the same one-request, two-input/two-output, seed-zero workload. The change from the historical 0.78 allocation reduces unbound KV preallocation to leave more VRAM headroom with the existing reservation. This is not a new HBF timing measurement; the matching staged run subsequently passed.

## Explicitly retained auxiliary components

The new runtime combination retains three unchanged auxiliary binaries from the successful archive: attach loader `823ec55914c5a31522cc9645e2ee821fa736dff16854a58829d3bd149d34af72`, syscall server `0b9b0d61f07b2fc10eb1644fde51a9ab47835991e4e0a3864fe956f78cf3781d`, and BPF probe `11dbc6a0724912c54a9ef5facb0219942ff9c0fbc87d4a996db9e3a9f9c6c7f5`. Their exact paths and hashes are explicit in `tools/native_ptx_reproduction/execution/bundle-inputs.example.giga.json`. They are not claimed as clean rebuilds from this integration turn. Framework overlays and checkpoint files are likewise pinned external inputs; the source and original build evidence are recorded in ENVIRONMENT.md.

See [auxiliary component provenance and build targets](../../scripts/AUXILIARY_COMPONENTS.md). Loader and probe sources match main and their original compile commands were recovered. The historical syscall-server byte hash is pinned, but its precise original producer command was not recovered; the new agent-only build did not rebuild that server. This is an explicit source-rebuild boundary, not a GPU coverage failure.

## Source-built representative model acceptance

Epoch 5801 completed with `PASS_8_OF_8_ADDRESSED_ACTIVE`, worker exit 0 and no remaining children. The full selected range total is 1,011,372,032 bytes across eight storages. In-range, admitted and completed counters are identical: 14,962,944 accesses / 239,149,056 intersection bytes; all recorded error classes are zero, all seven modules are COMPLETE, and accounting disable completed. Output `[[70,13]]` matches the new 0.75 native baseline exactly. Load took 507.014 seconds and generation 203.072 seconds. The worker completed at 2026-09-23 18:40:28.593 UTC, within the 1,800-second allowance.

See the actual [result](evidence/NEW_STAGED_075_RESULT.json), [dynamic storage validation](evidence/NEW_STAGED_075_DYNAMIC_VALIDATION.json), [worker finish](evidence/NEW_STAGED_075_WORKER_FINISH.json) and [controller finish](evidence/NEW_STAGED_075_FINISH.json). The source-built representative run and historical full98 run remain distinct; no second full98 execution is claimed. This short deterministic request validates the selected paths, not all possible prompts or individual storage latency.

## Checked-in preparation pipeline replay

After the model run, the checked-in `tools/native_ptx_reproduction/execution/` scripts completed a separate CPU-only stage, compile, scoped join and bundle composition in new output directories. All seven staged PTX hashes and all seven cubin hashes match the successful new runtime preparation exactly. The scoped join produced four native bindings and two sidecars; the bundle contains eleven explicitly hashed files. The new configuration passed the 33-artifact preflight and verified the actual epoch-5801 result as `PASS_8_OF_8_ADDRESSED_ACTIVE`. This replay validates the relocated preparation interfaces; it does not claim another GPU inference. See [stage](evidence/REPO_REPLAY_STAGE_RECEIPT.json), [compile](evidence/REPO_REPLAY_COMPILE_RECEIPT.json), and [join](evidence/REPO_REPLAY_JOIN_RECEIPT.json) receipts.
