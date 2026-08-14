#include <hbfsim/exact_environment.hpp>

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unistd.h>
#include <vector>

namespace {

void require(bool condition, std::string_view message)
{
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

#define CHECK(expression) require(static_cast<bool>(expression), #expression)

constexpr std::array<std::uint8_t, 16> kUuid{
    0xf0, 0x7e, 0xa2, 0xdf, 0x1b, 0x6f, 0x9a, 0x02,
    0xb5, 0x34, 0x50, 0x90, 0xab, 0xf3, 0xc1, 0x74};

struct Fixture {
    Fixture()
    {
        driver.current_context = [](std::uintptr_t* value) {
            *value = 0x1234;
            return 0;
        };
        driver.current_device = [](std::int32_t* value) {
            *value = 0;
            return 0;
        };
        driver.device_name = [](std::int32_t, std::string* value) {
            *value = "NVIDIA RTX PRO 6000 Blackwell Server Edition";
            return 0;
        };
        driver.device_uuid = [](std::int32_t,
                                std::array<std::uint8_t, 16>* value) {
            *value = kUuid;
            return 0;
        };
        driver.pci_bus_id = [](std::int32_t, std::string* value) {
            *value = "0000:8a:00.0";
            return 0;
        };
        driver.device_attribute = [](std::int32_t,
                                     hbfsim::ExactCudaAttribute attribute,
                                     std::int32_t* value) {
            switch (attribute) {
            case hbfsim::ExactCudaAttribute::CombinedPciDeviceId:
                *value = static_cast<std::int32_t>(0x2bb510deU);
                break;
            case hbfsim::ExactCudaAttribute::ComputeCapabilityMajor:
                *value = 12;
                break;
            case hbfsim::ExactCudaAttribute::ComputeCapabilityMinor:
                *value = 0;
                break;
            }
            return 0;
        };
        driver.driver_version = [](std::int32_t* value) {
            *value = 13000;
            return 0;
        };

        nvml.available = true;
        nvml.success_status = 0;
        nvml.not_supported_status = 3;
        nvml.device_by_pci_bus_id = [](std::string_view,
                                       std::uintptr_t* value) {
            *value = 0x5678;
            return 0;
        };
        nvml.device_uuid = [](std::uintptr_t, std::string* value) {
            *value = "GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174";
            return 0;
        };
        nvml.pci_identity = [](std::uintptr_t,
                               hbfsim::ExactNvmlPciIdentity* value) {
            *value = {.bus_id = "00000000:8A:00.0",
                      .vendor_id = 0x10de,
                      .device_id = 0x2bb5};
            return 0;
        };
        nvml.clock_mhz = [](std::uintptr_t, hbfsim::ExactClockDomain domain,
                            std::uint32_t* value) {
            *value = domain == hbfsim::ExactClockDomain::Sm ? 1830 : 14001;
            return 0;
        };
        nvml.enforced_power_limit_mw = [](std::uintptr_t,
                                          std::uint32_t* value) {
            *value = 600000;
            return 0;
        };
        nvml.temperature_c = [](std::uintptr_t, std::uint32_t* value) {
            *value = 42;
            return 0;
        };
        nvml.compute_mode = [](std::uintptr_t,
                               hbfsim::ExactComputeMode* value) {
            *value = hbfsim::ExactComputeMode::ExclusiveProcess;
            return 0;
        };
        nvml.compute_processes = [this](std::uintptr_t,
                                        std::vector<std::uint32_t>* value) {
            *value = {pid};
            return 0;
        };
    }

    std::uint32_t pid = static_cast<std::uint32_t>(getpid());
    hbfsim::ExactCudaDriverApi driver;
    hbfsim::ExactNvmlApi nvml;
};

void expect_error(const Fixture& fixture, hbfsim::ExactEnvironmentError error)
{
    const auto result = hbfsim::collect_exact_environment(
        fixture.driver, fixture.nvml, fixture.pid);
    CHECK(!result.environment.has_value());
    CHECK(result.error == error);
    CHECK(!result.operation.empty());
}

}  // namespace

int main()
{
    {
        Fixture fixture;
        const auto result = hbfsim::collect_exact_environment(
            fixture.driver, fixture.nvml, fixture.pid);
        CHECK(result.error == hbfsim::ExactEnvironmentError::None);
        CHECK(result.environment.has_value());
        const auto& live = *result.environment;
        CHECK(live.gpu_name ==
              "NVIDIA RTX PRO 6000 Blackwell Server Edition");
        CHECK(live.gpu_uuid ==
              "GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174");
        CHECK(live.pci_bus_id == "0000:8a:00.0");
        CHECK(live.pci_vendor_id == 0x10de);
        CHECK(live.pci_device_id == 0x2bb5);
        CHECK(live.compute_capability_major == 12);
        CHECK(live.compute_capability_minor == 0);
        CHECK(live.cuda_driver_version == 13000);
        CHECK(live.sm_clock_mhz == 1830);
        CHECK(live.memory_clock_mhz == 14001);
        CHECK(live.power_limit_mw == 600000);
        CHECK(live.temperature_c == 42);
        CHECK(live.current_process_is_exclusive);
        CHECK(live.captured_unix_ns != 0);
    }
    {
        Fixture fixture;
        fixture.nvml.compute_mode = [](std::uintptr_t,
                                       hbfsim::ExactComputeMode* value) {
            *value = hbfsim::ExactComputeMode::Default;
            return 0;
        };
        const auto result = hbfsim::collect_exact_environment(
            fixture.driver, fixture.nvml, fixture.pid);
        CHECK(result.error == hbfsim::ExactEnvironmentError::None);
        CHECK(result.environment.has_value());
        CHECK(result.environment->current_process_is_exclusive);
    }
    {
        Fixture fixture;
        fixture.nvml.compute_mode = [](std::uintptr_t,
                                       hbfsim::ExactComputeMode* value) {
            *value = hbfsim::ExactComputeMode::Default;
            return 0;
        };
        fixture.nvml.compute_processes =
            [&fixture](std::uintptr_t, std::vector<std::uint32_t>* value) {
                *value = {fixture.pid, fixture.pid + 1};
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::ExclusiveProcessViolation);
    }
    {
        Fixture fixture;
        fixture.driver.current_context = [](std::uintptr_t* value) {
            *value = 0;
            return 0;
        };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::NoCurrentCudaContext);
    }
    {
        Fixture fixture;
        const auto base = fixture.driver.device_attribute;
        fixture.driver.device_attribute =
            [base](std::int32_t device, hbfsim::ExactCudaAttribute attribute,
                   std::int32_t* value) {
                const auto status = base(device, attribute, value);
                if (attribute ==
                    hbfsim::ExactCudaAttribute::ComputeCapabilityMajor) {
                    *value = 11;
                }
                return status;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::UnsupportedComputeCapability);
    }
    {
        Fixture fixture;
        fixture.driver.device_attribute =
            [](std::int32_t, hbfsim::ExactCudaAttribute attribute,
               std::int32_t* value) {
                *value = attribute ==
                                 hbfsim::ExactCudaAttribute::CombinedPciDeviceId
                             ? static_cast<std::int32_t>(0x2bb51234U)
                             : (attribute == hbfsim::ExactCudaAttribute::
                                               ComputeCapabilityMajor
                                    ? 12
                                    : 0);
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::InvalidPciIdentity);
    }
    {
        Fixture fixture;
        fixture.nvml.available = false;
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::NvmlUnavailable);
    }
    {
        Fixture fixture;
        fixture.nvml.device_uuid = [](std::uintptr_t, std::string* value) {
            *value = "GPU-00000000-0000-0000-0000-000000000000";
            return 0;
        };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::DeviceIdentityMismatch);
    }
    {
        Fixture fixture;
        fixture.nvml.pci_identity =
            [](std::uintptr_t, hbfsim::ExactNvmlPciIdentity* value) {
                *value = {.bus_id = "00000000:8B:00.0",
                          .vendor_id = 0x10de,
                          .device_id = 0x2bb5};
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::DeviceIdentityMismatch);
    }
    {
        Fixture fixture;
        fixture.nvml.clock_mhz = [](std::uintptr_t,
                                    hbfsim::ExactClockDomain,
                                    std::uint32_t*) { return 3; };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::ClockQueryUnsupported);
    }
    {
        Fixture fixture;
        fixture.nvml.enforced_power_limit_mw =
            [](std::uintptr_t, std::uint32_t* value) {
                *value = 0;
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::InvalidPowerLimit);
    }
    {
        Fixture fixture;
        fixture.nvml.temperature_c =
            [](std::uintptr_t, std::uint32_t*) { return 15; };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::TemperatureQueryFailed);
    }
    {
        Fixture fixture;
        fixture.nvml.compute_processes =
            [&fixture](std::uintptr_t, std::vector<std::uint32_t>* value) {
                *value = {fixture.pid, fixture.pid + 1};
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::ExclusiveProcessViolation);
    }
    {
        Fixture fixture;
        std::uint32_t calls = 0;
        fixture.nvml.clock_mhz =
            [&calls](std::uintptr_t, hbfsim::ExactClockDomain domain,
                     std::uint32_t* value) {
                *value = domain == hbfsim::ExactClockDomain::Sm
                             ? 1830 + calls++
                             : 14001;
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::SnapshotChanged);
    }
    {
        Fixture fixture;
        std::uint32_t calls = 0;
        fixture.nvml.temperature_c =
            [&calls](std::uintptr_t, std::uint32_t* value) {
                *value = 42 + calls++;
                return 0;
            };
        expect_error(fixture,
                     hbfsim::ExactEnvironmentError::SnapshotChanged);
    }
    return 0;
}
