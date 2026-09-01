#include "../../src/cuda_runtime/hbm_cache.hpp"
#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/capacity_page_service.hpp"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <span>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <vector>
#include <unistd.h>

namespace {

bool fail_fdatasync = false;

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity service CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

}  // namespace

extern "C" int __real_fdatasync(int fd);

extern "C" int __wrap_fdatasync(int fd)
{
    if (fail_fdatasync) {
        errno = EIO;
        return -1;
    }
    return __real_fdatasync(fd);
}

int main()
{
    constexpr std::size_t page_bytes = 4096;
    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-capacity-service-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);
    auto backing = hbfsim::host_service::BackingStore::create_deterministic(
        path, 4 * page_bytes, 0x9876);
    const auto original_page_one = backing.read_page(1, page_bytes);

    std::unordered_map<std::uint64_t, std::vector<std::byte>> frames{
        {0x1000, std::vector<std::byte>(page_bytes)},
        {0x2000, std::vector<std::byte>(page_bytes)},
    };
    bool fail_next_host_to_frame = false;
    bool fail_next_frame_to_host = false;
    bool throw_next_host_to_frame = false;
    hbfsim::runtime::HbmCache cache({0x1000, 0x2000});
    hbfsim::host_service::CapacityPageService service(
        backing, cache, page_bytes,
        {
            .host_to_frame = [&](std::uint64_t frame,
                                 std::span<const std::byte> data) {
                if (fail_next_host_to_frame) {
                    fail_next_host_to_frame = false;
                    return false;
                }
                if (throw_next_host_to_frame) {
                    throw_next_host_to_frame = false;
                    throw std::runtime_error("injected copy failure");
                }
                std::ranges::copy(data, frames.at(frame).begin());
                return true;
            },
            .frame_to_host = [&](std::uint64_t frame,
                                 std::span<std::byte> data) {
                if (fail_next_frame_to_host) {
                    fail_next_frame_to_host = false;
                    return false;
                }
                std::ranges::copy(frames.at(frame), data.begin());
                return true;
            },
        });

    const auto initial_stats = service.stats_v2();
    CHECK(initial_stats.demand_requests == 0);
    CHECK(initial_stats.cache_hits == 0);
    CHECK(initial_stats.cache_misses == 0);
    CHECK(initial_stats.frame_count == 2);
    CHECK(initial_stats.resident_pages_current == 0);
    CHECK(initial_stats.free_frames == 2);

    const auto page_zero = service.resolve(0, 0);
    CHECK(page_zero.status == hbfsim::RequestStatus::Ready);
    CHECK(page_zero.media.flags ==
          hbfsim::host_service::CapacityMediaRead);
    CHECK(frames.at(page_zero.frame_address) ==
          backing.read_page(0, page_bytes));

    const auto page_zero_hit = service.resolve(0, 0);
    CHECK(page_zero_hit.status == hbfsim::RequestStatus::Ready);
    CHECK(page_zero_hit.media.flags ==
          hbfsim::host_service::CapacityMediaNone);

    const auto page_one = service.resolve(1, 1);
    CHECK(page_one.status == hbfsim::RequestStatus::Ready);
    CHECK(frames.at(page_one.frame_address) == original_page_one);
    std::ranges::fill(frames.at(page_one.frame_address), std::byte{0x5a});

    const auto page_two = service.resolve(2, 0);
    CHECK(page_two.status == hbfsim::RequestStatus::Ready);
    CHECK(!cache.resolve(0).has_value());
    CHECK(cache.resolve(1).has_value());

    const auto page_three = service.resolve(3, 0);
    CHECK(page_three.status == hbfsim::RequestStatus::Ready);
    CHECK(page_three.media.flags ==
          (hbfsim::host_service::CapacityMediaProgram |
           hbfsim::host_service::CapacityMediaRead));
    CHECK(page_three.media.program_page == 1);
    CHECK(page_three.media.program_range_id == 1);
    CHECK(!cache.resolve(1).has_value());
    CHECK(backing.read_page(1, page_bytes) ==
          std::vector<std::byte>(page_bytes, std::byte{0x5a}));

    const auto dirty_again = service.resolve(2, 1);
    CHECK(dirty_again.status == hbfsim::RequestStatus::Ready);
    std::ranges::fill(frames.at(dirty_again.frame_address), std::byte{0xa5});
    fail_fdatasync = true;
    CHECK(service.flush() == hbfsim::RequestStatus::IoError);
    CHECK(cache.dirty_pages() == 1);
    CHECK(cache.resolve(2).value() == dirty_again.frame_address);
    fail_fdatasync = false;
    CHECK(service.flush() == hbfsim::RequestStatus::Ready);
    CHECK(cache.dirty_pages() == 0);
    CHECK(backing.read_page(2, page_bytes) ==
          std::vector<std::byte>(page_bytes, std::byte{0xa5}));

    const auto stats = service.stats_v2();
    CHECK(stats.demand_requests == 6);
    CHECK(stats.cache_hits == 2);
    CHECK(stats.cache_misses == 4);
    CHECK(stats.cache_hits + stats.cache_misses == stats.demand_requests);
    CHECK(stats.hbf_read_bytes == stats.cache_misses * page_bytes);
    CHECK(stats.hbf_program_bytes == stats.dirty_evictions * page_bytes);
    CHECK(stats.clean_evictions + stats.dirty_evictions ==
          stats.completed_residences);
    CHECK(stats.resident_pages_current <= stats.frame_count);
    CHECK(stats.free_frames + stats.resident_pages_current +
              stats.evicting_pages ==
          stats.frame_count);
    CHECK(stats.resident_pages_peak <= stats.frame_count);
    CHECK(stats.host_service_time_ns > 0);
    CHECK(stats.backing_io_wall_time_ns > 0);
    CHECK(stats.h2d_copy_time_ns > 0);
    CHECK(stats.dtoh_copy_time_ns > 0);

    hbfsim::runtime::HbmCache retry_cache({0x3000});
    frames.emplace(0x3000, std::vector<std::byte>(page_bytes));
    hbfsim::host_service::CapacityPageService retry_service(
        backing, retry_cache, page_bytes,
        {
            .host_to_frame = [&](std::uint64_t frame,
                                 std::span<const std::byte> data) {
                if (fail_next_host_to_frame) {
                    fail_next_host_to_frame = false;
                    return false;
                }
                if (throw_next_host_to_frame) {
                    throw_next_host_to_frame = false;
                    throw std::runtime_error("injected copy failure");
                }
                std::ranges::copy(data, frames.at(frame).begin());
                return true;
            },
            .frame_to_host = [&](std::uint64_t frame,
                                 std::span<std::byte> data) {
                if (fail_next_frame_to_host) {
                    fail_next_frame_to_host = false;
                    return false;
                }
                std::ranges::copy(frames.at(frame), data.begin());
                return true;
            },
        });
    fail_next_host_to_frame = true;
    CHECK(retry_service.resolve(0, 0).status ==
          hbfsim::RequestStatus::CopyError);
    CHECK(!retry_cache.resolve(0).has_value());
    CHECK(retry_service.resolve(0, 0).status ==
          hbfsim::RequestStatus::Ready);

    const auto page_zero_before_writeback =
        backing.read_page(0, page_bytes);
    CHECK(retry_service.resolve(0, 1).status ==
          hbfsim::RequestStatus::Ready);
    std::ranges::fill(frames.at(0x3000), std::byte{0x6b});
    fail_next_frame_to_host = true;
    CHECK(retry_service.resolve(1, 0).status ==
          hbfsim::RequestStatus::CopyError);
    CHECK(retry_cache.resolve(0).value() == 0x3000);
    CHECK(backing.read_page(0, page_bytes) == page_zero_before_writeback);
    CHECK(retry_service.resolve(1, 0).status ==
          hbfsim::RequestStatus::Ready);
    CHECK(backing.read_page(0, page_bytes) ==
          std::vector<std::byte>(page_bytes, std::byte{0x6b}));

    throw_next_host_to_frame = true;
    CHECK(retry_service.resolve(2, 0).status ==
          hbfsim::RequestStatus::CopyError);
    CHECK(!retry_cache.resolve(2).has_value());

    std::vector<std::uint64_t> callback_reads;
    std::vector<std::uint64_t> callback_writes;
    std::size_t callback_flushes = 0;
    hbfsim::runtime::HbmCache callback_cache({0x4000, 0x5000});
    frames.emplace(0x4000, std::vector<std::byte>(page_bytes));
    frames.emplace(0x5000, std::vector<std::byte>(page_bytes));
    hbfsim::host_service::CapacityPageService callback_service(
        {
            .read_page = [&](std::uint64_t global_page, std::size_t bytes) {
                callback_reads.push_back(global_page);
                return hbfsim::host_service::RoutedPage{
                    .status = hbfsim::RequestStatus::Ready,
                    .range_id = global_page < 2 ? 1u : 2u,
                    .bytes = std::vector<std::byte>(
                        bytes, static_cast<std::byte>(global_page)),
                };
            },
            .write_page = [&](std::uint64_t global_page, std::size_t,
                              std::span<const std::byte>) {
                callback_writes.push_back(global_page);
                return hbfsim::RequestStatus::Ready;
            },
            .flush = [&] {
                ++callback_flushes;
                return hbfsim::RequestStatus::Ready;
            },
        },
        callback_cache, page_bytes,
        {
            .host_to_frame = [&](std::uint64_t frame,
                                 std::span<const std::byte> data) {
                std::ranges::copy(data, frames.at(frame).begin());
                return true;
            },
            .frame_to_host = [&](std::uint64_t frame,
                                 std::span<std::byte> data) {
                std::ranges::copy(frames.at(frame), data.begin());
                return true;
            },
        });
    CHECK(callback_service.resolve(0, 1).status ==
          hbfsim::RequestStatus::Ready);
    CHECK(callback_service.resolve(10, 1).status ==
          hbfsim::RequestStatus::Ready);
    CHECK((callback_reads == std::vector<std::uint64_t>{0, 10}));
    std::vector<std::pair<std::uint32_t, std::uint64_t>> modeled_programs;
    CHECK(callback_service.flush(
              [&](std::uint32_t range_id, std::uint64_t global_page) {
                  modeled_programs.emplace_back(range_id, global_page);
                  return hbfsim::RequestStatus::Ready;
              },
              1) == hbfsim::RequestStatus::Ready);
    CHECK((callback_writes == std::vector<std::uint64_t>{0}));
    CHECK((modeled_programs ==
           std::vector<std::pair<std::uint32_t, std::uint64_t>>{{1, 0}}));
    CHECK(callback_cache.resolve(10).has_value());
    CHECK(callback_cache.dirty_pages() == 1);
    CHECK(callback_flushes == 1);

    CHECK(callback_service.flush(
              [&](std::uint32_t range_id, std::uint64_t global_page) {
                  modeled_programs.emplace_back(range_id, global_page);
                  return hbfsim::RequestStatus::Timeout;
              },
              2) == hbfsim::RequestStatus::Timeout);
    CHECK(callback_cache.resolve(10).has_value());
    CHECK(callback_cache.dirty_pages() == 1);
    CHECK(callback_flushes == 1);
    CHECK(callback_service.flush(
              [&](std::uint32_t, std::uint64_t) {
                  return hbfsim::RequestStatus::Ready;
              },
              2) == hbfsim::RequestStatus::Ready);
    CHECK(callback_cache.dirty_pages() == 0);
    CHECK(callback_flushes == 2);

    CHECK(callback_service.resolve(0, 1).status ==
          hbfsim::RequestStatus::Ready);
    std::mutex concurrent_mutex;
    std::condition_variable concurrent_ready;
    bool concurrent_done = false;
    auto concurrent_status = hbfsim::RequestStatus::Pending;
    CHECK(callback_service.flush(
              [&](std::uint32_t, std::uint64_t) {
                  std::thread concurrent([&] {
                      const auto resolved = callback_service.resolve(10, 0);
                      {
                          std::lock_guard lock(concurrent_mutex);
                          concurrent_status = resolved.status;
                          concurrent_done = true;
                      }
                      concurrent_ready.notify_one();
                  });
                  std::unique_lock lock(concurrent_mutex);
                  if (!concurrent_ready.wait_for(
                          lock, std::chrono::milliseconds(100),
                          [&] { return concurrent_done; })) {
                      concurrent.detach();
                      return hbfsim::RequestStatus::Timeout;
                  }
                  lock.unlock();
                  concurrent.join();
                  return concurrent_status;
              },
              1) == hbfsim::RequestStatus::Ready);
    CHECK(concurrent_status == hbfsim::RequestStatus::Ready);

    hbfsim::runtime::HbmCache reclaim_cache({0x6000});
    frames.emplace(0x6000, std::vector<std::byte>(page_bytes));
    hbfsim::host_service::CapacityPageService reclaim_service(
        backing, reclaim_cache, page_bytes,
        {
            .host_to_frame = [&](std::uint64_t frame,
                                 std::span<const std::byte> data) {
                std::ranges::copy(data, frames.at(frame).begin());
                return true;
            },
            .frame_to_host = [&](std::uint64_t frame,
                                 std::span<std::byte> data) {
                std::ranges::copy(frames.at(frame), data.begin());
                return true;
            },
        });
    CHECK(reclaim_service.resolve(0, 1).status ==
          hbfsim::RequestStatus::Ready);
    auto reclaim_status = hbfsim::RequestStatus::Pending;
    CHECK(reclaim_service.flush(
              [&](std::uint32_t, std::uint64_t) {
                  std::thread concurrent([&] {
                      reclaim_status = reclaim_service.resolve(0, 0).status;
                  });
                  concurrent.join();
                  return reclaim_status;
              }) == hbfsim::RequestStatus::IoError);
    CHECK(reclaim_status == hbfsim::RequestStatus::Ready);
    CHECK(reclaim_cache.dirty_pages() == 1);
    CHECK(reclaim_service.flush(
              [](std::uint32_t, std::uint64_t) {
                  return hbfsim::RequestStatus::Ready;
              }) == hbfsim::RequestStatus::Ready);
    CHECK(reclaim_cache.dirty_pages() == 0);

    std::filesystem::remove(path);
    return 0;
}
