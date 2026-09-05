# Evaluation artifacts (phase 1)

Entry point: `docs/49-new-evaluation-plan.md`. Tested with Python 3.13.9, Matplotlib 3.10.8, NumPy 2.2.6. No network services or model download are required for previews.

```bash
python scripts/eval/generate_run_matrix.py
python scripts/eval/generate_mock.py
python scripts/eval/validate_results.py --input results/mock/eval.csv
python scripts/eval/render_figures.py --input results/mock/eval.csv --out figures/mock --watermark
python -m unittest discover -s scripts/eval -p 'test_*.py' -v
python scripts/eval/audit_async_probe.py
```

The last command requires a C++20 compiler and the pinned SM120 commit in the local Git object database. It compiles a temporary copy of the original branch's parser/transform code and records a static counterexample, without changing production code.

Formal rendering uses the same functions:

```bash
python scripts/eval/render_figures.py --input results/measured/eval.csv --out figures/final --strict-no-mock
```

Real CSV/JSON rows and a matching raw-artifact manifest are prerequisites. MOCK rows always get a watermark; a final output path or strict flag rejects them before writing. The renderer checks schema and artifact hashes, not the truth of scientific claims. See `docs/49-eval-audit/claim-gates.md`.

No new runtime experiments run automatically. The run matrix is a costed proposal, not a job queue. The test suite creates explicitly synthetic PROJECTED test records in temporary directories only; these are neither measurements nor research predictions and are deleted after the tests.
