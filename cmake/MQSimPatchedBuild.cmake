function(hbfsim_add_patched_mqsim target_name)
    set(mqsim_source "${CMAKE_CURRENT_SOURCE_DIR}/third_party/mqsim")
    set(mqsim_copy "${CMAKE_CURRENT_BINARY_DIR}/_deps/mqsim-hbf-src")
    set(mqsim_patches
        "${CMAKE_CURRENT_SOURCE_DIR}/patches/mqsim/0001-online-hbf-api.patch"
        "${CMAKE_CURRENT_SOURCE_DIR}/patches/mqsim/0002-qlc-support.patch"
    )

    file(REMOVE_RECURSE "${mqsim_copy}")
    file(MAKE_DIRECTORY "${mqsim_copy}")
    file(
        COPY "${mqsim_source}/"
        DESTINATION "${mqsim_copy}"
        PATTERN ".git" EXCLUDE
        PATTERN "build" EXCLUDE
        PATTERN "MQSim" EXCLUDE
    )

    foreach(mqsim_patch IN LISTS mqsim_patches)
        execute_process(
            COMMAND
                "${CMAKE_COMMAND}" -E env
                "GIT_CEILING_DIRECTORIES=${CMAKE_CURRENT_BINARY_DIR}/_deps"
                "${GIT_EXECUTABLE}" apply --check --unsafe-paths "${mqsim_patch}"
            WORKING_DIRECTORY "${mqsim_copy}"
            RESULT_VARIABLE patch_check_result
            ERROR_VARIABLE patch_check_error
        )
        if(NOT patch_check_result EQUAL 0)
            message(FATAL_ERROR "MQSim patch check failed for ${mqsim_patch}: ${patch_check_error}")
        endif()

        execute_process(
            COMMAND
                "${CMAKE_COMMAND}" -E env
                "GIT_CEILING_DIRECTORIES=${CMAKE_CURRENT_BINARY_DIR}/_deps"
                "${GIT_EXECUTABLE}" apply --unsafe-paths "${mqsim_patch}"
            WORKING_DIRECTORY "${mqsim_copy}"
            RESULT_VARIABLE patch_result
            ERROR_VARIABLE patch_error
        )
        if(NOT patch_result EQUAL 0)
            message(FATAL_ERROR "MQSim patch application failed for ${mqsim_patch}: ${patch_error}")
        endif()
        message(STATUS "MQSim HBF patch applied to clean build copy: ${mqsim_patch}")
    endforeach()

    file(GLOB_RECURSE mqsim_sources CONFIGURE_DEPENDS "${mqsim_copy}/src/*.cpp")
    list(FILTER mqsim_sources EXCLUDE REGEX "/src/main\\.cpp$")

    add_library(${target_name} STATIC ${mqsim_sources})
    target_compile_features(${target_name} PUBLIC cxx_std_17)
    target_include_directories(
        ${target_name}
        PUBLIC
            "${mqsim_copy}/src"
            "${mqsim_copy}/src/exec"
            "${mqsim_copy}/src/host"
            "${mqsim_copy}/src/nvm_chip"
            "${mqsim_copy}/src/nvm_chip/flash_memory"
            "${mqsim_copy}/src/sim"
            "${mqsim_copy}/src/ssd"
            "${mqsim_copy}/src/utils"
    )
endfunction()
