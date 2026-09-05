#include "ptx_analysis.hpp"
#include "ptx_ir.hpp"
#include <cstdio>
int main()
{
    const auto module=hbfsim::ptx::parse_module(R"PTX(.version 9.0
.target sm_120
.address_size 64
.visible .entry setup(.param .u64 input)
{
.reg .b64 %rd<4>;
.reg .b32 %r<4>;
ld.param.u64 %rd0, [input];
cvta.to.global.u64 %rd1, %rd0;
mov.u32 %r0, %tid.x;
mul.wide.u32 %rd2, %r0, 4;
add.u64 %rd3, %rd1, %rd2;
ld.global.u32 %r1, [%rd3];
add.u32 %r2, %r1, 1;
ret;
}
)PTX");
    const auto plan=hbfsim::ptx::analyze_futures(module.function("setup"));
    if(!plan.exact_safe()) {std::fputs("FAIL: actual parameter/address/thread setup rejected\n",stderr);return 1;}
    std::puts("PASS: supported real setup admitted with precise def-use");
}
