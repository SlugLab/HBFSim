#pragma once

#include "hbm_cache.hpp"
#include "vmm.hpp"

#include "../host_service/capacity_backing_router.hpp"
#include "../host_service/capacity_page_service.hpp"
#include "../host_service/capacity_worker.hpp"
#include "../host_service/control_layout.hpp"

#include <hbfsim/profile.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>

namespace hbfsim::runtime {

// logical_range borrows the CapacityRuntime-owned VmmDriver. A Task 4 context
// must therefore declare/construct its CapacityRuntime before CapacityMapping
// objects so mappings are destroyed first.
struct CapacityMapping {
    std::uint32_t range_id{0};
    std::uint64_t first_page{0};
    std::uint64_t page_count{0};
    std::shared_ptr<host_service::BackingStore> backing;
    VmmRange logical_range;
    host_service::CapacityBackingRouter::Token router_token{0};
    bool active{false};
};

class CapacityRuntime {
  public:
    static std::unique_ptr<CapacityRuntime> create(
        const Profile& profile, host_service::ControlView control,
        std::uintptr_t cuda_context, int device_ordinal);

    ~CapacityRuntime();
    CapacityRuntime(const CapacityRuntime&) = delete;
    CapacityRuntime& operator=(const CapacityRuntime&) = delete;

    host_service::CapacityBackingRouter& router() noexcept { return router_; }
    VmmDriver& vmm() noexcept { return driver_; }
    RequestStatus flush(
        host_service::CapacityPageService::ModelProgram model_program,
        std::optional<std::uint32_t> range_id = std::nullopt);
    void stop();

  private:
    class PinnedPage {
      public:
        PinnedPage() = default;
        ~PinnedPage();
        PinnedPage(const PinnedPage&) = delete;
        PinnedPage& operator=(const PinnedPage&) = delete;
        PinnedPage(PinnedPage&& other) noexcept;
        PinnedPage& operator=(PinnedPage&& other) noexcept;

        static PinnedPage allocate(std::size_t bytes);
        [[nodiscard]] std::byte* data() noexcept { return data_; }

      private:
        void reset() noexcept;
        std::byte* data_{nullptr};
        std::size_t bytes_{0};
    };

    CapacityRuntime(const Profile& profile,
                    host_service::ControlView control,
                    std::uintptr_t cuda_context, int device_ordinal);

    static bool start_worker(void* opaque) noexcept;
    static void stop_worker(void* opaque) noexcept;
    bool host_to_frame(std::uint64_t frame,
                       std::span<const std::byte> bytes) noexcept;
    bool frame_to_host(std::uint64_t frame,
                       std::span<std::byte> bytes) noexcept;

    std::size_t page_bytes_;
    std::uintptr_t cuda_context_;
    int device_ordinal_;
    CudaVmmDriver driver_;
    VmmFramePool vmm_;
    HbmCache cache_;
    host_service::CapacityBackingRouter router_;
    PinnedPage bounce_;
    host_service::CapacityPageService service_;
    host_service::CapacityWorker worker_;
};

}  // namespace hbfsim::runtime
