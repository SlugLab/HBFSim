#pragma once

#include <cstddef>
#include <cstdint>

#if defined(__CUDACC__)
#define HBFSIM_FUTURE_HD __host__ __device__
#else
#define HBFSIM_FUTURE_HD
#endif

namespace hbfsim::timing_future {

// C5 infrastructure cannot authorize an executable future transformation.
inline constexpr bool kUnitComplete = false;
inline constexpr std::uint32_t kAbiVersion = 1;
inline constexpr std::uint32_t kSharedControlAbi = 4;
inline constexpr std::uint64_t kFastScalarTiming = 1;
inline constexpr std::uint32_t kMaximumThreadFutures = 16;
inline constexpr std::uint32_t kMaximumBlockThreads = 1024;
inline constexpr std::uint32_t kPending=0, kReady=1, kTimeout=5,
    kUnsupported=6, kDaemonLost=7;
inline constexpr const char* kMode = "timing_load_future_v1";

enum class State : std::uint32_t {
    Unissued=0, Native=1, Issued=2, ModelReady=3, Consumed=4, TerminalError=5
};
enum class WaitKind : std::uint32_t { Dependency=0, Ordering=1 };
inline constexpr std::uint32_t kBecameReady=1, kBecameError=2,
    kConsumed=4, kDrained=8;

struct alignas(16) DeviceTimingFutureV1 {
    std::uint64_t control_alias{0}, control_generation{0}, issue_ns{0},
        ready_ns{0}, deadline_ns{0}, original_address{0}, reservation_id{0};
    State state{State::Unissued};
    std::uint32_t status{kPending};
};
struct alignas(8) TimingFutureLaneMetadataV1 {
    std::uint32_t abi_version{kAbiVersion}, struct_bytes{32},
        instruction_id{0}, bytes{0}, group_mask{0}, group_leader{32};
    std::uint64_t reservation_id{0};
};
struct alignas(8) Capabilities {
    std::uint32_t abi_version{kAbiVersion}, struct_bytes{16};
    std::uint64_t bits{0};
};
struct alignas(8) ModuleRequirements {
    std::uint32_t abi_version{kAbiVersion}, struct_bytes{48},
        token_bytes{64}, metadata_version{kAbiVersion}, metadata_bytes{32},
        shared_control_abi{kSharedControlAbi},
        maximum_thread_futures{kMaximumThreadFutures},
        maximum_block_threads{kMaximumBlockThreads};
    std::uint64_t required_capabilities{kFastScalarTiming}, reserved{0};
};
struct alignas(8) ModuleConfig {
    std::uint32_t abi_version{kAbiVersion}, struct_bytes{64}, enabled{0}, reserved{0};
    std::uint64_t control_alias{0}, control_generation{0}, trace_address{0}, trace_capacity{0};
    std::uint32_t maximum_thread_futures{kMaximumThreadFutures},
        maximum_block_threads{kMaximumBlockThreads};
    std::uint64_t reserved2{0};
};
struct Counters {
    std::uint64_t next_reservation{1}, issued{0}, pending{0}, model_ready{0},
        consumed{0}, drained{0}, terminal_error{0}, native_loads{0}, native_bytes{0},
        rejected{0}, groups_issued{0}, groups_completed{0}, trace_count{0}, trace_overflow{0};
};
struct Trace {
    std::uint64_t reservation_id, address, issue_ns, ready_ns, finish_ns;
    std::uint32_t instruction_id, bytes, lane, group_mask, event, status;
};
struct ConsumeResult {
    std::uint64_t native_bits{0};
    std::uint32_t status{kUnsupported};
    State state{State::TerminalError};
};

static_assert(sizeof(DeviceTimingFutureV1)==64 && alignof(DeviceTimingFutureV1)==16);
static_assert(offsetof(DeviceTimingFutureV1,control_alias)==0);
static_assert(offsetof(DeviceTimingFutureV1,control_generation)==8);
static_assert(offsetof(DeviceTimingFutureV1,issue_ns)==16);
static_assert(offsetof(DeviceTimingFutureV1,ready_ns)==24);
static_assert(offsetof(DeviceTimingFutureV1,deadline_ns)==32);
static_assert(offsetof(DeviceTimingFutureV1,original_address)==40);
static_assert(offsetof(DeviceTimingFutureV1,reservation_id)==48);
static_assert(offsetof(DeviceTimingFutureV1,state)==56);
static_assert(offsetof(DeviceTimingFutureV1,status)==60);
static_assert(sizeof(TimingFutureLaneMetadataV1)==32 && alignof(TimingFutureLaneMetadataV1)==8);
static_assert(offsetof(TimingFutureLaneMetadataV1,abi_version)==0);
static_assert(offsetof(TimingFutureLaneMetadataV1,struct_bytes)==4);
static_assert(offsetof(TimingFutureLaneMetadataV1,instruction_id)==8);
static_assert(offsetof(TimingFutureLaneMetadataV1,bytes)==12);
static_assert(offsetof(TimingFutureLaneMetadataV1,group_mask)==16);
static_assert(offsetof(TimingFutureLaneMetadataV1,group_leader)==20);
static_assert(offsetof(TimingFutureLaneMetadataV1,reservation_id)==24);
static_assert(sizeof(Capabilities)==16 && sizeof(ModuleRequirements)==48);
static_assert(sizeof(ModuleConfig)==64 && sizeof(ConsumeResult)==16 && sizeof(Trace)==64);

HBFSIM_FUTURE_HD constexpr bool valid_capabilities(const Capabilities& caps)
{
    return caps.abi_version==kAbiVersion && caps.struct_bytes==sizeof(caps) &&
           (caps.bits & ~kFastScalarTiming)==0;
}

// Called by context creation from the already validated concrete profile.
HBFSIM_FUTURE_HD constexpr Capabilities derive_capabilities(
    std::uint32_t mode, std::uint32_t empirical_flags, std::uint32_t time_scale,
    std::uint64_t read_ns, std::uint64_t program_ns,
    std::uint64_t bandwidth, std::uint64_t timeout_ns)
{
    return {.bits=(mode==1 && empirical_flags==0 && time_scale==1 &&
                   read_ns && program_ns && bandwidth && timeout_ns)
                      ? kFastScalarTiming : 0};
}

HBFSIM_FUTURE_HD constexpr bool valid_requirements(const ModuleRequirements& r)
{
    return r.abi_version==kAbiVersion && r.struct_bytes==sizeof(r) &&
        r.token_bytes==sizeof(DeviceTimingFutureV1) && r.metadata_version==kAbiVersion &&
        r.metadata_bytes==sizeof(TimingFutureLaneMetadataV1) &&
        r.shared_control_abi==kSharedControlAbi && r.maximum_thread_futures>0 &&
        r.maximum_thread_futures<=kMaximumThreadFutures && r.maximum_block_threads>0 &&
        r.maximum_block_threads<=kMaximumBlockThreads &&
        r.required_capabilities==kFastScalarTiming && r.reserved==0;
}
HBFSIM_FUTURE_HD constexpr bool supports(const Capabilities& caps,
                                         const ModuleRequirements& requirements)
{
    return valid_capabilities(caps) && valid_requirements(requirements) &&
           (caps.bits & requirements.required_capabilities)==requirements.required_capabilities;
}
HBFSIM_FUTURE_HD constexpr bool valid_config(const ModuleConfig& c)
{
    return c.abi_version==kAbiVersion && c.struct_bytes==sizeof(c) && c.enabled==1 &&
        c.reserved==0 && c.reserved2==0 && c.control_alias && c.control_generation &&
        c.trace_address && c.trace_capacity && c.maximum_thread_futures>0 &&
        c.maximum_thread_futures<=kMaximumThreadFutures && c.maximum_block_threads>0 &&
        c.maximum_block_threads<=kMaximumBlockThreads;
}
HBFSIM_FUTURE_HD constexpr bool can_issue(State state)
{
    return state==State::Unissued || state==State::Consumed;
}
HBFSIM_FUTURE_HD constexpr bool valid_trace_span(const ModuleConfig& c)
{
    return valid_config(c) && c.trace_address%alignof(Trace)==0 &&
        c.trace_capacity<=UINT64_MAX/sizeof(Trace) &&
        c.trace_address<=UINT64_MAX-c.trace_capacity*sizeof(Trace);
}
HBFSIM_FUTURE_HD constexpr bool valid_bytes(std::uint32_t bytes)
{
    return bytes==1 || bytes==2 || bytes==4 || bytes==8;
}
HBFSIM_FUTURE_HD constexpr unsigned first_lane(std::uint32_t mask)
{
    unsigned lane=0;
    if (!mask) return 32;
    while ((mask & 1U)==0) { ++lane; mask >>= 1; }
    return lane;
}
HBFSIM_FUTURE_HD constexpr bool valid_metadata(
    const DeviceTimingFutureV1& f, const TimingFutureLaneMetadataV1& m,
    unsigned lane, std::uint32_t instruction, std::uint32_t bytes)
{
    if (m.abi_version!=kAbiVersion || m.struct_bytes!=sizeof(m) ||
        m.reservation_id!=f.reservation_id || m.instruction_id!=instruction ||
        instruction==UINT32_MAX || !valid_bytes(bytes) || m.bytes!=bytes || lane>=32)
        return false;
    if (f.reservation_id==0)
        return f.state==State::Native && m.group_mask==0 && m.group_leader==32;
    if (f.state==State::Native) return false;
    return (m.group_mask & (1U<<lane)) && m.group_leader==first_lane(m.group_mask);
}

struct Transition { State state; std::uint32_t status; std::uint32_t events; };
struct AccountingDelta {
    std::uint32_t model_ready, consumed, drained, terminal_error, terminal,
        groups_completed;
};
// Shared by the CPU conservation oracle and CUDA atomic updates. Only the
// issue-time leader completes its group; no wait-time collective is needed.
HBFSIM_FUTURE_HD constexpr AccountingDelta accounting_delta(
    std::uint32_t events, unsigned lane, unsigned leader)
{
    const auto ready=std::uint32_t{bool(events & kBecameReady)};
    const auto consumed=std::uint32_t{bool(events & kConsumed)};
    const auto drained=std::uint32_t{bool(events & kDrained)};
    const auto error=std::uint32_t{bool(events & kBecameError)};
    return {ready,consumed,drained,error,consumed+drained+error,
        ready && lane<32 && lane==leader ? 1U : 0U};
}
HBFSIM_FUTURE_HD constexpr Transition fail_state(DeviceTimingFutureV1& f,
                                                 std::uint32_t status)
{
    const bool outstanding=f.state==State::Issued || f.state==State::ModelReady;
    f.state=State::TerminalError; f.status=status;
    return {f.state,status,outstanding ? kBecameError : 0};
}
HBFSIM_FUTURE_HD constexpr bool valid_terminal_status(std::uint32_t status)
{
    return status==kTimeout || status==kUnsupported || status==kDaemonLost;
}
HBFSIM_FUTURE_HD constexpr Transition reject_binding(DeviceTimingFutureV1& f)
{
    // Unvalidated metadata/ownership cannot release shared outstanding work.
    f.state=State::TerminalError;f.status=kUnsupported;
    return {f.state,f.status,0};
}
HBFSIM_FUTURE_HD constexpr Transition commit_transition(DeviceTimingFutureV1& original,
    const DeviceTimingFutureV1& candidate, Transition tentative, bool trace_admitted)
{
    if (tentative.events && !trace_admitted) return fail_state(original,kUnsupported);
    original=candidate;
    return tentative;
}
HBFSIM_FUTURE_HD constexpr Transition poll_state(
    DeviceTimingFutureV1& f, const TimingFutureLaneMetadataV1& m,
    unsigned lane, std::uint32_t instruction, std::uint32_t bytes,
    std::uint64_t alias, std::uint64_t generation, std::uint64_t now,
    std::uint32_t liveness_status)
{
    if (f.state==State::TerminalError) {
        if (!valid_terminal_status(f.status)) f.status=kUnsupported;
        return {f.state,f.status,0};
    }
    if (f.state==State::Consumed) return {f.state,kUnsupported,0};
    if (!valid_metadata(f,m,lane,instruction,bytes) || !alias || !generation ||
        f.control_alias!=alias || f.control_generation!=generation ||
        f.issue_ns>now || f.deadline_ns<=f.issue_ns || f.ready_ns<f.issue_ns ||
        (f.state==State::Issued && f.status!=kPending) ||
        ((f.state==State::Native || f.state==State::ModelReady) && f.status!=kReady) ||
        (f.state==State::ModelReady && (now<f.ready_ns || f.ready_ns>f.deadline_ns)) ||
        (f.state!=State::Issued && f.state!=State::ModelReady && f.state!=State::Native))
        return reject_binding(f);
    if (liveness_status!=0) return fail_state(f,valid_terminal_status(liveness_status) ? liveness_status : kUnsupported);
    if (f.state==State::Native) return {f.state,kReady,0};
    // A late observation cannot skip an earlier completion or earlier timeout.
    if (f.ready_ns<=f.deadline_ns && now>=f.ready_ns) {
        const auto event=f.state==State::Issued ? kBecameReady : 0;
        f.state=State::ModelReady; f.status=kReady;
        return {f.state,kReady,event};
    }
    if (now>=f.deadline_ns) return fail_state(f,kTimeout);
    return {f.state,kPending,0};
}
HBFSIM_FUTURE_HD constexpr Transition consume_state(
    DeviceTimingFutureV1& f, const TimingFutureLaneMetadataV1& m,
    unsigned lane, std::uint32_t instruction, std::uint32_t bytes,
    std::uint64_t alias, std::uint64_t generation, std::uint64_t now,
    std::uint32_t liveness_status, WaitKind kind)
{
    if (kind!=WaitKind::Dependency && kind!=WaitKind::Ordering)
        return f.state==State::Consumed || f.state==State::TerminalError
                   ? Transition{f.state,kUnsupported,0} : reject_binding(f);
    auto result=poll_state(f,m,lane,instruction,bytes,alias,generation,now,liveness_status);
    if (result.status!=kReady || (result.state!=State::ModelReady && result.state!=State::Native)) return result;
    f.state=State::Consumed; f.status=kReady;
    if (f.reservation_id) result.events |= kind==WaitKind::Dependency ? kConsumed : kDrained;
    result.state=f.state;
    return result;
}
struct ScalarReservation {
    std::uint64_t ready_ns{0}, transfer_end_ns{0}, deadline_ns{0};
    bool valid{false};
};
HBFSIM_FUTURE_HD constexpr ScalarReservation scalar_reservation(
    std::uint64_t arrival, std::uint64_t previous_tail, std::uint64_t latency,
    std::uint64_t transfer, std::uint64_t timeout)
{
    const auto start=previous_tail>arrival ? previous_tail : arrival;
    if (!latency || !transfer || !timeout || arrival>UINT64_MAX-latency ||
        arrival>UINT64_MAX-timeout || start>UINT64_MAX-transfer) return {};
    const auto end=start+transfer, ready=arrival+latency;
    return {ready>end ? ready : end,end,arrival+timeout,true};
}
// CPU oracle for exactly the equality relation used by the device's match_any.
HBFSIM_FUTURE_HD constexpr std::uint32_t page_group_mask(std::uint32_t active,
    const std::uint32_t* ranges, const std::uint64_t* pages, unsigned lane)
{
    if (lane>=32 || !(active & (1U<<lane))) return 0;
    std::uint32_t group=0;
    for(unsigned i=0;i<32;++i)
        if ((active & (1U<<i)) && ranges[i]==ranges[lane] && pages[i]==pages[lane]) group |= 1U<<i;
    return group;
}

} // namespace hbfsim::timing_future
#undef HBFSIM_FUTURE_HD
