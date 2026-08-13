#include <hbfsim/protocol.hpp>

#include <cassert>
#include <cstdint>
#include <type_traits>

static_assert(sizeof(hbfsim::HbfRequest) == 128);
static_assert(sizeof(hbfsim::HbfCompletion) == 64);
static_assert(sizeof(hbfsim::ControlHeader) == 64);
static_assert(sizeof(hbfsim::PageEntry) == 64);
static_assert(std::is_trivially_copyable_v<hbfsim::HbfRequest>);
static_assert(std::is_trivially_copyable_v<hbfsim::HbfCompletion>);

int main()
{
    assert(static_cast<std::uint32_t>(hbfsim::RequestStatus::DaemonLost) == 7u);

    hbfsim::SequenceRing<std::uint64_t, 8> ring;
    for (std::uint64_t value = 0; value < 8; ++value) {
        assert(ring.try_push(value));
    }
    assert(!ring.try_push(8));

    std::uint64_t value = 0;
    for (std::uint64_t expected = 0; expected < 8; ++expected) {
        assert(ring.try_pop(value));
        assert(value == expected);
    }
    assert(!ring.try_pop(value));

    for (std::uint64_t expected = 0; expected < 100000; ++expected) {
        assert(ring.try_push(expected));
        assert(ring.try_pop(value));
        assert(value == expected);
    }

    return 0;
}
