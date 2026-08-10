cmake_minimum_required(VERSION 3.25)

project(HBFSim VERSION 0.1.0 LANGUAGES CXX)

option(HBFSIM_ENABLE_CUDA "Build CUDA instrumentation and runtime components" ON)
option(HBFSIM_ENABLE_MQSIM "Build the MQSim-backed host service" ON)
option(HBFSIM_ENABLE_LLM_TESTS "Enable llama.cpp and vLLM integration tests" OFF)

include(CTest)
include(cmake/Dependencies.cmake)
find_package(OpenSSL REQUIRED COMPONENTS Crypto)

add_library(
    hbfsim_core STATIC
    src/api.cpp
    src/cuda_runtime/context.cpp
    src/cuda_runtime/coverage.cpp
    src/cuda_runtime/hbm_cache.cpp
    src/cuda_runtime/module_identity.cpp
    src/cuda_runtime/range_table.cpp
    src/cuda_runtime/timing_binding.cpp
    src/cuda_runtime/vmm.cpp
    src/profile/profile.cpp
    src/host_service/request_dispatcher.cpp
    src/host_service/backing_store.cpp
    src/host_service/capacity_backing_router.cpp
    src/host_service/capacity_page_service.cpp
    src/host_service/capacity_worker.cpp
    src/protocol/page_directory.cpp
    src/ptxpass_hbf/ptx_memory_op.cpp
    src/ptxpass_hbf/transform.cpp
    src/reporting/coverage_writer.cpp
    src/reporting/durable_append.cpp
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
target_link_libraries(hbfsim_core PUBLIC ${CMAKE_DL_LIBS})
set_target_properties(hbfsim_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(
    hbfsim_core
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime/third_party"
)

add_executable(hbfsim_cpu_tests tests/cpu/build_smoke.cpp)
target_link_libraries(hbfsim_cpu_tests PRIVATE hbfsim_core)

add_test(NAME build_smoke COMMAND hbfsim_cpu_tests)

add_executable(hbfsim_profile_tests tests/cpu/profile_test.cpp)
target_link_libraries(hbfsim_profile_tests PRIVATE hbfsim_core)

add_test(NAME profile COMMAND hbfsim_profile_tests)
set_tests_properties(profile PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")

add_executable(hbfsim_protocol_tests tests/cpu/protocol_layout_test.cpp)
target_link_libraries(hbfsim_protocol_tests PRIVATE hbfsim_core)
add_test(NAME protocol COMMAND hbfsim_protocol_tests)

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
add_dependencies(context_lifecycle_test hbfsim_marker_preload)
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

add_executable(ptx_transform_test tests/cpu/ptx_transform_test.cpp)
target_link_libraries(ptx_transform_test PRIVATE hbfsim_core)
target_include_directories(
    ptx_transform_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf"
)
add_test(NAME ptx_transform COMMAND ptx_transform_test)
set_tests_properties(
    ptx_transform PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
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
endif()

if(BUILD_TESTING AND HBFSIM_ENABLE_CUDA)
    add_test(
        NAME ptxpass_plugin
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_ptxpass_plugin.py"
            "$<TARGET_FILE:ptxpass_hbf_plugin>"
            "${CMAKE_CURRENT_SOURCE_DIR}/src/ptxpass_hbf/plugin.cpp"
    )
    set_tests_properties(
        ptxpass_plugin PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    )
endif()

if(BUILD_TESTING)
    add_test(
        NAME bpftime_patch
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_patch.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
            "${CMAKE_CURRENT_SOURCE_DIR}/third_party/bpftime"
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
    add_test(
        NAME coverage_manifest_flow
        COMMAND
            coverage_manifest_flow_test
            "$<TARGET_FILE:ptxpass_hbf_plugin>"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/ptx/unsupported_pointer.ptx"
    )

    add_test(
        NAME run_with_bpftime
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_run_with_bpftime.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_with_bpftime.sh"
            "${CMAKE_CURRENT_SOURCE_DIR}/patches/bpftime/0001-exact-module-load-provenance.patch"
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
    if(BUILD_TESTING AND HBFSIM_LLVM_OBJDUMP_EXECUTABLE)
        add_test(
            NAME bpftime_coverage_probe
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_bpftime_probe.py"
                "${HBFSIM_COVERAGE_PROBE}"
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
                --gpu-architecture=compute_120
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
            hbfsim_core PRIVATE HBFSIM_HAVE_DEVICE_HELPER_PTX=1
        )
        target_compile_definitions(
            ptx_transform_test PRIVATE HBFSIM_TEST_DEVICE_HELPER_PTX=1
        )
    endif()

    add_library(hbfsim_launch_gate SHARED src/cuda_runtime/launch_gate.cpp)
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
            fake_cuda_lookup SHARED tests/integration/fake_cuda_lookup.cpp
        )
        set_target_properties(
            fake_cuda_lookup PROPERTIES OUTPUT_NAME cuda SOVERSION 1
        )
        if(HBFSIM_ENABLE_CUDA)
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
                IN ITEMS clean daemon-death daemon-after-begin
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
            )
        endif()
        add_test(
            NAME timing_gate_binding
            COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_timing_gate_binding.py"
                    "$<TARGET_FILE:hbfsim_launch_gate>"
                    "$<TARGET_FILE:fake_cuda_lookup>"
        )
    endif()
elseif(HBFSIM_ENABLE_CUDA)
    message(FATAL_ERROR "HBFSIM_ENABLE_CUDA requires CUDA Toolkit 12.8 or newer")
endif()

if(HBFSIM_ENABLE_CUDA)
    target_compile_definitions(hbfsim_core PRIVATE HBFSIM_ENABLE_CUDA_RUNTIME=1)
    target_link_libraries(hbfsim_core PRIVATE CUDA::cudart CUDA::cuda_driver)
    enable_language(CUDA)

    add_executable(unsupported_kernel tests/gpu/unsupported_kernel.cu)
    target_link_libraries(
        unsupported_kernel PRIVATE hbfsim_core CUDA::cudart ${CMAKE_DL_LIBS}
    )
    set_target_properties(
        unsupported_kernel PROPERTIES
        CUDA_ARCHITECTURES "120-real;120-virtual"
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
            NAME unsupported_kernel_ptx
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_cuda_ptx.py"
                "$<TARGET_FILE:unsupported_kernel>"
                "${CUDAToolkit_BIN_DIR}/cuobjdump"
        )
        add_test(
            NAME device_helper_ptx
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_device_helper_ptx.py"
                "$<TARGET_FILE:ptxpass_hbf_plugin>"
                "${HBFSIM_DEVICE_PTX}"
                "${HBFSIM_PTXAS_EXECUTABLE}"
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
