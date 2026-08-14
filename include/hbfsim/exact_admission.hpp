#pragma once

#include <hbfsim/exact_environment.hpp>
#include <hbfsim/exact_profile.hpp>
#include <hbfsim/module_identity.hpp>

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace hbfsim {

struct ExactRunContract {
    std::string cache_condition;
    std::string concurrency_condition;
    ExactClusterShape cluster_shape;
    std::uint64_t cache_condition_epoch{0};
    std::uint64_t latest_relevant_mutation_epoch{0};
    std::string operation_class;
    std::uint64_t issued_operations{0};
    std::uint64_t bytes{0};
    std::uint32_t resident_warps{0};
    std::uint32_t queue_depth{0};
    std::uint32_t dimension_count{0};
    std::uint64_t iterations{0};
    std::uint32_t load_use_distance{0};
    std::uint64_t tile_elements{0};
    std::uint32_t multicast_targets{0};
};

struct ExactAdmissionDecision {
    bool allowed{false};
    std::string profile_id;
    std::string module_id;
    std::string kernel;
    std::vector<std::string> reasons;
};

class ExactAdmissionEvaluator {
  public:
    [[nodiscard]] ExactAdmissionDecision evaluate(
        const ExactProfile& profile, const LoadedModuleEvidence& evidence,
        const ExactLiveEnvironment& live, const ExactRunContract& contract,
        std::string_view kernel) const;
};

}  // namespace hbfsim
