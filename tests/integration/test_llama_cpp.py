#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class LlamaAdapterTest(unittest.TestCase):
    def test_patch_targets_pinned_cuda_backend(self):
        patch = (ROOT / "adapters/llama_cpp/0001-hbfsim-timing-adapter.patch").read_text()
        self.assertIn("ggml/src/ggml-cuda/ggml-cuda.cu", patch)
        self.assertIn("LLAMA_HBFSIM_CONFIG", patch)
        self.assertIn("HBFSIM_RANGE_MODE_TIMING", patch)

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


if __name__ == "__main__":
    unittest.main()
