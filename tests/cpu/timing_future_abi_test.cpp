#include <cstdio>
#if !__has_include(<hbfsim/timing_future_abi.hpp>)
int main() { std::fputs("FAIL: versioned 64-byte timing-future contract is absent\n", stderr); return 1; }
#else
#include <hbfsim/timing_future_abi.hpp>
#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include <array>
#include <cstdlib>
#include <cstring>
#include <type_traits>
using namespace hbfsim::timing_future;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)
int main()
{
    static_assert(sizeof(DeviceTimingFutureV1)==64 && alignof(DeviceTimingFutureV1)==16);
    static_assert(sizeof(TimingFutureLaneMetadataV1)==32 && alignof(TimingFutureLaneMetadataV1)==8);
    static_assert(std::is_trivially_copyable_v<DeviceTimingFutureV1>);
    static_assert(sizeof(hbfsim::device::SharedControlHeader)==384);
    static_assert(sizeof(hbfsim::device::SharedRequestSlot)==128);
    static_assert(sizeof(hbfsim::device::HbfRequest)==64);
    static_assert(sizeof(hbfsim::device::ResolveResult)==16);
    const std::array<std::size_t,9> offsets{
        offsetof(DeviceTimingFutureV1,control_alias),offsetof(DeviceTimingFutureV1,control_generation),
        offsetof(DeviceTimingFutureV1,issue_ns),offsetof(DeviceTimingFutureV1,ready_ns),
        offsetof(DeviceTimingFutureV1,deadline_ns),offsetof(DeviceTimingFutureV1,original_address),
        offsetof(DeviceTimingFutureV1,reservation_id),offsetof(DeviceTimingFutureV1,state),
        offsetof(DeviceTimingFutureV1,status)};
    CHECK((offsets==std::array<std::size_t,9>{0,8,16,24,32,40,48,56,60}));
    CHECK(offsetof(TimingFutureLaneMetadataV1,abi_version)==0);
    CHECK(offsetof(TimingFutureLaneMetadataV1,struct_bytes)==4);
    CHECK(offsetof(TimingFutureLaneMetadataV1,instruction_id)==8);
    CHECK(offsetof(TimingFutureLaneMetadataV1,bytes)==12);
    CHECK(offsetof(TimingFutureLaneMetadataV1,group_mask)==16);
    CHECK(offsetof(TimingFutureLaneMetadataV1,group_leader)==20);
    CHECK(offsetof(TimingFutureLaneMetadataV1,reservation_id)==24);
    DeviceTimingFutureV1 value{11,22,33,44,55,66,77,State::Issued,0};
    std::array<unsigned char,64> wire{}; std::memcpy(wire.data(),&value,64);
    for (unsigned i=0;i<7;++i) { std::uint64_t field=0; std::memcpy(&field,wire.data()+8*i,8); CHECK(field==11*(i+1)); }
    std::uint32_t state=0; std::memcpy(&state,wire.data()+56,4); CHECK(state==2);
    CHECK(valid_requirements(ModuleRequirements{}));
    auto requirement=ModuleRequirements{}; requirement.token_bytes=80; CHECK(!valid_requirements(requirement));
    requirement=ModuleRequirements{}; requirement.metadata_bytes=24; CHECK(!valid_requirements(requirement));
    CHECK(!kUnitComplete);
    std::puts("PASS: exact token/metadata marshaling and unchanged shared ABI4");
}
#endif
