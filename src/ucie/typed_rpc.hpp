#pragma once

#include "worker_io.hpp"
#include <hbfsim/ucie/device_frontend.hpp>

#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace hbfsim::ucie::typed_rpc {

constexpr std::uint8_t version=1;
constexpr char typed_tag='T';
constexpr char json_tag='J';

enum class Op : std::uint8_t {
    Reserve=1, Submit=2, Advance=3, CloseAdvance=4,
    ReadyPage=5, PeekEvent=6, Consume=7, IsActive=8,
    //9 remains reserved for rejected ordered Consume, absent from this build.
    ReserveSubmitSingle=10,
    ReserveSubmitOrdered=11,
    ConsumeCompactOrdered=12, // New negotiated direct-ID compact ACK, never reserved opcode9.
};

inline DeviceResult device_result(std::uint32_t raw)
{
    static_assert(static_cast<std::uint32_t>(DeviceResult::TimedOut)==3);
    if (raw>static_cast<std::uint32_t>(DeviceResult::TimedOut))
        throw std::invalid_argument("invalid typed worker result");
    return static_cast<DeviceResult>(raw);
}

inline std::string tagged_json(std::string body)
{
    if (body.size()>=worker_io::max_frame)
        throw std::overflow_error("tagged JSON frame too large");
    body.insert(body.begin(),json_tag);
    return body;
}

class Writer {
public:
    Writer(Op op,std::uint64_t seq,std::uint32_t stack)
    {
        u8(static_cast<std::uint8_t>(typed_tag));
        u8(version);
        u8(static_cast<std::uint8_t>(op));
        u64(seq);
        u32(stack);
    }
    void u8(std::uint8_t value)
    { body_.push_back(static_cast<char>(value)); }
    void boolean(bool value) { u8(value?1:0); }
    void u32(std::uint32_t value)
    {
        const char bytes[4]={
            static_cast<char>(value>>24),
            static_cast<char>(value>>16),
            static_cast<char>(value>>8),
            static_cast<char>(value)};
        body_.append(bytes,sizeof(bytes));
    }
    void u64(std::uint64_t value)
    {
        const char bytes[8]={
            static_cast<char>(value>>56),
            static_cast<char>(value>>48),
            static_cast<char>(value>>40),
            static_cast<char>(value>>32),
            static_cast<char>(value>>24),
            static_cast<char>(value>>16),
            static_cast<char>(value>>8),
            static_cast<char>(value)};
        body_.append(bytes,sizeof(bytes));
    }
    std::string finish() &&
    {
        if (body_.size()>worker_io::max_frame)
            throw std::overflow_error("typed worker frame too large");
        return std::move(body_);
    }
private:
    std::string body_;
};

class Reader {
public:
    explicit Reader(std::string body):body_(std::move(body))
    {
        if (body_.size()<15 || body_.size()>worker_io::max_frame ||
            static_cast<std::uint8_t>(body_[0])!=
                static_cast<std::uint8_t>(typed_tag) ||
            static_cast<std::uint8_t>(body_[1])!=version)
            throw std::invalid_argument("typed worker frame header/version");
        cursor_=2;
        const auto raw=u8();
        if ((raw<static_cast<std::uint8_t>(Op::Reserve) ||
             raw>static_cast<std::uint8_t>(Op::IsActive)) &&
            raw!=static_cast<std::uint8_t>(Op::ReserveSubmitSingle) &&
            raw!=static_cast<std::uint8_t>(Op::ReserveSubmitOrdered) &&
             raw!=static_cast<std::uint8_t>(Op::ConsumeCompactOrdered))
            throw std::invalid_argument("typed worker opcode");
        op_=static_cast<Op>(raw);
        sequence_=u64();
        stack_=u32();
    }
    Reader(const Reader&)=delete;
    Reader& operator=(const Reader&)=delete;
    Reader(Reader&& other) noexcept
        :body_(std::move(other.body_)),cursor_(other.cursor_),op_(other.op_),
         sequence_(other.sequence_),stack_(other.stack_),
         poison_on_abandon_(other.poison_on_abandon_),complete_(other.complete_)
    { other.poison_on_abandon_=nullptr; }
    Reader& operator=(Reader&&)=delete;
    ~Reader()
    { if (poison_on_abandon_ && !complete_) *poison_on_abandon_=true; }
    void poison_if_abandoned(bool* connection_poisoned) noexcept
    { poison_on_abandon_=connection_poisoned; }
    [[nodiscard]] Op op() const { return op_; }
    [[nodiscard]] std::uint64_t sequence() const { return sequence_; }
    [[nodiscard]] std::uint32_t stack() const { return stack_; }
    void expect(Op op,std::uint64_t sequence,std::uint32_t stack) const
    {
        if (op_!=op || sequence_!=sequence || stack_!=stack)
            throw std::invalid_argument("typed worker identity mismatch");
    }
    std::uint8_t u8()
    {
        need(1);
        return static_cast<std::uint8_t>(body_[cursor_++]);
    }
    bool boolean()
    {
        const auto value=u8();
        if (value>1) throw std::invalid_argument("typed worker boolean");
        return value!=0;
    }
    std::uint32_t u32()
    {
        need(4);
        const auto* bytes=reinterpret_cast<const unsigned char*>(body_.data()+cursor_);
        const auto value=(std::uint32_t(bytes[0])<<24)|
                         (std::uint32_t(bytes[1])<<16)|
                         (std::uint32_t(bytes[2])<<8)|
                          std::uint32_t(bytes[3]);
        cursor_+=4;
        return value;
    }
    std::uint64_t u64()
    {
        need(8);
        const auto* bytes=reinterpret_cast<const unsigned char*>(body_.data()+cursor_);
        std::uint64_t value=0;
        for (int i=0;i<8;++i)
            value=(value<<8)|bytes[i];
        cursor_+=8;
        return value;
    }
    void done() const
    {
        if (cursor_!=body_.size())
            throw std::invalid_argument("typed worker trailing bytes");
        complete_=true;
    }
private:
    void need(std::size_t count) const
    {
        if (count>body_.size()-cursor_)
            throw std::invalid_argument("typed worker truncated frame");
    }
    std::string body_;
    std::size_t cursor_{};
    Op op_{};
    std::uint64_t sequence_{};
    std::uint32_t stack_{};
    bool* poison_on_abandon_{};
    mutable bool complete_{};
};

} // namespace hbfsim::ucie::typed_rpc
