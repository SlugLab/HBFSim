cmake_minimum_required(VERSION 3.25)

project(HBFSim VERSION 0.1.0 LANGUAGES CXX)

option(HBFSIM_ENABLE_CUDA "Build CUDA instrumentation and runtime components" ON)
option(HBFSIM_ENABLE_MQSIM "Build the MQSim-backed host service" ON)
option(HBFSIM_ENABLE_LLM_TESTS "Enable llama.cpp and vLLM integration tests" OFF)

include(CTest)
include(cmake/Dependencies.cmake)

add_library(hbfsim_core STATIC src/api.cpp)
target_include_directories(
    hbfsim_core
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
)
target_compile_features(hbfsim_core PUBLIC cxx_std_20)

add_executable(hbfsim_cpu_tests tests/cpu/build_smoke.cpp)
target_link_libraries(hbfsim_cpu_tests PRIVATE hbfsim_core)

add_test(NAME build_smoke COMMAND hbfsim_cpu_tests)
