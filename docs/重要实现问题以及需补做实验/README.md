# Points in the implementation we would like you to check

**What this directory is.** Fourteen places in the code where our reading of the source
does not match what we expected, written up one point per file and numbered 00 to 14 with
no 06, followed by five files that are not code points, numbered 15 to 19: the experiments that
have to be added before submission, the questions only you can answer, a priority-ordered
list of work we could add before 2026-09-15, a check of every claim we make about the
device against the OCP specification, and the hardware-grounded Evaluation plan. One further file sits in the subdirectory
`cuda-problems-from-new-collaborator/` and did not come from us. You wrote the
implementation, so every point here is a question rather than a finding: we describe the
lines we read, why the lines surprised us, and where our reading could be wrong. Any of
the fourteen may turn out to be a deliberate choice we did not recognise, or a misreading
on our side.

**What the two-digit number in each file name means.** The number is the severity rank of
the point, and the letter after the number is the severity itself: `00` to `05` carry
severity `A`, `07` to `12` carry `B`, and `13` and `14` carry `C`. The file listing is
therefore the severity ordering, so the directory shows the most severe point first
without anyone opening `README.md`. The point numbered `00` was written after the others
and has to be listed first; the number `00` puts the point at the front without
renumbering the rest a second time, and renumbering a second time is worth avoiding
because the previous renumbering broke seven cross-references elsewhere in the repository,
each of which had to be found and fixed by hand.

**Why there is no `06`.** The file numbered `06` wrote up the same point as the file
numbered `00` — the same source file, the same condition `previous_page + 1 == page`, the
same 11,133 ns against 10,121 ns, the same severity `A`, and the same conflict with the
specification. The two have been merged into `00`, which keeps the ONFI 6.0 figure and the
`BUCCAP` register field that only `06` carried, and `06` has been deleted. The number `06`
was left unused rather than closed up, because closing it up means renaming `07` through
`14` and fixing every cross-reference to those eight files a second time.

The numbering used before was the order in which the
points were written, which carried no meaning, and a point's number here does not match
the number the same point had earlier. The directory was renamed at the same time, from
`docs/可能潜在代码实现问题请求审查` to `docs/重要实现问题以及需补做实验`.

**The five files that are not code points.**

- `15-experiments-we-must-add-before-submission.md` — the experiments that have to be run
  before submission, sixteen items in six groups, described in full at the end of this
  README. Previously `docs/EXPERIMENTS-NEEDED.md`.
- `16-questions-only-you-can-answer.md` — parameter values and design choices only you can
  settle, described in full at the end of this README. Previously
  `docs/QUESTIONS-FOR-COLLABORATOR.md`.
- `17-contributions-and-experiments-we-could-add.md` — eight pieces of work in the order we
  would do them, each with why, an effort estimate, and what we need you to decide.
  Includes our judgement on whether the read/write granularity asymmetry can be
  implemented and whether it can carry a contribution, together with the case against it.
- `18-spec-conformance-findings.md` — what OCP HBF architecture specification v0.7.0
  actually says, with a page number for every item, and seven statements of our own that
  the specification forces us to withdraw or change. Four of the code points below exist
  because of findings in that file.
- `19-hardware-grounded-evaluation-plan.md` — hardware-grounded Evaluation structure,
  validation contract, fidelity/cost methodology, and concentrated HBM–HBF LLM
  design-space study.

**The subdirectory `cuda-problems-from-new-collaborator/`.** One file,
`cuda-architecture-compatibility.md`, which is not one of our review points. That file
arrived with the commit `7f7ba90 cuda: decouple helper from fixed sm_120 target`, so it
was written on the implementation side rather than by us. The file is kept in a
subdirectory of its own so that nobody reads it as a point we are raising.

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

Files 07, 08, 09, 10, 11 and 14 were written before the severity and confidence lines
existed, so for those six files the severity and the confidence level appear in the table
below only, not inside the file.

**Which version of the code these points were read against.** All code quoted in this
directory comes from the remote branch `origin/hybrid`, read with
`git show origin/hybrid:<path>`. The local checkout is two commits behind that branch:
`7f7ba90 cuda: decouple helper from fixed sm_120 target` and the merge commit `0b34feb`.
Nothing here was read from the local working tree. One exception, marked inside the file:
the two upstream MQSim lines quoted in entry 13 cannot be re-checked here, because the
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
| 1 | `00-A-repeated-read-of-the-same-page-is-charged-more.md` | A | Primary source for the code lines and for the three specification sentences, each quoted with its page number; second-hand for the ONFI 6.0 figure; inference for the 10.0% difference | **Yes** — section 5.3.1 items 5 a, 7 and 8, pages 56 to 57, and register field `NCBB`, page 70 | The run length is raised only when the page number of an access is exactly one greater than the page number of the previous access, so a repeated read of the same page resets the run length to 1 and is charged a whole first page: 11,133 ns against 10,121 ns for a sequential read of the next page, about 10.0% more. The specification requires a repeated read of the same page to hit a cache buffer and be served immediately, and ONFI 6.0 puts the two cases the same way round. |
| 2 | `01-A-write-path-may-not-be-a-store-instruction.md` | A | Primary source for the specification text, page number given for every sentence; inference for the step that no GPU instruction set has a store instruction with these semantics | Not a conflict with the specification — a question about which layer our emulation models | A write to HBF has to arrive as 64-byte pieces accumulated into 4 KiB within a host-set time limit, in sequential order inside a NAND block, erasing the whole block when page 0 is written, and returns a per-command status code. We model a write by rewriting `st.global` in PTX, so if the real write path is maintained by a runtime rather than issued as a store instruction, that path models an operation the device does not have. |
| 3 | `05-A-accounting-unit-is-the-kernel-launch.md` | A | Primary source for all code and both proofs; inference for the 0.275 s to 8.97 s range | No — and the specification is why the problem is hard: no per-access event exists outside the program under test | Accounting happens per kernel launch, charging happens per warp-merged request, and the two are not connected, so accesses that were skipped appear in no counter. In the first live run, 10,584 launches touched registered memory, 0 were timed, and 0 ns of media time was reported. |
| 4 | `03-A-write-charged-per-instruction-not-per-4kib.md` | A | Primary source for the code and the specification; inference for the factor of 8 to 32 | **Yes** — section 5.4.1 item 3, page 58 | A store is charged one full program time per instruction, so filling a 4 KiB page costs 8 to 32 times what the specification says, 13.07 ms or 3.27 ms against 408,305 ns. |
| 5 | `02-A-headline-164x-came-from-a-100x-time-scale.md` | A | Primary source for the profile and the code; needs your confirmation of which profile the run used | No, but it interacts with item 15 of `15-experiments-we-must-add-before-submission.md` (item 11 before that document was renumbered) | The 164.70x figure was produced under a profile whose `time_scale` is 100, so every charged access costs 1 ms rather than 10 µs, and the proof document does not mention the factor. |
| 6 | `04-A-capacity-mode-rewrites-a-page-in-place.md` | A | Primary source for the code and the specification; inference for the mapping onto write error `0x2` and for the 104.5 ms figure | **Yes** — sections 11.5.2.2 and 11.5.2.6, pages 121 and 122 | The capacity mode writes an evicted page back to its own address. HBF forbids random writing inside a block; the legal alternative is a whole-block replay costing 104.5 ms, a write amplification of 256 times. |
| 7 | `07-B-cp-async-and-bulk-tensor-copy-unmatched.md` | B | Primary source: the six instructions were run through both regular expressions | No | `cp.async` and the bulk tensor copy instructions match neither the supported-form pattern nor the unsupported-list pattern, so accesses in those forms are neither modeled nor counted as unsupported. |
| 8 | `08-B-thermal-not-connected-to-timing.md` | B | Primary source: zero hits for six search terms in `src/` and `include/` | No | Temperature is calibrated by the scripts and never turns into an effect on access latency, so the paper's central claim has no mechanism behind it yet. |
| 9 | `09-B-no-page-residency-filter-across-warps.md` | B | Primary source | Partly — the same cache-buffer clauses as entry 00 | A page read again by a different warp, or later in time, is charged another full media access; the reported time comes out above what hardware would take, by an amount set by how much reuse the workload has. |
| 10 | `10-B-delay-injected-at-issue-not-at-use.md` | B | Primary source | No | The modeled delay is spent where the access is issued rather than where the loaded value is first used, which turns an access that could have been overlapped into a stall in place. |
| 11 | `11-B-one-scalar-for-all-channels.md` | B | Primary source | No, though a per-channel model would follow the per-bank ordering rules of section 5.3.1 | All traffic is serialized through one 64-bit counter, `fast_channel_tail_ns`, while the three parameter profiles declare 16, 32 and 64 channels with 8 dies each. |
| 12 | `12-B-erase-latency-is-a-derived-constant.md` | B | Primary source for the line and the profile value; inference for "no measurement stands behind it" | No — the specification contains no timing figure at all, which is why the number has to come from elsewhere | The block erase time is `program_latency_ns * 10`, which is 4,083,050 ns on the calibrated profile, with no measurement behind the factor of ten. |
| 13 | `13-C-reference-path-has-no-cache-read-either.md` | C | Primary source for the adapter; the two upstream MQSim lines cannot be re-checked in our tree | Indirectly — the same clauses as entry 00 | The detailed MQSim path charges the same latency for every read command, so it answers the same way as the fast path on entry 00 and cannot be used to settle the question. |
| 14 | `14-C-no-consistency-check-between-paths.md` | C | Primary source: searches for consistency, cross_check, agreement and divergence return nothing relevant | No | Nothing in the code compares the fast path against the detailed MQSim path, which the paper outline says must be checkable. |

## The entries that conflict with the OCP specification

Singled out because these are the ones a reviewer holding the specification could raise
without running anything:

- **Entry 00** — a repeated read of the same page resets the run length and is therefore
  charged a whole first page, while section 5.3.1 item 5 a and item 8 require a read that
  hits a cache buffer to be served immediately (pages 56 to 57), and item 7 together with
  the register field `NCBB` on page 70 gives the two cache buffers per bank the hit would
  land in.
- **Entry 03** — a write is charged per instruction instead of once per accumulated 4 KiB
  unit (section 5.4.1 item 3, page 58).
- **Entry 04** — the capacity mode performs an in-place page rewrite, which sections
  11.5.2.2 and 11.5.2.6 forbid (pages 121 and 122).
- **Entry 13** — the detailed path has the same behaviour as entry 00, through the same
  clauses.
- **Entry 09** — partly, through the same clauses, for reuse across warps within the
  two-page depth the specification guarantees.

Our own wording errors, as opposed to the code, are collected in section 13 of
`18-spec-conformance-findings.md`; seven statements we have made about HBF have to be
withdrawn or changed.

## One claim we withdrew

We first thought that charging every access at page granularity was itself wrong.
Checking the code showed the claim does not hold, for two reasons: page granularity is the
right unit for NAND, because one NAND read reads a whole page; and accesses to the same
page from within one warp are already merged, at
`src/cuda_runtime/device/hbf_device.cu` lines 506 to 510 and 513 to 519. Thirty-two
accesses to the same page from one warp produce one modeled request. The withdrawn claim
is written out at the top of `09-B-no-page-residency-filter-across-warps.md` so that the
point that remains is not confused with the point that fell.

## What each file here is for, and the one list kept outside this directory

The fourteen code points and the five files that go with the fourteen code points all live
in this directory. One list is still kept outside: `todo/facts-to-verify.md`.

- **Files `00-...` through `14-...`** — places where the code does something other than
  what we expected, and we want to know whether the expectation or the reading was at
  fault, one file per point.
- **`15-experiments-we-must-add-before-submission.md`** — the work order: twenty-three experiment items in eight
  groups, ordered by which of the paper's contribution claims each item supports, each
  naming the number it must produce and how to run it. Section 9 of
  `17-contributions-and-experiments-we-could-add.md` says where the new work sits against
  those sixteen, and names three of the sixteen that need small corrections after the
  specification check.
- **`16-questions-only-you-can-answer.md`** — parameter values and design choices only you
  can settle: where a threshold came from, whether a constraint is deliberate, whether a
  document can be obtained through a channel we do not have. Item 4 of
  `16-questions-only-you-can-answer.md` and entry 14 here cover the same missing
  consistency check; item 5 of `16-questions-only-you-can-answer.md` and entry 08 here
  cover the same disconnected thermal work. The two entries here restate the code evidence
  against `origin/hybrid` and stay inside the five-section form; the entries in
  `16-questions-only-you-can-answer.md` carry the suggested options. Answering either place
  answers both.
- **`17-contributions-and-experiments-we-could-add.md` and
  `18-spec-conformance-findings.md`** — the work we could add and the check against the OCP
  specification, both described at the top of this README.
- **`19-hardware-grounded-evaluation-plan.md`** — the formal Evaluation methodology and
  execution plan, including hardware reference hierarchy, fidelity/cost validation, claim
  gates, and the concentrated HBM–HBF LLM design-space study.
- **`todo/facts-to-verify.md`, the one list outside this directory** — facts that have to
  be checked before they are allowed into the paper.

Nothing in this directory has been changed in the code. Each file ends with the question
we would like answered, and we are not editing the lines in question until you answer.

Please tell us which of the points here you agree with, which ones you think we have read
wrong, and which ones you intend to change.
