#include <hbfsim/ucie/link.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>

namespace hbfsim::ucie {
namespace {
std::uint64_t add_time(std::uint64_t a, std::uint64_t b)
{
    if (a > std::numeric_limits<std::uint64_t>::max() - b)
        throw std::overflow_error("UCIe event time overflow");
    return a + b;
}
void add_count(std::uint64_t& target, std::uint64_t amount, bool* saturated=nullptr)
{
    // Implementation accounting is bounded at uint64 max, independent of
    // AoU's protocol credit counters. The caller can report lost precision.
    if (amount > std::numeric_limits<std::uint64_t>::max() - target) {
        target=std::numeric_limits<std::uint64_t>::max();
        if (saturated) *saturated=true;
    } else target+=amount;
}
} // namespace

StreamingLink::StreamingLink(LinkProfile profile)
    : profile_(std::move(profile)),
      ar_credits_(profile_.initial_ar_granules),
      r_credits_(profile_.initial_r_granules)
{
    validate_link_profile(profile_);
}

void StreamingLink::enqueue(Direction dir, std::uint64_t id, std::uint64_t ready_ns)
{
    if (id == 0 || ready_ns < now_)
        throw std::invalid_argument("UCIe message ID is zero or ready time is in the past");
    auto& active=dir==Direction::Request ? active_requests_ : active_returns_;
    if (active.contains(id)) throw std::invalid_argument("duplicate in-flight UCIe message ID");
    if (active.size()>=profile_.max_accepted)
        throw std::overflow_error("finite UCIe direction capacity reached");
    active.insert(id);
    events_.emplace(ready_ns, Event{.kind=Event::Kind::Arrival,
                                    .direction=dir, .id=id});
}

void StreamingLink::consume(Direction dir, std::uint64_t id, std::uint64_t at)
{
    const auto key = std::pair{dir,id};
    const auto found = awaiting_consume_.find(key);
    if (found == awaiting_consume_.end() || at < found->second || at < now_)
        throw std::invalid_argument("UCIe message was not delivered or consume time moved backward");
    awaiting_consume_.erase(found); // duplicate consume is rejected immediately
    events_.emplace(at, Event{.kind=Event::Kind::Consume,
                               .direction=dir, .id=id});
}

std::optional<std::uint64_t> StreamingLink::next_event_ns() const
{
    if (events_.empty()) return std::nullopt;
    return events_.begin()->first;
}

std::uint32_t StreamingLink::credits(Direction direction) const noexcept
{
    return direction == Direction::Request ? ar_credits_ : r_credits_;
}

bool StreamingLink::message_active(Direction direction,
                                   std::uint64_t request_id) const noexcept
{
    return (direction == Direction::Request ? active_requests_ : active_returns_)
        .contains(request_id);
}

const DirectionCounters& StreamingLink::counters(Direction dir) const
{
    return dir == Direction::Request ? request_counters_ : return_counters_;
}

std::uint32_t StreamingLink::legal_grant(std::uint32_t pending)
{
    if (pending == 0) return 0;
    if (pending < 4) return 1; // AoU encoding has no two-granule grant.
    std::uint32_t grant = 4;
    while (grant < 128 && grant * 2 <= pending) grant *= 2;
    return grant;
}

void StreamingLink::send_ready(Direction dir)
{
    auto& queue = dir == Direction::Request ? request_queue_ : return_queue_;
    auto& cursor = dir == Direction::Request ? request_cursor_ : return_cursor_;
    auto& sender_credit = dir == Direction::Request ? ar_credits_ : r_credits_;
    auto& grant_pending = dir == Direction::Request ? r_grant_pending_ : ar_grant_pending_;
    auto& counters = dir == Direction::Request ? request_counters_ : return_counters_;
    if (cursor > now_) {
        if (!queue.empty() || grant_pending != 0)
            events_.emplace(cursor, Event{.kind=Event::Kind::Retry, .direction=dir});
        return;
    }

    std::uint32_t used = 0;
    std::vector<std::uint64_t> completed;
    std::vector<FlitRecord::Fragment> fragments;
    while (!queue.empty() && used < 48) {
        auto& msg = queue.front();
        if (!msg.started) {
            const std::uint32_t full = dir == Direction::Request ? 3 : 14;
            if (sender_credit < full) break;
            sender_credit -= full; // all granules are reserved at MsgStart
            msg.started = true;
        }
        const bool start = msg.emitted == 0;
        const auto fragment = std::min<std::uint32_t>(48 - used, msg.remaining);
        fragments.push_back(FlitRecord::Fragment{.request_id=msg.id,
            .flit_granule=used, .message_granule=msg.emitted,
            .granules=fragment, .msg_start=start,
            .msg_end=(fragment==msg.remaining)});
        used += fragment;
        msg.remaining -= fragment;
        msg.emitted += fragment;
        if (msg.remaining == 0) {
            completed.push_back(msg.id);
            queue.pop_front();
        }
    }
    const auto grant = legal_grant(grant_pending);
    if (used == 0 && grant == 0) return;
    grant_pending -= grant;

    const auto start = now_;
    cursor = add_time(start, flit_serialization_ns(profile_));
    const auto delivery = add_time(cursor, profile_.propagation_ns);
    events_.emplace(delivery, Event{.kind=Event::Kind::FlitDelivery,
                                     .direction=dir, .completed=std::move(completed),
                                     .grant=grant});
    if (!queue.empty() || grant_pending != 0)
        events_.emplace(cursor, Event{.kind=Event::Kind::Retry, .direction=dir});
    add_count(counters.flits, 1, &counters.accounting_saturated);
    add_count(counters.wire_bytes, 256, &counters.accounting_saturated);
    add_count(counters.ucie_header_bytes, 2, &counters.accounting_saturated);
    add_count(counters.crc_position_bytes, 4, &counters.accounting_saturated);
    add_count(counters.aou_header_bytes, 10, &counters.accounting_saturated);
    add_count(counters.aou_message_bytes, used * 5, &counters.accounting_saturated);
    add_count(counters.unused_message_bytes, (48 - used) * 5,
              &counters.accounting_saturated);
    if (used == 0) add_count(counters.credit_only_flits, 1,
                             &counters.accounting_saturated);
    constexpr std::size_t kFlitTraceLimit = 4096;
    if (flits_.size() < kFlitTraceLimit)
        flits_.push_back(FlitRecord{.direction=dir, .start_ns=start,
            .delivered_ns=delivery, .message_granules=used,
            .grant_granules=grant, .credit_only=(used == 0),
            .fragments=std::move(fragments)});
    else add_count(flit_records_dropped_,1);
}

std::vector<Delivery> StreamingLink::step()
{
    if (events_.empty()) throw std::logic_error("no UCIe event to step");
    now_ = events_.begin()->first;
    std::vector<Delivery> delivered;
    // Process every event already scheduled at this timestamp before sending,
    // allowing simultaneous ready messages to share a flit without a wait window.
    while (!events_.empty() && events_.begin()->first == now_) {
        auto node = events_.extract(events_.begin());
        auto& event = node.mapped();
        if (event.kind == Event::Kind::Arrival) {
            auto& queue = event.direction == Direction::Request ? request_queue_ : return_queue_;
            queue.push_back(Message{.direction=event.direction, .id=event.id,
                .remaining=event.direction == Direction::Request ? 3U : 14U});
        } else if (event.kind == Event::Kind::FlitDelivery) {
            auto& sender_credit = event.direction == Direction::Request ? r_credits_ : ar_credits_;
            const auto maximum = event.direction == Direction::Request
                ? profile_.initial_r_granules : profile_.initial_ar_granules;
            if (event.grant > maximum - sender_credit)
                throw std::logic_error("AoU credit grant exceeds released capacity");
            sender_credit += event.grant;
            for (const auto id : event.completed) {
                delivered.push_back(Delivery{event.direction, id, now_});
                if (!awaiting_consume_.emplace(std::pair{event.direction,id}, now_).second)
                    throw std::logic_error("duplicate delivered UCIe message");
            }
        } else if (event.kind == Event::Kind::Consume) {
            auto& active=event.direction==Direction::Request
                ? active_requests_ : active_returns_;
            if (active.erase(event.id)!=1)
                throw std::logic_error("UCIe consumed message has no active owner");
            auto& pending = event.direction == Direction::Request
                ? ar_grant_pending_ : r_grant_pending_;
            const auto amount = event.direction == Direction::Request ? 3U : 14U;
            if (pending > 65535U - amount)
                throw std::overflow_error("AoU pending grant overflow");
            pending += amount;
        }
    }
    send_ready(Direction::Request);
    send_ready(Direction::Return);
    return delivered;
}

} // namespace hbfsim::ucie
