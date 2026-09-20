# Weight-model metadata sources

The catalog `qwen2_5_weight_models.json` records metadata only.  No model tensor
or shard was downloaded.

For Qwen2.5-7B-Instruct, the official configuration declares BF16, 28 layers,
hidden size 3584, intermediate size 18944, 28 attention heads, 4 KV heads,
vocabulary size 152064, and untied input/output embeddings.  Its official
Safetensors index declares `metadata.total_size = 15231233024` bytes, while the
official model card labels it 7.61B parameters.

For Qwen2.5-72B-Instruct, the official configuration declares BF16, 80 layers,
hidden size 8192, intermediate size 29568, 64 attention heads, 8 KV heads,
vocabulary size 152064, and untied input/output embeddings.  Its official
Safetensors index declares `metadata.total_size = 145412407296` bytes, while the
official model card labels it 72.7B parameters.

The catalog keeps `revision=main` and `resolved_commit=UNKNOWN`: the mutable
official repository ref is recorded, but a commit was not inferred from an
unrelated file-history entry.  The exact source URLs are stored beside every
field group.

`weight_workloads.py` reconstructs a logical tensor-region order from those
architecture values.  The summed BF16 byte count exactly equals the official
Safetensors payload size for both models.  This makes layer and region address
windows reproducible, but does not claim those logical offsets are physical
offsets inside the published shard files.

Generated traffic is read-only weight traffic.  The caller supplies the request
period as a scenario input.  Token rate, activation and KV-cache traffic, cache
hits, inference-stage causality, and actual shard I/O order remain unavailable.
