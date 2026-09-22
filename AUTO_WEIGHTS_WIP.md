# Automatic weight binding/instrumentation — unvalidated WIP

This branch preserves the exact three-file work in progress that existed when
the user requested a GitHub synchronization on 2026-09-22.  It is based on
`eabc5c2c0820ac0d84c2f16ea3460b219f11ff83`.

Status: **UNVALIDATED / INCOMPLETE**.  It is not an experiment runtime and must
not be used to claim automatic all-weight instrumentation.  Known review items
remain open, including selection/exclusion closure semantics, strict config
type validation, legacy `TimingConfig(parameter_regex=...)` normalization,
fully native `off`, multi-entry PTX cumulative staging, runtime strict-gate
coverage, binder generalization, and meaningful CPU/GPU acceptance tests.

The validated rebuttal baseline and experiment-source provenance are on the
separate `backup/rebuttal-before-auto-weights-20260922` branch.

