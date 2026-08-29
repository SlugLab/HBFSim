#include "transform.hpp"

#include "ptx_memory_op.hpp"

#if defined(HBFSIM_HAVE_DEVICE_HELPER_PTX)
#include "hbf_device_ptx.hpp"
#endif

#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace hbfsim::ptx {
namespace {

#define HBFSIM_STRINGIFY_DETAIL(value) #value
#define HBFSIM_STRINGIFY(value) HBFSIM_STRINGIFY_DETAIL(value)

#if !defined(HBFSIM_DEVICE_PTX_ARCHITECTURE)
#define HBFSIM_DEVICE_PTX_ARCHITECTURE 120
#endif

constexpr std::string_view kDevicePtxArchitecture =
    HBFSIM_STRINGIFY(HBFSIM_DEVICE_PTX_ARCHITECTURE);
constexpr unsigned kDevicePtxArchitectureNumber =
    HBFSIM_DEVICE_PTX_ARCHITECTURE;

std::string device_ptx_target()
{
    return "sm_" + std::string{kDevicePtxArchitecture};
}

bool has_compatible_device_target(const std::string& ptx)
{
    static const std::regex target(
        R"(^\s*\.target\s+sm_([0-9]+)(a?)(?:\s|,|$))",
        std::regex::multiline);
    std::smatch match;
    if (!std::regex_search(ptx, match, target) ||
        match[1].str() != kDevicePtxArchitecture) {
        return false;
    }
    const auto suffix = match[2].str();
    return suffix.empty() ||
           (suffix == "a" && kDevicePtxArchitectureNumber >= 90);
}

bool unsupported_memory_instruction(const std::string& line,
                                    std::string& opcode)
{
    static const std::regex expression(
        // The `cp.async` and `cp.reduce.async` alternatives require `.global`
        // in the opcode on purpose. The same families carry pure
        // synchronisation forms -- cp.async.commit_group, cp.async.wait_group,
        // cp.async.bulk.wait_group -- which touch no memory and must not be
        // reported as unsupported memory operations.
        //
        // The three bulk TENSOR prefixes are excluded by the lookaheads:
        // cp.async.bulk.tensor., cp.reduce.async.bulk.tensor. and
        // cp.async.bulk.prefetch.tensor. Those are the prefixes parse_tma
        // accepts on branch feature/sm120-exact-stage1, where they are modeled
        // rather than refused. Matching them here would refuse, once that
        // branch merges, exactly the launches it can model. This pattern
        // closes the gap that branch leaves open -- the plain
        // cp.async.ca/cg.shared.global form, which its unsupported pattern
        // does not match either -- and stays out of what it handles.
        R"(^\s*(?:@!?%[A-Za-z0-9_$]+\s+)?((?:atom|red)\.global\S*|ld\.(?!global)\S*|st\.(?!global)\S*|cp\.async(?!\.bulk\.(?:tensor|prefetch\.tensor)\.)\S*\.global\S*|cp\.reduce\.async(?!\.bulk\.tensor\.)\S*\.global\S*|tex\S*|suld\S*|sust\S*|asm\s*\().*;\s*(?://.*)?$)");
    std::smatch match;
    if (!std::regex_match(line, match, expression)) {
        return false;
    }
    opcode = match[1].str();
    return true;
}

std::string replace_address(const PtxMemoryOp& op,
                            const std::string& scratch)
{
    static const std::regex address(
        R"(\[\s*%rd[A-Za-z0-9_$]*(?:\s*[+-]\s*-?(?:0[xX])?[0-9A-Fa-f]+)?\s*\])");
    return std::regex_replace(op.original_line, address, "[" + scratch + "]",
                              std::regex_constants::format_first_only);
}

bool defines_function(const std::string& ptx, const std::string& name)
{
    const std::regex definition(
        R"(\.(?:visible\s+)?func(?:\s+\([^{};]*\))?\s+)" + name +
        R"(\s*\()",
        std::regex::ECMAScript);
    return std::regex_search(ptx, definition);
}

bool defines_device_helper_marker(const std::string& ptx)
{
    static const std::regex definition(
        R"(\.(?:visible\s+)?(?:global|const)[^;\n]*\b__hbfsim_device_helper_marker\b[^;\n]*;)");
    return std::regex_search(ptx, definition);
}

void append_device_helper(std::string& ptx, bool trusted_existing_helper)
{
    const bool marker = defines_device_helper_marker(ptx);
    const bool resolver = defines_function(ptx, "__hbfsim_resolve");
    const bool fault = defines_function(ptx, "__hbfsim_fault");
    if (marker || resolver || fault) {
#if defined(HBFSIM_HAVE_DEVICE_HELPER_PTX)
        const bool exact_helper =
            trusted_existing_helper && marker && resolver && fault &&
            ptx.find(kEmbeddedDevicePtx) != std::string::npos;
        if (exact_helper) {
            return;
        }
#endif
        throw std::runtime_error(
            "PTX module collides with the reserved HBFSim device ABI");
    }
    const auto expected_target = device_ptx_target();
    if (!has_compatible_device_target(ptx)) {
        throw std::runtime_error(
            "HBFSim device helper built for " + expected_target +
            " cannot instrument a PTX module with a different baseline target");
    }
#if defined(HBFSIM_HAVE_DEVICE_HELPER_PTX)
    const auto address_size = ptx.find(".address_size");
    const auto newline = address_size == std::string::npos
                             ? std::string::npos
                             : ptx.find('\n', address_size);
    const auto insertion = newline == std::string::npos ? 0 : newline + 1;
    std::string helper{"\n// HBFSim embedded device helper\n"};
    helper.append(kEmbeddedDevicePtx);
    helper.push_back('\n');
    ptx.insert(insertion, helper);
#else
    throw std::runtime_error(
        "HBFSim device helper PTX was unavailable at build time");
#endif
}

}  // namespace

TransformResult transform_ptx(const TransformRequest& request)
{
    TransformResult result{.output_ptx = {}, .coverage = {}, .modified = false};
    std::istringstream input(request.full_ptx);
    std::ostringstream output;
    std::string line;
    std::string function_name;
    bool excluded = false;
    bool selected = false;
    int brace_depth = 0;
    std::uint64_t scratch_id = 0;
    static const std::regex function_expression(
        R"(\.(?:visible\s+)?(?:entry|func)\s+([A-Za-z0-9_$.]+))");

    while (std::getline(input, line)) {
        std::smatch function_match;
        if (std::regex_search(line, function_match, function_expression)) {
            function_name = function_match[1].str();
            excluded = function_name.starts_with("__hbfsim_") ||
                       function_name.starts_with("__bpftime_");
            selected = request.to_patch_kernel.empty() ||
                       function_name == request.to_patch_kernel;
            if (excluded) {
                ++result.coverage.excluded_functions;
            }
        }

        const bool inside_function = !function_name.empty() && brace_depth > 0;
        if (inside_function && selected && !excluded) {
            if (const auto operation = parse_memory_op(line); operation) {
                const auto id = ++scratch_id;
                const auto address = "%hbfsim_addr_" + std::to_string(id);
                const auto status = "%hbfsim_status_" + std::to_string(id);
                const auto fault = "%hbfsim_fault_" + std::to_string(id);
                const bool predicated = !operation->predicate.empty();
                const auto skip_label = "$L__hbfsim_skip_" +
                                        std::to_string(id);
                if (predicated) {
                    const auto predicate_register =
                        operation->predicate.starts_with("@!")
                            ? "@" + operation->predicate.substr(2)
                            : "@!" + operation->predicate.substr(1);
                    output << "    " << predicate_register << " bra "
                           << skip_label << ";\n";
                }
                output << "    .reg .b64 " << address << ";\n";
                output << "    .reg .b32 " << status << ";\n";
                output << "    .reg .pred " << fault << ";\n";
                output << "    mov.b64 " << address << ", "
                       << operation->base_register << ";\n";
                if (operation->offset != 0) {
                    output << "    add.s64 " << address
                           << ", " << address << ", " << operation->offset
                           << ";\n";
                }
                output << "    { // HBFSim call sequence " << id << "\n";
                output << "    .param .b64 %hbfsim_param_addr_" << id << ";\n";
                output << "    .param .b32 %hbfsim_param_bytes_" << id << ";\n";
                output << "    .param .b32 %hbfsim_param_kind_" << id << ";\n";
                output << "    .param .align 8 .b8 %hbfsim_ret_" << id
                       << "[16];\n";
                output << "    st.param.b64 "
                       << "[%hbfsim_param_addr_" << id << "], " << address
                       << ";\n";
                output << "    st.param.b32 "
                       << "[%hbfsim_param_bytes_" << id << "], "
                       << operation->bytes << ";\n";
                output << "    st.param.b32 "
                       << "[%hbfsim_param_kind_" << id << "], "
                       << static_cast<std::uint32_t>(operation->kind) << ";\n";
                output << "    call" << (predicated ? "" : ".uni")
                       << " (%hbfsim_ret_" << id
                       << "), __hbfsim_resolve, (%hbfsim_param_addr_" << id
                       << ", %hbfsim_param_bytes_" << id
                       << ", %hbfsim_param_kind_" << id << ");\n";
                output << "    ld.param.b64 " << address
                       << ", [%hbfsim_ret_" << id << "+0];\n";
                output << "    ld.param.b32 " << status
                       << ", [%hbfsim_ret_" << id << "+8];\n";
                output << "    } // HBFSim call sequence " << id << "\n";
                output << "    setp.ne.u32 " << fault << ", " << status
                       << ", 1;\n";
                output << "    { // HBFSim fault sequence " << id << "\n";
                output << "    .param .b32 %hbfsim_fault_status_" << id
                       << ";\n";
                output << "    st.param.b32 [%hbfsim_fault_status_" << id
                       << "], " << status << ";\n";
                output << "    @" << fault << " call __hbfsim_fault, "
                       << "(%hbfsim_fault_status_" << id << ");\n";
                output << "    } // HBFSim fault sequence " << id << "\n";
                if (predicated) {
                    output << skip_label << ":\n";
                }
                output << replace_address(*operation, address) << '\n';
                ++result.coverage.rewritten_instructions;
                result.modified = true;
                continue;
            }
            std::string opcode;
            if (unsupported_memory_instruction(line, opcode)) {
                ++result.coverage.unsupported_instructions;
                result.coverage.unsupported_opcodes.push_back(opcode);
            }
        }

        output << line << '\n';
        for (const char character : line) {
            if (character == '{') {
                ++brace_depth;
            } else if (character == '}') {
                --brace_depth;
                if (brace_depth == 0) {
                    function_name.clear();
                }
            }
        }
    }
    result.output_ptx = output.str();
    if (result.modified) {
        append_device_helper(result.output_ptx,
                             request.trusted_existing_helper);
    }
    return result;
}

}  // namespace hbfsim::ptx
