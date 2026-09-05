# Offline prefetch model

Enable `HBFSIM_ENABLE_EVAL_TOOLS=ON` and build `hbf_prefetch_bench`.
`python3 scripts/run_prefetch_accuracy_sweep.py --build-dir BUILD --output OUT.json --csv OUT.csv` regenerates the seeded synthetic model. Explicit output paths must be outside the source tree. Record the exact Git SHA, executable SHA256, compiler, command and seed next to each run.

The model uses FIFO staging, bounded media servers, an assumed compute interval, and None/NextPage/Accuracy policies. NextPage predicts an address, not an expert. Accuracy policy consults future synthetic accesses; it is a sensitivity model, not an implemented predictor. Both in-flight and arrived pages count toward staging. Pending hits can still stall. Positive integer configurations in the tested range are supported; arbitrary large/overflowing configurations are not validated.

No runtime `readahead_pages` field, capacity readahead benchmark, speculative dispatcher or historical result CSV/JSON is imported from PR5. See [integration status](../../docs/eval/EVAL_BASE_INTEGRATION.md).
