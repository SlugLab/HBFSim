#include "hbfsim/durable_append.hpp"

#include <sys/file.h>

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace hbfsim {
namespace {

[[noreturn]] void fail(const char* operation, const std::filesystem::path& path,
                       int error)
{
    throw std::runtime_error(std::string("unable to ") + operation + " " +
                             path.string() + ": " + std::strerror(error));
}

void close_checked(int fd, const std::filesystem::path& path)
{
    if (::close(fd) != 0) {
        fail("close", path, errno);
    }
}

void sync_fd(int fd, const std::filesystem::path& path, bool directory)
{
    int status;
    do {
        status = directory ? ::fsync(fd) : ::fdatasync(fd);
    } while (status != 0 && errno == EINTR);
    if (status != 0) {
        fail(directory ? "sync directory for" : "sync", path, errno);
    }
}

void lock_fd(int fd, const std::filesystem::path& path)
{
    int status;
    do {
        status = ::flock(fd, LOCK_EX);
    } while (status != 0 && errno == EINTR);
    if (status != 0) {
        fail("lock", path, errno);
    }
}

}  // namespace

void append_durable_line(const std::filesystem::path& path,
                         std::string_view line)
{
    int fd = -1;
    bool created = false;
    for (;;) {
        fd = ::open(path.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC);
        if (fd >= 0) {
            break;
        }
        if (errno != ENOENT) {
            fail("open", path, errno);
        }
        fd = ::open(path.c_str(),
                    O_WRONLY | O_APPEND | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
        if (fd >= 0) {
            created = true;
            break;
        }
        if (errno != EEXIST) {
            fail("create", path, errno);
        }
    }

    try {
        lock_fd(fd, path);
        std::size_t offset = 0;
        while (offset < line.size()) {
            const auto count =
                ::write(fd, line.data() + offset, line.size() - offset);
            if (count < 0 && errno == EINTR) {
                continue;
            }
            if (count <= 0) {
                fail("append", path, count == 0 ? EIO : errno);
            }
            offset += static_cast<std::size_t>(count);
        }
        sync_fd(fd, path, false);
        const int closing_fd = fd;
        fd = -1;
        close_checked(closing_fd, path);

        if (created) {
            const auto parent = path.parent_path().empty()
                                    ? std::filesystem::path{"."}
                                    : path.parent_path();
            const int directory_fd =
                ::open(parent.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
            if (directory_fd < 0) {
                fail("open parent directory for", path, errno);
            }
            try {
                sync_fd(directory_fd, path, true);
            } catch (...) {
                ::close(directory_fd);
                throw;
            }
            close_checked(directory_fd, path);
        }
    } catch (...) {
        if (fd >= 0) {
            ::close(fd);
        }
        throw;
    }
}

}  // namespace hbfsim
