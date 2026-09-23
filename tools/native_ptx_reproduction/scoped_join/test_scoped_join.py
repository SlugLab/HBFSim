import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).with_name("join_scoped_staging_provenance.py")
SPEC = importlib.util.spec_from_file_location("scoped", SOURCE)
scoped = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(scoped)


class ScopedJoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.member = self.root / "member.ptx"
        self.raw = b".version 9.0\n.target sm_120\n.entry k() {}\n.entry other() {}\n"
        self.member.write_bytes(self.raw)
        self.raw_sha = scoped.digest(self.raw)
        self.container = self.root / "container.so"
        self.container.write_bytes(b"container")
        st = self.container.stat()
        snap = {"path": str(self.container.resolve()), "size_bytes": 9,
                "sha256": scoped.digest(b"container"), "device": st.st_dev,
                "inode": st.st_ino, "mtime_ns": st.st_mtime_ns}
        memberships = {entry: [{"member_id": "member-0001",
            "member_sha256": self.raw_sha, "container_sha256": snap["sha256"],
            "ptx_version": "9.0", "target": "sm_120",
            "target_directive": "sm_120"}] for entry in ("k", "other")}
        self.collector = {"schema": "hbfsim.native_ptx_collection.v2",
            "collector_status": "READY", "collector": {"sha256": "source"},
            "container": snap, "container_after_listing": copy.deepcopy(snap),
            "container_after_extraction": copy.deepcopy(snap),
            "members": [{"member_id": "member-0001", "member_sha256": self.raw_sha,
                "size_bytes": len(self.raw), "saved_path": str(self.member),
                "status": "READY", "entries": ["k", "other"],
                "ptx_version": "9.0", "target": "sm_120",
                "target_directive": "sm_120", "listed_index": 1,
                "listed_name": "one.sm_120.ptx", "duplicate_of_member_id": None}],
            "entry_memberships": memberships}
        self.collector_path = self.root / "collector.json"
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.staged = self.stage / f"{self.raw_sha}.ptx"
        self.staged.write_bytes(self.raw.replace(b".entry k() {}", b".entry k() { // transformed\n}"))
        self.pass_manifest = self.stage / "pass.jsonl"
        self.row = {"module_id": f"ptx:sha256:{self.raw_sha}", "kernel": "k",
            "instrumented": True, "rewritten_instructions": 2,
            "unsupported_instructions": 0, "unsupported_parameters": []}
        self.stage_manifest = {"status": "PARTIAL_READY", "policy": "partial",
            "kernel_patterns": ["^k$"], "variants": [{
                "raw_sha256": self.raw_sha, "raw_bytes": len(self.raw),
                "module_id": f"ptx:sha256:{self.raw_sha}", "status": "PARTIAL_READY",
                "kernel_entries": ["k", "other"], "selected_entries": ["k"],
                "entry_results": [{"kernel": "k", "status": "SUPPORTED_TRANSFORMED"}],
                "sources": [str(self.member)], "staged_path": str(self.staged),
                "staged_sha256": scoped.digest(self.staged.read_bytes())}]}
        self.write_all()

    def tearDown(self): self.tmp.cleanup()

    def write_all(self):
        self.collector_path.write_text(json.dumps(self.collector))
        self.pass_manifest.write_text(json.dumps(self.row) + "\n")
        stage_path = self.stage / "ptx-staging-manifest.json"
        stage_path.write_text(json.dumps(self.stage_manifest))
        artifacts = {str(stage_path): scoped.digest(stage_path.read_bytes()),
            str(self.pass_manifest): scoped.digest(self.pass_manifest.read_bytes()),
            str(self.staged): scoped.digest(self.staged.read_bytes())}
        marker = {"status": self.stage_manifest["status"],
            "manifest_sha256": scoped.digest(stage_path.read_bytes()),
            "pass_manifest_path": str(self.pass_manifest),
            "pass_manifest_sha256": scoped.digest(self.pass_manifest.read_bytes()),
            "artifact_sha256": artifacts}
        (self.stage / "COMPLETE.json").write_text(json.dumps(marker))

    def test_explicit_subset_ready(self):
        out = scoped.build_join(self.collector_path, self.stage, self.pass_manifest, ["k"])
        self.assertEqual(out["status"], "SCOPED_READY")
        self.assertFalse(out["whole_module_ready"])
        self.assertEqual(out["module_joins"][0]["unselected_entries"], ["other"])
        self.assertEqual(out["selection_contract"]["unselected_entry_policy"], "STRICT_REJECT")

    def test_extra_unselected_pass_row_rejected(self):
        self.pass_manifest.write_text(json.dumps(self.row) + "\n" + json.dumps({
            **self.row, "kernel": "other"}) + "\n")
        self.write_all = lambda: None
        stage_path = self.stage / "ptx-staging-manifest.json"
        marker_path = self.stage / "COMPLETE.json"
        marker = json.loads(marker_path.read_text())
        marker["pass_manifest_sha256"] = scoped.digest(self.pass_manifest.read_bytes())
        marker["artifact_sha256"][str(self.pass_manifest)] = marker["pass_manifest_sha256"]
        marker_path.write_text(json.dumps(marker))
        with self.assertRaisesRegex(ValueError, "explicit entry subset"):
            scoped.build_join(self.collector_path, self.stage, self.pass_manifest, ["k"])

    def test_clipped_staged_member_rejected(self):
        self.staged.write_bytes(b".version 9.0\n.target sm_120\n.entry k() {}\n")
        self.stage_manifest["variants"][0]["staged_sha256"] = scoped.digest(self.staged.read_bytes())
        self.write_all()
        with self.assertRaisesRegex(ValueError, "full member entry inventory"):
            scoped.build_join(self.collector_path, self.stage, self.pass_manifest, ["k"])

    def test_ambiguous_membership_rejected(self):
        second = copy.deepcopy(self.collector["members"][0])
        second["member_id"] = "member-0002"
        second["listed_index"] = 2
        self.collector["members"].append(second)
        self.collector["entry_memberships"] = {
            entry: [
                {**membership, "member_id": member_id}
                for member_id in ("member-0001", "member-0002")
            ] for entry, (membership,) in self.collector["entry_memberships"].items()
        }
        self.write_all()
        with self.assertRaisesRegex(ValueError, "unique collector membership"):
            scoped.build_join(self.collector_path, self.stage, self.pass_manifest, ["k"])


if __name__ == "__main__": unittest.main()
