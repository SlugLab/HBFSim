#!/usr/bin/env python3

import ctypes
import json
import pathlib
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    plugin_path = pathlib.Path(sys.argv[1]).resolve()
    helper_path = pathlib.Path(sys.argv[2]).resolve()
    ptxas = pathlib.Path(sys.argv[3]).resolve()
    helper = helper_path.read_text()
    for symbol in (
        "__hbfsim_control",
        "__hbfsim_control_generation",
        "__hbfsim_device_helper_marker",
        "__hbfsim_resolve",
        "__hbfsim_fault",
    ):
        require(symbol in helper, f"device helper lacks {symbol}")
    for instruction in (
        "ld.acquire.sys",
        "st.release.sys",
        "atom.cas.relaxed.sys",
        "atom.add.release.sys",
        "match.any.sync",
        "shfl.sync",
        "nanosleep",
        "%globaltimer",
    ):
        require(instruction in helper,
                f"device helper lacks required PTX instruction {instruction}")

    ptx = """.version 8.7
.target sm_120
.address_size 64

.visible .entry timing_kernel(
    .param .u64 timing_ptr
)
{
    .reg .b32 %r1;
    .reg .b64 %rd1;
    ld.param.u64 %rd1, [timing_ptr];
    ld.global.u32 %r1, [%rd1];
    ret;
}
"""
    request = json.dumps({
        "input": {
            "full_ptx": ptx,
            "to_patch_kernel": "timing_kernel",
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
        },
        "ebpf_instructions": [],
    }).encode()
    plugin = ctypes.CDLL(str(plugin_path))
    plugin.process_input.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                     ctypes.c_char_p]
    plugin.process_input.restype = ctypes.c_int
    output = ctypes.create_string_buffer(32 * 1024 * 1024)
    with tempfile.TemporaryDirectory(prefix="hbfsim-device-helper-") as work:
        work_path = pathlib.Path(work)
        manifest = work_path / "manifest.jsonl"
        import os
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest)
        status = plugin.process_input(request, len(output), output)
        require(status == 0, f"device-helper pass failed with status {status}")
        response = json.loads(output.value)
        transformed = response["output_ptx"]
        require(response["modified"] is True, "timing kernel was not rewritten")
        require(transformed.count("__hbfsim_device_helper_marker") == 1,
                "embedded helper was missing or duplicated")
        require(transformed.count(
                    ".visible .global .align 8 .u64 "
                    "__hbfsim_control_generation;") == 1,
                "control generation declaration was missing or duplicated")
        require("call.uni" in transformed and "__hbfsim_resolve" in transformed,
                "rewritten access does not call the embedded resolver")
        source = work_path / "self_contained.ptx"
        cubin = work_path / "self_contained.cubin"
        source.write_text(transformed)
        assembled = subprocess.run(
            [str(ptxas), "-arch=sm_120", str(source), "-o", str(cubin)],
            text=True, capture_output=True,
        )
        require(assembled.returncode == 0,
                f"self-contained transformed PTX did not assemble: "
                f"{assembled.stderr}")
        require(cubin.stat().st_size > 0, "ptxas produced an empty cubin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
