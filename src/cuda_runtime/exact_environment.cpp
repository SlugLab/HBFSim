#include <hbfsim/exact_environment.hpp>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <utility>

#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
#include <cuda.h>
#include <dlfcn.h>
#include <nvml.h>
#endif

namespace hbfsim {
namespace {

constexpr std::uint32_t kNvidiaPciVendorId = 0x10de;

ExactEnvironmentResult failure(ExactEnvironmentError error,
                               std::string operation,
                               int native_status = 0)
{
    return {.environment = std::nullopt,
            .error = error,
            .operation = std::move(operation),
            .native_status = native_status};
}

bool complete(const ExactCudaDriverApi& api)
{
    return api.current_context && api.current_device && api.device_name &&
           api.device_uuid && api.pci_bus_id && api.device_attribute &&
           api.driver_version;
}

bool complete(const ExactNvmlApi& api)
{
    return api.device_by_pci_bus_id && api.device_uuid && api.pci_identity &&
           api.clock_mhz && api.enforced_power_limit_mw &&
           api.temperature_c && api.compute_mode && api.compute_processes;
}

std::optional<std::string> canonical_pci_bus_id(std::string_view value)
{
    if (value.empty() || value.size() >= 64) {
        return std::nullopt;
    }
    const std::string text(value);
    unsigned int domain = 0;
    unsigned int bus = 0;
    unsigned int device = 0;
    unsigned int function = 0;
    char trailing = '\0';
    if (std::sscanf(text.c_str(), "%x:%x:%x.%x%c", &domain, &bus, &device,
                    &function, &trailing) != 4 ||
        domain > 0xffff || bus > 0xff || device > 0x1f || function > 7) {
        return std::nullopt;
    }
    std::array<char, 16> output{};
    const auto count = std::snprintf(output.data(), output.size(),
                                     "%04x:%02x:%02x.%x", domain, bus,
                                     device, function);
    if (count <= 0 || static_cast<std::size_t>(count) >= output.size()) {
        return std::nullopt;
    }
    return std::string(output.data(), static_cast<std::size_t>(count));
}

std::string format_gpu_uuid(const std::array<std::uint8_t, 16>& uuid)
{
    std::array<char, 41> output{};
    const auto count = std::snprintf(
        output.data(), output.size(),
        "GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-"
        "%02x%02x%02x%02x%02x%02x",
        uuid[0], uuid[1], uuid[2], uuid[3], uuid[4], uuid[5], uuid[6],
        uuid[7], uuid[8], uuid[9], uuid[10], uuid[11], uuid[12], uuid[13],
        uuid[14], uuid[15]);
    if (count != 40) {
        return {};
    }
    return std::string(output.data(), 40);
}

ExactEnvironmentResult cuda_failure(std::string operation, int status)
{
    return failure(ExactEnvironmentError::CudaQueryFailed,
                   std::move(operation), status);
}

ExactEnvironmentResult nvml_failure(std::string operation, int status)
{
    return failure(ExactEnvironmentError::NvmlQueryFailed,
                   std::move(operation), status);
}

}  // namespace

ExactEnvironmentResult collect_exact_environment(
    const ExactCudaDriverApi& driver, const ExactNvmlApi& nvml,
    std::uint32_t current_pid) noexcept
{
    try {
        if (!complete(driver)) {
            return failure(ExactEnvironmentError::InvalidApi, "cuda_api");
        }
        std::uintptr_t context = 0;
        auto status = driver.current_context(&context);
        if (status != 0) {
            return cuda_failure("cuCtxGetCurrent", status);
        }
        if (context == 0) {
            return failure(ExactEnvironmentError::NoCurrentCudaContext,
                           "cuCtxGetCurrent");
        }

        std::int32_t device = -1;
        if ((status = driver.current_device(&device)) != 0 || device < 0) {
            return cuda_failure("cuCtxGetDevice", status);
        }

        ExactLiveEnvironment live;
        if ((status = driver.device_name(device, &live.gpu_name)) != 0 ||
            live.gpu_name.empty()) {
            return cuda_failure("cuDeviceGetName", status);
        }
        std::array<std::uint8_t, 16> raw_uuid{};
        if ((status = driver.device_uuid(device, &raw_uuid)) != 0) {
            return cuda_failure("cuDeviceGetUuid", status);
        }
        live.gpu_uuid = format_gpu_uuid(raw_uuid);
        if (live.gpu_uuid.empty()) {
            return cuda_failure("cuDeviceGetUuid", status);
        }
        std::string cuda_pci_bus_id;
        if ((status = driver.pci_bus_id(device, &cuda_pci_bus_id)) != 0) {
            return cuda_failure("cuDeviceGetPCIBusId", status);
        }
        const auto canonical_cuda_pci = canonical_pci_bus_id(cuda_pci_bus_id);
        if (!canonical_cuda_pci) {
            return failure(ExactEnvironmentError::InvalidPciIdentity,
                           "cuDeviceGetPCIBusId");
        }
        live.pci_bus_id = *canonical_cuda_pci;

        std::int32_t combined_pci = 0;
        if ((status = driver.device_attribute(
                 device, ExactCudaAttribute::CombinedPciDeviceId,
                 &combined_pci)) != 0) {
            return cuda_failure("CU_DEVICE_ATTRIBUTE_GPU_PCI_DEVICE_ID",
                                status);
        }
        const auto combined = static_cast<std::uint32_t>(combined_pci);
        live.pci_vendor_id = combined & 0xffffU;
        live.pci_device_id = combined >> 16U;
        if (live.pci_vendor_id != kNvidiaPciVendorId ||
            live.pci_device_id == 0) {
            return failure(ExactEnvironmentError::InvalidPciIdentity,
                           "CU_DEVICE_ATTRIBUTE_GPU_PCI_DEVICE_ID");
        }

        std::int32_t major = 0;
        std::int32_t minor = 0;
        if ((status = driver.device_attribute(
                 device, ExactCudaAttribute::ComputeCapabilityMajor,
                 &major)) != 0) {
            return cuda_failure("compute_capability_major", status);
        }
        if ((status = driver.device_attribute(
                 device, ExactCudaAttribute::ComputeCapabilityMinor,
                 &minor)) != 0) {
            return cuda_failure("compute_capability_minor", status);
        }
        if (major != 12 || minor != 0) {
            return failure(
                ExactEnvironmentError::UnsupportedComputeCapability,
                "compute_capability");
        }
        live.compute_capability_major = static_cast<std::uint32_t>(major);
        live.compute_capability_minor = static_cast<std::uint32_t>(minor);

        std::int32_t driver_version = 0;
        if ((status = driver.driver_version(&driver_version)) != 0 ||
            driver_version <= 0) {
            return cuda_failure("cuDriverGetVersion", status);
        }
        live.cuda_driver_version =
            static_cast<std::uint32_t>(driver_version);

        if (!nvml.available || !complete(nvml)) {
            return failure(ExactEnvironmentError::NvmlUnavailable,
                           "libnvidia-ml.so.1");
        }
        std::uintptr_t nvml_device = 0;
        status = nvml.device_by_pci_bus_id(live.pci_bus_id, &nvml_device);
        if (status != nvml.success_status || nvml_device == 0) {
            return nvml_failure("nvmlDeviceGetHandleByPciBusId_v2", status);
        }
        std::string nvml_uuid;
        if ((status = nvml.device_uuid(nvml_device, &nvml_uuid)) !=
            nvml.success_status) {
            return nvml_failure("nvmlDeviceGetUUID", status);
        }
        ExactNvmlPciIdentity nvml_pci;
        if ((status = nvml.pci_identity(nvml_device, &nvml_pci)) !=
            nvml.success_status) {
            return nvml_failure("nvmlDeviceGetPciInfo_v3", status);
        }
        const auto canonical_nvml_pci =
            canonical_pci_bus_id(nvml_pci.bus_id);
        if (nvml_uuid != live.gpu_uuid || !canonical_nvml_pci ||
            *canonical_nvml_pci != live.pci_bus_id ||
            nvml_pci.vendor_id != live.pci_vendor_id ||
            nvml_pci.device_id != live.pci_device_id) {
            return failure(ExactEnvironmentError::DeviceIdentityMismatch,
                           "cuda_nvml_identity");
        }

        auto query_clock = [&](ExactClockDomain domain, std::uint32_t* value,
                               std::string_view operation)
            -> std::optional<ExactEnvironmentResult> {
            const auto query_status =
                nvml.clock_mhz(nvml_device, domain, value);
            if (query_status == nvml.not_supported_status) {
                return failure(
                    ExactEnvironmentError::ClockQueryUnsupported,
                    std::string(operation), query_status);
            }
            if (query_status != nvml.success_status || *value == 0) {
                return nvml_failure(std::string(operation), query_status);
            }
            return std::nullopt;
        };

        if (auto error = query_clock(ExactClockDomain::Sm,
                                     &live.sm_clock_mhz,
                                     "nvmlDeviceGetClockInfo(SM)")) {
            return *error;
        }
        if (auto error = query_clock(ExactClockDomain::Memory,
                                     &live.memory_clock_mhz,
                                     "nvmlDeviceGetClockInfo(MEM)")) {
            return *error;
        }
        if ((status = nvml.enforced_power_limit_mw(
                 nvml_device, &live.power_limit_mw)) != nvml.success_status) {
            return nvml_failure("nvmlDeviceGetEnforcedPowerLimit", status);
        }
        if (live.power_limit_mw == 0) {
            return failure(ExactEnvironmentError::InvalidPowerLimit,
                           "nvmlDeviceGetEnforcedPowerLimit");
        }
        if ((status = nvml.temperature_c(nvml_device,
                                         &live.temperature_c)) !=
                nvml.success_status ||
            live.temperature_c == 0) {
            return failure(ExactEnvironmentError::TemperatureQueryFailed,
                           "nvmlDeviceGetTemperature", status);
        }

        ExactComputeMode compute_mode = ExactComputeMode::Other;
        if ((status = nvml.compute_mode(nvml_device, &compute_mode)) !=
            nvml.success_status) {
            return nvml_failure("nvmlDeviceGetComputeMode", status);
        }
        std::vector<std::uint32_t> processes;
        if ((status = nvml.compute_processes(nvml_device, &processes)) !=
            nvml.success_status) {
            return nvml_failure("nvmlDeviceGetComputeRunningProcesses_v3",
                                status);
        }

        std::uint32_t second_sm_clock = 0;
        std::uint32_t second_memory_clock = 0;
        std::uint32_t second_temperature = 0;
        if (auto error = query_clock(ExactClockDomain::Sm, &second_sm_clock,
                                     "nvmlDeviceGetClockInfo(SM)-verify")) {
            return *error;
        }
        if (auto error = query_clock(
                ExactClockDomain::Memory, &second_memory_clock,
                "nvmlDeviceGetClockInfo(MEM)-verify")) {
            return *error;
        }
        if ((status = nvml.temperature_c(nvml_device,
                                         &second_temperature)) !=
                nvml.success_status ||
            second_temperature == 0) {
            return failure(ExactEnvironmentError::TemperatureQueryFailed,
                           "nvmlDeviceGetTemperature-verify", status);
        }
        if (second_sm_clock != live.sm_clock_mhz ||
            second_memory_clock != live.memory_clock_mhz ||
            second_temperature != live.temperature_c) {
            return failure(ExactEnvironmentError::SnapshotChanged,
                           "clock_temperature_stability");
        }

        if (processes.size() != 1 || processes.front() != current_pid) {
            return failure(
                ExactEnvironmentError::ExclusiveProcessViolation,
                compute_mode == ExactComputeMode::ExclusiveProcess
                    ? "exclusive_process_contract"
                    : "no_competing_process_contract");
        }
        live.current_process_is_exclusive = true;
        live.captured_unix_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::system_clock::now().time_since_epoch())
                .count());
        if (live.captured_unix_ns == 0) {
            return failure(ExactEnvironmentError::NvmlQueryFailed,
                           "snapshot_timestamp");
        }
        return {.environment = std::move(live),
                .error = ExactEnvironmentError::None,
                .operation = "complete",
                .native_status = 0};
    } catch (...) {
        return failure(ExactEnvironmentError::InvalidApi,
                       "collector_exception");
    }
}

#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
namespace {

template <class Function>
Function resolve(void* library, const char* name)
{
    return reinterpret_cast<Function>(dlsym(library, name));
}

class NvmlLibrary {
  public:
    NvmlLibrary()
        : handle_(dlopen("libnvidia-ml.so.1", RTLD_NOW | RTLD_LOCAL))
    {
    }

    ~NvmlLibrary()
    {
        if (initialized_ && shutdown_ != nullptr) {
            shutdown_();
        }
        if (handle_ != nullptr) {
            dlclose(handle_);
        }
    }

    NvmlLibrary(const NvmlLibrary&) = delete;
    NvmlLibrary& operator=(const NvmlLibrary&) = delete;

    bool load()
    {
        if (handle_ == nullptr) {
            return false;
        }
        init_ = resolve<decltype(init_)>(handle_, "nvmlInit_v2");
        shutdown_ = resolve<decltype(shutdown_)>(handle_, "nvmlShutdown");
        handle_by_pci_ = resolve<decltype(handle_by_pci_)>(
            handle_, "nvmlDeviceGetHandleByPciBusId_v2");
        uuid_ = resolve<decltype(uuid_)>(handle_, "nvmlDeviceGetUUID");
        pci_ = resolve<decltype(pci_)>(handle_, "nvmlDeviceGetPciInfo_v3");
        clock_ =
            resolve<decltype(clock_)>(handle_, "nvmlDeviceGetClockInfo");
        power_ = resolve<decltype(power_)>(
            handle_, "nvmlDeviceGetEnforcedPowerLimit");
        temperature_ = resolve<decltype(temperature_)>(
            handle_, "nvmlDeviceGetTemperature");
        compute_mode_ = resolve<decltype(compute_mode_)>(
            handle_, "nvmlDeviceGetComputeMode");
        processes_ = resolve<decltype(processes_)>(
            handle_, "nvmlDeviceGetComputeRunningProcesses_v3");
        if (init_ == nullptr || shutdown_ == nullptr ||
            handle_by_pci_ == nullptr || uuid_ == nullptr || pci_ == nullptr ||
            clock_ == nullptr || power_ == nullptr || temperature_ == nullptr ||
            compute_mode_ == nullptr || processes_ == nullptr) {
            return false;
        }
        const auto status = init_();
        initialized_ = status == NVML_SUCCESS;
        init_status_ = status;
        return initialized_;
    }

    int init_status() const
    {
        return static_cast<int>(init_status_);
    }

    ExactNvmlApi api()
    {
        ExactNvmlApi result;
        result.available = initialized_;
        result.success_status = NVML_SUCCESS;
        result.not_supported_status = NVML_ERROR_NOT_SUPPORTED;
        result.device_by_pci_bus_id = [this](std::string_view bus,
                                             std::uintptr_t* output) {
            const std::string name(bus);
            nvmlDevice_t device = nullptr;
            const auto status = handle_by_pci_(name.c_str(), &device);
            *output = reinterpret_cast<std::uintptr_t>(device);
            return static_cast<int>(status);
        };
        result.device_uuid = [this](std::uintptr_t input,
                                    std::string* output) {
            std::array<char, NVML_DEVICE_UUID_V2_BUFFER_SIZE> value{};
            const auto status = uuid_(reinterpret_cast<nvmlDevice_t>(input),
                                      value.data(), value.size());
            if (status == NVML_SUCCESS) {
                *output = value.data();
            }
            return static_cast<int>(status);
        };
        result.pci_identity = [this](std::uintptr_t input,
                                     ExactNvmlPciIdentity* output) {
            nvmlPciInfo_t value{};
            const auto status =
                pci_(reinterpret_cast<nvmlDevice_t>(input), &value);
            if (status == NVML_SUCCESS) {
                output->bus_id = value.busId;
                output->vendor_id = value.pciDeviceId & 0xffffU;
                output->device_id = value.pciDeviceId >> 16U;
            }
            return static_cast<int>(status);
        };
        result.clock_mhz = [this](std::uintptr_t input,
                                  ExactClockDomain domain,
                                  std::uint32_t* output) {
            const auto type = domain == ExactClockDomain::Sm ? NVML_CLOCK_SM
                                                             : NVML_CLOCK_MEM;
            return static_cast<int>(clock_(
                reinterpret_cast<nvmlDevice_t>(input), type, output));
        };
        result.enforced_power_limit_mw =
            [this](std::uintptr_t input, std::uint32_t* output) {
                return static_cast<int>(power_(
                    reinterpret_cast<nvmlDevice_t>(input), output));
            };
        result.temperature_c =
            [this](std::uintptr_t input, std::uint32_t* output) {
                return static_cast<int>(temperature_(
                    reinterpret_cast<nvmlDevice_t>(input),
                    NVML_TEMPERATURE_GPU, output));
            };
        result.compute_mode = [this](std::uintptr_t input,
                                     ExactComputeMode* output) {
            nvmlComputeMode_t mode = NVML_COMPUTEMODE_DEFAULT;
            const auto status = compute_mode_(
                reinterpret_cast<nvmlDevice_t>(input), &mode);
            if (status == NVML_SUCCESS) {
                *output = mode == NVML_COMPUTEMODE_EXCLUSIVE_PROCESS
                              ? ExactComputeMode::ExclusiveProcess
                              : (mode == NVML_COMPUTEMODE_DEFAULT
                                     ? ExactComputeMode::Default
                                     : ExactComputeMode::Other);
            }
            return static_cast<int>(status);
        };
        result.compute_processes =
            [this](std::uintptr_t input,
                   std::vector<std::uint32_t>* output) {
                auto device = reinterpret_cast<nvmlDevice_t>(input);
                unsigned int count = 0;
                auto status = processes_(device, &count, nullptr);
                if (status == NVML_SUCCESS && count == 0) {
                    output->clear();
                    return static_cast<int>(status);
                }
                if (status != NVML_ERROR_INSUFFICIENT_SIZE || count == 0 ||
                    count > 4096) {
                    return static_cast<int>(status);
                }
                std::vector<nvmlProcessInfo_t> values(count);
                status = processes_(device, &count, values.data());
                if (status != NVML_SUCCESS) {
                    return static_cast<int>(status);
                }
                output->clear();
                output->reserve(count);
                for (unsigned int index = 0; index < count; ++index) {
                    output->push_back(values[index].pid);
                }
                return static_cast<int>(status);
            };
        return result;
    }

  private:
    using TemperatureFn = nvmlReturn_t (*)(
        nvmlDevice_t, nvmlTemperatureSensors_t, unsigned int*);

    void* handle_{nullptr};
    nvmlReturn_t (*init_)() = nullptr;
    nvmlReturn_t (*shutdown_)() = nullptr;
    nvmlReturn_t (*handle_by_pci_)(const char*, nvmlDevice_t*) = nullptr;
    nvmlReturn_t (*uuid_)(nvmlDevice_t, char*, unsigned int) = nullptr;
    nvmlReturn_t (*pci_)(nvmlDevice_t, nvmlPciInfo_t*) = nullptr;
    nvmlReturn_t (*clock_)(nvmlDevice_t, nvmlClockType_t, unsigned int*) =
        nullptr;
    nvmlReturn_t (*power_)(nvmlDevice_t, unsigned int*) = nullptr;
    TemperatureFn temperature_ = nullptr;
    nvmlReturn_t (*compute_mode_)(nvmlDevice_t, nvmlComputeMode_t*) = nullptr;
    nvmlReturn_t (*processes_)(nvmlDevice_t, unsigned int*,
                               nvmlProcessInfo_t*) = nullptr;
    bool initialized_{false};
    nvmlReturn_t init_status_{NVML_ERROR_UNINITIALIZED};
};

ExactCudaDriverApi live_cuda_api()
{
    return {
        .current_context = [](std::uintptr_t* output) {
            CUcontext context = nullptr;
            const auto status = cuCtxGetCurrent(&context);
            *output = reinterpret_cast<std::uintptr_t>(context);
            return static_cast<int>(status);
        },
        .current_device = [](std::int32_t* output) {
            CUdevice device = 0;
            const auto status = cuCtxGetDevice(&device);
            *output = device;
            return static_cast<int>(status);
        },
        .device_name = [](std::int32_t device, std::string* output) {
            std::array<char, 256> value{};
            const auto status =
                cuDeviceGetName(value.data(), value.size(), device);
            if (status == CUDA_SUCCESS) {
                *output = value.data();
            }
            return static_cast<int>(status);
        },
        .device_uuid = [](std::int32_t device,
                          std::array<std::uint8_t, 16>* output) {
            CUuuid value{};
            const auto status = cuDeviceGetUuid(&value, device);
            if (status == CUDA_SUCCESS) {
                std::memcpy(output->data(), value.bytes, output->size());
            }
            return static_cast<int>(status);
        },
        .pci_bus_id = [](std::int32_t device, std::string* output) {
            std::array<char, 32> value{};
            const auto status =
                cuDeviceGetPCIBusId(value.data(), value.size(), device);
            if (status == CUDA_SUCCESS) {
                *output = value.data();
            }
            return static_cast<int>(status);
        },
        .device_attribute = [](std::int32_t device,
                               ExactCudaAttribute attribute,
                               std::int32_t* output) {
            CUdevice_attribute cuda_attribute{};
            switch (attribute) {
            case ExactCudaAttribute::CombinedPciDeviceId:
                cuda_attribute = CU_DEVICE_ATTRIBUTE_GPU_PCI_DEVICE_ID;
                break;
            case ExactCudaAttribute::ComputeCapabilityMajor:
                cuda_attribute =
                    CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR;
                break;
            case ExactCudaAttribute::ComputeCapabilityMinor:
                cuda_attribute =
                    CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR;
                break;
            }
            int value = 0;
            const auto status =
                cuDeviceGetAttribute(&value, cuda_attribute, device);
            *output = value;
            return static_cast<int>(status);
        },
        .driver_version = [](std::int32_t* output) {
            int value = 0;
            const auto status = cuDriverGetVersion(&value);
            *output = value;
            return static_cast<int>(status);
        },
    };
}

}  // namespace
#endif

ExactEnvironmentResult
collect_live_exact_environment(std::uint32_t current_pid) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    NvmlLibrary library;
    if (!library.load()) {
        return failure(ExactEnvironmentError::NvmlUnavailable,
                       "libnvidia-ml.so.1", library.init_status());
    }
    return collect_exact_environment(live_cuda_api(), library.api(),
                                     current_pid);
#else
    (void)current_pid;
    return failure(ExactEnvironmentError::CudaUnavailable,
                   "cuda_runtime_disabled");
#endif
}

}  // namespace hbfsim
