#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/capacity_backing_router.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <future>
#include <limits>
#include <memory>
#include <semaphore>
#include <span>
#include <vector>
#include <unistd.h>

namespace hbfsim::host_service {

class CapacityBackingRouterTestAccess {
  public:
    static void set_next_token(CapacityBackingRouter& router,
                               CapacityBackingRouter::Token next)
    {
        router.next_token_ = next;
        router.tokens_exhausted_ = false;
    }
};

}  // namespace hbfsim::host_service

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity router CHECK failed at line %d: %s\n",
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

int main()
{
    using hbfsim::RequestStatus;
    using hbfsim::host_service::BackingStore;
    using hbfsim::host_service::CapacityBackingRouter;
    using hbfsim::host_service::CapacityBackingRouterTestAccess;

    static_assert(sizeof(CapacityBackingRouter::Token) ==
                  sizeof(std::uint64_t));

    constexpr std::size_t page_bytes = 4096;
    const auto suffix = std::to_string(static_cast<long long>(::getpid()));
    const auto first_path = std::filesystem::temp_directory_path() /
                            ("hbfsim-router-first-" + suffix);
    const auto second_path = std::filesystem::temp_directory_path() /
                             ("hbfsim-router-second-" + suffix);
    std::filesystem::remove(first_path);
    std::filesystem::remove(second_path);
    auto first_store = std::make_shared<BackingStore>(
        BackingStore::create_deterministic(first_path, 2 * page_bytes, 0x1111));
    auto second_store = std::make_shared<BackingStore>(
        BackingStore::create_deterministic(second_path, 2 * page_bytes, 0x2222));
    const auto first_page_zero = first_store->read_page(0, page_bytes);
    const auto second_page_zero = second_store->read_page(0, page_bytes);
    CHECK(first_page_zero != second_page_zero);

    std::binary_semaphore admitted{0};
    std::binary_semaphore release{0};
    std::binary_semaphore deactivation_marked{0};
    std::binary_semaphore deactivation_completed{0};
    bool block_admitted_operation = false;
    CapacityBackingRouter router([&] {
        if (block_admitted_operation) {
            admitted.release();
            release.acquire();
        }
    }, [&] {
        deactivation_marked.release();
    });
    CHECK(!router.activate(0));
    router.cancel(0);
    const auto first = router.stage(1, 0, 2, false, first_store);
    const auto second = router.stage(2, 2, 2, true, second_store);
    CHECK(first != 0);
    CHECK(second != 0);
    CHECK(router.stage(1, 10, 1, false, first_store) == 0);
    CHECK(router.stage(3, 1, 2, false, first_store) == 0);
    CHECK(router.read_page(0, page_bytes).status == RequestStatus::IoError);
    CHECK(router.activate(first));
    CHECK(router.activate(second));

    const auto routed_first = router.read_page(0, page_bytes);
    CHECK(routed_first.status == RequestStatus::Ready);
    CHECK(routed_first.range_id == 1);
    CHECK(routed_first.bytes == first_page_zero);
    const auto routed_second = router.read_page(2, page_bytes);
    CHECK(routed_second.status == RequestStatus::Ready);
    CHECK(routed_second.range_id == 2);
    CHECK(routed_second.bytes == second_page_zero);

    const std::vector<std::byte> changed(page_bytes, std::byte{0x5a});
    CHECK(router.write_page(0, page_bytes, changed) ==
          RequestStatus::Unsupported);
    CHECK(router.write_page(2, page_bytes, changed) == RequestStatus::Ready);
    CHECK(router.flush(2) == RequestStatus::Ready);
    CHECK(second_store->read_page(0, page_bytes) == changed);
    CHECK(router.read_page(99, page_bytes).status == RequestStatus::IoError);
    CHECK(router.write_page(99, page_bytes, changed) ==
          RequestStatus::IoError);

    CHECK(router.stage(0, 10, 1, false, first_store) == 0);
    CHECK(router.stage(3, 10, 0, false, first_store) == 0);
    CHECK(router.stage(3, 10, 1, false, {}) == 0);
    CHECK(router.stage(3, std::numeric_limits<std::uint64_t>::max(), 2,
                       false, first_store) == 0);
    CHECK(router.stage(1, 10, 1, false, first_store) == 0);
    CHECK(router.stage(3, 1, 2, false, first_store) == 0);
    const auto staged = router.stage(3, 10, 1, false, first_store);
    CHECK(staged != 0);
    CHECK(router.read_page(10, page_bytes).status == RequestStatus::IoError);
    router.cancel(staged);
    const auto replacement = router.stage(3, 10, 1, false, first_store);
    CHECK(replacement != 0);
    CHECK(replacement != staged);
    CHECK(!router.activate(staged));
    CHECK(router.activate(replacement));

    block_admitted_operation = true;
    auto reader = std::async(std::launch::async,
                             [&] { return router.read_page(2, page_bytes); });
    admitted.acquire();
    auto deactivator = std::async(std::launch::async, [&] {
        const auto status = router.deactivate(2);
        deactivation_completed.release();
        return status;
    });
    deactivation_marked.acquire();
    CHECK(router.read_page(2, page_bytes).status == RequestStatus::IoError);
    CHECK(!deactivation_completed.try_acquire());
    release.release();
    CHECK(reader.get().status == RequestStatus::Ready);
    deactivation_completed.acquire();
    CHECK(deactivator.get() == RequestStatus::Ready);
    block_admitted_operation = false;
    CHECK(router.read_page(2, page_bytes).status == RequestStatus::IoError);
    CHECK(router.deactivate(2) == RequestStatus::IoError);

    CapacityBackingRouter capacity;
    std::vector<CapacityBackingRouter::Token> tokens;
    for (std::uint32_t id = 1; id <= 64; ++id) {
        const auto token = capacity.stage(id, id - 1, 1, false, first_store);
        CHECK(token != 0);
        tokens.push_back(token);
    }
    CHECK(capacity.stage(65, 64, 1, false, first_store) == 0);

    CapacityBackingRouter exhausted;
    CapacityBackingRouterTestAccess::set_next_token(
        exhausted,
        std::numeric_limits<CapacityBackingRouter::Token>::max() - 1);
    const auto penultimate =
        exhausted.stage(1, 0, 1, false, first_store);
    CHECK(penultimate ==
          std::numeric_limits<CapacityBackingRouter::Token>::max() - 1);
    exhausted.cancel(penultimate);
    const auto last = exhausted.stage(2, 1, 1, false, first_store);
    CHECK(last == std::numeric_limits<CapacityBackingRouter::Token>::max());
    exhausted.cancel(last);
    CHECK(exhausted.stage(3, 2, 1, false, first_store) == 0);
    CHECK(!exhausted.activate(penultimate));
    CHECK(!exhausted.activate(last));

    std::filesystem::remove(first_path);
    std::filesystem::remove(second_path);
    return 0;
}
