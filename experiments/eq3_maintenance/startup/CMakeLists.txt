cmake_minimum_required(VERSION 3.20)
project(eq3_startup_write_probe LANGUAGES CXX)

set(EQ3_BACKEND_BUILD "${CMAKE_CURRENT_LIST_DIR}/../backend/build-isolated" CACHE PATH
    "Existing isolated maintenance backend build (read-only input)")
set(HBFSIM_SOURCE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." CACHE PATH
    "HBFSim source root")
get_filename_component(EQ3_BACKEND_BUILD "${EQ3_BACKEND_BUILD}" ABSOLUTE)
get_filename_component(HBFSIM_SOURCE_ROOT "${HBFSIM_SOURCE_ROOT}" ABSOLUTE)

set(EQ3_ADAPTER "${EQ3_BACKEND_BUILD}/lib/libhbfsim_eq3_maint_adapter.a")
set(EQ3_MQSIM "${EQ3_BACKEND_BUILD}/lib/libmqsim_eq3_maint.a")
set(EQ3_MQSIM_SOURCE "${EQ3_BACKEND_BUILD}/source/mqsim_eq3_maint/src")
foreach(required IN ITEMS "${EQ3_ADAPTER}" "${EQ3_MQSIM}"
                          "${EQ3_MQSIM_SOURCE}/ssd/NVM_PHY_ONFI_NVDDR2.h")
  if(NOT EXISTS "${required}")
    message(FATAL_ERROR "missing existing isolated backend artifact: ${required}")
  endif()
endforeach()

add_executable(eq3_startup_write_probe startup_write_probe.cpp)
target_compile_features(eq3_startup_write_probe PRIVATE cxx_std_20)
target_include_directories(eq3_startup_write_probe PRIVATE
    "${CMAKE_CURRENT_LIST_DIR}/../backend/include"
    "${HBFSIM_SOURCE_ROOT}/include"
    "${HBFSIM_SOURCE_ROOT}/third_party/bpftime/third_party"
    "${EQ3_MQSIM_SOURCE}" "${EQ3_MQSIM_SOURCE}/exec"
    "${EQ3_MQSIM_SOURCE}/host" "${EQ3_MQSIM_SOURCE}/nvm_chip"
    "${EQ3_MQSIM_SOURCE}/nvm_chip/flash_memory" "${EQ3_MQSIM_SOURCE}/sim"
    "${EQ3_MQSIM_SOURCE}/ssd" "${EQ3_MQSIM_SOURCE}/utils")
target_link_libraries(eq3_startup_write_probe PRIVATE "${EQ3_ADAPTER}" "${EQ3_MQSIM}")
