#include "../../src/cuda_runtime/vmm.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "VMM CHECK failed at line %d: %s\n", line,
                 expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

class FakeVmmDriver final : public hbfsim::runtime::VmmDriver {
  public:
    std::size_t granularity(int device) override
    {
        granularity_devices.push_back(device);
        return 4096;
    }

    std::uintptr_t reserve(std::size_t bytes,
                           std::size_t alignment) override
    {
        reserved_bytes.push_back(bytes);
        reserved_alignments.push_back(alignment);
        return next_address;
    }

    bool free_address(std::uintptr_t address, std::size_t bytes) override
    {
        freed_addresses.push_back(address);
        freed_bytes.push_back(bytes);
        return true;
    }

    std::uint64_t create(std::size_t bytes, int) override
    {
        created_bytes.push_back(bytes);
        return next_handle++;
    }

    bool release(std::uint64_t handle) override
    {
        released.push_back(handle);
        return true;
    }

    bool map(std::uintptr_t address, std::size_t bytes,
             std::uint64_t handle) override
    {
        mapped_addresses.push_back(address);
        mapped_bytes.push_back(bytes);
        mapped_handles.push_back(handle);
        return true;
    }

    bool unmap(std::uintptr_t address, std::size_t bytes) override
    {
        unmapped_addresses.push_back(address);
        unmapped_bytes.push_back(bytes);
        return true;
    }

    bool set_access(std::uintptr_t address, std::size_t, int) override
    {
        accessed_addresses.push_back(address);
        return fail_access_call == 0 ||
               accessed_addresses.size() != fail_access_call;
    }

    std::uintptr_t next_address{0x1'0000'0000ULL};
    std::uint64_t next_handle{10};
    std::size_t fail_access_call{0};
    std::vector<std::size_t> reserved_bytes;
    std::vector<std::size_t> reserved_alignments;
    std::vector<std::uintptr_t> freed_addresses;
    std::vector<std::size_t> freed_bytes;
    std::vector<std::size_t> created_bytes;
    std::vector<std::uint64_t> released;
    std::vector<std::uintptr_t> mapped_addresses;
    std::vector<std::size_t> mapped_bytes;
    std::vector<std::uint64_t> mapped_handles;
    std::vector<std::uintptr_t> unmapped_addresses;
    std::vector<std::size_t> unmapped_bytes;
    std::vector<std::uintptr_t> accessed_addresses;
    std::vector<int> granularity_devices;
};

}  // namespace

int main()
{
    FakeVmmDriver driver;
    {
        auto logical = hbfsim::runtime::VmmRange::reserve_logical(
            driver, 5000, 0, 3);
        CHECK(logical.base() == driver.next_address);
        CHECK(logical.logical_bytes() == 5000);
        CHECK(logical.reserved_bytes() == 8192);
        CHECK(driver.reserved_alignments.back() == 4096);
        CHECK(driver.granularity_devices.back() == 3);
    }
    CHECK(driver.freed_addresses.size() == 1);
    CHECK(driver.freed_bytes.front() == 8192);

    driver.next_address = 0x2'0000'0000ULL;
    {
        auto frames = hbfsim::runtime::VmmFramePool::create(
            driver, 2, 4096, 3);
        CHECK(frames.frame_addresses().size() == 2);
        CHECK(frames.frame_addresses()[0] == driver.next_address);
        CHECK(frames.frame_addresses()[1] == driver.next_address + 4096);
        CHECK(driver.created_bytes.size() == 1);
        CHECK(driver.created_bytes.front() == 8192);
        CHECK(driver.mapped_handles.size() == 1);
    }
    CHECK(driver.unmapped_addresses.size() == 1);
    CHECK(driver.released.size() == 1);
    CHECK(driver.freed_addresses.size() == 2);

    FakeVmmDriver failing;
    failing.fail_access_call = 1;
    bool rejected = false;
    try {
        (void)hbfsim::runtime::VmmFramePool::create(
            failing, 2, 4096, 0);
    } catch (const hbfsim::runtime::VmmError&) {
        rejected = true;
    }
    CHECK(rejected);
    CHECK(failing.unmapped_addresses.size() == 1);
    CHECK(failing.released.size() == 1);
    CHECK(failing.freed_addresses.size() == 1);
    return 0;
}
