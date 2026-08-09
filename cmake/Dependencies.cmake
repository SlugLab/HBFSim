find_package(Git REQUIRED QUIET)

function(hbfsim_require_pinned_submodule name relative_path expected_commit)
    set(absolute_path "${CMAKE_CURRENT_SOURCE_DIR}/${relative_path}")
    if(NOT EXISTS "${absolute_path}/.git")
        message(FATAL_ERROR
            "${name} submodule is missing at ${relative_path}; "
            "run git submodule update --init --recursive")
    endif()

    execute_process(
        COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
        WORKING_DIRECTORY "${absolute_path}"
        RESULT_VARIABLE git_result
        OUTPUT_VARIABLE actual_commit
        ERROR_VARIABLE git_error
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(NOT git_result EQUAL 0)
        message(FATAL_ERROR
            "could not read ${name} submodule revision: ${git_error}")
    endif()
    if(NOT actual_commit STREQUAL expected_commit)
        message(FATAL_ERROR
            "${name} must be pinned to ${expected_commit}, found ${actual_commit}")
    endif()

    message(STATUS "${name} pinned commit: ${actual_commit}")
endfunction()

hbfsim_require_pinned_submodule(
    bpftime
    third_party/bpftime
    ec26daecc8e787fb80fd95dd596a576404a5e36e
)
hbfsim_require_pinned_submodule(
    MQSim
    third_party/mqsim
    51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16
)
