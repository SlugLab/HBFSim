#!/usr/bin/env python3

import ctypes
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def request_for(
    ptx: str, kernel: str, host_launch_only: bool = False
) -> bytes:
    return json.dumps(
        {
            "input": {
                "full_ptx": ptx,
                "to_patch_kernel": kernel,
                "global_ebpf_map_info_symbol": "map_info",
                "ebpf_communication_data_symbol": "constData",
                "host_launch_only": host_launch_only,
            },
            "ebpf_instructions": [],
        }
    ).encode()


def main() -> int:
    source = pathlib.Path(sys.argv[2]).read_text()
    ptxas = pathlib.Path(sys.argv[3]).resolve()
    configured_target = sys.argv[4]

    def configured_fixture(path: str) -> str:
        ptx = pathlib.Path(path).read_text().replace(
            ".target sm_120", f".target {configured_target}", 1
        )
        if int(configured_target.removeprefix("sm_")) < 75:
            ptx = ptx.replace(".nc.L2::128B", ".nc")
        return ptx

    for forbidden in ("hbfsim_expect_module_identity",
                      "publish_module_expectation", "dlsym(RTLD_DEFAULT"):
        require(forbidden not in source,
                "PTX pass still publishes stale process-global trust: "
                f"{forbidden}")
    plugin = ctypes.CDLL(str(pathlib.Path(sys.argv[1]).resolve()))
    plugin.print_config.argtypes = [ctypes.c_int, ctypes.c_char_p]
    plugin.process_input.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
    plugin.process_input.restype = ctypes.c_int

    config_buffer = ctypes.create_string_buffer(1024 * 1024)
    plugin.print_config(len(config_buffer), config_buffer)
    config = json.loads(config_buffer.value)
    require(config["name"] == "hbf_memory", "unexpected plugin name")

    ptx = configured_fixture("tests/fixtures/ptx/supported.ptx")
    request = request_for(ptx, "kernel")
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
                "excluded_functions": 0,
                "unsupported_opcodes": ["ld.param.u64"],
            },
            f"unexpected plugin coverage: {response['coverage']}",
        )
        require(response["output_ptx"].count("__hbfsim_module_identity") == 1,
                "transformed PTX must contain exactly one module identity")
        require(".visible .const .align 8 .b8 __hbfsim_module_identity[32]"
                in response["output_ptx"],
                "module identity must use immutable PTX constant storage")

        manifest = json.loads(manifest_path.read_text())
        require(re.fullmatch(r"ptx:sha256:[0-9a-f]{64}",
                             manifest["module_id"]) is not None,
                f"manifest lacks SHA-256 module identity: {manifest['module_id']}")
        require(manifest["kernel"] == "kernel", "manifest kernel mismatch")
        require(manifest["ptx_target"] == configured_target,
                "manifest target mismatch")
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
        require(manifest["host_launch_only"] is False,
                "ordinary pass was marked host-launch-only")

        host_manifest_path = pathlib.Path(directory) / "host-only.jsonl"
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(host_manifest_path)
        host_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        host_status = plugin.process_input(
            request_for(ptx, "kernel", host_launch_only=True),
            len(host_output), host_output,
        )
        require(host_status == 0,
                f"host-only pass failed with status {host_status}")
        host_response = json.loads(host_output.value)
        require(host_response["modified"] is True,
                "host-only PTX was not identity-tagged")
        require("__hbfsim_module_identity" in host_response["output_ptx"],
                "host-only PTX lacks module identity")
        require("__hbfsim_resolve" not in host_response["output_ptx"] and
                "__hbfsim_control" not in host_response["output_ptx"],
                "host-only PTX contains device instrumentation")
        require(
            host_response["coverage"]["rewritten_instructions"] == 0,
            "host-only PTX unexpectedly rewrote memory instructions",
        )
        host_manifest = json.loads(host_manifest_path.read_text())
        require(host_manifest["host_launch_only"] is True,
                "host-only manifest lacks mode marker")
        require(host_manifest["instrumented"] is False,
                "host-only manifest was marked device-instrumented")
        require(host_manifest["rewritten_instructions"] == 0,
                "host-only manifest reports rewritten instructions")
        require(host_manifest["parameters"] == manifest["parameters"],
                "host-only parameter ABI differs from ordinary pass")

        qualified_ptx = ptx.replace(
            ".param .u64 kernel_ptr",
            ".param .u64 .ptr .global .align 1 kernel_ptr",
        )
        qualified_manifest = pathlib.Path(directory) / "qualified.jsonl"
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(qualified_manifest)
        qualified_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        qualified_status = plugin.process_input(
            request_for(qualified_ptx, "kernel"), len(qualified_output),
            qualified_output,
        )
        require(qualified_status == 0,
                f"qualified pointer pass failed with status {qualified_status}")
        qualified_record = json.loads(qualified_manifest.read_text())
        require(qualified_record["parameters"][0]["kind"] == "pointer",
                "PTX .ptr .global parameter was not classified as a pointer")
        pointer_alignment_ptx = qualified_ptx.replace(
            ".param .u64 .ptr .global .align 1 kernel_ptr",
            ".param .u32 kernel_scalar,\n"
            "    .param .u64 .ptr .global .align 1 kernel_ptr",
        )
        pointer_alignment_manifest = (
            pathlib.Path(directory) / "pointer-alignment.jsonl"
        )
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(
            pointer_alignment_manifest
        )
        pointer_alignment_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        pointer_alignment_status = plugin.process_input(
            request_for(pointer_alignment_ptx, "kernel"),
            len(pointer_alignment_output), pointer_alignment_output,
        )
        require(pointer_alignment_status == 0,
                "qualified pointer ABI pass failed")
        pointer_alignment_record = json.loads(
            pointer_alignment_manifest.read_text()
        )
        require(pointer_alignment_record["parameters"] == [
            {"index": 0, "offset": 0, "width": 4, "kind": "scalar"},
            {"index": 1, "offset": 8, "width": 8, "kind": "pointer"},
        ], ".ptr .align describes pointee alignment, not parameter ABI "
           f"alignment: {pointer_alignment_record['parameters']}")
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
        identity = manifest["module_id"].removeprefix("ptx:sha256:")
        identity_bytes = ", ".join(
            f"0x{identity[index:index + 2]}" for index in range(0, 64, 2)
        )
        require(identity_bytes in response["output_ptx"],
                "manifest identity does not match injected module identity")
        ptx_file = pathlib.Path(directory) / "marked.ptx"
        cubin_file = pathlib.Path(directory) / "marked.cubin"
        ptx_file.write_text(response["output_ptx"])
        if ptxas.exists():
            assembled = subprocess.run(
                [str(ptxas), f"-arch={configured_target}", str(ptx_file),
                 "-o", str(cubin_file)],
                text=True, capture_output=True
            )
            require(assembled.returncode == 0,
                    f"marked PTX failed to assemble: {assembled.stderr}")

        malicious_ptx = ptx.replace(
            ".address_size 64\n",
            ".address_size 64\n.visible .const .align 8 .b8 "
            "__hbfsim_module_identity[32];\n",
        )
        malicious_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        malicious_status = plugin.process_input(
            request_for(malicious_ptx, "kernel"), len(malicious_output),
            malicious_output,
        )
        require(malicious_status != 0,
                "plugin accepted an untrusted preexisting identity marker")

        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = "/proc/1/hbfsim-manifest.jsonl"
        manifest_error_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        manifest_error_status = plugin.process_input(
            request, len(manifest_error_output), manifest_error_output,
        )
        require(manifest_error_status != 0,
                "plugin ignored an undurable manifest error")
        os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)

        second_kernel = """
.visible .entry kernel_b(
    .param .u64 kernel_b_ptr
)
{
    .reg .b32 %r<4>;
    .reg .b64 %rd<4>;
    ld.param.u64 %rd1, [kernel_b_ptr];
    ld.global.u32 %r1, [%rd1];
    ret;
}
"""
        multi_ptx = ptx.replace(".visible .entry kernel(",
                                ".visible .entry kernel_a(") + second_kernel
        manifest_path.unlink()
        first_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        first_status = plugin.process_input(
            request_for(multi_ptx, "kernel_a"), len(first_output), first_output)
        require(first_status == 0,
                f"first sequential pass failed with status {first_status}")
        first_response = json.loads(first_output.value)
        second_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        second_status = plugin.process_input(
            request_for(first_response["output_ptx"], "kernel_b"),
            len(second_output), second_output,
        )
        require(second_status == 0,
                f"second sequential pass failed with status {second_status}")
        sequential_manifests = [
            json.loads(line) for line in manifest_path.read_text().splitlines()
            if line
        ]
        require(len(sequential_manifests) == 2,
                "sequential pass did not emit two manifests")
        require(sequential_manifests[0]["module_id"] ==
                sequential_manifests[1]["module_id"],
                "sequential passes changed trusted module identity")
        second_response = json.loads(second_output.value)
        require(second_response["output_ptx"].count(
                    "__hbfsim_module_identity") == 1,
                "sequential pass duplicated module identity storage")
        if ptxas.exists():
            ptx_file.write_text(second_response["output_ptx"])
            assembled = subprocess.run(
                [str(ptxas), f"-arch={configured_target}", str(ptx_file),
                 "-o", str(cubin_file)],
                text=True, capture_output=True,
            )
            require(assembled.returncode == 0,
                    f"sequential marked PTX failed to assemble: {assembled.stderr}")

        first_byte = re.search(r"0x([0-9a-f]{2})",
                               second_response["output_ptx"])
        require(first_byte is not None, "trusted identity has no bytes")
        replacement = "0x00" if first_byte.group(1) != "00" else "0x01"
        mutated_ptx = (
            second_response["output_ptx"][:first_byte.start()] + replacement +
            second_response["output_ptx"][first_byte.end():]
        )
        mutated_output = ctypes.create_string_buffer(16 * 1024 * 1024)
        mutated_status = plugin.process_input(
            request_for(mutated_ptx, "kernel_b"), len(mutated_output),
            mutated_output,
        )
        require(mutated_status != 0,
                "plugin accepted a mutated trusted module state")
        manifest_path.unlink()

        aggregate_ptx = configured_fixture(
            "tests/fixtures/ptx/aggregate_parameter.ptx"
        )
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
