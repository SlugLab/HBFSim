#include "ptx_async_op.hpp"

#include "ptx_ir.hpp"

#include <algorithm>
#include <charconv>
#include <limits>
#include <regex>
#include <string_view>

namespace hbfsim::ptx {
namespace {

std::vector<std::string> parts(std::string_view value)
{
    std::vector<std::string> result;
    std::size_t begin = 0;
    while (begin <= value.size()) {
        const auto dot = value.find('.', begin);
        result.emplace_back(value.substr(
            begin, dot == std::string_view::npos ? value.size() - begin
                                                  : dot - begin));
        if (dot == std::string_view::npos) break;
        begin = dot + 1;
    }
    return result;
}

bool has(const std::vector<std::string>& values, std::string_view token)
{
    return std::find(values.begin(), values.end(), token) != values.end();
}

std::vector<std::string> registers(std::string_view input)
{
    static const std::regex expression(
        R"((?:%[A-Za-z][A-Za-z0-9_$]*|[A-Za-z_$][A-Za-z0-9_$.]*))");
    std::string text(input);
    std::vector<std::string> result;
    for (std::sregex_iterator it(text.begin(), text.end(), expression), end;
         it != end; ++it) {
        const auto value = it->str();
        if (value != "_" && std::find(result.begin(), result.end(), value) ==
                                result.end()) {
            result.push_back(value);
        }
    }
    return result;
}

std::optional<std::uint32_t> unsigned_immediate(std::string_view value)
{
    std::uint64_t parsed = 0;
    const auto converted =
        std::from_chars(value.data(), value.data() + value.size(), parsed);
    if (converted.ec != std::errc{} ||
        converted.ptr != value.data() + value.size() ||
        parsed > std::numeric_limits<std::uint32_t>::max()) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(parsed);
}

std::string single_address(const std::string& operand)
{
    const auto found = registers(operand);
    if (found.size() != 1) throw ParseError("async address requires one symbol");
    return found.front();
}

std::optional<AsyncInstruction> parse_tma(
    const std::string& opcode, const std::vector<std::string>& operands)
{
    if (!opcode.starts_with("cp.async.bulk.tensor.") &&
        !opcode.starts_with("cp.reduce.async.bulk.tensor.") &&
        !opcode.starts_with("cp.async.bulk.prefetch.tensor.")) {
        return std::nullopt;
    }
    const auto tokens = parts(opcode);
    TmaInstruction result;
    const bool reduction = opcode.starts_with("cp.reduce.");
    const bool prefetch = opcode.starts_with("cp.async.bulk.prefetch.");

    for (const auto& token : tokens) {
        if (token.size() == 2 && token[1] == 'd' && token[0] >= '1' &&
            token[0] <= '5') {
            if (result.dimensions != 0) throw ParseError("duplicate TMA dimension");
            result.dimensions = static_cast<std::uint32_t>(token[0] - '0');
        }
    }
    if (result.dimensions == 0) throw ParseError("TMA dimension is missing");

    const auto global = std::find(tokens.begin(), tokens.end(), "global");
    const auto shared = std::find_if(tokens.begin(), tokens.end(),
                                     [](const auto& token) {
                                         return token.starts_with("shared::");
                                     });
    if (prefetch) {
        result.direction = TmaDirection::Prefetch;
    } else if (global == tokens.end() || shared == tokens.end()) {
        throw ParseError("TMA direction is missing");
    } else if (global < shared) {
        result.direction = TmaDirection::SharedToGlobal;
    } else {
        result.direction = TmaDirection::GlobalToShared;
        result.destination = *shared;
    }

    const bool gather = has(tokens, "tile::gather4");
    const bool scatter = has(tokens, "tile::scatter4");
    const bool im2col = has(tokens, "im2col") || has(tokens, "im2col_no_offs");
    const bool wide = has(tokens, "im2col::w") || has(tokens, "im2col::w::128");
    if (static_cast<int>(gather) + static_cast<int>(scatter) +
            static_cast<int>(im2col) + static_cast<int>(wide) >
        1) {
        throw ParseError("conflicting TMA load modes");
    }
    result.mode = gather   ? TensorMode::Gather4
                  : scatter ? TensorMode::Scatter4
                  : wide    ? TensorMode::Im2colWide
                  : im2col  ? TensorMode::Im2col
                            : TensorMode::Tile;
    if ((gather || scatter) && result.dimensions != 2) {
        throw ParseError("gather4/scatter4 requires 2d TMA");
    }
    if ((im2col || wide) && result.dimensions < 3) {
        throw ParseError("im2col TMA requires 3d-5d");
    }
    if (gather && result.direction != TmaDirection::GlobalToShared) {
        throw ParseError("gather4 is only valid for TMA loads");
    }
    if (scatter && result.direction != TmaDirection::SharedToGlobal) {
        throw ParseError("scatter4 is only valid for TMA stores");
    }

    result.completion = has(tokens, "bulk_group")
                            ? CompletionKind::BulkGroup
                            : (has(tokens, "mbarrier::complete_tx::bytes") ||
                                       result.direction ==
                                           TmaDirection::GlobalToShared
                                   ? CompletionKind::Mbarrier
                                   : CompletionKind::None);
    result.multicast = has(tokens, "multicast::cluster");
    result.cache_hint = has(tokens, "L2::cache_hint");
    if (has(tokens, "cta_group::2")) result.cta_group = 2;
    if (has(tokens, "cta_group::1")) result.cta_group = 1;
    if (result.multicast && result.destination != "shared::cluster") {
        throw ParseError("TMA multicast requires shared::cluster destination");
    }
    static const std::vector<std::string> reductions{
        "add", "and", "or", "xor", "inc", "dec", "min", "max"};
    for (const auto& operation : reductions) {
        if (has(tokens, operation)) result.reduction = operation;
    }
    if (reduction && (result.reduction.empty() ||
                      result.direction != TmaDirection::SharedToGlobal)) {
        throw ParseError("unsupported tensor reduction direction/operator");
    }

    const std::size_t descriptor_operand =
        result.direction == TmaDirection::GlobalToShared ? 1 : 0;
    const std::size_t minimum =
        result.direction == TmaDirection::GlobalToShared ? 3 : 2;
    if (operands.size() < minimum) throw ParseError("TMA has too few operands");
    const auto descriptor_fields = registers(operands[descriptor_operand]);
    if (descriptor_fields.size() < result.dimensions + 1) {
        throw ParseError("TMA descriptor/coordinate operand is malformed");
    }
    result.descriptor = descriptor_fields.front();
    result.coordinates.assign(descriptor_fields.begin() + 1,
                              descriptor_fields.end());
    const auto expected_coordinates =
        result.mode == TensorMode::Gather4 || result.mode == TensorMode::Scatter4
            ? 5U
            : result.dimensions;
    if (result.coordinates.size() != expected_coordinates) {
        throw ParseError("TMA coordinate count does not match mode");
    }
    if (result.direction == TmaDirection::GlobalToShared) {
        result.destination = single_address(operands[0]);
        result.barrier = single_address(operands[2]);
    }
    if (result.multicast) {
        if (operands.size() < 4) throw ParseError("TMA multicast mask is missing");
        result.multicast_mask = operands[3];
    }
    const std::size_t extra = result.cache_hint ? 1 : 0;
    const std::size_t im2col_info =
        result.mode == TensorMode::Im2col || result.mode == TensorMode::Im2colWide
            ? 1
            : 0;
    const std::size_t mask = result.multicast ? 1 : 0;
    if (operands.size() != minimum + extra + im2col_info + mask) {
        throw ParseError("TMA operand count does not match modifiers");
    }
    return AsyncInstruction{std::move(result)};
}

std::optional<AsyncInstruction> parse_barrier(
    const std::string& opcode, const std::vector<std::string>& operands)
{
    if (!opcode.starts_with("mbarrier.")) return std::nullopt;
    BarrierInstruction result;
    std::size_t address_index = 0;
    std::size_t phase_index = std::numeric_limits<std::size_t>::max();
    if (opcode.starts_with("mbarrier.init.")) {
        result.op = BarrierOp::Init;
        if (operands.size() != 2) throw ParseError("mbarrier.init requires 2 operands");
        address_index = 0;
        result.expected_bytes = unsigned_immediate(operands[1]);
    } else if (opcode.starts_with("mbarrier.arrive.expect_tx.")) {
        result.op = BarrierOp::ArriveExpectTx;
        if (operands.size() != 3) throw ParseError("expect_tx requires 3 operands");
        address_index = 1;
        result.expected_bytes = unsigned_immediate(operands[2]);
    } else if (opcode.starts_with("mbarrier.arrive.")) {
        result.op = BarrierOp::Arrive;
        if (operands.size() < 2 || operands.size() > 3) {
            throw ParseError("mbarrier.arrive requires 2 or 3 operands");
        }
        address_index = 1;
    } else if (opcode.starts_with("mbarrier.complete_tx.")) {
        result.op = BarrierOp::CompleteTx;
        if (operands.size() != 2) throw ParseError("complete_tx requires 2 operands");
        address_index = 0;
        result.expected_bytes = unsigned_immediate(operands[1]);
    } else if (opcode.starts_with("mbarrier.test_wait.")) {
        result.op = BarrierOp::TestWait;
        if (operands.size() != 3) throw ParseError("test_wait requires 3 operands");
        address_index = 1;
        phase_index = 2;
    } else if (opcode.starts_with("mbarrier.try_wait.")) {
        result.op = BarrierOp::TryWait;
        if (operands.size() < 3 || operands.size() > 4) {
            throw ParseError("try_wait requires 3 or 4 operands");
        }
        address_index = 1;
        phase_index = 2;
    } else if (opcode.starts_with("mbarrier.inval.")) {
        result.op = BarrierOp::Invalidate;
        if (operands.size() != 1) throw ParseError("mbarrier.inval requires 1 operand");
    } else {
        throw ParseError("unsupported mbarrier operation");
    }
    result.address = single_address(operands[address_index]);
    if (phase_index != std::numeric_limits<std::size_t>::max()) {
        result.phase = operands[phase_index];
    }
    return AsyncInstruction{std::move(result)};
}

std::optional<AsyncInstruction> parse_group(
    const std::string& opcode, const std::vector<std::string>& operands)
{
    BulkGroupInstruction result;
    if (opcode == "cp.async.bulk.commit_group") {
        if (!operands.empty()) throw ParseError("commit_group takes no operands");
        result.op = BulkGroupOp::Commit;
    } else if (opcode == "cp.async.bulk.wait_group" ||
               opcode == "cp.async.bulk.wait_group.read") {
        if (operands.size() != 1) throw ParseError("wait_group requires one limit");
        const auto limit = unsigned_immediate(operands.front());
        if (!limit || *limit > 7) throw ParseError("wait_group limit must be 0-7");
        result.op = opcode.ends_with(".read") ? BulkGroupOp::WaitRead
                                              : BulkGroupOp::Wait;
        result.pending_limit = *limit;
    } else {
        return std::nullopt;
    }
    return AsyncInstruction{result};
}

std::optional<AsyncInstruction> parse_tensormap(
    const std::string& opcode, const std::vector<std::string>& operands)
{
    TensorMapInstruction result;
    if (opcode.starts_with("tensormap.replace.")) {
        const auto tokens = parts(opcode);
        if (tokens.size() < 5 || tokens[1] != "replace" ||
            tokens[2] != "tile") {
            throw ParseError("unsupported tensormap.replace form");
        }
        result.op = TensorMapOp::Replace;
        result.field = tokens[3];
        const bool indexed = result.field == "box_dim" ||
                             result.field == "global_dim" ||
                             result.field == "global_stride" ||
                             result.field == "element_stride";
        if (operands.size() != (indexed ? 3U : 2U)) {
            throw ParseError("tensormap.replace operand count differs");
        }
        result.address = single_address(operands[0]);
        if (indexed) {
            result.ordinal = unsigned_immediate(operands[1]);
            if (!result.ordinal || *result.ordinal >= 5) {
                throw ParseError("tensormap.replace ordinal must be 0-4");
            }
        }
        result.value = operands.back();
    } else if (opcode.starts_with("fence.proxy.tensormap::generic.release.")) {
        if (!operands.empty()) throw ParseError("TensorMap release fence takes no operands");
        result.op = TensorMapOp::FenceRelease;
    } else if (opcode.starts_with("fence.proxy.tensormap::generic.acquire.")) {
        if (operands.size() != 2 || operands[1] != "128") {
            throw ParseError("TensorMap acquire fence requires address, 128");
        }
        result.op = TensorMapOp::FenceAcquire;
        result.address = single_address(operands[0]);
    } else if (opcode == "fence.proxy.async" ||
               opcode.starts_with("fence.proxy.async.")) {
        if (!operands.empty()) throw ParseError("async proxy fence takes no operands");
        result.op = TensorMapOp::FenceAsync;
    } else {
        return std::nullopt;
    }
    return AsyncInstruction{std::move(result)};
}

}  // namespace

std::optional<AsyncInstruction> parse_async_instruction(
    const std::string& opcode, const std::vector<std::string>& operands)
{
    if (auto result = parse_tma(opcode, operands)) return result;
    if (auto result = parse_barrier(opcode, operands)) return result;
    if (auto result = parse_group(opcode, operands)) return result;
    return parse_tensormap(opcode, operands);
}

}  // namespace hbfsim::ptx
