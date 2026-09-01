#include <hbfsim/api.h>

#include <cassert>

int main()
{
    assert(hbfsim_abi_version() == 1u);
    assert(hbfsim_get_stats(nullptr, nullptr) == HBFSIM_INVALID_ARGUMENT);
    hbfsim_stats_v2 stats{};
    assert(hbfsim_get_stats_v2(nullptr, &stats, sizeof(stats)) ==
           HBFSIM_INVALID_ARGUMENT);
    assert(hbfsim_get_stats_v2(nullptr, nullptr, 0) ==
           HBFSIM_INVALID_ARGUMENT);
    return 0;
}
