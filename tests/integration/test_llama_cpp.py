#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PARSER = argparse.ArgumentParser(add_help=False)
PARSER.add_argument("--exact-profile", type=pathlib.Path)
EXACT_ARGS, UNITTEST_ARGS = PARSER.parse_known_args()
sys.argv = [sys.argv[0], *UNITTEST_ARGS]


class LlamaAdapterTest(unittest.TestCase):
    def test_patch_targets_pinned_cuda_backend(self):
        patch = (ROOT / "adapters/llama_cpp/0001-hbfsim-timing-adapter.patch").read_text()
        self.assertIn("ggml/src/ggml-cuda/ggml-cuda.cu", patch)
        self.assertIn("LLAMA_HBFSIM_CONFIG", patch)
        self.assertIn("HBFSIM_RANGE_MODE_TIMING", patch)
        self.assertIn("hbfsim_context_create_v2", patch)
        self.assertIn("hbfsim_publish_exact_run_contract", patch)
        self.assertIn("hbfsim_finalize_exact", patch)

    def test_generated_text_parser(self):
        path = ROOT / "adapters/llama_cpp/run.py"
        spec = importlib.util.spec_from_file_location("llama_run", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.generated_text("\n> prompt\nanswer\n", "prompt"), "answer")

    def test_compatibility_rejects_capacity_claim(self):
        data = json.loads((ROOT / "adapters/llama_cpp/compatibility.json").read_text())
        self.assertFalse(data["claims"]["capacity_supported_by_llama_adapter"])
        self.assertTrue(data["claims"]["live_gpu_delay_kernel"])

    @unittest.skipUnless(EXACT_ARGS.exact_profile is not None,
                         "exact workload was not requested")
    def test_real_exact_workload(self):
        exact_profile = EXACT_ARGS.exact_profile.resolve()
        llama_cli = pathlib.Path(os.environ.get(
            "HBFSIM_LLAMA_CLI", "/dev/shm/hbfsim-llama-build/bin/llama-cli"
        ))
        model = pathlib.Path(os.environ.get(
            "HBFSIM_LLAMA_MODEL",
            "/mnt/disk2/hbfsim-models/tinyllama-1.1b-chat-v1.0-f16.gguf",
        ))
        build = pathlib.Path(os.environ.get(
            "HBFSIM_BUILD_DIR", ROOT / "build-sm120-exact"
        )).resolve()
        bpftime = pathlib.Path(os.environ.get(
            "HBFSIM_BPFTIME_BUILD_DIR", ROOT / "build-bpftime-hbfsim"
        )).resolve()
        for path, label in ((exact_profile, "exact profile"),
                            (llama_cli, "llama-cli"), (model, "model")):
            self.assertTrue(path.is_file(), f"missing {label}: {path}")
        report = exact_profile.parent / "llama-exact-workload"
        completed = subprocess.run([
            sys.executable, str(ROOT / "adapters/llama_cpp/run.py"),
            "--mode", "compare-exact", "--llama-cli", str(llama_cli),
            "--model", str(model), "--hbf-build", str(build),
            "--bpftime-build", str(bpftime),
            "--profile", str(ROOT / "configs/profiles/nominal.json"),
            "--exact-profile", str(exact_profile),
            "--report-dir", str(report),
        ], cwd=ROOT, text=True, capture_output=True, timeout=600)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads((report / "summary.json").read_text())
        self.assertTrue(summary["exact_output_match"])
        exact = next(item for item in summary["results"]
                     if item["mode"] == "exact")
        self.assertGreater(exact["coverage"]["exact_launches"], 0)


if __name__ == "__main__":
    unittest.main()
