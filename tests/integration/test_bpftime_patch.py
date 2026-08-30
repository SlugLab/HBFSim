#!/usr/bin/env python3

import pathlib
import subprocess
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    patch = pathlib.Path(sys.argv[1]).resolve()
    bpftime = pathlib.Path(sys.argv[2]).resolve()
    require(patch.is_file(), f"bpftime provenance patch is missing: {patch}")
    applied = subprocess.run(
        ["git", "-C", str(bpftime), "apply", "--check", str(patch)],
        text=True,
        capture_output=True,
    )
    require(applied.returncode == 0,
            f"bpftime provenance patch does not apply: {applied.stderr}")

    text = patch.read_text()
    for required in (
        "hbfsim_begin_module_load_from_ptx",
        "hbfsim_end_module_load",
        "module_load_provenance_scope",
        "cuModuleLoadDataEx",
        "test_module_load_provenance.cpp",
        "both callbacks absent",
        "partial callback pair",
        "zero begin token",
        "matching begin and end",
        "original_ptx.empty()",
        "Skipping fatbin without extractable PTX",
	"BPFTIME_CUDA_LATE_PTX_PREPATCHED",
	"Using prepatched external PTX",
	"original PTX digest from staged filename",
        "fatbin_scan_disabled",
        "Skipping CUDA fatbin registration scan",
        "bpftime_nv_bind_ptx_variant",
        "runtime/agent/agent.version",
        "hook_entries_snapshot",
	"pass_execution_mutex",
	"cuLaunchKernelEx",
        "hbfsim_approve_original_cuda_function",
        "gate_approve(func, kernel_params, extra) != 0",
        "patched_kernel_by_ptx_variant",
        "patched_kernel_by_original_function",
        "find_patched_kernel_function_for_original",
        "native_execution_original_functions",
        "native_execution_patched_functions",
        "should_execute_original_function",
        "should_execute_original_for_patched",
        "native execution",
        "ambiguous patched CUDA kernel name",
        "sha256(original_ptx.data(), original_ptx.size())",
    ):
        require(required in text,
                f"bpftime provenance patch lacks contract evidence: {required}")

    cache_hit = text.find('SPDLOG_INFO("Module {} found in cache"')
    cache_miss = text.find("module_load_provenance_scope provenance")
    load = text.find("cuModuleLoadDataEx", cache_miss)
    require(cache_hit >= 0 and cache_miss > cache_hit and load > cache_miss,
            "bpftime bridge is not confined to the cache-miss load path")
    empty_guard = text.find("if (original_ptx.empty())")
    shared_memory_guard = text.find(
        "if (impl.shared_mem_ptr == 0)", empty_guard
    )
    require(
        empty_guard >= 0 and shared_memory_guard > empty_guard,
        "PTX-less fatbins are not skipped before shared-memory validation",
    )
    disabled_guard = text.find("if (fatbin_scan_disabled())")
    disabled_return = text.find("return;", disabled_guard)
    next_hunk = text.find("@@", disabled_guard)
    require(
        disabled_guard >= 0
        and disabled_return > disabled_guard
        and next_hunk > disabled_return,
        "disabled static fatbin scan does not bypass the host copy",
    )
    exact_lookup = text.find("find_patched_kernel_function_for_original")
    legacy_lookup = text.find("find_patched_kernel_function(*kernel_name)",
                              exact_lookup)
    require(exact_lookup >= 0 and legacy_lookup > exact_lookup,
            "driver launch does not prefer exact CUfunction binding")
    bind = text.find("int nv_attach_impl::bind_ptx_variant")
    policy = text.find("execution_policy = gate_bind", bind)
    publish = text.find("patched_kernel_by_original_function.emplace", bind)
    require(bind >= 0 and policy > bind and publish > policy,
            "exact binding publishes replacement state before gate policy")
    common = text.find("static CUresult cu_launch_kernel_common")
    common_exact = text.find(
        "find_patched_kernel_function_for_original(func)", common)
    common_approve = text.find("approve_exact_cuda_function", common_exact)
    common_native = text.find(
        "should_execute_original_function(func)", common_exact)
    common_replace = text.find("func = *patched;", common_exact)
    require(common >= 0 and common_exact > common and
            common_approve > common_exact and common_native > common_approve and
            common_replace > common_native,
            "ordinary exact launch does not approve then retain native execution")
    graph = text.find("cuda_graph_maybe_patch_kernel_node_params_v1")
    graph_exact = text.find(
        "find_patched_kernel_function_for_original", graph)
    graph_native = text.find(
        "should_execute_original_function(params->func)", graph_exact)
    graph_replace = text.find("patched_params.func = *exact", graph_exact)
    require(graph >= 0 and graph_exact > graph and
            graph_native > graph_exact and graph_replace > graph_native,
            "CUDA Graph exact binding replaces host-only native execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
