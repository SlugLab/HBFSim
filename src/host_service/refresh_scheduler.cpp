#include "refresh_scheduler.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hbfsim::host_service {
namespace {

bool conflicts(std::uint32_t channel, std::uint32_t die,
               std::span<const ApplicationMediaUse> application_reads)
{
    return std::any_of(application_reads.begin(), application_reads.end(),
                       [&](const auto& use) {
                           return use.channel == channel && use.die == die;
                       });
}

}  // namespace

RefreshScheduler::RefreshScheduler(Profile profile,
                                   ThermalReliabilityProfile thermal)
    : profile_(std::move(profile)), thermal_(std::move(thermal))
{
    if (profile_.page_bytes == 0 || profile_.pages_per_block == 0 ||
        profile_.channels == 0 || profile_.dies_per_channel == 0 ||
        thermal_.zone_bytes == 0 || thermal_.refresh_quantum_bytes == 0 ||
        thermal_.refresh_quantum_bytes != profile_.page_bytes) {
        throw std::invalid_argument("invalid refresh scheduler geometry");
    }
    const auto block_bytes = static_cast<std::uint64_t>(profile_.page_bytes) *
                             profile_.pages_per_block;
    if (block_bytes / profile_.page_bytes != profile_.pages_per_block ||
        thermal_.zone_bytes % block_bytes != 0) {
        throw std::invalid_argument("zone must contain complete blocks");
    }
}

void RefreshScheduler::register_range(std::uint64_t base,
                                      std::uint64_t length,
                                      bool contains_valid_data)
{
    const auto block_bytes = static_cast<std::uint64_t>(profile_.page_bytes) *
                             profile_.pages_per_block;
    if (length == 0 || base % profile_.page_bytes != 0 ||
        length % profile_.page_bytes != 0 ||
        base > std::numeric_limits<std::uint64_t>::max() - length) {
        throw std::invalid_argument("refresh range must be page aligned");
    }
    const auto first_block = base / block_bytes + (base % block_bytes != 0);
    const auto end_block = (base + length) / block_bytes;
    for (std::uint64_t global_block = first_block;
         global_block < end_block; ++global_block) {
        const auto address = global_block * block_bytes;
        const auto channel = static_cast<std::uint32_t>(
            global_block % profile_.channels);
        const auto die = static_cast<std::uint32_t>(
            (global_block / profile_.channels) % profile_.dies_per_channel);
        blocks_.push_back({
            .base = address,
            .zone = address / thermal_.zone_bytes,
            .block = global_block,
            .channel = channel,
            .die = die,
            .reliability = {.valid = contains_valid_data},
        });
    }
}

void RefreshScheduler::age(std::int64_t junction_millic,
                           std::chrono::nanoseconds elapsed,
                           std::uint64_t epoch)
{
    if (epoch == 0) throw std::invalid_argument("zero refresh epoch");
    for (auto& block : blocks_) {
        integrate_zone_damage(block.reliability, thermal_, junction_millic,
                              elapsed);
        if (block.eligibility_epoch == 0 &&
            refresh_eligible(block.reliability, thermal_)) {
            block.eligibility_epoch = epoch;
        }
    }
}

void RefreshScheduler::record_read(std::uint64_t address, std::uint64_t bytes)
{
    if (bytes == 0 || address > std::numeric_limits<std::uint64_t>::max() -
                                  (bytes - 1)) {
        throw std::invalid_argument("invalid disturb range");
    }
    const auto end = address + bytes;
    const auto block_bytes = static_cast<std::uint64_t>(profile_.page_bytes) *
                             profile_.pages_per_block;
    for (auto& block : blocks_) {
        if (address < block.base + block_bytes && end > block.base) {
            if (block.reliability.read_disturb_count ==
                std::numeric_limits<std::uint64_t>::max()) {
                throw std::overflow_error("read disturb overflow");
            }
            ++block.reliability.read_disturb_count;
        }
    }
}

std::vector<RefreshAction> RefreshScheduler::plan(
    std::uint64_t now_epoch,
    std::span<const ApplicationMediaUse> application_reads)
{
    if (!inflight_plan_.empty()) return {};
    std::optional<std::size_t> selected;
    auto selected_key = std::tuple{std::numeric_limits<std::uint64_t>::max(),
                                   std::numeric_limits<std::uint32_t>::max(),
                                   std::numeric_limits<std::uint32_t>::max(),
                                   std::numeric_limits<std::uint64_t>::max()};
    for (std::size_t index = 0; index < blocks_.size(); ++index) {
        const auto& block = blocks_[index];
        if (block.inflight || block.eligibility_epoch == 0 ||
            block.eligibility_epoch > now_epoch ||
            conflicts(block.channel, block.die, application_reads)) {
            continue;
        }
        const auto channel_distance = static_cast<std::uint32_t>(
            (block.channel + profile_.channels - next_channel_) %
            profile_.channels);
        const auto die_distance = static_cast<std::uint32_t>(
            (block.die + profile_.dies_per_channel - next_die_) %
            profile_.dies_per_channel);
        const auto key = std::tuple{block.eligibility_epoch, channel_distance,
                                    die_distance, block.block};
        if (key < selected_key) {
            selected_key = key;
            selected = index;
        }
    }
    if (!selected) return {};

    auto& block = blocks_[*selected];
    block.inflight = true;
    inflight_block_ = *selected;
    next_completion_ = 0;
    inflight_plan_.reserve(static_cast<std::size_t>(profile_.pages_per_block) * 2);
    for (std::uint32_t page = 0; page < profile_.pages_per_block; ++page) {
        const auto address = block.base +
                             static_cast<std::uint64_t>(page) *
                                 profile_.page_bytes;
        for (const auto kind : {RefreshActionKind::Read,
                                RefreshActionKind::Write}) {
            inflight_plan_.push_back({
                .action_id = next_action_id_++,
                .kind = kind,
                .address = address,
                .bytes = profile_.page_bytes,
                .channel = block.channel,
                .die = block.die,
                .zone = block.zone,
                .block = block.block,
                .page = page,
                .eligibility_epoch = block.eligibility_epoch,
            });
            if (planned_bytes_ > std::numeric_limits<std::uint64_t>::max() -
                                     profile_.page_bytes) {
                throw std::overflow_error("planned refresh bytes overflow");
            }
            planned_bytes_ += profile_.page_bytes;
        }
    }
    next_channel_ = (block.channel + 1) % profile_.channels;
    next_die_ = (block.die + 1) % profile_.dies_per_channel;
    return inflight_plan_;
}

bool RefreshScheduler::complete(const RefreshAction& action, bool success)
{
    if (inflight_plan_.empty() || next_completion_ >= inflight_plan_.size() ||
        action.action_id != inflight_plan_[next_completion_].action_id) {
        return false;
    }
    auto& block = blocks_[inflight_block_];
    if (!success) {
        block.inflight = false;
        inflight_plan_.clear();
        next_completion_ = 0;
        return true;
    }
    ++next_completion_;
    if (next_completion_ != inflight_plan_.size()) return true;
    commit_refresh(block.reliability);
    ++completed_blocks_;
    block.eligibility_epoch = 0;
    block.inflight = false;
    inflight_plan_.clear();
    next_completion_ = 0;
    return true;
}

std::uint64_t RefreshScheduler::completed_blocks() const noexcept
{
    return completed_blocks_;
}

std::uint64_t RefreshScheduler::maximum_pec() const noexcept
{
    std::uint64_t result = 0;
    for (const auto& block : blocks_) {
        result = std::max(result, block.reliability.maximum_pec);
    }
    return result;
}

std::uint64_t RefreshScheduler::planned_bytes() const noexcept
{
    return planned_bytes_;
}

long double RefreshScheduler::maximum_damage() const noexcept
{
    long double result = 0.0L;
    for (const auto& block : blocks_) {
        result = std::max(result, block.reliability.retention_damage);
    }
    return result;
}

long double RefreshScheduler::average_damage() const noexcept
{
    if (blocks_.empty()) return 0.0L;
    long double total = 0.0L;
    for (const auto& block : blocks_) total += block.reliability.retention_damage;
    return total / static_cast<long double>(blocks_.size());
}

}  // namespace hbfsim::host_service
