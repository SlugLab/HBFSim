# Native PTX extraction and scoped provenance

These tools preserve the original validated source bytes. `collector/` extracts embedded PTX members with `cuobjdump -xptx all`; `scoped_join/` links selected native entries to already transformed PTX and its pass manifest. Neither tool launches CUDA work or changes the PTX memory rewrite algorithm.

From the repository root:

```sh
python3 tools/native_ptx_reproduction/collector/collect_native_ptx.py --container /path/to/module.so --cuobjdump /path/to/cuda/bin/cuobjdump --output /new/collection
python3 tools/native_ptx_reproduction/scoped_join/join_scoped_staging_provenance.py --collector-manifest /new/collection/native-ptx-manifest.json --staging-dir /completed/stage --pass-manifest /completed/stage/pass-manifests.jsonl --selected-entries-json /path/to/selected-entries.json --output /new/provenance.json
python3 tools/native_ptx_reproduction/scoped_join/test_scoped_join.py
```

Use the actual filenames emitted by your collection/staging attempt. Extraction alone does not establish transformed execution. The scoped provenance records explicit selected entries, not whole-module coverage. Its preserved README describes the provenance contract; the experiment's partial runtime policy deliberately leaves unselected weight paths native. A selected binding must still have matching entry, container, raw and staged identities. Unselected native execution is not HBF coverage.

Use `adapters/vllm/auto_prepare_ptx.py` with the CVTA-aware pass for transformation. Rebuild the binding and combined-stage manifests from actual new artifacts; do not reuse the older experimental `build_union.py`, which selected an obsolete five-module fixture. The successful supported-weight run used seven raw modules/eight selected pass rows, including four actual MoE variants and both normalization entries from the joint module.

The relocated CLI checks and four scoped-join tests passed on giga; see `docs/hbfsim-supported-weights/evidence/NATIVE_TOOLS_RELOCATION_CHECK.json`. This checks relocation and provenance behavior, not a new model run.
