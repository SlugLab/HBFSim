cmake_minimum_required(VERSION 3.16)
project(hbfsim_eq3_thermal LANGUAGES CXX)

include(CTest)

add_library(hbfsim_eq3_thermal STATIC thermal.cpp text_format.cpp)
target_compile_features(hbfsim_eq3_thermal PUBLIC cxx_std_20)
target_include_directories(
  hbfsim_eq3_thermal PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
target_compile_options(hbfsim_eq3_thermal PRIVATE -Wall -Wextra -Wpedantic)

# Independent opt-in consumer library; no top-level runtime/default/ABI change.
add_library(hbfsim_eq3_observer STATIC observer.cpp)
target_link_libraries(hbfsim_eq3_observer PUBLIC hbfsim_eq3_thermal)
target_compile_options(hbfsim_eq3_observer PRIVATE -Wall -Wextra -Wpedantic)

add_library(hbfsim_eq3_cpu_service STATIC cpu_service.cpp)
target_link_libraries(hbfsim_eq3_cpu_service PUBLIC hbfsim_eq3_observer)
set(HBFSIM_EQ3_JSON_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/bpftime/third_party" CACHE PATH "Pinned existing JSON header directory")
target_include_directories(hbfsim_eq3_cpu_service PUBLIC "${HBFSIM_EQ3_JSON_INCLUDE}")
target_compile_options(hbfsim_eq3_cpu_service PRIVATE -Wall -Wextra -Wpedantic)
add_executable(hbfsim_eq3_cpu_pilot cpu_pilot.cpp)
target_link_libraries(hbfsim_eq3_cpu_pilot PRIVATE hbfsim_eq3_cpu_service)

add_executable(hbfsim_eq3_thermal_cli cli.cpp)
target_link_libraries(hbfsim_eq3_thermal_cli PRIVATE hbfsim_eq3_thermal)

if(BUILD_TESTING)
  add_executable(hbfsim_eq3_thermal_tests ../../tests/eq3_thermal/thermal_tests.cpp)
  target_link_libraries(hbfsim_eq3_thermal_tests PRIVATE hbfsim_eq3_thermal)
  add_test(NAME hbfsim_eq3_thermal_tests COMMAND hbfsim_eq3_thermal_tests)
  add_executable(hbfsim_eq3_observer_tests ../../tests/eq3_thermal/observer_tests.cpp)
  target_link_libraries(hbfsim_eq3_observer_tests PRIVATE hbfsim_eq3_observer)
  add_test(NAME hbfsim_eq3_observer_tests COMMAND hbfsim_eq3_observer_tests)
  add_executable(hbfsim_eq3_cpu_service_tests ../../tests/eq3_thermal/cpu_service_tests.cpp)
  target_link_libraries(hbfsim_eq3_cpu_service_tests PRIVATE hbfsim_eq3_cpu_service)
  add_test(NAME hbfsim_eq3_cpu_service_tests COMMAND hbfsim_eq3_cpu_service_tests)

  set(HBFSIM_EQ3_HOST_CORE_ARCHIVE "" CACHE FILEPATH "Reviewed existing host core archive; recreate via main CPU build")
  set(HBFSIM_EQ3_MQSIM_ARCHIVE "" CACHE FILEPATH "Matching patched MQSim archive; no copied-binary portability claim")
  if(HBFSIM_EQ3_HOST_CORE_ARCHIVE AND HBFSIM_EQ3_MQSIM_ARCHIVE)
    add_executable(hbfsim_eq3_mqsim_observer_tests ../../tests/eq3_thermal/mqsim_observer_tests.cpp)
    target_link_libraries(hbfsim_eq3_mqsim_observer_tests PRIVATE hbfsim_eq3_observer
      "${HBFSIM_EQ3_HOST_CORE_ARCHIVE}" "${HBFSIM_EQ3_MQSIM_ARCHIVE}")
    add_test(NAME hbfsim_eq3_mqsim_observer_tests COMMAND hbfsim_eq3_mqsim_observer_tests
      "${CMAKE_CURRENT_SOURCE_DIR}/../../configs/profiles/nominal.json")
  endif()
endif()
