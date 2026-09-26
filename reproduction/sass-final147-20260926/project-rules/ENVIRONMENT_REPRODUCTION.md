# Environment Reproduction and Migration

Last updated: 2026-09-19 UTC

## Purpose

Each environment_id is a versioned, reviewable build-and-run contract. A host
name, activated shell, copied binary, container tag without digest, or list of
remembered commands is not an environment definition.

## Required layout

For every approved environment, maintain:

    environments/<environment_id>/
      README.md
      manifest.json
      checksums.sha256
      lockfiles or exact package exports
      optional portable setup and verification scripts

Use docs/codex/templates/environment-manifest.template.json as the starting
schema. Do not create an approved environment entry until its values are known
and reviewed.

## Version record

The README and manifest must identify:

- environment_id, purpose, owner, status, and creation date;
- supported OS and kernel range;
- CPU architecture and required instruction sets;
- GPU model constraints only when scientifically or technically necessary;
- GPU driver, CUDA toolkit/runtime, ROCm or other accelerator stack;
- compiler, linker, build system, Python, and package-manager versions;
- simulator and all source repository commits/tags;
- model, dataset, and workload dependency versions;
- build target, build mode, flags, feature switches, and generated-code inputs;
- exact lockfile/export locations and their SHA-256 checksums;
- setup, build, smoke-test, and environment-validation commands;
- known incompatibilities and whether containers/modules are required;
- compatibility relation to predecessor environment_ids.

Machine-specific paths, hostnames, GPU UUIDs, and ordinals may appear only as
observed metadata or external inventory, never as portable requirements.

## Reproduction procedure

On a new server:

1. obtain the same source revisions;
2. verify lockfile, archive, image-digest, model, and dataset checksums;
3. install or activate the declared toolchain without choosing newer versions;
4. build locally for the declared target from source;
5. run the environment validation and minimal smoke test;
6. compare the produced environment hash with the recorded expectation;
7. record legitimate host differences in inventory and run metadata;
8. mark the environment reproducible only after validation passes.

If a required version cannot be recreated, create a new environment_id. Do not
silently substitute a nearby version.

## Migration acceptance

Cross-server migration is accepted only when:

- source and dependency provenance match;
- build target and flags are recorded;
- environment validation passes;
- the minimum pilot reproduces expected invariants within its tolerance;
- differences that can affect results are explicitly analyzed;
- EXPERIMENT_REGISTRY.md states whether old and new runs are comparable.

## Maintenance

Any version, flag, dependency, driver, base-image digest, simulator commit, or
model artifact change creates a new environment manifest revision and normally
a new environment_id. Update PROJECT_HANDOFF.md before moving active work.

