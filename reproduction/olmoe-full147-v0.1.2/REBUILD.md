# Build and verification guide

This package contains the source overlays for the tested inference configuration.
A fresh complete source build and cross-machine GPU reproduction of this
publication are **UNVERIFIED**. Historical compile/link and runtime receipts
establish which artifacts ran; publication CPU checks establish only the
relocated packet's supported software properties. They are separate claims.

## Parent source and dependencies

The release tree is based on HBFSim v0.1.1 (`d2e6ce239c117c640fe68c8b5b2a7c77cf184da6`).
The historical runtime reconstruction has a different source base:

| Role | Pinned source |
|---|---|
| HBFSim runtime/helper/pass parent | `eabc5c2c0820ac0d84c2f16ea3460b219f11ff83` plus the accepted first-fault patch |
| bpftime/eGPU parent | `ec26daecc8e787fb80fd95dd596a576404a5e36e` plus the published exact-agent patch and the frozen overlays |
| MQSim parent | `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` |
| Model identity, acquired separately | `OLMoE-1B-7B-0924`, revision `6d84c48581ece794365f2b8e9cfb043c68ade9c5` |

Use the existing [parent source reconstruction](../sass-final147-20260926/REBUILD.md)
and its complete patches as the prerequisite. Do not apply the old WIP patch,
use a current dirty research checkout as a substitute, or reconstruct from
opaque `.a` files. The new exact sources are under `source/agent`, `source/hbf`
and `source/measurement`; `SOURCE_INDEX.json` records identities and lineage.

The declared target environment was Python3.13.9, torch2.9.1+cu128,
Triton3.5.1, vLLM0.15.1 and flashinfer-python0.6.1. The actual model result
observed the library versions; the in-process Python version was not separately
queried. Keep framework CUDA12.8 and private HBF CUDA13 domains separate.
Build recipes record g++13/g++15, LLVM20 and the original CUDA13 compute_120
helper target. Select tool paths through explicit configuration; a changed
GPU target or toolchain needs new applicability and correctness validation.
Do not upgrade dependencies mid-comparison.

## Agent

Follow [the frozen agent reconstruction](source/agent/README.md). The accepted
agent contains eight attach translation units. Its final core compile replaced
one archive member; earlier Frida/fatbin changes and the other retained members
have separate lineage. A partial relink is not a fresh eight-TU build.
Recreate parent source, apply the listed overlays at their canonical paths,
configure the recorded options with local source/build/tool/include roots, and
build every dependency from source. The observed final artifact identity is
`e84798ffc5de03a0f7570e6a8bbb5b01edacd7074db5646727e94f83ce498965`.
New compilers or build locations need new observed outputs and validation;
bit-identical output is not assumed.

## Helper and pass

The [helper/pass recipe](source/hbf/REBUILD_NOT_RUN.md) preserves the actual
source and compile-feature roles. The original helper explicitly enabled
`HBFSIM_ENABLE_UNIFORM_ACCOUNT_AGGREGATION=1` and wait timing diagnostics,
while disabling timing futures and the two evaluation-only diagnostics.
The source feature remains default off. Keep these compile-time choices
separate from the host strategy switches in `STRATEGY.json`.

Rebuild the helper from its exact CU/header/include inputs using the configured
CUDA13 compiler. Run the retained `EmbedDevicePtx.cmake`, then
`source/hbf/tools/serialize_helper_header.py` to generate the C++ embedding.
The raw helper PTX hash (`6fdadac5…`) differs from the normalized embedded
payload and generated header (`99abe…`); these roles must not be interchanged.
No generated PTX or header is distributed.

Rebuild the HBF core, pass and prerequisite archives from the accepted parent
sources and the exact transform overlay. The historical pass identity is
`ce2c6d5185a600753baa7d9a43f99e49b5e6a32c08842818a34497346e898154`.
The source/object/archive/link joins are preserved in
`source/hbf/HISTORICAL_SOURCE_BUILD_JOIN.json`. Reusing old archives does not
establish a fresh source rebuild.

## Model connection and science

`source/measurement` preserves the exact deployed loader, runner, timing
consumer, scope and selected validation sources. It is archived source, not a
new runnable live plan. Parent interfaces and model setup remain in
[INTERFACES.md](../sass-final147-20260926/INTERFACES.md). Obtain the nine licensed
or model-generated PTX inputs and native binding metadata separately; no new
SASS conversion was performed for the accepted timing attempt. Their absence
from this public packet is not permission to substitute another PTX family.

Use new roots, metadata, process/boot/GPU observations, owner lock, nonce,
source-bound GO and INTENT for any new run. Never copy numeric GPU addresses,
old run identities or old success flags. Preserve all original scientific
parameters and distinguish application16KiB requests from native4KiB media.
Configure the strategy from `STRATEGY.json`, with ELF caching and same-page
merging off and nominal HBF wait on.

Validate exact promised input/output tokens, the actual147 registered storage
addresses/bytes and selected49/existing98 consumers, module inventory/active
service, backend completion/errors, control windows, original19 timing keys,
two calls each, residual, metadata, original archive ACK, natural exit and
resource release. Registration alone is not modeled service. Do not fabricate
full-tensor finiteness or native NAND command exports when they were not
collected. The old batch post-hook failure remains failed even though separate
postterminal scientific acceptance succeeded.

## Publication-only checks

From this packet directory:

```sh
python3 -B verify_packet.py
python3 -B verify_packet.py --cpu --cxx c++
```

The second command builds/runs the retained independent-integer accounting
fixture in a temporary directory. It requires a C++20 compiler, does not load
CUDA, and does not validate GPU instruction behavior, model performance, NAND
trace equivalence or physical calibration. Exact publication checks and their
scope are recorded separately in `evidence/PUBLICATION_VALIDATION.json`.
