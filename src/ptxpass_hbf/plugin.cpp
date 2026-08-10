#include "transform.hpp"

#include <json.hpp>

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <iomanip>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

nlohmann::json pass_config()
{
    return {
        {"name", "hbf_memory"},
        {"description", "Rewrite supported global memory accesses through HBFSim"},
        {"attach_points",
         {{"includes", {"^kprobe/.*$"}},
          {"excludes", {"^kprobe/__(?:hbfsim|bpftime)_.*$"}}}},
        {"attach_type", 8},
        {"parameters",
         {{"resolver", "__hbfsim_resolve"},
          {"fault_handler", "__hbfsim_fault"},
          {"emit_coverage", true}}},
        {"validation", {{"require_entry", true}, {"require_ret", true},
                         {"ptx_version_min", "8.7"}}},
    };
}

struct PassParameter {
    std::size_t index;
    std::size_t offset;
    std::size_t width;
    std::string name;
    std::string kind;
};

std::size_t scalar_width(const std::string& type)
{
    if (type == "pred") {
        return 1;
    }
    static const std::regex bits(R"([A-Za-z]+([0-9]+))");
    std::smatch match;
    if (!std::regex_match(type, match, bits)) {
        return 0;
    }
    return static_cast<std::size_t>(std::stoul(match[1].str())) / 8;
}

std::size_t align_up(std::size_t value, std::size_t alignment)
{
    return alignment == 0 ? value : (value + alignment - 1) / alignment * alignment;
}

bool parameter_feeds_memory_address(
    const std::string& body, const std::string& parameter_name)
{
    const std::regex load(
        R"(ld\.param\.(?:u64|b64)\s+(%rd[A-Za-z0-9_$]+)\s*,\s*\[\s*)" +
        parameter_name + R"((?:\s*\+\s*0)?\s*\])");
    std::smatch loaded;
    if (!std::regex_search(body, loaded, load)) {
        return false;
    }
    const auto address_register = loaded[1].str();
    const std::regex memory_address(
        R"((?:ld|st|atom|red)(?:\.global|\.[subf][0-9]|\.b[0-9])[^;]*\[\s*)" +
        address_register + R"((?:\s*[+-][^\]]+)?\s*\])");
    return std::regex_search(body, memory_address);
}

std::vector<PassParameter> parameter_metadata(
    const std::string& ptx, const std::string& kernel)
{
    const auto entry = ptx.find(".entry " + kernel);
    if (entry == std::string::npos) {
        return {};
    }
    const auto begin = ptx.find('(', entry);
    const auto end = begin == std::string::npos ? std::string::npos
                                                 : ptx.find(')', begin);
    if (end == std::string::npos) {
        return {};
    }

    const auto body_begin = ptx.find('{', end);
    auto body_end = std::string::npos;
    int depth = 0;
    for (auto cursor = body_begin; cursor != std::string::npos && cursor < ptx.size();
         ++cursor) {
        if (ptx[cursor] == '{') {
            ++depth;
        } else if (ptx[cursor] == '}' && --depth == 0) {
            body_end = cursor;
            break;
        }
    }
    if (body_begin == std::string::npos || body_end == std::string::npos) {
        return {};
    }
    const std::string body =
        ptx.substr(body_begin + 1, body_end - body_begin - 1);
    static const std::regex parameter(
        R"(\.param(?:\s+\.align\s+(\d+))?\s+\.([A-Za-z0-9]+)\s+([A-Za-z0-9_$.]+)(?:\[(\d+)\])?)");
    const std::string declarations = ptx.substr(begin + 1, end - begin - 1);
    std::vector<PassParameter> parameters;
    std::size_t index = 0;
    std::size_t offset = 0;
    for (std::sregex_iterator it(declarations.begin(), declarations.end(), parameter),
         last;
         it != last; ++it, ++index) {
        const auto type = (*it)[2].str();
        const auto name = (*it)[3].str();
        const bool aggregate = (*it)[4].matched;
        const std::size_t element_width = scalar_width(type);
        const std::size_t width = aggregate
                                      ? element_width * static_cast<std::size_t>(
                                                            std::stoul((*it)[4].str()))
                                      : element_width;
        const std::size_t alignment = (*it)[1].matched
                                          ? static_cast<std::size_t>(
                                                std::stoul((*it)[1].str()))
                                          : std::min<std::size_t>(width, 8);
        offset = align_up(offset, alignment);
        std::string kind = "scalar";
        if (aggregate) {
            kind = "opaque_aggregate";
        } else if ((type == "u64" || type == "b64") &&
                   parameter_feeds_memory_address(body, name)) {
            kind = "pointer";
        }
        parameters.push_back({index, offset, width, name, std::move(kind)});
        offset += width;
    }
    return parameters;
}

std::string module_id(const std::string& ptx)
{
    std::uint64_t hash = 14695981039346656037ULL;
    for (const unsigned char byte : ptx) {
        hash = (hash ^ byte) * 1099511628211ULL;
    }
    std::ostringstream output;
    output << "ptx:" << std::hex << std::setfill('0') << std::setw(16) << hash;
    return output.str();
}

std::string ptx_target(const std::string& ptx)
{
    static const std::regex target(R"(^\s*\.target\s+([^,\s]+))",
                                   std::regex::multiline);
    std::smatch match;
    return std::regex_search(ptx, match, target) ? match[1].str() : "";
}

void append_manifest(const nlohmann::json& manifest)
{
    const char* path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
    if (path == nullptr || path[0] == '\0') {
        return;
    }
    const std::string line = manifest.dump() + '\n';
    const int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (fd < 0) {
        throw std::runtime_error(std::string("unable to open pass manifest: ") +
                                 std::strerror(errno));
    }
    const auto written = write(fd, line.data(), line.size());
    const int saved_errno = errno;
    close(fd);
    if (written < 0 || static_cast<std::size_t>(written) != line.size()) {
        throw std::runtime_error(std::string("unable to append pass manifest: ") +
                                 std::strerror(saved_errno));
    }
}

int copy_output(const std::string& text, int length, char* output)
{
    if (length <= 0 || output == nullptr ||
        text.size() + 1 > static_cast<std::size_t>(length)) {
        return 66;
    }
    std::memcpy(output, text.c_str(), text.size() + 1);
    return 0;
}

bool hbf_relevant_unsupported_opcode(const std::string& opcode)
{
    static const std::regex non_hbf_space(
        R"(^(?:ld|st)\.(?:param|local|shared|const)(?:\.|$))");
    return !std::regex_search(opcode, non_hbf_space);
}

}  // namespace

extern "C" void print_config(int length, char* output)
{
    if (length <= 0 || output == nullptr) {
        return;
    }
    const auto text = pass_config().dump();
    std::snprintf(output, static_cast<std::size_t>(length), "%s", text.c_str());
}

extern "C" int process_input(const char* input, int length, char* output)
{
    try {
        if (input == nullptr) {
            return 65;
        }
        const auto root = nlohmann::json::parse(input);
        const auto& request_json = root.at("input");
        hbfsim::ptx::TransformRequest request{
            .full_ptx = request_json.at("full_ptx").get<std::string>(),
            .to_patch_kernel = request_json.value("to_patch_kernel", ""),
            .global_ebpf_map_info_symbol =
                request_json.value("global_ebpf_map_info_symbol", "map_info"),
            .ebpf_communication_data_symbol =
                request_json.value("ebpf_communication_data_symbol", "constData"),
        };
        const auto transformed = hbfsim::ptx::transform_ptx(request);
        const auto parameters =
            parameter_metadata(request.full_ptx, request.to_patch_kernel);

        std::vector<std::string> relevant_unsupported;
        for (const auto& opcode : transformed.coverage.unsupported_opcodes) {
            if (hbf_relevant_unsupported_opcode(opcode)) {
                relevant_unsupported.push_back(opcode);
            }
        }

        nlohmann::json unsupported = nlohmann::json::array();
        if (!relevant_unsupported.empty()) {
            for (const auto& parameter : parameters) {
                if (parameter.kind != "scalar") {
                    unsupported.push_back(
                        {{"index", parameter.index},
                         {"operation", relevant_unsupported.front()}});
                }
            }
        }
        nlohmann::json parameters_json = nlohmann::json::array();
        for (const auto& parameter : parameters) {
            parameters_json.push_back({
                {"index", parameter.index},
                {"offset", parameter.offset},
                {"width", parameter.width},
                {"kind", parameter.kind},
            });
        }
        append_manifest({
            {"module_id", module_id(request.full_ptx)},
            {"kernel", request.to_patch_kernel},
            {"ptx_target", ptx_target(request.full_ptx)},
            {"instrumented", transformed.modified &&
                                 relevant_unsupported.empty()},
            {"cubin_only", false},
            {"parameters", std::move(parameters_json)},
            {"unsupported_parameters", std::move(unsupported)},
            {"rewritten_instructions", transformed.coverage.rewritten_instructions},
            {"unsupported_instructions", relevant_unsupported.size()},
            {"unsupported_opcodes", relevant_unsupported},
        });

        return copy_output(nlohmann::json{
                               {"output_ptx", transformed.output_ptx},
                               {"modified", transformed.modified},
                           }.dump(),
                           length, output);
    } catch (const nlohmann::json::exception& error) {
        std::fprintf(stderr, "ptxpass_hbf configuration error: %s\n", error.what());
        return 64;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "ptxpass_hbf error: %s\n", error.what());
        return 70;
    }
}
