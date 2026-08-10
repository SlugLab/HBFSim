#include "transform.hpp"

#include <json.hpp>

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <regex>
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

std::vector<std::size_t> pointer_parameters(
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

    static const std::regex parameter(
        R"(\.param(?:\s+\.align\s+\d+)?\s+\.([A-Za-z0-9]+)\s+[^,\)]+)");
    const std::string declarations = ptx.substr(begin + 1, end - begin - 1);
    std::vector<std::size_t> pointers;
    std::size_t index = 0;
    for (std::sregex_iterator it(declarations.begin(), declarations.end(), parameter),
         last;
         it != last; ++it, ++index) {
        const auto type = (*it)[1].str();
        if (type == "u64" || type == "b64") {
            pointers.push_back(index);
        }
    }
    return pointers;
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
        const auto pointers =
            pointer_parameters(request.full_ptx, request.to_patch_kernel);

        std::vector<std::string> relevant_unsupported;
        for (const auto& opcode : transformed.coverage.unsupported_opcodes) {
            if (hbf_relevant_unsupported_opcode(opcode)) {
                relevant_unsupported.push_back(opcode);
            }
        }

        nlohmann::json unsupported = nlohmann::json::array();
        if (!relevant_unsupported.empty()) {
            for (const auto index : pointers) {
                unsupported.push_back(
                    {{"index", index},
                     {"operation", relevant_unsupported.front()}});
            }
        }
        append_manifest({
            {"name", request.to_patch_kernel},
            {"instrumented", transformed.modified &&
                                 relevant_unsupported.empty()},
            {"pointer_parameters", pointers},
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
