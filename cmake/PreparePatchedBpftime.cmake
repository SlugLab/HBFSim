if(NOT IS_ABSOLUTE "${BPFTIME_SOURCE}" OR
   NOT IS_ABSOLUTE "${PATCH}" OR
   NOT IS_ABSOLUTE "${OUTPUT_SOURCE}")
    message(FATAL_ERROR "bpftime source, patch, and output must be absolute")
endif()

get_filename_component(output_name "${OUTPUT_SOURCE}" NAME)
if(NOT output_name STREQUAL "bpftime-hbfsim-src")
    message(FATAL_ERROR
        "refusing to replace unexpected bpftime copy path: ${OUTPUT_SOURCE}"
    )
endif()

execute_process(
    COMMAND git rev-parse HEAD
    WORKING_DIRECTORY "${BPFTIME_SOURCE}"
    RESULT_VARIABLE revision_result
    OUTPUT_VARIABLE revision
    ERROR_VARIABLE revision_error
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT revision_result EQUAL 0)
    message(FATAL_ERROR "unable to read bpftime revision: ${revision_error}")
endif()
if(NOT revision STREQUAL "ec26daecc8e787fb80fd95dd596a576404a5e36e")
    message(FATAL_ERROR "unexpected bpftime revision: ${revision}")
endif()

file(REMOVE_RECURSE "${OUTPUT_SOURCE}")
file(MAKE_DIRECTORY "${OUTPUT_SOURCE}")
file(
    COPY "${BPFTIME_SOURCE}/"
    DESTINATION "${OUTPUT_SOURCE}"
    PATTERN ".git" EXCLUDE
    PATTERN "build" EXCLUDE
)

get_filename_component(output_parent "${OUTPUT_SOURCE}" DIRECTORY)
execute_process(
    COMMAND
        "${CMAKE_COMMAND}" -E env
        "GIT_CEILING_DIRECTORIES=${output_parent}"
        git apply --check "${PATCH}"
    WORKING_DIRECTORY "${OUTPUT_SOURCE}"
    RESULT_VARIABLE check_result
    ERROR_VARIABLE check_error
)
if(NOT check_result EQUAL 0)
    message(FATAL_ERROR "bpftime patch check failed: ${check_error}")
endif()

execute_process(
    COMMAND
        "${CMAKE_COMMAND}" -E env
        "GIT_CEILING_DIRECTORIES=${output_parent}"
        git apply "${PATCH}"
    WORKING_DIRECTORY "${OUTPUT_SOURCE}"
    RESULT_VARIABLE apply_result
    ERROR_VARIABLE apply_error
)
if(NOT apply_result EQUAL 0)
    message(FATAL_ERROR "bpftime patch application failed: ${apply_error}")
endif()
message(STATUS "bpftime exact-load patch applied to clean source copy")
