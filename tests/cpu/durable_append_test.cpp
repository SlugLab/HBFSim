#include "hbfsim/durable_append.hpp"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <future>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {

bool inject_faults = false;
int write_calls = 0;
int fdatasync_calls = 0;
int fsync_calls = 0;
std::mutex directory_gate_mutex;
std::condition_variable directory_gate_entered;
std::condition_variable directory_gate_release;
bool block_directory_sync = false;
bool directory_sync_entered = false;
bool release_directory_sync = false;

}  // namespace

extern "C" ssize_t __real_write(int fd, const void* buffer, size_t count);
extern "C" int __real_fdatasync(int fd);
extern "C" int __real_fsync(int fd);

extern "C" ssize_t __wrap_write(int fd, const void* buffer, size_t count)
{
    if (!inject_faults) {
        return __real_write(fd, buffer, count);
    }
    ++write_calls;
    if (write_calls == 1) {
        errno = EINTR;
        return -1;
    }
    if (write_calls == 2) {
        return __real_write(fd, buffer, std::max<std::size_t>(1, count / 2));
    }
    return __real_write(fd, buffer, count);
}

extern "C" int __wrap_fdatasync(int fd)
{
    if (inject_faults && ++fdatasync_calls == 1) {
        errno = EINTR;
        return -1;
    }
    return __real_fdatasync(fd);
}

extern "C" int __wrap_fsync(int fd)
{
    if (inject_faults && ++fsync_calls == 1) {
        errno = EINTR;
        return -1;
    }
    if (block_directory_sync) {
        std::unique_lock lock(directory_gate_mutex);
        directory_sync_entered = true;
        directory_gate_entered.notify_one();
        directory_gate_release.wait(lock,
                                    [] { return release_directory_sync; });
    }
    return __real_fsync(fd);
}

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

int main()
{
    const auto root =
        std::filesystem::temp_directory_path() /
        ("hbfsim-durable-append-test-" + std::to_string(::getpid()));
    std::filesystem::remove_all(root);
    std::filesystem::create_directory(root);
    const auto path = root / "events.jsonl";

    inject_faults = true;
    hbfsim::append_durable_line(path, "one\n");
    hbfsim::append_durable_line(path, "two\n");
    inject_faults = false;
    std::ifstream input(path);
    const std::string contents{std::istreambuf_iterator<char>(input), {}};
    CHECK(contents == "one\ntwo\n");
    CHECK(write_calls >= 4);
    CHECK(fdatasync_calls >= 3);
    CHECK(fsync_calls == 2);

    const auto concurrent_path = root / "concurrent.jsonl";
    block_directory_sync = true;
    auto creator = std::async(std::launch::async, [&] {
        hbfsim::append_durable_line(concurrent_path, "creator\n");
    });
    bool creator_reached_directory_sync = false;
    {
        std::unique_lock lock(directory_gate_mutex);
        creator_reached_directory_sync =
            directory_gate_entered.wait_for(lock, std::chrono::seconds(1), [] {
                return directory_sync_entered;
            });
    }
    if (!creator_reached_directory_sync) {
        {
            std::lock_guard lock(directory_gate_mutex);
            release_directory_sync = true;
        }
        directory_gate_release.notify_one();
        creator.wait();
        CHECK(creator_reached_directory_sync);
    }
    std::promise<void> second_started;
    auto second = std::async(std::launch::async, [&] {
        second_started.set_value();
        hbfsim::append_durable_line(concurrent_path, "second\n");
    });
    second_started.get_future().wait();
    const bool second_waited_for_directory =
        second.wait_for(std::chrono::milliseconds(100)) ==
        std::future_status::timeout;
    {
        std::lock_guard lock(directory_gate_mutex);
        release_directory_sync = true;
    }
    directory_gate_release.notify_one();
    creator.get();
    second.get();
    block_directory_sync = false;
    CHECK(second_waited_for_directory);
    std::ifstream concurrent_input(concurrent_path);
    const std::string concurrent_contents{
        std::istreambuf_iterator<char>(concurrent_input), {}};
    CHECK(concurrent_contents == "creator\nsecond\n");

    bool rejected = false;
    try {
        hbfsim::append_durable_line(root / "missing" / "events.jsonl",
                                    "three\n");
    } catch (const std::runtime_error&) {
        rejected = true;
    }
    CHECK(rejected);
    std::filesystem::remove_all(root);
    return 0;
}
