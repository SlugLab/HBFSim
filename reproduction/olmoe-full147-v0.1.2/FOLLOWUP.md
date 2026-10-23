# Optimization after the v0.1.2 freeze

The frozen strategy and accepted raw results must stay unchanged. Further
changes belong in independent source/build/run roots and need the same
correctness and resource gates. The current task disk authority is40GB total;
RAM/VRAM planning follows observed demand and host headroom. UCIe stays frozen.

The latest full147 result leaves initialization363.54s and generation108.21s.
The same-attempt host timeline places363.410967974s inside `LLM.__init__`;
the separate normal preparation adds about0.132s. The model-loading log's
21.687661s is an earlier subspan, not the whole initialization. The engine
profile/KV-cache/warmup log alone reports329.78s.

Existing GPU records show long fused-MoE kernel residence. Coarsely aligning
CUPTI records to UTC logs suggests about291.53s of unioned GPU intervals in
post-load initialization, including about280.32s summed fused-MoE duration.
**This phase attribution is conditional:** no independent CUPTI/UTC/host clock
bridge was recorded. Kernel residence also includes stalls and instrumented
waits; it is not pure computation or a wall share attributable to NAND.
Do not publish these exploratory sums as exact initialization percentages.

The next informative step is to reuse the existing passive framework observer
for memory profiling, KV initialization, model warmup and sampler work, with an
explicit clock bridge. Actual compilation/cache-hit/miss timings are still
missing:81 module-policy log rows are emitted before pool lookup and do not
prove81 compilations. The provider saved45 unique blobs (6.585GB); there is no
evidence that these are duplicate files or a measured time saving from dropping
them. Bound and justify any reduction in recording by its acceptance role.

Skipping profile/autotune/warmup, changing KV sizing, sharing live CUDA handles
or removing session clear is not an established transparent optimization.
These can alter RNG/cache/backend prestate or lifetimes. A candidate must first
show unchanged scientific semantics and then an actual layer0+head improvement
before another full147 evaluation. A matched pure-eGPU model baseline remains
unestablished; zero injected delay and fixed eval-delay=0 are different paths.

Same-page merging remains an independent experimental branch. Its prior model
ON/OFF observations did not demonstrate a speedup; logical sampling/warmup and
ordered NAND trace equivalence remain unresolved. Likewise, the accounting
microbenchmark's26.55× does not predict HBF end-to-end improvement. Do not turn
these into defaults or revise their original negative/limited results.
