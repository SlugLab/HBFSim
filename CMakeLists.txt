cmake_minimum_required(VERSION 3.25)

project(HBFSim VERSION 0.1.0 LANGUAGES CXX)

option(HBFSIM_ENABLE_CUDA "Build CUDA instrumentation and runtime components" ON)
option(HBFSIM_ENABLE_MQSIM "Build the MQSim-backed host service" ON)
option(HBFSIM_ENABLE_LLM_TESTS "Enable llama.cpp and vLLM integration tests" OFF)

include(CTest)
include(cmake/Dependencies.cmake)

add_library(
    hbfsim_core STATIC
    src/api.cpp
    src/cuda_runtime/coverage.cpp
    src/profile/profile.cpp
    src/protocol/page_directory.cpp
    src/ptxpass_hbf/ptx_memory_op.cpp
    src/ptxpass_hbf/transform.cpp
    src/reporting/coverage_writer.cpp
)
target_include_directories(
    hbfsim_core
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
)
target_compile_features(hbfsim_core PUBLIC cxx_std_20)
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

add_executable(coverage_gate_test tests/cpu/coverage_gate_test.cpp)
target_link_libraries(coverage_gate_test PRIVATE hbfsim_core)
add_test(NAME coverage_gate COMMAND coverage_gate_test)

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
target_link_libraries(ptxpass_hbf_plugin PRIVATE hbfsim_core)
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
        NAME ptxpass_plugin
        COMMAND
            "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_ptxpass_plugin.py"
            "$<TARGET_FILE:ptxpass_hbf_plugin>"
    )
    set_tests_properties(
        ptxpass_plugin PROPERTIES WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
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
        find_program(HBFSIM_NM_EXECUTABLE NAMES llvm-nm nm REQUIRED)
        add_test(
            NAME launch_gate_symbols
            COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_launch_gate_symbols.py"
                    "$<TARGET_FILE:hbfsim_launch_gate>"
                    "${HBFSIM_NM_EXECUTABLE}"
                    "${CMAKE_CURRENT_SOURCE_DIR}/src/cuda_runtime/launch_gate.cpp"
                    "${CUDAToolkit_VERSION}"
        )
    endif()
elseif(HBFSIM_ENABLE_CUDA)
    message(FATAL_ERROR "HBFSIM_ENABLE_CUDA requires CUDA Toolkit 12.8 or newer")
endif()

if(HBFSIM_ENABLE_CUDA)
    enable_language(CUDA)

    add_executable(unsupported_kernel tests/gpu/unsupported_kernel.cu)
    target_link_libraries(unsupported_kernel PRIVATE CUDA::cudart ${CMAKE_DL_LIBS})
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
        add_test(
            NAME unsupported_kernel_ptx
            COMMAND
                "${HBFSIM_PYTHON3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_cuda_ptx.py"
                "$<TARGET_FILE:unsupported_kernel>"
                "${CUDAToolkit_BIN_DIR}/cuobjdump"
        )
    endif()
endif()

if(HBFSIM_ENABLE_MQSIM)
    include(cmake/MQSimPatchedBuild.cmake)
    hbfsim_add_patched_mqsim(mqsim_hbf)

    target_sources(hbfsim_core PRIVATE src/mqsim_adapter/mqsim_online.cpp)
    target_link_libraries(hbfsim_core PRIVATE mqsim_hbf)

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
