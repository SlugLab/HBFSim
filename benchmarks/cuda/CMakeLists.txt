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

set(
    HBFSIM_SM120_FUTURE_PTX
    "${CMAKE_CURRENT_BINARY_DIR}/sm120_future_correctness.ptx"
)
add_custom_command(
    OUTPUT "${HBFSIM_SM120_FUTURE_PTX}"
    COMMAND
        "${CMAKE_CUDA_COMPILER}" --ptx --std=c++20
        "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
        "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/gpu/sm120_future_correctness.cu"
        -o "${HBFSIM_SM120_FUTURE_PTX}"
    DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/gpu/sm120_future_correctness.cu"
    VERBATIM
)
add_custom_target(
    sm120_future_correctness DEPENDS "${HBFSIM_SM120_FUTURE_PTX}"
)
add_executable(sm120_future_bench sm120_future_bench.cu)
target_link_libraries(
    sm120_future_bench
    PRIVATE hbfsim_core ptxpass_hbf_plugin CUDA::cudart CUDA::cuda_driver
)
target_include_directories(
    sm120_future_bench PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/bpftime/third_party"
)
set_target_properties(
    sm120_future_bench PROPERTIES
    CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
    CUDA_RUNTIME_LIBRARY Shared
    ENABLE_EXPORTS ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda"
)
add_dependencies(sm120_future_bench sm120_future_correctness)
target_compile_definitions(
    sm120_future_bench PRIVATE
    "HBFSIM_SM120_FUTURE_PTX_PATH=\"${HBFSIM_SM120_FUTURE_PTX}\""
)

set(
    HBFSIM_SM120_TMA_PTX
    "${CMAKE_CURRENT_BINARY_DIR}/sm120_tma_correctness.ptx"
)
add_custom_command(
    OUTPUT "${HBFSIM_SM120_TMA_PTX}"
    COMMAND
        "${CMAKE_CUDA_COMPILER}" --ptx --std=c++20
        "--gpu-architecture=compute_${HBFSIM_CUDA_ARCHITECTURE}"
        "--compiler-bindir=${HBFSIM_NVCC_HOST_COMPILER}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/gpu/sm120_tma_correctness.cu"
        -o "${HBFSIM_SM120_TMA_PTX}"
    DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/../../tests/gpu/sm120_tma_correctness.cu"
    VERBATIM
)
add_custom_target(sm120_tma_correctness DEPENDS "${HBFSIM_SM120_TMA_PTX}")
add_executable(sm120_tma_bench sm120_tma_bench.cu)
target_link_libraries(
    sm120_tma_bench
    PRIVATE hbfsim_core ptxpass_hbf_plugin CUDA::cudart CUDA::cuda_driver
)
target_include_directories(
    sm120_tma_bench PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/bpftime/third_party"
)
set_target_properties(
    sm120_tma_bench PROPERTIES
    CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}-virtual"
    CUDA_RUNTIME_LIBRARY Shared
    ENABLE_EXPORTS ON
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda"
)
add_dependencies(sm120_tma_bench sm120_tma_correctness)
target_compile_definitions(
    sm120_tma_bench PRIVATE
    "HBFSIM_SM120_TMA_PTX_PATH=\"${HBFSIM_SM120_TMA_PTX}\""
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

add_executable(sm120_calibration sm120_calibration.cu)
target_link_libraries(
    sm120_calibration PRIVATE hbfsim_core CUDA::cudart CUDA::cuda_driver
)
target_include_directories(
    sm120_calibration PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/bpftime/third_party"
)
set_target_properties(
    sm120_calibration PROPERTIES
    CUDA_ARCHITECTURES "${HBFSIM_CUDA_ARCHITECTURE}"
    CUDA_RUNTIME_LIBRARY Shared
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/benchmarks/cuda"
)
