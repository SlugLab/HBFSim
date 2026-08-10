#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace hbfsim::runtime {

class VmmError : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

class VmmDriver {
  public:
    virtual ~VmmDriver() = default;
    virtual std::size_t granularity(int device_ordinal) = 0;
    virtual std::uintptr_t reserve(std::size_t bytes,
                                   std::size_t alignment) = 0;
    virtual bool free_address(std::uintptr_t address, std::size_t bytes) = 0;
    virtual std::uint64_t create(std::size_t bytes, int device_ordinal) = 0;
    virtual bool release(std::uint64_t handle) = 0;
    virtual bool map(std::uintptr_t address, std::size_t bytes,
                     std::uint64_t handle) = 0;
    virtual bool unmap(std::uintptr_t address, std::size_t bytes) = 0;
    virtual bool set_access(std::uintptr_t address, std::size_t bytes,
                            int device_ordinal) = 0;
};

class CudaVmmDriver final : public VmmDriver {
  public:
    std::size_t granularity(int device_ordinal) override;
    std::uintptr_t reserve(std::size_t bytes,
                           std::size_t alignment) override;
    bool free_address(std::uintptr_t address, std::size_t bytes) override;
    std::uint64_t create(std::size_t bytes, int device_ordinal) override;
    bool release(std::uint64_t handle) override;
    bool map(std::uintptr_t address, std::size_t bytes,
             std::uint64_t handle) override;
    bool unmap(std::uintptr_t address, std::size_t bytes) override;
    bool set_access(std::uintptr_t address, std::size_t bytes,
                    int device_ordinal) override;
};

class VmmRange {
  public:
    VmmRange() = default;
    ~VmmRange();
    VmmRange(const VmmRange&) = delete;
    VmmRange& operator=(const VmmRange&) = delete;
    VmmRange(VmmRange&& other) noexcept;
    VmmRange& operator=(VmmRange&& other) noexcept;

    static VmmRange reserve_logical(VmmDriver& driver,
                                    std::size_t logical_bytes,
                                    std::size_t alignment,
                                    int device_ordinal);

    [[nodiscard]] std::uintptr_t base() const noexcept { return base_; }
    [[nodiscard]] std::size_t logical_bytes() const noexcept
    {
        return logical_bytes_;
    }
    [[nodiscard]] std::size_t reserved_bytes() const noexcept
    {
        return reserved_bytes_;
    }

  private:
    void reset() noexcept;

    VmmDriver* driver_{nullptr};
    std::uintptr_t base_{0};
    std::size_t logical_bytes_{0};
    std::size_t reserved_bytes_{0};
};

class VmmFramePool {
  public:
    VmmFramePool() = default;
    ~VmmFramePool();
    VmmFramePool(const VmmFramePool&) = delete;
    VmmFramePool& operator=(const VmmFramePool&) = delete;
    VmmFramePool(VmmFramePool&& other) noexcept;
    VmmFramePool& operator=(VmmFramePool&& other) noexcept;

    static VmmFramePool create(VmmDriver& driver, std::size_t frame_count,
                               std::size_t page_bytes,
                               int device_ordinal);

    [[nodiscard]] const std::vector<std::uint64_t>& frame_addresses() const
        noexcept
    {
        return frame_addresses_;
    }

  private:
    void reset() noexcept;

    VmmDriver* driver_{nullptr};
    std::uintptr_t base_{0};
    std::size_t reserved_bytes_{0};
    std::uint64_t handle_{0};
    bool mapped_{false};
    std::vector<std::uint64_t> frame_addresses_;
};

}  // namespace hbfsim::runtime
