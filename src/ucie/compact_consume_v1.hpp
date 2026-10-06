#pragma once
// Bounded private wire implementation; core Consume and public completion ABI stay unchanged.
#include "typed_rpc.hpp"
#include <hbfsim/ucie/compact_consume.hpp>
namespace hbfsim::ucie::compact_consume_v1 {
inline void validate_request(const Request& q) {
    if(q.count<2 || q.count>max_count || q.module>=16)
        throw std::invalid_argument("compact Consume count/module");
    for(std::uint32_t i=0;i<q.count;++i) {
        if(!q.ids[i]) throw std::invalid_argument("compact Consume zero ID");
        for(std::uint32_t j=0;j<i;++j)
            if(q.ids[i]==q.ids[j]) throw std::invalid_argument("compact Consume duplicate ID");
    }
}
inline void encode_request(typed_rpc::Writer& w,const Request& q) {
    validate_request(q); w.u32(version); w.u64(q.horizon); w.u32(q.module); w.u32(q.count);
    for(std::uint32_t i=0;i<q.count;++i) w.u64(q.ids[i]);
}
inline Request decode_request(typed_rpc::Reader& r) {
    if(r.u32()!=version) throw std::invalid_argument("compact Consume request version");
    Request q; q.horizon=r.u64(); q.module=r.u32(); q.count=r.u32();
    if(q.count<2 || q.count>max_count) throw std::invalid_argument("compact Consume request count");
    for(std::uint32_t i=0;i<q.count;++i) q.ids[i]=r.u64();
    r.done(); validate_request(q); return q; // No core mutation before whole-frame validation.
}
inline void validate_reply(const Request& q,const Reply& a) {
    if(a.confirmed>q.count || a.diagnostic_size>max_diagnostic)
        throw std::invalid_argument("compact Consume ACK bound");
    std::uint64_t sum=0; for(auto n:a.results) { if(n>a.confirmed) throw std::invalid_argument("compact Consume result bound"); sum+=n; }
    if(sum!=a.confirmed) throw std::invalid_argument("compact Consume result conservation");
    if(a.stop==Stop::Complete) {
        if(a.confirmed!=q.count || a.failing_index!=max_count || a.error_code || a.diagnostic_size)
            throw std::invalid_argument("compact Consume success shape");
    } else if(a.stop==Stop::SemanticError) {
        if(a.confirmed>=q.count || a.failing_index!=a.confirmed || a.error_code!=1)
            throw std::invalid_argument("compact Consume terminal prefix");
    } else throw std::invalid_argument("compact Consume stop enum");
}
inline void encode_reply(typed_rpc::Writer& w,const Request& q,const Reply& a) {
    validate_reply(q,a); w.u32(version); w.u64(q.horizon); w.u32(q.module); w.u32(q.count);
    w.u32(a.confirmed); w.u8(static_cast<std::uint8_t>(a.stop)); w.u32(a.failing_index); w.u32(a.error_code);
    for(auto n:a.results) w.u32(n);
    w.u32(a.diagnostic_size); for(std::uint32_t i=0;i<a.diagnostic_size;++i) w.u8(static_cast<std::uint8_t>(a.diagnostic[i]));
}
inline Reply decode_reply(typed_rpc::Reader& r,const Request& q) {
    if(r.u32()!=version || r.u64()!=q.horizon || r.u32()!=q.module || r.u32()!=q.count)
        throw std::invalid_argument("compact Consume ACK identity");
    Reply a; a.confirmed=r.u32(); a.stop=static_cast<Stop>(r.u8()); a.failing_index=r.u32(); a.error_code=r.u32();
    for(auto& n:a.results) n=r.u32();
    a.diagnostic_size=r.u32(); if(a.diagnostic_size>max_diagnostic) throw std::invalid_argument("compact Consume diagnostic bound");
    for(std::uint32_t i=0;i<a.diagnostic_size;++i) a.diagnostic[i]=static_cast<char>(r.u8());
    r.done(); validate_reply(q,a); return a; // All ACK validation before host commits.
}
} // namespace hbfsim::ucie::compact_consume_v1
