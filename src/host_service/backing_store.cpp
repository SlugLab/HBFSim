#include "backing_store.hpp"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace hbfsim::host_service {
namespace {

[[noreturn]] void io_error(const char* operation, int error = errno)
{
    throw BackingStoreError(std::string(operation) + ": " +
                            std::strerror(error));
}

void exact_pread(int fd, std::byte* data, std::size_t bytes,
                 std::uint64_t offset)
{
    std::size_t consumed = 0;
    while (consumed != bytes) {
        const auto result = ::pread(
            fd, data + consumed, bytes - consumed,
            static_cast<off_t>(offset + consumed));
        if (result > 0) {
            consumed += static_cast<std::size_t>(result);
            continue;
        }
        if (result < 0 && errno == EINTR) {
            continue;
        }
        if (result == 0) {
            throw BackingStoreError("backing store short read");
        }
        io_error("backing store read failed");
    }
}

void exact_pwrite(int fd, const std::byte* data, std::size_t bytes,
                  std::uint64_t offset)
{
    std::size_t consumed = 0;
    while (consumed != bytes) {
        const auto result = ::pwrite(
            fd, data + consumed, bytes - consumed,
            static_cast<off_t>(offset + consumed));
        if (result > 0) {
            consumed += static_cast<std::size_t>(result);
            continue;
        }
        if (result < 0 && errno == EINTR) {
            continue;
        }
        if (result == 0) {
            throw BackingStoreError("backing store short write");
        }
        io_error("backing store write failed");
    }
}

}  // namespace

BackingStore::BackingStore(const std::filesystem::path& path,
                           std::uint64_t file_offset,
                           std::uint64_t length, bool writable)
    : file_offset_(file_offset), length_(length), writable_(writable)
{
    if (path.empty() || length == 0 ||
        file_offset > std::numeric_limits<std::uint64_t>::max() - length ||
        file_offset + length >
            static_cast<std::uint64_t>(std::numeric_limits<off_t>::max())) {
        throw BackingStoreError("invalid backing store window");
    }
    fd_ = ::open(path.c_str(), (writable ? O_RDWR : O_RDONLY) | O_CLOEXEC);
    if (fd_ < 0) {
        io_error("failed to open backing store");
    }
    const auto lock = writable ? LOCK_EX : LOCK_SH;
    if (::flock(fd_, lock | LOCK_NB) != 0) {
        const auto error = errno;
        close();
        io_error("failed to lock backing store", error);
    }
    struct stat status {};
    if (::fstat(fd_, &status) != 0) {
        const auto error = errno;
        close();
        io_error("failed to stat backing store", error);
    }
    if (!S_ISREG(status.st_mode) || status.st_size < 0 ||
        static_cast<std::uint64_t>(status.st_size) < file_offset + length) {
        close();
        throw BackingStoreError("backing store is shorter than mapped window");
    }
}

BackingStore::~BackingStore()
{
    close();
}

BackingStore::BackingStore(BackingStore&& other) noexcept
    : fd_(other.fd_), file_offset_(other.file_offset_), length_(other.length_),
      writable_(other.writable_)
{
    other.fd_ = -1;
    other.file_offset_ = 0;
    other.length_ = 0;
    other.writable_ = false;
}

BackingStore& BackingStore::operator=(BackingStore&& other) noexcept
{
    if (this == &other) {
        return *this;
    }
    close();
    fd_ = other.fd_;
    file_offset_ = other.file_offset_;
    length_ = other.length_;
    writable_ = other.writable_;
    other.fd_ = -1;
    other.file_offset_ = 0;
    other.length_ = 0;
    other.writable_ = false;
    return *this;
}

BackingStore BackingStore::create_deterministic(
    const std::filesystem::path& path, std::uint64_t length,
    std::uint64_t seed)
{
    if (path.empty() || length == 0 ||
        length > static_cast<std::uint64_t>(
                     std::numeric_limits<off_t>::max())) {
        throw BackingStoreError("invalid deterministic backing store size");
    }
    const auto fd = ::open(path.c_str(), O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC,
                           S_IRUSR | S_IWUSR);
    if (fd < 0) {
        io_error("failed to create deterministic backing store");
    }
    try {
        if (::ftruncate(fd, static_cast<off_t>(length)) != 0) {
            io_error("failed to size deterministic backing store");
        }
        constexpr std::size_t chunk_bytes = 64 * 1024;
        std::vector<std::byte> chunk(chunk_bytes);
        auto state = seed == 0 ? 0x9e3779b97f4a7c15ULL : seed;
        std::uint64_t offset = 0;
        while (offset != length) {
            const auto bytes = static_cast<std::size_t>(
                std::min<std::uint64_t>(chunk.size(), length - offset));
            for (std::size_t index = 0; index < bytes; ++index) {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                chunk[index] = static_cast<std::byte>(state & 0xffU);
            }
            exact_pwrite(fd, chunk.data(), bytes, offset);
            offset += bytes;
        }
        if (::fdatasync(fd) != 0) {
            io_error("failed to sync deterministic backing store");
        }
    } catch (...) {
        ::close(fd);
        std::filesystem::remove(path);
        throw;
    }
    ::close(fd);
    try {
        return BackingStore(path, 0, length, true);
    } catch (...) {
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        throw;
    }
}

std::uint64_t BackingStore::length() const noexcept
{
    return length_;
}

std::pair<std::uint64_t, std::size_t> BackingStore::page_window(
    std::uint64_t logical_page, std::size_t page_bytes) const
{
    if (fd_ < 0 || page_bytes == 0 ||
        logical_page > std::numeric_limits<std::uint64_t>::max() /
                           page_bytes) {
        throw BackingStoreError("invalid backing page request");
    }
    const auto relative = logical_page * page_bytes;
    if (relative >= length_) {
        throw BackingStoreError("backing page is outside mapped window");
    }
    const auto available = std::min<std::uint64_t>(page_bytes,
                                                   length_ - relative);
    return {file_offset_ + relative, static_cast<std::size_t>(available)};
}

std::vector<std::byte> BackingStore::read_page(
    std::uint64_t logical_page, std::size_t page_bytes) const
{
    const auto [offset, bytes] = page_window(logical_page, page_bytes);
    std::vector<std::byte> result(page_bytes, std::byte{0});
    exact_pread(fd_, result.data(), bytes, offset);
    return result;
}

void BackingStore::write_page(std::uint64_t logical_page,
                              std::size_t page_bytes,
                              const std::vector<std::byte>& data)
{
    if (!writable_) {
        throw BackingStoreError("backing store is read-only");
    }
    if (data.size() != page_bytes) {
        throw BackingStoreError("backing page write has wrong size");
    }
    const auto [offset, bytes] = page_window(logical_page, page_bytes);
    exact_pwrite(fd_, data.data(), bytes, offset);
}

void BackingStore::flush()
{
    if (fd_ < 0) {
        throw BackingStoreError("backing store is closed");
    }
    if (!writable_) {
        return;
    }
    if (::fdatasync(fd_) != 0) {
        io_error("failed to flush backing store");
    }
}

void BackingStore::close() noexcept
{
    if (fd_ >= 0) {
        (void)::flock(fd_, LOCK_UN);
        (void)::close(fd_);
        fd_ = -1;
    }
}

}  // namespace hbfsim::host_service
