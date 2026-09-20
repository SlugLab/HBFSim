cmake_minimum_required(VERSION 3.20)
project(eq3_isolated_mqsim_maintenance LANGUAGES CXX)

find_package(Git REQUIRED)
set(HBFSIM_SOURCE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." CACHE PATH
    "Read-only HBFSim source containing the verified MQSim snapshot and patches 0001-0003")
get_filename_component(HBFSIM_SOURCE_ROOT "${HBFSIM_SOURCE_ROOT}" ABSOLUTE)
set(MQSIM_UPSTREAM "${HBFSIM_SOURCE_ROOT}/third_party/mqsim")
set(MQSIM_EXPECTED_REVISION "51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16")
execute_process(COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
    WORKING_DIRECTORY "${MQSIM_UPSTREAM}" OUTPUT_VARIABLE MQSIM_ACTUAL_REVISION
    OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE revision_result)
if(NOT revision_result EQUAL 0 OR NOT MQSIM_ACTUAL_REVISION STREQUAL MQSIM_EXPECTED_REVISION)
  message(FATAL_ERROR "isolated maintenance requires MQSim ${MQSIM_EXPECTED_REVISION}; found ${MQSIM_ACTUAL_REVISION}")
endif()
set(MQSIM_EXPERIMENT_SOURCE "${CMAKE_CURRENT_BINARY_DIR}/source/mqsim_eq3_maint")
set(MQSIM_PATCHES
    "${HBFSIM_SOURCE_ROOT}/patches/mqsim/0001-online-hbf-api.patch"
    "${HBFSIM_SOURCE_ROOT}/patches/mqsim/0002-qlc-support.patch"
    "${HBFSIM_SOURCE_ROOT}/patches/mqsim/0003-hbf-command-observer.patch"
    "${CMAKE_CURRENT_LIST_DIR}/patches/0004-eq3-maintenance.patch")

file(REMOVE_RECURSE "${MQSIM_EXPERIMENT_SOURCE}")
file(MAKE_DIRECTORY "${MQSIM_EXPERIMENT_SOURCE}")
file(COPY "${MQSIM_UPSTREAM}/" DESTINATION "${MQSIM_EXPERIMENT_SOURCE}"
     PATTERN ".git" EXCLUDE PATTERN "build" EXCLUDE PATTERN "MQSim" EXCLUDE)
foreach(patch IN LISTS MQSIM_PATCHES)
  execute_process(COMMAND "${CMAKE_COMMAND}" -E env
      "GIT_CEILING_DIRECTORIES=${CMAKE_CURRENT_BINARY_DIR}/source"
      "${GIT_EXECUTABLE}" apply --check --unsafe-paths "${patch}"
      WORKING_DIRECTORY "${MQSIM_EXPERIMENT_SOURCE}"
      RESULT_VARIABLE check_result ERROR_VARIABLE check_error)
  if(NOT check_result EQUAL 0)
    message(FATAL_ERROR "isolated MQSim patch check failed for ${patch}: ${check_error}")
  endif()
  execute_process(COMMAND "${CMAKE_COMMAND}" -E env
      "GIT_CEILING_DIRECTORIES=${CMAKE_CURRENT_BINARY_DIR}/source"
      "${GIT_EXECUTABLE}" apply --unsafe-paths "${patch}"
      WORKING_DIRECTORY "${MQSIM_EXPERIMENT_SOURCE}"
      RESULT_VARIABLE apply_result ERROR_VARIABLE apply_error)
  if(NOT apply_result EQUAL 0)
    message(FATAL_ERROR "isolated MQSim patch apply failed for ${patch}: ${apply_error}")
  endif()
endforeach()

file(GLOB_RECURSE MQSIM_SOURCES CONFIGURE_DEPENDS "${MQSIM_EXPERIMENT_SOURCE}/src/*.cpp")
list(FILTER MQSIM_SOURCES EXCLUDE REGEX "/src/main\\.cpp$")
add_library(mqsim_eq3_maint STATIC ${MQSIM_SOURCES})
target_compile_features(mqsim_eq3_maint PUBLIC cxx_std_17)
target_include_directories(mqsim_eq3_maint PUBLIC
    "${MQSIM_EXPERIMENT_SOURCE}/src" "${MQSIM_EXPERIMENT_SOURCE}/src/exec"
    "${MQSIM_EXPERIMENT_SOURCE}/src/host" "${MQSIM_EXPERIMENT_SOURCE}/src/nvm_chip"
    "${MQSIM_EXPERIMENT_SOURCE}/src/nvm_chip/flash_memory"
    "${MQSIM_EXPERIMENT_SOURCE}/src/sim" "${MQSIM_EXPERIMENT_SOURCE}/src/ssd"
    "${MQSIM_EXPERIMENT_SOURCE}/src/utils")
set_target_properties(mqsim_eq3_maint PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/lib")

add_library(hbfsim_eq3_maint_adapter STATIC
    "${HBFSIM_SOURCE_ROOT}/src/profile/profile.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/src/mqsim_online_maintenance.cpp")
target_compile_features(hbfsim_eq3_maint_adapter PUBLIC cxx_std_20)
target_include_directories(hbfsim_eq3_maint_adapter PUBLIC
    "${CMAKE_CURRENT_LIST_DIR}/include" "${HBFSIM_SOURCE_ROOT}/include"
    "${HBFSIM_SOURCE_ROOT}/third_party/bpftime/third_party")
target_link_libraries(hbfsim_eq3_maint_adapter PUBLIC mqsim_eq3_maint)
set_target_properties(hbfsim_eq3_maint_adapter PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/lib")

add_executable(hbf_mqsim_eq3_maint
    "${CMAKE_CURRENT_LIST_DIR}/service/hbf_mqsim_maintenance_service.cpp")
target_compile_features(hbf_mqsim_eq3_maint PRIVATE cxx_std_20)
target_include_directories(hbf_mqsim_eq3_maint PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/service"
    "${HBFSIM_SOURCE_ROOT}/third_party/bpftime/third_party")
target_link_libraries(hbf_mqsim_eq3_maint PRIVATE hbfsim_eq3_maint_adapter)
set_target_properties(hbf_mqsim_eq3_maint PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/bin")

enable_testing()
add_executable(eq3_maintenance_backend_tests
    "${CMAKE_CURRENT_LIST_DIR}/tests/maintenance_backend_tests.cpp")
target_compile_features(eq3_maintenance_backend_tests PRIVATE cxx_std_20)
target_link_libraries(eq3_maintenance_backend_tests PRIVATE hbfsim_eq3_maint_adapter)
set_target_properties(eq3_maintenance_backend_tests PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/tests")
add_test(NAME eq3_maintenance_backend COMMAND eq3_maintenance_backend_tests
    "${HBFSIM_SOURCE_ROOT}/configs/profiles/nominal.json")
