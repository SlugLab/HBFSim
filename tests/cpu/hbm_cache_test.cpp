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

    // A frame handed out by resolve() can still be evicted before the GPU has
    // finished reading it. This test MEASURES that window rather than closing
    // it, because closing it needs a device-protocol change.
    //
    // What happens: resolve() sets a second-chance bit, and begin_eviction()
    // scans up to frames_.size() * 2 entries, so it clears that bit on one
    // pass and can evict the same frame on the next -- inside one call, with
    // no wall-clock time in between. Completing that eviction reassigns the
    // frame to a different logical page while the read is outstanding, which
    // produces wrong bytes rather than a wrong number. That makes this the
    // most severe item on the current defect list.
    //
    // Why a within-sweep reprieve does NOT fix it, having been tried and
    // reverted: `referenced` is set both by publish() and by resolve(), so
    // refusing to evict anything this sweep reprieved also breaks the ordinary
    // case where every frame is referenced and the clock legitimately needs a
    // second pass to find any victim at all. The test above at line 38 pins
    // that case. Separating the two would need a marker set only by resolve()
    // and cleared when the access retires -- and the protocol carries no
    // retirement message: SharedCompletionSlot reports that the host finished
    // serving a page, not that the GPU finished consuming it. A marker with
    // nothing to clear it would pin every frame forever.
    //
    // So the fix is: add an access-retired message to the device protocol,
    // then gate eviction on it. That is its own change. This test exists so
    // the window is measured and discoverable, and so that whoever closes it
    // has to come here and update the expectation.
    {
        hbfsim::runtime::HbmCache one_frame(std::vector<std::uint64_t>{0x9000});
        CHECK(one_frame.publish(77, 0x9000));
        // The GPU is told to read frame 0x9000 for logical page 77.
        CHECK(one_frame.resolve(77).value() == 0x9000);
        // While that read is still in flight, the host finds a victim -- and
        // the victim is the very frame the GPU is reading.
        const auto victim = one_frame.begin_eviction();
        CHECK(victim.has_value());
        CHECK(victim->logical_page == 77);
        CHECK(victim->frame_address == 0x9000);
        CHECK(one_frame.cancel_eviction(*victim));
    }

    return 0;
}
