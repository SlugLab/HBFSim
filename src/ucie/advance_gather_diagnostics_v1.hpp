#pragma once
#include <cstdint>

namespace hbfsim::ucie::advance_gather_diagnostics_v1 {
// Private leader-thread counts only: no clocks, allocation, wire or public ABI.
// TLS avoids shared writes by independent frontend owners on different threads.
// Reset/export are fixture operations outside the measured request/tail gate.
struct Counts {
    std::uint64_t invocations{},active_zero{},active_one{},active_multiple{};
    std::uint64_t active_total{},active_max{},first_snapshots_bypass{};
};
inline thread_local Counts counts;
inline void reset() noexcept { counts={}; }
inline Counts snapshot() noexcept { return counts; }
inline void record(std::uint64_t active) noexcept {
    ++counts.invocations;
    if (!active) ++counts.active_zero;
    else if (active==1) ++counts.active_one;
    else ++counts.active_multiple;
    counts.active_total+=active;
    if (active>counts.active_max) counts.active_max=active;
}
}
