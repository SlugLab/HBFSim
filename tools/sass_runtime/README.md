# Optional recovered-kernel runtime

Ordinary runs default to `HBFSIM_ENABLE_SASS_LIFTER=0`. This leaves native PTX
instrumentation available. The research entry `scripts/run_with_sass_research.sh`
explicitly defaults it to `1`; an explicit user `0` wins. Neither setting proves
that a kernel was instrumented.

The published historical sources stay immutable. Prepare new runtime copies:

```sh
python3 tools/sass_runtime/prepare_overlay.py --output /absolute/fresh/sass-overlay
```

The output contains the agent source and its companion headers, provider source,
and `combined_model_adapter` package, plus source/output hashes. The agent's actual
recovered dispatch predicate, provider's recovered identity queries, and adapter's
registration entry all consume the switch. Native PTX binding is not gated.

Install the agent source into an **isolated** compatible bpftime source checkout:

```sh
python3 tools/sass_runtime/install_agent_overlay.py \
  --overlay /absolute/fresh/sass-overlay \
  --agent-source /absolute/isolated/bpftime/attach/nv_attach_impl/nv_attach_impl_frida_setup.cpp \
  --version-script /absolute/isolated/bpftime/runtime/agent/agent.version
```

Rebuild that agent and the copied provider using the toolchain and dependencies in
the published reproduction recipes. Put the copied adapter package and its copied
entry-point metadata on the research worker's Python path. Record the new build
inputs and output hashes. This source overlay is not an already built binary, and
the source command alone is not an end-to-end reproduction. The historical source
depends on the matching recovered ABI headers, native binding manifests, exact
recovered/staged PTX, and provider configuration described in
`reproduction/sass-final147-20260926/REBUILD.md`; do not substitute arbitrary GPU
targets or unlicensed external lifters.

The research build uses its own manifest, not the ordinary public-build stamp.
Set `HBFSIM_SASS_BUILD_MANIFEST` and `HBFSIM_SASS_BUILD_MANIFEST_SHA256`, plus
`HBFSIM_SASS_PROVIDER` to the rebuilt provider. `verify_build.py` requires schema
`hbfsim.sass_research_build.v1`, a hash-pinned `overlay_manifest`, nonempty
`source_inputs` and `build_receipts` (each `{path, sha256}`), and `artifacts` for
`agent`, `provider`, `server`, `gate`, `pass`, `cudart12` (each `{path, sha256}`). Each build
receipt must contain `status: PASS`, actual `commands` with `argv` and
`returncode: 0`, and `outputs` listing actual `{path, sha256}` built outputs. Agent and provider
resolved paths and hashes must match those outputs. These are actual build records, not values to
invent in order to pass a gate. This branch preserves the separate framework
CUDA/runtime selection; it does not claim to be the clean public bpftime build.

The ordinary launcher clears legacy recovered gates when off, rejects strict
recovered scope requests, and preserves the ordinary caller preload contract.
Selected project backends are checked by their clean-build stamp or research
artifact manifest and real consumer switches. The launcher does not infer library
identity from filenames or claim control over arbitrary user-added libraries. The adapter additionally checks both agent and provider
capabilities before enabling its recovered wrappers. With the switch off it emits
an explicit coverage gap and skips its recovered wrappers; strict mode rejects
the request. A full147 validation must still fail if required coverage is absent.
The historical launch scripts inside the evidence tree are preserved evidence,
not switch-aware current entry points.

For completed runs, summarize actual address-matched, completed module evidence:

```sh
python3 tools/sass_runtime/report_layers.py \
  --validation /absolute/run/MODEL_VALIDATION.json --sha256 RECEIPT_SHA256
```

This prints layers and common weights evidenced by the pinned validator receipt.
It rejects registration-only data. A listed layer can have only some weights
covered; native-versus-recovered origin and binding/launch details remain in the
original evidence. This reader does not rerun the underlying validator and cannot
turn an unvalidated or edited receipt into proof.

Run host unit checks with `python3 -B -m unittest discover -s tools/sass_runtime`.
Real agent/provider rebuild, export verification, disabled-mode dispatch and
enabled-mode representative validation must be checked in an isolated GPU-host
workspace before claiming the rebuilt runtime works.

Record each real build command, using fresh output paths (existing outputs are
refused). The command after `--` is executed once and its actual return code is
recorded even on failure:

```sh
python3 tools/sass_runtime/record_build.py --receipt /absolute/fresh/agent-build.json \
  --cwd /absolute/isolated/bpftime --output /absolute/new/libbpftime-agent.so \
  -- cmake --build /absolute/isolated/build --target bpftime-agent
```

The build must be configured to emit that exact output. Use the published full
source patch and complete pinned submodules before installing the overlay; the
historical incremental archive commands alone do not rebuild all dependencies.
Record provider compilation separately with its exact compiler argv and output.
After successful builds, generate the manifest without editing a build stamp:

```sh
python3 tools/sass_runtime/make_build_manifest.py --overlay /absolute/fresh/sass-overlay \
  --build-receipt /absolute/fresh/agent-build.json \
  --build-receipt /absolute/fresh/provider-build.json \
  --source-input /absolute/isolated/bpftime/runtime/agent/agent.version \
  --agent /absolute/new/libbpftime-agent.so --provider /absolute/new/libprovider_router.so \
  --server /absolute/selected/libbpftime-syscall-server.so \
  --gate /absolute/selected/libhbfsim_launch_gate.so --pass /absolute/selected/libptxpass_hbf.so \
  --cudart12 /absolute/framework/libcudart.so.12 \
  --output /absolute/fresh/research-build.json
```

Add `--source-input` for the actual checkout/patch and dependency identity records,
configuration receipts, and other compiled sources. The manifest pins all three
overlay consumers automatically. The launcher checks the same manifest verifier,
loads the selected provider and prepends the verified adapter package directory
to the target's Python path. Framework plugin discovery must still load that
package; a configuration or capability export alone is not runtime coverage.

The research profile requires `HBFSIM_TARGET_CUDART12_PRELOAD` matching its
manifest `cudart12` artifact and empty inherited/extra preloads. Only the target
loads that runtime, in agent:gate:provider:CUDA12 order. The loader loads its
server only. This preserves the framework/agent CUDA-domain separation; runtime
library maps still need checking during the isolated host validation.

To preserve a later verified agent's CUDA-domain fixes and staged PTX identities,
pass `--agent-input /absolute/current/nv_attach_impl_frida_setup.cpp` together
with its required `--agent-input-sha256`. The generator verifies those exact
bytes before changing only the recovered switch predicate and capability export;
it records the real input path and hash. The default remains the public frozen
agent. Companion headers still come from the public snapshot and must be checked
for compatibility with a custom agent before building. Neither input is edited.
