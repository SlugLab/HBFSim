#include <hbfsim/profile.hpp>

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string_view>

namespace {

void check(bool condition, std::string_view message)
{
    if (!condition) {
        std::cerr << "check failed: " << message << '\n';
        std::abort();
    }
}

template <typename Function>
void check_profile_error(Function&& function, std::string_view expected)
{
    try {
        function();
    } catch (const hbfsim::ProfileError& error) {
        check(error.what() == expected, "unexpected ProfileError message");
        return;
    }
    check(false, "expected ProfileError");
}

}  // namespace

int main()
{
    const auto nominal = hbfsim::load_profile("configs/profiles/nominal.json");
    check(nominal.name == "nominal", "nominal name");
    check(nominal.page_bytes == 16384, "nominal page size");
    check(nominal.read_latency_ns == 10000, "nominal read latency");
    check(nominal.program_latency_ns == 100000, "nominal program latency");
    check(nominal.channels == 32, "nominal channel count");
    check(nominal.aggregate_bandwidth_bytes_per_s == 512000000000ULL,
          "nominal aggregate bandwidth");
    check(hbfsim::blocks_per_plane(nominal) == 256, "derived block count");

    const auto conservative =
        hbfsim::load_profile("configs/profiles/conservative.json");
    const auto aggressive =
        hbfsim::load_profile("configs/profiles/aggressive.json");
    check(conservative.read_latency_ns > nominal.read_latency_ns,
          "conservative read latency ordering");
    check(nominal.read_latency_ns > aggressive.read_latency_ns,
          "aggressive read latency ordering");
    check(conservative.aggregate_bandwidth_bytes_per_s <
              nominal.aggregate_bandwidth_bytes_per_s,
          "conservative bandwidth ordering");
    check(nominal.aggregate_bandwidth_bytes_per_s <
              aggressive.aggregate_bandwidth_bytes_per_s,
          "aggressive bandwidth ordering");

    auto invalid = nominal;
    invalid.page_bytes = 12288;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "page_bytes must be a power of two");

    invalid = nominal;
    invalid.page_bytes = 256;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "page_bytes must be at least 512 for MQSim sector alignment");

    invalid = nominal;
    invalid.capacity_bytes = 0;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "capacity_bytes must be greater than zero");

    invalid = nominal;
    invalid.reference_sample_rate = -0.01;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "reference_sample_rate must be in [0, 1]");

    invalid = nominal;
    invalid.reference_sample_rate = 1.01;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "reference_sample_rate must be in [0, 1]");

    invalid = nominal;
    invalid.hbm_cache_bytes = invalid.capacity_bytes + 1;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "hbm_cache_bytes must not exceed capacity_bytes");

    invalid = nominal;
    invalid.capacity_bytes -= invalid.page_bytes;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "capacity geometry must contain an integral number of blocks per plane");

    return 0;
}
