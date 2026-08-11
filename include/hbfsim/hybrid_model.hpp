#pragma once

#include <hbfsim/protocol.hpp>

#include <cstdint>
#include <map>
#include <optional>
#include <set>
#include <vector>

namespace hbfsim {

struct AccessClass {
    RequestOperation operation{RequestOperation::Read};
    std::uint32_t bytes{0};
    std::uint32_t queue_bucket{0};
    std::uint32_t locality_bucket{0};

    auto operator<=>(const AccessClass&) const = default;
};

struct FastModelProfile {
    std::uint64_t read_latency_ns{0};
    std::uint64_t program_latency_ns{0};
    std::uint64_t aggregate_bandwidth_bytes_per_s{0};
};

struct CalibrationEstimate {
    std::uint64_t count{0};
    std::uint64_t mean_ns{0};
    std::uint64_t p50_ns{0};
    std::uint64_t p95_ns{0};
    std::uint64_t p99_ns{0};
    double log_mean{0.0};
    double log_sigma{0.0};
};

[[nodiscard]] std::uint64_t fast_service_ns(
    const FastModelProfile& profile, const AccessClass& request) noexcept;

class HybridSampler {
  public:
    HybridSampler(std::uint32_t warmup_requests, double sample_rate,
                  std::uint64_t seed);
    [[nodiscard]] bool reference(std::uint64_t sequence,
                                 const AccessClass& access);

  private:
    std::uint32_t warmup_requests_;
    std::uint64_t threshold_;
    std::uint64_t seed_;
    std::set<AccessClass> seen_;
};

class Calibrator {
  public:
    void observe(const AccessClass& access, std::uint64_t latency_ns);
    [[nodiscard]] std::optional<CalibrationEstimate> estimate(
        const AccessClass& access) const;

  private:
    std::map<AccessClass, std::vector<std::uint64_t>> samples_;
};

}  // namespace hbfsim
