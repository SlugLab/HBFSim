#pragma once

#include <arpa/inet.h>
#include <poll.h>
#include <sys/socket.h>

#include <array>
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace hbfsim::ucie::worker_io {
constexpr std::size_t max_frame=65536;
using Clock=std::chrono::steady_clock;

inline void transfer(int fd,void* memory,std::size_t length,bool sending,
                     Clock::time_point deadline)
{
    auto* cursor=static_cast<char*>(memory);
    while (length) {
        const auto now=Clock::now();
        if (now>=deadline) throw std::runtime_error("worker IPC deadline exceeded");
        const auto wait=std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline-now).count();
        pollfd interest{fd,static_cast<short>(sending ? POLLOUT : POLLIN),0};
        const auto result=::poll(&interest,1,static_cast<int>(
            std::min<std::int64_t>(wait,1000)));
        if (result==0) continue;
        if (result<0) {
            if (errno==EINTR) continue;
            throw std::runtime_error("worker IPC poll failed");
        }
        if (interest.revents&(POLLERR|POLLNVAL))
            throw std::runtime_error("worker IPC socket error");
        const auto count=sending
            ? ::send(fd,cursor,length,MSG_NOSIGNAL|MSG_DONTWAIT)
            : ::recv(fd,cursor,length,MSG_DONTWAIT);
        if (count==0) throw std::runtime_error("worker IPC EOF");
        if (count<0) {
            if (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR) continue;
            throw std::runtime_error("worker IPC transfer failed");
        }
        cursor+=count;
        length-=static_cast<std::size_t>(count);
    }
}

inline void send_frame(int fd,const std::string& body,
                       std::chrono::milliseconds timeout)
{
    if (body.empty() || body.size()>max_frame)
        throw std::overflow_error("worker IPC output frame too large");
    auto end=Clock::now()+timeout;
    auto length=htonl(static_cast<std::uint32_t>(body.size()));
    transfer(fd,&length,sizeof(length),true,end);
    transfer(fd,const_cast<char*>(body.data()),body.size(),true,end);
}

inline std::string receive_frame(int fd,std::chrono::milliseconds timeout)
{
    auto end=Clock::now()+timeout;
    std::uint32_t length{};
    transfer(fd,&length,sizeof(length),false,end);
    const auto count=ntohl(length);
    if (count==0 || count>max_frame)
        throw std::overflow_error("worker IPC input frame too large");
    std::string body(count,'\0');
    transfer(fd,body.data(),body.size(),false,end);
    return body;
}
} // namespace hbfsim::ucie::worker_io
