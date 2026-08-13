#include "hbfsim/durable_append.hpp"
#include "async_object_analysis.hpp"
#include "ptx_analysis.hpp"
#include "ptx_ir.hpp"
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
#include <set>
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

struct FuturePassEvidence {
    std::string ir_sha256;
    nlohmann::json instruction_table = nlohmann::json::array();
    nlohmann::json maximum_live;
    nlohmann::json ambiguities = nlohmann::json::array();
};

struct TmaPassEvidence {
    bool present{false};
    std::string ir_sha256;
    nlohmann::json tensormap_parameters = nlohmann::json::array();
    nlohmann::json descriptor_instruction_ids = nlohmann::json::array();
    nlohmann::json barrier_instruction_ids = nlohmann::json::array();
    nlohmann::json bulk_group_instruction_ids = nlohmann::json::array();
    nlohmann::json instruction_table = nlohmann::json::array();
    std::uint32_t maximum_live_async_objects{0};
    nlohmann::json ambiguities = nlohmann::json::array();
};

std::string selected_kernel_ptx(const std::string& ptx,
                                const std::string& kernel)
{
    const std::regex entry(
        R"(\.(?:visible\s+)?entry\s+)" + kernel + R"(\s*\()",
        std::regex::ECMAScript);
    std::smatch match;
    if (!std::regex_search(ptx, match, entry)) {
        throw std::invalid_argument("selected PTX kernel was not found");
    }
    const auto begin = static_cast<std::size_t>(match.position());
    const auto body_begin = ptx.find('{', begin);
    if (body_begin == std::string::npos) {
        throw std::invalid_argument("selected PTX kernel has no body");
    }
    int depth = 0;
    std::size_t end = std::string::npos;
    for (auto cursor = body_begin; cursor < ptx.size(); ++cursor) {
        if (ptx[cursor] == '{') {
            ++depth;
        } else if (ptx[cursor] == '}' && --depth == 0) {
            end = cursor + 1;
            break;
        }
    }
    if (end == std::string::npos) {
        throw std::invalid_argument("selected PTX kernel is unterminated");
    }
    std::ostringstream output;
    for (const auto directive : {".version", ".target", ".address_size"}) {
        const auto position = ptx.find(directive);
        if (position != std::string::npos) {
            const auto newline = ptx.find('\n', position);
            output << ptx.substr(position, newline - position) << '\n';
        }
    }
    output << ptx.substr(begin, end - begin) << '\n';
    return output.str();
}

FuturePassEvidence future_pass_evidence(const std::string& ptx,
                                        const std::string& kernel)
{
    const auto module = hbfsim::ptx::parse_module(
        selected_kernel_ptx(ptx, kernel));
    const auto& function = module.function(kernel);
    const auto plan = hbfsim::ptx::analyze_futures(function);
    FuturePassEvidence result;
    std::ostringstream canonical;
    canonical << "sm120-future-v1\n" << function.name << '\n';
    for (const auto& instruction : function.instructions) {
        if (!instruction.memory.has_value()) {
            continue;
        }
        const char* kind = "none";
        switch (instruction.memory->kind) {
        case hbfsim::ptx::MemoryKind::Load: kind = "load"; break;
        case hbfsim::ptx::MemoryKind::Store: kind = "store"; break;
        case hbfsim::ptx::MemoryKind::AtomicRmw: kind = "atomic_rmw"; break;
        case hbfsim::ptx::MemoryKind::None: break;
        }
        result.instruction_table.push_back({
            {"instruction_id", instruction.instruction_id},
            {"source_line", instruction.location.line},
            {"bytes", instruction.memory->bytes},
            {"opcode", instruction.opcode},
            {"memory_kind", kind},
        });
        canonical << instruction.instruction_id << '|' << instruction.location.line
                  << '|' << instruction.opcode << '|'
                  << instruction.memory->bytes << '|' << kind << '\n';
    }
    result.ir_sha256 = hex_identity(sha256(canonical.str()));
    result.maximum_live = {
        {"thread", plan.maximum_live.thread_futures},
        {"warp", plan.maximum_live.warp_futures},
        {"cta", plan.maximum_live.cta_futures},
        {"cluster", plan.maximum_live.cluster_futures},
    };
    std::set<std::string> reasons;
    for (const auto& [instruction, reason] : plan.rejection_reasons) {
        (void)instruction;
        reasons.insert(reason);
    }
    for (const auto& reason : reasons) {
        result.ambiguities.push_back(reason);
    }
    return result;
}

const char* tma_direction(hbfsim::ptx::TmaDirection value)
{
    switch (value) {
    case hbfsim::ptx::TmaDirection::GlobalToShared:
        return "global_to_shared";
    case hbfsim::ptx::TmaDirection::SharedToGlobal:
        return "shared_to_global";
    case hbfsim::ptx::TmaDirection::Prefetch: return "prefetch";
    }
    return "unknown";
}

const char* tensor_mode(hbfsim::ptx::TensorMode value)
{
    switch (value) {
    case hbfsim::ptx::TensorMode::Tile: return "tile";
    case hbfsim::ptx::TensorMode::Gather4: return "gather4";
    case hbfsim::ptx::TensorMode::Scatter4: return "scatter4";
    case hbfsim::ptx::TensorMode::Im2col: return "im2col";
    case hbfsim::ptx::TensorMode::Im2colWide: return "im2col_wide";
    }
    return "unknown";
}

const char* completion_kind(hbfsim::ptx::CompletionKind value)
{
    switch (value) {
    case hbfsim::ptx::CompletionKind::Mbarrier: return "mbarrier";
    case hbfsim::ptx::CompletionKind::BulkGroup: return "bulk_group";
    case hbfsim::ptx::CompletionKind::None: return "none";
    }
    return "unknown";
}

std::uint32_t immediate_mask(const hbfsim::ptx::TmaInstruction& tma)
{
    if (!tma.multicast) return 0;
    try {
        std::size_t consumed = 0;
        const auto value = std::stoul(tma.multicast_mask, &consumed, 0);
        return consumed == tma.multicast_mask.size() && value <= 0xffff
                   ? static_cast<std::uint32_t>(value)
                   : 0;
    } catch (const std::exception&) {
        return 0;
    }
}

TmaPassEvidence tma_pass_evidence(
    const std::string& ptx, const std::string& kernel,
    const std::vector<PassParameter>& parameters)
{
    const auto selected = selected_kernel_ptx(ptx, kernel);
    const auto module = hbfsim::ptx::parse_module(selected);
    const auto& function = module.function(kernel);
    const auto plan = hbfsim::ptx::analyze_async_objects(function);
    TmaPassEvidence result;
    result.present = !plan.tma_instruction_ids.empty();
    if (!result.present) return result;

    std::set<std::string> descriptor_registers;
    std::ostringstream canonical;
    canonical << "sm120-tma-v1\n" << function.name << '\n';
    for (const auto& instruction : function.instructions) {
        if (!instruction.async.has_value()) continue;
        if (const auto* tma =
                std::get_if<hbfsim::ptx::TmaInstruction>(
                    &*instruction.async)) {
            descriptor_registers.insert(tma->descriptor);
            const auto generation =
                plan.descriptor_generations.contains(instruction.instruction_id)
                    ? plan.descriptor_generations.at(instruction.instruction_id)
                    : 0;
            const auto mask = immediate_mask(*tma);
            result.instruction_table.push_back({
                {"instruction_id", instruction.instruction_id},
                {"source_line", instruction.location.line},
                {"direction", tma_direction(tma->direction)},
                {"mode", tensor_mode(tma->mode)},
                {"dimensions", tma->dimensions},
                {"completion", completion_kind(tma->completion)},
                {"multicast_mask", mask},
                {"descriptor_generation", generation},
            });
            canonical << instruction.instruction_id << '|'
                      << instruction.location.line << '|'
                      << tma_direction(tma->direction) << '|'
                      << tensor_mode(tma->mode) << '|' << tma->dimensions << '|'
                      << completion_kind(tma->completion) << '|' << mask << '|'
                      << generation << '\n';
        }
    }
    std::unordered_map<std::string, std::size_t> parameter_registers;
    const auto one_register = [](std::string_view operand) {
        static const std::regex expression(R"(%[A-Za-z][A-Za-z0-9_$]*)");
        std::smatch match;
        const std::string text(operand);
        return std::regex_search(text, match, expression) ? match.str()
                                                          : std::string{};
    };
    for (const auto& instruction : function.instructions) {
        if (instruction.operands.size() != 2) continue;
        const auto destination = one_register(instruction.operands[0]);
        if (destination.empty()) continue;
        if (instruction.opcode.starts_with("ld.param.") ||
            instruction.opcode.starts_with("mov.")) {
            for (const auto& parameter : parameters) {
                if (instruction.operands[1].find(parameter.name) !=
                    std::string::npos) {
                    parameter_registers[destination] = parameter.index;
                }
            }
        }
        if (instruction.opcode.starts_with("mov.") ||
            instruction.opcode.starts_with("cvta.param.")) {
            const auto source = one_register(instruction.operands[1]);
            const auto found = parameter_registers.find(source);
            if (found != parameter_registers.end()) {
                parameter_registers[destination] = found->second;
            }
        }
    }
    std::set<std::size_t> provenance_parameters;
    for (const auto& descriptor : descriptor_registers) {
        const auto found = parameter_registers.find(descriptor);
        if (found != parameter_registers.end()) {
            provenance_parameters.insert(found->second);
        }
    }
    for (const auto index : provenance_parameters) {
        result.tensormap_parameters.push_back(index);
    }
    for (const auto id : plan.descriptor_instruction_ids)
        result.descriptor_instruction_ids.push_back(id);
    for (const auto id : plan.barrier_instruction_ids)
        result.barrier_instruction_ids.push_back(id);
    for (const auto id : plan.bulk_group_instruction_ids)
        result.bulk_group_instruction_ids.push_back(id);
    result.maximum_live_async_objects = plan.maximum_live_objects;
    std::set<std::string> reasons;
    for (const auto& [instruction, reason] : plan.rejection_reasons) {
        (void)instruction;
        reasons.insert(reason);
    }
    for (const auto& reason : reasons) result.ambiguities.push_back(reason);
    result.ir_sha256 = hex_identity(sha256(canonical.str()));
    return result;
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
            .async_futures = request_json.value("async_futures", true),
        };
        const auto trusted_identity =
            trusted_modules().identity_for(request.full_ptx);
        request.trusted_existing_helper =
            trusted_identity.previously_emitted;
        auto transformed = hbfsim::ptx::transform_ptx(request);
        const auto future_evidence =
            future_pass_evidence(request.full_ptx, request.to_patch_kernel);
        const auto& identity = trusted_identity.value;
        transformed.output_ptx =
            inject_module_identity(std::move(transformed.output_ptx), identity);
        const auto parameters =
            parameter_metadata(request.full_ptx, request.to_patch_kernel);
        const auto tma_evidence = tma_pass_evidence(
            request.full_ptx, request.to_patch_kernel, parameters);

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
        nlohmann::json manifest{
            {"manifest_schema_version", tma_evidence.present ? 4 : 3},
            {"module_id", "ptx:sha256:" + identity},
            {"original_ptx_sha256", identity},
            {"transformed_ptx_sha256",
             hex_identity(sha256(transformed.output_ptx))},
            {"aot_required_for_exact", true},
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
            {"async_transform_version",
             request.async_futures ? "sm120-future-v1" : "legacy-sync-v1"},
            {"ir_sha256", future_evidence.ir_sha256},
            {"instruction_table", future_evidence.instruction_table},
            {"maximum_live_futures", future_evidence.maximum_live},
            {"ambiguities", future_evidence.ambiguities},
        };
        if (tma_evidence.present) {
            manifest["tma_transform_version"] = "sm120-tma-v1";
            manifest["tma_ir_sha256"] = tma_evidence.ir_sha256;
            manifest["tensormap_parameters"] =
                tma_evidence.tensormap_parameters;
            manifest["descriptor_instruction_ids"] =
                tma_evidence.descriptor_instruction_ids;
            manifest["barrier_instruction_ids"] =
                tma_evidence.barrier_instruction_ids;
            manifest["bulk_group_instruction_ids"] =
                tma_evidence.bulk_group_instruction_ids;
            manifest["tma_instruction_table"] =
                tma_evidence.instruction_table;
            manifest["maximum_live_async_objects"] =
                tma_evidence.maximum_live_async_objects;
            manifest["tma_ambiguities"] = tma_evidence.ambiguities;
            manifest["tensormap_provenance_required"] = true;
        }
        append_manifest(manifest);

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
