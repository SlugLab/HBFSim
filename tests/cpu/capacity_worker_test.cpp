#include "../../src/cuda_runtime/hbm_cache.hpp"
#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/capacity_page_service.hpp"
#include "../../src/host_service/capacity_worker.hpp"
#include "../../src/host_service/control_layout.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <semaphore>
#include <span>
#include <thread>
#include <unordered_map>
#include <vector>
#include <unistd.h>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity worker CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

hbfsim::HbfRequest request(std::uint64_t ticket, std::uint64_t request_id,
                           std::uint64_t logical_page,
                           std::uint32_t operation,
                           std::uint32_t page_bytes)
{
    return {
        .request_id = request_id,
        .sequence = ticket,
        .arrival_ns = 0,
        .logical_address = logical_page * page_bytes,
        .deadline_ns = 0,
        .bytes = page_bytes,
        .range_id = 1,
        .stream_id = 0,
        .operation = operation,
        .page_generation = 0,
        .flags = 0,
    };
}

bool wait_for_result(hbfsim::host_service::ControlView control,
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

void signal_stop_locked(void* opaque) noexcept
{
    static_cast<std::binary_semaphore*>(opaque)->release();
}

}  // namespace

int main()
{
    constexpr std::uint32_t capacity = 4;
    constexpr std::uint32_t page_bytes = 4096;
    std::vector<std::byte> control_memory(
        hbfsim::host_service::control_region_bytes(capacity));
    hbfsim::host_service::ControlView control(control_memory.data(),
                                               control_memory.size());
    CHECK(control.initialize(capacity));

    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-capacity-worker-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);
    auto backing = hbfsim::host_service::BackingStore::create_deterministic(
        path, 4 * page_bytes, 0x1234);
    const auto original_page_zero = backing.read_page(0, page_bytes);
    const auto original_page_two = backing.read_page(2, page_bytes);
    const auto original_page_three = backing.read_page(3, page_bytes);
    CHECK(original_page_two != original_page_three);

    std::unordered_map<std::uint64_t, std::vector<std::byte>> frames{
        {0x1000, std::vector<std::byte>(page_bytes)},
        {0x2000, std::vector<std::byte>(page_bytes)},
        {0x3000, std::vector<std::byte>(page_bytes)},
    };
    std::atomic<bool> block_next_copy{false};
    std::binary_semaphore copy_entered{0};
    std::binary_semaphore copy_release{0};
    hbfsim::runtime::HbmCache cache({0x1000, 0x2000, 0x3000});
    hbfsim::host_service::CapacityPageService service(
        backing, cache, page_bytes,
        {
            .host_to_frame = [&](std::uint64_t frame,
                                 std::span<const std::byte> bytes) {
                if (block_next_copy.exchange(false)) {
                    copy_entered.release();
                    copy_release.acquire();
                }
                std::ranges::copy(bytes, frames.at(frame).begin());
                return true;
            },
            .frame_to_host = [&](std::uint64_t frame,
                                 std::span<std::byte> bytes) {
                std::ranges::copy(frames.at(frame), bytes.begin());
                return true;
            },
        });

    hbfsim::host_service::CapacityWorker worker(control, service);

    const auto read = request(0, 10, 0, 0, page_bytes);
    CHECK(control.begin_capacity_handoff(read));
    hbfsim::host_service::CapacityHandoffResult result{};
    CHECK(wait_for_result(control, read, result));
    CHECK(result.status == hbfsim::RequestStatus::Ready);
    CHECK(result.frame_address != 0);
    CHECK(result.media.flags == hbfsim::host_service::CapacityMediaRead);
    CHECK(frames.at(result.frame_address) == original_page_zero);
    CHECK(control.release_capacity_handoff(read));

    const auto write = request(1, 11, 1, 1, page_bytes);
    CHECK(control.begin_capacity_handoff(write));
    CHECK(wait_for_result(control, write, result));
    CHECK(result.status == hbfsim::RequestStatus::Ready);
    std::ranges::fill(frames.at(result.frame_address), std::byte{0x5a});
    CHECK(control.release_capacity_handoff(write));
    std::vector<std::pair<std::uint32_t, std::uint64_t>> modeled_programs;
    CHECK(worker.flush(
              [&](std::uint32_t range_id, std::uint64_t global_page) {
                  modeled_programs.emplace_back(range_id, global_page);
                  return hbfsim::RequestStatus::Ready;
              },
              1) == hbfsim::RequestStatus::Ready);
    CHECK((modeled_programs ==
           std::vector<std::pair<std::uint32_t, std::uint64_t>>{{1, 1}}));
    CHECK(backing.read_page(1, page_bytes) ==
          std::vector<std::byte>(page_bytes, std::byte{0x5a}));

    block_next_copy.store(true);
    const auto timed_out = request(2, 12, 2, 0, page_bytes);
    CHECK(control.begin_capacity_handoff(timed_out));
    copy_entered.acquire();
    CHECK(control.complete_capacity_handoff(
        timed_out.sequence, timed_out.request_id, 0,
        hbfsim::RequestStatus::Timeout));
    CHECK(control.capacity_handoff_result(timed_out, result));
    CHECK(result.status == hbfsim::RequestStatus::Timeout);
    CHECK(control.release_capacity_handoff(timed_out));

    const auto reused = request(timed_out.sequence + capacity, 13, 3, 0,
                                page_bytes);
    CHECK(control.begin_capacity_handoff(reused));
    copy_release.release();
    CHECK(wait_for_result(control, reused, result));
    CHECK(result.status == hbfsim::RequestStatus::Ready);
    CHECK(result.frame_address != 0);
    CHECK(frames.at(result.frame_address) == original_page_three);
    CHECK(frames.at(result.frame_address) != original_page_two);
    CHECK(control.release_capacity_handoff(reused));

    block_next_copy.store(true);
    const auto during_stop = request(3, 14, 1, 0, page_bytes);
    CHECK(control.begin_capacity_handoff(during_stop));
    copy_entered.acquire();

    std::binary_semaphore stop_locked{0};
    std::binary_semaphore second_stop_started{0};
    std::atomic<int> stop_returned{0};
    std::atomic<int> stop_errors{0};
    std::thread first_stop([&] {
        try {
            worker.stop_with_hook_for_test(signal_stop_locked, &stop_locked);
        } catch (...) {
            stop_errors.fetch_add(1);
        }
        stop_returned.fetch_add(1);
    });
    stop_locked.acquire();
    std::thread second_stop([&] {
        second_stop_started.release();
        try {
            worker.stop();
        } catch (...) {
            stop_errors.fetch_add(1);
        }
        stop_returned.fetch_add(1);
    });
    second_stop_started.acquire();
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    CHECK(stop_returned.load() == 0);
    copy_release.release();
    first_stop.join();
    second_stop.join();
    CHECK(stop_errors.load() == 0);
    CHECK(stop_returned.load() == 2);
    CHECK(control.capacity_handoff_result(during_stop, result));
    CHECK(result.status == hbfsim::RequestStatus::Ready);
    CHECK(result.frame_address != 0);
    CHECK(frames.at(result.frame_address) ==
          std::vector<std::byte>(page_bytes, std::byte{0x5a}));
    CHECK(control.release_capacity_handoff(during_stop));

    const auto after_stop = request(4, 15, 1, 0, page_bytes);
    CHECK(control.begin_capacity_handoff(after_stop));
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    CHECK(!control.capacity_handoff_result(after_stop, result));

    std::filesystem::remove(path);
    return 0;
}
