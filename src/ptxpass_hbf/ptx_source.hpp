#pragma once

#include <string>

namespace hbfsim::ptx::detail {

inline std::string code_without_comments(const std::string& line,
                                         bool& inside_block_comment)
{
    std::string code;
    for (std::size_t index = 0; index < line.size();) {
        if (inside_block_comment) {
            const auto end = line.find("*/", index);
            if (end == std::string::npos) {
                return code;
            }
            inside_block_comment = false;
            index = end + 2;
            continue;
        }
        if (line.compare(index, 2, "//") == 0) {
            break;
        }
        if (line.compare(index, 2, "/*") == 0) {
            inside_block_comment = true;
            index += 2;
            continue;
        }
        code.push_back(line[index]);
        ++index;
    }
    return code;
}
}  // namespace hbfsim::ptx::detail
