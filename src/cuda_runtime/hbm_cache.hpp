#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <optional>
#include <unordered_map>
#include <vector>

namespace hbfsim::runtime {

struct CacheEviction {
    std::uint64_t logical_page;
    std::uint64_t frame_address;
    std::uint64_t generation;
    bool dirty;
};

class HbmCache {
  public:
    explicit HbmCache(std::vector<std::uint64_t> frame_addresses);

    bool publish(std::uint64_t logical_page,
                 std::uint64_t frame_address);
    [[nodiscard]] std::optional<std::uint64_t> resolve(
        std::uint64_t logical_page);
    [[nodiscard]] std::optional<std::uint64_t> reclaim_eviction(
        std::uint64_t logical_page);
    bool mark_dirty(std::uint64_t logical_page);
    [[nodiscard]] std::size_t dirty_pages() const;
    [[nodiscard]] std::optional<std::uint64_t> free_frame() const;
    [[nodiscard]] std::optional<CacheEviction> begin_eviction();
    [[nodiscard]] std::optional<CacheEviction> begin_eviction_in_range(
        std::uint64_t first_page, std::uint64_t page_count,
        bool dirty_only);
    bool cancel_eviction(const CacheEviction& eviction);
    bool complete_eviction(const CacheEviction& eviction);

  private:
    struct Frame {
        std::uint64_t address;
        std::optional<std::uint64_t> logical_page;
        bool referenced{false};
        bool dirty{false};
        bool evicting{false};
        std::uint64_t generation{1};
    };

    [[nodiscard]] std::optional<CacheEviction> begin_eviction_locked(
        bool range_limited, std::uint64_t first_page,
        std::uint64_t page_count, bool dirty_only);

    mutable std::mutex mutex_;
    std::vector<Frame> frames_;
    std::unordered_map<std::uint64_t, std::size_t> page_to_frame_;
    std::unordered_map<std::uint64_t, std::size_t> evicting_pages_;
    std::unordered_map<std::uint64_t, std::size_t> address_to_frame_;
    std::size_t clock_hand_{0};
    std::size_t dirty_pages_{0};
};

}  // namespace hbfsim::runtime
