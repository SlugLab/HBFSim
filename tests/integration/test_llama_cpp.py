#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
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
        self.assertIn("exact probe shadow", patch)
        self.assertIn("hbfsim_register_device(exact probe)", patch)
        self.assertIn("one-shot exact probe", patch)
        self.assertIn("hbfsim_probe_ring_capacity", patch)
        self.assertIn(
            "exact_requested ? hbfsim_probe_ring_capacity : 256", patch
        )

    def test_exact_kernel_contract_matches_probe_launch_width(self):
        probe = (ROOT / "adapters/llama_cpp/hbfsim_llama_probe.cu").read_text()
        match = re.search(r"constexpr std::uint32_t kThreads = (\d+);", probe)
        self.assertIsNotNone(match)
        contract = json.loads((
            ROOT / "adapters/llama_cpp/exact-kernel-contract.json"
        ).read_text())
        self.assertEqual(
            contract["hbfsim_llama_probe_kernel"]["block_threads"],
            int(match.group(1)),
        )

    def test_generated_text_parser(self):
        path = ROOT / "adapters/llama_cpp/run.py"
        spec = importlib.util.spec_from_file_location("llama_run", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.generated_text("\n> prompt\nanswer\n", "prompt"), "answer")
        self.assertEqual(module.exact_result_boundary("baseline"), {})
        self.assertEqual(module.exact_result_boundary("exact"), {
            "exact_scope": "one_shot_sideband_probe",
            "model_graph_fidelity": "native",
            "model_storage_registered": False,
        })
        self.assertEqual(module.dry_run_result(
            "exact", ["llama-cli"], {"timing_model": "hybrid"}
        ), {
            "mode": "exact",
            "command": ["llama-cli"],
            "config": {"timing_model": "hybrid"},
            "exact_scope": "one_shot_sideband_probe",
            "model_graph_fidelity": "native",
            "model_storage_registered": False,
        })

    def test_exact_uses_llama_probe_hook(self):
        runner = (ROOT / "adapters/llama_cpp/run.py").read_text()
        self.assertIn('args.hbf_build / "llama_probe.bpf.o"', runner)
        self.assertIn('environment["LD_PRELOAD"] = config["probe_library"]',
                      runner)

    def test_llama_probe_uses_hookable_shared_cudart(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertRegex(
            cmake,
            r"hbfsim_llama_probe_module PROPERTIES[^)]*?"
            r"CUDA_RUNTIME_LIBRARY Shared",
        )
        self.assertRegex(
            cmake,
            r"target_link_libraries\(\s*hbfsim_llama_probe_module\s+"
            r"PRIVATE\s+CUDA::cuda_driver\s*\)",
        )
        probe = (ROOT / "adapters/llama_cpp/hbfsim_llama_probe.cu").read_text()
        self.assertIn("cudaGetFuncBySymbol", probe)
        self.assertIn("cuLaunchKernel", probe)

    def test_exact_binds_probe_ptx_variant(self):
        patch = (ROOT / "adapters/llama_cpp/0001-hbfsim-timing-adapter.patch").read_text()
        self.assertIn("bpftime_nv_bind_ptx_variant", patch)
        self.assertIn("hbfsim_llama_probe_function", patch)
        self.assertIn("exact probe PTX variant binding", patch)

    def test_build_rejects_adapter_disabled_backend(self):
        helper = (ROOT / "adapters/llama_cpp/build.sh").read_text()
        self.assertIn("built ggml-cuda backend is missing the HBFSim adapter", helper)
        self.assertIn("LLAMA_HBFSIM_CONFIG", helper)

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
            "--timeout", "900",
        ], cwd=ROOT, text=True, capture_output=True, timeout=1800)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads((report / "summary.json").read_text())
        self.assertTrue(summary["exact_output_match"])
        exact = next(item for item in summary["results"]
                     if item["mode"] == "exact")
        self.assertGreater(exact["coverage"]["exact_launches"], 0)
        self.assertTrue(exact["exact_probe"]["bit_exact"])
        self.assertEqual(exact["exact_probe"]["output_count"], 128)
        self.assertEqual(exact["exact_probe"]["issued_operations"], 393216)
        self.assertEqual(exact["exact_scope"], "one_shot_sideband_probe")
        self.assertEqual(exact["model_graph_fidelity"], "native")
        self.assertFalse(exact["model_storage_registered"])


if __name__ == "__main__":
    unittest.main()
