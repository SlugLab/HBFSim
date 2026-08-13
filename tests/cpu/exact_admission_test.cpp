#include <hbfsim/exact_admission.hpp>

#include <algorithm>
#include <functional>
#include <iterator>
#include <random>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

void require(bool condition, std::string_view message)
{
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

#define CHECK(expression) require(static_cast<bool>(expression), #expression)

const std::vector<std::string> kValidationClasses{
    "ordinary_load", "ordinary_store", "tma_load", "tma_store",
    "unicast", "multicast", "mixed_hbm_hbf"};

hbfsim::ExactKernelArtifact kernel()
{
    return {.name = "kernel",
            .registers = 48,
            .spill_store_bytes = 16,
            .spill_load_bytes = 8,
            .static_shared_bytes = 1024,
            .max_dynamic_shared_bytes = 49152,
            .block_threads = 256,
            .occupancy_blocks_per_sm = 2};
}

struct Case {
    hbfsim::ExactProfile profile;
    hbfsim::LoadedModuleEvidence evidence;
    hbfsim::ExactLiveEnvironment live;
    hbfsim::ExactRunContract contract;
};

Case matching_case()
{
    Case value;
    value.profile.schema_version = 1;
    value.profile.profile_id = "profile";
    value.profile.target = {
        .gpu_name = "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        .gpu_uuid = "GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174",
        .pci_vendor_id = 0x10de,
        .pci_device_id = 0x2bb5,
        .compute_capability_major = 12,
        .compute_capability_minor = 0,
        .driver_version = 13000,
    };
    value.profile.toolchain = {
        .cuda_version = "13.0",
        .ptxas_version = "ptxas release 13.0, V13.0.88",
        .nvdisasm_version = "nvdisasm release 13.0, V13.0.85",
        .cuobjdump_version = "cuobjdump release 13.0, V13.0.85",
        .ncu_version = "2025.4.1.0",
    };
    value.profile.conditions = {
        .sm_clock_mhz = 1830,
        .memory_clock_mhz = 14001,
        .power_limit_mw = 600000,
        .temperature_min_c = 30,
        .temperature_max_c = 75,
        .cache_condition = "warm_l2",
        .concurrency_condition = "exclusive_process",
        .cluster_shape = {.x = 2, .y = 1, .z = 1},
    };
    value.profile.thresholds = {
        .p50_percent = 5.0,
        .p95_percent = 10.0,
        .counter_percent = 10.0,
    };
    const std::string module_id = "ptx:sha256:" + std::string(64, '1');
    value.profile.modules.push_back({
        .module_id = module_id,
        .ptx_target = "sm_120",
        .original_ptx_sha256 = std::string(64, '2'),
        .transformed_ptx_sha256 = std::string(64, '3'),
        .cubin_sha256 = std::string(64, '4'),
        .sass_sha256 = std::string(64, '5'),
        .kernels = {kernel()},
    });
    value.profile.validation.status = hbfsim::ValidationStatus::Passed;
    for (const auto& operation_class : kValidationClasses) {
        value.profile.validation.classes.push_back({
            .operation_class = operation_class,
            .passed = true,
            .p50_error_percent = 2.0,
            .p95_error_percent = 4.0,
            .counter_error_percent = 5.0,
        });
    }

    value.evidence = {
        .module_id = module_id,
        .ptx_target = "sm_120",
        .original_ptx_sha256 = std::string(64, '2'),
        .transformed_ptx_sha256 = std::string(64, '3'),
        .cubin_sha256 = std::string(64, '4'),
        .sass_sha256 = std::string(64, '5'),
        .toolchain =
            {.cuda_release = "13.0",
             .ptxas_version = "ptxas release 13.0, V13.0.88",
             .nvdisasm_version = "nvdisasm release 13.0, V13.0.85",
             .cuobjdump_version =
                 "cuobjdump release 13.0, V13.0.85"},
        .kernels = {kernel()},
        .aot_verified = true,
    };
    value.live = {
        .gpu_name = value.profile.target.gpu_name,
        .gpu_uuid = value.profile.target.gpu_uuid,
        .pci_bus_id = "0000:8a:00.0",
        .pci_vendor_id = value.profile.target.pci_vendor_id,
        .pci_device_id = value.profile.target.pci_device_id,
        .compute_capability_major = 12,
        .compute_capability_minor = 0,
        .cuda_driver_version = 13000,
        .sm_clock_mhz = 1830,
        .memory_clock_mhz = 14001,
        .power_limit_mw = 600000,
        .temperature_c = 42,
        .current_process_is_exclusive = true,
        .captured_unix_ns = 1,
    };
    value.contract = {
        .cache_condition = "warm_l2",
        .concurrency_condition = "exclusive_process",
        .cluster_shape = {.x = 2, .y = 1, .z = 1},
        .cache_condition_epoch = 2,
        .latest_relevant_mutation_epoch = 1,
    };
    return value;
}

using Mutator = std::function<void(Case&)>;

struct Mutation {
    std::string reason;
    Mutator apply;
};

std::vector<Mutation> mutations()
{
    return {
        {"profile_not_validated",
         [](Case& value) {
             value.profile.validation.status =
                 hbfsim::ValidationStatus::Pending;
         }},
        {"validation_class_missing",
         [](Case& value) { value.profile.validation.classes.pop_back(); }},
        {"gpu_name_mismatch",
         [](Case& value) { value.live.gpu_name += " changed"; }},
        {"gpu_uuid_mismatch",
         [](Case& value) { value.live.gpu_uuid[4] = '0'; }},
        {"pci_device_mismatch",
         [](Case& value) { value.live.pci_device_id++; }},
        {"compute_capability_mismatch",
         [](Case& value) { value.live.compute_capability_major = 11; }},
        {"driver_version_mismatch",
         [](Case& value) { value.live.cuda_driver_version++; }},
        {"live_environment_missing",
         [](Case& value) { value.live.captured_unix_ns = 0; }},
        {"ptx_target_mismatch",
         [](Case& value) { value.evidence.ptx_target = "sm_120a"; }},
        {"toolchain_mismatch",
         [](Case& value) { value.evidence.toolchain.ptxas_version += "x"; }},
        {"aot_evidence_missing",
         [](Case& value) { value.evidence.aot_verified = false; }},
        {"original_ptx_sha256_mismatch",
         [](Case& value) { value.evidence.original_ptx_sha256[0] = 'a'; }},
        {"transformed_ptx_sha256_mismatch",
         [](Case& value) {
             value.evidence.transformed_ptx_sha256[0] = 'a';
         }},
        {"cubin_sha256_mismatch",
         [](Case& value) { value.evidence.cubin_sha256[0] = 'a'; }},
        {"sass_sha256_mismatch",
         [](Case& value) { value.evidence.sass_sha256[0] = 'a'; }},
        {"kernel_resource_missing",
         [](Case& value) { value.evidence.kernels.clear(); }},
        {"register_count_mismatch",
         [](Case& value) { value.evidence.kernels[0].registers++; }},
        {"spill_bytes_mismatch",
         [](Case& value) {
             value.evidence.kernels[0].spill_store_bytes++;
         }},
        {"shared_memory_mismatch",
         [](Case& value) {
             value.evidence.kernels[0].max_dynamic_shared_bytes++;
         }},
        {"occupancy_tier_mismatch",
         [](Case& value) {
             value.evidence.kernels[0].occupancy_blocks_per_sm++;
         }},
        {"sm_clock_mismatch",
         [](Case& value) { value.live.sm_clock_mhz++; }},
        {"memory_clock_mismatch",
         [](Case& value) { value.live.memory_clock_mhz++; }},
        {"power_limit_mismatch",
         [](Case& value) { value.live.power_limit_mw++; }},
        {"temperature_out_of_range",
         [](Case& value) { value.live.temperature_c = 76; }},
        {"cache_condition_unproven",
         [](Case& value) { value.contract.cache_condition_epoch = 1; }},
        {"concurrency_condition_unproven",
         [](Case& value) {
             value.live.current_process_is_exclusive = false;
         }},
        {"cluster_shape_mismatch",
         [](Case& value) { value.contract.cluster_shape.x++; }},
    };
}

}  // namespace

int main()
{
    hbfsim::ExactAdmissionEvaluator evaluator;
    const auto matching = matching_case();
    const auto admitted = evaluator.evaluate(
        matching.profile, matching.evidence, matching.live,
        matching.contract, "kernel");
    CHECK(admitted.allowed);
    CHECK(admitted.reasons.empty());
    CHECK(admitted.profile_id == "profile");
    CHECK(admitted.module_id == matching.evidence.module_id);
    CHECK(admitted.kernel == "kernel");

    const auto cases = mutations();
    std::vector<std::string> canonical_reason_order;
    canonical_reason_order.reserve(cases.size());
    for (const auto& mutation : cases) {
        canonical_reason_order.push_back(mutation.reason);
    }
    for (const auto& mutation : cases) {
        auto value = matching_case();
        mutation.apply(value);
        const auto rejected = evaluator.evaluate(
            value.profile, value.evidence, value.live, value.contract,
            "kernel");
        CHECK(!rejected.allowed);
        CHECK(rejected.reasons == std::vector<std::string>{mutation.reason});
    }

    {
        auto value = matching_case();
        value.profile.modules.clear();
        const auto rejected = evaluator.evaluate(
            value.profile, value.evidence, value.live, value.contract,
            "kernel");
        CHECK(rejected.reasons ==
              std::vector<std::string>{"module_artifact_missing"});
    }
    {
        auto value = matching_case();
        value.contract.cache_condition = "cold";
        const auto rejected = evaluator.evaluate(
            value.profile, value.evidence, value.live, value.contract,
            "kernel");
        CHECK(rejected.reasons ==
              std::vector<std::string>{"cache_condition_unproven"});
    }

    std::mt19937 random(0x120);
    for (int iteration = 0; iteration < 256; ++iteration) {
        auto value = matching_case();
        auto shuffled = cases;
        std::shuffle(shuffled.begin(), shuffled.end(), random);
        const auto mutation_count = 1 + random() % 5;
        for (std::size_t index = 0; index < mutation_count; ++index) {
            shuffled[index].apply(value);
        }
        const auto first = evaluator.evaluate(
            value.profile, value.evidence, value.live, value.contract,
            "kernel");
        const auto second = evaluator.evaluate(
            value.profile, value.evidence, value.live, value.contract,
            "kernel");
        CHECK(first.allowed == first.reasons.empty());
        CHECK(first.reasons == second.reasons);
        CHECK(std::set(first.reasons.begin(), first.reasons.end()).size() ==
              first.reasons.size());
        std::size_t prior = 0;
        bool first_reason = true;
        for (const auto& reason : first.reasons) {
            const auto found = std::find(canonical_reason_order.begin(),
                                         canonical_reason_order.end(), reason);
            CHECK(found != canonical_reason_order.end());
            const auto position = static_cast<std::size_t>(
                std::distance(canonical_reason_order.begin(), found));
            CHECK(first_reason || position > prior);
            first_reason = false;
            prior = position;
        }
    }
    return 0;
}
