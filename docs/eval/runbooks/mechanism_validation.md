# Mechanism validation

Build as in [environment setup](environment_setup.md), then run:

```bash
ctest --test-dir ../eval-artifacts/build-tools   -R 'ptx|coverage|capacity|mqsim|prefetch|trace' --output-on-failure
PYTHONPATH=adapters/vllm_capacity python3 -m unittest discover   -s adapters/vllm_capacity/tests -p 'test_*.py'
python3 -m pytest -q adapters/vllm/tests
python3 scripts/run_prefetch_accuracy_sweep.py   --build-dir ../eval-artifacts/build-tools --seed 7   --output ../eval-artifacts/prefetch.json --csv ../eval-artifacts/prefetch.csv
```

The sweep's default MoE shape includes at least one complete 884,736-page synthetic token even when `--accesses` is smaller. It is not a Qwen run. Supply positive bounded numeric configurations; input overflow/huge-stream stress is outside current validation. Save the exact SHA/build/binary hash with these outputs.

For already archived real traces and manifests, set the following three input paths/identity values to your frozen capture artifacts, then execute the existing CLI (output directory must not exist):

```bash
python3 adapters/vllm_capacity/trace_replay.py --help
python3 adapters/vllm_capacity/trace_validation.py --help
python3 adapters/vllm_capacity/trace_replay.py   --trace "$EVAL_TRACE" --model-manifest "$EVAL_MODEL_MANIFEST"   --environment-fingerprint "$EVAL_CAPTURE_FINGERPRINT"   --profile configs/profiles/nominal.json --git-commit "$(git rev-parse HEAD)"   --output-dir ../eval-artifacts/capacity-replay --repetition 0 --order-seed 0   --timing-binary ../eval-artifacts/build-tools/hbf_trace_timing   --timing-mode fast --timing-mode hybrid --timing-mode mqsim
```

The complete fixture reproduction needs neither weights nor a GPU: CTest `capacity_trace_policy`, `trace_timing`, `trace_replay_timing` construct small input manifests/traces in temporary directories, test policies, page splitting, conservation and separate timing fields. They do not certify full trace-schema completeness or real-Qwen fidelity.

Use `hbf_mqsim_bench --profile configs/profiles/nominal.json --requests 4096 --bytes 16384 --operation read --arrival-gap-ns 0` for media-only replay with explicit queue depth in the profile. This exercises the existing engine, not hardware. For baseline parity compare all deterministic JSON fields; exclude only host wall time and simulator requests/s. Keep code/profile/workload keys identical.
