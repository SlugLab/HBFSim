if(NOT DEFINED PATCHES AND DEFINED PATCH)
    set(PATCHES "${PATCH}")
endif()
if(NOT IS_ABSOLUTE "${BPFTIME_SOURCE}" OR
   NOT IS_ABSOLUTE "${OUTPUT_SOURCE}" OR NOT DEFINED PATCHES)
    message(FATAL_ERROR "bpftime source, patches, and output must be absolute")
endif()
foreach(patch IN LISTS PATCHES)
    if(NOT IS_ABSOLUTE "${patch}" OR NOT EXISTS "${patch}")
        message(FATAL_ERROR "bpftime patch must be an existing absolute path: ${patch}")
    endif()
endforeach()
list(LENGTH PATCHES patch_count)
if(patch_count LESS 1)
    message(FATAL_ERROR "at least one bpftime patch is required")
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

execute_process(
    COMMAND
        git status --porcelain=v1 --untracked-files=all
        --ignore-submodules=none
    WORKING_DIRECTORY "${BPFTIME_SOURCE}"
    RESULT_VARIABLE status_result
    OUTPUT_VARIABLE source_status
    ERROR_VARIABLE status_error
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT status_result EQUAL 0)
    message(FATAL_ERROR
        "unable to inspect pinned bpftime source worktree: ${status_error}"
    )
endif()
if(NOT source_status STREQUAL "")
    message(FATAL_ERROR "pinned bpftime source worktree is dirty")
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
foreach(patch IN LISTS PATCHES)
    execute_process(
        COMMAND
            "${CMAKE_COMMAND}" -E env
            "GIT_CEILING_DIRECTORIES=${output_parent}"
            git apply --check "${patch}"
        WORKING_DIRECTORY "${OUTPUT_SOURCE}"
        RESULT_VARIABLE check_result
        ERROR_VARIABLE check_error
    )
    if(NOT check_result EQUAL 0)
        message(FATAL_ERROR "bpftime patch check failed for ${patch}: ${check_error}")
    endif()
    execute_process(
        COMMAND
            "${CMAKE_COMMAND}" -E env
            "GIT_CEILING_DIRECTORIES=${output_parent}"
            git apply "${patch}"
        WORKING_DIRECTORY "${OUTPUT_SOURCE}"
        RESULT_VARIABLE apply_result
        ERROR_VARIABLE apply_error
    )
    if(NOT apply_result EQUAL 0)
        message(FATAL_ERROR "bpftime patch application failed for ${patch}: ${apply_error}")
    endif()
endforeach()
message(STATUS "bpftime exact-load patch series applied to clean source copy")
