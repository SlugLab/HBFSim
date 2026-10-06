#pragma once
#include "reserve_submit_single_v1.hpp"
#include <hbfsim/ucie/ordered_admission.hpp>
#include <cstddef>
#include <stdexcept>
namespace hbfsim::ucie::ordered_admission_v1 {
constexpr std::size_t request_base=35,reply_base=52,max_request=1811,
    max_normal_reply=340,max_terminal_reply=1346,reply_buffer_bound=1364;
static_assert(max_request<=worker_io::max_frame && reply_buffer_bound<=worker_io::max_frame);
inline std::size_t checked_add(std::size_t a,std::size_t b) {
    if (a>worker_io::max_frame || b>worker_io::max_frame-a)
        throw std::overflow_error("ordered admission frame bound");
    return a+b;
}
inline void validate_request_shape(const Request& request) {
    if (request.count<2 || request.count>max_count || request.module>=modules)
        throw std::invalid_argument("ordered admission count/module");
    for (std::uint32_t i=0;i<request.count;++i)
        if (request.records[i].read.module_id!=request.module)
            throw std::invalid_argument("ordered admission mixed module");
}
inline std::size_t request_size(const Request& request) {
    validate_request_shape(request);
    std::size_t size=request_base;
    for (std::uint32_t i=0;i<request.count;++i) {
        const auto& record=request.records[i];
        size=checked_add(size,67+(record.read.expected_media_page?8:0)+
            (record.read.expected_generation?8:0)+(record.proof?28:0));
    }
    if (size>max_request) throw std::overflow_error("ordered admission request size");
    return size;
}
inline void encode_request(typed_rpc::Writer& writer,const Request& request) {
    (void)request_size(request);
    writer.u32(version);writer.u64(request.horizon);
    writer.u32(request.module);writer.u32(request.count);
    for (std::uint32_t i=0;i<request.count;++i) {
        const auto& record=request.records[i];
        writer.u64(record.token);
        reserve_submit_single_v1::encode_read(writer,record.read,record.proof);
    }
}
inline Request decode_request(typed_rpc::Reader& reader,std::uint32_t stack) {
    if (reader.u32()!=version) throw std::invalid_argument("ordered admission version");
    Request request;
    request.horizon=reader.u64();request.module=reader.u32();request.count=reader.u32();
    if (request.count<2 || request.count>max_count || request.module>=modules)
        throw std::invalid_argument("ordered admission count/module");
    for (std::uint32_t i=0;i<request.count;++i) {
        auto& record=request.records[i];record.token=reader.u64();
        const auto fields=reserve_submit_single_v1::decode_read(reader,stack);
        record.read=fields.read;record.proof=fields.proof;
    }
    reader.done();
    (void)request_size(request); // structural only; no suffix Submit semantics
    return request;
}
inline void validate_reply_shape(const Reply& result,std::uint32_t count) {
    if (count<2 || count>max_count || result.completed>count ||
        result.diagnostic_size>max_diagnostic)
        throw std::invalid_argument("ordered admission reply bounds");
    const bool terminal=result.stop==Stop::SemanticError;
    if (terminal) {
        if (result.completed>=count || result.failing_index!=result.completed ||
            !result.error_code)
            throw std::invalid_argument("ordered admission terminal shape");
    } else if (result.failing_index!=no_index || result.error_code || result.diagnostic_size)
        throw std::invalid_argument("ordered admission normal error fields");
    if (result.stop==Stop::End) {
        if (result.completed!=count) throw std::invalid_argument("ordered admission short end");
    } else if (result.stop==Stop::ReserveDenied || result.stop==Stop::SubmitFalse) {
        if (!result.completed) throw std::invalid_argument("ordered admission empty stop");
    } else if (!terminal) throw std::invalid_argument("ordered admission stop enum");
    for (std::uint32_t i=0;i<result.completed;++i) {
        const auto& item=result.items[i];
        if (item.accepted && !item.reserved)
            throw std::invalid_argument("ordered admission accepted without reserve");
        const bool last_stop=!terminal && result.stop!=Stop::End && i+1==result.completed;
        if (last_stop) {
            if (item.accepted || item.reserved!=(result.stop==Stop::SubmitFalse))
                throw std::invalid_argument("ordered admission stop result");
        } else if (!item.reserved || !item.accepted)
            throw std::invalid_argument("ordered admission non-success prefix");
    }
    const auto size=checked_add(checked_add(reply_base,18*result.completed),result.diagnostic_size);
    if (size>(terminal?max_terminal_reply:max_normal_reply))
        throw std::overflow_error("ordered admission reply size");
}
inline void encode_reply(typed_rpc::Writer& writer,const Request& request,const Reply& result) {
    validate_reply_shape(result,request.count);
    writer.u32(version);writer.u64(request.horizon);writer.u32(request.module);
    writer.u32(request.count);writer.u32(result.completed);
    writer.u8(static_cast<std::uint8_t>(result.stop));writer.u32(result.failing_index);
    writer.u32(result.error_code);writer.u32(result.diagnostic_size);
    for (std::uint32_t i=0;i<result.diagnostic_size;++i)
        writer.u8(static_cast<std::uint8_t>(result.diagnostic[i]));
    for (std::uint32_t i=0;i<result.completed;++i) {
        const auto& item=result.items[i];writer.u64(item.token);writer.u64(item.request_id);
        writer.boolean(item.reserved);writer.boolean(item.accepted);
    }
}
inline Reply decode_reply(typed_rpc::Reader& reader,const Request& request) {
    if (reader.u32()!=version || reader.u64()!=request.horizon ||
        reader.u32()!=request.module || reader.u32()!=request.count)
        throw std::invalid_argument("ordered admission reply identity/version");
    Reply result;result.completed=reader.u32();
    result.stop=static_cast<Stop>(reader.u8());result.failing_index=reader.u32();
    result.error_code=reader.u32();result.diagnostic_size=reader.u32();
    if (result.completed>request.count || result.completed>max_count ||
        result.diagnostic_size>max_diagnostic)
        throw std::invalid_argument("ordered admission reply lengths");
    for (std::uint32_t i=0;i<result.diagnostic_size;++i)
        result.diagnostic[i]=static_cast<char>(reader.u8());
    for (std::uint32_t i=0;i<result.completed;++i) {
        auto& item=result.items[i];item.token=reader.u64();item.request_id=reader.u64();
        item.reserved=reader.boolean();item.accepted=reader.boolean();
        if (item.token!=request.records[i].token ||
            item.request_id!=request.records[i].read.request_id)
            throw std::invalid_argument("ordered admission reply item identity");
    }
    validate_reply_shape(result,request.count);
    reader.done(); // only now may the leader trust the entire prefix
    return result;
}
} // namespace hbfsim::ucie::ordered_admission_v1
