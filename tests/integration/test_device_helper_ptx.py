#!/usr/bin/env python3

import ctypes
import json
import pathlib
import re
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
    configured_target = sys.argv[4]
    helper = helper_path.read_text()
    for symbol in (
        "__hbfsim_control",
        "__hbfsim_control_generation",
        "__hbfsim_device_helper_marker",
        "__hbfsim_future_issue",
        "__hbfsim_future_poll",
        "__hbfsim_future_wait",
        "__hbfsim_future_fault",
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
    require(re.search(
        r"\.visible\s+\.func\s+\(\.param\s+\.align\s+16\s+\.b8\s+"
        r"func_retval0\[64\]\)\s+__hbfsim_future_issue\(", helper),
        "future issue has the wrong 64-byte return ABI")
    require(re.search(
        r"__hbfsim_future_poll\(\s*\.param\s+\.align\s+16\s+\.b8\s+"
        r"__hbfsim_future_poll_param_0\[64\]", helper),
        "future poll has the wrong 64-byte parameter ABI")
    require(re.search(
        r"\.visible\s+\.func\s+\(\.param\s+\.align\s+16\s+\.b8\s+"
        r"func_retval0\[64\]\)\s+__hbfsim_future_wait\(", helper),
        "future wait has the wrong 64-byte return ABI")

    ptx_template = """.version 8.7
.target {target}
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
        targets = [configured_target]
        if int(configured_target.removeprefix("sm_")) in {90, 100, 101, 120}:
            targets.append(f"{configured_target}a")
        for target in targets:
            request = json.dumps({
                "input": {
                    "full_ptx": ptx_template.replace("{target}", target),
                    "to_patch_kernel": "timing_kernel",
                    "global_ebpf_map_info_symbol": "map_info",
                    "ebpf_communication_data_symbol": "constData",
                },
                "ebpf_instructions": [],
            }).encode()
            output.value = b""
            status = plugin.process_input(request, len(output), output)
            require(status == 0,
                    f"device-helper pass failed for {target} with status {status}")
            response = json.loads(output.value)
            transformed = response["output_ptx"]
            require(response["modified"] is True,
                    f"{target} timing kernel was not rewritten")
            require(transformed.count("__hbfsim_device_helper_marker") == 1,
                    "embedded helper was missing or duplicated")
            require(transformed.count(
                        ".visible .global .align 8 .u64 "
                        "__hbfsim_control_generation;") == 1,
                    "control generation declaration was missing or duplicated")
            require("call.uni" in transformed and
                    "__hbfsim_resolve" in transformed,
                    "rewritten access does not call the embedded resolver")
            require("__hbfsim_future_issue" in transformed and
                    "__hbfsim_future_wait" in transformed,
                    "embedded helper lacks split future entry points")
            source = work_path / f"self_contained_{target}.ptx"
            cubin = work_path / f"self_contained_{target}.cubin"
            source.write_text(transformed)
            assembled = subprocess.run(
                [str(ptxas), f"-arch={target}", str(source), "-o", str(cubin)],
                text=True, capture_output=True,
            )
            require(assembled.returncode == 0,
                    f"self-contained {target} PTX did not assemble: "
                    f"{assembled.stderr}")
            require(cubin.stat().st_size > 0,
                    f"ptxas produced an empty {target} cubin")

        configured_architecture = int(configured_target.removeprefix("sm_"))
        mismatched_target = (
            "sm_75" if configured_architecture == 70 else "sm_70"
        )
        for rejected_target in (mismatched_target, f"{configured_target}x"):
            request = json.dumps({
                "input": {
                    "full_ptx": ptx_template.replace(
                        "{target}", rejected_target
                    ),
                    "to_patch_kernel": "timing_kernel",
                    "global_ebpf_map_info_symbol": "map_info",
                    "ebpf_communication_data_symbol": "constData",
                },
                "ebpf_instructions": [],
            }).encode()
            output.value = b""
            status = plugin.process_input(request, len(output), output)
            require(status != 0,
                    f"device-helper pass accepted incompatible target "
                    f"{rejected_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
