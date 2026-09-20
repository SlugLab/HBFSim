# EQ3 experiment approval contract

Status: active governance contract. Formal runs remain
`PENDING_USER_APPROVAL` until an independently supplied confirmation is bound to
the exact manifest described below.

## Trust boundary

`tools/eq3_experiment_gate.py` is a local validator, not an identity or consent
system. It cannot prove who authored a file. A coordinator must obtain an actual
user confirmation through an independently controlled conversation or external
channel, preserve that source reference and exact statement, and only then make
the separate confirmation record. An agent-generated record, silence, a broad
instruction to continue, or a test fixture is not approval.

The helper never writes approval records and has no workload launch action. Its
only subprocesses are read-only Git identity/diff queries. A successful check
means only that the supplied artifacts satisfy this contract and are ready for a
separate submission workflow.

## Manifest version 1

The JSON manifest uses `schema_version: eq3-experiment-manifest-v1`. Its required
top-level fields are:

- `experiment_id`, string `version`, and `approval_status` (default
  `PENDING_USER_APPROVAL`);
- `scientific_config`, containing nonempty sections for the research question and
  hypothesis; evidence type and limits; device/topology; geometry/materials/
  boundaries; workload and initial state; power/reliability/control; scan matrix
  and repetitions; time/numerics; controls/ablations/observations; and acceptance,
  abort, and outputs;
- `code`, with repository revision, dirty-diff SHA-256, and at least one source
  artifact;
- nonempty `dependencies` and `inputs` artifact arrays;
- `resource_budget.requested` and `resource_budget.limits` for CPU configuration
  count, executions/configuration, CPU-hours, build threads, RAM GiB, disk GiB,
  and GPU-compute minutes;
- nonempty `prerequisites`, each `PASSED` with a path-verified evidence artifact;
- `canonical_manifest_hash`.

Each code, dependency, input, or prerequisite-evidence artifact has a portable
`logical_id`, a `semantic_role`, a SHA-256, and a repository-root-relative
verification `path`.
The path is used to verify current bytes but is excluded from the semantic hash,
so relocation alone does not invalidate approval. Absolute paths and paths that
escape the supplied root are refused.

The canonical hash is SHA-256 over compact, key-sorted UTF-8 JSON containing the
schema, experiment identity/version, scientific configuration, code revision and
artifact identities/hashes, dependency and input identities/hashes, requested
resources and limits, and prerequisite evidence. It excludes the mutable status,
the hash field itself, artifact verification paths, and optional
`execution_context` host/path/run receipts. Unknown top-level or artifact fields
are refused rather than silently omitted. Changing scientific semantics, code,
dependencies, inputs, resource bounds, or prerequisites therefore requires a new
hash and new user confirmation.

## Confirmation record version 1

The separate record uses `schema_version: eq3-user-confirmation-v1`,
`record_kind: USER_CONFIRMATION`, `evidence_class: USER_CONFIRMED`, and
`is_test_fixture: false`. It repeats the experiment ID, version, and canonical
manifest hash. Its `source` records:

- `type`: `codex_user_message` or `external_user_confirmation`;
- a durable `reference` and `captured_at` timestamp;
- the exact user `statement` and its SHA-256.

The statement must itself contain the exact labeled bindings
`experiment_id=...`, `version=...`, and `canonical_manifest_hash=...`. This
mechanical requirement prevents a generic or historical approval from being
rebound silently. It does not authenticate the statement; the coordinator remains
responsible for the trust boundary above. Records marked as fixtures, or stored
under a `fixture`/`fixtures` directory, are refused for production checks.

## Command interface and fail-closed behavior

From the repository root:

```sh
python3 tools/eq3_experiment_gate.py hash-manifest --manifest PRECHECK.json
python3 tools/eq3_experiment_gate.py check \
  --manifest PRECHECK.json --approval USER_CONFIRMATION.json --root .
```

`hash-manifest` computes the portable binding after schema, prerequisite, and
budget validation. Place that digest in `canonical_manifest_hash`, present the
readable preflight and digest to the user, and wait for explicit confirmation.
`check` obtains the actual Git `HEAD` and the SHA-256 of the current tracked binary
diff, then exits 0 and reports `READY_FOR_SUBMISSION` only when those observations,
the declared digest, current artifact and prerequisite-receipt bytes, budget, and
confirmation all match. Relevant untracked code must be listed as a code artifact.
It reports `launch_performed: false` in both success and refusal output. Missing
approval, stale hashes, changed inputs or receipts, exceeded limits, failed
prerequisites, code-state drift, and fixture approvals exit 2.

There is intentionally no `--yes`, environment-variable bypass, fixture mode, or
command to submit work. A later launcher must call this check before submission
and must stop on every nonzero exit. Changes outside the approved semantic or
resource envelope require a revised readable preflight, manifest version/hash,
and explicit user confirmation. Host, compiler, dependency ranges, device and
cooling observations that are execution-environment constraints must additionally
be checked by that launcher against the approved scientific configuration; this
portable helper does not authenticate hosts or discover hardware.
