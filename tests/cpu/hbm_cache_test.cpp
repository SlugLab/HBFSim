#include "../../src/cuda_runtime/hbm_cache.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <vector>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "HBM cache CHECK failed at line %d: %s\n", line,
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
    hbfsim::runtime::HbmCache cache({0x1000, 0x2000});
    CHECK(cache.publish(3, 0x1000));
    CHECK(cache.publish(4, 0x2000));
    CHECK(!cache.publish(5, 0x1000));
    CHECK(cache.resolve(3).value() == 0x1000);
    CHECK(cache.mark_dirty(3));
    CHECK(cache.dirty_pages() == 1);

    const auto first = cache.begin_eviction();
    CHECK(first.has_value());
    CHECK(first->logical_page == 3);
    CHECK(first->frame_address == 0x1000);
    CHECK(first->dirty);
    CHECK(!cache.resolve(3).has_value());
    CHECK(cache.dirty_pages() == 1);
    CHECK(!cache.publish(5, first->frame_address));
    CHECK(cache.cancel_eviction(*first));
    CHECK(cache.resolve(3).value() == 0x1000);
    CHECK(cache.dirty_pages() == 1);

    const auto clean = cache.begin_eviction();
    CHECK(clean.has_value());
    CHECK(clean->logical_page == 4);
    CHECK(!clean->dirty);
    CHECK(cache.complete_eviction(*clean));

    const auto retried = cache.begin_eviction();
    CHECK(retried.has_value());
    CHECK(retried->logical_page == 3);
    CHECK(retried->dirty);
    CHECK(cache.complete_eviction(*retried));
    CHECK(cache.dirty_pages() == 0);
    CHECK(!cache.complete_eviction(*retried));
    CHECK(cache.publish(5, retried->frame_address));
    CHECK(cache.resolve(5).value() == 0x1000);
    const auto second = cache.begin_eviction();
    CHECK(second.has_value());
    CHECK(second->logical_page == 5);
    CHECK(!second->dirty);
    CHECK(cache.complete_eviction(*second));

    hbfsim::runtime::HbmCache empty(std::vector<std::uint64_t>{});
    CHECK(!empty.begin_eviction().has_value());
    CHECK(!empty.publish(1, 0x1000));

    hbfsim::runtime::HbmCache reserved({0x3000, 0x4000});
    CHECK(reserved.publish(9, 0x3000));
    const auto in_writeback = reserved.begin_eviction();
    CHECK(in_writeback.has_value());
    CHECK(in_writeback->logical_page == 9);
    CHECK(!reserved.publish(9, 0x4000));
    CHECK(reserved.cancel_eviction(*in_writeback));
    CHECK(reserved.resolve(9).value() == 0x3000);

    hbfsim::runtime::HbmCache ranged({0x5000, 0x6000, 0x7000});
    CHECK(ranged.publish(10, 0x5000));
    CHECK(ranged.publish(20, 0x6000));
    CHECK(ranged.publish(30, 0x7000));
    CHECK(ranged.mark_dirty(10));
    CHECK(ranged.mark_dirty(20));
    const auto range_twenty = ranged.begin_eviction_in_range(20, 1, true);
    CHECK(range_twenty.has_value());
    CHECK(range_twenty->logical_page == 20);
    CHECK(range_twenty->dirty);
    CHECK(ranged.resolve(10).has_value());
    CHECK(ranged.complete_eviction(*range_twenty));
    CHECK(!ranged.complete_eviction(*range_twenty));
    CHECK(!ranged.begin_eviction_in_range(30, 1, true).has_value());
    CHECK(ranged.resolve(30).has_value());
    CHECK(!ranged.begin_eviction_in_range(0, 0, false).has_value());
    CHECK(!ranged
               .begin_eviction_in_range(
                   std::numeric_limits<std::uint64_t>::max(), 2, false)
               .has_value());
    const auto range_ten = ranged.begin_eviction_in_range(10, 1, true);
    CHECK(range_ten.has_value());
    CHECK(range_ten->logical_page == 10);
    CHECK(ranged.cancel_eviction(*range_ten));
    CHECK(ranged.resolve(10).value() == 0x5000);
    return 0;
}
