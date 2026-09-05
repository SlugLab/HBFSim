#pragma once

#include <json.hpp>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace hbfsim::eval {
inline std::uint64_t integer(const nlohmann::json& object, const char* key)
{
    const auto& value = object.at(key);
    if (value.is_number_unsigned()) return value.get<std::uint64_t>();
    if (!value.is_number_integer() || value.get<std::int64_t>() < 0)
        throw std::invalid_argument(std::string(key)+" requires a nonnegative integer");
    return static_cast<std::uint64_t>(value.get<std::int64_t>());
}
}
