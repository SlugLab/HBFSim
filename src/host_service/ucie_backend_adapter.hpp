#pragma once

#include "control_layout.hpp"

#include <filesystem>
#include <memory>

namespace hbfsim::host_service {

enum class UcieWaitMode { Nominal, ZeroInjected };

struct UcieBackendOptions {
    std::filesystem::path top_profile;
    std::filesystem::path worker_executable;
    std::filesystem::path placement_manifest;
    UcieWaitMode wait_mode{UcieWaitMode::Nominal};
};

// Host-only bridge from the existing page HbfRequest ring to the independent
// Stage3 UCIe frontend. It does not change the device helper or shared ABI.
class UcieBackendAdapter {
public:
    UcieBackendAdapter(ControlView control, UcieBackendOptions options);
    ~UcieBackendAdapter();
    UcieBackendAdapter(const UcieBackendAdapter&) = delete;
    UcieBackendAdapter& operator=(const UcieBackendAdapter&) = delete;
    bool poll_once();
    void write_report(const std::filesystem::path& path) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace hbfsim::host_service
