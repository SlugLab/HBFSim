# Points in the implementation we would like you to check

**What this directory is.** Fourteen places in the code where our reading of the source
does not match what we expected, written up one point per file, plus two files that are
not about the code: a check of every claim we make about the device against the OCP
specification, and a priority-ordered list of work we could add before 2026-09-15. You
wrote the implementation, so every point here is a question rather than a finding: we
describe the lines we read, why the lines surprised us, and where our reading could be
wrong. Any of the fourteen may turn out to be a deliberate choice we did not recognise,
or a misreading on our side.

**The two files that are not code points.**

- `spec-conformance-findings.md` — what OCP HBF architecture specification v0.7.0
  actually says, with a page number for every item, and seven statements of our own that
  the specification forces us to withdraw or change. Four of the code points below exist
  because of findings in that file.
- `contributions-and-experiments-we-could-add.md` — eight pieces of work in the order we
  would do them, each with why, an effort estimate, and what we need you to decide.
  Includes our judgement on whether the read/write granularity asymmetry can be
  implemented and whether it can carry a contribution, together with the case against it.

**What each code point file contains.** Three lines at the top — severity, how sure we
are, and whether the point conflicts with the specification — then five sections in the
same order every time.

1. **What we read in the code** — the file path, the line numbers, and the lines
   themselves.
2. **Why it looks questionable to us** — the expectation the lines did not meet, with the
   document or claim that set the expectation.
3. **Which direction the effect goes** — whether the modeled number comes out above or
   below what the hardware would do. Where we have not measured a size, no size is given.
4. **Where we may have read it wrong** — the readings under which the code is correct as
   written.
5. **What we would like you to confirm** — the specific question to answer.

Files 01 to 06 were written before the severity and confidence lines existed, so for
those six the two fields appear in the table below only, not inside the files.

**Which version of the code these points were read against.** All code quoted in this
directory comes from the remote branch `origin/hybrid`, read with
`git show origin/hybrid:<path>`. The local checkout is two commits behind that branch:
`7f7ba90 cuda: decouple helper from fixed sm_120 target` and the merge commit `0b34feb`.
Nothing here was read from the local working tree. One exception, marked inside the file:
the two upstream MQSim lines quoted in entry 08 cannot be re-checked here, because the
`third_party/mqsim` submodule is not checked out in our tree.

## How the two ratings are defined

**Severity — how wrong the number we would report is.**

- **A** — a number we would report comes out wrong by more than an order of magnitude,
  or with the wrong sign, or the operation being modeled could not run on the real
  device. Something published would be wrong, not merely imprecise.
- **B** — the direction of the error is known and the size is not measured, or a claim in
  the paper has no mechanism behind it yet.
- **C** — a missing check or a missing disclosure. No number produced so far is wrong
  because of the point; what is lost is the ability to catch a future error.

**How sure we are — where our evidence comes from.**

- **Primary source** — we read the code line, the specification page, or the proof
  document ourselves.
- **Second-hand** — the point rests on material we have not read in the original.
- **Inference** — our own reasoning or arithmetic on top of checked facts.

Most entries mix the three; the table gives the level of the load-bearing part, and each
file says which of its own statements sit at which level.

## The fourteen points, most severe first

| # | File | Severity | How sure we are | Conflicts with the OCP specification? | The point |
|---|---|---|---|---|---|
| 1 | `00-write-path-may-not-be-a-store-instruction.md` | A | Primary source for the specification text, page number given for every sentence; inference for the step that no GPU instruction set has a store instruction with these semantics | Not a conflict with the specification — a question about which layer our emulation models | A write to HBF has to arrive as 64-byte pieces accumulated into 4 KiB within a host-set time limit, in sequential order inside a NAND block, erasing the whole block when page 0 is written, and returns a per-command status code. We model a write by rewriting `st.global` in PTX, so if the real write path is maintained by a runtime rather than issued as a store instruction, that path models an operation the device does not have. |
| 2 | `10-accounting-unit-is-the-kernel-launch.md` | A | Primary source for all code and both proofs; inference for the 0.275 s to 8.97 s range | No — and the specification is why the problem is hard: no per-access event exists outside the program under test | Accounting happens per kernel launch, charging happens per warp-merged request, and the two are not connected, so accesses that were skipped appear in no counter. In the first live run, 10,584 launches touched registered memory, 0 were timed, and 0 ns of media time was reported. |
| 3 | `11-write-charged-per-instruction-not-per-4kib-unit.md` | A | Primary source for the code and the specification; inference for the factor of 8 to 32 | **Yes** — section 5.4.1 item 3, page 58 | A store is charged one full program time per instruction, so filling a 4 KiB page costs 8 to 32 times what the specification says, 13.07 ms or 3.27 ms against 408,305 ns. |
| 4 | `13-nominal-profile-time-scale-100.md` | A | Primary source for the profile and the code; needs your confirmation of which profile the run used | No, but it interacts with item 11 of `docs/EXPERIMENTS-NEEDED.md` | The 164.70x figure was produced under a profile whose `time_scale` is 100, so every charged access costs 1 ms rather than 10 µs, and the proof document does not mention the factor. |
| 5 | `07-same-page-reread-charged-as-a-new-page.md` | A | Primary source for the code, the breakpoints and the specification; second-hand for the ONFI 6.0 figure | **Yes** — section 5.3.1 items 5a, 7, 8, pages 56 to 57, and register field `NCBB`, page 70 | Reading the same page again is charged 11,133 ns while reading the next page is charged 10,121 ns, so re-reading is modeled as about 10% more expensive. Both documents put the two cases the other way round. |
| 6 | `12-capacity-mode-rewrites-a-page-in-place.md` | A | Primary source for the code and the specification; inference for the mapping onto write error `0x2` and for the 104.5 ms figure | **Yes** — sections 11.5.2.2 and 11.5.2.6, pages 121 and 122 | The capacity mode writes an evicted page back to its own address. HBF forbids random writing inside a block; the legal alternative is a whole-block replay costing 104.5 ms, a write amplification of 256 times. |
| 7 | `01-cp-async-and-bulk-tensor-copy-unmatched.md` | B | Primary source: the six instructions were run through both regular expressions | No | `cp.async` and the bulk tensor copy instructions match neither the supported-form pattern nor the unsupported-list pattern, so accesses in those forms are neither modeled nor counted as unsupported. |
| 8 | `06-thermal-not-connected-to-timing.md` | B | Primary source: zero hits for six search terms in `src/` and `include/` | No | Temperature is calibrated by the scripts and never turns into an effect on access latency, so the paper's central claim has no mechanism behind it yet. |
| 9 | `03-no-page-residency-filter-across-warps.md` | B | Primary source | Partly — the same cache-buffer clauses as entry 07 | A page read again by a different warp, or later in time, is charged another full media access; the reported time comes out above what hardware would take, by an amount set by how much reuse the workload has. |
| 10 | `02-delay-injected-at-issue-not-at-use.md` | B | Primary source | No | The modeled delay is spent where the access is issued rather than where the loaded value is first used, which turns an access that could have been overlapped into a stall in place. |
| 11 | `04-one-scalar-for-all-channels.md` | B | Primary source | No, though a per-channel model would follow the per-bank ordering rules of section 5.3.1 | All traffic is serialized through one 64-bit counter, `fast_channel_tail_ns`, while the three parameter profiles declare 16, 32 and 64 channels with 8 dies each. |
| 12 | `09-erase-latency-is-a-derived-constant.md` | B | Primary source for the line and the profile value; inference for "no measurement stands behind it" | No — the specification contains no timing figure at all, which is why the number has to come from elsewhere | The block erase time is `program_latency_ns * 10`, which is 4,083,050 ns on the calibrated profile, with no measurement behind the factor of ten. |
| 13 | `08-reference-path-has-no-cache-read-either.md` | C | Primary source for the adapter; the two upstream MQSim lines cannot be re-checked in our tree | Indirectly — the same clauses as entry 07 | The detailed MQSim path charges the same latency for every read command, so it answers the same way as the fast path on entry 07 and cannot be used to settle the question. |
| 14 | `05-no-consistency-check-between-paths.md` | C | Primary source: searches for consistency, cross_check, agreement and divergence return nothing relevant | No | Nothing in the code compares the fast path against the detailed MQSim path, which the paper outline says must be checkable. |

## The entries that conflict with the OCP specification

Singled out because these are the ones a reviewer holding the specification could raise
without running anything:

- **Entry 11** — a write is charged per instruction instead of once per accumulated 4 KiB
  unit (section 5.4.1 item 3, page 58).
- **Entry 12** — the capacity mode performs an in-place page rewrite, which sections
  11.5.2.2 and 11.5.2.6 forbid (pages 121 and 122).
- **Entry 07** — a read that hits a cache buffer is charged the full first-page cost,
  while section 5.3.1 items 5a and 8 require it to be served immediately (pages 56 to 57).
- **Entry 08** — the detailed path has the same behaviour as entry 07, through the same
  clauses.
- **Entry 03** — partly, through the same clauses, for reuse across warps within the
  two-page depth the specification guarantees.

Our own wording errors, as opposed to the code, are collected in section 13 of
`spec-conformance-findings.md`; seven statements we have made about HBF have to be
withdrawn or changed.

## One claim we withdrew

We first thought that charging every access at page granularity was itself wrong.
Checking the code showed the claim does not hold, for two reasons: page granularity is the
right unit for NAND, because one NAND read reads a whole page; and accesses to the same
page from within one warp are already merged, at
`src/cuda_runtime/device/hbf_device.cu` lines 506 to 510 and 513 to 519. Thirty-two
accesses to the same page from one warp produce one modeled request. The withdrawn claim
is written out at the top of `03-no-page-residency-filter-across-warps.md` so that the
point that remains is not confused with the point that fell.

## How this directory relates to the other three lists

- **This directory** — places where the code does something other than what we expected,
  and we want to know whether the expectation or the reading was at fault, one file per
  point; plus the specification check and the list of work we could add.
- **`docs/QUESTIONS-FOR-COLLABORATOR.md`** — parameter values and design choices only you
  can settle: where a threshold came from, whether a constraint is deliberate, whether a
  document can be obtained through a channel we do not have. Item 4 of that file and entry
  05 here cover the same missing consistency check; item 5 of that file and entry 06 here
  cover the same disconnected thermal work. The two entries here restate the code evidence
  against `origin/hybrid` and stay inside the five-section form; the entries in
  `docs/QUESTIONS-FOR-COLLABORATOR.md` carry the suggested options. Answering either place
  answers both.
- **`docs/EXPERIMENTS-NEEDED.md`** — the work order: sixteen experiment items in six
  groups, ordered by which of the paper's contribution claims each item supports, each
  naming the number it must produce and how to run it. Section 9 of
  `contributions-and-experiments-we-could-add.md` says where the new work sits against
  those sixteen, and names three of the sixteen that need small corrections after the
  specification check.
- **`todo/facts-to-verify.md`** — facts that have to be checked before they are allowed
  into the paper.

Nothing in this directory has been changed in the code. Each file ends with the question
we would like answered, and we are not editing the lines in question until you answer.

Please tell us which of the points here you agree with, which ones you think we have read
wrong, and which ones you intend to change.
