#include "parallel_component_accounting_v1.hpp"
#include <hbfsim/ucie/worker_protocol.hpp>
#include <hbfsim/ucie/multistack_profile.hpp>
#include "worker_io.hpp"
#include "typed_rpc.hpp"
#include "reserve_submit_single_v1.hpp"
#include "ordered_admission_v1.hpp"
#include "compact_consume_v1.hpp"
#include "next_event_snapshot_v1.hpp"
#include "shm_rpc.hpp"
#include "worker_handler.hpp"

#include <json.hpp>

#include <chrono>
#include <array>
#include <thread>
#include <cerrno>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace hbfsim::ucie {
namespace {
using nlohmann::json;

// Parent-side resources remain owned even if path conversion/map/fork throws.
struct StartupOwnedFd {
    int value{-1};
    explicit StartupOwnedFd(int fd) noexcept : value(fd) {}
    ~StartupOwnedFd() { reset(); }
    StartupOwnedFd(const StartupOwnedFd&)=delete;
    StartupOwnedFd& operator=(const StartupOwnedFd&)=delete;
    void reset(int fd=-1) noexcept { if(value>=0) ::close(value); value=fd; }
    int release() noexcept { return std::exchange(value,-1); }
};

void require_ok(const json& reply,std::uint64_t seq,std::uint32_t stack)
{
    if (!reply.is_object() || !reply.contains("sequence") ||
        !reply["sequence"].is_number_unsigned() ||
        reply["sequence"].get<std::uint64_t>()!=seq ||
        !reply.contains("stack_id") ||
        reply["stack_id"].get<std::uint32_t>()!=stack)
        throw std::runtime_error("worker IPC sequence/stack mismatch");
    if (!reply.value("ok",false))
        throw std::runtime_error("worker rejected command: "+
                                 reply.value("error",std::string("unknown")));
}

constexpr std::uint32_t capacity_version=1;
constexpr std::size_t capacity_modules=16; // this reviewed profile only
struct CapacitySnapshot {
    std::uint64_t horizon{};
    std::array<std::uint32_t,capacity_modules> outstanding{};
};

struct TypedReadyPage {
    std::uint32_t total{};
    std::vector<WorkerReady> records;
};

TypedReadyPage decode_ready(typed_rpc::Reader& reply,bool has_time,
    CapacitySnapshot* capacity=nullptr,
    const std::array<std::uint32_t,capacity_modules>* limits=nullptr,
    std::uint64_t expected_horizon=0,
    next_event_snapshot_v1::Snapshot* event=nullptr)
{
    const auto time=has_time ? reply.u64() : 0;
    if ((capacity && (!has_time || !limits || time!=expected_horizon)) ||
        (event && (!has_time || time!=expected_horizon)))
        throw std::runtime_error("capacity advance horizon mismatch");
    TypedReadyPage page;
    page.total=reply.u32();
    const auto count=reply.u32();
    if (count>16 || count>page.total)
        throw std::runtime_error("typed worker ready page size");
    for (std::uint32_t i=0;i<count;++i) {
        WorkerReady ready;
        ready.request_id=reply.u64();
        ready.module_id=reply.u32();
        ready.result=typed_rpc::device_result(reply.u32());
        ready.ar_delivered_ns=reply.u64();
        ready.media_ready_ns=reply.u64();
        ready.response_delivered_ns=reply.u64();
        page.records.push_back(ready);
    }
    if (capacity) {
        if (reply.u32()!=capacity_version || reply.u64()!=expected_horizon ||
            reply.u32()!=capacity_modules)
            throw std::runtime_error("capacity snapshot version/horizon/length");
        capacity->horizon=expected_horizon;
        for (std::size_t m=0;m<capacity_modules;++m) {
            capacity->outstanding[m]=reply.u32();
            if (reply.u32()!=limits->at(m) ||
                capacity->outstanding[m]>limits->at(m))
                throw std::runtime_error("capacity snapshot bounds/profile");
        }
    }
    if (event) *event=next_event_snapshot_v1::decode(reply,expected_horizon);
    reply.done();
    return page;
}
}

struct StackWorkerClient::Impl {
    pid_t pid{-1};
    int fd{-1};
    std::uint64_t sequence{};
    std::uint32_t stack{};
    bool typed_active{};
    bool shm_active{};
    const bool reserve_submit_requested=[] {
        const auto* v=std::getenv("HBFSIM_UCIE_RESERVE_SUBMIT_SINGLE_V1");
        if (!v || !(*v) || std::string(v)=="0")
            return false;
        if (std::string(v)!="1")
            throw std::invalid_argument("single-child admission flag must be0 or1");
        return true;
    }();
    bool reserve_submit_active{};
    const bool ordered_requested=[] {
        const auto* value=std::getenv("HBFSIM_UCIE_ORDERED_ADMISSION_V1");
        if (!value || std::string(value)=="0") return false;
        if (std::string(value)!="1")
            throw std::invalid_argument("ordered admission flag must be0 or1");
        return true;
    }();
    const bool compact_requested=[] {
        const auto* v=std::getenv("HBFSIM_UCIE_COMPACT_CONSUME_ORDERED_V1");
        if(!v || std::string(v)=="0") return false;
        if(std::string(v)!="1") throw std::invalid_argument("compact Consume flag must be0 or1");
        return true;
    }();
    bool compact_active{};
    std::uint64_t compact_commands{},compact_offered{},compact_confirmed{},compact_terminal{};
    std::array<std::uint64_t,17> compact_histogram{};
    std::array<std::uint64_t,4> compact_results{};
    bool ordered_active{};
    bool ordered_topology_eligible{};
    std::uint64_t ordered_commands{},ordered_reserved{},ordered_denied{},
        ordered_accepted{},ordered_released{},ordered_terminal{};
    std::uint64_t fusion_commands{},fusion_reserved{},fusion_denied{},
        fusion_accepted{},fusion_released{};
    bool poisoned{};
    bool capacity_active{};
    const bool capacity_requested=[] {
        const auto* value=std::getenv("HBFSIM_UCIE_CAPACITY_SNAPSHOT_V1");
        return value && std::string(value)=="1";
    }();
    const std::thread::id owner_thread{std::this_thread::get_id()};
    std::array<std::uint32_t,capacity_modules> capacity_limits{};
    std::optional<CapacitySnapshot> capacity;
    std::uint64_t capacity_skipped{};
    const bool capacity_zero_inactive_requested=[] {
        const auto* value=std::getenv("HBFSIM_UCIE_CAPACITY_ZERO_INACTIVE_V1");
        if (!value || std::string(value)=="0")
            return false;
        if (std::string(value)!="1")
            throw std::invalid_argument("capacity-zero inactive flag must be 0 or 1");
        return true;
    }();
    std::uint64_t capacity_zero_queries{},capacity_zero_hits{},
        capacity_zero_fallbacks{};
    const bool next_event_requested=[] {
        const auto* value=std::getenv("HBFSIM_UCIE_NEXT_EVENT_SNAPSHOT_V1");
        return value && std::string(value)=="1";
    }();
    bool next_event_active{};
    next_event_snapshot_v1::SingleUse next_event_saved;
    std::uint64_t next_event_hits{};
    struct GatherPending {
        typed_rpc::Op kind;
        std::uint64_t sequence{},to_horizon{};
        std::chrono::steady_clock::time_point deadline;
    };
    struct InitialHelloPending {
        std::uint64_t sequence{};
        bool typed_requested{},shm_requested{};
        std::chrono::steady_clock::time_point deadline;
    };
    std::optional<InitialHelloPending> initial_hello;
    std::optional<GatherPending> gather_pending;
    std::optional<compact_consume_v1::Request> compact_pending_request;
    void require_owner() const
    {
        if (owner_thread!=std::this_thread::get_id())
            throw std::logic_error("worker gather requires owner thread");
    }
    void require_startup_ready() const
    {
        if (!initial_hello) return;
        require_owner(); // wrong owner cannot mutate even accounting or poison
        throw std::logic_error("ordinary worker API during pending initial HELLO");
    }
    std::chrono::milliseconds initial_remaining() const
    {
        require_owner();
        if (!initial_hello) throw std::logic_error("initial worker HELLO not pending");
        const auto remaining=initial_hello->deadline-std::chrono::steady_clock::now();
        if (remaining<=std::chrono::steady_clock::duration::zero())
            throw std::runtime_error("initial worker HELLO dispatch deadline expired");
        return std::chrono::ceil<std::chrono::milliseconds>(remaining);
    }
    void abandon_initial() noexcept
    {
        if (initial_hello) { poisoned=true; initial_hello.reset(); }
    }
    void require_idle_rpc()
    {
        require_startup_ready();
        if (!gather_pending) return; // original synchronous owner contract unchanged
        require_owner();
        poisoned=true;
        invalidate_capacity(); invalidate_next_event();
        throw std::logic_error("unrelated RPC during pending worker gather");
    }
    std::chrono::milliseconds gather_remaining() const
    {
        require_owner();
        if (!gather_pending) throw std::logic_error("worker gather not pending");
        const auto remaining=gather_pending->deadline-std::chrono::steady_clock::now();
        if (remaining<=std::chrono::steady_clock::duration::zero())
            throw std::runtime_error("worker gather dispatch deadline expired");
        return std::chrono::ceil<std::chrono::milliseconds>(remaining);
    }
    void require_pending_kind(typed_rpc::Op kind) const
    {
        require_owner();
        if (!gather_pending || gather_pending->kind!=kind)
            throw std::logic_error("worker gather pending operation kind mismatch");
    }
    void abandon_gather() noexcept
    {
        invalidate_capacity(); invalidate_next_event();
        if (gather_pending) { poisoned=true; gather_pending.reset(); }
        compact_pending_request.reset();
    }
    void invalidate_next_event() noexcept { next_event_saved.invalidate(); }
    void negotiate_next_event(const json& hello)
    {
        if (!next_event_requested || !typed_active ||
            !hello.contains("next_event_snapshot_version")) return; // old peer
        const auto& value=hello.at("next_event_snapshot_version");
        if (!value.is_number_unsigned() ||
            value.get<std::uint64_t>()!=next_event_snapshot_v1::version)
            throw std::runtime_error("next-event HELLO version mismatch");
        next_event_active=true;
    }

    void negotiate_reserve_submit(const json& hello)
    {
        if (!reserve_submit_requested || local || !typed_active || !shm_active ||
            !hello.contains("reserve_submit_single_version"))
            return;
        const auto& value=hello.at("reserve_submit_single_version");
        if (!value.is_number_unsigned() || value.get<std::uint64_t>()!=
                reserve_submit_single_v1::version)
            throw std::runtime_error("single-child admission HELLO version mismatch");
        reserve_submit_active=true;
    }
    void negotiate_compact(const json& hello)
    {
        if(!compact_requested || !ordered_topology_eligible || local || !typed_active || !shm_active) return;
        const bool v=hello.contains("compact_consume_version"), n=hello.contains("compact_consume_max_count");
        if(!v && !n) return; // Capability absent: choose old Consume before any new send.
        if(!v || !n || !hello.at("compact_consume_version").is_number_unsigned() ||
           hello.at("compact_consume_version").get<std::uint64_t>()!=compact_consume_v1::version ||
           !hello.at("compact_consume_max_count").is_number_unsigned() ||
           hello.at("compact_consume_max_count").get<std::uint64_t>()!=compact_consume_v1::max_count ||
           !hello.at("modules").is_number_unsigned() || hello.at("modules").get<std::uint64_t>()!=16)
            throw std::runtime_error("compact Consume HELLO version/limit/profile mismatch");
        compact_active=true;
    }
    void negotiate_ordered(const json& hello)
    {
        if (!ordered_requested || !ordered_topology_eligible || local || !typed_active || !shm_active) return;
        const bool has_version=hello.contains("ordered_admission_version");
        const bool has_limit=hello.contains("ordered_admission_max_count");
        if (!has_version && !has_limit) return; // old peer: choose old commands before send
        if (!has_version || !has_limit ||
            !hello.at("ordered_admission_version").is_number_unsigned() ||
            hello.at("ordered_admission_version").get<std::uint64_t>()!=ordered_admission_v1::version ||
            !hello.at("ordered_admission_max_count").is_number_unsigned() ||
            hello.at("ordered_admission_max_count").get<std::uint64_t>()!=ordered_admission_v1::max_count ||
            !hello.at("modules").is_number_unsigned() ||
            hello.at("modules").get<std::uint64_t>()!=ordered_admission_v1::modules)
            throw std::runtime_error("ordered admission HELLO version/limit/profile mismatch");
        ordered_active=true;
    }
    void invalidate_capacity() noexcept { capacity.reset(); }
    void negotiate_capacity(const json& hello)
    {
        if (!capacity_requested || !typed_active ||
            !hello.contains("capacity_snapshot_version")) return; // old peer
        if (!hello.at("capacity_snapshot_version").is_number_unsigned() ||
            hello.at("capacity_snapshot_version").get<std::uint64_t>()!=capacity_version ||
            !hello.at("modules").is_number_unsigned() ||
            hello.at("modules").get<std::uint64_t>()!=capacity_modules)
            throw std::runtime_error("capacity HELLO version/profile mismatch");
        const auto& values=hello.at("capacity_limits");
        if (!values.is_array() || values.size()!=capacity_modules)
            throw std::runtime_error("capacity HELLO limits length");
        for (std::size_t m=0;m<capacity_modules;++m) {
            const auto& value=values.at(m);
            if (!value.is_number_unsigned() || !value.get<std::uint64_t>() ||
                value.get<std::uint64_t>()>std::numeric_limits<std::uint32_t>::max())
                throw std::runtime_error("capacity HELLO limits range");
            capacity_limits[m]=value.get<std::uint32_t>();
        }
        capacity_active=true;
    }
    struct HelloCapabilities {
        bool typed_active{},shm_active{},capacity_active{},next_event_active{},
            reserve_submit_active{},ordered_active{},compact_active{};
        std::array<std::uint32_t,capacity_modules> capacity_limits{};
    };
    HelloCapabilities validate_initial_reply(const json& hello,
        bool typed_requested,bool shm_requested) const
    {
        HelloCapabilities staged;
        if (hello.value("protocol",0)!=1)
            throw std::runtime_error("worker protocol version mismatch");
        if (typed_requested) {
            if (hello.value("typed_rpc_version",0)!=typed_rpc::version)
                throw std::runtime_error("typed worker protocol not acknowledged");
            staged.typed_active=true;
        }
        if (shm_requested) {
            if (hello.value("shm_rpc_version",0)!=shm_rpc::version)
                throw std::runtime_error("shm worker protocol not acknowledged");
            staged.shm_active=true;
        }
        [&] {
        if (!capacity_requested || !staged.typed_active ||
            !hello.contains("capacity_snapshot_version")) return; // old peer
        if (!hello.at("capacity_snapshot_version").is_number_unsigned() ||
            hello.at("capacity_snapshot_version").get<std::uint64_t>()!=capacity_version ||
            !hello.at("modules").is_number_unsigned() ||
            hello.at("modules").get<std::uint64_t>()!=capacity_modules)
            throw std::runtime_error("capacity HELLO version/profile mismatch");
        const auto& values=hello.at("capacity_limits");
        if (!values.is_array() || values.size()!=capacity_modules)
            throw std::runtime_error("capacity HELLO limits length");
        for (std::size_t m=0;m<capacity_modules;++m) {
            const auto& value=values.at(m);
            if (!value.is_number_unsigned() || !value.get<std::uint64_t>() ||
                value.get<std::uint64_t>()>std::numeric_limits<std::uint32_t>::max())
                throw std::runtime_error("capacity HELLO limits range");
            staged.capacity_limits[m]=value.get<std::uint32_t>();
        }
        staged.capacity_active=true;
        }();
        [&] {
        if (!next_event_requested || !staged.typed_active ||
            !hello.contains("next_event_snapshot_version")) return; // old peer
        const auto& value=hello.at("next_event_snapshot_version");
        if (!value.is_number_unsigned() ||
            value.get<std::uint64_t>()!=next_event_snapshot_v1::version)
            throw std::runtime_error("next-event HELLO version mismatch");
        staged.next_event_active=true;
        }();
        [&] {
        if (!reserve_submit_requested || local || !staged.typed_active || !staged.shm_active ||
            !hello.contains("reserve_submit_single_version"))
            return;
        const auto& value=hello.at("reserve_submit_single_version");
        if (!value.is_number_unsigned() || value.get<std::uint64_t>()!=
                reserve_submit_single_v1::version)
            throw std::runtime_error("single-child admission HELLO version mismatch");
        staged.reserve_submit_active=true;
        }();
        [&] {
        if (!ordered_requested || !ordered_topology_eligible || local || !staged.typed_active || !staged.shm_active) return;
        const bool has_version=hello.contains("ordered_admission_version");
        const bool has_limit=hello.contains("ordered_admission_max_count");
        if (!has_version && !has_limit) return; // old peer: choose old commands before send
        if (!has_version || !has_limit ||
            !hello.at("ordered_admission_version").is_number_unsigned() ||
            hello.at("ordered_admission_version").get<std::uint64_t>()!=ordered_admission_v1::version ||
            !hello.at("ordered_admission_max_count").is_number_unsigned() ||
            hello.at("ordered_admission_max_count").get<std::uint64_t>()!=ordered_admission_v1::max_count ||
            !hello.at("modules").is_number_unsigned() ||
            hello.at("modules").get<std::uint64_t>()!=ordered_admission_v1::modules)
            throw std::runtime_error("ordered admission HELLO version/limit/profile mismatch");
        staged.ordered_active=true;
        }();
        [&] {
        if(!compact_requested || !ordered_topology_eligible || local || !staged.typed_active || !staged.shm_active) return;
        const bool v=hello.contains("compact_consume_version"), n=hello.contains("compact_consume_max_count");
        if(!v && !n) return; // Capability absent: choose old Consume before any new send.
        if(!v || !n || !hello.at("compact_consume_version").is_number_unsigned() ||
           hello.at("compact_consume_version").get<std::uint64_t>()!=compact_consume_v1::version ||
           !hello.at("compact_consume_max_count").is_number_unsigned() ||
           hello.at("compact_consume_max_count").get<std::uint64_t>()!=compact_consume_v1::max_count ||
           !hello.at("modules").is_number_unsigned() || hello.at("modules").get<std::uint64_t>()!=16)
            throw std::runtime_error("compact Consume HELLO version/limit/profile mismatch");
        staged.compact_active=true;
        }();
        return staged;
    }
    void install_initial_reply(const HelloCapabilities& staged) noexcept
    {
        typed_active=staged.typed_active;shm_active=staged.shm_active;
        capacity_active=staged.capacity_active;capacity_limits=staged.capacity_limits;
        next_event_active=staged.next_event_active;
        reserve_submit_active=staged.reserve_submit_active;
        ordered_active=staged.ordered_active;compact_active=staged.compact_active;
    }
    shm_rpc::Shared* shm{};
    std::unique_ptr<WorkerCommandState> local;
    std::string local_response;
    struct ErrorGuard {
        Impl& impl;
        int active_exceptions{std::uncaught_exceptions()};
        ~ErrorGuard()
        { if (std::uncaught_exceptions()>active_exceptions) {
            impl.invalidate_capacity();
            impl.invalidate_next_event();
            impl.poisoned=true;
          } }
    };

    bool sideband_alive() const
    {
        pollfd state{fd,static_cast<short>(POLLIN|POLLHUP|POLLERR),0};
        int result;
        do { result=::poll(&state,1,0); } while (result<0 && errno==EINTR);
        if (result<0) throw std::runtime_error("worker sideband poll failed");
        return result==0 || !(state.revents&(POLLHUP|POLLERR|POLLNVAL));
    }

    void send(const std::string& body,std::chrono::milliseconds timeout)
    {
        parallel_component_accounting_v1::PhysicalScope physical_scope{stack,!local,true};
        if (local) {
            local_response=local->handle(body);
            return;
        }
        if (shm_active) shm_rpc::send(shm->request,body,timeout,
                                      [&] { return sideband_alive(); });
        else worker_io::send_frame(fd,body,timeout);
    }

    std::string receive(std::chrono::milliseconds timeout)
    {
        parallel_component_accounting_v1::PhysicalScope physical_scope{stack,!local,false};
        if (local) return std::exchange(local_response,{});
        if (shm_active) return shm_rpc::receive(shm->response,timeout,
                                                 [&] { return sideband_alive(); });
        return worker_io::receive_frame(fd,timeout);
    }

    json exchange(json command,std::chrono::milliseconds timeout=
                 std::chrono::seconds(30))
    {
      if (poisoned) throw std::runtime_error("poisoned worker transport");
      try {
        const auto seq=++sequence;
        command["sequence"]=seq;
        command["stack_id"]=stack;
        auto payload=command.dump();
        if (typed_active) payload=typed_rpc::tagged_json(std::move(payload));
        send(payload,timeout);
        auto reply_body=receive(timeout);
        if (typed_active) {
            if (reply_body.empty() || reply_body[0]!=typed_rpc::json_tag)
                throw std::runtime_error("worker tagged JSON reply mismatch");
            reply_body.erase(0,1);
        }
        auto reply=json::parse(reply_body);
        require_ok(reply,seq,stack);
        return reply;
      } catch (...) { invalidate_next_event(); poisoned=true; throw; }
    }
    template <typename Encode>
    typed_rpc::Reader exchange_typed(typed_rpc::Op op,Encode&& encode,
        std::chrono::milliseconds timeout=std::chrono::seconds(30),
        bool gather_dispatched=false)
    {
        if (poisoned) throw std::runtime_error("poisoned worker transport");
      try {
        if (!typed_active)
            throw std::logic_error("typed worker RPC without negotiation");
        const auto seq=++sequence;
        typed_rpc::Writer command(op,seq,stack);
        encode(command);
        send(std::move(command).finish(),timeout);
        auto body=receive(gather_dispatched ? gather_remaining() : timeout);
        return decode_typed_response(std::move(body),op,seq);
      } catch (...) { invalidate_next_event(); poisoned=true; throw; }
    }
    typed_rpc::Reader decode_typed_response(std::string body,
        typed_rpc::Op op,std::uint64_t seq)
    {
        if (!body.empty() && body[0]==typed_rpc::json_tag) {
            auto error=json::parse(body.substr(1));
            require_ok(error,seq,stack);
            throw std::runtime_error("typed worker returned JSON success");
        }
        typed_rpc::Reader reply(std::move(body));
        reply.poison_if_abandoned(&poisoned);
        reply.expect(op,seq,stack);
        return reply;
    }
    std::vector<WorkerReady> decode_ready_response(typed_rpc::Reader first,
        std::uint64_t to_horizon,bool dispatched)
    {
        CapacitySnapshot pending;
        next_event_snapshot_v1::Snapshot pending_event;
        auto page=decode_ready(first,true,
            capacity_active ? &pending : nullptr,
            capacity_active ? &capacity_limits : nullptr,to_horizon,
            next_event_active ? &pending_event : nullptr);
        std::vector<WorkerReady> out=std::move(page.records);
        while (out.size()<page.total) {
            if (out.empty() || out.size()>std::numeric_limits<std::uint32_t>::max())
                throw std::runtime_error("typed ready pagination did not progress");
            auto next=exchange_typed(typed_rpc::Op::ReadyPage,
                [&](typed_rpc::Writer& writer) {
                    writer.u64(to_horizon);
                    writer.u32(static_cast<std::uint32_t>(out.size()));
                },dispatched ? gather_remaining() : std::chrono::seconds(30),dispatched);
            auto next_page=decode_ready(next,false);
            if (next_page.total!=page.total || next_page.records.empty())
                throw std::runtime_error("typed ready snapshot size/progress changed");
            out.insert(out.end(),next_page.records.begin(),next_page.records.end());
        }
        if (out.size()!=page.total)
            throw std::runtime_error("typed ready snapshot overflow");
        if (dispatched) (void)gather_remaining();
        if (capacity_active) capacity=pending;
        // First response AND every page validate before either snapshot installs.
        if (next_event_active && owner_thread==std::this_thread::get_id())
            next_event_saved.install(pending_event);
        return out;
    }
    ~Impl() { shutdown(); } // also runs when StackWorkerClient construction throws
    void shutdown() noexcept
    {
        abandon_initial(); // never send STOP while an unread HELLO is outstanding
        abandon_gather(); // poison unread replies before any STOP exchange
        invalidate_next_event();
        invalidate_capacity();
        if (local) {
            try {
                if (!poisoned && !local->stopped())
                    (void)exchange({{"type","STOP"}},
                                   std::chrono::milliseconds(100));
            } catch (...) {}
            local.reset();
            return;
        }
        if (pid<0) {
            if (fd>=0) { ::close(fd);fd=-1; }
            shm_rpc::unmap(shm);shm=nullptr;
            return;
        }
        try {
            if (fd>=0 && !poisoned)
                (void)exchange({{"type","STOP"}},
                               std::chrono::milliseconds(100));
        } catch (...) {}
        if (fd>=0) { ::close(fd); fd=-1; }
        int status=0;
        for (int attempts=0;attempts<100;++attempts) {
            const auto observed=::waitpid(pid,&status,WNOHANG);
            if (observed==pid || (observed<0 && errno==ECHILD))
                { pid=-1; shm_rpc::unmap(shm); shm=nullptr; return; }
            ::usleep(10000);
        }
        ::kill(pid,SIGTERM); // exact worker PID owned by this client
        for (int attempts=0;attempts<100;++attempts) {
            const auto observed=::waitpid(pid,&status,WNOHANG);
            if (observed==pid || (observed<0 && errno==ECHILD))
                { pid=-1; shm_rpc::unmap(shm); shm=nullptr; return; }
            ::usleep(10000);
        }
        ::kill(pid,SIGKILL);
        while (::waitpid(pid,&status,0)<0 && errno==EINTR) {}
        pid=-1;
        shm_rpc::unmap(shm);
        shm=nullptr;
    }
};

StackWorkerClient::StackWorkerClient(const std::filesystem::path& executable,
        const std::filesystem::path& profile,std::uint32_t stack_id,
        bool top_profile,bool local)
    : StackWorkerClient(executable,profile,stack_id,top_profile,local,false) {}

StackWorkerClient::StackWorkerClient(const std::filesystem::path& executable,
        const std::filesystem::path& profile,std::uint32_t stack_id,
        bool top_profile,bool local,bool defer_initial_hello)
    : impl_(std::make_unique<Impl>()),stack_id_(stack_id)
{
    impl_->stack=stack_id;
    if (local) {
        impl_->local=std::make_unique<WorkerCommandState>(
            profile,top_profile,stack_id,false,true);
        json hello={{"type","HELLO"},
                    {"typed_rpc_version",typed_rpc::version}};
        if (impl_->next_event_requested)
            hello["next_event_snapshot_version"]=next_event_snapshot_v1::version;
        if (impl_->capacity_requested)
            hello["capacity_snapshot_version"]=capacity_version;
        try {
            const auto reply=impl_->exchange(std::move(hello),
                                              std::chrono::minutes(10));
            if (reply.value("protocol",0)!=1 ||
                reply.value("typed_rpc_version",0)!=typed_rpc::version)
                throw std::runtime_error("local worker typed HELLO mismatch");
            impl_->typed_active=true;
            impl_->negotiate_capacity(reply);
            impl_->negotiate_next_event(reply);
        } catch (...) {
            impl_->shutdown();
            throw;
        }
        std::cerr << "LOCAL_STACK_V1_ACTIVE stack=" << stack_id << '\n';
        return;
    }
    // Parser mode is not topology. Inspect actual topology once, before fork/HELLO.
    // OFF/local paths do not gain parsing work; the dispatch hot path reads only the capability.
    if (impl_->ordered_requested || impl_->compact_requested) {
        if (top_profile) {
            const auto top=load_multistack_profile(profile);
            impl_->ordered_topology_eligible=!top.shared_upstream &&
                top.modules_per_stack==ordered_admission_v1::modules;
        } else {
            impl_->ordered_topology_eligible=
                load_device_profile(profile).layout.host_channels==ordered_admission_v1::modules;
        }
    }
    int pair[2]{-1,-1};
    if (::socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,pair)!=0)
        throw std::runtime_error("worker IPC socket creation failed");
    StartupOwnedFd socket_parent{pair[0]},socket_child{pair[1]},mailbox{-1};
    const auto* shm_env=std::getenv("HBFSIM_UCIE_SHM_RPC");
    const bool shm_requested=shm_env && std::string(shm_env)=="1";
    const auto* typed_env=std::getenv("HBFSIM_UCIE_TYPED_RPC");
    const bool typed_requested=shm_requested ||
        (typed_env && std::string(typed_env)=="1");
    int shm_fd=-1;
    try {
        if (shm_requested) {
            shm_fd=shm_rpc::create_fd();
            mailbox.reset(shm_fd);
            impl_->shm=shm_rpc::map_fd(shm_fd,true);
        }
    } catch (...) {
        mailbox.reset();socket_parent.reset();socket_child.reset();
        throw;
    }
    const auto exe=std::filesystem::absolute(executable).string();
    const auto path=std::filesystem::absolute(profile).string();
    const auto id=std::to_string(stack_id);
    std::chrono::steady_clock::time_point startup_origin{};
    if (defer_initial_hello) startup_origin=std::chrono::steady_clock::now();
    const auto pid=::fork();
    if (pid<0) {
        socket_parent.reset();socket_child.reset();mailbox.reset();
        shm_rpc::unmap(impl_->shm);
        impl_->shm=nullptr;
        throw std::runtime_error("worker fork failed");
    }
    if (pid==0) {
        ::close(pair[0]);
        if (shm_requested) {
            const int socket_temp=::fcntl(pair[1],F_DUPFD_CLOEXEC,5);
            const int shm_temp=::fcntl(shm_fd,F_DUPFD_CLOEXEC,5);
            if (socket_temp<0 || shm_temp<0 ||
                ::dup2(socket_temp,3)<0 || ::dup2(shm_temp,4)<0 ||
                ::fcntl(3,F_SETFD,0)<0 || ::fcntl(4,F_SETFD,0)<0 ||
                ::setenv("HBFSIM_UCIE_SHM_FD","4",1)!=0)
                _exit(126);
            ::close(socket_temp); ::close(shm_temp);
            if (pair[1]!=3 && pair[1]!=4) ::close(pair[1]);
            if (shm_fd!=3 && shm_fd!=4) ::close(shm_fd);
        } else {
            if (::dup2(pair[1],3)<0 || ::fcntl(3,F_SETFD,0)<0)
                _exit(126);
            if (pair[1]!=3) ::close(pair[1]);
        }
        ::execl(exe.c_str(),exe.c_str(),
                top_profile?"--top-profile":"--profile",path.c_str(),
                "--stack",id.c_str(),static_cast<char*>(nullptr));
        _exit(127);
    }
    impl_->pid=pid; // no throwing operations between spawn and exact PID ownership
    impl_->fd=socket_parent.release();
    socket_child.reset();mailbox.reset();
    impl_->stack=stack_id;
    try {
        // Dense 512 GiB MQSim metadata allocation can be minutes of host
        // initialization; this finite wall-clock deadline is not model time.
        json hello={{"type","HELLO"}};
        if (typed_requested) hello["typed_rpc_version"]=typed_rpc::version;
        if (shm_requested) hello["shm_rpc_version"]=shm_rpc::version;
        if (impl_->next_event_requested && typed_requested)
            hello["next_event_snapshot_version"]=next_event_snapshot_v1::version;
        if (impl_->capacity_requested && typed_requested)
            hello["capacity_snapshot_version"]=capacity_version;
        if (impl_->reserve_submit_requested && shm_requested)
            hello["reserve_submit_single_version"]=reserve_submit_single_v1::version;
        if (impl_->ordered_requested && shm_requested && impl_->ordered_topology_eligible) {
            hello["ordered_admission_version"]=ordered_admission_v1::version;
            hello["ordered_admission_max_count"]=ordered_admission_v1::max_count;
        }
        if(impl_->compact_requested && shm_requested && impl_->ordered_topology_eligible) {
            hello["compact_consume_version"]=compact_consume_v1::version;
            hello["compact_consume_max_count"]=compact_consume_v1::max_count;
        }
        if (defer_initial_hello) {
            const auto seq=++impl_->sequence;
            hello["sequence"]=seq;hello["stack_id"]=stack_id;
            impl_->initial_hello=Impl::InitialHelloPending{seq,typed_requested,
                shm_requested,startup_origin+std::chrono::minutes(10)};
            // Both capability flags are still false: original socket framing only.
            impl_->send(hello.dump(),impl_->initial_remaining());
            return;
        }
        const auto reply=impl_->exchange(std::move(hello),std::chrono::minutes(10));
        if (reply.value("protocol",0)!=1)
            throw std::runtime_error("worker protocol version mismatch");
        if (typed_requested) {
            if (reply.value("typed_rpc_version",0)!=typed_rpc::version)
                throw std::runtime_error("typed worker protocol not acknowledged");
            impl_->typed_active=true;
        }
        if (shm_requested) {
            if (reply.value("shm_rpc_version",0)!=shm_rpc::version)
                throw std::runtime_error("shm worker protocol not acknowledged");
            impl_->shm_active=true;
        }
        impl_->negotiate_capacity(reply);
        impl_->negotiate_next_event(reply);
        impl_->negotiate_reserve_submit(reply);
        impl_->negotiate_ordered(reply);
        impl_->negotiate_compact(reply);
    } catch (...) {
        impl_->shutdown();
        throw;
    }
}

bool StackWorkerClient::initial_hello_pending() const
{
    impl_->require_owner();
    return bool(impl_->initial_hello);
}

void StackWorkerClient::finish_initial_hello()
{
    impl_->require_owner();
    if (!impl_->initial_hello) throw std::logic_error("initial worker HELLO not pending");
    Impl::ErrorGuard guard{*impl_};
    if (impl_->poisoned) throw std::runtime_error("poisoned worker transport");
    const auto expected=*impl_->initial_hello;
    auto reply=json::parse(impl_->receive(impl_->initial_remaining()));
    require_ok(reply,expected.sequence,impl_->stack);
    const auto staged=impl_->validate_initial_reply(reply,
        expected.typed_requested,expected.shm_requested);
    (void)impl_->initial_remaining(); // dispatch origin, after full decode, before installation
    impl_->install_initial_reply(staged);
    impl_->initial_hello.reset();
}

void StackWorkerClient::abandon_initial_hello() noexcept
{
    if (impl_) impl_->abandon_initial();
}

StackWorkerClient::~StackWorkerClient()
{
    if (impl_) {
        if(impl_->compact_requested) {
            std::cerr << "COMPACT_CONSUME_CLIENT stack=" << stack_id_ << " negotiated=" << impl_->compact_active
                << " commands=" << impl_->compact_commands << " offered=" << impl_->compact_offered
                << " confirmed=" << impl_->compact_confirmed << " terminal=" << impl_->compact_terminal;
            for(std::size_t i=0;i<impl_->compact_histogram.size();++i) std::cerr << " k" << i << '=' << impl_->compact_histogram[i];
            for(std::size_t i=0;i<4;++i) std::cerr << " result" << i << '=' << impl_->compact_results[i];
            std::cerr << '\n';
        }
        if (impl_->ordered_requested)
            std::cerr << "ORDERED_ADMISSION_CLIENT stack=" << stack_id_
                << " negotiated=" << impl_->ordered_active
                << " commands=" << impl_->ordered_commands
                << " reserved=" << impl_->ordered_reserved << " denied=" << impl_->ordered_denied
                << " accepted=" << impl_->ordered_accepted << " released=" << impl_->ordered_released
                << " terminal=" << impl_->ordered_terminal << '\n';
        if (impl_->reserve_submit_requested)
            std::cerr << "RESERVE_SUBMIT_SINGLE_CLIENT stack=" << stack_id_
                << " negotiated=" << impl_->reserve_submit_active
                << " commands=" << impl_->fusion_commands
                << " reserved=" << impl_->fusion_reserved
                << " denied=" << impl_->fusion_denied
                << " accepted=" << impl_->fusion_accepted
                << " released=" << impl_->fusion_released << '\n';
        if (impl_->capacity_requested)
            std::cerr << "CAPACITY_SNAPSHOT_V1 stack=" << stack_id_
                << " negotiated=" << impl_->capacity_active
                << " eligibility_skipped=" << impl_->capacity_skipped << '\n';
        if (impl_->capacity_zero_inactive_requested)
            std::cerr << "CAPACITY_ZERO_INACTIVE_V1 stack=" << stack_id_
                << " negotiated=" << impl_->capacity_active
                << " queries=" << impl_->capacity_zero_queries
                << " hits=" << impl_->capacity_zero_hits
                << " fallback=" << impl_->capacity_zero_fallbacks << '\n';
        if (impl_->next_event_requested)
            std::cerr << "NEXT_EVENT_SNAPSHOT_V1 stack=" << stack_id_
                << " negotiated=" << impl_->next_event_active
                << " query_hits=" << impl_->next_event_hits << '\n';
        impl_->shutdown();
    }
}

bool StackWorkerClient::transport_alive() const
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::TransportAlive};
    if (impl_ && impl_->gather_pending) impl_->require_owner();
    if (impl_ && impl_->local)
        return !impl_->poisoned && !impl_->local->stopped();
    if (!impl_ || impl_->fd < 0 || impl_->pid < 0) return false;
    pollfd state{impl_->fd,static_cast<short>(POLLIN|POLLHUP|POLLERR),0};
    int result;
    do { result=::poll(&state,1,0); } while (result<0 && errno==EINTR);
    if (result<0) throw std::runtime_error("worker transport poll failed");
    return result==0 || !(state.revents&(POLLHUP|POLLERR|POLLNVAL));
}

void StackWorkerClient::add_backing(const BackingRange& r)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Backing};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    (void)impl_->exchange({{"type","BACKING"},{"region_id",r.region_id},
        {"address",r.address},{"bytes",r.bytes},
        {"canonical_id",r.canonical_id},
        {"canonical_offset",r.canonical_offset},
        {"generation",r.generation},{"module_id",r.module_id},
        {"endpoint_id",r.endpoint_id},{"readable",r.readable}});
}

void StackWorkerClient::retire_backing_generation(
    std::uint64_t canonical_id,std::uint64_t generation,
    std::uint32_t module_id,std::uint64_t endpoint_id)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Retire};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    (void)impl_->exchange({{"type","RETIRE"},
        {"canonical_id",canonical_id},{"generation",generation},
        {"module_id",module_id},{"endpoint_id",endpoint_id}});
}

bool StackWorkerClient::reserve(std::uint64_t token,std::uint64_t horizon,
        const std::vector<std::uint32_t>& counts)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Reserve};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    if (impl_->typed_active &&
        counts.size()>std::numeric_limits<std::uint32_t>::max())
        throw std::overflow_error("typed reservation count overflow");
    Impl::ErrorGuard guard{*impl_};
    if (impl_->typed_active) {
        auto reply=impl_->exchange_typed(typed_rpc::Op::Reserve,
            [&](typed_rpc::Writer& writer) {
                writer.u64(token);
                writer.u64(horizon);
                writer.u32(static_cast<std::uint32_t>(counts.size()));
                for (const auto count:counts) writer.u32(count);
            });
        const bool reserved=reply.boolean();
        reply.done();
        return reserved;
    }
    const auto reply=impl_->exchange({{"type","RESERVE"},{"token",token},
        {"horizon",horizon},{"counts",counts}});
    return reply.at("reserved").get<bool>();
}

bool StackWorkerClient::reserve_submit_single_remote() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    return impl_->reserve_submit_active && !impl_->local &&
        impl_->typed_active && impl_->shm_active;
}

bool StackWorkerClient::reserve_submit_single(std::uint64_t token,
    std::uint64_t horizon,const std::vector<std::uint32_t>& counts,
    const DeviceRead& read,std::optional<PacketBackingProof> proof)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::ReserveSubmit};
    impl_->require_owner();
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    if (!reserve_submit_single_remote())
        throw std::logic_error("single-child admission without remote capability");
    if (read.stack_id!=stack_id_)
        throw std::invalid_argument("child read targets another stack");
    if (counts.size()>reserve_submit_single_v1::max_modules)
        throw std::overflow_error("single-child admission count frame bound");
    auto response=impl_->exchange_typed(typed_rpc::Op::ReserveSubmitSingle,
        [&](typed_rpc::Writer& w) {
            w.u32(reserve_submit_single_v1::version);
            w.u32(1);
            w.u64(token);
            w.u64(horizon);
            w.u32(static_cast<std::uint32_t>(counts.size()));
            for (const auto count:counts)
                w.u32(count);
            reserve_submit_single_v1::encode_read(w,read,proof);
        });
    const auto result=reserve_submit_single_v1::decode_reply(response,token,
        horizon,read.request_id);
    ++impl_->fusion_commands;
    if (result.reserved)
        ++impl_->fusion_reserved;
    else
        ++impl_->fusion_denied;
    if (result.accepted)
        ++impl_->fusion_accepted;
    if (result.reserved && !result.accepted)
        ++impl_->fusion_released;
    return result.accepted;
}

bool StackWorkerClient::ordered_admission_remote() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    return impl_->ordered_active && !impl_->local && impl_->typed_active && impl_->shm_active;
}

ordered_admission_v1::Reply StackWorkerClient::reserve_submit_ordered(
    const ordered_admission_v1::Request& request)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,false,
        parallel_component_accounting_v1::Method::ReserveSubmitOrdered};
    impl_->require_owner();impl_->require_idle_rpc();
    impl_->invalidate_next_event();impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    if (!ordered_admission_remote())
        throw std::logic_error("ordered admission without remote capability");
    (void)ordered_admission_v1::request_size(request);
    for (std::uint32_t i=0;i<request.count;++i)
        if (request.records[i].read.stack_id!=stack_id_)
            throw std::invalid_argument("ordered admission read targets another stack");
    auto response=impl_->exchange_typed(typed_rpc::Op::ReserveSubmitOrdered,
        [&](typed_rpc::Writer& writer) { ordered_admission_v1::encode_request(writer,request); });
    const auto result=ordered_admission_v1::decode_reply(response,request);
    ++impl_->ordered_commands;
    for (std::uint32_t i=0;i<result.completed;++i) {
        const auto& item=result.items[i];
        if (item.reserved) ++impl_->ordered_reserved;else ++impl_->ordered_denied;
        if (item.accepted) ++impl_->ordered_accepted;
        if (item.reserved && !item.accepted) ++impl_->ordered_released;
    }
    if (result.stop==ordered_admission_v1::Stop::SemanticError) {
        // Reader is complete: explicit poison is required, no replay or invented current outcome.
        ++impl_->ordered_terminal;
        std::cerr << "ORDERED_ADMISSION_TERMINAL stack=" << stack_id_
            << " sequence=" << response.sequence() << " horizon=" << request.horizon
            << " module=" << request.module << " offered=" << request.count
            << " completed=" << result.completed << " failing_index=" << result.failing_index
            << " code=" << result.error_code << " current_outcome=UNKNOWN diagnostic_bytes=";
        std::cerr.write(result.diagnostic.data(),result.diagnostic_size);
        std::cerr << '\n';
        impl_->poisoned=true;impl_->invalidate_next_event();impl_->invalidate_capacity();
    }
    return result;
}

bool StackWorkerClient::capacity_proves_full(std::uint64_t token,
    std::uint64_t horizon,const std::vector<std::uint32_t>& counts)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::CapacityProvesFull};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    // This is NOT reserve: invalid/unknown conditions return to its unchanged
    // authoritative validation/error path. It never fabricates admission.
    if (!impl_->capacity_active || !impl_->capacity || impl_->poisoned ||
        impl_->owner_thread!=std::this_thread::get_id() || !token ||
        counts.size()!=capacity_modules || impl_->capacity->horizon!=horizon)
        return false;
    bool full=false;
    for (std::size_t m=0;m<capacity_modules;++m) {
        // Withholding only when the requested module is exactly full.
        if (counts[m] && impl_->capacity->outstanding[m]==impl_->capacity_limits[m])
            full=true;
    }
    if (!full) return false;
    Impl::ErrorGuard guard{*impl_};
    if (!transport_alive())
        throw std::runtime_error("capacity-full worker transport died");
    ++impl_->capacity_skipped;
    return true;
}

bool StackWorkerClient::capacity_proves_inactive(std::uint32_t module_id,
    std::uint64_t horizon)
{
    impl_->require_startup_ready();
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    if (!impl_->capacity_zero_inactive_requested)
        return false;
    Impl::ErrorGuard guard{*impl_};
    if (impl_->poisoned)
        throw std::runtime_error("poisoned worker transport");
    ++impl_->capacity_zero_queries;
    if (impl_->owner_thread!=std::this_thread::get_id() ||
        !impl_->capacity_active || !impl_->capacity ||
        impl_->capacity->horizon!=horizon || module_id>=capacity_modules ||
        impl_->capacity->outstanding[module_id]!=0) {
        ++impl_->capacity_zero_fallbacks;
        return false;
    }
    // The snapshot is installed only after the complete existing Advance
    // reply and all Ready pages validate. Zero is module outstanding, not
    // free credit or a proof that shared upstream link tails are finished.
    if (!transport_alive())
        throw std::runtime_error("capacity-zero worker transport died");
    ++impl_->capacity_zero_hits;
    return true;
}

void StackWorkerClient::release_reservation(std::uint64_t token)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Release};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
  Impl::ErrorGuard guard{*impl_};
  (void)impl_->exchange({{"type","RELEASE"},{"token",token}}); }

bool StackWorkerClient::try_submit(const DeviceRead& r,
                                  std::optional<PacketBackingProof> proof)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Submit};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    if (r.stack_id!=stack_id_)
        throw std::invalid_argument("child read targets another stack");
    Impl::ErrorGuard guard{*impl_};
    if (impl_->typed_active) {
        auto reply=impl_->exchange_typed(typed_rpc::Op::Submit,
            [&](typed_rpc::Writer& writer) {
                reserve_submit_single_v1::encode_read(writer,r,proof);
            });
        const bool accepted=reply.boolean();
        reply.done();
        return accepted;
    }
    json command={{"type","SUBMIT"},
        {"request_id",r.request_id},{"arrival_ns",r.arrival_ns},
        {"deadline_ns",r.deadline_ns},{"local_address",r.local_address},
        {"axi_id",r.axi_id},{"module_id",r.module_id},
        {"endpoint_id",r.endpoint_id},{"bytes",r.bytes},
        {"operation",r.operation}};
    if (r.expected_media_page)
        command["expected_media_page"]=*r.expected_media_page;
    if (r.expected_generation)
        command["expected_generation"]=*r.expected_generation;
    if (proof) {
        command["original_local_address"]=proof->original_local_address;
        command["original_bytes"]=proof->original_bytes;
        command["expected_canonical_id"]=proof->canonical_id;
        command["packet_generation"]=proof->generation;
    }
    const auto reply=impl_->exchange(std::move(command));
    return reply.at("accepted").get<bool>();
}

void StackWorkerClient::cancel(std::uint64_t id,std::uint64_t horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Cancel};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
  Impl::ErrorGuard guard{*impl_};
  (void)impl_->exchange({{"type","CANCEL"},{"request_id",id},
                        {"horizon",horizon}}); }

std::vector<WorkerReady> StackWorkerClient::advance_until(std::uint64_t horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Advance};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    if (impl_->typed_active) {
        auto first=impl_->exchange_typed(typed_rpc::Op::Advance,
            [&](typed_rpc::Writer& writer) { writer.u64(horizon); });
        return impl_->decode_ready_response(std::move(first),horizon,false);
    }
    auto reply=impl_->exchange({{"type","ADVANCE"},{"horizon",horizon}});
    std::vector<WorkerReady> out;
    const auto total=reply.at("ready_total").get<std::size_t>();
    while (true) {
        for (const auto& value:reply.at("ready")) {
            out.push_back({value.at("request_id").get<std::uint64_t>(),
                value.at("module_id").get<std::uint32_t>(),
                static_cast<DeviceResult>(value.at("result").get<int>()),
                value.at("ar_delivered_ns").get<std::uint64_t>(),
                value.at("media_ready_ns").get<std::uint64_t>(),
                value.at("response_delivered_ns").get<std::uint64_t>()});
        }
        if (out.size()==total) break;
        if (out.size()>total || reply.at("ready").empty())
            throw std::runtime_error("worker ready pagination did not progress");
        reply=impl_->exchange({{"type","READY_PAGE"},
            {"horizon",horizon},{"offset",out.size()}});
        if (reply.at("ready_total").get<std::size_t>()!=total)
            throw std::runtime_error("worker ready snapshot size changed");
    }
    return out;
}

std::vector<WorkerReady> StackWorkerClient::close_and_advance(
    std::uint64_t from_horizon,std::uint64_t to_horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::CloseAdvance};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    if (impl_->typed_active) {
        auto first=impl_->exchange_typed(typed_rpc::Op::CloseAdvance,
            [&](typed_rpc::Writer& writer) {
                writer.u64(from_horizon);
                writer.u64(to_horizon);
            });
        return impl_->decode_ready_response(std::move(first),to_horizon,false);
    }
    auto reply=impl_->exchange({{"type","CLOSE_ADVANCE"},
        {"from_horizon",from_horizon},{"to_horizon",to_horizon}});
    std::vector<WorkerReady> out;
    const auto total=reply.at("ready_total").get<std::size_t>();
    while (true) {
        for (const auto& value:reply.at("ready")) {
            out.push_back({value.at("request_id").get<std::uint64_t>(),
                value.at("module_id").get<std::uint32_t>(),
                static_cast<DeviceResult>(value.at("result").get<int>()),
                value.at("ar_delivered_ns").get<std::uint64_t>(),
                value.at("media_ready_ns").get<std::uint64_t>(),
                value.at("response_delivered_ns").get<std::uint64_t>()});
        }
        if (out.size()==total) break;
        if (out.size()>total || reply.at("ready").empty())
            throw std::runtime_error("worker ready pagination did not progress");
        reply=impl_->exchange({{"type","READY_PAGE"},
            {"horizon",to_horizon},{"offset",out.size()}});
        if (reply.at("ready_total").get<std::size_t>()!=total)
            throw std::runtime_error("worker ready snapshot size changed");
    }
    return out;
}

bool StackWorkerClient::close_gather_local() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    if (impl_->poisoned || impl_->gather_pending)
        throw std::logic_error("invalid CloseAdvance gather preflight");
    return static_cast<bool>(impl_->local);
}

bool StackWorkerClient::close_gather_remote() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    if (impl_->poisoned || impl_->gather_pending)
        throw std::logic_error("invalid CloseAdvance gather preflight");
    return !impl_->local && impl_->typed_active && impl_->shm_active;
}

void StackWorkerClient::begin_close_gather(std::uint64_t from,std::uint64_t to)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::GatherBegin};
    impl_->require_owner();
    impl_->require_idle_rpc();
    Impl::ErrorGuard guard{*impl_};
    if (!close_gather_remote())
        throw std::logic_error("CloseAdvance gather requires remote typed SHM");
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
    const auto seq=++impl_->sequence;
    typed_rpc::Writer command(typed_rpc::Op::CloseAdvance,seq,impl_->stack);
    command.u64(from); command.u64(to);
    // Start the existing 30-second command budget at dispatch, never at join.
    impl_->gather_pending=Impl::GatherPending{typed_rpc::Op::CloseAdvance,seq,to,
        std::chrono::steady_clock::now()+std::chrono::seconds(30)};
    impl_->send(std::move(command).finish(),impl_->gather_remaining());
}

std::vector<WorkerReady> StackWorkerClient::finish_close_gather()
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::GatherFinish};
    impl_->require_owner();
    Impl::ErrorGuard guard{*impl_};
    if (impl_->poisoned) throw std::runtime_error("poisoned worker transport");
    impl_->require_pending_kind(typed_rpc::Op::CloseAdvance);
    const auto timeout=impl_->gather_remaining();
    const auto seq=impl_->gather_pending->sequence;
    const auto horizon=impl_->gather_pending->to_horizon;
    // Readers are function-local. All destroy before the frontend aborts clients.
    auto first=impl_->decode_typed_response(impl_->receive(timeout),
        typed_rpc::Op::CloseAdvance,seq);
    auto out=impl_->decode_ready_response(std::move(first),horizon,true);
    impl_->gather_pending.reset();
    return out;
}

void StackWorkerClient::abandon_close_gather() noexcept
{
    impl_->abandon_gather();
}

bool StackWorkerClient::advance_gather_local() const
{
    impl_->require_startup_ready();
    return close_gather_local(); // identical transport/owner/poison preflight
}

bool StackWorkerClient::advance_gather_remote() const
{
    impl_->require_startup_ready();
    return close_gather_remote();
}

void StackWorkerClient::begin_advance_gather(std::uint64_t horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::GatherBegin};
    impl_->require_owner();
    impl_->require_idle_rpc();
    Impl::ErrorGuard guard{*impl_};
    if (!advance_gather_remote())
        throw std::logic_error("Advance gather requires remote typed SHM");
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
    const auto seq=++impl_->sequence;
    typed_rpc::Writer command(typed_rpc::Op::Advance,seq,impl_->stack);
    command.u64(horizon);
    impl_->gather_pending=Impl::GatherPending{typed_rpc::Op::Advance,seq,horizon,
        std::chrono::steady_clock::now()+std::chrono::seconds(30)};
    impl_->send(std::move(command).finish(),impl_->gather_remaining());
}

std::vector<WorkerReady> StackWorkerClient::finish_advance_gather()
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::GatherFinish};
    impl_->require_owner();
    Impl::ErrorGuard guard{*impl_};
    if (impl_->poisoned) throw std::runtime_error("poisoned worker transport");
    impl_->require_pending_kind(typed_rpc::Op::Advance);
    const auto timeout=impl_->gather_remaining();
    const auto seq=impl_->gather_pending->sequence;
    const auto horizon=impl_->gather_pending->to_horizon;
    auto first=impl_->decode_typed_response(impl_->receive(timeout),
        typed_rpc::Op::Advance,seq);
    auto out=impl_->decode_ready_response(std::move(first),horizon,true);
    impl_->gather_pending.reset();
    return out;
}

void StackWorkerClient::abandon_advance_gather() noexcept
{
    impl_->abandon_gather(); // cross-kind pending is never left for STOP
}

DeviceMediaPort::EventPeek StackWorkerClient::next_event(std::uint64_t horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Peek};
    impl_->require_idle_rpc();
    impl_->invalidate_capacity(); // preserve original public-query contract
    Impl::ErrorGuard guard{*impl_};
    auto saved=impl_->next_event_saved.take(horizon,
        impl_->next_event_active && !impl_->poisoned &&
        impl_->owner_thread==std::this_thread::get_id());
    if (saved && saved->peek.supported && transport_alive()) {
        ++impl_->next_event_hits;
        return saved->peek;
    }
    // Unknown/unsupported/mismatched/dead peers still use the original query
    // and its authoritative validation/poisoning path. Saved value is consumed.
    if (impl_->typed_active) {
        auto reply=impl_->exchange_typed(typed_rpc::Op::PeekEvent,
            [&](typed_rpc::Writer& writer) { writer.u64(horizon); });
        DeviceMediaPort::EventPeek result;
        result.supported=reply.boolean();
        const bool has_next=reply.boolean();
        if (!result.supported && has_next)
            throw std::runtime_error("unsupported typed peek has event");
        if (has_next) result.next_ns=reply.u64();
        reply.done();
        return result;
    }
    const auto reply=impl_->exchange({{"type","PEEK_EVENT"},{"horizon",horizon}});
    DeviceMediaPort::EventPeek result;
    result.supported=reply.at("supported").get<bool>();
    if (result.supported && !reply.at("next_event_ns").is_null())
        result.next_ns=reply.at("next_event_ns").get<std::uint64_t>();
    return result;
}

bool StackWorkerClient::compact_consume_remote() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    return impl_->compact_active && !impl_->local && impl_->typed_active && impl_->shm_active;
}
compact_consume_v1::Reply StackWorkerClient::consume_compact_ordered(const compact_consume_v1::Request& request)
{
    impl_->require_startup_ready();
    impl_->require_owner();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,false,parallel_component_accounting_v1::Method::Consume};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    if(!compact_consume_remote()) throw std::logic_error("unnegotiated compact Consume client");
    compact_consume_v1::validate_request(request);
    ++impl_->compact_commands; impl_->compact_offered+=request.count; ++impl_->compact_histogram[request.count];
    auto wire=impl_->exchange_typed(typed_rpc::Op::ConsumeCompactOrdered,[&](typed_rpc::Writer& w) {
        compact_consume_v1::encode_request(w,request);
    });
    const auto result=compact_consume_v1::decode_reply(wire,request);
    impl_->compact_confirmed+=result.confirmed;
    for(std::size_t i=0;i<4;++i) impl_->compact_results[i]+=result.results[i];
    if(parallel_component_accounting_v1::running()) {
        if(stack_id_>=4) parallel_component_accounting_v1::state.valid.store(false);
        else for(std::size_t i=0;i<4;++i) parallel_component_accounting_v1::envelopes.consume_results[stack_id_][i]+=result.results[i];
    }
    if(result.stop==compact_consume_v1::Stop::SemanticError) {
        ++impl_->compact_terminal; impl_->poisoned=true; // Valid prefix returned once; never replay current/suffix.
    }
    return result;
}

bool StackWorkerClient::compact_consume_begin_available() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    return !impl_->poisoned && !impl_->gather_pending &&
           !impl_->compact_pending_request && compact_consume_remote();
}

bool StackWorkerClient::compact_consume_pending() const
{
    impl_->require_startup_ready();
    impl_->require_owner();
    return impl_->gather_pending &&
           impl_->gather_pending->kind==typed_rpc::Op::ConsumeCompactOrdered;
}

void StackWorkerClient::begin_compact_consume(const compact_consume_v1::Request& request)
{
    impl_->require_startup_ready();
    impl_->require_owner(); // wrong owner cannot mutate accounting, sequence or pending
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,false,parallel_component_accounting_v1::Method::Consume};
    impl_->require_idle_rpc();
    Impl::ErrorGuard guard{*impl_};
    if(!compact_consume_begin_available())
        throw std::logic_error("compact begin requires idle negotiated remote client");
    compact_consume_v1::validate_request(request);
    if(request.count<2) throw std::logic_error("compact wave requires original non-single group");
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
    const auto seq=++impl_->sequence;
    typed_rpc::Writer command(typed_rpc::Op::ConsumeCompactOrdered,seq,impl_->stack);
    compact_consume_v1::encode_request(command,request);
    // Lifetime begins at dispatch; finish never grants a fresh command budget.
    impl_->compact_pending_request=request;
    impl_->gather_pending=Impl::GatherPending{typed_rpc::Op::ConsumeCompactOrdered,
        seq,request.horizon,std::chrono::steady_clock::now()+std::chrono::seconds(30)};
    ++impl_->compact_commands; impl_->compact_offered+=request.count;
    ++impl_->compact_histogram[request.count];
    impl_->send(std::move(command).finish(),impl_->gather_remaining());
}

compact_consume_v1::Reply StackWorkerClient::finish_compact_consume()
{
    impl_->require_startup_ready();
    impl_->require_owner();
    Impl::ErrorGuard guard{*impl_};
    if(impl_->poisoned) throw std::runtime_error("poisoned worker transport");
    impl_->require_pending_kind(typed_rpc::Op::ConsumeCompactOrdered);
    if(!impl_->compact_pending_request ||
       impl_->compact_pending_request->horizon!=impl_->gather_pending->to_horizon)
        throw std::logic_error("compact copied pending request/horizon mismatch");
    const auto request=*impl_->compact_pending_request;
    const auto seq=impl_->gather_pending->sequence;
    auto wire=impl_->decode_typed_response(impl_->receive(impl_->gather_remaining()),
        typed_rpc::Op::ConsumeCompactOrdered,seq);
    const auto result=compact_consume_v1::decode_reply(wire,request);
    (void)impl_->gather_remaining(); // original dispatch deadline, before any acceptance
    // decode_reply validates the entire frame before counters or host commit.
    impl_->gather_pending.reset(); impl_->compact_pending_request.reset();
    impl_->compact_confirmed+=result.confirmed;
    for(std::size_t i=0;i<4;++i) impl_->compact_results[i]+=result.results[i];
    if(parallel_component_accounting_v1::running()) {
        if(stack_id_>=4) parallel_component_accounting_v1::state.valid.store(false);
        else for(std::size_t i=0;i<4;++i) parallel_component_accounting_v1::envelopes.consume_results[stack_id_][i]+=result.results[i];
    }
    if(result.stop==compact_consume_v1::Stop::SemanticError) {
        ++impl_->compact_terminal; impl_->poisoned=true;
    }
    return result;
}

void StackWorkerClient::abandon_compact_consume() noexcept
{
    // Private frontend calls on the same original owner. Unknown ACKs are never replayed.
    impl_->abandon_gather();
}

DeviceCompletion StackWorkerClient::consume_completion(std::uint64_t id,
                                                        std::uint64_t horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Consume};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    impl_->invalidate_capacity();
    Impl::ErrorGuard guard{*impl_};
    if (impl_->typed_active) {
        auto reply=impl_->exchange_typed(typed_rpc::Op::Consume,
            [&](typed_rpc::Writer& writer) {
                writer.u64(id);
                writer.u64(horizon);
            });
        DeviceCompletion out;
        out.request.request_id=id;
        out.request.stack_id=stack_id_;
        out.request.module_id=reply.u32();
        out.result=typed_rpc::device_result(reply.u32());
        out.ar_delivered_ns=reply.u64();
        out.media_ready_ns=reply.u64();
        out.response_delivered_ns=reply.u64();
        out.consumed_ns=reply.u64();
        out.axi_payload_bytes=reply.u32();
        reply.done();
        if(parallel_component_accounting_v1::running()) {
        const auto result=static_cast<unsigned>(out.result);
        if(stack_id_>=4||result>=4)parallel_component_accounting_v1::state.valid.store(false);
        else ++parallel_component_accounting_v1::envelopes.consume_results[stack_id_][result];
    }
    return out;
    }
    const auto value=impl_->exchange({{"type","CONSUME"},
        {"request_id",id},{"horizon",horizon}});
    DeviceCompletion out;
    out.request.request_id=id;
    out.request.stack_id=stack_id_;
    out.request.module_id=value.at("module_id").get<std::uint32_t>();
    out.result=static_cast<DeviceResult>(value.at("result").get<int>());
    out.ar_delivered_ns=value.at("ar_delivered_ns").get<std::uint64_t>();
    out.media_ready_ns=value.at("media_ready_ns").get<std::uint64_t>();
    out.response_delivered_ns=value.at("response_delivered_ns").get<std::uint64_t>();
    out.consumed_ns=value.at("consumed_ns").get<std::uint64_t>();
    out.axi_payload_bytes=value.at("axi_payload_bytes").get<std::uint32_t>();
    if(parallel_component_accounting_v1::running()) {
        const auto result=static_cast<unsigned>(out.result);
        if(stack_id_>=4||result>=4)parallel_component_accounting_v1::state.valid.store(false);
        else ++parallel_component_accounting_v1::envelopes.consume_results[stack_id_][result];
    }
    return out;
}

bool StackWorkerClient::is_active(std::uint64_t id)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::IsActive};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    Impl::ErrorGuard guard{*impl_};
    if (impl_->typed_active) {
        auto reply=impl_->exchange_typed(typed_rpc::Op::IsActive,
            [&](typed_rpc::Writer& writer) { writer.u64(id); });
        const bool active=reply.boolean();
        reply.done();
        return component_scope.boolean_result(active);
    }
    const auto value=impl_->exchange({{"type","IS_ACTIVE"},{"request_id",id}});
    return component_scope.boolean_result(value.at("active").get<bool>());
}

void StackWorkerClient::close_horizon(std::uint64_t horizon)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Close};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event(); impl_->invalidate_capacity();
  Impl::ErrorGuard guard{*impl_};
  (void)impl_->exchange({{"type","CLOSE"},{"horizon",horizon}}); }

WorkerStats StackWorkerClient::stats()
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::Stats};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    Impl::ErrorGuard guard{*impl_};
    const auto value=impl_->exchange({{"type","STATS"}});
    WorkerStats result{value.at("process_id").get<std::uint64_t>(),
        value.at("accepted").get<std::uint64_t>(),
        value.at("succeeded").get<std::uint64_t>(),
        value.at("cancelled").get<std::uint64_t>(),
        value.at("failed").get<std::uint64_t>(),
        value.at("native_commands").get<std::uint64_t>(),
        value.at("media_submit_bytes").get<std::uint64_t>(),
        value.at("max_rss_kib").get<std::uint64_t>(),
        value.at("physical_outstanding").get<std::uint64_t>(),
        value.at("current_time_ns").get<std::uint64_t>()};
    result.advance_commands=value.at("advance_commands").get<std::uint64_t>();
    result.peek_commands=value.at("peek_commands").get<std::uint64_t>();
    result.ipc_commands=value.at("ipc_commands").get<std::uint64_t>();
    result.close_commands=value.at("close_commands").get<std::uint64_t>();
    result.close_advance_commands=value.at("close_advance_commands").get<std::uint64_t>();
    result.reserve_commands=value.at("reserve_commands").get<std::uint64_t>();
    result.submit_commands=value.at("submit_commands").get<std::uint64_t>();
    result.consume_commands=value.at("consume_commands").get<std::uint64_t>();
    result.is_active_commands=value.at("is_active_commands").get<std::uint64_t>();
    result.transport=value.at("transport").get<std::string>();
    if (result.transport!="local" && result.transport!="process")
        throw std::runtime_error("unknown worker transport");
    result.physical_ipc_send_count=
        value.at("physical_ipc_send_count").get<std::uint64_t>();
    result.physical_ipc_receive_count=
        value.at("physical_ipc_receive_count").get<std::uint64_t>();
    for (const auto& link:value.at("module_links"))
        result.module_links.push_back({
            link.at("module_id").get<std::uint32_t>(),
            link.at("ar_wire_bytes").get<std::uint64_t>(),
            link.at("r_wire_bytes").get<std::uint64_t>(),
            link.at("ar_credit_granules").get<std::uint32_t>(),
            link.at("r_credit_granules").get<std::uint32_t>()});
    return result;
}

std::vector<NativeGroupRecord> StackWorkerClient::native_evidence(
    std::size_t offset,std::size_t count)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::NativeEvidence};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    if (count==0 || count>16)
        throw std::invalid_argument("native evidence page size must be 1..16");
    Impl::ErrorGuard guard{*impl_};
    const auto value=impl_->exchange({{"type","EVIDENCE"},
        {"kind","native"},{"offset",offset},{"count",count}});
    std::vector<NativeGroupRecord> out;
    for (const auto& item:value.at("records")) {
        NativeGroupRecord r;
        r.stack_id=item.at("stack_id").get<std::uint32_t>();
        r.module_id=item.at("module_id").get<std::uint32_t>();
        r.group_token=item.at("group_token").get<std::uint64_t>();
        r.bank.host_channel=item.at("host_channel").get<std::uint32_t>();
        r.bank.core_die=item.at("core_die").get<std::uint32_t>();
        r.bank.bank=item.at("hbf_bank").get<std::uint32_t>();
        r.bank.native_channel=item.at("native_channel").get<std::uint32_t>();
        r.bank.native_chip=item.at("native_chip").get<std::uint32_t>();
        r.bank.native_die=item.at("native_die").get<std::uint32_t>();
        r.bank.native_plane=item.at("native_plane").get<std::uint32_t>();
        r.proof.expected_logical_page=item.at("expected_lpa").get<std::uint64_t>();
        r.proof.observed_logical_page=item.at("observed_lpa").get<std::uint64_t>();
        r.proof.observed_media_bytes=item.at("media_bytes").get<std::uint64_t>();
        r.proof.issued_commands=item.at("commands").get<std::uint64_t>();
        r.proof.phase_events=item.at("phase_events").get<
            std::array<std::uint64_t,5>>();
        r.proof.first_issued_ns=item.at("first_issued_ns").get<std::uint64_t>();
        r.proof.last_issued_ns=item.at("last_issued_ns").get<std::uint64_t>();
        r.proof.first_media_begin_ns=item.at("media_begin_ns").get<std::uint64_t>();
        r.proof.last_media_end_ns=item.at("media_end_ns").get<std::uint64_t>();
        r.proof.logical_page_match=item.at("lpa_match").get<bool>();
        r.proof.bank_match=item.at("bank_match").get<bool>();
        r.proof.observer_failed=item.at("observer_failed").get<bool>();
        out.push_back(std::move(r));
    }
    return out;
}

std::vector<DeviceRequestRecord> StackWorkerClient::request_evidence(
    std::size_t offset,std::size_t count)
{
    impl_->require_startup_ready();
    parallel_component_accounting_v1::RpcScope component_scope{stack_id_,impl_ && bool(impl_->local),parallel_component_accounting_v1::Method::RequestEvidence};
    impl_->require_idle_rpc();
    impl_->invalidate_next_event();
    if (count==0 || count>16)
        throw std::invalid_argument("request evidence page size must be 1..16");
    Impl::ErrorGuard guard{*impl_};
    const auto value=impl_->exchange({{"type","EVIDENCE"},
        {"kind","request"},{"offset",offset},{"count",count}});
    std::vector<DeviceRequestRecord> out;
    for (const auto& item:value.at("records")) {
        DeviceRequestRecord r;
        r.request_id=item.at("request_id").get<std::uint64_t>();
        r.transport_id=item.at("transport_id").get<std::uint64_t>();
        r.group_token=item.at("group_token").get<std::uint64_t>();
        r.canonical_id=item.at("canonical_id").get<std::uint64_t>();
        r.generation=item.at("generation").get<std::uint64_t>();
        r.stack_id=item.at("stack_id").get<std::uint32_t>();
        r.module_id=item.at("module_id").get<std::uint32_t>();
        r.endpoint_id=item.at("endpoint_id").get<std::uint64_t>();
        r.canonical_page=item.at("canonical_page").get<std::uint64_t>();
        r.media_lpa=item.at("media_lpa").get<std::uint64_t>();
        r.accepted_ns=item.at("accepted_ns").get<std::uint64_t>();
        r.ar_delivered_ns=item.at("ar_delivered_ns").get<std::uint64_t>();
        r.media_ready_ns=item.at("media_ready_ns").get<std::uint64_t>();
        r.r_delivered_ns=item.at("r_delivered_ns").get<std::uint64_t>();
        r.consumed_ns=item.at("consumed_ns").get<std::uint64_t>();
        r.result=static_cast<DeviceResult>(item.at("result").get<int>());
        out.push_back(std::move(r));
    }
    return out;
}
} // namespace hbfsim::ucie
