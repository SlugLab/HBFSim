cmake_minimum_required(VERSION 3.16)
project(hbfsim_eq3_thermal LANGUAGES CXX)

include(CTest)

add_library(hbfsim_eq3_thermal STATIC thermal.cpp text_format.cpp)
target_compile_features(hbfsim_eq3_thermal PUBLIC cxx_std_20)
target_include_directories(
  hbfsim_eq3_thermal PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
target_compile_options(hbfsim_eq3_thermal PRIVATE -Wall -Wextra -Wpedantic)

add_executable(hbfsim_eq3_thermal_cli cli.cpp)
target_link_libraries(hbfsim_eq3_thermal_cli PRIVATE hbfsim_eq3_thermal)

if(BUILD_TESTING)
  add_executable(hbfsim_eq3_thermal_tests ../../tests/eq3_thermal/thermal_tests.cpp)
  target_link_libraries(hbfsim_eq3_thermal_tests PRIVATE hbfsim_eq3_thermal)
  add_test(NAME hbfsim_eq3_thermal_tests COMMAND hbfsim_eq3_thermal_tests)
endif()
