# Independent semantic review: PR1, PR2, PR4

2026-09-26. Review only; no upstream/frozen edits, push, merge, installs or GPU execution. Root Astra decides merge readiness.

## Provenance and limits

DOC_DERIVED: source snapshot is local Concordia c60cfd8ab54fd1ec4512f749ad61cccc4a390a05 plus each independently applied saved prN.patch. Four source modules copied, only lifter patched. Unique isolated Cargo packages use existing offline dependencies, one compilation job. Sources and patch.log are retained in prN-crate. A git-show attempt could not obtain absent partial-clone objects offline; we therefore used the exact saved diffs and local base (all patches apply). These are source-equivalent targeted builds, not upstream workspace unit tests or LLVM tests.

Initial shared-target/same-package-name Cargo harness reused the PR2 binary incorrectly. Its results are INVALID, preserved in direct-results-invalid-cache.json and HARNESS_CACHE_FAILURE.md. Unique package/executable names fixed the harness; final direct-results.json is authoritative. No upstream source changed to make checks pass.

Compiler references use /usr/local/cuda-13.1/bin/ptxas and cuobjdump, sm_120, PTX8.8; exact version in ptxas.version. Commands: ptxas -arch=sm_120 INPUT.ptx -o OUTPUT.cubin; cuobjdump -sass OUTPUT.cubin. All reference PTX, cubins, disassemblies and build logs retained. run_checks.py reproduces direct text lifter checks. No numerical device execution or broad ISA certification.

Official primary source: https://docs.nvidia.com/cuda/parallel-thread-execution/#integer-arithmetic-instructions-mad defines PTX mad.hi as high half of product plus a 32-bit c (u32). https://docs.nvidia.com/cuda/parallel-thread-execution/#data-movement-and-conversion-instructions-cvt specifies packed input a goes to upper half and input b to lower half. https://docs.nvidia.com/cuda/parallel-thread-execution/#comparison-and-selection-instructions-setp defines unordered comparisons and boolean predicate combination. NVIDIA binary utilities only lists instruction families; it does not provide a complete normative SASS encoding specification. Compiler round-trip observations below are evidence for sm_120, not an official all-architecture SASS specification.

## PR1: bounded merge candidate

Exact encoded negative quiet-NaN preservation is model-independent. Fresh compiler references fsel_ffffffff.sass and fsel_ffc00000.sass independently contain FSEL R5,R4,-QNAN,!P0 with first words 0xffffffff04057808 and 0xffc0000004057808 respectively. Both have distinct payloads and identical display labels. The bit extraction and negative quiet-NaN mask are correct for this confirmed encoding.

Nine direct checks: allones and canonical+negated selection emit exact payloads; missing encoding, register opcode, wrong source position, positive NaN, infinity, signaling NaN and execution-predicated opcode safely diagnose unsupported. Low16=0x7808 intentionally excludes guarded encodings such as 0x1808; this is a safe support limit. No model, storage address, shape or QKV dependency exists.

Allones PTX assembles. A deliberately minimal canonical/!P0 fixture initially failed assembly because existing register collection does not declare a predicate appearing only as a negated selector. Original failure preserved. Adding an actual defining ISETP before FSEL makes the complete canonical-defined fixture assemble (rc0); this is an inherited frontend issue, not an encoded-payload regression, and ordinary real kernels define P0 first. No source repair performed.

Recommendation: semantic change itself is merge-ready within documented text/confirmed-encoding scope, subject to root checking exact remote identity/repository requirements. Do not claim arbitrary FSEL forms, binary decoder support or GPU equivalence. Added upstream tests are positive/missing only; our negative checks provide additional current evidence.

## PR2: DO NOT MERGE — incorrect register addend semantics

P1 blocker at pr2 lifter.rs lines1124–1137, especially emitted mad.hi.u32 at1136 (saved diff hunk starts1121).

Fresh mad.ptx asks for PTX mad.hi.u32 d,a,b,c with all parameters unknown. NVIDIA compiler produces:
- LDC R5,c[0x0][0x390] (c)
- HFMA2 R4,-RZ,RZ,0,0 (zero low half)
- LDC.64 R6,c[0x0][0x388] (a,b)
- IMAD.HI.U32 R5,R6,R7,R4

This SASS addend is the register pair R4:R5, not a standalone 32-bit R4. PR2 direct fixture emits mad.hi.u32 %r5,%r6,%r7,%r4 with no diagnostic. Taking a=b=0,c=7, compiler source requires 7; translated operation returns 0 because R4=0. This counterexample needs no QKV assumptions. The observed compile strongly establishes the pair interpretation for this target; generic operation is high32(a*b + paired c), including carry from low-half addition. RZ source3 is a bounded supported zero-addend special case; ordinary pair cannot map directly to PTX mad.hi with the low register.

Second issue: filter_map drops unsupported operands and shifts positions. IMAD.HI.U32 R5,R6,c[0x0][0x4],R4 becomes mad.hi.u32 %r5,%r6,%r4,0 with empty diagnostics (constant fixture). Even unsupported input must diagnose rather than fabricate another operation. Signed HI is correctly rejected. X modifier is currently silently accepted/ignored; no X semantic support established.

Required correction before readiness: preserve actual pair/carry semantics or reject everything outside proven RZ/addend domain; validate operand shape/modifiers without dropping operands; add arithmetic tests exercising nonzero pair halves, overflow/carry, zero and unsupported operands. Existing upstream string test for R18 would certify the wrong mapping.

## PR4: DO NOT MERGE for general claimed forms without boundary checks

Basic FSETP.GEU.AND P0,PT,Ra,Rb,PT -> setp.geu.f32 is correct for unordered NaN comparison; RN BF16 PACK_AB has correct type and lane order. Fresh geu.sass/pack_rn.sass corroborate this independently of any model.

P1 blocker at pr4 lifter.rs1211–1218: all BF16 PACK_AB variants emit RN and omit other semantic modifiers. Fresh compiler-generated pack_rz.sass contains F2FP.BF16.F32.PACK_AB.RZ, pack_relu.sass contains F2FP.RELU.BF16.F32.PACK_AB. Direct lifter probes accept both with no diagnostic and produce cvt.rn.bf16x2.f32. RZ example input 0x3f80c000 (=1.005859375) rounds toward zero to bf16 0x3f80 but RN gives0x3f81. RELU example -1 becomes zero in reference, but stays -1 after translation. These are real compiler-emitted forms; requirement is correct handling or safe rejection, not full ISA implementation.

Related inherited FSETP weaknesses remain: nontrivial final predicate (e.g. AND !PT), FTZ, second predicate result are not preserved by fsetp_op. Direct GEU.AND !PT emits bare geu and would return true for equal inputs, although AND false must be false. FTZ silently drops. These predate this patch's general handler; do not call them new regressions, but the newly corrected GEU support must be restricted or these combinations fail general correctness. Other comparison types intentionally retain preexisting behavior, including NEU collapsing to ordered NE; no broader comparison support claim is justified.

Minimal readiness route: explicitly guard supported BF16 RN/non-RELU forms and supported GEU predicate/FTZ combination, diagnosing others, or implement their semantics and test. Existing PR tests cover only benign PT/default-round forms and cannot establish generic correctness. Binary decoder modifier propagation is separate and was not exercised by this text harness.
