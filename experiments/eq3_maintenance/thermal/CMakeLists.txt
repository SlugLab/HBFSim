cmake_minimum_required(VERSION 3.16)
project(eq3_maintenance_thermal_service LANGUAGES CXX)

find_package(Eigen3 3.3 REQUIRED NO_MODULE)
find_package(OpenSSL REQUIRED COMPONENTS Crypto)

set(EQ3_SOURCE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." CACHE PATH
    "HBFSim source root used by this isolated experiment target")
set(EQ3_THERMAL_ARCHIVE "" CACHE FILEPATH
    "Explicit matching libhbfsim_eq3_thermal.a; no default artifact lookup")
if(NOT EQ3_THERMAL_ARCHIVE OR NOT EXISTS "${EQ3_THERMAL_ARCHIVE}")
  message(FATAL_ERROR "Set EQ3_THERMAL_ARCHIVE to the reviewed matching thermal archive")
endif()

add_executable(eq3_maintenance_thermal_service thermal_service.cpp)
target_compile_features(eq3_maintenance_thermal_service PRIVATE cxx_std_20)
target_compile_options(eq3_maintenance_thermal_service PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(eq3_maintenance_thermal_service PRIVATE
  "${EQ3_SOURCE_ROOT}/include"
  "${EQ3_SOURCE_ROOT}/third_party/bpftime/third_party")
target_link_libraries(eq3_maintenance_thermal_service PRIVATE
  Eigen3::Eigen OpenSSL::Crypto "${EQ3_THERMAL_ARCHIVE}")
