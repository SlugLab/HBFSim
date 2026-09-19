## What this changes

<!-- One paragraph. What behavior differs after this lands. -->

## Why

<!-- The problem this solves. Link an issue or a TODO.md item if there is one. -->

## Evidence

<!--
Paste the actual output, not a summary of it. For a change that touches modeling
or measurement, say which of the four reported times moved: modeled device time,
host service time, wall-clock time, or emulator overhead.
-->

```text

```

## Checklist

- [ ] `./scripts/bootstrap.sh` and `cmake --build build -j` succeed with
      `HBFSIM_ENABLE_CUDA=OFF HBFSIM_ENABLE_MQSIM=ON`.
- [ ] `ctest --test-dir build --output-on-failure` is green, or every new failure
      is explained above.
- [ ] `python3 scripts/check_doc_links.py` reports zero broken links.
- [ ] No number is claimed that a document under `docs/proofs/` does not support.
- [ ] If this supersedes a published number, the affected proof document says so.
