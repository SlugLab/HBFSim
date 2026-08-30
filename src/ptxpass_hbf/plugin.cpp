#include "hbfsim/durable_append.hpp"
#include "transform.hpp"

#include <json.hpp>
#include <openssl/sha.h>

#include <array>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iomanip>
#include <mutex>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace {

nlohmann::json pass_config()
{
    return {
        {"name", "hbf_memory"},
        {"description",
         "Rewrite supported global memory accesses through HBFSim"},
        {"attach_points",
         {{"includes", {"^kprobe/.*$"}},
          {"excludes", {"^kprobe/__(?:hbfsim|bpftime)_.*$"}}}},
        {"attach_type", 8},
        {"parameters",
         {{"resolver", "__hbfsim_resolve"},
          {"fault_handler", "__hbfsim_fault"},
          {"emit_coverage", true}}},
        {"validation",
         {{"require_entry", true},
          {"require_ret", true},
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
    return alignment == 0 ? value
                          : (value + alignment - 1) / alignment * alignment;
}

bool parameter_feeds_memory_address(const std::string& body,
                                    const std::string& parameter_name)
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

std::vector<PassParameter> parameter_metadata(const std::string& ptx,
                                              const std::string& kernel)
{
    const auto entry = ptx.find(".entry " + kernel);
    if (entry == std::string::npos) {
        return {};
    }
    const auto begin = ptx.find('(', entry);
    const auto end =
        begin == std::string::npos ? std::string::npos : ptx.find(')', begin);
    if (end == std::string::npos) {
        return {};
    }

    const auto body_begin = ptx.find('{', end);
    auto body_end = std::string::npos;
    int depth = 0;
    for (auto cursor = body_begin;
         cursor != std::string::npos && cursor < ptx.size(); ++cursor) {
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
        R"(\.param((?:\s+\.[A-Za-z][A-Za-z0-9_]*(?:\s+\d+)?)*)\s+([A-Za-z0-9_$.]+)(?:\[(\d+)\])?)");
    static const std::regex type_qualifier(
        R"(\.(pred|[A-Za-z]+[0-9]+)(?:\s|$))");
    static const std::regex alignment_qualifier(R"(\.align\s+(\d+))");
    const std::string declarations = ptx.substr(begin + 1, end - begin - 1);
    std::vector<PassParameter> parameters;
    std::size_t index = 0;
    std::size_t offset = 0;
    for (std::sregex_iterator
             it(declarations.begin(), declarations.end(), parameter),
         last;
         it != last; ++it, ++index) {
        const auto qualifiers = (*it)[1].str();
        std::smatch type_match;
        if (!std::regex_search(qualifiers, type_match, type_qualifier)) {
            continue;
        }
        const auto type = type_match[1].str();
        const auto name = (*it)[2].str();
        const bool aggregate = (*it)[3].matched;
        const std::size_t element_width = scalar_width(type);
        const std::size_t width =
            aggregate ? element_width *
                            static_cast<std::size_t>(std::stoul((*it)[3].str()))
                      : element_width;
        const bool pointer_qualified =
            qualifiers.find(".ptr") != std::string::npos;
        std::smatch alignment_match;
        const std::size_t alignment =
            !pointer_qualified &&
                    std::regex_search(qualifiers, alignment_match,
                                      alignment_qualifier)
                ? static_cast<std::size_t>(
                      std::stoul(alignment_match[1].str()))
                : std::min<std::size_t>(width, 8);
        offset = align_up(offset, alignment);
        std::string kind = "scalar";
        if (aggregate) {
            kind = "opaque_aggregate";
        } else if ((type == "u64" || type == "b64") &&
                   (pointer_qualified ||
                    parameter_feeds_memory_address(body, name))) {
            kind = "pointer";
        }
        parameters.push_back({index, offset, width, name, std::move(kind)});
        offset += width;
    }
    return parameters;
}

constexpr std::string_view module_identity_symbol = "__hbfsim_module_identity";

std::array<unsigned char, SHA256_DIGEST_LENGTH> sha256(const std::string& text)
{
    std::array<unsigned char, SHA256_DIGEST_LENGTH> digest{};
    SHA256(reinterpret_cast<const unsigned char*>(text.data()), text.size(),
           digest.data());
    return digest;
}

std::string
hex_identity(const std::array<unsigned char, SHA256_DIGEST_LENGTH>& identity)
{
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const auto byte : identity) {
        output << std::setw(2) << static_cast<unsigned>(byte);
    }
    return output.str();
}

class TrustedModuleRegistry {
  public:
    struct Identity {
        std::string value;
        bool previously_emitted;
    };

    Identity identity_for(const std::string& ptx)
    {
        const auto state = hex_identity(sha256(ptx));
        std::lock_guard lock(mutex_);
        if (ptx.find(module_identity_symbol) == std::string::npos) {
            return {.value = state, .previously_emitted = false};
        }
        const auto found = emitted_states_.find(state);
        if (found == emitted_states_.end()) {
            throw std::invalid_argument(
                "untrusted preexisting HBFSim module identity");
        }
        return {.value = found->second, .previously_emitted = true};
    }

    void record(const std::string& emitted_ptx, const std::string& identity)
    {
        std::lock_guard lock(mutex_);
        emitted_states_.insert_or_assign(hex_identity(sha256(emitted_ptx)),
                                         identity);
    }

  private:
    std::mutex mutex_;
    std::unordered_map<std::string, std::string> emitted_states_;
};

TrustedModuleRegistry& trusted_modules()
{
    static TrustedModuleRegistry registry;
    return registry;
}

std::string inject_module_identity(std::string ptx, const std::string& identity)
{
    if (ptx.find(module_identity_symbol) != std::string::npos) {
        return ptx;
    }
    const auto directives_end = ptx.find(".address_size");
    const auto newline = directives_end == std::string::npos
                             ? std::string::npos
                             : ptx.find('\n', directives_end);
    const auto insert = newline == std::string::npos ? 0 : newline + 1;
    std::ostringstream declaration;
    declaration << ".visible .const .align 8 .b8 " << module_identity_symbol
                << "[32] = {";
    for (std::size_t index = 0; index < SHA256_DIGEST_LENGTH; ++index) {
        if (index != 0) {
            declaration << ", ";
        }
        declaration << "0x" << identity.substr(index * 2, 2);
    }
    declaration << "};\n";
    ptx.insert(insert, declaration.str());
    return ptx;
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
    hbfsim::append_durable_line(path, line);
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
            .ebpf_communication_data_symbol = request_json.value(
                "ebpf_communication_data_symbol", "constData"),
        };
        const auto trusted_identity =
            trusted_modules().identity_for(request.full_ptx);
        request.trusted_existing_helper =
            trusted_identity.previously_emitted;
        auto transformed = hbfsim::ptx::transform_ptx(request);
        const auto& identity = trusted_identity.value;
        transformed.output_ptx =
            inject_module_identity(std::move(transformed.output_ptx), identity);
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
            {"module_id", "ptx:sha256:" + identity},
            {"kernel", request.to_patch_kernel},
            {"ptx_target", ptx_target(request.full_ptx)},
            {"instrumented",
             transformed.modified && relevant_unsupported.empty()},
            {"cubin_only", false},
            {"parameters", std::move(parameters_json)},
            {"unsupported_parameters", std::move(unsupported)},
            {"rewritten_instructions",
             transformed.coverage.rewritten_instructions},
            {"unsupported_instructions", relevant_unsupported.size()},
            {"unsupported_opcodes", relevant_unsupported},
        });

        const auto response =
            nlohmann::json{
                {"output_ptx", transformed.output_ptx},
                {"modified", transformed.modified},
                {"coverage",
                 {{"rewritten_instructions",
                   transformed.coverage.rewritten_instructions},
                  {"unsupported_instructions",
                   transformed.coverage.unsupported_instructions},
                  {"excluded_functions",
                   transformed.coverage.excluded_functions},
                  {"unsupported_opcodes",
                   transformed.coverage.unsupported_opcodes}}},
            }
                .dump();
        const auto status = copy_output(response, length, output);
        if (status == 0) {
            trusted_modules().record(transformed.output_ptx, identity);
        }
        return status;
    } catch (const nlohmann::json::exception& error) {
        std::fprintf(stderr, "ptxpass_hbf configuration error: %s\n",
                     error.what());
        return 64;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "ptxpass_hbf error: %s\n", error.what());
        return 70;
    }
}
