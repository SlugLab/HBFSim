#!/usr/bin/env python3

import ctypes
import json
import os
import pathlib
import sys
import tempfile


def main() -> int:
    plugin = ctypes.CDLL(str(pathlib.Path(sys.argv[1]).resolve()))
    plugin.print_config.argtypes = [ctypes.c_int, ctypes.c_char_p]
    plugin.process_input.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
    plugin.process_input.restype = ctypes.c_int

    config_buffer = ctypes.create_string_buffer(1024 * 1024)
    plugin.print_config(len(config_buffer), config_buffer)
    config = json.loads(config_buffer.value)
    assert config["name"] == "hbf_memory"

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
        assert status == 0
        response = json.loads(output.value)
        assert response["modified"] is True
        assert "__hbfsim_resolve" in response["output_ptx"]

        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == "kernel"
        assert manifest["instrumented"] is True
        assert manifest["pointer_parameters"] == [0]
        assert manifest["unsupported_parameters"] == []
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
