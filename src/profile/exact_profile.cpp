#include <hbfsim/exact_profile.hpp>

#include <json.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <iterator>
#include <set>
#include <unordered_set>

namespace hbfsim {
namespace {

using json = nlohmann::json;

[[noreturn]] void fail(std::string reason, std::string message)
{
    throw ExactProfileError(std::move(reason), std::move(message));
}

void object_with_keys(const json& value,
                      std::initializer_list<std::string_view> required,
                      std::initializer_list<std::string_view> optional = {})
{
    if (!value.is_object()) {
        fail("invalid_field", "expected an object");
    }
    std::set<std::string_view> allowed(required.begin(), required.end());
    allowed.insert(optional.begin(), optional.end());
    for (const auto& [key, unused] : value.items()) {
        (void)unused;
        if (!allowed.contains(key)) {
            fail("unknown_field", "unknown exact profile field: " + key);
        }
    }
    for (const auto key : required) {
        if (!value.contains(std::string(key))) {
            fail("missing_field", "missing exact profile field: " +
                                      std::string(key));
        }
    }
}

template <class T>
T field(const json& value, std::string_view key)
{
    try {
        return value.at(std::string(key)).get<T>();
    } catch (const json::exception&) {
        fail("invalid_field", "invalid exact profile field: " +
                                  std::string(key));
    }
}

std::string nonempty_string(const json& value, std::string_view key)
{
    auto result = field<std::string>(value, key);
    if (result.empty()) {
        fail("invalid_field", "empty exact profile field: " +
                                  std::string(key));
    }
    return result;
}

std::uint32_t positive_u32(const json& value, std::string_view key)
{
    const auto result = field<std::uint32_t>(value, key);
    if (result == 0) {
        fail("invalid_field", "zero exact profile field: " +
                                  std::string(key));
    }
    return result;
}

bool valid_sha256(std::string_view value)
{
    return value.size() == 64 &&
           std::all_of(value.begin(), value.end(), [](char character) {
               return (character >= '0' && character <= '9') ||
                      (character >= 'a' && character <= 'f');
           });
}

std::string sha256_field(const json& value, std::string_view key)
{
    auto result = nonempty_string(value, key);
    if (!valid_sha256(result)) {
        fail("invalid_sha256", "invalid SHA-256 field: " +
                                   std::string(key));
    }
    return result;
}

double nonnegative_number(const json& value, std::string_view key)
{
    const auto result = field<double>(value, key);
    if (!std::isfinite(result) || result < 0.0) {
        fail("invalid_field", "invalid nonnegative field: " +
                                  std::string(key));
    }
    return result;
}

ExactTarget parse_target(const json& value)
{
    object_with_keys(value,
                     {"gpu_name", "gpu_uuid", "pci_vendor_id",
                      "pci_device_id", "compute_capability_major",
                      "compute_capability_minor", "driver_version"});
    ExactTarget result{
        .gpu_name = nonempty_string(value, "gpu_name"),
        .gpu_uuid = nonempty_string(value, "gpu_uuid"),
        .pci_vendor_id = positive_u32(value, "pci_vendor_id"),
        .pci_device_id = positive_u32(value, "pci_device_id"),
        .compute_capability_major =
            positive_u32(value, "compute_capability_major"),
        .compute_capability_minor =
            field<std::uint32_t>(value, "compute_capability_minor"),
        .driver_version = positive_u32(value, "driver_version"),
    };
    if (result.compute_capability_major != 12 ||
        result.compute_capability_minor != 0) {
        fail("target_not_sm120", "exact profile target must be CC 12.0");
    }
    return result;
}

ExactToolchain parse_toolchain(const json& value)
{
    object_with_keys(value,
                     {"cuda_version", "ptxas_version", "nvdisasm_version",
                      "cuobjdump_version", "ncu_version"});
    return {
        .cuda_version = nonempty_string(value, "cuda_version"),
        .ptxas_version = nonempty_string(value, "ptxas_version"),
        .nvdisasm_version = nonempty_string(value, "nvdisasm_version"),
        .cuobjdump_version = nonempty_string(value, "cuobjdump_version"),
        .ncu_version = nonempty_string(value, "ncu_version"),
    };
}

ExactConditions parse_conditions(const json& value)
{
    object_with_keys(value,
                     {"sm_clock_mhz", "memory_clock_mhz", "power_limit_mw",
                      "temperature_min_c", "temperature_max_c",
                      "cache_condition", "concurrency_condition",
                      "cluster_shape"},
                     {"clock_control", "sm_clock_min_mhz",
                      "sm_clock_max_mhz", "memory_clock_min_mhz",
                      "memory_clock_max_mhz"});
    const auto& shape = value.at("cluster_shape");
    object_with_keys(shape, {"x", "y", "z"});
    ExactConditions result{
        .sm_clock_mhz = positive_u32(value, "sm_clock_mhz"),
        .memory_clock_mhz = positive_u32(value, "memory_clock_mhz"),
        .power_limit_mw = positive_u32(value, "power_limit_mw"),
        .temperature_min_c = field<std::uint32_t>(value, "temperature_min_c"),
        .temperature_max_c = field<std::uint32_t>(value, "temperature_max_c"),
        .cache_condition = nonempty_string(value, "cache_condition"),
        .concurrency_condition =
            nonempty_string(value, "concurrency_condition"),
        .cluster_shape = {.x = positive_u32(shape, "x"),
                          .y = positive_u32(shape, "y"),
                          .z = positive_u32(shape, "z")},
    };
    if (result.temperature_min_c > result.temperature_max_c) {
        fail("invalid_temperature_interval",
             "temperature_min_c exceeds temperature_max_c");
    }
    const auto dynamic_fields =
        static_cast<unsigned>(value.contains("clock_control")) +
        static_cast<unsigned>(value.contains("sm_clock_min_mhz")) +
        static_cast<unsigned>(value.contains("sm_clock_max_mhz")) +
        static_cast<unsigned>(value.contains("memory_clock_min_mhz")) +
        static_cast<unsigned>(value.contains("memory_clock_max_mhz"));
    if (dynamic_fields == 0) {
        result.clock_control = "base";
        result.sm_clock_min_mhz = result.sm_clock_mhz;
        result.sm_clock_max_mhz = result.sm_clock_mhz;
        result.memory_clock_min_mhz = result.memory_clock_mhz;
        result.memory_clock_max_mhz = result.memory_clock_mhz;
    } else if (dynamic_fields == 5) {
        result.clock_control = nonempty_string(value, "clock_control");
        result.sm_clock_min_mhz = positive_u32(value, "sm_clock_min_mhz");
        result.sm_clock_max_mhz = positive_u32(value, "sm_clock_max_mhz");
        result.memory_clock_min_mhz =
            positive_u32(value, "memory_clock_min_mhz");
        result.memory_clock_max_mhz =
            positive_u32(value, "memory_clock_max_mhz");
        if (result.clock_control != "none" ||
            result.sm_clock_min_mhz > result.sm_clock_mhz ||
            result.sm_clock_mhz > result.sm_clock_max_mhz ||
            result.memory_clock_min_mhz > result.memory_clock_mhz ||
            result.memory_clock_mhz > result.memory_clock_max_mhz) {
            fail("invalid_clock_interval",
                 "dynamic clock interval or policy is invalid");
        }
    } else {
        fail("invalid_clock_interval",
             "dynamic clock evidence must be complete");
    }
    return result;
}

ExactThresholds parse_thresholds(const json& value)
{
    object_with_keys(value,
                     {"p50_percent", "p95_percent", "counter_percent"});
    ExactThresholds result{
        .p50_percent = nonnegative_number(value, "p50_percent"),
        .p95_percent = nonnegative_number(value, "p95_percent"),
        .counter_percent = nonnegative_number(value, "counter_percent"),
    };
    if (result.p50_percent > 5.0 || result.p95_percent > 10.0 ||
        result.counter_percent > 10.0) {
        fail("threshold_exceeds_exact_limit",
             "exact threshold exceeds the declared acceptance limit");
    }
    return result;
}

ExactFutureLimits parse_limits(const json& value)
{
    object_with_keys(value,
                     {"max_thread_futures", "max_warp_futures",
                      "max_cta_futures", "max_cluster_futures",
                      "max_thread_async_objects", "max_warp_async_objects",
                      "max_cta_async_objects", "max_cluster_async_objects"});
    ExactFutureLimits result{
        .max_thread_futures = positive_u32(value, "max_thread_futures"),
        .max_warp_futures = positive_u32(value, "max_warp_futures"),
        .max_cta_futures = positive_u32(value, "max_cta_futures"),
        .max_cluster_futures = positive_u32(value, "max_cluster_futures"),
        .max_thread_async_objects =
            positive_u32(value, "max_thread_async_objects"),
        .max_warp_async_objects =
            positive_u32(value, "max_warp_async_objects"),
        .max_cta_async_objects =
            positive_u32(value, "max_cta_async_objects"),
        .max_cluster_async_objects =
            positive_u32(value, "max_cluster_async_objects"),
    };
    if (result.max_thread_futures > result.max_warp_futures ||
        result.max_warp_futures > result.max_cta_futures ||
        result.max_cta_futures > result.max_cluster_futures ||
        result.max_thread_async_objects > result.max_warp_async_objects ||
        result.max_warp_async_objects > result.max_cta_async_objects ||
        result.max_cta_async_objects > result.max_cluster_async_objects) {
        fail("invalid_limit_order", "exact limits must be nondecreasing");
    }
    return result;
}

ExactKernelArtifact parse_kernel(const json& value)
{
    object_with_keys(value,
                     {"name", "registers", "spill_store_bytes",
                      "spill_load_bytes", "static_shared_bytes",
                      "max_dynamic_shared_bytes", "block_threads",
                      "occupancy_blocks_per_sm"});
    return {
        .name = nonempty_string(value, "name"),
        .registers = positive_u32(value, "registers"),
        .spill_store_bytes = field<std::uint64_t>(value, "spill_store_bytes"),
        .spill_load_bytes = field<std::uint64_t>(value, "spill_load_bytes"),
        .static_shared_bytes =
            field<std::uint64_t>(value, "static_shared_bytes"),
        .max_dynamic_shared_bytes =
            field<std::uint64_t>(value, "max_dynamic_shared_bytes"),
        .block_threads = positive_u32(value, "block_threads"),
        .occupancy_blocks_per_sm =
            positive_u32(value, "occupancy_blocks_per_sm"),
    };
}

ExactModuleArtifact parse_module(const json& value)
{
    object_with_keys(value,
                     {"module_id", "ptx_target", "original_ptx_sha256",
                      "transformed_ptx_sha256", "cubin_sha256",
                      "sass_sha256", "kernels"});
    const auto module_id = nonempty_string(value, "module_id");
    constexpr std::string_view prefix = "ptx:sha256:";
    if (!module_id.starts_with(prefix) ||
        !valid_sha256(std::string_view(module_id).substr(prefix.size()))) {
        fail("invalid_module_id", "invalid exact module ID");
    }
    const auto target = nonempty_string(value, "ptx_target");
    if (target != "sm_120" && target != "sm_120a" && target != "sm_120f") {
        fail("target_not_sm120", "unsupported exact PTX target");
    }
    const auto& kernels = value.at("kernels");
    if (!kernels.is_array() || kernels.empty()) {
        fail("invalid_field", "exact module requires kernels");
    }
    ExactModuleArtifact result{
        .module_id = module_id,
        .ptx_target = target,
        .original_ptx_sha256 = sha256_field(value, "original_ptx_sha256"),
        .transformed_ptx_sha256 =
            sha256_field(value, "transformed_ptx_sha256"),
        .cubin_sha256 = sha256_field(value, "cubin_sha256"),
        .sass_sha256 = sha256_field(value, "sass_sha256"),
    };
    std::unordered_set<std::string> names;
    for (const auto& item : kernels) {
        auto kernel = parse_kernel(item);
        if (!names.insert(kernel.name).second) {
            fail("duplicate_kernel", "duplicate kernel in exact module");
        }
        result.kernels.push_back(std::move(kernel));
    }
    return result;
}

ExactDataset parse_dataset(const json& value)
{
    object_with_keys(value, {"manifest_sha256", "case_ids"});
    const auto& cases = value.at("case_ids");
    if (!cases.is_array() || cases.empty()) {
        fail("invalid_field", "exact dataset requires case IDs");
    }
    ExactDataset result{.manifest_sha256 =
                            sha256_field(value, "manifest_sha256")};
    std::unordered_set<std::string> unique;
    for (const auto& item : cases) {
        if (!item.is_string() || item.get_ref<const std::string&>().empty()) {
            fail("invalid_field", "invalid exact dataset case ID");
        }
        auto id = item.get<std::string>();
        if (!unique.insert(id).second) {
            fail("duplicate_case_id", "duplicate exact dataset case ID");
        }
        result.case_ids.push_back(std::move(id));
    }
    return result;
}

ValidationStatus parse_status(const json& value)
{
    const auto text = nonempty_string(value, "status");
    if (text == "pending") {
        return ValidationStatus::Pending;
    }
    if (text == "passed") {
        return ValidationStatus::Passed;
    }
    if (text == "failed") {
        return ValidationStatus::Failed;
    }
    fail("invalid_validation_status", "invalid exact validation status");
}

ExactValidationClass parse_validation_class(const json& value)
{
    object_with_keys(value,
                     {"operation_class", "passed", "p50_error_percent",
                      "p95_error_percent", "counter_error_percent"});
    return {
        .operation_class = nonempty_string(value, "operation_class"),
        .passed = field<bool>(value, "passed"),
        .p50_error_percent =
            nonnegative_number(value, "p50_error_percent"),
        .p95_error_percent =
            nonnegative_number(value, "p95_error_percent"),
        .counter_error_percent =
            nonnegative_number(value, "counter_error_percent"),
    };
}

ExactValidation parse_validation(const json& value,
                                 const ExactThresholds& thresholds)
{
    object_with_keys(value, {"status"}, {"training", "holdout", "classes"});
    ExactValidation result{.status = parse_status(value)};
    const auto evidence_fields =
        static_cast<unsigned>(value.contains("training")) +
        static_cast<unsigned>(value.contains("holdout")) +
        static_cast<unsigned>(value.contains("classes"));
    if (evidence_fields == 0 && result.status != ValidationStatus::Passed) {
        return result;
    }
    if (evidence_fields != 3) {
        fail("missing_field",
             "validation evidence must include training, holdout, and classes");
    }
    result.training = parse_dataset(value.at("training"));
    result.holdout = parse_dataset(value.at("holdout"));
    if (result.training.manifest_sha256 == result.holdout.manifest_sha256) {
        fail("training_validation_overlap",
             "training and holdout manifests must differ");
    }
    std::unordered_set<std::string> training_cases(
        result.training.case_ids.begin(), result.training.case_ids.end());
    for (const auto& id : result.holdout.case_ids) {
        if (training_cases.contains(id)) {
            fail("training_validation_overlap",
                 "training and holdout case IDs overlap");
        }
    }
    static constexpr std::array required_classes{
        "ordinary_load", "ordinary_store", "tma_load", "tma_store",
        "unicast",       "multicast",      "mixed_hbm_hbf"};
    const auto& classes = value.at("classes");
    if (!classes.is_array()) {
        fail("invalid_field", "validation classes must be an array");
    }
    std::unordered_set<std::string> names;
    bool all_passed = true;
    for (const auto& item : classes) {
        auto record = parse_validation_class(item);
        if (!names.insert(record.operation_class).second) {
            fail("duplicate_validation_class",
                 "duplicate exact validation class");
        }
        all_passed = all_passed && record.passed &&
                     record.p50_error_percent <= thresholds.p50_percent &&
                     record.p95_error_percent <= thresholds.p95_percent &&
                     record.counter_error_percent <=
                         thresholds.counter_percent;
        result.classes.push_back(std::move(record));
    }
    for (const auto* name : required_classes) {
        if (!names.contains(name)) {
            fail("validation_class_missing",
                 "required exact validation class is missing");
        }
    }
    if (names.size() != required_classes.size()) {
        fail("unknown_validation_class", "unknown exact validation class");
    }
    if (result.status == ValidationStatus::Passed && !all_passed) {
        fail("validation_not_passed",
             "passed validation contains a failing class");
    }
    if (result.status == ValidationStatus::Failed && all_passed) {
        fail("validation_status_inconsistent",
             "failed validation contains no failing class");
    }
    return result;
}

CalibratedQueue parse_queue(const json& value, std::uint32_t expected_count,
                            std::string_view name)
{
    object_with_keys(value,
                     {"count", "depth", "arbitration",
                      "service_ns_by_class"});
    CalibratedQueue result{
        .count = positive_u32(value, "count"),
        .depth = positive_u32(value, "depth"),
        .arbitration = nonempty_string(value, "arbitration"),
        .service_ns_by_class =
            field<std::vector<std::uint64_t>>(value, "service_ns_by_class"),
    };
    if (result.count != expected_count) {
        fail("invalid_queue_count", std::string(name) +
                                        " queue count differs");
    }
    if (result.arbitration != "fifo" &&
        result.arbitration != "round_robin") {
        fail("invalid_arbitration", "unknown calibrated queue arbitration");
    }
    if (result.service_ns_by_class.empty() ||
        std::any_of(result.service_ns_by_class.begin(),
                    result.service_ns_by_class.end(),
                    [](std::uint64_t value) { return value == 0; })) {
        fail("invalid_field", "calibrated service classes must be positive");
    }
    return result;
}

std::vector<std::string> unique_strings(const json& value,
                                        std::string_view key)
{
    auto result = field<std::vector<std::string>>(value, key);
    if (result.empty()) fail("invalid_field", "empty calibrated string list");
    std::unordered_set<std::string> unique;
    for (const auto& item : result) {
        if (item.empty() || !unique.insert(item).second) {
            fail("invalid_field", "invalid calibrated string list");
        }
    }
    return result;
}

RoutingProgram parse_routing(const json& value)
{
    object_with_keys(value,
                     {"version", "program_sha256", "inputs",
                      "smsp_proxy_lut", "gnic_lut", "gpc_lut"});
    RoutingProgram result{
        .version = positive_u32(value, "version"),
        .program_sha256 = sha256_field(value, "program_sha256"),
        .inputs = unique_strings(value, "inputs"),
        .smsp_proxy_lut =
            field<std::vector<std::uint32_t>>(value, "smsp_proxy_lut"),
        .gnic_lut = field<std::vector<std::uint32_t>>(value, "gnic_lut"),
        .gpc_lut = field<std::vector<std::uint32_t>>(value, "gpc_lut"),
    };
    static const std::set<std::string> allowed_inputs{
        "smid", "warpid", "cta_shape", "resident_warps",
        "cluster_ctarank", "operation"};
    if (result.inputs.size() != allowed_inputs.size() ||
        !std::all_of(result.inputs.begin(), result.inputs.end(),
                     [&](const auto& input) {
                         return allowed_inputs.contains(input);
                     })) {
        fail("unknown_routing_input",
             "routing inputs must be the declared visible proxies");
    }
    if (result.smsp_proxy_lut.empty() || result.gnic_lut.empty() ||
        result.gpc_lut.empty() ||
        std::any_of(result.gnic_lut.begin(), result.gnic_lut.end(),
                    [](auto value) { return value >= 4; }) ||
        std::any_of(result.gpc_lut.begin(), result.gpc_lut.end(),
                    [](auto value) { return value >= 2; })) {
        fail("invalid_routing_lut", "invalid calibrated routing LUT");
    }
    return result;
}

void validate_counter_error_evidence(
    const json& calibration, const std::vector<std::string>& metrics)
{
    const auto has_contract = calibration.contains("counter_error_contract");
    const auto has_scales =
        calibration.contains("counter_error_scale_by_class");
    if (has_contract != has_scales) {
        fail("invalid_counter_error_contract",
             "counter error contract and scales must be supplied together");
    }
    if (!has_contract) return;
    const auto& contract = calibration.at("counter_error_contract");
    object_with_keys(
        contract,
        {"version", "percentage_metrics", "traffic_metrics",
         "duration_metrics", "fallback_metrics"});
    if (positive_u32(contract, "version") != 1 ||
        nonempty_string(contract, "percentage_metrics") !=
            "absolute_percentage_points" ||
        nonempty_string(contract, "traffic_metrics") !=
            "native_or_logical_issued_or_training_class_envelope" ||
        nonempty_string(contract, "duration_metrics") !=
            "relative_to_native" ||
        nonempty_string(contract, "fallback_metrics") !=
            "relative_to_native") {
        fail("invalid_counter_error_contract",
             "unknown counter error normalization contract");
    }
    const auto& scales = calibration.at("counter_error_scale_by_class");
    static constexpr std::array operation_classes{
        "ordinary_load", "ordinary_store", "tma_load", "tma_store",
        "unicast",       "multicast",      "mixed_hbm_hbf"};
    if (!scales.is_object() || scales.size() != operation_classes.size()) {
        fail("invalid_counter_error_contract",
             "counter error class scales are incomplete");
    }
    const std::set<std::string> metric_names(metrics.begin(), metrics.end());
    for (const auto* operation : operation_classes) {
        if (!scales.contains(operation)) {
            fail("invalid_counter_error_contract",
                 "counter error class scale is missing");
        }
        const auto& class_scales = scales.at(operation);
        if (!class_scales.is_object() ||
            class_scales.size() != metric_names.size()) {
            fail("invalid_counter_error_contract",
                 "counter error metric scales are incomplete");
        }
        for (const auto& metric : metric_names) {
            if (!class_scales.contains(metric)) {
                fail("invalid_counter_error_contract",
                     "counter error metric scale is missing");
            }
            const auto scale = nonnegative_number(class_scales, metric);
            const auto percentage = metric.ends_with(".pct") ||
                                    metric.find(".pct_of_peak_") !=
                                        std::string::npos;
            const auto traffic = metric == "dram__bytes.sum" ||
                                 metric == "lts__t_sectors.sum";
            if ((percentage && scale != 100.0) ||
                (traffic && scale <= 0.0)) {
                fail("invalid_counter_error_contract",
                     "counter error metric scale violates its units");
            }
        }
    }
}

ExactWorkloadDomain parse_workload_domain(const json& value)
{
    object_with_keys(value,
                     {"schema_version", "match_policy", "program_sha256",
                      "feature_names", "vectors_by_class"});
    if (positive_u32(value, "schema_version") != 1) {
        fail("invalid_workload_domain", "unknown workload-domain schema");
    }
    ExactWorkloadDomain result{
        .match_policy = nonempty_string(value, "match_policy"),
        .program_sha256 = sha256_field(value, "program_sha256"),
        .feature_names = unique_strings(value, "feature_names"),
    };
    static const std::vector<std::string> expected_features{
        "log2_issued_operations", "log2_bytes", "log2_resident_warps",
        "log2_queue_depth", "dimension_count", "cache_warm",
        "log2_iterations", "log2_load_use_distance_plus_one",
        "log2_tile_elements", "cluster_size", "multicast_targets",
    };
    static const std::set<std::string> expected_classes{
        "ordinary_load", "ordinary_store", "tma_load", "tma_store",
        "unicast", "multicast", "mixed_hbm_hbf",
    };
    if (result.match_policy != "exact_calibrated_vector" ||
        result.feature_names != expected_features) {
        fail("invalid_workload_domain",
             "workload domain policy or feature order is invalid");
    }
    const auto& classes = value.at("vectors_by_class");
    if (!classes.is_object() || classes.size() != expected_classes.size()) {
        fail("invalid_workload_domain",
             "workload domain classes are incomplete");
    }
    for (const auto& operation : expected_classes) {
        if (!classes.contains(operation) ||
            !classes.at(operation).is_array() ||
            classes.at(operation).empty()) {
            fail("invalid_workload_domain",
                 "workload domain class vectors are missing");
        }
        std::set<std::vector<double>> unique;
        for (const auto& encoded : classes.at(operation)) {
            const auto features = encoded.get<std::vector<double>>();
            if (features.size() != expected_features.size() ||
                std::any_of(features.begin(), features.end(),
                            [](double item) {
                                return !std::isfinite(item) || item < 0.0;
                            }) ||
                !unique.insert(features).second) {
                fail("invalid_workload_domain",
                     "workload domain vector is invalid or duplicated");
            }
            result.vectors.push_back({.operation_class = operation,
                                      .features = features});
        }
    }
    return result;
}

ExactCalibration parse_calibration(const json& value,
                                   const ExactThresholds& thresholds)
{
    object_with_keys(value,
                     {"label_semantics", "gnic", "gpc", "routing",
                      "metric_names", "raw_training_sha256",
                      "raw_holdout_sha256", "fitted_case_ids", "residuals",
                      "counter_thresholds", "workload_domain"},
                     {"counter_error_contract",
                      "counter_error_scale_by_class"});
    ExactCalibration result{
        .label_semantics = nonempty_string(value, "label_semantics"),
        .gnic = parse_queue(value.at("gnic"), 4, "GNIC"),
        .gpc = parse_queue(value.at("gpc"), 2, "GPC"),
        .routing = parse_routing(value.at("routing")),
        .metric_names = unique_strings(value, "metric_names"),
        .raw_training_sha256 = sha256_field(value, "raw_training_sha256"),
        .raw_holdout_sha256 = sha256_field(value, "raw_holdout_sha256"),
        .fitted_case_ids = unique_strings(value, "fitted_case_ids"),
        .workload_domain =
            parse_workload_domain(value.at("workload_domain")),
    };
    if (result.label_semantics != "contention_equivalent") {
        fail("physical_channel_claim",
             "channel labels must be contention-equivalent classes");
    }
    if (result.raw_training_sha256 == result.raw_holdout_sha256) {
        fail("training_validation_overlap", "raw evidence hashes overlap");
    }
    std::unordered_set<std::string> residual_names;
    for (const auto& item : value.at("residuals")) {
        object_with_keys(item,
                         {"operation_class", "p50_error_percent",
                          "p95_error_percent"});
        CalibrationResidual residual{
            .operation_class = nonempty_string(item, "operation_class"),
            .p50_error_percent =
                nonnegative_number(item, "p50_error_percent"),
            .p95_error_percent =
                nonnegative_number(item, "p95_error_percent"),
        };
        if (!residual_names.insert(residual.operation_class).second) {
            fail("invalid_field", "duplicate calibration residual class");
        }
        result.residuals.push_back(std::move(residual));
    }
    if (result.residuals.empty()) {
        fail("invalid_field", "calibration residuals are empty");
    }
    std::unordered_set<std::string> metrics(result.metric_names.begin(),
                                            result.metric_names.end());
    std::unordered_set<std::string> threshold_metrics;
    for (const auto& item : value.at("counter_thresholds")) {
        object_with_keys(item, {"metric", "max_error_percent"});
        CounterThreshold threshold{
            .metric = nonempty_string(item, "metric"),
            .max_error_percent =
                nonnegative_number(item, "max_error_percent"),
        };
        if (!metrics.contains(threshold.metric) ||
            !threshold_metrics.insert(threshold.metric).second ||
            threshold.max_error_percent > thresholds.counter_percent) {
            fail("invalid_counter_threshold",
                 "counter threshold is missing, duplicated, or relaxed");
        }
        result.counter_thresholds.push_back(std::move(threshold));
    }
    if (threshold_metrics.size() != metrics.size()) {
        fail("invalid_counter_threshold",
             "every declared metric requires a counter threshold");
    }
    validate_counter_error_evidence(value, result.metric_names);
    return result;
}

}  // namespace

ExactProfileError::ExactProfileError(std::string reason, std::string message)
    : std::runtime_error(std::move(message)), reason_(std::move(reason))
{
}

const std::string& ExactProfileError::reason() const noexcept
{
    return reason_;
}

ExactProfile parse_exact_profile(std::string_view text)
{
    try {
        const auto root = json::parse(text);
        object_with_keys(root,
                         {"schema_version", "profile_id", "target",
                          "toolchain", "conditions", "thresholds", "limits",
                          "modules", "validation"},
                         {"calibration", "runtime_artifacts", "fit_report"});
        if (root.contains("runtime_artifacts")) {
            const auto& artifacts = root.at("runtime_artifacts");
            object_with_keys(artifacts,
                             {"bundle_root", "prepatched_ptx_dir",
                              "pass_manifest"});
            for (const auto key : {"bundle_root", "prepatched_ptx_dir",
                                   "pass_manifest"}) {
                const auto path = nonempty_string(artifacts, key);
                if (path.front() != '/') {
                    fail("invalid_field",
                         "runtime artifact path must be absolute");
                }
            }
        }
        if (root.contains("fit_report") &&
            !root.at("fit_report").is_object()) {
            fail("invalid_field", "fit_report must be an object");
        }
        ExactProfile result;
        result.schema_version = field<std::uint32_t>(root, "schema_version");
        if (result.schema_version != 1 && result.schema_version != 2) {
            fail("unsupported_schema_version",
                 "unsupported exact profile schema version");
        }
        result.profile_id = nonempty_string(root, "profile_id");
        result.target = parse_target(root.at("target"));
        result.toolchain = parse_toolchain(root.at("toolchain"));
        result.conditions = parse_conditions(root.at("conditions"));
        result.thresholds = parse_thresholds(root.at("thresholds"));
        result.limits = parse_limits(root.at("limits"));
        const auto& modules = root.at("modules");
        if (!modules.is_array() || modules.empty()) {
            fail("invalid_field", "exact profile requires modules");
        }
        std::unordered_set<std::string> module_ids;
        for (const auto& item : modules) {
            auto module = parse_module(item);
            if (!module_ids.insert(module.module_id).second) {
                fail("duplicate_module_id", "duplicate exact module ID");
            }
            result.modules.push_back(std::move(module));
        }
        result.validation =
            parse_validation(root.at("validation"), result.thresholds);
        if (result.schema_version == 2) {
            if (!root.contains("calibration")) {
                fail("missing_field", "schema v2 requires calibration");
            }
            result.calibration =
                parse_calibration(root.at("calibration"), result.thresholds);
        } else if (root.contains("calibration")) {
            fail("unknown_field", "schema v1 cannot contain calibration");
        }
        return result;
    } catch (const ExactProfileError&) {
        throw;
    } catch (const json::exception& error) {
        fail("invalid_json", error.what());
    }
}

ExactProfile load_exact_profile(const std::filesystem::path& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        fail("profile_io_error", "unable to open exact profile");
    }
    const std::string contents((std::istreambuf_iterator<char>(input)),
                               std::istreambuf_iterator<char>());
    if (!input.eof() && input.fail()) {
        fail("profile_io_error", "unable to read exact profile");
    }
    return parse_exact_profile(contents);
}

}  // namespace hbfsim
