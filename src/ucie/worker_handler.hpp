#pragma once
#include <cstdint>
#include <chrono>
#include <filesystem>
#include <memory>
#include <string>

namespace hbfsim::ucie {
// One sequenced command state machine for process and in-daemon endpoints.
class WorkerCommandState {
public:
    WorkerCommandState(const std::filesystem::path& profile,bool top_profile,
                       std::uint32_t stack_id,bool shm_available,
                       bool local_transport);
    ~WorkerCommandState();
    WorkerCommandState(const WorkerCommandState&)=delete;
    WorkerCommandState& operator=(const WorkerCommandState&)=delete;
    std::string handle(std::string frame);
    bool typed_active() const noexcept;
    bool shm_active() const noexcept;
    bool stopped() const noexcept;
    int exit_code() const noexcept;
    std::chrono::milliseconds reply_timeout() const noexcept;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace hbfsim::ucie
