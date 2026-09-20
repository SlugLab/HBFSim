# Defect review and fixes

Seven commits on the default branch `main` fix six groups of defects in HBFSim's timing and
instrumentation path, and leave one further defect measured rather than fixed. **Three of the seven
commits fix defects that an earlier round had already reported as handled**, so the lesson carried
forward here is that a commit message announcing a class of defect closed is not evidence that every
instance of the class is closed.

Two facts bound everything below. A build with both optional components enabled registers 62 tests,
of which 60 pass; the two failures are the two the repository owner had already documented as limited
by the machine rather than by the code, and both were reproduced independently. The device source
file `src/cuda_runtime/device/hbf_device.cu` has not been compiled at all, because the machine used
for the review has no CUDA compiler.

Limits that still stand come first, because the limits bound what the fixes are worth. Each defect
entry then says four things: what the thing under discussion is, what goes wrong concretely if the
defect is not fixed, whether anything can honestly be said about why the same defect does not arise
elsewhere, and the assumptions under which the entry's conclusion stops holding.

## What this document records, and what is not in it

The seven commits were produced across two working sessions, each session driven by an independent
review of the code. The reasoning behind the changes does not fit in a commit message, which is the
reason this document exists.

**A fuller working record exists outside this repository and is deliberately not published here.**
The outside record holds the complete text of two external review conversations, which are
third-party conversation content, along with internal working notes written in Chinese; this
repository withdrew its internal Chinese working documents on 2026-09-19. The outside record is the
source and is updated first. The document you are reading is the public record, written from the
outside record.

## What is still open or still limited

### A frame can be reassigned before the GPU has read the frame

The page-frame cache is the structure that holds a fixed number of page-sized slots of GPU memory and
assigns each slot, called a frame, to one simulated page at a time. A frame is handed to the GPU, and
the GPU then reads the frame. Between the two moments there is an interval, and during the interval
the eviction scan can take the same frame back: one call to the scan can clear a frame's second-chance
bit — the bit that spares a frame on first encounter instead of evicting it — and then reach the same
frame again and evict it. **The defect is measured rather than fixed.**

What goes wrong if the interval is not removed: the GPU reads a frame that now belongs to a different
simulated page, so the access returns the contents of some other page. The failure mode is a wrong
value rather than a crash or a timing error. `tests/cpu/hbm_cache_test.cpp` asserts the current
behaviour, so whoever removes the interval has to come and change the expectation rather than
discovering the assertion by accident.

Removing the interval needs a message in the device protocol that says an access has retired, and the
device protocol carries no such message; adding one is a protocol change rather than a local repair.
Any design in which the host learns that an access has retired before reusing the frame does not have
the interval at all. We do not know of a published comparison with another GPU memory simulator on
this point, and no such comparison was attempted.

Nothing currently published is affected. The only published run that used the page-cache path touched
128 distinct pages against a cache of 131,072 frames, a peak occupancy of 0.0977 percent, so the
eviction code was never entered; every other published figure uses a mode in which the cache object is
never created. *This paragraph was checked by a subagent reading the source and was not re-verified
line by line by the main agent.*

The conclusion holds only while eviction is reached through occupancy. The protection covers one
eviction entry point, and the flush paths are a second one: the flush paths call the eviction routine
without filtering for modified pages, so a flush issued while a device access is outstanding can
trigger the defect even when the working set always fits in the cache.

### The device source has not been compiled

`src/cuda_runtime/device/hbf_device.cu` is the file that holds the code running on the GPU itself.
The file has not been compiled during this work, because the machine used for the review has no CUDA
compiler installed.

What that costs concretely: the tests pin the policy helper functions and the predicates, which are
ordinary host-compilable code, so a helper that computes a wrong sleep interval or a predicate that
accepts the wrong range would be caught. The claim that every call site in the device code actually
uses those helpers is a property established by reading the source, not by an assertion that fails
when the property breaks. A future edit that reintroduces a hand-written loop at one call site would
pass every test in the repository.

The limit disappears as soon as the device file is compiled and a device-side test is run on real
hardware, and until then it applies to every device-side claim in this document.

### The timing-future path cannot be exercised with any profile in this repository

The timing-future path is the mode in which a memory access is started at one instruction and its
result is waited for at a later instruction, rather than the thread stopping where the access starts.
The gate that admits a profile to the path is at `include/hbfsim/timing_future_abi.hpp:118-120` and
requires `mode==1 && empirical_flags==0 && time_scale==1` together with four non-zero fields.

No profile shipped here satisfies the gate. Of the 19 profiles under `configs/` that carry a
`time_scale` field, 18 have `time_scale` 100; the one profile that has `time_scale` 1,
`configs/profiles/cd8p-vmem-p50.json`, carries an `empirical_vmem` block, which sets `empirical_flags`
to 1. The repository's own planning document states the same thing at
`docs/49-eval-audit/execution-plan.md:41`.

The consequence for the rest of this document is a good one rather than a bad one: the emitter fix
described under *The backing load was consumed at the instruction that issued the load* changes no
published measurement. `HBFSIM_ENABLE_TIMING_FUTURES` defaults to off at `CMakeLists.txt:8`, and no
document under `docs/proofs/` mentions the timing-future path. **The same fact also means the fix is
unverified by measurement**: nothing in the repository can run the fixed emitter end to end, so the
fix rests on reading the emitted code. Shipping one profile that satisfies the gate would remove both
halves of the situation at once.

## A gap in what this round reported, found by someone else

**The test coverage reported after the last of the seven commits was incomplete, and another
contributor found the gap rather than the review that claimed the coverage.** After `a2a3387`, commit
`5e7c09c` by the repository owner corrected two integration tests that the service-time change had
broken: `tests/integration/test_trace_timing.py`, where `modeled_device_service_ns` went from 20,064
to 20,000, and `tests/integration/test_trace_replay_timing.py`, where `modeled_device_time_ns` went
from 30,096 to 30,000.

The root cause, verified during this review: the binary those two tests drive, `hbf_trace_timing`, is
built only when `HBFSIM_ENABLE_MQSIM` and `HBFSIM_ENABLE_EVAL_TOOLS` are both on, at
`CMakeLists.txt:1038` and `CMakeLists.txt:1053`. Both build configurations used while the service-time
change was made had `HBFSIM_ENABLE_MQSIM=OFF`, so the two tests were never registered with the test
runner, and a report that every test passed was true of the tests that ran and silent about the tests
that did not exist in that configuration.

What remains true is that no published number changed. The values 20,064 and 30,096 appear nowhere
under `docs/`, checked again during this review. The gap was in test coverage, not in results.

A build with both options on has since been run: 60 of 62 tests pass. The two failures are the two
the repository owner had documented as limited by the machine, and both were reproduced independently
here — `context_lifecycle` exhausts memory under the process address-space limit, and `vmem_tuning`
needs an external performance file that is not present. A third test, `mqsim_service_client`, fails
when the build directory lies outside the checkout, because the test refuses to run in that
arrangement; built inside the checkout, the test passes.

The practical rule that follows is narrow and worth stating plainly: a pass count means nothing
without the build configuration that produced the count, because an option left off silently removes
tests rather than failing them.

## The defects that are fixed

### Three wait loops slept past the time they were waiting for

A wait loop here is a loop in the device code that sleeps in small steps until the modeled completion
time of a memory access has passed, at which point the access is allowed to proceed. The old loops
chose each step by doubling an interval that started at 64 nanoseconds, **without ever consulting how
much time was left to wait**.

Concretely, with a modeled completion time 10,000 nanoseconds away: the loop has slept 8,128
nanoseconds in total, the target is not reached, so the next step is 8,192 nanoseconds, and the thread
wakes at 16,320 nanoseconds. Every injected read comes out 63 percent slow. A 1,000 nanosecond target
is worse in relative terms — the thread wakes at 1,984 nanoseconds, 98 percent over. The error is
systematic rather than random: every injected read in a run overshoots, and always in the direction
that makes the modeled device look slower than the model says.

The replacement never asks for more than half the remaining time. Halving is enough because the PTX
instruction set specifies the duration of `nanosleep` as "approximated, but guaranteed to be in the
interval [0, 2\*t]", so a request for half the remaining time cannot overshoot the target.

`git grep 'while (gpu_time_ns() < target)'` returns three sites. Commit `b20834f` converted two of
them, and the commit message claimed the class of defect was closed. The third site, in
`wait_for_completion` on the reference path, was converted only later, in `a135f48`. The third site
was the worst of the three: `bounded_sleep` takes its interval by reference, and the completion-ring
wait above `wait_for_completion` has already doubled that interval, so on entry the interval can
already sit at the 1,048,576 nanosecond cap — a 10,000 nanosecond modeled delay could be served by a
single sleep of about one millisecond.

One call to `bounded_sleep` survives on purpose, on the completion-ring wait itself. The
completion-ring wait waits on the host process, whose finish time is not known to the device, and
doubling the interval is the right policy when the target is unknown. What makes doubling wrong at
the other three sites is precisely that the target time is known there, which is also the reason a
simulator that advances a clock of its own rather than waiting on a real one never meets this defect:
no thread sleeps, so no sleep can overshoot. HBFSim sleeps because the application runs on the real
device while the modeled timing is injected into the real run.

The argument for halving holds only while the `[0, 2*t]` bound in the instruction set holds. On
hardware or an instruction-set revision where `nanosleep` may exceed twice the requested duration,
halving the remaining time would no longer bound the wake-up time, and the policy would have to be
re-derived from whatever bound replaced it.

### The rewriter did not report global accesses it could not rewrite

PTX is the intermediate code NVIDIA's compiler produces on the way to machine code; HBFSim rewrites
some of the memory instructions in PTX so that the accesses go through the simulator instead of
straight to memory. Alongside the rewriting there is a scan whose job is to list the instructions the
rewriter could not handle, and **the scan excluded global loads and stores** — the one class of
instruction the project exists to model.

What goes wrong concretely: an access the rewriter could not parse was neither rewritten nor
reported, so a kernel could be marked as fully instrumented while one of the kernel's accesses ran at
full native speed. The coverage figure — how many of a kernel's accesses were routed through the
simulator and how many were not — then says nothing, because the accesses that were missed are
missing from both sides of the count. A run in that state does not look broken; the run looks fast.

Commit `b20834f` brought global loads and stores into the scan. Commit `05a6987` then changed the
counting from one count per source line to one count per access, which took a test fixture from 3
reported to 4: a single line carrying two accesses had been reported once.

We do not know of a published comparison with another instrumentation tool on how unhandled accesses
are counted. The mechanism worth naming is internal to this project: HBFSim's stated policy is to
refuse to run rather than skip an instruction silently, and excluding a class of instruction from the
scan defeats the policy for exactly that class, because refusing requires noticing first.

The per-access counting matters only where one source line can carry more than one access; on a
fixture where every line carries one access, per-line and per-access counts agree, and the change
would be invisible.

### An access starting outside a registered range but reaching into one was passed through

A registered range is a region of memory the simulator has been told to model, for example the first
16,384 bytes of a model weight tensor. An access can cross the boundary of a registered range in two
directions. Starting inside the range and ending past the end was already rejected. Starting outside
the range and reaching into the range was not: the lookup keys on the start address only, finds no
range, and returns a status meaning *use the real address directly*.

What goes wrong concretely: the bytes of the access that land on simulated memory are neither modeled
nor rejected, and the access is counted as a legitimate bypass. Nothing in the run reports a problem,
because the counter that would have shown the problem records the access as a normal one.

The mechanism that makes the asymmetry visible is in the same file: the store side already treated any
overlap with a registered range as disqualifying, so the load side was the one side out of step, and
bringing the load side into line with the store side is the whole of the fix in `a135f48`. We do not
know of a published comparison with another simulator here either.

The regression test added in `04383eb` is the part worth remembering. **The test asserted only the
direction that already worked**, which recorded a live defect as settled and would have kept the defect
settled indefinitely. The test now covers both directions in one case.

The conclusion is bounded by what rejection means for a workload that legitimately issues such
accesses: after the fix, a run that makes an access overlapping a registered range from outside stops
instead of mismodeling silently. For a workload where those accesses are expected rather than a bug,
the fix converts silent wrong numbers into a refusal to run, which is the trade this project makes
everywhere and is still a behaviour change.

### The backing load was consumed at the instruction that issued the load

HBFSim separates issuing a memory access from consuming the result of the access, so that the wait
for the modeled completion time falls on the instruction that reads the value rather than on the
instruction that starts the access. The real load that fetches the value, called the backing load
below, is supposed to sit in the background while the kernel's own unrelated instructions keep
running. **The emitted code defeated the separation.**

The rewriter widened the loaded value into the register the wait helper takes, on the instruction
immediately after the backing load. The scoreboard — the hardware unit that stalls a warp, the group
of GPU threads scheduled as one unit, until a register the warp reads is ready — therefore stalled at
the issue site, and every backing load became a blocking load. What is lost is the entire reason the
separation exists. Verbatim from the emitter's output, with the entry point renamed to `kernel`:

```
214:  @%__tf_exec15 ld.global.u32 %__tf_raw15, [%__tf_address1];
215:  @%__tf_exec15 mov.b32 %__tf_rawbits15, %__tf_raw15;
216:  @%__tf_exec15 cvt.u64.u32 %__tf_nativebits15, %__tf_rawbits15;
```

The value is not needed until line 246. The roughly 30 instructions in between, one of which is an
independent operation belonging to the kernel itself, are the instructions that were supposed to keep
running while the access was still outstanding, and did not.

Deleting lines 215 and 216 is not an option: the wait helper takes the value as a parameter and
returns the value, and the returned value is what finally writes the original destination register.
The widening therefore moved to the first instruction that really consumes the value. After the change
in `70ec96c` the backing load is followed only by `mov.pred %__tf_valid15, 1;`, and the first read of
`%__tf_raw15` is at line 242, just before the wait call at line 250.

A design that stops the thread at the instruction which starts the access cannot have this defect,
because a design of that shape never tries to keep later instructions running; the price paid for not
having the defect is that no instruction ever runs while an access is outstanding. The comparison is
between two designs rather than between two systems, and no comparison against another system was
attempted.

Two boundaries apply. The fix is checked by reading the emitted code, because no profile shipped here
can run the timing-future path at all, as recorded above. And the roughly 30 instructions are a
property of the fixture that was inspected: on a kernel with no independent work between issuing an
access and consuming the result, the separation buys nothing and the defect would have cost nothing
either.

### One service-time model, two implementations, disagreeing

`hbfsim::fast_service_ns` in `src/hybrid/calibrator.cpp` and `hbfsim::device::fast_service_ns` in
`src/cuda_runtime/device/hbf_device.cuh` compute how long one request takes to be served under a
given profile. The two functions are the same model under the same name in two namespaces. An earlier
round changed the device copy to return the larger of the first-byte latency and the transfer time,
and left the host copy adding the two together, so **the same profile and the same request produced
two different service times depending on which side asked**.

The size of the disagreement in one concrete case is visible in the two integration-test expectations
corrected by the repository owner in `5e7c09c`: 20,064 nanoseconds under the adding form against
20,000 under the larger-of form. A disagreement of that shape does not announce itself, because each
side is internally consistent; a number is simply wrong depending on where the number was computed.

Commit `a2a3387` brings the host copy back in step with the device copy and adds a test that pins the
two functions to each other across both operations, three shipped profiles, and seven request sizes.
The test that already existed could not catch the drift, and the reason is worth naming, because the
reason generalises: every assertion in the older test compares one profile against another profile,
and the ordering between profiles holds under either definition. A test that compares two inputs to
each other is blind to a change that moves both inputs the same way. Only pinning one implementation
to the other implementation detects the drift.

A third implementation deliberately still adds the first-byte latency and the transfer time, and the
difference is now documented where a reader will meet it. `scalar_prediction` in
`scripts/tune_vmem_profile.py` is the naive parameter-sheet closed form that the calibration compares
measurements against — the function exists in order to be compared with, not in order to be accurate.
`scalar_prediction` now carries a comment saying that it is deliberately a sum and must not be
changed to match, and `hbfsim::fast_service_ns` in `src/hybrid/calibrator.cpp` carries the reverse
reference, so that a reader arriving from either side does not reconcile the two forms and destroy
the comparison.

Two limits on the conclusion. The pinning test covers three shipped profiles and seven request sizes;
a profile or a request size outside that set is not covered by the test, only by the shared source of
the formula. And pinning one implementation to another establishes agreement, not correctness: if the
larger-of form is the wrong model, both sides are now wrong together, and the calibration data rather
than the test is what decides that question.

### Four findings from the correctness review

Commit `18663a2` acts on four findings from a review whose object was the previous round's own work.
**One of the four was introduced by the previous round rather than found by it**, which is the single
most useful thing in this section: a review that only looks for pre-existing defects will not find
the defects the review's own fixes create.

**The clamp that was missing from the new sleep.** The sleep added in the previous round takes a
modeled completion time as its target, and nothing clamped that target to the deadline of the
request. A request has a deadline after which the code declares a timeout; a sleep aimed past the
deadline delays the timeout check by as much as one whole sleep, and the instruction set caps one
sleep at one millisecond. Concretely, a request that should be declared timed out at the deadline can
keep waiting for up to a millisecond longer, against modeled delays that the examples above measure
in thousands of nanoseconds. The target is now the earlier of the completion time and the deadline.
The clamp matters only where a modeled completion time can fall past the deadline; with short modeled
delays and a distant deadline, the two forms behave identically, which is why the defect survived the
round that created the defect.

**A test that asserted less than the guarantee.** The test covering the sleep policy asserted a bound
that gave away 64 nanoseconds of slack and never checked the per-call limit at all. The reviewer
demonstrated the gap in the strongest available form: an implementation that does overshoot, against
which every committed assertion passed. The test now asserts the guarantee directly across 25 probe
values, and carries the overshooting implementation inline so that the test asserts the check rejects
the overshooting implementation. A test that asserts a weaker bound than the property being defended
is not a weak test but an absent one, and nothing about the arrangement is specific to sleeps.

**A fixture that could not reach the case the fixture guards.** The fixture written for the coverage
fix contained only accesses the rewriter cannot parse, so the kernel is not marked instrumented in
that fixture whether the fix is present or not, and the fixture would have passed with the fix
reverted. The replacement pairs one access the rewriter can rewrite with one access the rewriter
cannot, which is the only combination in which the defect is observable. The new assertions were
checked by reverting the rewriter's pattern, rebuilding, and watching the test fail, which is the
check that distinguishes a test that passes from a test that could fail.

**A comment that described a pattern the pattern did not match.** A comment added in the previous
round stated that the scan catches inline assembly in its own right. The pattern was `asm\s*\(`,
which does not match `asm volatile (` — the form almost everything writes, and therefore the form the
comment most needed to cover. Faced with a comment and a pattern that disagree, the round changed the
pattern to accept `asm volatile (` rather than softening the comment to fit the gap; the comment
became true. Where the source uses a form of inline assembly that the widened pattern still does not
match, the comment is again ahead of the pattern, and the pattern is the part to change.

## The state of the branch the fixes now sit on

The default branch `main` carries three further commits from the repository owner: a merge of the
thermal development, `8475fa2`; the test alignment described above, `5e7c09c`; and a validation
record, `a93c0c1`.

The three commits add files under `eq3_thermal` and change three integration tests. **The three
commits modify no existing file under `src/cuda_runtime/`, `src/host_service/` or `tests/cpu/`**,
which was verified first-hand, so every analysis and every fix recorded in this document applies
unchanged on the current branch. Anyone reading this document against a later state of `main` should
repeat that check before relying on the sentence.

## Appendix A: the seven commits

Oldest first.

1. `b20834f` — Fix four timing defects found by auditing two external reviews
2. `04383eb` — Settle two more review claims: one refuted, one measured and left open
3. `05a6987` — Count unsupported global accesses per access, not per line
4. `a135f48` — Fix two defects an independent review found in the previous round
5. `70ec96c` — Stop consuming the backing load at the issue site
6. `18663a2` — Act on four findings from the correctness review
7. `a2a3387` — Bring the host `fast_service_ns` back in step with the device one

Commits 4, 5 and 6 are the three that fix defects an earlier commit had reported as handled:
`a135f48` converts the third wait loop and fixes the range lookup whose regression test asserted the
wrong direction, `70ec96c` repairs the emitted code, and `18663a2` acts on the four review findings,
one of which the previous round introduced.

## Appendix B: how each statement here was checked

The distinction below is worth keeping when quoting anything from this document.

Verified first-hand by the main agent during this review:

- the seven commit hashes and their subject lines;
- every defect description and every fix under *The defects that are fixed*;
- the build-configuration root cause under *A gap in what this round reported*, including
  `CMakeLists.txt:1038` and `CMakeLists.txt:1053`, and the absence of the values 20,064 and 30,096
  anywhere under `docs/`;
- the uncompiled state of `src/cuda_runtime/device/hbf_device.cu`;
- the timing-future gate at `include/hbfsim/timing_future_abi.hpp:118-120`, together with the profile
  counts under `configs/`;
- the unfixed reassignment interval in the page-frame cache, and the flush paths that reach eviction
  without filtering for modified pages;
- the file-level scope of the repository owner's three commits.

Checked by a subagent reading the source and not re-verified line by line by the main agent: the
statement that nothing currently published is affected by the page-frame cache defect, together with
the figures behind the statement — 128 distinct pages, a cache of 131,072 frames, and a peak
occupancy of 0.0977 percent.

Checked by running the build and the tests: 60 of 62 tests pass with `HBFSIM_ENABLE_MQSIM` and
`HBFSIM_ENABLE_EVAL_TOOLS` both on. `context_lifecycle` exhausts memory under the process
address-space limit and `vmem_tuning` needs an external performance file that is not present; both
failures reproduce the repository owner's own record of them. `mqsim_service_client` refuses to run
when the build directory lies outside the checkout, and passes when built inside the checkout.

