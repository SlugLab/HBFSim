#include "tma_transform.hpp"

#include <algorithm>
#include <array>
#include <deque>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace hbfsim::ptx {
namespace {

using RegisterWidths = std::map<std::string, std::uint32_t>;

RegisterWidths register_widths(std::string_view ptx)
{
    RegisterWidths result;
    const std::regex declaration(
        R"(\.reg\s+\.(?:b|u|s|f)(8|16|32|64|128)\s+([^;]+);)");
    const std::regex name(
        R"((%[A-Za-z_$][A-Za-z0-9_$]*)(?:<([0-9]+)>)?)");
    const std::string source{ptx};
    for (std::sregex_iterator declaration_it(source.begin(), source.end(),
                                             declaration), end;
         declaration_it != end; ++declaration_it) {
        const auto width = static_cast<std::uint32_t>(
            std::stoul((*declaration_it)[1].str()));
        const auto names = (*declaration_it)[2].str();
        for (std::sregex_iterator name_it(names.begin(), names.end(), name);
             name_it != end; ++name_it) {
            const auto base = (*name_it)[1].str();
            if ((*name_it)[2].matched) {
                const auto count = std::stoul((*name_it)[2].str());
                for (std::size_t index = 0; index < count; ++index) {
                    result[base + std::to_string(index)] = width;
                }
            } else {
                result[base] = width;
            }
        }
    }
    return result;
}

std::uint32_t register_width(const RegisterWidths& widths,
                             std::string_view operand)
{
    if (!operand.starts_with('%')) return 0;
    const auto found = widths.find(std::string{operand});
    if (found == widths.end()) {
        throw std::runtime_error("TMA operand register has no typed declaration: " +
                                 std::string{operand});
    }
    return found->second;
}

std::vector<std::string> lines(std::string_view ptx)
{
    std::vector<std::string> result;
    std::istringstream input(std::string{ptx});
    std::string line;
    while (std::getline(input, line)) result.push_back(std::move(line));
    return result;
}

std::size_t body_line(const std::vector<std::string>& source,
                      std::string_view kernel)
{
    const std::regex definition(
        R"(\.(?:visible\s+)?(?:entry|func)\s+)" + std::string{kernel} +
        R"((?:\s|\())");
    bool found = false;
    for (std::size_t index = 0; index < source.size(); ++index) {
        if (!found && std::regex_search(source[index], definition)) found = true;
        if (found && source[index].find('{') != std::string::npos) return index;
    }
    throw std::runtime_error("TMA transform could not locate kernel body");
}

std::size_t instruction_end(const std::vector<std::string>& source,
                            std::uint32_t line)
{
    auto index = static_cast<std::size_t>(line - 1);
    while (index < source.size() && source[index].find(';') == std::string::npos) {
        ++index;
    }
    if (index == source.size()) {
        throw std::runtime_error("unterminated TMA instruction");
    }
    return index + 1;
}

std::string token(std::uint32_t id)
{
    return "%hbfsim_tma_" + std::to_string(id) + "_token";
}

std::string inverse_predicate(std::string_view predicate)
{
    if (predicate.empty() || predicate.front() != '@') return {};
    return predicate.starts_with("@!")
               ? "@" + std::string{predicate.substr(2)}
               : "@!" + std::string{predicate.substr(1)};
}

std::uint32_t tensormap_field(std::string_view field)
{
    static const std::map<std::string_view, std::uint32_t> fields{
        {"global_address", 0}, {"rank", 1},
        {"box_dim", 2}, {"global_dim", 3},
        {"global_stride", 4}, {"element_stride", 5},
        {"elemtype", 6}, {"interleave_layout", 7},
        {"swizzle_mode", 8}, {"swizzle_atomicity", 9},
        {"fill_mode", 10},
    };
    const auto found = fields.find(field);
    if (found == fields.end()) {
        throw std::runtime_error("unsupported TensorMap replace field");
    }
    return found->second;
}

std::uint32_t reduction_operation(std::string_view operation)
{
    static const std::map<std::string_view, std::uint32_t> operations{
        {"add", 0}, {"and", 1}, {"or", 2},  {"xor", 3},
        {"inc", 4}, {"dec", 5}, {"min", 6}, {"max", 7},
    };
    if (operation.empty()) return UINT32_MAX;
    const auto found = operations.find(operation);
    if (found == operations.end()) {
        throw std::runtime_error("unsupported TMA reduction operation");
    }
    return found->second;
}

std::string emit_tensormap_replace_begin(
    const Instruction& instruction, const TensorMapInstruction& update,
    const RegisterWidths& widths)
{
    const auto id = instruction.instruction_id;
    const bool narrow_address = register_width(widths, update.address) == 32;
    const bool shared_address = update.state_space == "shared::cta";
    std::ostringstream output;
    output << "    // HBFSim TensorMap replace begin " << id << "\n"
           << (instruction.predicate.empty()
                   ? ""
                   : "    " + inverse_predicate(instruction.predicate) +
                         " bra.uni $hbfsim_tmap_" + std::to_string(id) +
                         "_predicate_false;\n")
           << "    {\n"
           << "    .param .b64 %hbfsim_tmap_" << id << "_address;\n"
           << "    .param .b64 %hbfsim_tmap_" << id << "_return;\n"
           << (narrow_address
                   ? "    .reg .b64 %hbfsim_tmap_" + std::to_string(id) +
                         "_address64;\n" +
                         (shared_address
                              ? "    .reg .b64 %hbfsim_tmap_" +
                                    std::to_string(id) +
                                    "_shared64;\n    cvt.u64.u32 "
                                    "%hbfsim_tmap_" + std::to_string(id) +
                                    "_shared64, " + update.address +
                                    ";\n    cvta.shared.u64 %hbfsim_tmap_" +
                                    std::to_string(id) + "_address64, "
                                    "%hbfsim_tmap_" + std::to_string(id) +
                                    "_shared64;\n"
                              : "    cvt.u64.u32 %hbfsim_tmap_" +
                                    std::to_string(id) + "_address64, " +
                                    update.address + ";\n")
                   : "")
           << "    st.param.b64 [%hbfsim_tmap_" << id << "_address], "
           << (narrow_address
                   ? "%hbfsim_tmap_" + std::to_string(id) + "_address64"
                   : update.address)
           << ";\n"
           << "    call.uni (%hbfsim_tmap_" << id
           << "_return), __hbfsim_tensormap_replace_begin, "
           << "(%hbfsim_tmap_" << id << "_address);\n"
           << "    ld.param.b64 %hbfsim_tmap_" << id
           << "_token, [%hbfsim_tmap_" << id << "_return];\n"
           << "    }\n"
           << "    setp.ne.u64 %hbfsim_tmap_" << id << "_valid, "
           << "%hbfsim_tmap_" << id << "_token, 0;\n"
           << "    @!%hbfsim_tmap_" << id << "_valid trap;\n";
    return output.str();
}

std::string emit_tensormap_replace_commit(
    const Instruction& instruction, const TensorMapInstruction& update,
    const RegisterWidths& widths)
{
    const auto id = instruction.instruction_id;
    const auto value_width = register_width(widths, update.value);
    const bool narrow_value = value_width == 16 || value_width == 32;
    const bool narrow_address = register_width(widths, update.address) == 32;
    const bool shared_address = update.state_space == "shared::cta";
    std::ostringstream output;
    output << "    // HBFSim TensorMap replace commit " << id << "\n"
           << "    {\n"
           << "    .param .b64 %hbfsim_tmap_commit_" << id << "_token;\n"
           << "    .param .b64 %hbfsim_tmap_commit_" << id << "_address;\n"
           << "    .param .b32 %hbfsim_tmap_commit_" << id << "_field;\n"
           << "    .param .b32 %hbfsim_tmap_commit_" << id << "_ordinal;\n"
           << "    .param .b64 %hbfsim_tmap_commit_" << id << "_value;\n"
           << "    .param .b32 %hbfsim_tmap_commit_" << id << "_return;\n"
           << (narrow_address
                   ? "    .reg .b64 %hbfsim_tmap_commit_" +
                         std::to_string(id) +
                         "_address64;\n" +
                         (shared_address
                              ? "    .reg .b64 %hbfsim_tmap_commit_" +
                                    std::to_string(id) +
                                    "_shared64;\n    cvt.u64.u32 "
                                    "%hbfsim_tmap_commit_" +
                                    std::to_string(id) + "_shared64, " +
                                    update.address +
                                    ";\n    cvta.shared.u64 "
                                    "%hbfsim_tmap_commit_" +
                                    std::to_string(id) + "_address64, "
                                    "%hbfsim_tmap_commit_" +
                                    std::to_string(id) + "_shared64;\n"
                              : "    cvt.u64.u32 "
                                    "%hbfsim_tmap_commit_" +
                                    std::to_string(id) + "_address64, " +
                                    update.address + ";\n")
                   : "")
           << (narrow_value
                   ? "    .reg .b64 %hbfsim_tmap_commit_" +
                         std::to_string(id) +
                         "_value64;\n    cvt.u64.u" +
                         std::to_string(value_width) +
                         " %hbfsim_tmap_commit_" + std::to_string(id) +
                         "_value64, " + update.value + ";\n"
                   : "")
           << "    st.param.b64 [%hbfsim_tmap_commit_" << id
           << "_token], %hbfsim_tmap_" << id << "_token;\n"
           << "    st.param.b64 [%hbfsim_tmap_commit_" << id
           << "_address], "
           << (narrow_address
                   ? "%hbfsim_tmap_commit_" + std::to_string(id) +
                         "_address64"
                   : update.address)
           << ";\n"
           << "    st.param.b32 [%hbfsim_tmap_commit_" << id
           << "_field], " << tensormap_field(update.field) << ";\n"
           << "    st.param.b32 [%hbfsim_tmap_commit_" << id
           << "_ordinal], " << update.ordinal.value_or(0) << ";\n"
           << "    st.param.b64 [%hbfsim_tmap_commit_" << id
           << "_value], "
           << (narrow_value ? "%hbfsim_tmap_commit_" + std::to_string(id) +
                                  "_value64"
                            : update.value)
           << ";\n"
           << "    call.uni (%hbfsim_tmap_commit_" << id
           << "_return), __hbfsim_tensormap_replace_commit, "
           << "(%hbfsim_tmap_commit_" << id << "_token, "
           << "%hbfsim_tmap_commit_" << id << "_address, "
           << "%hbfsim_tmap_commit_" << id << "_field, "
           << "%hbfsim_tmap_commit_" << id << "_ordinal, "
           << "%hbfsim_tmap_commit_" << id << "_value);\n"
           << "    ld.param.b32 %hbfsim_tmap_commit_" << id
           << "_status, [%hbfsim_tmap_commit_" << id << "_return];\n"
           << "    }\n"
           << "    setp.ne.u32 %hbfsim_tmap_commit_" << id
           << "_valid, %hbfsim_tmap_commit_" << id << "_status, 0;\n"
           << "    @!%hbfsim_tmap_commit_" << id << "_valid trap;\n";
    if (!instruction.predicate.empty()) {
        output << "$hbfsim_tmap_" << id << "_predicate_false:\n";
    }
    return output.str();
}

std::string emit_tensormap_acquire(
    const Instruction& instruction, const TensorMapInstruction& acquire,
    const RegisterWidths& widths)
{
    const auto id = instruction.instruction_id;
    const bool narrow_address = register_width(widths, acquire.address) == 32;
    std::ostringstream output;
    output << "    // HBFSim TensorMap acquire " << id << "\n"
           << (instruction.predicate.empty()
                   ? ""
                   : "    " + inverse_predicate(instruction.predicate) +
                         " bra.uni $hbfsim_tmap_acquire_" +
                         std::to_string(id) + "_predicate_false;\n")
           << "    {\n"
           << "    .param .b64 %hbfsim_tmap_acquire_" << id
           << "_address;\n"
           << "    .param .b32 %hbfsim_tmap_acquire_" << id
           << "_return;\n"
           << (narrow_address
                   ? "    .reg .b64 %hbfsim_tmap_acquire_" +
                         std::to_string(id) +
                         "_address64;\n    cvt.u64.u32 %hbfsim_tmap_acquire_" +
                         std::to_string(id) + "_address64, " +
                         acquire.address + ";\n"
                   : "")
           << "    st.param.b64 [%hbfsim_tmap_acquire_" << id
           << "_address], "
           << (narrow_address
                   ? "%hbfsim_tmap_acquire_" + std::to_string(id) +
                         "_address64"
                   : acquire.address)
           << ";\n"
           << "    call.uni (%hbfsim_tmap_acquire_" << id
           << "_return), __hbfsim_tensormap_acquire, "
           << "(%hbfsim_tmap_acquire_" << id << "_address);\n"
           << "    ld.param.b32 %hbfsim_tmap_acquire_" << id
           << "_status, [%hbfsim_tmap_acquire_" << id << "_return];\n"
           << "    }\n"
           << "    setp.ne.u32 %hbfsim_tmap_acquire_" << id
           << "_valid, %hbfsim_tmap_acquire_" << id << "_status, 0;\n"
           << "    @!%hbfsim_tmap_acquire_" << id << "_valid trap;\n";
    if (!instruction.predicate.empty()) {
        output << "$hbfsim_tmap_acquire_" << id
               << "_predicate_false:\n";
    }
    return output.str();
}

std::string emit_tensormap_copy_begin(
    const Instruction& instruction, const TensorMapInstruction& copy,
    const RegisterWidths& widths)
{
    const auto id = instruction.instruction_id;
    const bool narrow_source = register_width(widths, copy.source) == 32;
    std::ostringstream output;
    output << "    // HBFSim TensorMap copy begin " << id << "\n"
           << (instruction.predicate.empty()
                   ? ""
                   : "    " + inverse_predicate(instruction.predicate) +
                         " bra.uni $hbfsim_tmap_copy_" +
                         std::to_string(id) + "_predicate_false;\n")
           << "    {\n"
           << "    .param .b64 %hbfsim_tmap_copy_" << id << "_source;\n"
           << "    .param .b64 %hbfsim_tmap_copy_" << id << "_return;\n"
           << (narrow_source
                   ? "    .reg .b64 %hbfsim_tmap_copy_" +
                         std::to_string(id) +
                         "_source64;\n    .reg .b64 %hbfsim_tmap_copy_" +
                         std::to_string(id) +
                         "_shared64;\n    cvt.u64.u32 %hbfsim_tmap_copy_" +
                         std::to_string(id) + "_shared64, " + copy.source +
                         ";\n    cvta.shared.u64 %hbfsim_tmap_copy_" +
                         std::to_string(id) + "_source64, "
                         "%hbfsim_tmap_copy_" + std::to_string(id) +
                         "_shared64;\n"
                   : "")
           << "    st.param.b64 [%hbfsim_tmap_copy_" << id << "_source], "
           << (narrow_source ? "%hbfsim_tmap_copy_" + std::to_string(id) +
                                   "_source64"
                             : copy.source)
           << ";\n"
           << "    call.uni (%hbfsim_tmap_copy_" << id
           << "_return), __hbfsim_tensormap_copy_begin, "
           << "(%hbfsim_tmap_copy_" << id << "_source);\n"
           << "    ld.param.b64 %hbfsim_tmap_copy_" << id
           << "_token, [%hbfsim_tmap_copy_" << id << "_return];\n"
           << "    }\n"
           << "    setp.ne.u64 %hbfsim_tmap_copy_" << id << "_valid, "
           << "%hbfsim_tmap_copy_" << id << "_token, 0;\n"
           << "    @!%hbfsim_tmap_copy_" << id << "_valid trap;\n";
    return output.str();
}

std::string emit_tensormap_copy_commit(
    const Instruction& instruction, const TensorMapInstruction& copy,
    const RegisterWidths& widths)
{
    const auto id = instruction.instruction_id;
    const bool narrow_destination =
        register_width(widths, copy.address) == 32;
    std::ostringstream output;
    output << "    // HBFSim TensorMap copy commit " << id << "\n"
           << "    {\n"
           << "    .param .b64 %hbfsim_tmap_copy_commit_" << id
           << "_token;\n"
           << "    .param .b64 %hbfsim_tmap_copy_commit_" << id
           << "_destination;\n"
           << "    .param .b32 %hbfsim_tmap_copy_commit_" << id
           << "_return;\n"
           << (narrow_destination
                   ? "    .reg .b64 %hbfsim_tmap_copy_commit_" +
                         std::to_string(id) +
                         "_destination64;\n    cvt.u64.u32 "
                         "%hbfsim_tmap_copy_commit_" + std::to_string(id) +
                         "_destination64, " + copy.address + ";\n"
                   : "")
           << "    st.param.b64 [%hbfsim_tmap_copy_commit_" << id
           << "_token], %hbfsim_tmap_copy_" << id << "_token;\n"
           << "    st.param.b64 [%hbfsim_tmap_copy_commit_" << id
           << "_destination], "
           << (narrow_destination
                   ? "%hbfsim_tmap_copy_commit_" + std::to_string(id) +
                         "_destination64"
                   : copy.address)
           << ";\n"
           << "    call.uni (%hbfsim_tmap_copy_commit_" << id
           << "_return), __hbfsim_tensormap_copy_commit, "
           << "(%hbfsim_tmap_copy_commit_" << id << "_token, "
           << "%hbfsim_tmap_copy_commit_" << id << "_destination);\n"
           << "    ld.param.b32 %hbfsim_tmap_copy_commit_" << id
           << "_status, [%hbfsim_tmap_copy_commit_" << id << "_return];\n"
           << "    }\n"
           << "    setp.ne.u32 %hbfsim_tmap_copy_commit_" << id
           << "_valid, %hbfsim_tmap_copy_commit_" << id << "_status, 0;\n"
           << "    @!%hbfsim_tmap_copy_commit_" << id << "_valid trap;\n";
    if (!instruction.predicate.empty()) {
        output << "$hbfsim_tmap_copy_" << id << "_predicate_false:\n";
    }
    return output.str();
}

std::string emit_issue(const Instruction& instruction,
                       const TmaInstruction& tma,
                       const RegisterWidths& widths)
{
    const auto id = instruction.instruction_id;
    const bool software_only = tma.cta_group == 2;
    const auto direction = tma.direction == TmaDirection::SharedToGlobal
                               ? 1U
                               : tma.direction == TmaDirection::Prefetch ? 2U
                                                                         : 0U;
    const auto barrier = tma.barrier.empty() ? "0" : tma.barrier;
    const bool narrow_barrier = register_width(widths, barrier) == 32;
    const auto mask = tma.multicast ? tma.multicast_mask : "0";
    const bool narrow_mask = register_width(widths, mask) == 16;
    const auto shared = tma.shared_address.empty() ? "0" : tma.shared_address;
    const bool narrow_shared = register_width(widths, shared) == 32;
    std::array<std::string, 3> offsets{"0", "0", "0"};
    std::array<bool, 3> narrow_offsets{};
    for (std::size_t index = 0; index < tma.im2col_offsets.size(); ++index) {
        offsets[index] = tma.im2col_offsets[index];
        narrow_offsets[index] = register_width(widths, offsets[index]) == 16;
    }
    const auto shared_scope =
        tma.shared_state_space == "shared::cluster" ? 1U : 0U;
    std::ostringstream output;
    output << "    // HBFSim TMA issue " << id << "\n"
           << (instruction.predicate.empty()
                   ? ""
                   : "    " + inverse_predicate(instruction.predicate) +
                         " bra.uni $hbfsim_tma_" + std::to_string(id) +
                         "_predicate_false;\n")
           << "    {\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_descriptor;\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_shared;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_shared_scope;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_cta_group;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_instruction;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_direction;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_access;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_reduction;\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_barrier;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_mask;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_coordinate_0;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_coordinate_1;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_coordinate_2;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_coordinate_3;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_coordinate_4;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_offset_0;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_offset_1;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_offset_2;\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_return;\n"
           << (narrow_barrier
                   ? "    .reg .b64 %hbfsim_tma_" + std::to_string(id) +
                         "_barrier64;\n    cvt.u64.u32 %hbfsim_tma_" +
                         std::to_string(id) + "_barrier64, " + barrier + ";\n"
                   : "")
           << (narrow_shared
                   ? "    .reg .b64 %hbfsim_tma_" + std::to_string(id) +
                         "_shared64;\n    cvt.u64.u32 %hbfsim_tma_" +
                         std::to_string(id) + "_shared64, " + shared + ";\n"
                   : "")
           << (narrow_mask
                   ? "    .reg .b32 %hbfsim_tma_" + std::to_string(id) +
                         "_mask32;\n    cvt.u32.u16 %hbfsim_tma_" +
                         std::to_string(id) + "_mask32, " + mask + ";\n"
                   : "");
    for (std::size_t index = 0; index < offsets.size(); ++index) {
        if (narrow_offsets[index]) {
            output << "    .reg .b32 %hbfsim_tma_" << id << "_offset_"
                   << index << "_32;\n    cvt.u32.u16 %hbfsim_tma_" << id
                   << "_offset_" << index << "_32, " << offsets[index]
                   << ";\n";
        }
    }
    output
           << "    st.param.b64 [%hbfsim_tma_" << id << "_descriptor], "
           << tma.descriptor << ";\n"
           << "    st.param.b64 [%hbfsim_tma_" << id << "_shared], "
           << (narrow_shared ? "%hbfsim_tma_" + std::to_string(id) +
                                   "_shared64"
                             : shared)
           << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_shared_scope], "
           << shared_scope << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_cta_group], "
           << tma.cta_group << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_instruction], "
           << id << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_direction], "
           << direction << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_access], "
           << static_cast<std::uint32_t>(tma.mode) << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_reduction], "
           << reduction_operation(tma.reduction) << ";\n"
           << "    st.param.b64 [%hbfsim_tma_" << id << "_barrier], "
           << (narrow_barrier ? "%hbfsim_tma_" + std::to_string(id) +
                                    "_barrier64"
                              : barrier)
           << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_mask], "
           << (narrow_mask ? "%hbfsim_tma_" + std::to_string(id) +
                                 "_mask32"
                           : mask)
           << ";\n";
    for (std::size_t coordinate = 0; coordinate < 5; ++coordinate) {
        output << "    st.param.b32 [%hbfsim_tma_" << id
               << "_coordinate_" << coordinate << "], "
               << (coordinate < tma.coordinates.size()
                       ? tma.coordinates[coordinate]
                       : "0")
               << ";\n";
    }
    for (std::size_t index = 0; index < offsets.size(); ++index) {
        output << "    st.param.b32 [%hbfsim_tma_" << id << "_offset_"
               << index << "], "
               << (narrow_offsets[index]
                       ? "%hbfsim_tma_" + std::to_string(id) + "_offset_" +
                             std::to_string(index) + "_32"
                       : offsets[index])
               << ";\n";
    }
    output
           << "    call.uni (%hbfsim_tma_" << id
           << "_return), __hbfsim_tma_issue, (%hbfsim_tma_" << id
           << "_descriptor, %hbfsim_tma_" << id
           << "_shared, %hbfsim_tma_" << id << "_shared_scope, %hbfsim_tma_" << id
           << "_cta_group, %hbfsim_tma_" << id
           << "_instruction, %hbfsim_tma_" << id
           << "_direction, %hbfsim_tma_" << id << "_access, %hbfsim_tma_" << id
           << "_reduction, %hbfsim_tma_" << id
           << "_barrier, %hbfsim_tma_" << id << "_mask, %hbfsim_tma_" << id
           << "_coordinate_0, %hbfsim_tma_" << id
           << "_coordinate_1, %hbfsim_tma_" << id
           << "_coordinate_2, %hbfsim_tma_" << id
           << "_coordinate_3, %hbfsim_tma_" << id
           << "_coordinate_4, %hbfsim_tma_" << id
           << "_offset_0, %hbfsim_tma_" << id
           << "_offset_1, %hbfsim_tma_" << id << "_offset_2);\n"
           << "    ld.param.b64 " << token(id) << ", [%hbfsim_tma_" << id
           << "_return];\n"
           << "    }\n";
    output << "    setp.ne.u64 %hbfsim_tma_" << id << "_valid, "
           << token(id) << ", 0;\n"
           << "    @!%hbfsim_tma_" << id << "_valid trap;\n"
           << "    setp.lt.s64 %hbfsim_tma_" << id << "_software, "
           << token(id) << ", 0;\n";
    if (software_only) {
        output << "    @!%hbfsim_tma_" << id << "_software trap;\n"
               << "    bra.uni $hbfsim_tma_" << id << "_native_done;\n";
    } else {
        output << "    @%hbfsim_tma_" << id
               << "_software bra.uni $hbfsim_tma_" << id
               << "_native_done;\n";
    }
    if (!instruction.predicate.empty()) {
        output << "$hbfsim_tma_" << id << "_predicate_false:\n";
        if (software_only) {
            output << "    bra.uni $hbfsim_tma_" << id
                   << "_native_done;\n";
        }
    }
    return output.str();
}

std::string emit_barrier_poll(const Instruction& wait,
                              std::uint32_t issue_id,
                              const std::string& barrier)
{
    const auto predicate = wait.operands.empty() ? std::string{}
                                                  : wait.operands.front();
    static const std::regex predicate_name(
        R"(^%?[A-Za-z_$][A-Za-z0-9_$]*$)");
    if (!std::regex_match(predicate, predicate_name)) {
        throw std::runtime_error("TMA barrier wait predicate is not a register");
    }
    const auto id = wait.instruction_id;
    const bool narrow_barrier = barrier.starts_with("%r") &&
                                !barrier.starts_with("%rd");
    std::ostringstream output;
    output << "    // HBFSim conjunctive TMA barrier poll " << issue_id << "\n"
           << "    {\n"
           << "    .param .b64 %hbfsim_tma_poll_" << id << "_token;\n"
           << "    .param .b64 %hbfsim_tma_poll_" << id << "_barrier;\n"
           << "    .param .b32 %hbfsim_tma_poll_" << id << "_return;\n"
           << (narrow_barrier
                   ? "    .reg .b64 %hbfsim_tma_poll_" +
                         std::to_string(id) +
                         "_barrier64;\n    cvt.u64.u32 %hbfsim_tma_poll_" +
                         std::to_string(id) + "_barrier64, " + barrier + ";\n"
                   : "")
           << "    st.param.b64 [%hbfsim_tma_poll_" << id << "_token], "
           << token(issue_id) << ";\n"
           << "    st.param.b64 [%hbfsim_tma_poll_" << id << "_barrier], "
           << (narrow_barrier
                   ? "%hbfsim_tma_poll_" + std::to_string(id) + "_barrier64"
                   : barrier)
           << ";\n"
           << "    call.uni (%hbfsim_tma_poll_" << id
           << "_return), __hbfsim_tma_barrier_poll, "
           << "(%hbfsim_tma_poll_" << id << "_token, %hbfsim_tma_poll_"
           << id << "_barrier);\n"
           << "    ld.param.b32 %hbfsim_tma_poll_" << id
           << "_ready, [%hbfsim_tma_poll_" << id << "_return];\n"
           << "    and.b32 %hbfsim_tma_poll_" << id
           << "_software_flag, %hbfsim_tma_poll_" << id << "_ready, 2;\n"
           << "    and.b32 %hbfsim_tma_poll_" << id
           << "_ready, %hbfsim_tma_poll_" << id << "_ready, 1;\n"
           << "    setp.ne.u32 %hbfsim_tma_poll_" << id
           << "_predicate, %hbfsim_tma_poll_" << id << "_ready, 0;\n"
           << "    setp.ne.u32 %hbfsim_tma_poll_" << id
           << "_software, %hbfsim_tma_poll_" << id
           << "_software_flag, 0;\n"
           << "    or.pred %hbfsim_tma_poll_" << id << "_combined, "
           << predicate << ", %hbfsim_tma_poll_" << id << "_software;\n"
           << "    and.pred " << predicate << ", %hbfsim_tma_poll_" << id
           << "_predicate, %hbfsim_tma_poll_" << id << "_combined;\n"
           << "    }\n";
    return output.str();
}

std::string emit_group_wait(const Instruction& wait,
                            const std::vector<std::uint32_t>& issues,
                            bool read_only)
{
    std::ostringstream output;
    for (const auto issue : issues) {
        output << "    // HBFSim TMA bulk-group "
               << (read_only ? "read " : "full ") << "wait " << issue
               << "\n    {\n"
               << "    .param .b64 %hbfsim_tma_group_" << wait.instruction_id
               << "_" << issue << "_token;\n"
               << "    .param .b32 %hbfsim_tma_group_" << wait.instruction_id
               << "_" << issue << "_read;\n"
               << "    st.param.b64 [%hbfsim_tma_group_"
               << wait.instruction_id << "_" << issue << "_token], "
               << token(issue) << ";\n"
               << "    st.param.b32 [%hbfsim_tma_group_"
               << wait.instruction_id << "_" << issue << "_read], "
               << (read_only ? 1 : 0) << ";\n"
               << "    call.uni __hbfsim_tma_wait_group, "
               << "(%hbfsim_tma_group_" << wait.instruction_id << "_"
               << issue << "_token, %hbfsim_tma_group_"
               << wait.instruction_id << "_" << issue << "_read);\n"
               << "    }\n";
    }
    return output.str();
}

}  // namespace

TmaTransformResult transform_tma(std::string_view ptx,
                                 std::string_view kernel,
                                 AsyncObjectLimits limits)
{
    TmaTransformResult result;
    const auto module = parse_module(ptx);
    const auto& function = module.function(kernel);
    result.plan = analyze_async_objects(function, limits);
    if (!result.plan.exact_safe()) {
        result.rejection_reason = result.plan.rejection_reasons.begin()->second;
        return result;
    }
    if (result.plan.tma_instruction_ids.empty()) {
        result.output_ptx = std::string{ptx};
        return result;
    }
    const auto source = lines(ptx);
    const auto widths = register_widths(ptx);
    std::map<std::size_t, std::string> before;
    std::map<std::size_t, std::string> after;
    std::set<std::size_t> suppressed;
    std::ostringstream declarations;
    std::ostringstream initializations;
    declarations << "    // HBFSim TMA async state for " << kernel << "\n";

    std::map<std::string, std::uint32_t> barrier_issue;
    std::vector<std::uint32_t> uncommitted_store_issues;
    std::deque<std::vector<std::uint32_t>> committed_store_groups;
    for (const auto& instruction : function.instructions) {
        if (!instruction.async) continue;
        if (const auto* tensor_map =
                std::get_if<TensorMapInstruction>(&*instruction.async)) {
            if (tensor_map->op == TensorMapOp::Replace) {
                declarations << "    .reg .b64 %hbfsim_tmap_"
                             << instruction.instruction_id << "_token;\n"
                             << "    .reg .pred %hbfsim_tmap_"
                             << instruction.instruction_id << "_valid;\n"
                             << "    .reg .b32 %hbfsim_tmap_commit_"
                             << instruction.instruction_id << "_status;\n"
                             << "    .reg .pred %hbfsim_tmap_commit_"
                             << instruction.instruction_id << "_valid;\n";
                initializations << "    mov.u64 %hbfsim_tmap_"
                                << instruction.instruction_id
                                << "_token, 0;\n";
                before[instruction.location.line] +=
                    emit_tensormap_replace_begin(instruction, *tensor_map,
                                                 widths);
                after[instruction_end(source, instruction.location.line)] +=
                    emit_tensormap_replace_commit(instruction, *tensor_map,
                                                  widths);
                ++result.rewritten_instructions;
            } else if (tensor_map->op == TensorMapOp::CopyFence) {
                declarations << "    .reg .b64 %hbfsim_tmap_copy_"
                             << instruction.instruction_id << "_token;\n"
                             << "    .reg .pred %hbfsim_tmap_copy_"
                             << instruction.instruction_id << "_valid;\n"
                             << "    .reg .b32 %hbfsim_tmap_copy_commit_"
                             << instruction.instruction_id << "_status;\n"
                             << "    .reg .pred %hbfsim_tmap_copy_commit_"
                             << instruction.instruction_id << "_valid;\n";
                initializations << "    mov.u64 %hbfsim_tmap_copy_"
                                << instruction.instruction_id
                                << "_token, 0;\n";
                before[instruction.location.line] +=
                    emit_tensormap_copy_begin(instruction, *tensor_map,
                                              widths);
                after[instruction_end(source, instruction.location.line)] +=
                    emit_tensormap_copy_commit(instruction, *tensor_map,
                                               widths);
                ++result.rewritten_instructions;
            } else if (tensor_map->op == TensorMapOp::FenceAcquire) {
                declarations << "    .reg .b32 %hbfsim_tmap_acquire_"
                             << instruction.instruction_id << "_status;\n"
                             << "    .reg .pred %hbfsim_tmap_acquire_"
                             << instruction.instruction_id << "_valid;\n";
                after[instruction_end(source, instruction.location.line)] +=
                    emit_tensormap_acquire(instruction, *tensor_map, widths);
                ++result.rewritten_instructions;
            }
        } else if (const auto* tma =
                       std::get_if<TmaInstruction>(&*instruction.async)) {
            declarations << "    .reg .b64 " << token(instruction.instruction_id)
                         << ";\n    .reg .pred %hbfsim_tma_"
                         << instruction.instruction_id << "_valid;\n"
                         << "    .reg .pred %hbfsim_tma_"
                         << instruction.instruction_id << "_software;\n";
            initializations << "    mov.u64 "
                            << token(instruction.instruction_id) << ", 1;\n";
            before[instruction.location.line] +=
                emit_issue(instruction, *tma, widths);
            const auto end = instruction_end(source,
                                             instruction.location.line);
            after[end] +=
                "$hbfsim_tma_" +
                std::to_string(instruction.instruction_id) +
                "_native_done:\n";
            if (tma->cta_group == 2) {
                for (auto line = instruction.location.line;
                     line <= end; ++line) {
                    suppressed.insert(line);
                }
            }
            if (tma->completion == CompletionKind::Mbarrier) {
                barrier_issue[tma->barrier] = instruction.instruction_id;
            } else if (tma->completion == CompletionKind::BulkGroup) {
                uncommitted_store_issues.push_back(
                    instruction.instruction_id);
            }
            ++result.rewritten_instructions;
        } else if (const auto* barrier =
                       std::get_if<BarrierInstruction>(&*instruction.async)) {
            if (barrier->op != BarrierOp::TestWait &&
                barrier->op != BarrierOp::TryWait) {
                continue;
            }
            const auto issue = barrier_issue.find(barrier->address);
            if (issue == barrier_issue.end()) {
                result.rejection_reason = "ambiguous_mbarrier_phase";
                return result;
            }
            declarations << "    .reg .b32 %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_ready;\n"
                         << "    .reg .b32 %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_software_flag;\n"
                         << "    .reg .pred %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_predicate;\n"
                         << "    .reg .pred %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_software;\n"
                         << "    .reg .pred %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_combined;\n";
            after[instruction_end(source, instruction.location.line)] +=
                emit_barrier_poll(instruction, issue->second,
                                  barrier->address);
            ++result.rewritten_instructions;
        } else if (const auto* group =
                       std::get_if<BulkGroupInstruction>(&*instruction.async)) {
            if (group->op == BulkGroupOp::Commit) {
                after[instruction_end(source, instruction.location.line)] +=
                    "    call.uni __hbfsim_tma_commit_group, ();\n";
                committed_store_groups.push_back(
                    std::move(uncommitted_store_issues));
                uncommitted_store_issues.clear();
            } else {
                const auto retire = committed_store_groups.size() >
                                            group->pending_limit
                                        ? committed_store_groups.size() -
                                              group->pending_limit
                                        : 0;
                std::vector<std::uint32_t> waited_issues;
                for (std::size_t index = 0; index < retire; ++index) {
                    waited_issues.insert(waited_issues.end(),
                                         committed_store_groups[index].begin(),
                                         committed_store_groups[index].end());
                }
                after[instruction_end(source, instruction.location.line)] +=
                    emit_group_wait(instruction, waited_issues,
                                    group->op == BulkGroupOp::WaitRead);
                if (group->op == BulkGroupOp::Wait) {
                    committed_store_groups.erase(
                        committed_store_groups.begin(),
                        committed_store_groups.begin() + retire);
                }
            }
            ++result.rewritten_instructions;
        }
    }
    before[body_line(source, kernel) + 2] = declarations.str() +
        initializations.str() +
        before[body_line(source, kernel) + 2];

    std::ostringstream output;
    for (std::size_t index = 1; index <= source.size(); ++index) {
        if (before.contains(index)) output << before[index];
        if (!suppressed.contains(index)) output << source[index - 1] << '\n';
        if (after.contains(index)) output << after[index];
    }
    result.output_ptx = output.str();
    result.modified = true;
    return result;
}

}  // namespace hbfsim::ptx
