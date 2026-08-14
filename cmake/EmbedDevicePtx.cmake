if(NOT DEFINED INPUT OR NOT DEFINED OUTPUT)
    message(FATAL_ERROR "EmbedDevicePtx.cmake requires INPUT and OUTPUT")
endif()

file(READ "${INPUT}" helper_ptx)
string(REGEX REPLACE "(^|\n)[ \t]*\\.(version|target|address_size)[^\n]*(\n|$)" "\\1" helper_ptx "${helper_ptx}")
string(LENGTH "${helper_ptx}" helper_ptx_length)
set(delimiter "HBFDEV80F5898")
file(WRITE "${OUTPUT}"
    "#pragma once\n#include <string_view>\nnamespace hbfsim::ptx {\n"
    "inline constexpr std::string_view kEmbeddedDevicePtx{R\"${delimiter}("
    "${helper_ptx}"
    ")${delimiter}\", ${helper_ptx_length}};\n}  // namespace hbfsim::ptx\n")
