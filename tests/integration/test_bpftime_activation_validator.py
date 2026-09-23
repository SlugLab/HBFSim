import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_bpftime_activation.py"
SPEC = importlib.util.spec_from_file_location("validate_bpftime_activation", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ActivationValidatorTest(unittest.TestCase):
    def _paths(self, decisions):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        manifest = root / "manifest.jsonl"
        coverage = root / "coverage.jsonl"
        manifest.write_text(json.dumps({"module_id": "ptx:sha256:abc", "kernel": "k"}) + "\n")
        coverage.write_text("".join(json.dumps(item) + "\n" for item in decisions))
        self.addCleanup(temporary.cleanup)
        return manifest, coverage

    def test_strict_accepts_required_no_direct_hit_with_negative_fixture(self):
        paths = self._paths([
            {"allowed": False, "reason": "strict fixture denial", "modeled": False},
            {"allowed": True, "reason": "", "modeled": False,
             "requires_instrumented_execution": True},
        ])
        MODULE.validate_activation(*paths, "strict")
        MODULE.validate_activation(*paths, "auto")

    def test_strict_rejects_only_denials_or_unrequired_no_hit(self):
        for decisions in (
            [{"allowed": False, "reason": "denied", "modeled": False,
              "requires_instrumented_execution": True}],
            [{"allowed": True, "reason": "", "modeled": False,
              "requires_instrumented_execution": False}],
        ):
            with self.subTest(decisions=decisions):
                paths = self._paths(decisions)
                with self.assertRaises(ValueError):
                    MODULE.validate_activation(*paths, "strict")

    def test_legacy_contract_is_preserved(self):
        good = self._paths([{"allowed": True, "reason": "modeled", "modeled": True}])
        MODULE.validate_activation(*good, "legacy")
        missing_reason = self._paths([{"allowed": True, "reason": "", "modeled": True}])
        with self.assertRaises(ValueError):
            MODULE.validate_activation(*missing_reason, "legacy")
        no_modeled = self._paths([{"allowed": True, "reason": "native", "modeled": False}])
        with self.assertRaises(ValueError):
            MODULE.validate_activation(*no_modeled, "legacy")


if __name__ == "__main__":
    unittest.main()
