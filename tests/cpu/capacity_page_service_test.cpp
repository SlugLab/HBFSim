#include "../../src/cuda_runtime/hbm_cache.hpp"
#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/capacity_page_service.hpp"

#include <algorithm>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <span>
#include <stdexcept>
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

    const auto page_zero = service.resolve(0, 0);
    CHECK(page_zero.status == hbfsim::RequestStatus::Ready);
    CHECK(frames.at(page_zero.frame_address) ==
          backing.read_page(0, page_bytes));

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
    CHECK(callback_service.flush(0, 2) == hbfsim::RequestStatus::Ready);
    CHECK((callback_writes == std::vector<std::uint64_t>{0}));
    CHECK(callback_cache.resolve(10).has_value());
    CHECK(callback_cache.dirty_pages() == 1);
    CHECK(callback_flushes == 1);

    std::filesystem::remove(path);
    return 0;
}
