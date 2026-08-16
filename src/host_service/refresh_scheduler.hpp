#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/thermal_reliability.hpp>

#include <chrono>
#include <cstdint>
#include <span>
#include <vector>

namespace hbfsim::host_service {

struct SharedRangeRecord;

enum class RefreshActionKind : std::uint32_t { Read = 0, Write = 1 };

struct ApplicationMediaUse {
    std::uint32_t channel{0};
    std::uint32_t die{0};
};

struct RefreshAction {
    std::uint64_t action_id{0};
    RefreshActionKind kind{RefreshActionKind::Read};
    std::uint64_t address{0};
    std::uint32_t bytes{0};
    std::uint32_t channel{0};
    std::uint32_t die{0};
    std::uint64_t zone{0};
    std::uint64_t block{0};
    std::uint32_t page{0};
    std::uint64_t eligibility_epoch{0};
};

struct RefreshDebtDecay {
    std::uint64_t drained{0};
    std::uint64_t remaining{0};
};

RefreshDebtDecay decay_refresh_debt(
    std::uint64_t debt, std::chrono::nanoseconds elapsed,
    std::uint64_t bandwidth_bytes_per_s, std::uint32_t service_ppm);

class RefreshScheduler {
public:
    RefreshScheduler(Profile profile, ThermalReliabilityProfile thermal);

    void register_range(std::uint64_t base, std::uint64_t length,
                        bool contains_valid_data);
    void register_published_range(const SharedRangeRecord& range,
                                  bool contains_valid_data);
    void register_published_range(const SharedRangeRecord& range);
    void age(std::int64_t junction_millic, std::chrono::nanoseconds elapsed,
             std::uint64_t epoch);
    void record_read(std::uint64_t address, std::uint64_t bytes);
    void record_program(std::uint64_t address, std::uint64_t bytes);

    [[nodiscard]] std::vector<RefreshAction> plan(
        std::uint64_t now_epoch,
        std::span<const ApplicationMediaUse> application_reads);
    bool complete(const RefreshAction& action, bool success);

    [[nodiscard]] std::uint64_t completed_blocks() const noexcept;
    [[nodiscard]] std::uint64_t maximum_pec() const noexcept;
    [[nodiscard]] std::uint64_t average_pec_millionths() const noexcept;
    [[nodiscard]] std::uint64_t planned_bytes() const noexcept;
    [[nodiscard]] long double maximum_damage() const noexcept;
    [[nodiscard]] long double average_damage() const noexcept;

private:
    struct BlockState {
        std::uint64_t base{0};
        std::uint64_t zone{0};
        std::uint64_t block{0};
        std::uint32_t channel{0};
        std::uint32_t die{0};
        ZoneReliability reliability{};
        std::vector<bool> programmed_pages;
        std::uint32_t programmed_page_count{0};
        std::uint64_t eligibility_epoch{0};
        bool inflight{false};
    };

    struct PublishedRangeState {
        std::uint32_t range_id{0};
        std::uint64_t file_offset{0};
        std::uint64_t length{0};
    };

    Profile profile_;
    ThermalReliabilityProfile thermal_;
    std::vector<BlockState> blocks_;
    std::vector<PublishedRangeState> published_ranges_;
    std::vector<RefreshAction> inflight_plan_;
    std::size_t next_completion_{0};
    std::size_t inflight_block_{0};
    std::uint64_t next_action_id_{1};
    std::uint32_t next_channel_{0};
    std::uint32_t next_die_{0};
    std::uint64_t completed_blocks_{0};
    std::uint64_t planned_bytes_{0};
};

}  // namespace hbfsim::host_service
