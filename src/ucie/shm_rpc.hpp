#pragma once

#include "worker_io.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <string_view>

#include <fcntl.h>
#include <linux/futex.h>
#include <linux/memfd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

namespace hbfsim::ucie::shm_rpc {

constexpr std::uint32_t magic=0x48424653; // HBFS
constexpr std::uint32_t version=1;
constexpr std::uint32_t empty=0;
constexpr std::uint32_t waiting=1;
constexpr std::uint32_t ready=2;
static_assert(std::atomic<std::uint32_t>::is_always_lock_free);
static_assert(sizeof(std::atomic<std::uint32_t>)==sizeof(std::uint32_t));

struct alignas(64) Slot {
    std::atomic<std::uint32_t> state{empty};
    std::uint32_t length{};
    char payload[worker_io::max_frame]{};
};

struct alignas(64) Shared {
    std::uint32_t object_magic{magic};
    std::uint32_t abi_version{version};
    std::uint32_t object_bytes{};
    std::uint32_t capacity{worker_io::max_frame};
    Slot request;
    Slot response;
};

inline int create_fd()
{
    const int fd=static_cast<int>(::syscall(SYS_memfd_create,
        "hbfsim-ucie-worker-mailbox",MFD_CLOEXEC|MFD_ALLOW_SEALING));
    if (fd<0) throw std::runtime_error("worker memfd_create failed");
    if (::ftruncate(fd,sizeof(Shared))!=0 ||
        ::fcntl(fd,F_ADD_SEALS,F_SEAL_SHRINK|F_SEAL_GROW|F_SEAL_SEAL)<0) {
        ::close(fd);
        throw std::runtime_error("worker mailbox size/seal failed");
    }
    return fd;
}

inline Shared* map_fd(int fd,bool initialize)
{
    struct stat metadata{};
    if (::fstat(fd,&metadata)!=0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_size!=static_cast<off_t>(sizeof(Shared)))
        throw std::runtime_error("worker mailbox object size/type mismatch");
    const auto seals=::fcntl(fd,F_GET_SEALS);
    constexpr int required=F_SEAL_SHRINK|F_SEAL_GROW|F_SEAL_SEAL;
    if (seals<0 || (seals&required)!=required)
        throw std::runtime_error("worker mailbox object seals mismatch");
    void* raw=::mmap(nullptr,sizeof(Shared),PROT_READ|PROT_WRITE,
                     MAP_SHARED,fd,0);
    if (raw==MAP_FAILED) throw std::runtime_error("worker mailbox mmap failed");
    auto* shared=static_cast<Shared*>(raw);
    if (initialize) {
        new (shared) Shared;
        shared->object_bytes=sizeof(Shared);
    }
    if (shared->object_magic!=magic || shared->abi_version!=version ||
        shared->object_bytes!=sizeof(Shared) ||
        shared->capacity!=worker_io::max_frame ||
        reinterpret_cast<std::uintptr_t>(&shared->request.state)%
            alignof(std::atomic<std::uint32_t>)!=0 ||
        reinterpret_cast<std::uintptr_t>(&shared->response.state)%
            alignof(std::atomic<std::uint32_t>)!=0) {
        ::munmap(shared,sizeof(Shared));
        throw std::runtime_error("worker mailbox ABI mismatch");
    }
    return shared;
}

inline void unmap(Shared* shared) noexcept
{ if (shared) ::munmap(shared,sizeof(Shared)); }

inline std::uint32_t* futex_word(Slot& slot)
{ return reinterpret_cast<std::uint32_t*>(&slot.state); }

inline void wake(Slot& slot)
{
    const auto result=::syscall(SYS_futex,futex_word(slot),FUTEX_WAKE,1,
                                nullptr,nullptr,0);
    if (result<0) throw std::runtime_error("worker mailbox futex wake failed");
}

template <typename Alive>
void send(Slot& slot,std::string_view body,std::chrono::milliseconds timeout,
          Alive&& alive)
{
    const auto deadline=std::chrono::steady_clock::now()+timeout;
    if (body.empty() || body.size()>worker_io::max_frame)
        throw std::invalid_argument("worker mailbox send length");
    const auto before=slot.state.load(std::memory_order_acquire);
    if (before!=empty && before!=waiting)
        throw std::runtime_error("worker mailbox reentrant publication");
    if (!alive() || std::chrono::steady_clock::now()>=deadline)
        throw std::runtime_error("worker mailbox send peer/deadline");
    slot.length=static_cast<std::uint32_t>(body.size());
    std::memcpy(slot.payload,body.data(),body.size());
    if (std::chrono::steady_clock::now()>=deadline)
        throw std::runtime_error("worker mailbox send timeout");
    const auto previous=slot.state.exchange(ready,std::memory_order_acq_rel);
    if (previous!=empty && previous!=waiting)
        throw std::runtime_error("worker mailbox publication state");
    if (previous==waiting) wake(slot);
}

template <typename Alive>
std::string receive(Slot& slot,std::chrono::milliseconds timeout,
                    Alive&& alive)
{
    const auto deadline=std::chrono::steady_clock::now()+timeout;
    for (unsigned spin=0;;++spin) {
        auto state=slot.state.load(std::memory_order_acquire);
        if (state==ready) {
            const auto length=slot.length;
            if (length==0 || length>worker_io::max_frame)
                throw std::runtime_error("worker mailbox receive length");
            std::string body(slot.payload,slot.payload+length);
            slot.state.store(empty,std::memory_order_release);
            return body;
        }
        if (state!=empty && state!=waiting)
            throw std::runtime_error("worker mailbox receive state");
        if (spin<512) {
#if defined(__x86_64__)
            __asm__ __volatile__("pause");
#endif
            continue;
        }
        if (!alive()) throw std::runtime_error("worker mailbox peer died");
        const auto now=std::chrono::steady_clock::now();
        if (now>=deadline) throw std::runtime_error("worker mailbox receive timeout");
        if (state==empty) {
            auto expected=empty;
            if (!slot.state.compare_exchange_strong(expected,waiting,
                    std::memory_order_acq_rel,std::memory_order_acquire))
                continue;
        }
        // A READY publication before this syscall returns EAGAIN; it cannot
        // be lost. The finite park also discovers death on the sideband fd.
        const auto remaining=std::chrono::duration_cast<
            std::chrono::nanoseconds>(deadline-now);
        const auto park=std::min(remaining,
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::milliseconds(10)));
        const auto ns=park.count();
        timespec relative{static_cast<time_t>(ns/1000000000),
                          static_cast<long>(ns%1000000000)};
        const auto result=::syscall(SYS_futex,futex_word(slot),FUTEX_WAIT,
                                    waiting,&relative,nullptr,0);
        if (result<0 && errno!=EAGAIN && errno!=EINTR && errno!=ETIMEDOUT)
            throw std::runtime_error("worker mailbox futex wait failed");
    }
}

} // namespace hbfsim::ucie::shm_rpc
