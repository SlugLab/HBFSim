#include "../../src/cuda_runtime/capacity_runtime.hpp"

#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/control_layout.hpp"

#include <hbfsim/profile.hpp>

#include <cuda.h>
#include <cuda_runtime_api.h>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <unistd.h>

namespace {

constexpr std::uint32_t kPageBytes = 16 * 1024;

__global__ void capacity_sentinel(std::uint8_t* page)
{
    if (blockIdx.x == 0 && threadIdx.x == 0) {
        page[0] = 0xa5;
        page[kPageBytes - 1] = 0x5a;
    }
}

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

hbfsim::Profile tiny_profile()
{
    return {
        .name = "capacity-runtime-live-smoke",
        .capacity_bytes = 2 * kPageBytes,
        .page_bytes = kPageBytes,
        .read_latency_ns = 1,
        .program_latency_ns = 1,
        .channels = 1,
        .dies_per_channel = 1,
        .planes_per_die = 1,
        .pages_per_block = 1,
        .channel_width_bits = 8,
        .channel_transfer_rate_mtps = 1,
        .queue_depth = 1,
        .aggregate_bandwidth_bytes_per_s = 1,
        .hbm_cache_bytes = 2 * kPageBytes,
        .reference_sample_rate = 0.0,
        .reference_warmup_requests = 0,
        .time_scale = 1,
        .timing_tolerance_ns = 0,
        .page_read_latency_lsb_ns = 1,
        .page_read_latency_csb_ns = 1,
        .page_read_latency_msb_ns = 1,
        .page_read_latency_tsb_ns = 1,
        .page_program_latency_lsb_ns = 1,
        .page_program_latency_csb_ns = 1,
        .page_program_latency_msb_ns = 1,
        .page_program_latency_tsb_ns = 1,
    };
}

hbfsim::HbfRequest request(std::uint64_t ticket, std::uint64_t id,
                           std::uint32_t operation)
{
    return {
        .request_id = id,
        .sequence = ticket,
        .logical_address = 0,
        .bytes = kPageBytes,
        .range_id = 1,
        .operation = operation,
    };
}

bool wait_for_result(
    hbfsim::host_service::ControlView control,
    const hbfsim::HbfRequest& pending,
    hbfsim::host_service::CapacityHandoffResult& result)
{
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(2);
    while (std::chrono::steady_clock::now() < deadline) {
        if (control.capacity_handoff_result(pending, result)) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    return false;
}

class ContextOwner {
  public:
    ~ContextOwner()
    {
        if (context != nullptr) {
            (void)::cuCtxSetCurrent(nullptr);
            (void)::cuDevicePrimaryCtxRelease(device);
        }
    }
    CUcontext context{nullptr};
    CUdevice device{0};
};

}  // namespace

int main()
{
    const char* phase = "CUDA initialization";
    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-capacity-runtime-live-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);
    try {
        require(::cuInit(0) == CUDA_SUCCESS, "cuInit failed");
        CUdevice device = 0;
        require(::cuDeviceGet(&device, 0) == CUDA_SUCCESS,
                "cuDeviceGet failed");
        ContextOwner context;
        context.device = device;
        require(::cuDevicePrimaryCtxRetain(&context.context, device) ==
                    CUDA_SUCCESS,
                "cuDevicePrimaryCtxRetain failed");
        require(::cuCtxSetCurrent(context.context) == CUDA_SUCCESS,
                "cuCtxSetCurrent failed");

        phase = "capacity runtime construction";
        constexpr std::uint32_t slots = 2;
        std::vector<std::byte> control_memory(
            hbfsim::host_service::control_region_bytes(slots));
        hbfsim::host_service::ControlView control(control_memory.data(),
                                                   control_memory.size());
        require(control.initialize(slots), "control initialization failed");
        auto runtime = hbfsim::runtime::CapacityRuntime::create(
            tiny_profile(), control,
            reinterpret_cast<std::uintptr_t>(context.context), device);
        require(runtime != nullptr, "CapacityRuntime::create failed");
        std::fprintf(stderr,
                     "capacity runtime logical cache cap: %u bytes in two "
                     "%u-byte frames\n",
                     2 * kPageBytes, kPageBytes);

        phase = "backing publication";
        auto backing = std::make_shared<hbfsim::host_service::BackingStore>(
            hbfsim::host_service::BackingStore::create_deterministic(
                path, 2 * kPageBytes, 0x2468ace0));
        auto expected = backing->read_page(0, kPageBytes);
        const auto token = runtime->router().stage(1, 0, 2, true, backing);
        require(token != 0 && runtime->router().activate(token),
                "backing publication failed");

        phase = "clean-miss copy";
        auto read = request(0, 1, 0);
        require(control.begin_capacity_handoff(read),
                "clean-miss handoff failed");
        hbfsim::host_service::CapacityHandoffResult result{};
        require(wait_for_result(control, read, result),
                "clean-miss handoff timed out");
        require(result.status == hbfsim::RequestStatus::Ready &&
                    result.frame_address != 0,
                "clean-miss resolution failed");
        require(control.release_capacity_handoff(read),
                "clean-miss handoff release failed");

        phase = "sentinel kernel";
        capacity_sentinel<<<1, 1>>>(
            reinterpret_cast<std::uint8_t*>(result.frame_address));
        require(::cudaGetLastError() == cudaSuccess,
                "sentinel launch failed");
        require(::cudaDeviceSynchronize() == cudaSuccess,
                "sentinel synchronization failed");

        phase = "dirty-page mark";
        auto write = request(1, 2, 1);
        require(control.begin_capacity_handoff(write),
                "dirty handoff failed");
        require(wait_for_result(control, write, result),
                "dirty handoff timed out");
        require(result.status == hbfsim::RequestStatus::Ready,
                "dirty-page mark failed");
        require(control.release_capacity_handoff(write),
                "dirty handoff release failed");

        phase = "dirty-page flush";
        require(runtime->flush(
                    [](std::uint32_t range, std::uint64_t page) {
                        return range == 1 && page == 0
                                   ? hbfsim::RequestStatus::Ready
                                   : hbfsim::RequestStatus::IoError;
                    },
                    1) == hbfsim::RequestStatus::Ready,
                "dirty flush failed");
        expected[0] = std::byte{0xa5};
        expected[kPageBytes - 1] = std::byte{0x5a};
        require(backing->read_page(0, kPageBytes) == expected,
                "flushed backing bytes differ");

        phase = "clean shutdown";
        runtime->stop();
        require(runtime->router().deactivate(1) ==
                    hbfsim::RequestStatus::Ready,
                "backing deactivation failed");
        runtime.reset();
        backing.reset();
        std::filesystem::remove(path);
        std::fprintf(stderr, "capacity runtime live smoke passed\n");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "capacity runtime live smoke failed in %s: %s\n",
                     phase, error.what());
        std::filesystem::remove(path);
        return 1;
    }
}
