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
| `benchmarks/prefetch/hbf_prefetch_bench.cpp` | Sweeps one access stream through the model and prints JSON |
| `src/host_service/capacity_page_service.cpp` | The real system-side readahead, in capacity mode |
| `tests/cpu/capacity_readahead_test.cpp` | Its contract: off by default, never forces a writeback, never drains on the demand's own path |
| `benchmarks/prefetch/hbf_capacity_readahead_bench.cpp` | Drives the real readahead and reports media reads avoided |
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

## The real readahead, as opposed to the model

`hbf_capacity_readahead_bench` exercises `CapacityPageService` directly. It
reports media reads avoided, not wall-clock time, and touches no GPU.

Does the implementation reproduce what the model predicts for a next-page
policy, which is (P-1)/P for P pages per expert:

```
for p in 4 8 16; do
  ./build/hbf_capacity_readahead_bench --stream moe --pages-per-expert $p \
      --readahead $p --frames 256 --drain-per-demand 0
done
```

What the worker's drain rate is worth. `--drain-per-demand` is how many queued
pages the worker gets through between two demands, and 0 means it keeps up
completely:

```
for d in 0 4 2 1; do
  ./build/hbf_capacity_readahead_bench --stream moe --pages-per-expert 8 \
      --readahead 8 --frames 256 --drain-per-demand $d
done
```

Read both numbers the benchmark prints, because they answer different
questions and reporting only the first is how an earlier version of this file
reached a wrong conclusion.

`demand_reads_avoided_fraction` is the share of demands that no longer wait on
the media, so it sets the latency. `total_media_reads_change_fraction` counts
every read of the backing store, readahead included, so it sets the bandwidth
the device must supply.

At P=8, depth 8, 256 frames, against an 840-read baseline:

| drain per demand | demand reads | readahead reads | total | demand avoided | total change |
|---|---:|---:|---:|---:|---:|
| 0 (keeps up) | 111 | 868 | 979 | +89.16% | **+16.55%** |
| 1 | 959 | 825 | 1784 | +6.35% | **+112.38%** |

So the readahead removes most of the waiting and still raises total media
traffic. That is a trade of bandwidth for latency, and it is the wrong trade
whenever the tier is bandwidth-bound. Draining one page per demand loses the
latency benefit as well and more than doubles the traffic.

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
