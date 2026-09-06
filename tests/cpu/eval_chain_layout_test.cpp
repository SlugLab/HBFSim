#include "../../src/cuda_runtime/device/hbf_device.cuh"

#include <cstdint>
#include <initializer_list>
#include <limits>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) return __LINE__;                                     \
    } while (false)

using namespace hbfsim::device;

constexpr std::uint64_t align_row(std::uint64_t bytes)
{
    return (bytes + alignof(EvalChainRow) - 1) &
           ~(std::uint64_t{alignof(EvalChainRow)} - 1);
}

constexpr EvalChainDiagnosticConfig config(std::uint32_t grid,
                                           std::uint32_t warps,
                                           std::uint32_t hops,
                                           std::uint64_t base = 0x1000)
{
    const auto capacity = std::uint64_t{hops} + 7;
    const auto stride = align_row(sizeof(EvalChainRow) +
                                  capacity * sizeof(EvalChainEvent));
    return {
        kEvalChainDiagnosticMagic,
        kEvalChainDiagnosticVersion,
        sizeof(EvalChainDiagnosticConfig),
        500,
        7,
        grid,
        1,
        1,
        warps * 32,
        1,
        1,
        warps,
        hops,
        std::uint64_t{grid} * warps,
        base,
        std::uint64_t{grid} * warps * stride,
        stride,
        capacity,
    };
}

int main()
{
    static_assert(sizeof(EvalDelayConfig) == 32);
    static_assert(sizeof(EvalDelayCounters) == 48);
    static_assert(sizeof(EvalDelayTrace) == 40);
    static_assert(sizeof(EvalChainDiagnosticConfig) == 104);
    static_assert(sizeof(EvalChainEvent) == 56);
    static_assert(sizeof(EvalChainRow) == 128);

    auto off = config(1, 1, 1);
    off.magic = 0;
    CHECK(eval_chain_diagnostic_action(off, 0) ==
          EvalChainDiagnosticAction::Off);
    for (const auto warps : {1U, 2U, 4U, 8U, 16U}) {
        const auto current = config(5, warps, 64);
        CHECK(eval_chain_config_valid(current));
        CHECK(eval_chain_diagnostic_action(current, 0) ==
              EvalChainDiagnosticAction::Apply);
        CHECK(eval_chain_diagnostic_action(current, kEvalDelayMagic) ==
              EvalChainDiagnosticAction::Reject);
        for (std::uint32_t block = 0; block < current.grid_x; ++block) {
            for (std::uint32_t warp = 0; warp < warps; ++warp) {
                const auto producer = eval_chain_producer(
                    current, block, 0, 0, warp * 32, 0, 0);
                const auto expected_row = std::uint64_t{block} * warps + warp;
                CHECK(producer.valid == 1);
                CHECK(producer.row == expected_row);
                CHECK(producer.thread_id == 32 * expected_row);
                CHECK(producer.warp == warp);
            }
        }
    }

    const auto repeated = config(4097, 1, 1);
    const auto first = eval_chain_producer(repeated, 0, 0, 0, 0, 0, 0);
    const auto alias = eval_chain_producer(repeated, 4096, 0, 0, 0, 0, 0);
    CHECK((first.row & 4095) == (alias.row & 4095));
    CHECK(first.row != alias.row);

    const auto valid = config(3, 4, 16);
    CHECK(eval_chain_producer(valid, 0, 0, 0, 1, 0, 0).valid == 0);
    CHECK(eval_chain_producer(valid, 0, 1, 0, 0, 0, 0).valid == 0);
    CHECK(eval_chain_producer(valid, 0, 0, 0, 0, 1, 0).valid == 0);
    CHECK(eval_chain_producer(valid, valid.grid_x, 0, 0, 0, 0, 0).valid ==
          0);
    CHECK(eval_chain_storage(valid, valid.row_count).valid == 0);

    const auto row0 = eval_chain_storage(valid, 0);
    const auto row1 = eval_chain_storage(valid, 1);
    CHECK(row0.valid == 1 && row1.valid == 1);
    CHECK(row1.row_address - row0.row_address == valid.row_stride);
    CHECK(row0.trace_address == row0.row_address + sizeof(EvalChainRow));
    const auto last = eval_chain_slot(valid, 0, valid.trace_capacity - 1);
    CHECK(last.valid == 1);
    CHECK(last.address + sizeof(EvalChainEvent) <= row1.row_address);
    CHECK(eval_chain_slot(valid, 0, valid.trace_capacity).valid == 0);

    auto malformed = config(1, 1, 1);
    malformed.version = 2;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.launch_epoch = 0;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.grid_y = 2;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.warps_per_block = 3;
    malformed.block_x = 96;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.block_x = 64;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.trace_capacity -= 1;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.row_stride -= alignof(EvalChainRow);
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.storage_bytes -= malformed.row_stride;
    CHECK(!eval_chain_config_valid(malformed));
    malformed = config(1, 1, 1);
    malformed.storage_address += 1;
    CHECK(!eval_chain_config_valid(malformed));

    auto too_many_rows = config(1, 16, 1);
    too_many_rows.grid_x = std::numeric_limits<std::uint32_t>::max();
    too_many_rows.row_count =
        std::uint64_t{too_many_rows.grid_x} * too_many_rows.warps_per_block;
    too_many_rows.storage_bytes =
        too_many_rows.row_count * too_many_rows.row_stride;
    CHECK(!eval_chain_config_valid(too_many_rows));

    auto overflow = config(1, 1, 1);
    overflow.storage_address = std::numeric_limits<std::uint64_t>::max() - 63;
    CHECK(!eval_chain_config_valid(overflow));
    std::uint64_t arithmetic = 0;
    CHECK(!eval_chain_checked_add(
        std::numeric_limits<std::uint64_t>::max(), 1, &arithmetic));
    CHECK(!eval_chain_checked_multiply(
        std::numeric_limits<std::uint64_t>::max(), 2, &arithmetic));

    return 0;
}
