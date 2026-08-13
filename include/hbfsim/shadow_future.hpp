#pragma once

#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>

namespace hbfsim {

enum class FutureState : std::uint32_t {
    Native = 0,
    Issued = 1,
    Ready = 2,
    DeferredMaterialization = 3,
    TerminalError = 4,
    Consumed = 5,
};

enum class FuturePoll : std::uint32_t {
    Native = 0,
    Pending = 1,
    Ready = 2,
    DeferredMaterialization = 3,
    TerminalError = 4,
    Consumed = 5,
};

enum class FutureWaitKind : std::uint32_t {
    Dependency = 0,
    Ordering = 1,
};

enum class FutureScope : std::uint32_t {
    Thread = 0,
    Warp = 1,
    Cta = 2,
    Cluster = 3,
    System = 4,
};

enum ShadowFutureFlags : std::uint32_t {
    FutureNone = 0,
    FutureNative = 1U << 0,
    FutureTiming = 1U << 1,
    FutureCapacity = 1U << 2,
    FutureAtomic = 1U << 3,
};

struct alignas(64) ShadowFuture {
    std::uint64_t ticket{0};
    std::uint64_t original_address{0};
    std::uint64_t resolved_address{0};
    std::uint64_t ready_ns{0};
    std::uint32_t bytes{0};
    std::uint32_t instruction_id{0};
    std::uint32_t channel{0};
    std::uint32_t flags{FutureNone};
    FutureState state{FutureState::Native};
    RequestStatus status{RequestStatus::Pending};
};

struct ShadowFutureRequest {
    std::uint64_t original_address{0};
    std::uint64_t resolved_address{0};
    std::uint64_t issue_ns{0};
    std::uint64_t ready_ns{0};
    std::uint64_t deadline_ns{0};
    std::uint32_t bytes{0};
    std::uint32_t instruction_id{0};
    std::uint32_t channel{0};
    std::uint32_t flags{FutureNone};
    FutureScope scope{FutureScope::Thread};
};

struct ShadowFutureCompletion {
    std::uint64_t resolved_address{0};
    std::uint64_t ready_ns{0};
    RequestStatus status{RequestStatus::Ready};
    bool deferred_materialization{false};
};

struct FutureCounters {
    std::uint64_t issued{0};
    std::uint64_t issue_throttle_ns{0};
    std::uint64_t dependency_wait_ns{0};
    std::uint64_t ordering_wait_ns{0};
    std::uint64_t terminal_completions{0};
    std::uint64_t faults{0};
};

class ShadowFutureMachine {
  public:
    explicit ShadowFutureMachine(std::size_t maximum_outstanding);

    [[nodiscard]] ShadowFuture issue(const ShadowFutureRequest& request);
    [[nodiscard]] FuturePoll poll(ShadowFuture& future);
    [[nodiscard]] bool complete(
        std::uint64_t ticket, const ShadowFutureCompletion& completion);
    [[nodiscard]] ShadowFuture wait(ShadowFuture& future,
                                    FutureWaitKind kind);
    [[nodiscard]] bool consume(ShadowFuture& future);
    [[nodiscard]] std::size_t drain(FutureScope scope, FutureWaitKind kind);

    void advance_to(std::uint64_t time_ns);
    [[nodiscard]] std::uint64_t now_ns() const;
    [[nodiscard]] FutureCounters counters() const;

  private:
    struct Record {
        ShadowFuture future;
        std::uint64_t deadline_ns{0};
        FutureScope scope{FutureScope::Thread};
        bool terminal_counted{false};
    };

    void refresh_record(Record& record);
    void refresh_all();
    void make_terminal(Record& record, FutureState state,
                       RequestStatus status);
    [[nodiscard]] std::size_t active_issued() const;
    [[nodiscard]] std::uint64_t next_progress_time() const;

    const std::size_t maximum_outstanding_;
    mutable std::mutex mutex_;
    std::map<std::uint64_t, Record> records_;
    std::uint64_t next_ticket_{1};
    std::uint64_t now_ns_{0};
    FutureCounters counters_;
};

static_assert(sizeof(ShadowFuture) == 64);

}  // namespace hbfsim
