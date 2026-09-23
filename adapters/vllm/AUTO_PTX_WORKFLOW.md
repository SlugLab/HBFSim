# Automatic vLLM PTX instrumentation workflow

`adapters/vllm/auto_run.py` provides one bounded entry point for a matched
baseline discovery run followed by automatic PTX staging and a fresh timing
run. The default is `--hbf-weight-selection all` with
`--hbf-instrumentation-policy strict`.

The workflow creates a new report directory and refuses to reuse it. It runs
an HBF runtime bundle preflight before the native baseline discovery. Strict mode
requires the gate's `hbfsim_instrumentation_policy_capabilities_v1` export and
the agent's `bpftime_nv_strict_bridge_capabilities_v1` export. It also calls
`hbfsim_strict_denial_capabilities_v1` in an isolated CPU subprocess and
requires bit 0 before the native baseline or any GPU target can start. Artifact
hashes and the returned capability value are recorded. It then runs the same
model and workload once natively to populate the Triton PTX cache,
invokes `auto_prepare_ptx.py` in a time-limited child, verifies `COMPLETE.json`
and every published artifact hash, the exact staged PTX file set, and the
published entry-to-pass-manifest mapping. It then builds a BPF object containing one CUDA
kprobe section for every unique transformed entry, and only then launches a
fresh process through `scripts/run_with_bpftime.sh`. The attach loader must
attach every section before it publishes readiness.

Before each GPU process, the workflow checks host memory and GPU memory. Its
GPU admission includes current usage, the requested vLLM reservation, and 5%
headroom. The default target timeout is 14,400 seconds and remains explicitly
configurable. A timeout terminates only the new process group, escalates from
TERM to KILL after a bounded grace period, and retains stdout, stderr and a
process receipt.

Run the source-tree entry from the repository root. It depends on the adjacent
`run.py`, `auto_prepare_ptx.py`, and `scripts/run_with_bpftime.sh`; it is not an
installed console command. Example (execute only when the GPU owner schedules
the run):

```sh
python3 adapters/vllm/auto_run.py \
  --model /absolute/model \
  --profile /absolute/profile.json \
  --report-dir /absolute/new-report \
  --build-dir /absolute/hbfsim-build \
  --bpftime-build-dir /absolute/bpftime-build
```

The selection forms are:

```sh
# All learned weights (default).
python3 adapters/vllm/auto_run.py ... --hbf-weight-selection all

# Only matching learned weights.
python3 adapters/vllm/auto_run.py ... --hbf-weight-selection include \
  --hbf-include-pattern 'model.layers.*.mlp.*'

# All learned weights except matching names.
python3 adapters/vllm/auto_run.py ... --hbf-weight-selection all \
  --hbf-exclude-pattern '*.kv_cache*'

# Pure native execution: model and report directory are sufficient.
python3 adapters/vllm/auto_run.py --model /absolute/model \
  --report-dir /absolute/native-report --hbf-weight-selection off
```

Use `--hbf-instrumentation-policy partial` explicitly to permit a run when at
least one transformed variant is published. `workflow.json` then records the
stage status, published entry names, and the count of variants that were not
fully ready. This describes discovered PTX only. It does not establish dynamic
coverage of every model weight or universal coverage of opaque cubins.

`--hbf-weight-selection off` skips discovery, staging, probe construction and
the bpftime wrapper. It invokes `run.py` directly in native mode and records
`DISABLED_NATIVE`; no profile or HBF/bpftime build paths are required.

Each child phase has a finite timeout. A free-disk floor is checked before the
discovery and staging phases. There are no implicit retries. On any failure,
the output directory and phase logs are retained and `workflow.json` records
the failed phase and exception.

Only a strict timing target receives
`HBFSIM_STRICT_STOP_ON_DENIAL_PATH=<new-report>/first-denial.json`. The path is
required to be absent before launch. Baseline/native, partial, and off modes
remove this variable even if the parent environment contains it. A gate denial
still makes the workflow fail; exit 86 is never converted into success. When
the fresh exclusive path contains a complete schema-v1 denial receipt and the
process receipt also records exit 86, `workflow.json` preserves the API,
runtime domain, original DSO, kernel, reason, function addresses, and hashes of
the denial and nonempty process-maps evidence as `VALIDATED_FAILURE`. Missing or
incomplete evidence is recorded as `INVALID_OR_ABSENT`, while the original
process failure remains authoritative.

This mechanism stops strict execution at the first denied launch so a later
CUDA error cannot hide the first policy boundary. It does not make a native
cubin transformable, prove that an unsupported module was instrumented, or
establish successful model inference.

After the fresh process exits, success still requires complete baseline and
timing `result.json` reports, identical prompt and output token identities,
consistent exact Triton binding receipts, gate decisions with
`requires_instrumented_execution`, and matching strict bridge receipts. A
no-direct-hit launch may correctly have `modeled=false` while still requiring
and selecting the transformed function. Strict mode rejects opaque or unknown
runtime coverage. Partial mode records
those boundaries as `PARTIAL_OBSERVED` or `UNKNOWN_BOUNDARY`; neither process
success nor staging readiness is reported as dynamic coverage of all weights.
Strict mode also requires `strict-bridge.jsonl`: every gate decision requiring
instrumentation must show an exact alias, `PATCHED` selection, successful CUDA
return, and an aggregate original-function join to an exact Triton binding.
The report labels this aggregate evidence explicitly because schema v1 has no
unique launch identifier and does not prove per-access or all-weight coverage.
