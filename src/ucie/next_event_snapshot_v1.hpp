#pragma once
// Private negotiated extension. No public layout, typed opcode or mailbox change.
#include "typed_rpc.hpp"
#include <optional>
#include <utility>

namespace hbfsim::ucie::next_event_snapshot_v1 {
constexpr std::uint32_t version=1;
struct Snapshot {
    std::uint64_t horizon{};
    DeviceMediaPort::EventPeek peek;
};
inline void encode(typed_rpc::Writer& reply,const Snapshot& value)
{
    if (!value.peek.supported && value.peek.next_ns)
        throw std::runtime_error("unsupported next-event snapshot has event");
    reply.u32(version);
    reply.u64(value.horizon);
    reply.boolean(value.peek.supported);
    reply.boolean(value.peek.next_ns.has_value());
    if (value.peek.next_ns) reply.u64(*value.peek.next_ns);
}
inline Snapshot decode(typed_rpc::Reader& reply,std::uint64_t horizon)
{
    if (reply.u32()!=version || reply.u64()!=horizon)
        throw std::runtime_error("next-event snapshot version/horizon mismatch");
    Snapshot value{horizon,{}};
    value.peek.supported=reply.boolean();
    const bool present=reply.boolean();
    if (!value.peek.supported && present)
        throw std::runtime_error("unsupported next-event snapshot has event");
    if (present) value.peek.next_ns=reply.u64();
    // A timestamp at the current horizon is valid. No narrowing or sentinel.
    return value;
}
class SingleUse {
public:
    void invalidate() noexcept { value_.reset(); }
    void install(Snapshot value) { value_=std::move(value); }
    std::optional<Snapshot> take(std::uint64_t horizon,bool eligible)
    {
        auto value=std::exchange(value_,std::nullopt);
        if (!eligible || !value || value->horizon!=horizon) return std::nullopt;
        return value;
    }
private:
    std::optional<Snapshot> value_;
};
} // namespace hbfsim::ucie::next_event_snapshot_v1
