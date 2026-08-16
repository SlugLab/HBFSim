cmake_minimum_required(VERSION 3.25)

project(HBFSim VERSION 0.1.0 LANGUAGES CXX)

option(HBFSIM_ENABLE_CUDA "Build CUDA instrumentation and runtime components" ON)
option(HBFSIM_ENABLE_MQSIM "Build the MQSim-backed host service" ON)
option(HBFSIM_ENABLE_LLM_TESTS "Enable llama.cpp and vLLM integration tests" OFF)

if(HBFSIM_ENABLE_CUDA)
    if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
        set(
            CMAKE_CUDA_ARCHITECTURES 120 CACHE STRING
            "Single CUDA architecture supported by the embedded HBFSim helper"
        )
    endif()
    if(NOT "${CMAKE_CUDA_ARCHITECTURES}" MATCHES "^[1-9][0-9]*$")
        message(
            FATAL_ERROR
            "HBFSim requires one numeric baseline CUDA architecture because "
            "the device helper PTX is embedded into instrumented modules; "
            "found '${CMAKE_CUDA_ARCHITECTURES}'"
        )
    endif()
    set(HBFSIM_CUDA_ARCHITECTURE "${CMAKE_CUDA_ARCHITECTURES}")
    if(HBFSIM_CUDA_ARCHITECTURE LESS 70)
        message(
            FATAL_ERROR
            "HBFSim requires compute capability 7.0 or newer for "
            "__match_any_sync, __nanosleep, and acquire/release system atomics; "
            "found sm_${HBFSIM_CUDA_ARCHITECTURE}"
        )
    endif()
    set(HBFSIM_CUDA_PTX_TARGET "sm_${HBFSIM_CUDA_ARCHITECTURE}")
    if(NOT HBFSIM_CUDA_ARCHITECTURE STREQUAL "120")
        message(
            WARNING
            "sm_${HBFSIM_CUDA_ARCHITECTURE} is a non-reference HBFSim target. "
            "A successful build does not establish live host/GPU control "
            "protocol validation on that platform."
        )
    endif()
endif()

include(CTest)
include(cmake/Dependencies.cmake)
find_package(OpenSSL REQUIRED COMPONENTS Crypto)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

add_library(
    hbfsim_core STATIC
    src/api.cpp
    src/cuda_runtime/context.cpp
    src/cuda_runtime/capacity_runtime.cpp
    src/cuda_runtime/coverage.cpp
    src/cuda_runtime/exact_admission.cpp
    src/cuda_runtime/exact_artifact.cpp
    src/cuda_runtime/exact_environment.cpp
    src/cuda_runtime/hbm_cache.cpp
    src/cuda_runtime/module_identity.cpp
    src/cuda_runtime/range_table.cpp
    src/cuda_runtime/shadow_future.cpp
    src/cuda_runtime/sm120_channels.cpp
    src/cuda_runtime/timing_binding.cpp
    src/cuda_runtime/tensormap.cpp
    src/cuda_runtime/tma_tile.cpp
    src/cuda_runtime/tma_async.cpp
    src/cuda_runtime/thermal_telemetry.cpp
    src/cuda_runtime/device_tensormap.cpp
    src/cuda_runtime/vmm.cpp
    src/hybrid/calibrator.cpp
    src/profile/exact_profile.cpp
    src/profile/profile.cpp
    src/thermal/thermal_reliability.cpp
    src/host_service/request_dispatcher.cpp
    src/host_service/thermal_controller.cpp
    src/host_service/refresh_scheduler.cpp
    src/host_service/backing_store.cpp
    src/host_service/capacity_backing_router.cpp
    src/host_service/capacity_page_service.cpp
    src/host_service/capacity_worker.cpp
    src/protocol/page_directory.cpp
    src/ptxpass_hbf/ptx_analysis.cpp
    src/ptxpass_hbf/async_object_analysis.cpp
    src/ptxpass_hbf/ptx_async_op.cpp
    src/ptxpass_hbf/future_transform.cpp
    src/ptxpass_hbf/ptx_memory_op.cpp
    src/ptxpass_hbf/ptx_ir.cpp
    src/ptxpass_hbf/transform.cpp
    src/ptxpass_hbf/tma_transform.cpp
    src/reporting/coverage_writer.cpp
    src/reporting/durable_append.cpp
    src/reporting/thermal_report.cpp
)
target_include_directories(
    hbfsim_core
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
)

add_executable(hbfsimd src/host_service/main.cpp)
target_link_libraries(hbfsimd PRIVATE hbfsim_core)
target_include_directories(
    hbfsimd PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/host_service"
)
target_compile_features(hbfsim_core PUBLIC cxx_std_20)
target_link_libraries(hbfsim_core PUBLIC ${CMAKE_DL_LIBS} OpenSSL::Crypto)
set_target_properties(hbfsim_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(
    hbfsim_core
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
)

add_library(hbfsim SHARED src/cuda_runtime/shared_library.cpp)
target_link_libraries(
    hbfsim PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,hbfsim_core>"
)
set_target_properties(
    hbfsim PROPERTIES OUTPUT_NAME hbfsim VERSION 0.1.0 SOVERSION 0
)

add_executable(hbfsim_cpu_tests tests/cpu/build_smoke.cpp)
target_link_libraries(hbfsim_cpu_tests PRIVATE hbfsim_core)

add_test(NAME build_smoke COMMAND hbfsim_cpu_tests)

add_executable(hbfsim_profile_tests tests/cpu/profile_test.cpp)
target_link_libraries(hbfsim_profile_tests PRIVATE hbfsim_core)

add_test(NAME profile COMMAND hbfsim_profile_tests)
set_tests_properties(profile PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

add_executable(
    thermal_reliability_test tests/cpu/thermal_reliability_test.cpp
)
target_link_libraries(thermal_reliability_test PRIVATE hbfsim_core)
add_test(NAME thermal_reliability COMMAND thermal_reliability_test)

add_executable(thermal_telemetry_test tests/cpu/thermal_telemetry_test.cpp)
target_link_libraries(thermal_telemetry_test PRIVATE hbfsim_core)
add_test(NAME thermal_telemetry COMMAND thermal_telemetry_test)

add_executable(thermal_controller_test tests/cpu/thermal_controller_test.cpp)
target_link_libraries(thermal_controller_test PRIVATE hbfsim_core)
add_test(NAME thermal_controller COMMAND thermal_controller_test)
set_tests_properties(
    thermal_controller PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(thermal_report_fixture tests/cpu/thermal_report_fixture.cpp)
target_link_libraries(thermal_report_fixture PRIVATE hbfsim_core)
add_test(
    NAME thermal_report
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_thermal_report.py"
            "$<TARGET_FILE:thermal_report_fixture>"
)

add_executable(
    thermal_device_reference_test tests/cpu/thermal_device_reference_test.cpp
)
target_link_libraries(thermal_device_reference_test PRIVATE hbfsim_core)
add_test(NAME thermal_device_reference COMMAND thermal_device_reference_test)

add_executable(refresh_scheduler_test tests/cpu/refresh_scheduler_test.cpp)
target_link_libraries(refresh_scheduler_test PRIVATE hbfsim_core)
add_test(NAME refresh_scheduler COMMAND refresh_scheduler_test)
set_tests_properties(
    refresh_scheduler PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(background_dispatch_test tests/cpu/background_dispatch_test.cpp)
target_link_libraries(background_dispatch_test PRIVATE hbfsim_core)
add_test(NAME background_dispatch COMMAND background_dispatch_test)
set_tests_properties(
    background_dispatch PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(thermal_consistency_test tests/cpu/thermal_consistency_test.cpp)
target_link_libraries(thermal_consistency_test PRIVATE hbfsim_core)
add_test(NAME thermal_consistency COMMAND thermal_consistency_test)
set_tests_properties(
    thermal_consistency PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(thermal_daemon_test tests/integration/thermal_daemon_test.cpp)
target_link_libraries(thermal_daemon_test PRIVATE hbfsim_core)
add_test(NAME thermal_daemon COMMAND thermal_daemon_test)
set_tests_properties(
    thermal_daemon PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(thermal_capacity_test tests/integration/thermal_capacity_test.cpp)
target_link_libraries(thermal_capacity_test PRIVATE hbfsim_core)
add_test(NAME thermal_capacity COMMAND thermal_capacity_test)
set_tests_properties(
    thermal_capacity PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_test(
    NAME thermal_timing_environment
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_thermal_timing_environment.py"
)

add_executable(exact_profile_test tests/cpu/exact_profile_test.cpp)
target_link_libraries(exact_profile_test PRIVATE hbfsim_core)
target_include_directories(
    exact_profile_test
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
)
add_test(NAME exact_profile COMMAND exact_profile_test)
set_tests_properties(
    exact_profile PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(sm120_channels_test tests/cpu/sm120_channels_test.cpp)
target_link_libraries(sm120_channels_test PRIVATE hbfsim_core)
add_test(NAME sm120_channels COMMAND sm120_channels_test)
set_tests_properties(
    sm120_channels PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(
    sm120_channels_property_test tests/cpu/sm120_channels_property_test.cpp
)
target_link_libraries(sm120_channels_property_test PRIVATE hbfsim_core)
add_test(NAME sm120_channels_property COMMAND sm120_channels_property_test)
if(HBFSIM_ENABLE_CUDA)
    add_test(
        NAME sm120_calibration_cases
        COMMAND Python3::Interpreter
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_sm120_calibration_cases.py"
                "${CMAKE_BINARY_DIR}/benchmarks/cuda/sm120_calibration"
    )
endif()
add_test(
    NAME collect_sm120
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_collect_sm120.py"
)
add_test(
    NAME fit_sm120_channels
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_fit_sm120_channels.py"
)
add_test(
    NAME validate_sm120_exact
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_validate_sm120_exact.py"
)

add_executable(hbfsim_calibrator_tests tests/cpu/calibrator_test.cpp)
target_link_libraries(hbfsim_calibrator_tests PRIVATE hbfsim_core)
add_test(NAME calibrator COMMAND hbfsim_calibrator_tests)

add_executable(hbfsim_protocol_tests tests/cpu/protocol_layout_test.cpp)
target_link_libraries(hbfsim_protocol_tests PRIVATE hbfsim_core)
add_test(NAME protocol COMMAND hbfsim_protocol_tests)
add_test(NAME protocol_layout COMMAND hbfsim_protocol_tests)

add_executable(shadow_future_test tests/cpu/shadow_future_test.cpp)
target_link_libraries(shadow_future_test PRIVATE hbfsim_core)
add_test(NAME shadow_future COMMAND shadow_future_test)

add_executable(tensormap_registry_test tests/cpu/tensormap_registry_test.cpp)
target_link_libraries(tensormap_registry_test PRIVATE hbfsim_core)
add_test(NAME tensormap_registry COMMAND tensormap_registry_test)

add_executable(tma_tile_test tests/cpu/tma_tile_test.cpp)
target_link_libraries(tma_tile_test PRIVATE hbfsim_core)
add_test(NAME tma_tile COMMAND tma_tile_test)

add_executable(tma_tile_property_test tests/cpu/tma_tile_property_test.cpp)
target_link_libraries(tma_tile_property_test PRIVATE hbfsim_core)
add_test(NAME tma_tile_property COMMAND tma_tile_property_test)

add_executable(tma_async_test tests/cpu/tma_async_test.cpp)
target_link_libraries(tma_async_test PRIVATE hbfsim_core)
add_test(NAME tma_async COMMAND tma_async_test)

add_executable(tma_multicast_test tests/cpu/tma_multicast_test.cpp)
target_link_libraries(tma_multicast_test PRIVATE hbfsim_core)
add_test(NAME tma_multicast COMMAND tma_multicast_test)

add_executable(hbfsim_page_directory_tests tests/cpu/page_directory_test.cpp)
target_link_libraries(hbfsim_page_directory_tests PRIVATE hbfsim_core)
add_test(NAME page_directory COMMAND hbfsim_page_directory_tests)

add_executable(hbfsim_range_table_tests tests/cpu/range_table_test.cpp)
target_link_libraries(hbfsim_range_table_tests PRIVATE hbfsim_core)
add_test(NAME range_table COMMAND hbfsim_range_table_tests)

add_executable(hbfsim_timing_binding_tests tests/cpu/timing_binding_test.cpp)
target_link_libraries(hbfsim_timing_binding_tests PRIVATE hbfsim_core)
add_test(NAME timing_binding COMMAND hbfsim_timing_binding_tests)

add_executable(
    hbfsim_device_helper_abi_tests tests/cpu/device_helper_abi_test.cpp
)
target_link_libraries(hbfsim_device_helper_abi_tests PRIVATE hbfsim_core)
add_test(NAME device_helper_abi COMMAND hbfsim_device_helper_abi_tests)

add_executable(
    device_future_reference_test tests/cpu/device_future_reference_test.cpp
)
target_link_libraries(device_future_reference_test PRIVATE hbfsim_core)
add_test(NAME device_future_reference COMMAND device_future_reference_test)

add_executable(
    device_tensormap_reference_test
    tests/cpu/device_tensormap_reference_test.cpp
)
target_link_libraries(device_tensormap_reference_test PRIVATE hbfsim_core)
add_test(NAME device_tensormap_reference COMMAND device_tensormap_reference_test)

add_executable(hbfsim_backing_store_tests tests/cpu/backing_store_test.cpp)
target_link_libraries(hbfsim_backing_store_tests PRIVATE hbfsim_core)
add_test(NAME backing_store COMMAND hbfsim_backing_store_tests)

add_executable(
    hbfsim_capacity_backing_router_tests
    tests/cpu/capacity_backing_router_test.cpp
)
target_link_libraries(hbfsim_capacity_backing_router_tests PRIVATE hbfsim_core)
add_test(
    NAME capacity_backing_router COMMAND hbfsim_capacity_backing_router_tests
)

add_executable(hbfsim_hbm_cache_tests tests/cpu/hbm_cache_test.cpp)
target_link_libraries(hbfsim_hbm_cache_tests PRIVATE hbfsim_core)
add_test(NAME hbm_cache COMMAND hbfsim_hbm_cache_tests)

add_executable(hbfsim_vmm_tests tests/cpu/vmm_test.cpp)
target_link_libraries(hbfsim_vmm_tests PRIVATE hbfsim_core)
add_test(NAME vmm COMMAND hbfsim_vmm_tests)

add_executable(
    hbfsim_capacity_page_service_tests
    tests/cpu/capacity_page_service_test.cpp
)
target_link_libraries(hbfsim_capacity_page_service_tests PRIVATE hbfsim_core)
target_link_options(
    hbfsim_capacity_page_service_tests PRIVATE "LINKER:--wrap=fdatasync"
)
add_test(
    NAME capacity_page_service COMMAND hbfsim_capacity_page_service_tests
)

add_executable(
    hbfsim_capacity_handoff_tests tests/cpu/capacity_handoff_test.cpp
)
target_link_libraries(hbfsim_capacity_handoff_tests PRIVATE hbfsim_core)
add_test(NAME capacity_handoff COMMAND hbfsim_capacity_handoff_tests)

add_executable(
    hbfsim_capacity_dispatch_tests tests/cpu/capacity_dispatch_test.cpp
)
target_link_libraries(hbfsim_capacity_dispatch_tests PRIVATE hbfsim_core)
add_test(NAME capacity_dispatch COMMAND hbfsim_capacity_dispatch_tests)

add_executable(
    hbfsim_capacity_worker_tests tests/cpu/capacity_worker_test.cpp
)
target_link_libraries(hbfsim_capacity_worker_tests PRIVATE hbfsim_core)
add_test(NAME capacity_worker COMMAND hbfsim_capacity_worker_tests)

add_executable(
    hbfsim_device_ordering_reference_tests
    tests/cpu/device_ordering_reference_test.cpp
)
target_link_libraries(
    hbfsim_device_ordering_reference_tests PRIVATE hbfsim_core
)
add_test(
    NAME device_ordering_reference
    COMMAND hbfsim_device_ordering_reference_tests
)

add_executable(context_lifecycle_test tests/cpu/context_lifecycle_test.cpp)
target_link_libraries(context_lifecycle_test PRIVATE hbfsim_core)
add_library(hbfsim_marker_preload SHARED tests/integration/marker_preload.cpp)
add_dependencies(context_lifecycle_test hbfsim_marker_preload hbfsimd)
if(NOT HBFSIM_ENABLE_CUDA)
    target_compile_definitions(
        context_lifecycle_test PRIVATE HBFSIM_TEST_CUDA_DISABLED=1
    )
endif()
add_test(
    NAME context_lifecycle
    COMMAND context_lifecycle_test "$<TARGET_FILE:hbfsimd>"
            "$<TARGET_FILE:hbfsim_marker_preload>"
)
set_tests_properties(
    context_lifecycle PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(daemon_protocol_test tests/integration/daemon_protocol_test.cpp)
target_link_libraries(daemon_protocol_test PRIVATE hbfsim_core)
add_test(
    NAME daemon_protocol
    COMMAND daemon_protocol_test "$<TARGET_FILE:hbfsimd>"
)
set_tests_properties(
    daemon_protocol PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(coverage_gate_test tests/cpu/coverage_gate_test.cpp)
target_link_libraries(coverage_gate_test PRIVATE hbfsim_core)
add_test(NAME coverage_gate COMMAND coverage_gate_test)

add_executable(durable_append_test tests/cpu/durable_append_test.cpp)
target_link_libraries(durable_append_test PRIVATE hbfsim_core)
target_link_options(
    durable_append_test PRIVATE
    "LINKER:--wrap=write,--wrap=fdatasync,--wrap=fsync"
)
add_test(NAME durable_append COMMAND durable_append_test)

add_executable(module_identity_test tests/cpu/module_identity_test.cpp)
target_link_libraries(module_identity_test PRIVATE hbfsim_core)
add_test(NAME module_identity COMMAND module_identity_test)

add_executable(exact_artifact_test tests/cpu/exact_artifact_test.cpp)
target_link_libraries(exact_artifact_test PRIVATE hbfsim_core)
target_include_directories(
    exact_artifact_test
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
)
add_test(NAME exact_artifact COMMAND exact_artifact_test)

add_executable(exact_environment_test tests/cpu/exact_environment_test.cpp)
target_link_libraries(exact_environment_test PRIVATE hbfsim_core)
add_test(NAME exact_environment COMMAND exact_environment_test)

add_executable(exact_admission_test tests/cpu/exact_admission_test.cpp)
target_link_libraries(exact_admission_test PRIVATE hbfsim_core)
add_test(NAME exact_admission COMMAND exact_admission_test)

add_executable(ptx_transform_test tests/cpu/ptx_transform_test.cpp)
target_link_libraries(ptx_transform_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_transform_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
if(HBFSIM_ENABLE_CUDA)
    add_test(NAME ptx_transform COMMAND ptx_transform_test)
    set_tests_properties(
        ptx_transform PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    )
endif()

add_executable(ptx_ir_test tests/cpu/ptx_ir_test.cpp)
target_link_libraries(ptx_ir_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_ir_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
add_test(NAME ptx_ir COMMAND ptx_ir_test)

add_executable(ptx_async_op_test tests/cpu/ptx_async_op_test.cpp)
target_link_libraries(ptx_async_op_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_async_op_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
add_test(NAME ptx_async_op COMMAND ptx_async_op_test)

add_executable(ptx_tma_transform_test tests/cpu/ptx_tma_transform_test.cpp)
target_link_libraries(ptx_tma_transform_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_tma_transform_test
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
target_compile_definitions(
    ptx_tma_transform_test PRIVATE HBFSIM_TEST_DEVICE_HELPER_PTX=1
)
if(HBFSIM_ENABLE_CUDA)
    add_test(NAME ptx_tma_transform COMMAND ptx_tma_transform_test)
    set_tests_properties(
        ptx_tma_transform
        PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    )
endif()

add_executable(
    async_object_analysis_test tests/cpu/async_object_analysis_test.cpp
)
target_link_libraries(async_object_analysis_test PRIVATE hbfsim_core)
target_include_directories(
    async_object_analysis_test
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
add_test(NAME async_object_analysis COMMAND async_object_analysis_test)
set_tests_properties(
    ptx_ir PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(ptx_analysis_test tests/cpu/ptx_analysis_test.cpp)
target_link_libraries(ptx_analysis_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_analysis_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
add_test(NAME ptx_analysis COMMAND ptx_analysis_test)

add_executable(
    ptx_future_transform_test tests/cpu/ptx_future_transform_test.cpp
)
target_link_libraries(ptx_future_transform_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_future_transform_test
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
add_test(NAME ptx_future_transform COMMAND ptx_future_transform_test)
set_tests_properties(
    ptx_future_transform
    PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)

add_executable(ptxpass_hbf src/ptxpass_hbf/main.cpp)
target_link_libraries(ptxpass_hbf PRIVATE hbfsim_core)
target_include_directories(
    ptxpass_hbf
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
)
set_target_properties(
    ptxpass_hbf PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/src/ptxpass_hbf"
)

add_library(ptxpass_hbf_plugin SHARED src/ptxpass_hbf/plugin.cpp)
target_link_libraries(
    ptxpass_hbf_plugin PRIVATE hbfsim_core OpenSSL::Crypto
)
target_include_directories(
    ptxpass_hbf_plugin
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
)
set_target_properties(
    ptxpass_hbf_plugin PROPERTIES
    OUTPUT_NAME ptxpass_hbf
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
)

if(BUILD_TESTING)
    find_program(HBFSIM_PYTHON3_EXECUTABLE python3 REQUIRED)
    add_test(
        NAME build_sm120_artifact
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_build_sm120_artifact.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/calibration/build_sm120_artifact.py"
    )
    if(HBFSIM_ENABLE_CUDA)
        add_test(
            NAME exact_prepare_sm120
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_prepare_sm120_exact.py"
                "${CMAKE_CURRENT_SOURCE_DIR}/scripts/calibration/prepare_sm120_exact.py"
                "$<TARGET_FILE:ptxpass_hbf_plugin>"
                "${CMAKE_CURRENT_SOURCE_DIR}/scripts/calibration/build_sm120_artifact.py"
        )
    endif()
    add_test(
        NAME exact_merge_sm120_fragments
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_merge_sm120_fragments.py"
    )
    if(HBFSIM_ENABLE_CUDA)
        add_test(
            NAME exact_sm120_admission
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_sm120_exact_admission.py"
                "$<TARGET_FILE:hbfsim_launch_gate>"
        )
    endif()
endif()

if(BUILD_TESTING)
    add_test(
        NAME llama_cpp_adapter
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_llama_cpp.py"
    )
    add_test(
        NAME thermal_logp
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_thermal_logp.py"
    )
    add_test(
        NAME vmem_tuning
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_vmem_tuning.py"
    )
    add_test(
        NAME bpftime_patch
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_patch.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
    )
    add_test(
        NAME bpftime_aot_patch
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_aot_patch.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0002-sm120-aot-bundle-load.patch"
    )
    add_test(
        NAME bpftime_host_compiler_patch
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_host_compiler_patch.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0002-sm120-aot-bundle-load.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0003-libbpf-modern-libc-const.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0004-honor-llvm-aot-cli-option.patch"
    )
    add_test(
        NAME bpftime_cuda13_patch
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_cuda13_patch.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0002-sm120-aot-bundle-load.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0003-libbpf-modern-libc-const.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0004-honor-llvm-aot-cli-option.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0005-cuda13-context-create.patch"
    )
    add_test(
        NAME build_patched_bpftime
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_build_patched_bpftime.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/build_patched_bpftime.sh"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
            "${CMAKE_CURRENT_SOURCE_DIR}/cmake/PreparePatchedBpftime.cmake"
    )
    add_test(
        NAME bpftime_prepatched_bootstrap_patch
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_prepatched_bootstrap_patch.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0002-sm120-aot-bundle-load.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0003-libbpf-modern-libc-const.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0004-honor-llvm-aot-cli-option.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0005-cuda13-context-create.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0006-prepatched-bootstrap-once.patch"
    )

    add_executable(
        coverage_manifest_flow_test
        tests/integration/coverage_manifest_flow_test.cpp
    )
    target_link_libraries(
        coverage_manifest_flow_test PRIVATE hbfsim_core ${CMAKE_DL_LIBS}
    )
    target_include_directories(
        coverage_manifest_flow_test
        PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
    )
    if(HBFSIM_ENABLE_CUDA)
        add_test(
            NAME coverage_manifest_flow
            COMMAND
                coverage_manifest_flow_test
                "$<TARGET_FILE:ptxpass_hbf_plugin>"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/ptx/unsupported_pointer.ptx"
        )
    endif()

    add_test(
        NAME run_with_bpftime
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_run_with_bpftime.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_with_bpftime.sh"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0002-sm120-aot-bundle-load.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0003-libbpf-modern-libc-const.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0004-honor-llvm-aot-cli-option.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0005-cuda13-context-create.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0006-prepatched-bootstrap-once.patch"
    )
endif()

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(LIBBPF QUIET IMPORTED_TARGET libbpf)
endif()
find_program(HBFSIM_CLANG_EXECUTABLE NAMES clang-19 clang)
find_program(HBFSIM_LLVM_OBJDUMP_EXECUTABLE NAMES llvm-objdump-19 llvm-objdump)
if(TARGET PkgConfig::LIBBPF AND HBFSIM_CLANG_EXECUTABLE)
    add_executable(
        hbfsim_bpftime_attach_loader
        src/cuda_runtime/bpftime_attach_loader.cpp
    )
    target_link_libraries(hbfsim_bpftime_attach_loader PRIVATE PkgConfig::LIBBPF)
    set(HBFSIM_COVERAGE_PROBE "${CMAKE_BINARY_DIR}/coverage_probe.bpf.o")
    add_custom_command(
        OUTPUT "${HBFSIM_COVERAGE_PROBE}"
        COMMAND
            "${HBFSIM_CLANG_EXECUTABLE}" -target bpf -O2 -g -c
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/coverage_probe.bpf.c"
            -o "${HBFSIM_COVERAGE_PROBE}"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/coverage_probe.bpf.c"
        VERBATIM
    )
    add_custom_target(hbfsim_coverage_probe ALL DEPENDS "${HBFSIM_COVERAGE_PROBE}")
    set(HBFSIM_MICROBENCH_PROBE "${CMAKE_BINARY_DIR}/microbench_probe.bpf.o")
    add_custom_command(
        OUTPUT "${HBFSIM_MICROBENCH_PROBE}"
        COMMAND
            "${HBFSIM_CLANG_EXECUTABLE}" -target bpf -O2 -g -c
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/microbench_probe.bpf.c"
            -o "${HBFSIM_MICROBENCH_PROBE}"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/microbench_probe.bpf.c"
        VERBATIM
    )
    add_custom_target(
        hbfsim_microbench_probe ALL DEPENDS "${HBFSIM_MICROBENCH_PROBE}"
    )
    set(
        HBFSIM_VMEM_TUNING_PROBE
        "${CMAKE_BINARY_DIR}/vmem_tuning_probe.bpf.o"
    )
    add_custom_command(
        OUTPUT "${HBFSIM_VMEM_TUNING_PROBE}"
        COMMAND
            "${HBFSIM_CLANG_EXECUTABLE}" -target bpf -O2 -g -c
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/vmem_tuning_probe.bpf.c"
            -o "${HBFSIM_VMEM_TUNING_PROBE}"
        DEPENDS
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/vmem_tuning_probe.bpf.c"
        VERBATIM
    )
    add_custom_target(
        hbfsim_vmem_tuning_probe ALL DEPENDS "${HBFSIM_VMEM_TUNING_PROBE}"
    )
    set(HBFSIM_LLAMA_PROBE "${CMAKE_BINARY_DIR}/llama_probe.bpf.o")
    add_custom_command(
        OUTPUT "${HBFSIM_LLAMA_PROBE}"
        COMMAND
            "${HBFSIM_CLANG_EXECUTABLE}" -target bpf -O2 -g -c
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/llama_probe.bpf.c"
            -o "${HBFSIM_LLAMA_PROBE}"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/llama_probe.bpf.c"
        VERBATIM
    )
    add_custom_target(hbfsim_llama_probe ALL DEPENDS "${HBFSIM_LLAMA_PROBE}")
    if(HBFSIM_ENABLE_CUDA)
        set(
            HBFSIM_LLAMA_PROBE_PTX
            "${CMAKE_BINARY_DIR}/hbfsim_llama_probe.ptx"
        )
        add_custom_command(
            OUTPUT "${HBFSIM_LLAMA_PROBE_PTX}"
            COMMAND
                "${CMAKE_CUDA_COMPILER}" --ptx
                "${CMAKE_CURRENT_SOURCE_DIR}/adapters/llama_cpp/hbfsim_llama_probe.cu"
                -std=c++17
                "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
                -o "${HBFSIM_LLAMA_PROBE_PTX}"
            DEPENDS
                "${CMAKE_CURRENT_SOURCE_DIR}/adapters/llama_cpp/hbfsim_llama_probe.cu"
            VERBATIM
        )
        add_custom_target(
            hbfsim_llama_probe_ptx ALL DEPENDS "${HBFSIM_LLAMA_PROBE_PTX}"
        )
        add_library(
            hbfsim_llama_probe_module SHARED
            adapters/llama_cpp/hbfsim_llama_probe.cu
        )
        target_link_libraries(
            hbfsim_llama_probe_module PRIVATE CUDA::cuda_driver
        )
        set_target_properties(
            hbfsim_llama_probe_module PROPERTIES
            OUTPUT_NAME hbfsim_llama_probe
            CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}"
            CUDA_RUNTIME_LIBRARY Shared
            POSITION_INDEPENDENT_CODE ON
        )
    endif()
    set(HBFSIM_VLLM_PROBE "${CMAKE_BINARY_DIR}/vllm_fused_moe_probe.bpf.o")
    add_custom_command(
        OUTPUT "${HBFSIM_VLLM_PROBE}"
        COMMAND
            "${HBFSIM_CLANG_EXECUTABLE}" -target bpf -O2 -g -c
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/vllm_fused_moe_probe.bpf.c"
            -o "${HBFSIM_VLLM_PROBE}"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/vllm_fused_moe_probe.bpf.c"
        VERBATIM
    )
    add_custom_target(hbfsim_vllm_probe ALL DEPENDS "${HBFSIM_VLLM_PROBE}")
    if(BUILD_TESTING AND HBFSIM_LLVM_OBJDUMP_EXECUTABLE)
        add_test(
            NAME bpftime_coverage_probe
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_probe.py"
                "${HBFSIM_COVERAGE_PROBE}"
                "${HBFSIM_LLVM_OBJDUMP_EXECUTABLE}"
        )
        add_test(
            NAME bpftime_vllm_probe
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_vllm_probe.py"
                "${HBFSIM_VLLM_PROBE}"
                "${HBFSIM_LLVM_OBJDUMP_EXECUTABLE}"
        )
    endif()
    if(BUILD_TESTING)
        add_test(
            NAME bpftime_attach_loader_shutdown
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_attach_loader_shutdown.py"
                "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/bpftime_attach_loader.cpp"
        )
    endif()
endif()

find_package(CUDAToolkit 12.8 QUIET)
if(CUDAToolkit_FOUND)
    if(HBFSIM_ENABLE_CUDA)
        if(CMAKE_CUDA_HOST_COMPILER)
            set(HBFSIM_NVCC_HOST_COMPILER "${CMAKE_CUDA_HOST_COMPILER}")
        else()
            unset(HBFSIM_NVCC_HOST_COMPILER CACHE)
            find_program(
                HBFSIM_NVCC_HOST_COMPILER NAMES g++-13 g++-12 g++-14 g++
            )
        endif()
        set(HBFSIM_DEVICE_PTX "${CMAKE_BINARY_DIR}/generated/hbf_device.ptx")
        set(
            HBFSIM_DEVICE_PTX_HEADER
            "${CMAKE_BINARY_DIR}/generated/hbf_device_ptx.hpp"
        )
        add_custom_command(
            OUTPUT "${HBFSIM_DEVICE_PTX}" "${HBFSIM_DEVICE_PTX_HEADER}"
            COMMAND "${CMAKE_COMMAND}" -E make_directory
                    "${CMAKE_BINARY_DIR}/generated"
            COMMAND
                "${CUDAToolkit_NVCC_EXECUTABLE}" --ptx --std=c++20
                "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
                --relocatable-device-code=true --keep-device-functions
                "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
                "-I${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/device"
                "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/device/hbf_device.cu"
                -o "${HBFSIM_DEVICE_PTX}"
            COMMAND
                "${CMAKE_COMMAND}" "-DINPUT=${HBFSIM_DEVICE_PTX}"
                "-DOUTPUT=${HBFSIM_DEVICE_PTX_HEADER}" -P
                "${CMAKE_CURRENT_SOURCE_DIR}/cmake/EmbedDevicePtx.cmake"
            DEPENDS
                "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/device/hbf_device.cu"
                "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/device/hbf_device.cuh"
                "${CMAKE_CURRENT_SOURCE_DIR}/cmake/EmbedDevicePtx.cmake"
            VERBATIM
        )
        add_custom_target(
            hbfsim_device_ptx DEPENDS "${HBFSIM_DEVICE_PTX_HEADER}"
        )
        add_dependencies(hbfsim_core hbfsim_device_ptx)
        target_include_directories(
            hbfsim_core PRIVATE "${CMAKE_BINARY_DIR}/generated"
        )
        target_compile_definitions(
            hbfsim_core
            PRIVATE
                HBFSIM_HAVE_DEVICE_HELPER_PTX=1
                "HBFSIM_DEVICE_PTX_ARCHITECTURE=${HBFSIM_CUDA_ARCHITECTURE}"
        )
        target_compile_definitions(
            ptx_transform_test
            PRIVATE
                HBFSIM_TEST_DEVICE_HELPER_PTX=1
                "HBFSIM_TEST_DEVICE_PTX_ARCHITECTURE=${HBFSIM_CUDA_ARCHITECTURE}"
        )
    endif()

    add_library(
        hbfsim_launch_gate SHARED
        src/cuda_runtime/launch_gate.cpp
        src/cuda_runtime/tensormap_interpose.cpp
    )
    target_link_libraries(hbfsim_launch_gate PRIVATE hbfsim_core ${CMAKE_DL_LIBS})
    target_include_directories(
        hbfsim_launch_gate
        PRIVATE
            "${CUDAToolkit_INCLUDE_DIRS}"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
    )
    set_target_properties(
        hbfsim_launch_gate PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    )
    if(BUILD_TESTING)
        target_compile_definitions(
            hbfsim_launch_gate PRIVATE HBFSIM_ENABLE_TEST_HOOKS=1
        )
        add_library(
            fake_cuda_lookup SHARED
            tests/integration/fake_cuda_lookup.cpp
            tests/integration/fake_cuda_capacity_abi.cpp
        )
        target_include_directories(
            fake_cuda_lookup PRIVATE "${CUDAToolkit_INCLUDE_DIRS}"
        )
        set_target_properties(
            fake_cuda_lookup PROPERTIES OUTPUT_NAME cuda SOVERSION 1
        )
        if(HBFSIM_ENABLE_CUDA)
            add_executable(
                capacity_runtime_test
                tests/integration/capacity_runtime_test.cpp
            )
            target_link_libraries(
                capacity_runtime_test PRIVATE hbfsim_core fake_cuda_lookup
            )
            add_test(NAME capacity_runtime COMMAND capacity_runtime_test)

            add_executable(
                device_range_validation_test
                tests/integration/device_range_validation_test.cpp
            )
            target_link_libraries(
                device_range_validation_test
                PRIVATE hbfsim_core fake_cuda_lookup
            )
            add_test(
                NAME device_range_validation
                COMMAND device_range_validation_test
            )
            add_executable(
                public_cuda_lifecycle_test
                tests/integration/public_cuda_lifecycle_test.cpp
            )
            target_link_libraries(
                public_cuda_lifecycle_test PRIVATE hbfsim_core fake_cuda_lookup
            )
            foreach(
                mode
                IN ITEMS clean timing-unregister daemon-death daemon-after-begin
                         unregister-failure register-destroy-race
            )
                add_test(
                    NAME public_cuda_lifecycle_${mode}
                    COMMAND
                        "${CMAKE_COMMAND}" -E env
                        "LD_PRELOAD=$<TARGET_FILE:hbfsim_launch_gate>"
                        "HBFSIM_DAEMON_PATH=$<TARGET_FILE:hbfsimd>"
                        "$<TARGET_FILE:public_cuda_lifecycle_test>"
                        "${mode}"
                        "${CMAKE_CURRENT_SOURCE_DIR}/configs/profiles/nominal.json"
                )
            endforeach()
            if(HBFSIM_ENABLE_MQSIM)
                set(public_cuda_capacity_modes
                    capacity capacity-destroy-failure
                    capacity-destroy-cleanup-failure)
            else()
                set(public_cuda_capacity_modes capacity-disabled)
            endif()
            foreach(public_cuda_capacity_mode IN LISTS public_cuda_capacity_modes)
                add_test(
                    NAME public_cuda_lifecycle_${public_cuda_capacity_mode}
                    COMMAND
                        "${CMAKE_COMMAND}" -E env
                        "LD_PRELOAD=$<TARGET_FILE:hbfsim_launch_gate>"
                        "HBFSIM_DAEMON_PATH=$<TARGET_FILE:hbfsimd>"
                        "$<TARGET_FILE:public_cuda_lifecycle_test>"
                        "${public_cuda_capacity_mode}"
                        "${CMAKE_CURRENT_SOURCE_DIR}/configs/profiles/nominal.json"
                )
            endforeach()
        endif()
        find_program(HBFSIM_NM_EXECUTABLE NAMES llvm-nm nm REQUIRED)
        add_test(
            NAME launch_gate_symbols
            COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_launch_gate_symbols.py"
                    "$<TARGET_FILE:hbfsim_launch_gate>"
                    "${HBFSIM_NM_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/launch_gate.cpp"
                    "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/context.cpp"
                    "${CUDAToolkit_VERSION}"
        )
        add_test(
            NAME cuda_lookup_interposition
            COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_cuda_lookup_interposition.py"
                    "$<TARGET_FILE:hbfsim_launch_gate>"
                    "$<TARGET_FILE:fake_cuda_lookup>"
                    "${CUDAToolkit_VERSION}"
        )
        add_test(
            NAME tensormap_interposition
            COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_tensormap_interposition.py"
                    "$<TARGET_FILE:hbfsim_launch_gate>"
                    "$<TARGET_FILE:fake_cuda_lookup>"
        )
        if(HBFSIM_ENABLE_CUDA)
            add_test(
                NAME cuda_module_association
                COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                        "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_cuda_module_association.py"
                        "$<TARGET_FILE:hbfsim_launch_gate>"
                        "$<TARGET_FILE:fake_cuda_lookup>"
                        "$<TARGET_FILE:ptxpass_hbf_plugin>"
                        "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/ptx/supported.ptx"
                        "${CUDAToolkit_VERSION}"
                        "${HBFSIM_CUDA_PTX_TARGET}"
            )
        endif()
        add_test(
            NAME timing_gate_binding
            COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_timing_gate_binding.py"
                    "$<TARGET_FILE:hbfsim_launch_gate>"
                    "$<TARGET_FILE:fake_cuda_lookup>"
        )
        if(HBFSIM_ENABLE_CUDA)
            add_test(
                NAME live_sm120_environment
                COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                        "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_live_sm120_environment.py"
                        "$<TARGET_FILE:hbfsim_launch_gate>"
            )
            set_tests_properties(
                live_sm120_environment PROPERTIES SKIP_RETURN_CODE 77
            )
        endif()
    endif()
elseif(HBFSIM_ENABLE_CUDA)
    message(FATAL_ERROR "HBFSIM_ENABLE_CUDA requires CUDA Toolkit 12.8 or newer")
endif()

if(HBFSIM_ENABLE_CUDA)
    target_compile_definitions(hbfsim_core PRIVATE HBFSIM_ENABLE_CUDA_RUNTIME=1)
    target_link_libraries(hbfsim_core PRIVATE CUDA::cudart CUDA::cuda_driver)
    enable_language(CUDA)

    add_subdirectory(benchmarks/cuda)

    if(BUILD_TESTING AND TARGET sm120_future_bench)
        add_test(
            NAME sm120_future_live
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_sm120_future_live.py"
                --build-dir "${CMAKE_BINARY_DIR}"
        )
        set_tests_properties(
            sm120_future_live PROPERTIES
            WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
            SKIP_RETURN_CODE 77
            TIMEOUT 300
            LABELS "live;sm120"
        )
    endif()

    set(
        HBFSIM_THERMAL_TIMING_PTX
        "${CMAKE_CURRENT_BINARY_DIR}/thermal_timing_live.ptx"
    )
    add_custom_command(
        OUTPUT "${HBFSIM_THERMAL_TIMING_PTX}"
        COMMAND
            "${CMAKE_CUDA_COMPILER}" --ptx --std=c++20
            "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
            "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/thermal_timing_live.cu"
            -o "${HBFSIM_THERMAL_TIMING_PTX}"
        DEPENDS
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/thermal_timing_live.cu"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/gpu/sm120_future_correctness.cu"
        VERBATIM
    )
    add_custom_target(
        thermal_timing_live_ptx DEPENDS "${HBFSIM_THERMAL_TIMING_PTX}"
    )
    add_executable(
        thermal_timing_live EXCLUDE_FROM_ALL
        benchmarks/cuda/sm120_future_bench.cu
    )
    target_link_libraries(
        thermal_timing_live
        PRIVATE hbfsim_core ptxpass_hbf_plugin CUDA::cudart CUDA::cuda_driver
    )
    target_include_directories(
        thermal_timing_live PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
    )
    target_compile_definitions(
        thermal_timing_live PRIVATE
        "HBFSIM_SM120_FUTURE_PTX_PATH=\"${HBFSIM_THERMAL_TIMING_PTX}\""
    )
    set_target_properties(
        thermal_timing_live PROPERTIES
        CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
        CUDA_RUNTIME_LIBRARY Shared
        ENABLE_EXPORTS ON
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/tests/gpu"
    )
    add_dependencies(thermal_timing_live thermal_timing_live_ptx hbfsimd)
    if(TARGET hbfsim_launch_gate)
        add_dependencies(thermal_timing_live hbfsim_launch_gate)
    endif()
    if(BUILD_TESTING)
        add_test(
            NAME thermal_timing_live
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_thermal_timing_live.py"
                --binary "$<TARGET_FILE:thermal_timing_live>"
                --gate "$<TARGET_FILE:hbfsim_launch_gate>"
                --daemon "$<TARGET_FILE:hbfsimd>"
                --profile
                "${CMAKE_CURRENT_SOURCE_DIR}/configs/profiles/thermal-validation.json"
        )
        set_tests_properties(
            thermal_timing_live PROPERTIES
            WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
            SKIP_RETURN_CODE 77
            TIMEOUT 600
            LABELS "live;sm120;thermal"
        )
    endif()

    add_executable(unsupported_kernel tests/gpu/unsupported_kernel.cu)
    target_link_libraries(
        unsupported_kernel PRIVATE hbfsim_core CUDA::cudart ${CMAKE_DL_LIBS}
    )
    set_target_properties(
        unsupported_kernel PROPERTIES
        CUDA_ARCHITECTURES
            "${HBFSIM_CUDA_ARCHITECTURE}-real;${HBFSIM_CUDA_ARCHITECTURE}-virtual"
        CUDA_RUNTIME_LIBRARY Shared
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/tests/gpu"
    )

    add_library(
        hbfsim_vllm_extension SHARED adapters/vllm/hbfsim_extension.cpp
    )
    target_include_directories(
        hbfsim_vllm_extension PRIVATE adapters/vllm
    )
    target_link_libraries(hbfsim_vllm_extension PRIVATE hbfsim_core)
    set_target_properties(
        hbfsim_vllm_extension PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    )

    if(BUILD_TESTING AND TARGET fake_cuda_lookup AND TARGET hbfsim_launch_gate)
        add_executable(
            vllm_extension_test tests/integration/vllm_extension_test.cpp
        )
    target_link_libraries(
            vllm_extension_test
            PRIVATE hbfsim_vllm_extension fake_cuda_lookup
        )
        target_include_directories(
            vllm_extension_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include"
        )
        add_dependencies(vllm_extension_test hbfsimd)
        add_test(
            NAME vllm_extension
            COMMAND
                "${CMAKE_COMMAND}" -E env
                "LD_PRELOAD=$<TARGET_FILE:hbfsim_launch_gate>"
                "$<TARGET_FILE:vllm_extension_test>"
                "$<TARGET_FILE:hbfsimd>"
                "${CMAKE_CURRENT_SOURCE_DIR}/configs/profiles/nominal.json"
        )
    endif()

    add_executable(
        capacity_runtime_live_test EXCLUDE_FROM_ALL
        tests/gpu/capacity_runtime_live_test.cu
    )
    target_link_libraries(
        capacity_runtime_live_test
        PRIVATE hbfsim_core CUDA::cudart CUDA::cuda_driver
    )
    set_target_properties(
        capacity_runtime_live_test PROPERTIES
        CUDA_ARCHITECTURES
            "${HBFSIM_CUDA_ARCHITECTURE}-real;${HBFSIM_CUDA_ARCHITECTURE}-virtual"
        CUDA_RUNTIME_LIBRARY Shared
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/tests/gpu"
    )
    if(TARGET hbfsim_bpftime_attach_loader AND TARGET hbfsim_coverage_probe)
        add_dependencies(
            unsupported_kernel hbfsim_bpftime_attach_loader hbfsim_coverage_probe
        )
    endif()
    if(TARGET hbfsim_launch_gate)
        add_dependencies(unsupported_kernel hbfsim_launch_gate ptxpass_hbf_plugin)
    endif()
    if(BUILD_TESTING)
        find_program(
            HBFSIM_PTXAS_EXECUTABLE NAMES ptxas
            HINTS "/usr/local/cuda-12.8/bin" "${CUDAToolkit_BIN_DIR}"
            REQUIRED
        )
        add_test(
            NAME ptxpass_plugin
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_ptxpass_plugin.py"
                "$<TARGET_FILE:ptxpass_hbf_plugin>"
                "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf/plugin.cpp"
                "${HBFSIM_PTXAS_EXECUTABLE}"
                "${HBFSIM_CUDA_PTX_TARGET}"
        )
        set_tests_properties(
            ptxpass_plugin PROPERTIES
            WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        )
        find_program(
            HBFSIM_TMA_PTXAS_EXECUTABLE NAMES ptxas
            HINTS "/usr/local/cuda-13.0/bin" "/usr/local/cuda-13/bin"
            NO_DEFAULT_PATH
        )
        if(HBFSIM_TMA_PTXAS_EXECUTABLE)
            add_test(
                NAME tma_ptxpass
                COMMAND
                    "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_tma_ptxpass.py"
                    "$<TARGET_FILE:ptxpass_hbf>"
                    "${HBFSIM_TMA_PTXAS_EXECUTABLE}"
                    "${HBFSIM_CUDA_PTX_TARGET}"
            )
            set_tests_properties(
                tma_ptxpass PROPERTIES
                WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
            )
        endif()
        add_test(
            NAME unsupported_kernel_ptx
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_cuda_ptx.py"
                "$<TARGET_FILE:unsupported_kernel>"
                "${CUDAToolkit_BIN_DIR}/cuobjdump"
                "${HBFSIM_CUDA_PTX_TARGET}"
        )
        add_test(
            NAME device_helper_ptx
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_device_helper_ptx.py"
                "$<TARGET_FILE:ptxpass_hbf_plugin>"
                "${HBFSIM_DEVICE_PTX}"
                "${HBFSIM_PTXAS_EXECUTABLE}"
                "${HBFSIM_CUDA_PTX_TARGET}"
        )
        set_tests_properties(
            device_helper_ptx PROPERTIES
            WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        )
    endif()
endif()

if(HBFSIM_ENABLE_MQSIM)
    include(cmake/MQSimPatchedBuild.cmake)
    hbfsim_add_patched_mqsim(mqsim_hbf)
    set_target_properties(mqsim_hbf PROPERTIES POSITION_INDEPENDENT_CODE ON)

    target_sources(hbfsim_core PRIVATE src/mqsim_adapter/mqsim_online.cpp)
    target_link_libraries(hbfsim_core PRIVATE mqsim_hbf)
    target_compile_definitions(hbfsimd PRIVATE HBFSIM_ENABLE_MQSIM_RUNTIME=1)
    target_compile_definitions(
        daemon_protocol_test PRIVATE HBFSIM_TEST_MQSIM_ENABLED=1
    )

    add_executable(mqsim_online_test tests/integration/mqsim_online_test.cpp)
    target_link_libraries(mqsim_online_test PRIVATE hbfsim_core)
    add_test(NAME mqsim_online COMMAND mqsim_online_test)
    set_tests_properties(
        mqsim_online PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    )

    add_executable(
        hbf_mqsim_bench benchmarks/mqsim/hbf_mqsim_bench.cpp
    )
    target_link_libraries(hbf_mqsim_bench PRIVATE hbfsim_core)
    target_include_directories(
        hbf_mqsim_bench
        PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
    )

    if(BUILD_TESTING)
        find_program(HBFSIM_PYTHON3_EXECUTABLE python3 REQUIRED)
        add_test(
            NAME mqsim_benchmark
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_mqsim_benchmark.py"
                "$<TARGET_FILE:hbf_mqsim_bench>"
        )
        set_tests_properties(
            mqsim_benchmark
            PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        )
    endif()
endif()
