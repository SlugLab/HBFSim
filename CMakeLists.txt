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
    src/profile/profile.cpp
    src/protocol/page_directory.cpp
)
target_include_directories(
    hbfsim_core
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
)
target_compile_features(hbfsim_core PUBLIC cxx_std_20)
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
endif()
