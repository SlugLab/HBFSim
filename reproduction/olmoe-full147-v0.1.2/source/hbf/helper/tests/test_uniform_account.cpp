#include "uniform_account_logic.hpp"
#include "hbf_device.cuh"
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <map>
#include <utility>
#include <vector>
using U=std::uint64_t;
using hbfsim::accounting_uniform::Outcome;
static unsigned checks=0;
void check(bool v) { ++checks; if(!v) { std::cerr<<"failed check "<<checks<<"\n"; std::abort(); } }

// Independent scalar oracle: widened arithmetic, no candidate checked_sum/CAS.
struct Ledger { U field=0, overflow=0; };
void reference_add(Ledger& x,U v) {
    if(!v)return;
    unsigned __int128 exact=static_cast<unsigned __int128>(x.field)+v;
    if(exact>static_cast<unsigned __int128>(UINT64_MAX)) {x.field=UINT64_MAX;++x.overflow;}
    else x.field=static_cast<U>(exact);
}
// Actual pinned scalar body, not the independent __int128 oracle.
static hbfsim::device::AccessAccountingCounters __hbfsim_access_accounting_counters{};
U system_acquire(U* p) { return *p; }
bool system_compare_exchange(U* p,U& expected,U desired) {
    if(*p!=expected) {expected=*p;return false;}
    *p=desired;return true;
}
U system_fetch_add(U* p,U value) {U old=*p;*p+=value;return old;}
#include "scalar_candidate.inc"
struct Atomic {
    Ledger* ledger;
    mutable unsigned loads=0, attempts=0, successes=0;
    mutable std::vector<U> interference;
    U load()const {++loads;return ledger->field;}
    bool cas(U& expected,U desired)const {
        ++attempts;
        if(!interference.empty()) {ledger->field=interference.front();interference.erase(interference.begin());expected=ledger->field;return false;}
        if(expected!=ledger->field) {expected=ledger->field;return false;}
        ledger->field=desired;++successes;return true;
    }
};
void candidate_group(Ledger& l,U v,unsigned n,Atomic& a) {
    if(v==0)return;
    U sum=0;
    if(n==1 || !hbfsim::accounting_uniform::checked_sum(v,n,sum) ||
       hbfsim::accounting_uniform::try_commit(a,sum)==Outcome::fallback)
        {
            __hbfsim_access_accounting_counters.counter_overflow=l.overflow;
            for(unsigned i=0;i<n;++i)scalar_candidate(&l.field,v);
            l.overflow=__hbfsim_access_accounting_counters.counter_overflow;
        }
}
void exhaustive_and_extremes() {
    std::vector<U> initial={0,1,7,UINT64_MAX,UINT64_MAX-1,UINT64_MAX-31,UINT64_MAX/2};
    std::vector<U> values={0,1,2,7,UINT64_MAX,UINT64_MAX/2,1ULL<<32,1ULL<<63};
    for(U start:initial)for(U value:values)for(unsigned n=1;n<=32;++n) {
        Ledger expected{start,0},actual{start,0};
        for(unsigned i=0;i<n;++i)reference_add(expected,value);
        Atomic a{&actual};candidate_group(actual,value,n,a);
        check(actual.field==expected.field);check(actual.overflow==expected.overflow);
        check(a.successes<=1);
    }
    U sum=123;
    check(!hbfsim::accounting_uniform::checked_sum(1,0,sum));check(sum==123);
    check(!hbfsim::accounting_uniform::checked_sum(1,33,sum));check(sum==123);
    for(U initial=0;initial<12;++initial)for(U value=0;value<12;++value)for(unsigned n=1;n<=32;++n) {
        Ledger x{initial,0},expected=x;for(unsigned k=0;k<n;++k)reference_add(expected,value);
        Atomic a{&x};candidate_group(x,value,n,a);check(x.field==expected.field && x.overflow==expected.overflow);
    }
}
void cas_interference_and_saturation() {
    {Ledger x{0,0};Atomic a{&x};a.interference={5,9};candidate_group(x,2,4,a);
     check(x.field==17 && x.overflow==0 && a.successes==1 && a.attempts==3);}
    {Ledger x{0,0};Atomic a{&x};a.interference={UINT64_MAX};candidate_group(x,1,4,a);
     check(a.successes==0 && a.attempts==1);check(x.field==UINT64_MAX && x.overflow==4);}
    {Ledger x{UINT64_MAX-4,0};Atomic a{&x};candidate_group(x,1,4,a);
     check(x.field==UINT64_MAX && x.overflow==0 && a.successes==1);}
    {Ledger x{UINT64_MAX-3,0};Atomic a{&x};candidate_group(x,1,4,a);
     check(x.field==UINT64_MAX && x.overflow==1 && a.successes==0);}
    {Ledger x{UINT64_MAX,UINT64_MAX};Atomic a{&x};candidate_group(x,1,2,a);
     check(x.overflow==1);check(a.successes==0);}
    {Ledger x{23,0};Atomic a{&x};candidate_group(x,0,32,a);
     check(a.loads==0 && a.attempts==0 && x.field==23);}
}
void partitions_and_sparse_cohorts() {
    // Independent map oracle for full64-bit (field,value); no 32-round reduction.
    struct Lane{unsigned lane;U field,value;};
    std::vector<Lane> lanes={{0,7,1},{3,7,1},{4,7+(1ULL<<32),1},{9,7,1+(1ULL<<32)},{31,7,1}};
    std::map<std::pair<U,U>,unsigned> masks;
    for(auto l:lanes)masks[{l.field,l.value}]|=(1U<<l.lane);
    check(masks.size()==3);check(masks[{7,1}]==((1U<<0)|(1U<<3)|(1U<<31)));
    std::map<U,Ledger> actual,expected;
    for(auto l:lanes)reference_add(expected[l.field],l.value);
    for(auto [key,mask]:masks){auto [field,value]=key;unsigned n=__builtin_popcount(mask);Atomic a{&actual[field]};candidate_group(actual[field],value,n,a);}
    for(auto [field,l]:expected)check(actual[field].field==l.field && actual[field].overflow==l.overflow);
    // Distinct module field addresses stay distinct even when offsets agree.
    Ledger moduleA{},moduleB{};Atomic a{&moduleA},b{&moduleB};candidate_group(moduleA,1,3,a);candidate_group(moduleB,1,5,b);
    check(moduleA.field==3 && moduleB.field==5);
    // Epoch reset is only represented between fully completed groups.
    moduleA={};candidate_group(moduleA,1,2,a);check(moduleA.field==2 && moduleB.field==5);
}
int main() {
    using namespace hbfsim::device;
    static_assert(sizeof(DeviceTimingState)==128);
    static_assert(sizeof(AccessAccountingConfig)==32);
    static_assert(sizeof(AccessAccountingCounters)==176);
    static_assert(offsetof(AccessAccountingConfig,request_epoch)==24);
    static_assert(offsetof(DeviceTimingState,fast_total_ns)==96);
    static_assert(offsetof(DeviceTimingState,reference_post_target_loop_wait_ns)==120);
    static_assert(offsetof(AccessAccountingCounters,supported_accesses)==0);
    static_assert(offsetof(AccessAccountingCounters,counter_overflow)==168);
    exhaustive_and_extremes();cas_interference_and_saturation();partitions_and_sparse_cohorts();
    std::cout<<"{\"status\":\"PASS\",\"checks\":"<<checks<<",\"ABI\":{\"state\":128,\"config\":32,\"counters\":176},\"GPU\":0,\"scope\":\"shared pure candidate logic, independent widened scalar oracle; no GPU collective proof\"}\n";
}
