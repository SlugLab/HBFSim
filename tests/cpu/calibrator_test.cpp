#include <hbfsim/hybrid_model.hpp>

#include <cmath>
#include <cstdint>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

int main()
{
    using namespace hbfsim;

    HybridSampler sampler(4, 0.25, 7);
    const AccessClass read_class{RequestOperation::Read, 16'384, 0, 0};
    const AccessClass write_class{RequestOperation::Write, 16'384, 0, 0};
    for (std::uint64_t sequence = 0; sequence < 4; ++sequence) {
        CHECK(sampler.reference(sequence, read_class));
    }
    CHECK(sampler.reference(4, write_class));
    CHECK(sampler.reference(12345, read_class) ==
          sampler.reference(12345, read_class));

    FastModelProfile conservative{
        .read_latency_ns = 20'000,
        .program_latency_ns = 200'000,
        .aggregate_bandwidth_bytes_per_s = 128'000'000'000ULL,
    };
    FastModelProfile nominal{
        .read_latency_ns = 10'000,
        .program_latency_ns = 100'000,
        .aggregate_bandwidth_bytes_per_s = 512'000'000'000ULL,
    };
    FastModelProfile aggressive{
        .read_latency_ns = 5'000,
        .program_latency_ns = 50'000,
        .aggregate_bandwidth_bytes_per_s = 1'000'000'000'000ULL,
    };
    CHECK(fast_service_ns(conservative, read_class) >
          fast_service_ns(nominal, read_class));
    CHECK(fast_service_ns(nominal, read_class) >
          fast_service_ns(aggressive, read_class));
    CHECK(fast_service_ns(nominal, write_class) >
          fast_service_ns(nominal, read_class));

    Calibrator calibrator;
    calibrator.observe(read_class, 10'000);
    calibrator.observe(read_class, 12'000);
    calibrator.observe(read_class, 14'000);
    const auto estimate = calibrator.estimate(read_class);
    CHECK(estimate.has_value());
    CHECK(estimate->count == 3);
    CHECK(estimate->mean_ns == 12'000);
    CHECK(estimate->p50_ns == 12'000);
    CHECK(estimate->p95_ns == 14'000);
    CHECK(std::isfinite(estimate->log_mean));
    CHECK(std::isfinite(estimate->log_sigma));
    CHECK(!calibrator.estimate(write_class).has_value());
    return 0;
}
