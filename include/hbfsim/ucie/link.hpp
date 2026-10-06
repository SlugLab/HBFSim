#pragma once

#include <hbfsim/ucie/profile.hpp>

#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <set>
#include <vector>

namespace hbfsim::ucie {

enum class Direction { Request, Return };

struct Delivery {
    Direction direction;
    std::uint64_t request_id;
    std::uint64_t delivered_ns;
};

struct FlitRecord {
    struct Fragment {
        std::uint64_t request_id;
        std::uint32_t flit_granule;
        std::uint32_t message_granule;
        std::uint32_t granules;
        bool msg_start;
        bool msg_end;
    };
    Direction direction;
    std::uint64_t start_ns;
    std::uint64_t delivered_ns;
    std::uint32_t message_granules;
    std::uint32_t grant_granules;
    bool credit_only;
    std::vector<Fragment> fragments;
};

struct DirectionCounters {
    std::uint64_t flits{};
    std::uint64_t wire_bytes{};
    std::uint64_t ucie_header_bytes{};
    std::uint64_t crc_position_bytes{};
    std::uint64_t aou_header_bytes{};
    std::uint64_t aou_message_bytes{};
    std::uint64_t unused_message_bytes{};
    std::uint64_t credit_only_flits{};
    bool accounting_saturated{};
};

// The component models Format 6 positions and AoU granule occupancy. It does
// not serialize field bits, compute CRC, train the PHY, or perform retry.
class StreamingLink {
public:
    explicit StreamingLink(LinkProfile profile);
    void enqueue(Direction direction, std::uint64_t request_id,
                 std::uint64_t ready_ns);
    // AR is consumed after real MQSim submission; R after caller poll.
    // Consumption creates a later grant event, never an immediate credit.
    void consume(Direction direction, std::uint64_t request_id,
                 std::uint64_t consumed_ns);
    [[nodiscard]] std::optional<std::uint64_t> next_event_ns() const;
    // Processes all arrivals/deliveries at the next event time. The owner must
    // advance its single MQSim clock to that time before calling this method.
    std::vector<Delivery> step();
    [[nodiscard]] std::uint64_t current_time_ns() const noexcept { return now_; }
    [[nodiscard]] const DirectionCounters& counters(Direction direction) const;
    [[nodiscard]] const std::vector<FlitRecord>& flits() const noexcept { return flits_; }
    [[nodiscard]] std::uint32_t credits(Direction direction) const noexcept;
    [[nodiscard]] bool message_active(Direction direction,
                                      std::uint64_t request_id) const noexcept;
    [[nodiscard]] std::uint64_t flit_records_dropped() const noexcept
    { return flit_records_dropped_; }

private:
    struct Message {
        Direction direction;
        std::uint64_t id;
        std::uint32_t remaining;
        bool started{};
        std::uint32_t emitted{};
    };
    struct Event {
        enum class Kind { Arrival, FlitDelivery, Consume, Retry } kind;
        Direction direction;
        std::uint64_t id{};
        std::vector<std::uint64_t> completed;
        std::uint32_t grant{};
    };
    using Scheduled = std::multimap<std::uint64_t, Event>;

    void send_ready(Direction direction);
    static std::uint32_t legal_grant(std::uint32_t pending);
    LinkProfile profile_;
    std::uint64_t now_{};
    Scheduled events_;
    std::deque<Message> request_queue_;
    std::deque<Message> return_queue_;
    std::uint64_t request_cursor_{};
    std::uint64_t return_cursor_{};
    std::uint32_t ar_credits_{};
    std::uint32_t r_credits_{};
    std::uint32_t ar_grant_pending_{};
    std::uint32_t r_grant_pending_{};
    DirectionCounters request_counters_;
    DirectionCounters return_counters_;
    std::vector<FlitRecord> flits_;
    std::map<std::pair<Direction,std::uint64_t>, std::uint64_t> awaiting_consume_;
    std::set<std::uint64_t> active_requests_;
    std::set<std::uint64_t> active_returns_;
    std::uint64_t flit_records_dropped_{};
};

} // namespace hbfsim::ucie
