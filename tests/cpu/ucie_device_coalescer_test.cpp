#include <hbfsim/ucie/backing_registry.hpp>
#include <hbfsim/ucie/device_coalescer.hpp>

#include <cstdint>
#include <stdexcept>

namespace {
void require(bool value, const char* message)
{
    if (!value) throw std::runtime_error(message);
}
hbfsim::ucie::DevicePageKey key(std::uint64_t page, std::uint32_t chip=0)
{
    return {1,1,0,0,7,page,0,chip};
}
}

int main()
{
    using namespace hbfsim::ucie;
    BackingRegistry registry(1ULL<<36,4);
    registry.add({1,0,4096,88,0,9,0,0,7,true});
    registry.add({2,8192,4096,88,0,9,0,0,7,true});
    registry.add({3,16384,4096,99,4096,9,0,0,7,false});
    const auto original=registry.resolve_span(64,64,0,0,7);
    const auto alias=registry.resolve_span(8256,64,0,0,7);
    require(original && alias && original->canonical_id==alias->canonical_id &&
            original->canonical_offset==alias->canonical_offset,
            "explicit aliases must resolve to one canonical span");
    require(!registry.resolve_span(4096-64,128,0,0,7),
            "cross-region span was admitted");
    require(!registry.resolve_span(16384,64,0,0,7),
            "unreadable backing was admitted");
    require(!registry.resolve_span(64,64,0,0,8),
            "endpoint boundary was crossed");
    bool rejected=false;
    try { registry.add({4,128,4096,88,0,9,0,0,7,true}); }
    catch (const std::invalid_argument&) { rejected=true; }
    require(rejected && registry.size()==3,"overlap modified registry");
    registry.retire(1);
    require(!registry.resolve_span(64,64,0,0,7),"retired range still joins");
    registry.retire_generation(88,9,0,0,7);
    require(!registry.resolve_span(8256,64,0,0,7),
            "alias survived canonical generation retirement");
    rejected=false;
    try { registry.add({4,0,4096,88,0,9,0,0,7,true}); }
    catch (const std::invalid_argument&) { rejected=true; }
    require(rejected,"retired generation was revived");
    registry.add({4,0,4096,88,0,10,0,0,7,true});
    require(registry.resolve_span(64,64,0,0,7)->generation==10,
            "new generation was not resolved");

    DeviceReadCoalescer coal(6,16);
    const auto first=coal.arrive(1,key(0));
    const auto second_join=coal.arrive(2,key(0));
    require(first && second_join && second_join->token==first->token &&
            !second_join->buffer_hit && coal.group_count()==1,
            "same-page in-flight reads did not coalesce");
    const auto starts=coal.take_submittable();
    require(starts.size()==1 && starts[0].token==first->token &&
            coal.occupied_slots(0,0)==1,"sense was not slot-reserved");
    require(coal.arrive(3,key(0))->token==first->token,
            "read failed to join submitted in-flight group");
    const auto one=coal.complete(first->token,true);
    require(one.success && one.waiters.size()==3,
            "coalesced media completion lost individual waiters");
    const auto second=coal.arrive(4,key(1));
    require(second && coal.take_submittable().size()==1,
            "second bank slot not allocated");
    const auto third=coal.arrive(5,key(2));
    require(third && coal.take_submittable().empty() &&
            coal.occupied_slots(0,0)==2,
            "third page sensed despite both physical slots pinned");
    const auto other=coal.arrive(6,key(0,1));
    const auto independent=coal.take_submittable();
    require(other && independent.size()==1 && independent[0].token==other->token &&
            coal.occupied_slots(0,1)==1,"another bank was blocked");
    coal.consume(1);
    coal.consume(2);
    require(coal.take_submittable().empty(),
            "slot was replaced before last R consumption");
    coal.consume(3);
    const auto resumed=coal.take_submittable();
    require(resumed.size()==1 && resumed[0].token==third->token,
            "queued third page did not start after true R consume");
    require(coal.arrive(7,key(0)) && coal.group_count()==4,
            "post-completion read improperly joined retired group");
    require(!coal.detach(7),"queued group unexpectedly requires callback");
    require(!coal.complete(second->token,false).success,
            "failure result was misclassified");
    require(coal.complete(third->token,true).success &&
            coal.complete(other->token,true).success,
            "ready media result was lost");
    coal.consume(5);
    coal.consume(6);
    require(coal.group_count()==0 && coal.waiter_count()==0 &&
            coal.occupied_slots(0,0)==0 && coal.occupied_slots(0,1)==0,
            "coalescer did not drain resources");
    require(coal.counters().media_misses==5 &&
            coal.counters().inflight_joins==2 &&
            coal.counters().buffer_hits==0,
            "cache-off accounting mismatch");

    DeviceReadCoalescer cached(3,4,true,1,2);
    const auto cold=cached.arrive(10,key(7));
    require(cold && !cold->buffer_hit &&
            cached.take_submittable().size()==1,"cache fixture failed to start");
    require(cached.complete(cold->token,true).success,
            "cache fixture failed to complete");
    const auto pinned_hit=cached.arrive(11,key(7));
    require(pinned_hit && pinned_hit->buffer_hit &&
            pinned_hit->token==cold->token,
            "ready pinned page did not hit the buffer");
    cached.consume(10);
    cached.consume(11);
    require(cached.occupied_slots(0,0)==1,
            "completed page did not remain in finite cache slot");
    const auto later_hit=cached.arrive(12,key(7));
    require(later_hit && later_hit->buffer_hit &&
            cached.take_submittable().empty(),
            "cross-completion buffer hit issued a second sense");
    cached.consume(12);
    require(cached.counters().media_misses==1 &&
            cached.counters().buffer_hits==2,
            "cache-hit accounting mismatch");
    rejected=false;
    try { (void)cached.arrive(13,key(8,2)); }
    catch (const std::invalid_argument&) { rejected=true; }
    require(rejected,"out-of-layout native bank admitted");
    const auto b=cached.arrive(14,key(8));
    require(b && cached.take_submittable().size()==1,
            "second distinct page did not use free buffer slot");
    require(cached.complete(b->token,true).success,
            "second page media failed");
    cached.consume(14);
    require(cached.occupied_slots(0,0)==2,
            "two completed pages did not occupy two real slots");
    const auto a_hit=cached.arrive(15,key(7));
    const auto b_hit=cached.arrive(16,key(8));
    require(a_hit && b_hit && a_hit->buffer_hit && b_hit->buffer_hit,
            "one of two cached pages was prematurely evicted");
    cached.consume(15);
    cached.consume(16);
    const auto c=cached.arrive(17,key(9));
    require(c && cached.take_submittable().size()==1,
            "third page failed to replace an unpinned cache entry");
    require(cached.complete(c->token,true).success,
            "replacement page media failed");
    cached.consume(17);
    const auto b_still_hit=cached.arrive(18,key(8));
    require(b_still_hit && b_still_hit->buffer_hit,
            "third page replaced both cache entries");
    cached.consume(18);
    const auto a_miss=cached.arrive(19,key(7));
    require(a_miss && !a_miss->buffer_hit,
            "specified lower-slot replacement did not evict A");
    require(!cached.detach(19),"queued cache miss unexpectedly requires callback");
}
