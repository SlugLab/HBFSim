#pragma once

#include "typed_rpc.hpp"
#include <hbfsim/ucie/stack_frontend.hpp>
#include <optional>

namespace hbfsim::ucie::reserve_submit_single_v1 {

constexpr std::uint32_t version=1;
// Opcode9 belongs to the retained, rejected Consume batch experiment. This
// candidate uses10, negotiated independently, and leaves9 an invalid opcode.
constexpr std::uint32_t max_modules=(worker_io::max_frame-160)/4;

struct ReadFields {
    DeviceRead read;
    std::optional<PacketBackingProof> proof;
};

inline void encode_read(typed_rpc::Writer& w,const DeviceRead& r,
                        const std::optional<PacketBackingProof>& proof)
{
    w.u64(r.request_id);
    w.u64(r.arrival_ns);
    w.u64(r.deadline_ns);
    w.u64(r.local_address);
    w.u32(r.axi_id);
    w.u32(r.module_id);
    w.u64(r.endpoint_id);
    w.u32(r.bytes);
    w.u32(r.operation);
    w.boolean(r.expected_media_page.has_value());
    if (r.expected_media_page)
        w.u64(*r.expected_media_page);
    w.boolean(r.expected_generation.has_value());
    if (r.expected_generation)
        w.u64(*r.expected_generation);
    w.boolean(proof.has_value());
    if (proof) {
        w.u64(proof->original_local_address);
        w.u32(proof->original_bytes);
        w.u64(proof->canonical_id);
        w.u64(proof->generation);
    }
}

inline ReadFields decode_read(typed_rpc::Reader& r,std::uint32_t stack)
{
    ReadFields f{};
    auto& v=f.read;
    v.request_id=r.u64();
    v.arrival_ns=r.u64();
    v.deadline_ns=r.u64();
    v.local_address=r.u64();
    v.axi_id=r.u32();
    v.stack_id=stack;
    v.module_id=r.u32();
    v.endpoint_id=r.u64();
    v.bytes=r.u32();
    v.operation=r.u32();
    if (r.boolean())
        v.expected_media_page=r.u64();
    if (r.boolean())
        v.expected_generation=r.u64();
    if (r.boolean())
        f.proof=PacketBackingProof{r.u64(),r.u32(),r.u64(),r.u64()};
    return f;
}

struct Reply {
    bool reserved{},accepted{};
};

inline void encode_reply(typed_rpc::Writer& w,std::uint64_t token,
    std::uint64_t horizon,std::uint64_t id,Reply result)
{
    w.u32(version);
    w.u64(token);
    w.u64(horizon);
    w.u64(id);
    w.boolean(result.reserved);
    w.boolean(result.accepted);
}

inline Reply decode_reply(typed_rpc::Reader& r,std::uint64_t token,
    std::uint64_t horizon,std::uint64_t id)
{
    if (r.u32()!=version || r.u64()!=token || r.u64()!=horizon || r.u64()!=id)
        throw std::runtime_error("single-child admission reply identity/version");
    Reply v{r.boolean(),r.boolean()};
    r.done();
    if (v.accepted && !v.reserved)
        throw std::runtime_error("single-child accepted without reservation");
    return v;
}

} // namespace hbfsim::ucie::reserve_submit_single_v1
