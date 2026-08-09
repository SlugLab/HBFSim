#pragma once

#include <hbfsim/protocol.hpp>

#include <cstdint>
#include <mutex>
#include <optional>
#include <unordered_map>

namespace hbfsim {

struct PageReservation {
    bool owner;
    std::uint64_t generation;
};

struct ResolvedPage {
    std::uint64_t frame;
    std::uint64_t generation;
    PageState state;
};

class PageDirectory {
public:
    PageReservation lookup_or_reserve(std::uint64_t logical_page,
                                      std::uint64_t request_id);
    bool publish(std::uint64_t logical_page,
                 std::uint64_t generation,
                 std::uint64_t frame);
    [[nodiscard]] std::optional<ResolvedPage> resolve(
        std::uint64_t logical_page) const;
    bool mark_dirty(std::uint64_t logical_page, std::uint64_t generation);
    bool begin_writeback(std::uint64_t logical_page, std::uint64_t generation);
    bool evict(std::uint64_t logical_page, std::uint64_t generation);

private:
    struct Entry {
        std::uint64_t frame{0};
        std::uint64_t owner_request_id{0};
        std::uint64_t generation{1};
        PageState state{PageState::Invalid};
        std::uint32_t waiter_count{0};
    };

    mutable std::mutex mutex_;
    std::unordered_map<std::uint64_t, Entry> entries_;
};

}  // namespace hbfsim
