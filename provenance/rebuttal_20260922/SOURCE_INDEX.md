# Verified-source provenance index

Base commit: `eabc5c2c0820ac0d84c2f16ea3460b219f11ff83`.

| Item | Preserved form | Source on giga | Meaning |
|---|---|---|---|
| Current rebuttal worktree | Normal tracked files plus selected docs/experiment source | `/root/hbfsim-exp/rebuttal_20260921/worktree` | Current integration source; raw/build/model files excluded |
| Atomic GPU sidecar | `patches/atomic-sidecar-source-v1.patch` | `atomic-sidecar-source-v1` | Verified HostNativeAtomic=0 repair source delta |
| Compact diagnostic/runtime | `patches/first-fault-compact-source-v2.patch` | `first-fault-compact-source-v2` | Accepted compact diagnostic, runtime-domain, joint-session, and sidecar source delta |
| R3 scalar ABI | Three source files under `r3-scalar-abi-source-v2/` | `r3-scalar-abi-source-v2` | Independent R3 scalar-offset ABI correction source; it uses the baseline repository's `include/` tree |
| Runtime receipts | Files under `receipts/` | Corresponding frozen runtime/build directories | Artifact hashes and CPU receipts only; binaries excluded |
| Scientific review | Files under `reports/` | `report-final-review-20260922` and `report-tools-v1` | Latest reviewed claims and scope boundaries |

Patches are kept separately rather than merged into one synthetic tree because
the experiments used independently frozen runtime variants.  Applying all
patches together would create a source combination that was never validated.
Each patch is intended to be applied separately to a clean checkout of
`eabc5c2c0820ac0d84c2f16ea3460b219f11ff83`; do not apply it on top of this
already integrated baseline snapshot.
