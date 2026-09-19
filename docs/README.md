# Documentation index

Start here if you are new to HBFSim. The root [`README.md`](../README.md) says what HBFSim is;
this file says where each kind of document lives.

## Read in this order

1. [`skills/00-reading-order.md`](skills/00-reading-order.md) — a reading order through the code,
   one document per subsystem.
2. [`proofs/`](proofs/) — the checkpoint documents. **Every experiment number HBFSim reports comes
   from one of these, together with the commands that produced it and the boundary of the claim.**
   A number quoted anywhere else without one of these behind it is not a result.
3. [`eval/`](eval/) — the evaluation plan, the workload methodology, and the reproduction runbooks.

## What each directory holds

- [`proofs/`](proofs/) — checkpoint documents, one per experiment. Read
  [`proofs/2026-08-11-cd8p-vmem-tuning.md`](proofs/2026-08-11-cd8p-vmem-tuning.md) first: it records
  the calibration the timing model rests on, its source hash, and what the calibration does not show.
- [`eval/`](eval/) — the evaluation plan, what is integrated on the default branch, and what is
  deliberately deferred. [`eval/README.md`](eval/README.md) is the entry point.
- [`skills/`](skills/) — one document per subsystem, written for someone reading the code for the
  first time: PTX instrumentation, CUDA memory semantics, asynchronous copies, the device helper and
  control ABI, the online MQSim service, capacity address translation, the vLLM integration.
- [`architecture/`](architecture/) — the runtime function map.
- [`superpowers/`](superpowers/) — the design contracts (`specs/`) and the implementation plans
  (`plans/`) that the components were built against.
- [`49-eval-audit/`](49-eval-audit/) and the `49-`/`50-` documents — the audit trail of the current
  measurement campaign: claim gates, capability audits, blockers, run status.
- [`eq3_thermal/`](eq3_thermal/) — the thermal and reliability model: design, verification against
  the specification, and its current status.
- [`reference/`](reference/) — reference notes, including the CUDA architecture compatibility audit.
- [`ref_article/`](ref_article/) — the papers and specification captures HBFSim's claims are checked
  against. [`ref_article/README.md`](ref_article/README.md) records, for each one, the full citation,
  the open-access link, the file hash, and which part of it this project actually uses.
- [`HBF_OCP/`](HBF_OCP/) — the Open Compute Project HBF architecture specification capture.
- [`assets/`](assets/) — figures used by the two README files.

## Two conventions worth knowing

**Numbers carry their boundary with them.** A checkpoint document states what it does not show as
plainly as what it shows: that six calibration breakpoints matching exactly is a deterministic check
rather than a cross-validation, that a 110 GiB logical range is a sparse span rather than 110 GiB of
payload read, that a build passing CPU tests is not live GPU evidence. Keep those sentences when you
quote a number.

**A superseded number is marked in place, not deleted.** When a fix invalidates a published
measurement, the checkpoint document that carried the measurement says so and names the commit. The
`queue_depth` fix in commit `12ef138` is the worked example:
[`proofs/2026-08-10-capacity-runtime-non-live.md`](proofs/2026-08-10-capacity-runtime-non-live.md)
and [`proofs/2026-08-16-density-interleave-nand-sweep.md`](proofs/2026-08-16-density-interleave-nand-sweep.md)
both carry that mark.

## A note on paths you may not find

Some documents here cite a working document that is not in this repository — a session log, an
internal defect register, an evaluation design draft. Those are the project's own working material
rather than published artifacts, and the citation is kept so the provenance of a number stays
traceable for the authors. Where one of those citations points at a path that no longer resolves at
the current commit, the path was correct at the commit the document names.
