#include <hbfsim/tma_async.hpp>

#include <array>
#include <cstdint>
#include <stdexcept>

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}
}  // namespace

int main()
{
    for (const auto& [mask, targets] :
         std::array<std::pair<std::uint16_t, std::size_t>, 5>{
             {{0x1, 1}, {0x3, 2}, {0xf, 4}, {0xff, 8}, {0xffff, 16}}}) {
        hbfsim::TmaMulticastTracker multicast;
        const auto issue = multicast.issue(7, mask, 0x4000, 3, 128);
        require(issue.targets.size() == targets && issue.source_bytes == 128 &&
                    multicast.source_bytes() == 128,
                "multicast source was not shared once");
        for (std::size_t index = 0; index < issue.targets.size(); ++index) {
            const auto& key = issue.targets[index];
            require(key.cluster_id == 7 && key.barrier == 0x4000 &&
                        key.phase == 3,
                    "multicast target key differs");
            if (index == 0 && issue.targets.size() > 1) {
                require(multicast.fault(key) && !multicast.ready(key),
                        "per-target fault was not isolated");
                continue;
            }
            require(multicast.mark_native_complete(key) &&
                        !multicast.ready(key) &&
                        multicast.mark_shadow_complete(key, 128) &&
                        multicast.ready(key),
                    "per-target conjunctive completion failed");
        }
        const auto successful_targets =
            targets - (targets > 1 ? std::size_t{1} : std::size_t{0});
        require(multicast.materialized_bytes() == successful_targets * 128 &&
                    multicast.source_bytes() == 128,
                "multicast source/materialization byte accounting differs");
    }
    return 0;
}
