#!/usr/bin/env python3
"""Fail-closed approval gate for formal EQ3 experiment submission.

This helper validates evidence and reports readiness.  It deliberately has no
workload execution, scheduler, or approval-writing capability.
"""

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path


MANIFEST_SCHEMA = "eq3-experiment-manifest-v1"
APPROVAL_SCHEMA = "eq3-user-confirmation-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESOURCE_FIELDS = {
    "cpu_configurations": int,
    "executions_per_configuration": int,
    "cpu_hours": (int, float),
    "build_threads": int,
    "ram_gib": (int, float),
    "disk_gib": (int, float),
    "gpu_compute_minutes": (int, float),
}
SCIENTIFIC_SECTIONS = {
    "research_question_and_hypothesis", "evidence_type_and_limits",
    "device_topology", "geometry_materials_boundaries", "workload_and_initial_state",
    "power_reliability_control", "scan_matrix_and_repetitions", "time_and_numerics",
    "controls_ablations_observations", "acceptance_abort_and_outputs",
}


class GateError(ValueError):
    """A stable, user-facing gate refusal."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise GateError(code, message)


def load_json(path):
    def reject(token):
        _fail("INVALID_JSON", f"non-finite JSON token: {token}")

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject)
    except GateError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        _fail("INVALID_JSON", str(exc))


def _object(value, label):
    if not isinstance(value, dict):
        _fail("SCHEMA_INVALID", f"{label} must be an object")
    return value


def _exact_keys(value, required, optional, label):
    keys = set(_object(value, label))
    missing = set(required) - keys
    extra = keys - set(required) - set(optional)
    if missing or extra:
        _fail("SCHEMA_INVALID", f"{label} missing={sorted(missing)} extra={sorted(extra)}")


def _string(value, label):
    if not isinstance(value, str) or not value.strip():
        _fail("SCHEMA_INVALID", f"{label} must be a non-empty string")
    return value


def _sha256(value, label):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        _fail("SCHEMA_INVALID", f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _canonical_bytes(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("SCHEMA_INVALID", f"manifest is not canonical JSON: {exc}")


def _artifact_binding(artifact, label):
    _exact_keys(artifact, {"logical_id", "semantic_role", "sha256", "path"}, set(), label)
    return {
        "logical_id": _string(artifact["logical_id"], f"{label}.logical_id"),
        "semantic_role": _string(artifact["semantic_role"], f"{label}.semantic_role"),
        "sha256": _sha256(artifact["sha256"], f"{label}.sha256"),
    }


def _validate_artifact_group(value, label):
    if not isinstance(value, list) or not value:
        _fail("SCHEMA_INVALID", f"{label} must be a non-empty array")
    bindings = [_artifact_binding(item, f"{label}[{index}]") for index, item in enumerate(value)]
    ids = [item["logical_id"] for item in bindings]
    if len(ids) != len(set(ids)):
        _fail("SCHEMA_INVALID", f"{label} logical_id values must be unique")
    return bindings


def _validate_resources(resources):
    _exact_keys(resources, {"requested", "limits"}, set(), "resource_budget")
    requested = _object(resources["requested"], "resource_budget.requested")
    limits = _object(resources["limits"], "resource_budget.limits")
    expected = set(RESOURCE_FIELDS)
    if set(requested) != expected or set(limits) != expected:
        _fail("SCHEMA_INVALID", "requested and limits must contain the complete resource field set")
    for name, expected_type in RESOURCE_FIELDS.items():
        for group_name, group in (("requested", requested), ("limits", limits)):
            value = group[name]
            if isinstance(value, bool) or not isinstance(value, expected_type):
                _fail("SCHEMA_INVALID", f"resource_budget.{group_name}.{name} has invalid type")
            if not math.isfinite(value) or value < 0:
                _fail("SCHEMA_INVALID", f"resource_budget.{group_name}.{name} must be finite and nonnegative")
        if requested[name] > limits[name]:
            _fail("RESOURCE_BUDGET_EXCEEDED", f"requested {name} exceeds the manifest limit")


def _validate_prerequisites(prerequisites):
    if not isinstance(prerequisites, list) or not prerequisites:
        _fail("SCHEMA_INVALID", "prerequisites must be a non-empty array")
    seen = set()
    bindings = []
    for index, item in enumerate(prerequisites):
        label = f"prerequisites[{index}]"
        _exact_keys(item, {"id", "status", "evidence"}, set(), label)
        prerequisite_id = _string(item["id"], f"{label}.id")
        if prerequisite_id in seen:
            _fail("SCHEMA_INVALID", "prerequisite ids must be unique")
        seen.add(prerequisite_id)
        if item["status"] != "PASSED":
            _fail("PREREQUISITE_NOT_PASSED", f"{prerequisite_id} is not PASSED")
        evidence = _artifact_binding(item["evidence"], f"{label}.evidence")
        bindings.append({"id": prerequisite_id, "status": "PASSED", "evidence": evidence})
    return bindings


def manifest_binding(manifest):
    """Validate manifest shape and return its portable scientific binding."""
    required = {
        "schema_version", "experiment_id", "version", "approval_status",
        "canonical_manifest_hash", "scientific_config", "code", "dependencies",
        "inputs", "resource_budget", "prerequisites",
    }
    _exact_keys(manifest, required, {"execution_context"}, "manifest")
    if manifest["schema_version"] != MANIFEST_SCHEMA:
        _fail("SCHEMA_INVALID", f"unsupported manifest schema: {manifest['schema_version']!r}")
    experiment_id = _string(manifest["experiment_id"], "experiment_id")
    version = _string(manifest["version"], "version")
    if manifest["approval_status"] not in ("PENDING_USER_APPROVAL", "USER_APPROVED"):
        _fail("SCHEMA_INVALID", "approval_status must be PENDING_USER_APPROVAL or USER_APPROVED")
    scientific_config = _object(manifest["scientific_config"], "scientific_config")
    if set(scientific_config) != SCIENTIFIC_SECTIONS:
        missing = sorted(SCIENTIFIC_SECTIONS - set(scientific_config))
        extra = sorted(set(scientific_config) - SCIENTIFIC_SECTIONS)
        _fail("SCIENTIFIC_PREFLIGHT_INCOMPLETE",
              f"scientific_config sections missing={missing} extra={extra}")
    for section in sorted(SCIENTIFIC_SECTIONS):
        value = _object(scientific_config[section], f"scientific_config.{section}")
        if not value:
            _fail("SCIENTIFIC_PREFLIGHT_INCOMPLETE",
                  f"scientific_config.{section} must not be empty")
    code = _object(manifest["code"], "code")
    _exact_keys(code, {"repository_revision", "dirty_diff_sha256", "artifacts"}, set(), "code")
    _string(code["repository_revision"], "code.repository_revision")
    _sha256(code["dirty_diff_sha256"], "code.dirty_diff_sha256")
    code_artifacts = _validate_artifact_group(code["artifacts"], "code.artifacts")
    dependencies = _validate_artifact_group(manifest["dependencies"], "dependencies")
    inputs = _validate_artifact_group(manifest["inputs"], "inputs")
    _validate_resources(manifest["resource_budget"])
    prerequisite_bindings = _validate_prerequisites(manifest["prerequisites"])
    if "execution_context" in manifest:
        _object(manifest["execution_context"], "execution_context")
    return {
        "schema_version": manifest["schema_version"],
        "experiment_id": experiment_id,
        "version": version,
        "scientific_config": scientific_config,
        "code": {
            "repository_revision": code["repository_revision"],
            "dirty_diff_sha256": code["dirty_diff_sha256"],
            "artifacts": code_artifacts,
        },
        "dependencies": dependencies,
        "inputs": inputs,
        "resource_budget": manifest["resource_budget"],
        "prerequisites": prerequisite_bindings,
    }


def canonical_manifest_hash(manifest):
    return hashlib.sha256(_canonical_bytes(manifest_binding(manifest))).hexdigest()


def _resolve_artifact(root, relative_path, label):
    path_text = _string(relative_path, f"{label}.path")
    candidate = Path(path_text)
    if candidate.is_absolute():
        _fail("ARTIFACT_PATH_INVALID", f"{label}.path must be relative to --root")
    root = Path(root).resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        _fail("ARTIFACT_PATH_INVALID", f"{label}.path escapes --root")
    if not resolved.is_file():
        _fail("ARTIFACT_MISSING", f"{label}.path is not a file: {path_text}")
    return resolved


def verify_artifacts(manifest, root):
    groups = (("code.artifacts", manifest["code"]["artifacts"]),
              ("dependencies", manifest["dependencies"]),
              ("inputs", manifest["inputs"]))
    for group_name, artifacts in groups:
        for index, artifact in enumerate(artifacts):
            label = f"{group_name}[{index}]"
            path = _resolve_artifact(root, artifact["path"], label)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != artifact["sha256"]:
                _fail("ARTIFACT_HASH_MISMATCH", f"{label} content does not match its bound SHA-256")
    for index, prerequisite in enumerate(manifest["prerequisites"]):
        artifact = prerequisite["evidence"]
        label = f"prerequisites[{index}].evidence"
        path = _resolve_artifact(root, artifact["path"], label)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != artifact["sha256"]:
            _fail("PREREQUISITE_EVIDENCE_MISMATCH",
                  f"{label} content does not match its bound SHA-256")


def observed_git_state(root):
    """Read the production checkout identity without changing repository state."""
    root = Path(root).resolve()
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        tracked_diff = subprocess.run(
            ["git", "-C", str(root), "diff", "--binary", "HEAD", "--", "."], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        _fail("CODE_STATE_UNAVAILABLE", f"cannot observe Git code state under --root: {exc}")
    return revision, hashlib.sha256(tracked_diff).hexdigest()


def validate_approval(approval, manifest, expected_hash, approval_path=None):
    required = {
        "schema_version", "record_kind", "experiment_id", "version",
        "canonical_manifest_hash", "evidence_class", "source", "is_test_fixture",
    }
    _exact_keys(approval, required, set(), "approval")
    if approval["schema_version"] != APPROVAL_SCHEMA:
        _fail("APPROVAL_INVALID", "unsupported approval schema")
    if approval["record_kind"] != "USER_CONFIRMATION":
        _fail("APPROVAL_INVALID", "record_kind must be USER_CONFIRMATION")
    if approval["evidence_class"] != "USER_CONFIRMED":
        _fail("APPROVAL_INVALID", "evidence_class must be USER_CONFIRMED")
    if approval["is_test_fixture"] is not False:
        _fail("TEST_FIXTURE_REFUSED", "test approval fixtures are never production approval")
    if approval["experiment_id"] != manifest["experiment_id"] or approval["version"] != manifest["version"]:
        _fail("APPROVAL_BINDING_MISMATCH", "approval experiment_id/version does not match manifest")
    _sha256(approval["canonical_manifest_hash"], "approval.canonical_manifest_hash")
    if approval["canonical_manifest_hash"] != expected_hash:
        _fail("APPROVAL_BINDING_MISMATCH", "approval is bound to a different manifest hash")
    source = approval["source"]
    _exact_keys(source, {"type", "reference", "captured_at", "statement", "statement_sha256"}, set(), "approval.source")
    if source["type"] not in ("codex_user_message", "external_user_confirmation"):
        _fail("APPROVAL_INVALID", "approval source type is not an independently supplied user confirmation")
    _string(source["reference"], "approval.source.reference")
    _string(source["captured_at"], "approval.source.captured_at")
    statement = _string(source["statement"], "approval.source.statement")
    statement_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    if _sha256(source["statement_sha256"], "approval.source.statement_sha256") != statement_hash:
        _fail("APPROVAL_INVALID", "approval statement hash does not match its content")
    markers = ((f"experiment_id={manifest['experiment_id']}", "experiment_id"),
               (f"version={manifest['version']}", "version"),
               (f"canonical_manifest_hash={expected_hash}", "canonical manifest hash"))
    for marker, label in markers:
        if marker not in statement:
            _fail("APPROVAL_INVALID", f"approval statement does not explicitly name {label}")
    if approval_path and any(part.lower() in {"fixture", "fixtures"} for part in Path(approval_path).parts):
        _fail("TEST_FIXTURE_REFUSED", "approval records stored in fixture directories are refused")


def validate_gate(manifest, approval, root, approval_path=None,
                  observed_code_revision=None, observed_dirty_diff_sha256=None):
    expected_hash = canonical_manifest_hash(manifest)
    declared_hash = _sha256(manifest["canonical_manifest_hash"], "canonical_manifest_hash")
    if declared_hash != expected_hash:
        _fail("MANIFEST_HASH_STALE", "canonical_manifest_hash does not match manifest semantics")
    verify_artifacts(manifest, root)
    if observed_code_revision is None or observed_dirty_diff_sha256 is None:
        observed_code_revision, observed_dirty_diff_sha256 = observed_git_state(root)
    if manifest["code"]["repository_revision"] != observed_code_revision:
        _fail("CODE_REVISION_MISMATCH", "current Git revision differs from the bound revision")
    if manifest["code"]["dirty_diff_sha256"] != observed_dirty_diff_sha256:
        _fail("CODE_DIFF_MISMATCH", "current tracked Git diff differs from the bound diff")
    if approval is None:
        _fail("PENDING_USER_APPROVAL", "no independently supplied user confirmation record was provided")
    if approval.get('record_kind') == 'USER_STAGE_AUTHORIZATION':
        from eq3_campaign_gate import validate_stage
        validate_stage(approval, manifest, root)
    else:
        validate_approval(approval, manifest, expected_hash, approval_path)
    return {"status": "READY_FOR_SUBMISSION", "experiment_id": manifest["experiment_id"],
            "version": manifest["version"], "canonical_manifest_hash": expected_hash,
            "launch_performed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    hash_parser = subparsers.add_parser("hash-manifest", help="print the portable semantic hash")
    hash_parser.add_argument("--manifest", type=Path, required=True)
    check_parser = subparsers.add_parser("check", help="validate readiness without launching anything")
    check_parser.add_argument("--manifest", type=Path, required=True)
    check_parser.add_argument("--approval", type=Path)
    check_parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        if args.command == "hash-manifest":
            print(canonical_manifest_hash(manifest))
            return 0
        approval = load_json(args.approval) if args.approval else None
        print(json.dumps(validate_gate(manifest, approval, args.root, args.approval), sort_keys=True))
        return 0
    except GateError as exc:
        print(json.dumps({"status": "REFUSED", "code": exc.code, "message": str(exc),
                          "launch_performed": False}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
