# Workload methodology and evidence contract

Freeze exact Git/submodule SHAs, build options/compiler, executable hashes, profile content/hash, model/tokenizer revision and weight hashes, trace hashes, environment fingerprint, device identity, command, random seed, run order, warmup, repetitions and raw output locations before comparing configurations. An environment fingerprint supplied to the replay CLI is caller metadata; validate it against the archived capture manifest rather than treating an arbitrary string as attestation.

Separate these evidence classes: live native GPU measurement; live instrumented timing/capacity execution; frozen real-trace replay; synthetic stream arithmetic; empirical device calibration; hardware-proxy measurement; HBF projection. A successful CPU/fake-driver or static PTX test does not establish live GPU behavior.

Use identical prompts, token limits, seed, model bytes, profiles, timing scale and placement across matched A/B arms. Exclude model loading, compilation and declared warmup from steady-state timing. Preserve token IDs/checksums, exact supported/unsupported coverage, failures and all attempted points. No coverage bypass is added for planned ablations. Capacity and strict-policy kernels containing unsupported global async operations must remain refused.

The Qwen source protocol uses BF16 Qwen3-30B-A3B, 48 layers, 128 experts/layer and 8 routed experts/token. These are source-protocol assumptions: validate the actual frozen model manifest. “30B” total parameters and “A3B” activated parameters are model labels, not a measurement of transferred bytes. Expert page locality in the synthetic prefetch stream is an assumption, not measured predictor accuracy.

The source smoke specifies four fixed 32-token prompts, 32 generated tokens, max length 256, seed 0, concurrency one, eager execution and vLLM 0.15.1. New capture/staging on that stack is deferred. Supply archived manifests/traces to the offline replay; do not infer serving concurrency from independently combined single-sequence routes.

Use the source plan's minimum five independent application/trace captures per cell where feasible, and at least 11 microbenchmark samples for hardware latency. Declare the count and warmup before collection. Replaying the same deterministic trace five times is not five independent routing samples. Hardware writes, reformatting and new hardware campaigns are outside this integration task.
