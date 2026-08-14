#include <hbfsim/exact_profile.hpp>

#include <json.hpp>

#include <fstream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

void require(bool condition, std::string_view message)
{
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

#define CHECK(expression) require(static_cast<bool>(expression), #expression)

nlohmann::json fixture()
{
    std::ifstream input("tests/fixtures/exact/sm120-valid.json");
    require(input.good(), "unable to open exact profile fixture");
    return nlohmann::json::parse(input);
}

nlohmann::json stage4_fixture()
{
    std::ifstream input("tests/fixtures/exact/sm120-stage4-valid.json");
    require(input.good(), "unable to open Stage 4 profile fixture");
    return nlohmann::json::parse(input);
}

template <class Mutator>
void expect_stage4_error(Mutator mutate, std::string_view expected_reason)
{
    auto document = stage4_fixture();
    mutate(document);
    try {
        (void)hbfsim::parse_exact_profile(document.dump());
    } catch (const hbfsim::ExactProfileError& error) {
        CHECK(error.reason() == expected_reason);
        return;
    }
    throw std::runtime_error("Stage 4 profile unexpectedly parsed");
}

template <class Mutator>
void expect_error(Mutator mutate, std::string_view expected_reason)
{
    auto document = fixture();
    mutate(document);
    try {
        (void)hbfsim::parse_exact_profile(document.dump());
    } catch (const hbfsim::ExactProfileError& error) {
        CHECK(error.reason() == expected_reason);
        return;
    }
    throw std::runtime_error("exact profile unexpectedly parsed");
}

}  // namespace

int main()
{
    const auto profile = hbfsim::load_exact_profile(
        "tests/fixtures/exact/sm120-valid.json");
    CHECK(profile.schema_version == 1);
    CHECK(profile.target.compute_capability_major == 12);
    CHECK(profile.target.compute_capability_minor == 0);
    CHECK(profile.modules.at(0).kernels.at(0).registers == 48);
    CHECK(profile.validation.status == hbfsim::ValidationStatus::Passed);

    const auto stage4 = hbfsim::load_exact_profile(
        "tests/fixtures/exact/sm120-stage4-valid.json");
    CHECK(stage4.schema_version == 2);
    CHECK(stage4.calibration.gnic.count == 4);
    CHECK(stage4.calibration.gpc.count == 2);
    CHECK(stage4.calibration.routing.version == 1);
    CHECK(stage4.calibration.routing.gnic_lut.size() == 8);
    CHECK(stage4.calibration.label_semantics == "contention_equivalent");
    CHECK(stage4.calibration.counter_thresholds.size() == 3);

    auto staged = stage4_fixture();
    staged["runtime_artifacts"] = {
        {"bundle_root", "/tmp/bundles"},
        {"prepatched_ptx_dir", "/tmp/prepatched"},
        {"pass_manifest", "/tmp/pass-manifest.jsonl"},
    };
    staged["fit_report"] = {{"schema_version", 1}};
    staged["validation"] = {{"status", "pending"}};
    CHECK(hbfsim::parse_exact_profile(staged.dump()).validation.status ==
          hbfsim::ValidationStatus::Pending);
    expect_stage4_error(
        [](auto& value) {
            value["runtime_artifacts"] = {
                {"bundle_root", "relative"},
                {"prepatched_ptx_dir", "/tmp/prepatched"},
                {"pass_manifest", "/tmp/pass-manifest.jsonl"},
            };
        },
        "invalid_field");

    expect_stage4_error(
        [](auto& value) { value["calibration"]["gnic"]["count"] = 3; },
        "invalid_queue_count");
    expect_stage4_error(
        [](auto& value) { value["calibration"]["gpc"]["count"] = 3; },
        "invalid_queue_count");
    expect_stage4_error(
        [](auto& value) {
            value["calibration"]["routing"]["inputs"].push_back("smsp_id");
        },
        "unknown_routing_input");
    expect_stage4_error(
        [](auto& value) {
            value["calibration"]["label_semantics"] = "physical_channel";
        },
        "physical_channel_claim");
    expect_stage4_error(
        [](auto& value) {
            value["calibration"]["raw_holdout_sha256"] =
                value["calibration"]["raw_training_sha256"];
        },
        "training_validation_overlap");
    expect_stage4_error(
        [](auto& value) {
            value["calibration"]["counter_thresholds"][0]
                 ["max_error_percent"] = 10.01;
        },
        "invalid_counter_threshold");

    expect_error(
        [](auto& value) {
            value["target"]["compute_capability_minor"] = 1;
        },
        "target_not_sm120");
    expect_error(
        [](auto& value) {
            value["modules"][0]["cubin_sha256"] =
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
        },
        "invalid_sha256");
    expect_error(
        [](auto& value) {
            value["modules"].push_back(value["modules"][0]);
        },
        "duplicate_module_id");
    expect_error(
        [](auto& value) {
            value["modules"][0]["kernels"].push_back(
                value["modules"][0]["kernels"][0]);
        },
        "duplicate_kernel");
    expect_error(
        [](auto& value) { value["thresholds"]["p50_percent"] = 5.01; },
        "threshold_exceeds_exact_limit");
    expect_error(
        [](auto& value) { value["validation"]["classes"].erase(0); },
        "validation_class_missing");
    expect_error(
        [](auto& value) {
            value["validation"]["holdout"]["case_ids"].push_back(
                "train-load");
        },
        "training_validation_overlap");
    expect_error(
        [](auto& value) {
            value["conditions"]["temperature_min_c"] = 80;
        },
        "invalid_temperature_interval");
    expect_error(
        [](auto& value) { value["unexpected"] = true; },
        "unknown_field");

    auto pending = fixture();
    pending["validation"]["status"] = "pending";
    CHECK(hbfsim::parse_exact_profile(pending.dump()).validation.status ==
          hbfsim::ValidationStatus::Pending);

    auto failed = fixture();
    failed["validation"]["status"] = "failed";
    failed["validation"]["classes"][0]["passed"] = false;
    CHECK(hbfsim::parse_exact_profile(failed.dump()).validation.status ==
          hbfsim::ValidationStatus::Failed);

    expect_error(
        [](auto& value) {
            value["validation"]["classes"][0]["passed"] = false;
        },
        "validation_not_passed");

    return 0;
}
