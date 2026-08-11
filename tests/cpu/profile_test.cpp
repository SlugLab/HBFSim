#include <hbfsim/profile.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
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

std::string tuned_document(bool include_sixth_point = true)
{
    std::string document = R"JSON({
  "name": "cd8p-vmem-p50",
  "capacity_bytes": 1919850381312,
  "page_bytes": 4096,
  "read_latency_ns": 11133,
  "program_latency_ns": 408305,
  "channels": 32,
  "dies_per_channel": 8,
  "planes_per_die": 4,
  "pages_per_block": 256,
  "channel_width_bits": 8,
  "channel_transfer_rate_mtps": 1600,
  "queue_depth": 1,
  "aggregate_bandwidth_bytes_per_s": 103540697,
  "hbm_cache_bytes": 4294967296,
  "reference_sample_rate": 0.01,
  "reference_warmup_requests": 1024,
  "time_scale": 1,
  "timing_tolerance_ns": 27105,
  "empirical_vmem": {
    "source_kind": "nvme-mem2nvm-vmem-sw-cold-fault",
    "source_sha256": "4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f",
    "source_capacity_bytes": 1920383410176,
    "quantile": "p50",
    "sample_count": 11,
    "read_curve": [
      {"pages": 1, "cumulative_ns": 11133, "p95_ns": 38238},
      {"pages": 4, "cumulative_ns": 41495, "p95_ns": 43033},
      {"pages": 16, "cumulative_ns": 168606, "p95_ns": 1247765},
      {"pages": 64, "cumulative_ns": 2824351, "p95_ns": 3860958},
      {"pages": 256, "cumulative_ns": 10767793, "p95_ns": 11968167}
)JSON";
    if (include_sixth_point) {
        document +=
            R"JSON(,      {"pages": 512, "cumulative_ns": 20254374, "p95_ns": 22163673}
)JSON";
    }
    document += R"JSON(    ],
    "program_p50_ns": 408305,
    "program_p95_ns": 596336
  }
}
)JSON";
    return document;
}

std::filesystem::path write_profile(std::string_view document,
                                    std::string_view suffix)
{
    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-profile-" + std::string(suffix) + ".json");
    std::ofstream output(path);
    output << document;
    output.close();
    check(static_cast<bool>(output), "write temporary profile");
    return path;
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
    check(!nominal.empirical_vmem.has_value(), "legacy profile empirical state");

    const auto tuned_path = write_profile(tuned_document(), "valid");
    const auto tuned = hbfsim::load_profile(tuned_path);
    std::filesystem::remove(tuned_path);
    check(tuned.empirical_vmem.has_value(), "empirical profile loaded");
    check(tuned.empirical_vmem->read_curve.size() == 6,
          "six empirical points");
    check(tuned.empirical_vmem->read_curve.front().pages == 1,
          "first empirical page");
    check(tuned.empirical_vmem->read_curve.back().cumulative_ns == 20'254'374,
          "last empirical cumulative latency");
    check(tuned.empirical_vmem->program_p50_ns == 408'305,
          "empirical program latency");

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

    invalid = tuned;
    invalid.page_bytes = 16'384;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem requires page_bytes == 4096");

    invalid = tuned;
    invalid.empirical_vmem->source_sha256 = "invalid";
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem source_sha256 must be lowercase hexadecimal SHA256");

    invalid = tuned;
    invalid.empirical_vmem->read_curve[1].pages = 1;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem pages must be strictly increasing");

    invalid = tuned;
    invalid.empirical_vmem->read_curve[1].cumulative_ns = 11'133;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem P50 latency must be strictly increasing");

    invalid = tuned;
    invalid.empirical_vmem->read_curve[0].p95_ns = 11'132;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem P95 latency must not be below P50");

    invalid = tuned;
    invalid.empirical_vmem->read_curve.back().pages = 1024;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem final page must not exceed 1023");

    invalid = tuned;
    invalid.empirical_vmem->program_p50_ns = 0;
    check_profile_error(
        [&] { hbfsim::validate_profile(invalid); },
        "empirical_vmem program P50 must be greater than zero");

    const auto five_path = write_profile(tuned_document(false), "five-points");
    check_profile_error(
        [&] { (void)hbfsim::load_profile(five_path); },
        "empirical_vmem read_curve must contain exactly 6 points");
    std::filesystem::remove(five_path);

    return 0;
}
