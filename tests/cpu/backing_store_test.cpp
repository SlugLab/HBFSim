#include "../../src/host_service/backing_store.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <vector>
#include <unistd.h>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "backing store CHECK failed at line %d: %s\n", line,
                 expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

}  // namespace

int main()
{
    constexpr std::size_t page_bytes = 4096;
    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-backing-store-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);

    {
        auto store = hbfsim::host_service::BackingStore::create_deterministic(
            path, 3 * page_bytes + 17, 0x1234);
        CHECK(store.length() == 3 * page_bytes + 17);
        const auto first = store.read_page(1, page_bytes);
        const auto repeated = store.read_page(1, page_bytes);
        CHECK(first == repeated);
        CHECK(first.size() == page_bytes);

        const auto tail = store.read_page(3, page_bytes);
        CHECK(tail.size() == page_bytes);
        for (std::size_t index = 17; index < tail.size(); ++index) {
            CHECK(tail[index] == std::byte{0});
        }

        std::vector<std::byte> replacement(page_bytes, std::byte{0x5a});
        store.write_page(2, page_bytes, replacement);
        store.flush();
        CHECK(store.read_page(2, page_bytes) == replacement);

        bool out_of_range = false;
        try {
            (void)store.read_page(4, page_bytes);
        } catch (const hbfsim::host_service::BackingStoreError&) {
            out_of_range = true;
        }
        CHECK(out_of_range);
    }

    hbfsim::host_service::BackingStore read_only(
        path, 0, 3 * page_bytes + 17, false);
    read_only.flush();
    bool write_rejected = false;
    try {
        read_only.write_page(
            0, page_bytes,
            std::vector<std::byte>(page_bytes, std::byte{0}));
    } catch (const hbfsim::host_service::BackingStoreError&) {
        write_rejected = true;
    }
    CHECK(write_rejected);

    std::filesystem::remove(path);
    return 0;
}
