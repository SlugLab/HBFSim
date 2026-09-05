#include <hbfsim/timing_binding.hpp>
#include <hbfsim/timing_future_abi.hpp>
#include <hbfsim/launch_gate_abi.hpp>
#include <cstdio>
#include <cstdlib>
using namespace hbfsim;
using namespace hbfsim::timing_future;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)
struct Fixture {
    FutureInitialization result{FutureInitialization::Ready};
    unsigned calls=0, clears=0;
    std::uint64_t alias=0,generation=0;
};
bool sync_init(ModuleHandle,std::uintptr_t,std::uint64_t,void*) noexcept { return true; }
FutureInitialization future_init(ModuleHandle,std::uintptr_t alias,std::uint64_t generation,
    const Capabilities& caps,const ModuleRequirements& req,void* opaque) noexcept
{
    auto& f=*static_cast<Fixture*>(opaque);++f.calls;if(!alias)++f.clears;
    f.alias=alias;f.generation=generation;
    if(f.result!=FutureInitialization::Ready) return f.result;
    return alias && supports(caps,req) ? FutureInitialization::Ready : FutureInitialization::Unavailable;
}
int main()
{
    static_assert(offsetof(LaunchGateApiV4,activate_with_capabilities)==sizeof(LaunchGateApiV3));
    static_assert(offsetof(LaunchGateApiV4,register_range_with_policy)==offsetof(LaunchGateApiV3,register_range_with_policy));
    for(bool before : {false,true}) {
        TimingBindingRegistry r;Fixture f;std::uint64_t gen=0;
        if(before)CHECK(r.add_future_module(1,100,0,ModuleRequirements{},future_init,&f));
        CHECK(r.activate_with_capabilities(10,200,100,0,{.bits=kFastScalarTiming},sync_init,nullptr,gen));
        if(!before)CHECK(r.add_future_module(1,100,0,ModuleRequirements{},future_init,&f));
        CHECK(r.ready(1,100,0,gen));CHECK(!r.ready(1,100,1,gen));CHECK(!r.ready(1,101,0,gen));
        CHECK(!r.ready(1,100,0,gen+1));CHECK(r.quiesce(10,gen));CHECK(!r.ready(1,100,0,gen));
        CHECK(r.invalidate(10,gen,sync_init,nullptr));CHECK(r.finish_retire(10,gen));
        CHECK(f.alias==0 && f.generation==0 && f.clears>0);
    }
    for(auto caps : {Capabilities{},derive_capabilities(0,0,1,10,20,100,1000),
            derive_capabilities(2,0,1,10,20,100,1000),derive_capabilities(1,1,1,10,20,100,1000),
            derive_capabilities(1,0,2,10,20,100,1000)}) {
        TimingBindingRegistry r;Fixture f;std::uint64_t gen=0;
        CHECK(r.add_future_module(1,100,0,ModuleRequirements{},future_init,&f));
        CHECK(r.activate_with_capabilities(10,200,100,0,caps,sync_init,nullptr,gen));
        CHECK(!r.ready(1,100,0,gen));CHECK(r.quiesce(10,gen));
        const auto clears=f.clears;
        CHECK(r.invalidate(10,gen,sync_init,nullptr));CHECK(f.clears>clears);
        CHECK(r.finish_retire(10,gen));
    }
    {
        TimingBindingRegistry r;Fixture f;std::uint64_t gen=0;
        CHECK(r.add_future_module(1,100,0,ModuleRequirements{},future_init,&f));
        CHECK(r.activate(10,200,100,0,sync_init,nullptr,gen));CHECK(!r.ready(1,100,0,gen));
    }
    {
        TimingBindingRegistry r;Fixture f;std::uint64_t gen=0;
        CHECK(r.add_future_module(1,100,0,ModuleRequirements{},future_init,&f));
        f.result=FutureInitialization::Quarantine;
        CHECK(!r.activate_with_capabilities(10,200,100,0,{.bits=kFastScalarTiming},sync_init,nullptr,gen));
        CHECK(gen!=0);CHECK(r.active_context(100));CHECK(!r.can_activate());
        CHECK(!r.ready(1,100,0,gen));CHECK(!r.quiesce(10,gen));
    }
    {
        TimingBindingRegistry r;Fixture f;std::uint64_t gen=0;
        CHECK(r.activate_with_capabilities(10,200,100,0,{.bits=kFastScalarTiming},sync_init,nullptr,gen));
        f.result=FutureInitialization::Quarantine;
        CHECK(!r.add_future_module(1,100,0,ModuleRequirements{},future_init,&f));
        CHECK(!r.can_activate());CHECK(!r.ready_for_active(1,100,0));
        r.erase(1);CHECK(!r.has_future_modules() && r.future_unit_observed());
        const auto calls=f.calls;
        CHECK(!r.add_future_module(2,100,0,ModuleRequirements{},future_init,&f));
        CHECK(f.calls==calls && r.future_module(2) && r.has_future_modules());
        CHECK(!r.ready_for_active(2,100,0));
    }
    {
        TimingBindingRegistry r;Fixture f;std::uint64_t gen=0;
        auto req=ModuleRequirements{};req.metadata_version=2;
        CHECK(!r.add_future_module(1,100,0,req,future_init,&f));CHECK(f.calls==0);
        CHECK(!r.activate_with_capabilities(10,200,100,0,{.abi_version=2},sync_init,nullptr,gen));
        CHECK(gen==0 && r.can_activate());
    }
    std::puts("PASS: capability binding, legacy refusal and retained quarantine ownership");
}
