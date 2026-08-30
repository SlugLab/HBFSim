#pragma once

#include <filesystem>
#include <string_view>

namespace hbfsim {

// Appends a complete record, synchronizes it, and synchronizes the parent
// directory when this call creates the file.
void append_durable_line(const std::filesystem::path& path,
                         std::string_view line);

}  // namespace hbfsim
