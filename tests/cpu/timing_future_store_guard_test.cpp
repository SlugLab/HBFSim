#include "hbf_device.cuh"
#include <cstdio>
#include <cstdint>
using namespace hbfsim::device;
static bool safe(const SharedRangeRecord* ranges,std::uint32_t count,std::uint64_t address,std::uint32_t bytes) {
#ifdef HBFSIM_FUTURE_STORE_SPAN_GUARD
 return timing_future_native_store_span(ranges,count,address,bytes);
#else
 // Existing resolver classifies the starting address only. This is the
 // semantic RED for a store beginning outside and crossing into HBF.
 (void)bytes;
 for(unsigned i=0;i<count;++i)if(address>=ranges[i].base && address-ranges[i].base<ranges[i].length)return false;
 return true;
#endif
}
int main() {
 SharedRangeRecord ranges[2]{};ranges[0].base=0x1000;ranges[0].length=0x1000;
 ranges[1].base=0x8000;ranges[1].length=0x1000;
 struct Case {std::uint64_t address;unsigned bytes;bool allowed;};
 const Case cases[]={{0xffc,4,true},{0xffe,4,false},{0x1000,4,false},{0x1ffe,4,false},{0x2000,4,true},{0x7ffe,4,false},{0x9000,8,true},{UINT64_MAX-2,4,false},{0,4,false},{0x4000,3,false}};
 unsigned failed=0;
 for(auto c:cases)if(safe(ranges,2,c.address,c.bytes)!=c.allowed){++failed;std::printf("FAIL span address=%llx bytes=%u expected=%d\n",(unsigned long long)c.address,c.bytes,c.allowed);}
 if(safe(ranges,kRangeCapacity+1,0x4000,4)){++failed;std::puts("FAIL excessive range count");}
 ranges[1].base=UINT64_MAX-4;ranges[1].length=8;
 if(safe(ranges,2,0x4000,4)){++failed;std::puts("FAIL malformed frozen range");}
 return failed?1:0;
}
