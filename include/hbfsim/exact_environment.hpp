#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

namespace hbfsim {

enum class ExactCudaAttribute : std::uint32_t {
    CombinedPciDeviceId,
    ComputeCapabilityMajor,
    ComputeCapabilityMinor,
};

enum class ExactClockDomain : std::uint32_t { Sm, Memory };

enum class ExactComputeMode : std::uint32_t {
    Default,
    ExclusiveProcess,
    Other,
};

struct ExactNvmlPciIdentity {
    std::string bus_id;
    std::uint32_t vendor_id{0};
    std::uint32_t device_id{0};
};

struct ExactCudaDriverApi {
    std::function<int(std::uintptr_t*)> current_context;
    std::function<int(std::int32_t*)> current_device;
    std::function<int(std::int32_t, std::string*)> device_name;
    std::function<int(std::int32_t, std::array<std::uint8_t, 16>*)>
        device_uuid;
    std::function<int(std::int32_t, std::string*)> pci_bus_id;
    std::function<int(std::int32_t, ExactCudaAttribute, std::int32_t*)>
        device_attribute;
    std::function<int(std::int32_t*)> driver_version;
};

struct ExactNvmlApi {
    bool available{false};
    int success_status{0};
    int not_supported_status{3};
    std::function<int(std::string_view, std::uintptr_t*)>
        device_by_pci_bus_id;
    std::function<int(std::uintptr_t, std::string*)> device_uuid;
    std::function<int(std::uintptr_t, ExactNvmlPciIdentity*)> pci_identity;
    std::function<int(std::uintptr_t, ExactClockDomain, std::uint32_t*)>
        clock_mhz;
    std::function<int(std::uintptr_t, std::uint32_t*)>
        enforced_power_limit_mw;
    std::function<int(std::uintptr_t, std::uint32_t*)>
        instantaneous_power_mw;
    std::function<int(std::uintptr_t, std::uint32_t*)> temperature_c;
    std::function<int(std::uintptr_t, ExactComputeMode*)> compute_mode;
    std::function<int(std::uintptr_t, std::vector<std::uint32_t>*)>
        compute_processes;
};

struct ExactLiveEnvironment {
    std::string gpu_name;
    std::string gpu_uuid;
    std::string pci_bus_id;
    std::uint32_t pci_vendor_id{0};
    std::uint32_t pci_device_id{0};
    std::uint32_t compute_capability_major{0};
    std::uint32_t compute_capability_minor{0};
    std::uint32_t cuda_driver_version{0};
    std::uint32_t sm_clock_mhz{0};
    std::uint32_t memory_clock_mhz{0};
    std::uint32_t power_limit_mw{0};
    std::uint32_t temperature_c{0};
    bool current_process_is_exclusive{false};
    std::uint64_t captured_unix_ns{0};
};

enum class ExactEnvironmentError : std::uint32_t {
    None = 0,
    InvalidApi = 1,
    CudaUnavailable = 2,
    NoCurrentCudaContext = 3,
    CudaQueryFailed = 4,
    UnsupportedComputeCapability = 5,
    InvalidPciIdentity = 6,
    NvmlUnavailable = 7,
    NvmlQueryFailed = 8,
    DeviceIdentityMismatch = 9,
    ClockQueryUnsupported = 10,
    InvalidPowerLimit = 11,
    TemperatureQueryFailed = 12,
    ExclusiveProcessViolation = 13,
    SnapshotChanged = 14,
    PowerQueryFailed = 15,
};

struct ExactEnvironmentResult {
    std::optional<ExactLiveEnvironment> environment;
    ExactEnvironmentError error{ExactEnvironmentError::None};
    std::string operation;
    int native_status{0};
};

struct NvmlThermalSample {
    std::uint64_t host_ns{0};
    std::int64_t gpu_millic{0};
    std::uint64_t gpu_power_mw{0};
};

struct NvmlThermalSampleResult {
    std::optional<NvmlThermalSample> sample;
    ExactEnvironmentError error{ExactEnvironmentError::None};
    std::string operation;
    int native_status{0};
};

[[nodiscard]] ExactEnvironmentResult collect_exact_environment(
    const ExactCudaDriverApi& driver, const ExactNvmlApi& nvml,
    std::uint32_t current_pid) noexcept;

[[nodiscard]] ExactEnvironmentResult
collect_live_exact_environment(std::uint32_t current_pid) noexcept;

[[nodiscard]] NvmlThermalSampleResult collect_nvml_thermal_sample(
    const ExactNvmlApi& nvml, std::string_view pci_bus_id,
    std::uint64_t host_ns) noexcept;

[[nodiscard]] NvmlThermalSampleResult collect_live_nvml_thermal_sample(
    std::string_view pci_bus_id, std::uint64_t host_ns) noexcept;

inline constexpr std::uint32_t kExactEnvironmentSnapshotAbiVersion = 1;
inline constexpr std::size_t kExactEnvironmentNameBytes = 128;
inline constexpr std::size_t kExactEnvironmentUuidBytes = 96;
inline constexpr std::size_t kExactEnvironmentPciBytes = 32;
inline constexpr std::size_t kExactEnvironmentOperationBytes = 64;

struct ExactEnvironmentSnapshotV1 {
    std::uint32_t abi_version{0};
    std::uint32_t struct_bytes{0};
    std::uint32_t error_code{0};
    std::int32_t native_status{0};
    std::uint64_t captured_unix_ns{0};
    std::uint32_t pci_vendor_id{0};
    std::uint32_t pci_device_id{0};
    std::uint32_t compute_capability_major{0};
    std::uint32_t compute_capability_minor{0};
    std::uint32_t cuda_driver_version{0};
    std::uint32_t sm_clock_mhz{0};
    std::uint32_t memory_clock_mhz{0};
    std::uint32_t power_limit_mw{0};
    std::uint32_t temperature_c{0};
    std::uint32_t current_process_is_exclusive{0};
    char gpu_name[kExactEnvironmentNameBytes]{};
    char gpu_uuid[kExactEnvironmentUuidBytes]{};
    char pci_bus_id[kExactEnvironmentPciBytes]{};
    char operation[kExactEnvironmentOperationBytes]{};
};

static_assert(std::is_standard_layout_v<ExactEnvironmentSnapshotV1>);

}  // namespace hbfsim

extern "C" int hbfsim_collect_exact_environment_v1(
    hbfsim::ExactEnvironmentSnapshotV1* output,
    std::size_t output_bytes) noexcept;
