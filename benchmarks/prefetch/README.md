# Prefetch experiment: what to run and where everything is

The reasoning behind the experiment, and what its numbers may and may not be
used to claim, is in `docs/46-预取实验设计.md`. This file is only the map and
the commands.

## Files

| Path | What it is |
|---|---|
| `include/hbfsim/prefetch_model.hpp` | The model's interface, with each knob's meaning |
| `src/prefetch/prefetch_model.cpp` | The model: a deterministic discrete-event simulation |
| `tests/cpu/prefetch_model_test.cpp` | 12 property assertions, written before the implementation |
| `benchmarks/prefetch/hbf_prefetch_bench.cpp` | Sweeps one access stream and prints JSON |
| `scripts/run_prefetch_accuracy_sweep.py` | Runs all three streams and writes the artifact and a CSV |
| `docs/proofs/artifacts/prefetch-accuracy-sweep.json` | The swept cells |
| `docs/proofs/artifacts/prefetch-accuracy-sweep.csv` | The same cells, flat, for plotting |

## Build

The model and its test are CPU-only. Neither needs a GPU or `nvcc`.

```
cmake -S . -B build -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON \
      -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)" --target prefetch_model_test hbf_prefetch_bench
```

## Run the tests

```
./build/prefetch_model_test    # exit code 0, prints nothing on success
```

On failure it prints the line number and the assertion that failed.

## Reproduce the sweep

```
python3 scripts/run_prefetch_accuracy_sweep.py --build-dir build
```

Writes `docs/proofs/artifacts/prefetch-accuracy-sweep.json` and the matching
`.csv`. Both carry a `disclaimer` field reading
`modeled, not measured on any device or GPU`.

## One stream at a time

```
./build/hbf_prefetch_bench --stream sequential --accesses 20000
./build/hbf_prefetch_bench --stream random     --accesses 20000
./build/hbf_prefetch_bench --stream moe        --accesses 20000 \
                           --pages-per-expert 2304
```

`--pages-per-expert 2304` is one Qwen3-30B-A3B expert: 3 x 2048 x 768
parameters in bf16 is 9,437,184 bytes, which is 2304 pages of 4 KiB.

Other options: `--compute-ns` (accelerator time between two accesses, the
interval a prefetch hides behind), `--lead` (how many accesses ahead a prefetch
is issued), `--buffer-pages`, `--max-in-flight`, `--seed`.

## Reproducing the two results the paper leans on

The naive next-page policy's accuracy on a Mixture-of-Experts stream is
(P-1)/P, where P is how many pages one expert occupies:

```
for p in 1 2 4 8 16; do
  ./build/hbf_prefetch_bench --stream moe --accesses 20000 --pages-per-expert $p
done
```

One token of Qwen3-30B-A3B, which is 884,736 page accesses:

```
./build/hbf_prefetch_bench --stream moe --accesses 20000 --pages-per-expert 2304
```
