# Why the fixed HBFSim request passes while general instruction gaps exist

Status: source/captured-parameter analysis, not a new GPU run. Frozen HBFSim artifacts and original successful/failed evidence remain unchanged. USER_CONFIRMED scope: repair upstream independently. A successful fixed model request is not a proof for arbitrary SASS operands, rounding modes, shapes, or binary decoding.

## Exact implementation identity

HBFSim used the combined v7 lifter in `rebuttal_20260921/sass-lifter-20260924/task/qkv-output-repair-v1/src/sass/lifter.rs`, recorded SHA256 `0ec6653a78a2e8eb1ac5a8f5aa1a34c2fbe310fd5c7bb073bbb36a289ee12ca9`. It is not an individual upstream PR head. Its HI.U32 lowering also uses PTX mad.hi.u32, so the upstream high-pair-addend bug exists in this frozen source. We do not claim the HBFSim implementation already fixed it.

## PR2: a real dormant tail-path bug

DOC_DERIVED: both exact Li6 and Li7 SASS have two HI.U32 instructions at 0x4a0/0x4c0. The first consumes the R18:R19 pair. R18 is zeroed but R19 is reciprocal-derived data, not established zero; zeroing R18 alone DOES NOT make the old lowering correct.

Both captures have c[0x408]=0: the SEL at 0x160 produces R4=0, so 0x180 loads R4 from c[0x3c0] (2048). The instructions are behind the tail branch at 0x390. At 0x340, P0 is `R17 > R10`; 0x390 branches to 0x590 when false, skipping both HI instructions. Initial R17 is zero (CS2R pair), R10 = R4 - block.x * c[0x40c]. The loop advances R17 by block.x * c[0x40c] via R15, and exits using the R15 >= R4 predicate saved at 0x5f50. R17 receives R15 at 0x6e40 before R15 is reused; the saved P0 reaches the loop branch at 0x76f0. Thus an exact multiple of the tile size has no final partial tile that enters this path.

Observed saved native aggregate captures (152 bytes, parameter bank base 0x380):

| Capture | R4 input c[0x3c0] | block.x | c[0x40c] | c[0x410] | R17 at loop entries | R10 |
|---|---:|---:|---:|---:|---|---:|
| runs/li6-exact-head-lift-v1/native-launch.json | 2048 | 16 | 64 | 16 | 0, 1024 | 1024 |
| runs/router-li7-lift-output-v2/native-launch.json | 2048 | 32 | 64 | 32 | 0 | 0 |

For these captured inputs, `R17 > R10` is false at every entry. This is a static derivation from actual captured inputs, not an instruction execution trace. A secondary `c[0x410] == 1` bypass exists, but the actual values are 16/32, so it is NOT the explanation here. The original incorrect preliminary hypothesis is explicitly rejected.

The final147 logs contain actual selected entry/profile/grid/block/storage and output/service closure, but do not retain the complete aggregate bytes for each selected call. Therefore applying the above exact branch proof to every final147 call is INFERRED from the same contiguous width-2048 consumers, not a newly observed per-call branch trace. The measured output PASS remains valid for those outputs; the universal instruction claim was never established. New nonzero-high-word/carry/wrap/alias tests directly target the missing instruction domain.

## PR1, PR3, PR4, PR5: narrower forms than the general surface

DOC_DERIVED: `HBFSIM_INSTRUCTION_INVENTORY.json` inventories exact Li6/Li7 source images. Each has one bare CS2R, two FSEL, one GEU.AND, one default-rounding BF16 PACK_AB, 123 WIDE.U32 instructions, two HI.U32 instructions, and no ATOMG instruction.

- PR1: HBFSim uses the proved immediate negative-QNaN encoding and payload. Other encodings/execution predicates need explicit rejection or proof; matching the real workload did not test the negative boundary.
- PR3: HBFSim recovery uses cuobjdump text plus real CUBIN metadata. The generic built-in binary decoder's inability to identify SRZ is a distinct path, not exercised by this text recovery. Pair/predicate tests and public scope documentation close that claim gap without inventing decoder support.
- PR4: the two images use `F2FP.BF16.F32.PACK_AB` (default RN), not RZ/RELU. Their GEU form is `FSETP.GEU.AND P0, PT, R5, R0, PT`, without FTZ/dynamic boolean/live second predicate. Fixed-model PASS therefore does not validate the previously silently mislowered modes. Independent compiler references and mode-sensitive tests address them.
- PR5: the kernel's WIDE addressing forms use scalar R/UR multiplicands and an R pair addend. The existing generic int_add fixture uses immediate 4, which the original narrow repair rejected. Descriptor atomic handling is absent from these images. Operand variants and explicit wide-data rejection require their own integration tests; they cannot be inferred from QKV success.

## Result interpretation and repair boundary

Final147 epoch6712 is actual MODEL_CONNECTED_PASS for its declared fixed request/selected output comparisons and modeled service closure. It is stronger than conversion-only or instrumented-kernel-only evidence, but does not prove untaken tails, arbitrary shapes, every expert/prefill consumer, or all ISA modes. Registration extent is not all bytes touched.

Upstream fixes are generic operand/instruction semantics and guards with independent numerical oracles, negative cases and existing non-QKV fixtures. They are not deployed into the frozen HBFSim binaries and do not retroactively change old validation receipts. A future HBFSim successor would need its own build identity and relevant validation before inheriting deployment claims.
