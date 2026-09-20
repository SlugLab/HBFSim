#include <hbfsim/hybrid_model.hpp>

#include "../../src/cuda_runtime/device/hbf_device.cuh"

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
    // This function and hbfsim::device::fast_service_ns are one model with two
    // implementations: the host one feeds the offline replay benchmark's fast
    // mode, the device one feeds the runtime's own accounting. A review of an
    // earlier round found the device side had been changed from
    // base + transfer to max(base, transfer) while this side was left adding,
    // so the same profile and the same request produced two different service
    // times depending on which side asked.
    //
    // The assertions below this one only compare profiles against each other,
    // so they hold under either definition and cannot catch that drift. Pin
    // the two implementations to each other instead, across both operations
    // and across sizes on both sides of the crossover where transfer overtakes
    // latency.
    {
        const FastModelProfile* profiles[] = {&conservative, &nominal,
                                              &aggressive};
        const std::uint32_t sizes[] = {1, 512, 4'096, 16'384, 1'048'576,
                                       268'435'456, 4'294'967'295U};
        for (const auto* profile : profiles) {
            for (const auto bytes : sizes) {
                for (const auto operation : {RequestOperation::Read,
                                             RequestOperation::Write}) {
                    const AccessClass access{operation, bytes, 0, 0};
                    const auto base =
                        operation == RequestOperation::Write
                            ? profile->program_latency_ns
                            : profile->read_latency_ns;
                    CHECK(fast_service_ns(*profile, access) ==
                          hbfsim::device::fast_service_ns(
                              base, bytes,
                              profile->aggregate_bandwidth_bytes_per_s));
                }
            }
        }
    }

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
