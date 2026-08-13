#pragma once

#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace hbfsim {

enum class ValidationStatus { Pending, Passed, Failed };

struct ExactTarget {
    std::string gpu_name;
    std::string gpu_uuid;
    std::uint32_t pci_vendor_id{0};
    std::uint32_t pci_device_id{0};
    std::uint32_t compute_capability_major{0};
    std::uint32_t compute_capability_minor{0};
    std::uint32_t driver_version{0};
};

struct ExactToolchain {
    std::string cuda_version;
    std::string ptxas_version;
    std::string nvdisasm_version;
    std::string cuobjdump_version;
    std::string ncu_version;
};

struct ExactClusterShape {
    std::uint32_t x{0};
    std::uint32_t y{0};
    std::uint32_t z{0};
};

struct ExactConditions {
    std::uint32_t sm_clock_mhz{0};
    std::uint32_t memory_clock_mhz{0};
    std::uint32_t power_limit_mw{0};
    std::uint32_t temperature_min_c{0};
    std::uint32_t temperature_max_c{0};
    std::string cache_condition;
    std::string concurrency_condition;
    ExactClusterShape cluster_shape;
};

struct ExactThresholds {
    double p50_percent{0};
    double p95_percent{0};
    double counter_percent{0};
};

struct ExactFutureLimits {
    std::uint32_t max_thread_futures{0};
    std::uint32_t max_warp_futures{0};
    std::uint32_t max_cta_futures{0};
    std::uint32_t max_cluster_futures{0};
    std::uint32_t max_thread_async_objects{0};
    std::uint32_t max_warp_async_objects{0};
    std::uint32_t max_cta_async_objects{0};
    std::uint32_t max_cluster_async_objects{0};
};

struct ExactKernelArtifact {
    std::string name;
    std::uint32_t registers{0};
    std::uint64_t spill_store_bytes{0};
    std::uint64_t spill_load_bytes{0};
    std::uint64_t static_shared_bytes{0};
    std::uint64_t max_dynamic_shared_bytes{0};
    std::uint32_t block_threads{0};
    std::uint32_t occupancy_blocks_per_sm{0};
};

struct ExactModuleArtifact {
    std::string module_id;
    std::string ptx_target;
    std::string original_ptx_sha256;
    std::string transformed_ptx_sha256;
    std::string cubin_sha256;
    std::string sass_sha256;
    std::vector<ExactKernelArtifact> kernels;
};

struct ExactDataset {
    std::string manifest_sha256;
    std::vector<std::string> case_ids;
};

struct ExactValidationClass {
    std::string operation_class;
    bool passed{false};
    double p50_error_percent{0};
    double p95_error_percent{0};
    double counter_error_percent{0};
};

struct ExactValidation {
    ValidationStatus status{ValidationStatus::Pending};
    ExactDataset training;
    ExactDataset holdout;
    std::vector<ExactValidationClass> classes;
};

struct ExactProfile {
    std::uint32_t schema_version{0};
    std::string profile_id;
    ExactTarget target;
    ExactToolchain toolchain;
    ExactConditions conditions;
    ExactThresholds thresholds;
    ExactFutureLimits limits;
    std::vector<ExactModuleArtifact> modules;
    ExactValidation validation;
};

class ExactProfileError : public std::runtime_error {
  public:
    ExactProfileError(std::string reason, std::string message);
    [[nodiscard]] const std::string& reason() const noexcept;

  private:
    std::string reason_;
};

[[nodiscard]] ExactProfile parse_exact_profile(std::string_view json);
[[nodiscard]] ExactProfile
load_exact_profile(const std::filesystem::path& path);

}  // namespace hbfsim
