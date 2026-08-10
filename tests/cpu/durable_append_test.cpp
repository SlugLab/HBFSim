#include "hbfsim/durable_append.hpp"

#include <algorithm>
#include <cerrno>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {

bool inject_faults = false;
int write_calls = 0;
int fdatasync_calls = 0;
int fsync_calls = 0;

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
