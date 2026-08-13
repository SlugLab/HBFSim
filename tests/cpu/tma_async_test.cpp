#include <hbfsim/tma_async.hpp>

#include <array>
#include <cstddef>
#include <stdexcept>

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}
}  // namespace

int main()
{
    hbfsim::TmaBarrierTracker barriers;
    const hbfsim::TmaBarrierKey first{1, 2, 0x1000, 7};
    const hbfsim::TmaBarrierKey second{1, 3, 0x1000, 7};
    require(barriers.initialize(first, 64) && barriers.initialize(second, 64) &&
                !barriers.initialize(first, 64),
            "barrier keys are not unique");
    require(barriers.mark_native_complete(first) && !barriers.ready(first) &&
                barriers.state(first) ==
                    hbfsim::AsyncCompletionState::ShadowPending,
            "native-before-shadow became ready early");
    require(barriers.mark_shadow_complete(first, 32) &&
                barriers.mark_shadow_complete(first, 32) &&
                barriers.ready(first),
            "conjunctive barrier completion failed");
    require(barriers.mark_shadow_complete(second, 64) &&
                !barriers.ready(second) &&
                barriers.mark_native_complete(second) && barriers.ready(second),
            "shadow-before-native became ready early");
    require(barriers.consume(first) &&
                barriers.state(first) == hbfsim::AsyncCompletionState::Consumed &&
                barriers.invalidate(first),
            "barrier consume/invalidate failed");
    const hbfsim::TmaBarrierKey faulted{2, 0, 0x2000, 1};
    require(barriers.initialize(faulted, 16) && barriers.fault(faulted) &&
                barriers.state(faulted) ==
                    hbfsim::AsyncCompletionState::Faulted &&
                !barriers.mark_native_complete(faulted),
            "terminal barrier fault was not terminal");

    hbfsim::BulkGroupTracker groups(32);
    std::array<std::byte, 16> source{};
    source[0] = std::byte{0x44};
    const auto a = groups.issue_store(source);
    source[0] = std::byte{0x99};
    require(a != 0 && groups.snapshot(a)[0] == std::byte{0x44},
            "store source was not snapshotted");
    const auto b = groups.issue_store(source);
    require(b != 0 && groups.issue_store(source) == 0,
            "bounded CTA staging limit was not enforced");
    require(groups.commit_group() && groups.pending_groups() == 1 &&
                !groups.wait_read(0),
            "unread committed group reported ready");
    require(groups.mark_source_read(a) && groups.mark_source_read(b) &&
                groups.wait_read(0) && !groups.wait_full(0),
            ".read/full wait semantics were conflated");
    require(groups.mark_destination_complete(a) &&
                groups.mark_destination_complete(b) && groups.wait_full(0) &&
                groups.retire_completed(0) && groups.staged_bytes() == 0 &&
                groups.pending_groups() == 0,
            "full group retirement failed");
    require(!groups.commit_group(), "empty group commit was accepted");
    return 0;
}
