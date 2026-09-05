from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


def tensor(name: str, begin: int, size: int) -> dict[str, object]:
    return {
        "tensor": name,
        "source_shard": "model.safetensors",
        "source_shard_sha256": "a" * 64,
        "file_offset_begin": begin,
        "file_offset_end": begin + size,
        "bytes": size,
        "dtype": "BF16",
        "shape": [2, 2],
    }


def main() -> int:
    timing_binary = pathlib.Path(sys.argv[1]).resolve(strict=True)
    root = pathlib.Path(__file__).resolve().parents[2]
    replay = root / "adapters" / "vllm_capacity" / "trace_replay.py"
    profile = root / "configs" / "profiles" / "nominal.json"
    fingerprint = "b" * 64
    with tempfile.TemporaryDirectory() as temporary:
        work = pathlib.Path(temporary)
        manifest = work / "model.json"
        experts = []
        for expert in range(2):
            begin = expert * 16_384
            experts.append(
                {
                    "layer": 0,
                    "expert_id": expert,
                    "total_bytes": 16_384,
                    "w13": {
                        "bytes": 8_192,
                        "tensors": [
                            tensor(f"e{expert}.gate", begin, 4_096),
                            tensor(f"e{expert}.up", begin + 4_096, 4_096),
                        ],
                    },
                    "w2": {
                        "bytes": 8_192,
                        "tensor": tensor(
                            f"e{expert}.down", begin + 8_192, 8_192
                        ),
                    },
                }
            )
        manifest.write_text(
            json.dumps(
                {
                    "ModelFingerprint": fingerprint,
                    "configuration": {
                        "num_hidden_layers": 1,
                        "num_experts": 2,
                        "num_experts_per_tok": 1,
                    },
                    "expert_weight_bytes": 32_768,
                    "expert_weights": experts,
                }
            ),
            encoding="utf-8",
        )
        trace = work / "trace.jsonl"
        trace.write_text(
            "".join(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model_fingerprint": fingerprint,
                        "phase": "prefill",
                        "request_id": "r0",
                        "prompt_id": 0,
                        "token_step": step,
                        "layer_id": 0,
                        "topk_expert_ids": [expert],
                    }
                )
                + "\n"
                for step, expert in enumerate((0, 1, 0))
            ),
            encoding="utf-8",
        )
        output = work / "output"
        completed = subprocess.run(
            [
                sys.executable,
                str(replay),
                "--trace",
                str(trace),
                "--model-manifest",
                str(manifest),
                "--profile",
                str(profile),
                "--output-dir",
                str(output),
                "--git-commit",
                "c" * 40,
                "--environment-fingerprint",
                "d" * 64,
                "--timing-binary",
                str(timing_binary),
                "--timing-mode",
                "fast",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {"status": "PASS", "cell_count": 18}
        summary = json.loads((output / "replay-summary.json").read_text())
        assert summary["cell_count"] == 18
        assert {cell["simulator_mode"] for cell in summary["cells"]} == {
            "hbf-fast"
        }
        constrained = [
            cell
            for cell in summary["cells"]
            if cell["ratio"] == "1:1" and cell["policy"] == "lru"
        ]
        assert len(constrained) == 1
        assert constrained[0]["misses"] == 3
        assert constrained[0]["modeled_device_time_ns"] == 30_096
        assert summary["execution_order_seed"] == 0
        assert summary["execution_order_randomized"] is True

        filtered_output = work / "filtered-output"
        filtered = subprocess.run(
            [
                sys.executable,
                str(replay),
                "--trace",
                str(trace),
                "--model-manifest",
                str(manifest),
                "--profile",
                str(profile),
                "--output-dir",
                str(filtered_output),
                "--git-commit",
                "c" * 40,
                "--environment-fingerprint",
                "d" * 64,
                "--timing-binary",
                str(timing_binary),
                "--timing-mode",
                "fast",
                "--timing-mode",
                "hybrid",
                "--ratio",
                "1:4",
                "--policy",
                "belady",
                "--order-seed",
                "17",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(filtered.stdout) == {"status": "PASS", "cell_count": 2}
        filtered_summary = json.loads(
            (filtered_output / "replay-summary.json").read_text()
        )
        assert filtered_summary["selected_ratios"] == ["1:4"]
        assert filtered_summary["selected_policies"] == ["belady"]
        assert filtered_summary["execution_order_seed"] == 17
        assert {cell["simulator_mode"] for cell in filtered_summary["cells"]} == {
            "hbf-fast",
            "hbf-hybrid",
        }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
