# Points in the implementation we would like you to check

**What this directory is.** Six places in the code where our reading of the source
does not match what we expected, written up one point per file. You wrote the
implementation, so every point here is a question rather than a finding: we
describe the lines we read, why the lines surprised us, and where our reading
could be wrong. Any of the six may turn out to be a deliberate choice we did not
recognise, or a misreading on our side.

**What each file contains.** Five sections, in the same order every time.

1. **What we read in the code** — the file path, the line numbers, and the lines
   themselves.
2. **Why it looks questionable to us** — the expectation the lines did not meet,
   with the document or claim that set the expectation.
3. **Which direction the effect goes** — whether the modeled number comes out
   above or below what the hardware would do. Only the direction. Where we have
   not measured a size, no size is given.
4. **Where we may have read it wrong** — the readings under which the code is
   correct as written.
5. **What we would like you to confirm** — the specific question to answer.

**Which version of the code the six points were read against.** All of the code
quoted in this directory comes from the remote branch `origin/hybrid`, read with
`git show origin/hybrid:<path>`. The local checkout is two commits behind that
branch: `7f7ba90 cuda: decouple helper from fixed sm_120 target` and the merge
commit `0b34feb`. Nothing here was read from the local working tree.

## The six points

| File | Point | Status |
|---|---|---|
| `01-cp-async-and-bulk-tensor-copy-unmatched.md` | `cp.async` and the bulk tensor copy instructions match neither the supported-form pattern nor the unsupported-list pattern, so they are neither modeled nor counted as unsupported. | Checked against `origin/hybrid`; the six instructions in the table were run through both regular expressions. |
| `02-delay-injected-at-issue-not-at-use.md` | The modeled delay is spent where the access is issued, not where the loaded value is first used. | Checked against `origin/hybrid`. |
| `03-no-page-residency-filter-across-warps.md` | A page read again by a different warp, or later in time, is charged another full media access. | Checked against `origin/hybrid`. Contains one claim we withdrew — see below. |
| `04-one-scalar-for-all-channels.md` | All traffic is serialized through one 64-bit counter, while the three parameter profiles declare 16, 32 and 64 channels. | Checked against `origin/hybrid`. |
| `05-no-consistency-check-between-paths.md` | Nothing in the code compares the fast path against the detailed MQSim path, which the paper outline says must be checkable. | Checked against `origin/hybrid`. Same subject as item 4 of `docs/QUESTIONS-FOR-COLLABORATOR.md`. |
| `06-thermal-not-connected-to-timing.md` | Temperature is calibrated by the scripts but never turns into an effect on access latency. | Checked against `origin/hybrid`. Same subject as item 5 of `docs/QUESTIONS-FOR-COLLABORATOR.md`. |

**One claim we withdrew.** We first thought that charging every access at page
granularity was itself wrong. Checking the code showed the claim does not hold, for
two reasons: page granularity is the right unit for NAND, because one NAND read
reads a whole page; and accesses to the same page from within one warp are already
merged, at `src/cuda_runtime/device/hbf_device.cu` lines 506 to 510 and 513 to 519.
Thirty-two accesses to the same page from one warp produce one modeled request. The
withdrawn claim is written out at the top of
`03-no-page-residency-filter-across-warps.md` so that the point that remains is not
confused with the point that fell.

## How this directory relates to the other three lists

- **This directory** — places where the code does something other than what we
  expected, and we want to know whether the expectation or the reading was at
  fault. One file per point.
- **`docs/QUESTIONS-FOR-COLLABORATOR.md`** — parameter values and design choices
  only you can settle: where a threshold came from, whether a constraint is
  deliberate, whether a document can be obtained through a channel we do not have.
  Item 4 of that file and item 05 here cover the same missing consistency check;
  item 5 of that file and item 06 here cover the same disconnected thermal work.
  The two entries here restate the code evidence against `origin/hybrid` and stay
  inside the five-section form; the entries in `docs/QUESTIONS-FOR-COLLABORATOR.md`
  carry the suggested options. Answering either place answers both.
- **`docs/EXPERIMENTS-NEEDED.md`** — the work order: sixteen experiment items in
  six groups, ordered by which of the paper's contribution claims each item
  supports, each naming the number it must produce and how to run it.
- **`todo/facts-to-verify.md`** — facts that have to be checked before they are
  allowed into the paper.

Nothing in this directory has been changed in the code. Each file ends with the
question we would like answered, and we are not editing the lines in question
until you answer.


