#include <hbfsim/api.h>

#include <cassert>

int main()
{
    assert(hbfsim_abi_version() == 1u);
    assert(hbfsim_get_stats(nullptr, nullptr) == HBFSIM_INVALID_ARGUMENT);
    return 0;
}
