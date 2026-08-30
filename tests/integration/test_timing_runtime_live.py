#!/usr/bin/env python3
import ctypes
import hashlib
import json
import os
import pathlib
import sys

CUDA_SUCCESS = 0
HBFSIM_OK = 0

class HbfSimOptions(ctypes.Structure):
    _fields_ = [
        ("profile_path", ctypes.c_char_p),
        ("report_dir", ctypes.c_char_p),
        ("mode", ctypes.c_uint32),
        ("ring_capacity", ctypes.c_uint32),
        ("request_timeout_ns", ctypes.c_uint64),
    ]

class HbfSimRangeOptions(ctypes.Structure):
    _fields_ = [
        ("mode", ctypes.c_uint32),
        ("permissions", ctypes.c_uint32),
        ("cache_policy", ctypes.c_uint32),
        ("stream_id", ctypes.c_uint32),
    ]

class CULaunchConfig(ctypes.Structure):
    _fields_ = [
        ("grid_dim_x", ctypes.c_uint),
        ("grid_dim_y", ctypes.c_uint),
        ("grid_dim_z", ctypes.c_uint),
        ("block_dim_x", ctypes.c_uint),
        ("block_dim_y", ctypes.c_uint),
        ("block_dim_z", ctypes.c_uint),
        ("shared_mem_bytes", ctypes.c_uint),
        ("stream", ctypes.c_void_p),
        ("attrs", ctypes.c_void_p),
        ("num_attrs", ctypes.c_uint),
    ]

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def configure(function, argtypes, restype=ctypes.c_int):
    function.argtypes = argtypes
    function.restype = restype
    return function

def transform(plugin_path, manifest_path):
    host_launch_only = (
        os.environ.get("HBFSIM_TEST_HOST_LAUNCH_ONLY", "1") != "0"
    )
    ptx = """.version 8.8
.target sm_120
.address_size 64

.visible .entry timing_runtime_live(
    .param .u64 input_ptr,
    .param .u64 output_ptr
)
{
    .reg .b32 %r1;
    .reg .b64 %rd<3>;
    ld.param.u64 %rd1, [input_ptr];
    ld.param.u64 %rd2, [output_ptr];
    ld.global.u32 %r1, [%rd1];
    st.global.u32 [%rd2], %r1;
    ret;
}
"""
    os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
    plugin = ctypes.CDLL(str(plugin_path))
    process_input = configure(
        plugin.process_input,
        [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p],
    )
    request = json.dumps({
        "input": {
            "full_ptx": ptx,
            "to_patch_kernel": "timing_runtime_live",
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
            "host_launch_only": host_launch_only,
        },
        "ebpf_instructions": [],
    }).encode()
    output = ctypes.create_string_buffer(32 * 1024 * 1024)
    require(process_input(request, len(output), output) == 0,
            "PTX transformation failed")
    response = json.loads(output.value)
    require(response["modified"] is True, "PTX was not transformed")
    transformed = response["output_ptx"]
    manifest = json.loads(manifest_path.read_text())
    require("__hbfsim_module_identity" in transformed,
            "transformed PTX lacks HBFSim module identity")
    if host_launch_only:
        require(response["coverage"]["rewritten_instructions"] == 0,
                "host-only PTX unexpectedly rewrote memory instructions")
        require("__hbfsim_resolve" not in transformed and
                "__hbfsim_control" not in transformed,
                "host-only PTX contains HBFSim device instrumentation")
        require(manifest["host_launch_only"] is True and
                manifest["instrumented"] is False and
                manifest["rewritten_instructions"] == 0,
                "invalid host-only pass manifest")
    else:
        require(response["coverage"]["rewritten_instructions"] == 2,
                "device pass did not rewrite both memory instructions")
        require("__hbfsim_resolve" in transformed,
                "device pass lacks HBFSim resolver")
        require("call.uni" not in transformed,
                "device pass emitted an unproven uniform call")
        require(manifest["host_launch_only"] is False and
                manifest["instrumented"] is True and
                manifest["rewritten_instructions"] == 2,
                "invalid device pass manifest")
    return ptx, transformed

def main():
    if len(sys.argv) != 7:
        print("usage: test gate extension plugin daemon profile result_dir",
              file=sys.stderr)
        return 64
    gate, extension_path, plugin_path, daemon, profile, result_dir = map(
        pathlib.Path, sys.argv[1:])
    if result_dir.exists():
        require(not any(result_dir.iterdir()),
                "precreated result directory is not empty")
    else:
        result_dir.mkdir(parents=True)
    manifest_path = result_dir / "pass-manifest.jsonl"
    direct_native = os.environ.get("HBFSIM_TEST_DIRECT_NATIVE") == "1"
    original, transformed = transform(plugin_path.resolve(), manifest_path)
    executed = original if direct_native else transformed
    (result_dir / "transformed.ptx").write_text(transformed)
    (result_dir / "executed.ptx").write_text(executed)

    driver = ctypes.CDLL("libcuda.so.1")
    gate_library = (
        ctypes.CDLL(str(gate.resolve()), mode=ctypes.RTLD_GLOBAL)
        if direct_native else ctypes.CDLL(None)
    )
    process = gate_library
    extension = ctypes.CDLL(str(extension_path.resolve()))
    cuda_calls = driver if direct_native else process

    cu_init = configure(driver.cuInit, [ctypes.c_uint])
    cu_device_get = configure(
        driver.cuDeviceGet, [ctypes.POINTER(ctypes.c_int), ctypes.c_int])
    cu_primary_retain = configure(
        driver.cuDevicePrimaryCtxRetain,
        [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int])
    cu_ctx_set = configure(driver.cuCtxSetCurrent, [ctypes.c_void_p])
    cu_mem_alloc = configure(
        driver.cuMemAlloc_v2,
        [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t])
    cu_mem_free = configure(driver.cuMemFree_v2, [ctypes.c_uint64])
    cu_htod = configure(
        driver.cuMemcpyHtoD_v2,
        [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t])
    cu_dtoh = configure(
        driver.cuMemcpyDtoH_v2,
        [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_size_t])
    cu_get_function = configure(
        driver.cuModuleGetFunction,
        [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_char_p])
    cu_sync = configure(driver.cuCtxSynchronize, [])
    cu_primary_release = configure(driver.cuDevicePrimaryCtxRelease_v2,
                                   [ctypes.c_int])
    begin = configure(
        process.hbfsim_begin_module_load_from_ptx,
        [ctypes.c_char_p, ctypes.c_size_t],
        ctypes.c_uint64)
    load = configure(
        cuda_calls.cuModuleLoadDataEx,
        [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint,
         ctypes.c_void_p, ctypes.c_void_p])
    direct_bind = configure(
        process.hbfsim_bind_native_cuda_function,
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p])
    approve = configure(
        process.hbfsim_approve_original_cuda_function,
        [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p])
    launch = configure(
        cuda_calls.cuLaunchKernel,
        [ctypes.c_void_p] + [ctypes.c_uint] * 7 +
        [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p])
    launch_ex = configure(
        cuda_calls.cuLaunchKernelEx,
        [ctypes.POINTER(CULaunchConfig), ctypes.c_void_p,
         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p])
    unload = configure(cuda_calls.cuModuleUnload, [ctypes.c_void_p])

    context_create = configure(
        extension.hbfsim_context_create,
        [ctypes.POINTER(HbfSimOptions), ctypes.POINTER(ctypes.c_void_p)])
    register_device = configure(
        extension.hbfsim_register_device,
        [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
         ctypes.POINTER(HbfSimRangeOptions)])
    timing_backend = configure(
        extension.hbfsim_timing_backend, [ctypes.c_void_p], ctypes.c_char_p)
    context_destroy = configure(
        extension.hbfsim_context_destroy, [ctypes.c_void_p], None)

    device = ctypes.c_int()
    cuda_context = ctypes.c_void_p()
    input_ptr = ctypes.c_uint64()
    output_ptr = ctypes.c_uint64()
    hbfsim_context = ctypes.c_void_p()
    module = ctypes.c_void_p()
    post_launch_module = ctypes.c_void_p()
    created = registered = loaded = post_launch_loaded = False
    try:
        require(cu_init(0) == CUDA_SUCCESS, "cuInit failed")
        require(cu_device_get(ctypes.byref(device), 0) == CUDA_SUCCESS,
                "cuDeviceGet failed")
        require(cu_primary_retain(ctypes.byref(cuda_context), device.value)
                == CUDA_SUCCESS, "primary context retain failed")
        require(cu_ctx_set(cuda_context) == CUDA_SUCCESS,
                "cuCtxSetCurrent failed")
        require(cu_mem_alloc(ctypes.byref(input_ptr), 16 * 1024)
                == CUDA_SUCCESS, "input allocation failed")
        require(cu_mem_alloc(ctypes.byref(output_ptr), 16 * 1024)
                == CUDA_SUCCESS, "output allocation failed")
        input_value = ctypes.c_uint32(0x1234ABCD)
        zero = ctypes.c_uint32(0)
        require(cu_htod(input_ptr.value, ctypes.byref(input_value), 4)
                == CUDA_SUCCESS, "input initialization failed")
        require(cu_htod(output_ptr.value, ctypes.byref(zero), 4)
                == CUDA_SUCCESS, "output initialization failed")

        options = HbfSimOptions(
            str(profile.resolve()).encode(),
            str(result_dir.resolve()).encode(),
            0, 64, 30_000_000_000)
        require(context_create(ctypes.byref(options),
                               ctypes.byref(hbfsim_context)) == HBFSIM_OK,
                "hbfsim_context_create failed")
        created = True
        backend = timing_backend(hbfsim_context)
        require(backend == b"host_launch_mqsim",
                f"unexpected timing backend: {backend!r}")
        range_options = HbfSimRangeOptions(1, 1, 0, 0)
        require(register_device(
                    hbfsim_context, ctypes.c_void_p(input_ptr.value),
                    16 * 1024, ctypes.byref(range_options)) == HBFSIM_OK,
                "hbfsim_register_device failed")
        registered = True

        encoded = executed.encode()
        image = ctypes.create_string_buffer(encoded)
        load_token = begin(image, len(encoded))
        require((load_token == 0) == direct_native,
                "unexpected module identity transaction state")
        require(load(ctypes.byref(module), image, 0, None, None)
                == CUDA_SUCCESS, "cuModuleLoadDataEx failed")
        loaded = True
        function = ctypes.c_void_p()
        require(cu_get_function(ctypes.byref(function), module,
                                b"timing_runtime_live") == CUDA_SUCCESS,
                "cuModuleGetFunction failed")
        if direct_native:
            module_id = (
                "ptx:sha256:" +
                hashlib.sha256(original.encode()).hexdigest()
            ).encode()
            require(direct_bind(
                        function, module_id, b"timing_runtime_live") == 0,
                    "direct native function binding failed")
        input_arg = ctypes.c_uint64(input_ptr.value)
        output_arg = ctypes.c_uint64(output_ptr.value)
        parameters = (ctypes.c_void_p * 2)(
            ctypes.cast(ctypes.byref(input_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(output_arg), ctypes.c_void_p),
        )
        if direct_native:
            require(approve(function, parameters, None) == 1,
                    "direct native launch approval failed")
        use_launch_ex = os.environ.get("HBFSIM_TEST_LAUNCH_EX") == "1"
        if use_launch_ex:
            config = CULaunchConfig(
                256, 1, 1, 128, 1, 1, 0, None, None, 0)
            launch_status = launch_ex(
                ctypes.byref(config), function, parameters, None)
        else:
            launch_status = launch(
                function, 256, 1, 1, 128, 1, 1, 0, None, parameters, None)
        sync_status = cu_sync() if launch_status == CUDA_SUCCESS else launch_status
        post_launch_token = begin(image, len(encoded))
        post_launch_load_status = (
            load(ctypes.byref(post_launch_module), image, 0, None, None)
            if ((post_launch_token != 0 or direct_native) and
                sync_status == CUDA_SUCCESS)
            else sync_status
        )
        post_launch_loaded = post_launch_load_status == CUDA_SUCCESS
        observed = ctypes.c_uint32()
        copy_status = cu_dtoh(ctypes.byref(observed), output_ptr.value, 4)
        summary = {
            "direct_native": direct_native,
            "launch_status": launch_status,
            "sync_status": sync_status,
            "copy_status": copy_status,
            "observed": observed.value,
            "expected": input_value.value,
            "launch_api": "cuLaunchKernelEx" if use_launch_ex else "cuLaunchKernel",
            "registered": registered,
            "timing_backend": backend.decode(),
            "post_launch_module_load_status": post_launch_load_status,
        }
        (result_dir / "result.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n")
        require(launch_status == CUDA_SUCCESS, "gated launch failed")
        require(sync_status == CUDA_SUCCESS,
                f"device execution failed with CUDA status {sync_status}")
        require(post_launch_load_status == CUDA_SUCCESS,
                "post-launch CUDA module load failed")
        require(copy_status == CUDA_SUCCESS, "output copy failed")
        require(observed.value == input_value.value,
                "output value differs from input")
        evidence_path = result_dir / "host-timing-fallback.jsonl"
        evidence = [json.loads(line) for line in
                    evidence_path.read_text().splitlines() if line]
        require(len(evidence) == 1,
                f"expected one host timing request, got {len(evidence)}")
        require(evidence[0]["backend"] == "host_launch_mqsim" and
                evidence[0]["granularity"] == "launch" and
                evidence[0]["modeled_ns"] > 0 and
                evidence[0]["bytes"] == 16 * 1024,
                "invalid host timing evidence")
        print("timing_runtime_live=PASS")
        return 0
    finally:
        if post_launch_loaded:
            unload(post_launch_module)
        if loaded:
            unload(module)
        if created:
            context_destroy(hbfsim_context)
        if output_ptr.value:
            cu_mem_free(output_ptr.value)
        if input_ptr.value:
            cu_mem_free(input_ptr.value)
        if cuda_context.value:
            cu_ctx_set(None)
            cu_primary_release(device.value)

if __name__ == "__main__":
    raise SystemExit(main())
