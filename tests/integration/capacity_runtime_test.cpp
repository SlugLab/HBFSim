#include "../../src/cuda_runtime/capacity_runtime.hpp"

#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/control_layout.hpp"

#include <hbfsim/profile.hpp>

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>
#include <unistd.h>

extern "C" {
void fakeCudaCapacityReset();
void fakeCudaCapacityFail(const char* operation, int call);
void fakeCudaSetCurrentDomain(std::uintptr_t context, int device);
std::size_t fakeCudaCapacityLiveReservations();
std::size_t fakeCudaCapacityLiveHandles();
std::size_t fakeCudaCapacityLivePinnedBuffers();
std::size_t fakeCudaCapacityLiveWorkerContexts();
std::size_t fakeCudaCapacityExplicitContextClears();
std::size_t fakeCudaCapacityReservedBytes();
std::size_t fakeCudaCapacityPinnedBytes();
std::size_t fakeCudaCapacityHtoDCalls();
std::size_t fakeCudaCapacityDtoHCalls();
std::uintptr_t fakeCudaCapacityLastContext();
int fakeCudaCapacityLastDevice();
int fakeCudaCapacityReadDevice(std::uintptr_t address, void* bytes,
                               std::size_t size);
int fakeCudaCapacityWriteDevice(std::uintptr_t address, const void* bytes,
                                std::size_t size);
std::size_t fakeCudaCapacityEventCount();
int fakeCudaCapacityEvent(std::size_t index);
}

namespace {

static_assert(std::is_same_v<
              decltype(std::declval<hbfsim::runtime::CapacityRuntime&>().vmm()),
              hbfsim::runtime::VmmDriver&>);
static_assert(
    std::is_default_constructible_v<hbfsim::runtime::CapacityMapping>);
static_assert(std::is_move_constructible_v<hbfsim::runtime::CapacityMapping>);
static_assert(
    !std::is_copy_constructible_v<hbfsim::runtime::CapacityMapping>);

constexpr std::uint32_t kPageBytes = 16 * 1024;
constexpr std::uint64_t kCacheBytes = 32 * 1024;
constexpr std::uintptr_t kContext = 0xCA00;
constexpr int kDevice = 3;

enum FakeEvent {
    ContextSet = 1,
    Granularity = 2,
    Reserve = 3,
    Create = 4,
    Map = 5,
    SetAccess = 6,
    HostAlloc = 7,
    HtoD = 8,
    DtoH = 9,
    WorkerContextExit = 10,
    HostFree = 11,
    Unmap = 12,
    Release = 13,
    AddressFree = 14,
};

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity runtime CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

hbfsim::Profile tiny_profile()
{
    return {
        .name = "capacity-runtime-test",
        .capacity_bytes = 8ULL * 1024 * 1024 * 1024,
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
        .hbm_cache_bytes = kCacheBytes,
        .reference_sample_rate = 0.0,
        .reference_warmup_requests = 0,
        .time_scale = 1,
        .timing_tolerance_ns = 0,
    };
}

hbfsim::HbfRequest request(std::uint64_t ticket, std::uint64_t id,
                           std::uint64_t page, std::uint32_t operation)
{
    return {
        .request_id = id,
        .sequence = ticket,
        .logical_address = page * kPageBytes,
        .bytes = kPageBytes,
        .range_id = 9,
        .operation = operation,
    };
}

bool wait_for_result(
    hbfsim::host_service::ControlView control,
    const hbfsim::HbfRequest& pending,
    hbfsim::host_service::CapacityHandoffResult& result)
{
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(1);
    while (std::chrono::steady_clock::now() < deadline) {
        if (control.capacity_handoff_result(pending, result)) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    return false;
}

void check_no_live_resources()
{
    CHECK(fakeCudaCapacityLiveReservations() == 0);
    CHECK(fakeCudaCapacityLiveHandles() == 0);
    CHECK(fakeCudaCapacityLivePinnedBuffers() == 0);
    CHECK(fakeCudaCapacityLiveWorkerContexts() == 0);
}

}  // namespace

int main()
{
    hbfsim::runtime::CapacityMapping empty_mapping;
    CHECK(empty_mapping.range_id == 0);
    CHECK(empty_mapping.first_page == 0);
    CHECK(empty_mapping.page_count == 0);
    CHECK(empty_mapping.backing == nullptr);
    CHECK(empty_mapping.logical_range.base() == 0);
    CHECK(empty_mapping.router_token == 0);
    CHECK(!empty_mapping.active);
    hbfsim::runtime::CapacityMapping moved_mapping(std::move(empty_mapping));
    CHECK(moved_mapping.logical_range.base() == 0);

    constexpr std::uint32_t slots = 4;
    std::vector<std::byte> control_memory(
        hbfsim::host_service::control_region_bytes(slots));
    hbfsim::host_service::ControlView control(control_memory.data(),
                                               control_memory.size());
    CHECK(control.initialize(slots));

    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-capacity-runtime-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);

    fakeCudaCapacityReset();
    {
        auto runtime = hbfsim::runtime::CapacityRuntime::create(
            tiny_profile(), control, kContext, kDevice);
        CHECK(runtime != nullptr);
        CHECK(fakeCudaCapacityLiveReservations() == 1);
        CHECK(fakeCudaCapacityLiveHandles() == 1);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 1);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 1);
        CHECK(fakeCudaCapacityReservedBytes() == kCacheBytes);
        CHECK(fakeCudaCapacityPinnedBytes() == kPageBytes);
        CHECK(fakeCudaCapacityLastContext() == kContext);
        CHECK(fakeCudaCapacityLastDevice() == kDevice);
        CHECK(fakeCudaCapacityEventCount() == 7);
        const std::vector<int> construction_events{
            Granularity, Reserve, Create, Map,
            SetAccess,   HostAlloc, ContextSet,
        };
        for (std::size_t index = 0; index < construction_events.size();
             ++index) {
            CHECK(fakeCudaCapacityEvent(index) == construction_events[index]);
        }

        auto& driver = runtime->vmm();
        CHECK(driver.granularity(kDevice) == kPageBytes);
        const auto wrong_context_address =
            driver.reserve(kPageBytes, kPageBytes);
        CHECK(wrong_context_address != 0);
        const auto wrong_context_handle =
            driver.create(kPageBytes, kDevice);
        CHECK(wrong_context_handle != 0);
        fakeCudaSetCurrentDomain(0xCB00, kDevice + 1);
        CHECK(!driver.map(wrong_context_address, kPageBytes,
                          wrong_context_handle));
        fakeCudaSetCurrentDomain(kContext, kDevice);
        CHECK(driver.release(wrong_context_handle));
        CHECK(driver.free_address(wrong_context_address, kPageBytes));
        CHECK(fakeCudaCapacityLiveReservations() == 1);
        CHECK(fakeCudaCapacityLiveHandles() == 1);

        auto backing = std::make_shared<hbfsim::host_service::BackingStore>(
            hbfsim::host_service::BackingStore::create_deterministic(
                path, 2 * kPageBytes, 0x12345678));
        const auto page_zero = backing->read_page(0, kPageBytes);
        const auto token = runtime->router().stage(9, 0, 2, true, backing);
        CHECK(token != 0);
        CHECK(runtime->router().activate(token));

        auto read = request(0, 10, 0, 0);
        CHECK(control.begin_capacity_handoff(read));
        hbfsim::host_service::CapacityHandoffResult result{};
        CHECK(wait_for_result(control, read, result));
        CHECK(result.status == hbfsim::RequestStatus::Ready);
        std::vector<std::byte> device_page(kPageBytes);
        CHECK(fakeCudaCapacityReadDevice(result.frame_address,
                                         device_page.data(),
                                         device_page.size()) == 0);
        CHECK(device_page == page_zero);
        const auto first_frame = result.frame_address;
        CHECK(fakeCudaCapacityHtoDCalls() == 1);
        CHECK(control.release_capacity_handoff(read));

        auto write = request(1, 11, 1, 1);
        CHECK(control.begin_capacity_handoff(write));
        CHECK(wait_for_result(control, write, result));
        CHECK(result.status == hbfsim::RequestStatus::Ready);
        CHECK(result.frame_address != first_frame);
        const std::vector<std::byte> replacement(kPageBytes,
                                                  std::byte{0x5a});
        CHECK(fakeCudaCapacityWriteDevice(result.frame_address,
                                          replacement.data(),
                                          replacement.size()) == 0);
        CHECK(control.release_capacity_handoff(write));
        std::vector<std::pair<std::uint32_t, std::uint64_t>> programmed;
        CHECK(runtime->flush(
                  [&](std::uint32_t range_id, std::uint64_t page) {
                      programmed.emplace_back(range_id, page);
                      return hbfsim::RequestStatus::Ready;
                  },
                  9) == hbfsim::RequestStatus::Ready);
        CHECK((programmed ==
               std::vector<std::pair<std::uint32_t, std::uint64_t>>{{9, 1}}));
        CHECK(fakeCudaCapacityDtoHCalls() == 1);
        CHECK(backing->read_page(1, kPageBytes) == replacement);
        runtime->stop();
        CHECK(fakeCudaCapacityExplicitContextClears() == 1);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 0);
    }
    check_no_live_resources();
    CHECK(fakeCudaCapacityEventCount() >= 5);
    const auto count = fakeCudaCapacityEventCount();
    CHECK(fakeCudaCapacityEvent(count - 5) == WorkerContextExit);
    CHECK(fakeCudaCapacityEvent(count - 4) == HostFree);
    CHECK(fakeCudaCapacityEvent(count - 3) == Unmap);
    CHECK(fakeCudaCapacityEvent(count - 2) == Release);
    CHECK(fakeCudaCapacityEvent(count - 1) == AddressFree);

    for (const char* operation : {"cuMemGetAllocationGranularity",
                                  "cuMemAddressReserve", "cuMemCreate",
                                  "cuMemMap", "cuMemSetAccess",
                                  "cudaHostAlloc", "cuCtxSetCurrent",
                                  "cuCtxGetCurrent", "cuCtxGetDevice"}) {
        fakeCudaCapacityReset();
        fakeCudaCapacityFail(operation, 1);
        auto rejected = hbfsim::runtime::CapacityRuntime::create(
            tiny_profile(), control, kContext, kDevice);
        CHECK(rejected == nullptr);
        check_no_live_resources();
        const std::string_view failed_operation(operation);
        CHECK(fakeCudaCapacityExplicitContextClears() ==
              (failed_operation.starts_with("cuCtx") ? 1 : 0));
    }

    fakeCudaCapacityReset();
    auto wrong_device = hbfsim::runtime::CapacityRuntime::create(
        tiny_profile(), control, kContext, kDevice + 1);
    CHECK(wrong_device == nullptr);
    CHECK(fakeCudaCapacityEventCount() == 0);
    CHECK(fakeCudaCapacityLastContext() == 0);
    CHECK(fakeCudaCapacityLastDevice() == -1);
    CHECK(fakeCudaCapacityExplicitContextClears() == 0);
    check_no_live_resources();

    fakeCudaCapacityReset();
    std::filesystem::remove(path);
    return 0;
}
