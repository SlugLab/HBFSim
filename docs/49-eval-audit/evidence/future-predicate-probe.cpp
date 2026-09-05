#include "src/ptxpass_hbf/future_transform.hpp"
#include <iostream>
int main() {
const char* ptx = R"(.version 8.8
.target sm_120
.address_size 64
.visible .entry probe(.param .u64 ptr)
{
.reg .b64 %rd1;
.reg .b32 %r<5>;
.reg .pred %p;
ld.param.u64 %rd1, [ptr];
ld.global.u32 %r1, [%rd1];
mov.u32 %r2, 1;
setp.eq.u32 %p, %r2, 0;
@%p add.u32 %r3, %r1, 1;
add.u32 %r4, %r1, 2;
ret;
}
)";
auto result=hbfsim::ptx::transform_futures(ptx,"probe");
std::cout << "modified=" << result.modified << " reject=" << result.rejection_reason << "\n";
std::cout << result.output_ptx;
}
