#include <hbfsim/api.h>

#include <cassert>

int main()
{
    assert(hbfsim_abi_version() == 1u);
    return 0;
}
