#include <hbfsim/hybrid_model.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace hbfsim {
namespace {

std::uint64_t mix(std::uint64_t value) noexcept
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

std::uint64_t percentile(const std::vector<std::uint64_t>& sorted,
                         std::uint32_t numerator,
                         std::uint32_t denominator) noexcept
{
    const auto rank = (static_cast<unsigned __int128>(sorted.size()) *
                       numerator + denominator - 1) /
                      denominator;
    const auto index = rank == 0 ? 0 : static_cast<std::size_t>(rank - 1);
    return sorted[std::min(index, sorted.size() - 1)];
}

}  // namespace

std::uint64_t fast_service_ns(const FastModelProfile& profile,
                              const AccessClass& request) noexcept
{
    if (request.bytes == 0 || profile.aggregate_bandwidth_bytes_per_s == 0) {
        return 0;
    }
    const auto base = request.operation == RequestOperation::Write
                          ? profile.program_latency_ns
                          : profile.read_latency_ns;
    const auto transfer =
        (static_cast<unsigned __int128>(request.bytes) * 1'000'000'000ULL +
         profile.aggregate_bandwidth_bytes_per_s - 1) /
        profile.aggregate_bandwidth_bytes_per_s;
    // The later of the two bounds, not their sum, matching
    // hbfsim::device::fast_service_ns. read_latency_ns is a first-byte latency
    // that overlaps the transfer, so a request is done once both the latency
    // has elapsed and the bytes have moved; the device wait loop enforces
    // max(arrival + latency, channel_tail + transfer). An earlier round
    // changed the device side and left this host mirror adding, so the same
    // profile and request gave two different service times depending on which
    // side asked. calibrator_test.cpp now pins the two together.
    //
    // No overflow guard is needed any more: taking the larger of two values
    // cannot overflow, unlike the sum this replaces.
    //
    // One place in this repository still adds the two, and that is intended:
    // scalar_prediction in scripts/tune_vmem_profile.py. It is the naive
    // parameter-sheet closed-form the calibration table compares measurements
    // against, not a second opinion about what this function should return.
    // Do not reconcile them.
    const auto widened = static_cast<std::uint64_t>(transfer);
    return widened > base ? widened : base;
}

HybridSampler::HybridSampler(std::uint32_t warmup_requests,
                             double sample_rate, std::uint64_t seed)
    : warmup_requests_(warmup_requests), seed_(seed)
{
    if (!std::isfinite(sample_rate) || sample_rate < 0.0 ||
        sample_rate > 1.0) {
        throw std::invalid_argument("sample_rate must be in [0, 1]");
    }
    threshold_ = sample_rate >= 1.0
                     ? std::numeric_limits<std::uint64_t>::max()
                     : static_cast<std::uint64_t>(
                           sample_rate *
                           static_cast<long double>(
                               std::numeric_limits<std::uint64_t>::max()));
}

bool HybridSampler::reference(std::uint64_t sequence,
                              const AccessClass& access)
{
    const auto unseen = seen_.insert(access).second;
    if (sequence < warmup_requests_ || unseen) {
        return true;
    }
    auto value = sequence ^ seed_;
    value ^= static_cast<std::uint64_t>(access.bytes) << 17;
    value ^= static_cast<std::uint64_t>(access.queue_bucket) << 33;
    value ^= static_cast<std::uint64_t>(access.locality_bucket) << 49;
    value ^= static_cast<std::uint64_t>(access.operation) << 61;
    return mix(value) <= threshold_;
}

void Calibrator::observe(const AccessClass& access,
                         std::uint64_t latency_ns)
{
    if (latency_ns == 0) {
        throw std::invalid_argument("latency_ns must be positive");
    }
    samples_[access].push_back(latency_ns);
}

std::optional<CalibrationEstimate> Calibrator::estimate(
    const AccessClass& access) const
{
    const auto found = samples_.find(access);
    if (found == samples_.end() || found->second.empty()) {
        return std::nullopt;
    }
    auto sorted = found->second;
    std::sort(sorted.begin(), sorted.end());
    const auto sum = std::accumulate(
        sorted.begin(), sorted.end(), static_cast<unsigned __int128>(0));
    long double log_sum = 0.0L;
    for (const auto sample : sorted) {
        log_sum += std::log(static_cast<long double>(sample));
    }
    const auto log_mean = log_sum / sorted.size();
    long double squared = 0.0L;
    for (const auto sample : sorted) {
        const auto delta = std::log(static_cast<long double>(sample)) - log_mean;
        squared += delta * delta;
    }
    return CalibrationEstimate{
        .count = sorted.size(),
        .mean_ns = static_cast<std::uint64_t>(sum / sorted.size()),
        .p50_ns = percentile(sorted, 50, 100),
        .p95_ns = percentile(sorted, 95, 100),
        .p99_ns = percentile(sorted, 99, 100),
        .log_mean = static_cast<double>(log_mean),
        .log_sigma = static_cast<double>(std::sqrt(squared / sorted.size())),
    };
}

}  // namespace hbfsim
