#!/usr/bin/env python3

import ctypes
import json
import os
import pathlib
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    plugin = ctypes.CDLL(str(pathlib.Path(sys.argv[1]).resolve()))
    plugin.print_config.argtypes = [ctypes.c_int, ctypes.c_char_p]
    plugin.process_input.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
    plugin.process_input.restype = ctypes.c_int

    config_buffer = ctypes.create_string_buffer(1024 * 1024)
    plugin.print_config(len(config_buffer), config_buffer)
    config = json.loads(config_buffer.value)
    require(config["name"] == "hbf_memory", "unexpected plugin name")

    ptx = pathlib.Path("tests/fixtures/ptx/supported.ptx").read_text()
    request = json.dumps(
        {
            "input": {
                "full_ptx": ptx,
                "to_patch_kernel": "kernel",
                "global_ebpf_map_info_symbol": "map_info",
                "ebpf_communication_data_symbol": "constData",
            },
            "ebpf_instructions": [],
        }
    ).encode()
    output = ctypes.create_string_buffer(16 * 1024 * 1024)
    with tempfile.TemporaryDirectory(prefix="hbfsim-pass-") as directory:
        manifest_path = pathlib.Path(directory) / "manifests.jsonl"
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
        status = plugin.process_input(request, len(output), output)
        require(status == 0, f"plugin failed with status {status}")
        response = json.loads(output.value)
        require(response["modified"] is True, "supported PTX was not modified")
        require("__hbfsim_resolve" in response["output_ptx"],
                "transformed PTX lacks resolver call")
        require(
            response["coverage"] == {
                "rewritten_instructions": 3,
                "unsupported_instructions": 1,
                "excluded_functions": 1,
                "unsupported_opcodes": ["ld.param.u64"],
            },
            f"unexpected plugin coverage: {response['coverage']}",
        )
        require(response["output_ptx"].count("__hbfsim_module_marker") == 1,
                "transformed PTX must contain exactly one module marker")

        manifest = json.loads(manifest_path.read_text())
        require(manifest["module_id"].startswith("ptx:"),
                "manifest lacks PTX module identity")
        require(manifest["kernel"] == "kernel", "manifest kernel mismatch")
        require(manifest["ptx_target"] == "sm_120", "manifest target mismatch")
        require(manifest["instrumented"] is True,
                "supported kernel not marked instrumented")
        require(manifest["cubin_only"] is False,
                "PTX manifest incorrectly marked cubin-only")
        require(
            manifest["parameters"] == [
                {"index": 0, "offset": 0, "width": 8, "kind": "pointer"}
            ],
            f"unexpected parameter ABI: {manifest['parameters']}",
        )
        require(manifest["unsupported_parameters"] == [],
                "supported kernel has unsupported parameters")
        marker = manifest["module_id"].removeprefix("ptx:")
        require(f"__hbfsim_module_marker = 0x{marker}" in response["output_ptx"],
                "manifest identity does not match injected module marker")
        ptx_file = pathlib.Path(directory) / "marked.ptx"
        cubin_file = pathlib.Path(directory) / "marked.cubin"
        ptx_file.write_text(response["output_ptx"])
        ptxas = pathlib.Path("/usr/local/cuda-12.8/bin/ptxas")
        if ptxas.exists():
            assembled = subprocess.run(
                [str(ptxas), "-arch=sm_120", str(ptx_file), "-o", str(cubin_file)],
                text=True, capture_output=True
            )
            require(assembled.returncode == 0,
                    f"marked PTX failed to assemble: {assembled.stderr}")
        manifest_path.unlink()

        aggregate_ptx = pathlib.Path(
            "tests/fixtures/ptx/aggregate_parameter.ptx"
        ).read_text()
        aggregate_request = json.dumps(
            {
                "input": {
                    "full_ptx": aggregate_ptx,
                    "to_patch_kernel": "aggregate_kernel",
                    "global_ebpf_map_info_symbol": "map_info",
                    "ebpf_communication_data_symbol": "constData",
                },
                "ebpf_instructions": [],
            }
        ).encode()
        aggregate_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        aggregate_status = plugin.process_input(
            aggregate_request, len(aggregate_output), aggregate_output
        )
        require(aggregate_status == 0,
                f"aggregate plugin request failed with status {aggregate_status}")
        aggregate_manifest = json.loads(manifest_path.read_text())
        require(
            aggregate_manifest["parameters"] == [
                {
                    "index": 0,
                    "offset": 0,
                    "width": 24,
                    "kind": "opaque_aggregate",
                }
            ],
            f"unexpected aggregate ABI: {aggregate_manifest['parameters']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
