#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <vector>

namespace hbfsim::host_service {

class BackingStoreError : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

class BackingStore {
  public:
    BackingStore(const std::filesystem::path& path,
                 std::uint64_t file_offset, std::uint64_t length,
                 bool writable);
    ~BackingStore();

    BackingStore(const BackingStore&) = delete;
    BackingStore& operator=(const BackingStore&) = delete;
    BackingStore(BackingStore&& other) noexcept;
    BackingStore& operator=(BackingStore&& other) noexcept;

    static BackingStore create_deterministic(
        const std::filesystem::path& path, std::uint64_t length,
        std::uint64_t seed);

    [[nodiscard]] std::uint64_t length() const noexcept;
    [[nodiscard]] std::vector<std::byte> read_page(
        std::uint64_t logical_page, std::size_t page_bytes) const;
    void write_page(std::uint64_t logical_page, std::size_t page_bytes,
                    const std::vector<std::byte>& data);
    void flush();

  private:
    [[nodiscard]] std::pair<std::uint64_t, std::size_t> page_window(
        std::uint64_t logical_page, std::size_t page_bytes) const;
    void close() noexcept;

    int fd_{-1};
    std::uint64_t file_offset_{0};
    std::uint64_t length_{0};
    bool writable_{false};
};

}  // namespace hbfsim::host_service
