# Native PTX scoped provenance join v1

This read-only adapter joins an immutable v2 collector manifest to an existing
completed staging generation for an explicit entry subset. It does not modify
the stage, pass manifest, collector, runtime gate, or agent.

Output schema is `hbfsim.native_ptx_provenance_scoped.v1`. A successful output
has `status: SCOPED_READY`, `scope: explicit_entry_subset`, and
`whole_module_ready: false`. `selection_contract.unselected_entry_policy` is
`STRICT_REJECT`; execution must enforce that at launch. A `partial` staging
producer status means only that the explicitly selected entry was transformed.
It does not authorize partial runtime fallback.

The adapter requires:

- stable collector container snapshots and every saved member hash;
- exactly one collector member membership for every explicit entry;
- stage raw-SHA set equal to the selected members, excluding other attempts;
- complete raw source SHA/size and complete staged `.entry` inventory;
- exact selected `entry_results` with `SUPPORTED_TRANSFORMED`;
- exact, duplicate-free pass-manifest `(module_id, kernel)` pairs;
- closed `COMPLETE` hashes and every variant staged hash.

Usage for the first plain BF16 RMSNorm entry:

```sh
python3 join_scoped_staging_provenance.py \
  --collector-manifest /root/hbfsim-exp/rebuttal_20260921/native-ptx-collector-v2/vllm-C-attempt-2/native-ptx-manifest.json \
  --staging-dir /absolute/path/to/existing/norm-stage \
  --pass-manifest /absolute/path/to/existing/norm-stage/pass-manifest.jsonl \
  --selected-entries-json selected-plain-bf16.json \
  --output /fresh/path/native-provenance-scoped.json
```

The selected sidecar field is now `selected_entry_memberships`. Each
`module_joins` item contains one `member` object with `full_entry_inventory`,
plus separate `selected_entries` and `unselected_entries`. This intentionally
differs from whole-module join-v4's `entry_memberships` and members array.
