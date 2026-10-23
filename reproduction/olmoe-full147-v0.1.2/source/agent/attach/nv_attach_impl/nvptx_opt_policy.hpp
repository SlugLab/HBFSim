#pragma once
#include <cstdlib>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>

namespace bpftime::attach::nvptx_opt_policy {
inline bool valid_raw_sha256(std::string_view value) {
    if (value.size() != 64) return false;
    for (char c : value)
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}
inline std::string original_name(std::string_view compiled_name) {
    constexpr std::string_view prefix = "patched.";
    if (compiled_name.substr(0, prefix.size()) == prefix)
        compiled_name.remove_prefix(prefix.size());
    return std::string(compiled_name);
}
template <typename Hash>
std::string raw_digest_for_module(std::string_view name, std::string_view original_bytes,
                                  bool prepatched, Hash hash_raw_bytes) {
    if (!prepatched) return hash_raw_bytes(original_bytes);
    constexpr std::string_view suffix = ".ptx";
    if (name.size() != 64 + suffix.size() || name.substr(64) != suffix ||
        !valid_raw_sha256(name.substr(0, 64)))
        throw std::runtime_error("Prepatched module filename must be raw SHA256.ptx");
    return std::string(name.substr(0, 64));
}
inline char parse_level(std::string_view value, const char *setting) {
    if (value.size() != 1 || value[0] < '0' || value[0] > '3')
        throw std::runtime_error(std::string(setting) + " must be exactly one of 0, 1, 2, 3");
    return value[0];
}
struct config {
    char fallback = '3';
    std::map<std::string, char> by_raw_sha256;
    std::string option_for_raw(std::string_view raw) const {
        auto found = by_raw_sha256.find(std::string(raw));
        return std::string("-O") + (found == by_raw_sha256.end() ? fallback : found->second);
    }
};
inline config parse(const char *global_level, const char *raw_map) {
    config result;
    if (global_level != nullptr)
        result.fallback = parse_level(global_level, "HBFSIM_NVPTX_OPT_LEVEL");
    if (raw_map == nullptr) return result;
    const std::string_view input(raw_map);
    if (input.empty()) throw std::runtime_error("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256 is empty");
    std::size_t begin = 0;
    while (begin < input.size()) {
        const auto comma = input.find(',', begin);
        const auto end = comma == std::string_view::npos ? input.size() : comma;
        const auto token = input.substr(begin, end - begin);
        if (token.size() != 66 || token[64] != '=' || !valid_raw_sha256(token.substr(0, 64)))
            throw std::runtime_error("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256 expects lowercase raw SHA256=level entries");
        const char level = parse_level(token.substr(65, 1), "HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256");
        if (!result.by_raw_sha256.emplace(std::string(token.substr(0, 64)), level).second)
            throw std::runtime_error("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256 contains duplicate raw SHA256");
        if (comma == std::string_view::npos) break;
        begin = comma + 1;
        if (begin == input.size()) throw std::runtime_error("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256 has trailing comma");
    }
    return result;
}
inline config from_environment() {
    return parse(std::getenv("HBFSIM_NVPTX_OPT_LEVEL"),
                 std::getenv("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256"));
}
} // namespace bpftime::attach::nvptx_opt_policy
