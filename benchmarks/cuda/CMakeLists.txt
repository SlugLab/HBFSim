add_executable(hbf_microbench hbf_microbench.cu)
target_link_libraries(
    hbf_microbench PRIVATE hbfsim_core CUDA::cudart CUDA::cuda_driver
)
target_include_directories(
    hbf_microbench PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
)
set_target_properties(
    hbf_microbench PROPERTIES
    CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
    CUDA_RUNTIME_LIBRARY Shared
    ENABLE_EXPORTS ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda"
)
set(HBFSIM_MICROBENCH_PTX "${CMAKE_CURRENT_BINARY_DIR}/hbf_microbench_kernel.ptx")
add_custom_command(
    OUTPUT "${HBFSIM_MICROBENCH_PTX}"
    COMMAND
        "${CMAKE_CUDA_COMPILER}" --ptx --std=c++20
        "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
        "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
        "${CMAKE_CURRENT_SOURCE_DIR}/hbf_microbench_kernel.cu"
        -o "${HBFSIM_MICROBENCH_PTX}"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/hbf_microbench_kernel.cu"
    VERBATIM
)
add_custom_target(hbf_microbench_ptx DEPENDS "${HBFSIM_MICROBENCH_PTX}")
add_dependencies(hbf_microbench hbf_microbench_ptx)
target_compile_definitions(
    hbf_microbench PRIVATE
    "HBFSIM_MICROBENCH_PTX_PATH=\"${HBFSIM_MICROBENCH_PTX}\""
)

add_executable(hbf_vmem_tuning_bench hbf_vmem_tuning_bench.cu)
target_link_libraries(
    hbf_vmem_tuning_bench PRIVATE hbfsim_core CUDA::cudart CUDA::cuda_driver
)
target_include_directories(
    hbf_vmem_tuning_bench PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
)
set_target_properties(
    hbf_vmem_tuning_bench PROPERTIES
    CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
    CUDA_RUNTIME_LIBRARY Shared
    ENABLE_EXPORTS ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda"
)
set(
    HBFSIM_VMEM_TUNING_PTX
    "${CMAKE_CURRENT_BINARY_DIR}/hbf_vmem_tuning_bench.ptx"
)
add_custom_command(
    OUTPUT "${HBFSIM_VMEM_TUNING_PTX}"
    COMMAND
        "${CMAKE_CUDA_COMPILER}" --ptx --std=c++20
        "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
        "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
        -DHBFSIM_VMEM_KERNEL_ONLY=1
        "${CMAKE_CURRENT_SOURCE_DIR}/hbf_vmem_tuning_bench.cu"
        -o "${HBFSIM_VMEM_TUNING_PTX}"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/hbf_vmem_tuning_bench.cu"
    VERBATIM
)
add_custom_target(
    hbf_vmem_tuning_ptx DEPENDS "${HBFSIM_VMEM_TUNING_PTX}"
)
add_dependencies(hbf_vmem_tuning_bench hbf_vmem_tuning_ptx)
target_compile_definitions(
    hbf_vmem_tuning_bench PRIVATE
    "HBFSIM_VMEM_TUNING_PTX_PATH=\"${HBFSIM_VMEM_TUNING_PTX}\""
)

# Experiment only: no new executable in a default production build.
option(HBFSIM_BUILD_KNOWN_DELAY_BENCHMARK "Build explicit EQ1-A delay experiment" OFF)
if(HBFSIM_BUILD_KNOWN_DELAY_BENCHMARK)
    add_executable(hbf_dependent_delay hbf_dependent_delay.cu)
    target_link_libraries(hbf_dependent_delay PRIVATE hbfsim_core CUDA::cudart CUDA::cuda_driver ${CMAKE_DL_LIBS})
    target_include_directories(hbf_dependent_delay PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/bpftime/third_party")
    set_target_properties(hbf_dependent_delay PROPERTIES
        CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
        CUDA_RUNTIME_LIBRARY Shared ENABLE_EXPORTS ON
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda")
    set(HBFSIM_DELAY_PTX "${CMAKE_CURRENT_BINARY_DIR}/hbf_dependent_delay.ptx")
    add_custom_command(OUTPUT "${HBFSIM_DELAY_PTX}"
        COMMAND "${CMAKE_CUDA_COMPILER}" --ptx --std=c++20 -O3 -lineinfo
            "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
            "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
            -DHBFSIM_DELAY_KERNEL_ONLY=1
            "${CMAKE_CURRENT_SOURCE_DIR}/hbf_dependent_delay.cu" -o "${HBFSIM_DELAY_PTX}"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/hbf_dependent_delay.cu" VERBATIM)
    add_custom_target(hbf_dependent_delay_ptx DEPENDS "${HBFSIM_DELAY_PTX}")
    add_dependencies(hbf_dependent_delay hbf_dependent_delay_ptx hbfsim_device_ptx ptxpass_hbf_plugin hbfsim_launch_gate)
    if(BUILD_TESTING)
        add_executable(hbfsim_eval_chain_layout_test
            "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/cpu/eval_chain_layout_test.cpp")
        target_compile_definitions(hbfsim_eval_chain_layout_test PRIVATE
            HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC=1)
        target_compile_features(hbfsim_eval_chain_layout_test PRIVATE cxx_std_20)
        add_test(NAME eval_chain_layout COMMAND hbfsim_eval_chain_layout_test)
        set_tests_properties(eval_chain_layout PROPERTIES TIMEOUT 30)
    endif()
endif()

# Opt-in diagnostic only; do not add to a default production build.
option(HBFSIM_BUILD_C6_GPU_DIAGNOSTIC
       "Build the bounded C6 future correctness diagnostic" OFF)
if(HBFSIM_BUILD_C6_GPU_DIAGNOSTIC)
    add_executable(c6_future_correctness
        c6_future_correctness_candidate.cu)
    target_link_libraries(c6_future_correctness PRIVATE
        hbfsim_core CUDA::cudart CUDA::cuda_driver ${CMAKE_DL_LIBS})
    target_include_directories(c6_future_correctness PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/bpftime/third_party")
    set_target_properties(c6_future_correctness PROPERTIES
        CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
        CUDA_RUNTIME_LIBRARY Shared
        ENABLE_EXPORTS ON
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda")
    add_dependencies(c6_future_correctness
        hbfsim_device_ptx ptxpass_hbf_plugin hbfsim_launch_gate)
endif()
