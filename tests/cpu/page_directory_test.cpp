#include <hbfsim/page_directory.hpp>

#include <cassert>

int main()
{
    hbfsim::PageDirectory directory;

    const auto miss = directory.lookup_or_reserve(42, 9);
    assert(miss.owner);
    const auto waiter = directory.lookup_or_reserve(42, 10);
    assert(!waiter.owner);
    assert(waiter.generation == miss.generation);

    assert(directory.publish(42, miss.generation, 3));
    const auto resolved = directory.resolve(42);
    assert(resolved.has_value());
    assert(resolved->frame == 3);
    assert(resolved->generation == miss.generation);

    assert(!directory.publish(42, miss.generation - 1, 4));
    assert(directory.evict(42, miss.generation));
    assert(!directory.resolve(42).has_value());

    const auto reused = directory.lookup_or_reserve(42, 11);
    assert(reused.owner);
    assert(reused.generation > miss.generation);
    assert(!directory.publish(42, miss.generation, 5));
    assert(directory.publish(42, reused.generation, 6));
    assert(directory.resolve(42)->frame == 6);
    assert(directory.mark_dirty(42, reused.generation));
    assert(directory.resolve(42)->state == hbfsim::PageState::Dirty);
    assert(!directory.evict(42, reused.generation));
    assert(directory.begin_writeback(42, reused.generation));
    assert(!directory.mark_dirty(42, reused.generation));
    assert(directory.evict(42, reused.generation));

    return 0;
}
