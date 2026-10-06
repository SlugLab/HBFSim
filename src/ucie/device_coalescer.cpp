#include <hbfsim/ucie/device_coalescer.hpp>

#include <algorithm>
#include <cstdlib>
#include <string_view>
#include <utility>
#include <limits>
#include <stdexcept>

namespace hbfsim::ucie {

DeviceReadCoalescer::DeviceReadCoalescer(std::size_t max_groups,
                                         std::size_t max_waiters,
                                         bool buffer_cache,
                                         std::uint32_t max_channels,
                                         std::uint32_t max_chips_per_channel,
                                         bool inflight_coalescing)
    : max_groups_(max_groups),max_waiters_(max_waiters),
      buffer_cache_(buffer_cache),inflight_coalescing_(inflight_coalescing),
      max_channels_(max_channels),
      max_chips_per_channel_(max_chips_per_channel)
{
    if (const auto* flag=std::getenv("HBFSIM_UCIE_COALESCER_EMPTY_WAIT")) {
        const std::string_view value(flag);
        if (value!="0" && value!="1")
            throw std::invalid_argument("invalid coalescer empty-wait flag");
        empty_wait_fastpath_=value=="1";
    }
    if (!max_groups_ || !max_waiters_ || max_groups_>max_waiters_ ||
        max_channels_==0 || max_channels_>16 ||
        max_chips_per_channel_==0 || max_chips_per_channel_>256)
        throw std::invalid_argument("invalid coalescer bounds");
}

void DeviceReadCoalescer::swap_state(DeviceReadCoalescer& other) noexcept
{
    using std::swap;
    swap(max_groups_,other.max_groups_);swap(max_waiters_,other.max_waiters_);
    swap(buffer_cache_,other.buffer_cache_);swap(inflight_coalescing_,other.inflight_coalescing_);
    swap(max_channels_,other.max_channels_);swap(max_chips_per_channel_,other.max_chips_per_channel_);
    swap(next_token_,other.next_token_);swap(groups_,other.groups_);swap(joinable_,other.joinable_);
    swap(ready_,other.ready_);swap(waiter_to_group_,other.waiter_to_group_);swap(banks_,other.banks_);
    swap(counters_,other.counters_);swap(waiting_group_count_,other.waiting_group_count_);
    swap(empty_wait_fastpath_,other.empty_wait_fastpath_);
}
DeviceReadCoalescer& DeviceReadCoalescer::operator=(const DeviceReadCoalescer& other)
{
    if (this!=&other) {DeviceReadCoalescer copy(other);swap_state(copy);} return *this;
}
DeviceReadCoalescer::DeviceReadCoalescer(DeviceReadCoalescer&& other) noexcept
    : max_groups_(other.max_groups_),max_waiters_(other.max_waiters_),
      buffer_cache_(other.buffer_cache_),inflight_coalescing_(other.inflight_coalescing_),
      max_channels_(other.max_channels_),max_chips_per_channel_(other.max_chips_per_channel_)
{
    swap_state(other);
}
DeviceReadCoalescer& DeviceReadCoalescer::operator=(DeviceReadCoalescer&& other) noexcept
{
    if (this!=&other) {DeviceReadCoalescer moved(std::move(other));swap_state(moved);} return *this;
}
std::optional<DeviceArrival> DeviceReadCoalescer::arrive(
    std::uint64_t waiter_id, const DevicePageKey& key)
{
    if (waiter_id==0 || waiter_to_group_.contains(waiter_id))
        throw std::invalid_argument("duplicate or zero waiter ID");
    if (key.native_channel>=max_channels_ ||
        key.native_chip>=max_chips_per_channel_)
        throw std::invalid_argument("bank outside validated layout");
    if (waiter_to_group_.size()>=max_waiters_) return std::nullopt;
    const auto found=inflight_coalescing_ ? joinable_.find(key) : joinable_.end();
    if (found!=joinable_.end()) {
        groups_.at(found->second).waiters.insert(waiter_id);
        waiter_to_group_.emplace(waiter_id,found->second);
        counters_.peak_waiters=std::max<std::uint64_t>(
            counters_.peak_waiters,waiter_to_group_.size());
        ++counters_.inflight_joins;
        return DeviceArrival{found->second,false};
    }
    if (buffer_cache_) {
        const auto ready=ready_.find(key);
        if (ready!=ready_.end()) {
            groups_.at(ready->second).waiters.insert(waiter_id);
            waiter_to_group_.emplace(waiter_id,ready->second);
            counters_.peak_waiters=std::max<std::uint64_t>(
                counters_.peak_waiters,waiter_to_group_.size());
            ++counters_.buffer_hits;
            return DeviceArrival{ready->second,true};
        }
    }
    if (groups_.size()>=max_groups_) return std::nullopt;
    if (next_token_==std::numeric_limits<std::uint64_t>::max())
        throw std::overflow_error("group token exhausted");
    const auto token=next_token_++;
    Group group;
    group.key=key;
    group.waiters.insert(waiter_id);
    if (buffer_cache_) {
        auto& bank=banks_[{key.native_channel,key.native_chip}];
        for (std::size_t i=0;i<bank.slots.size();++i) {
            auto& slot=bank.slots[i];
            if (slot.token==0 && slot.cached_key==key) {
                group.phase=Phase::Ready;
                group.slot=static_cast<int>(i);
                group.success=true;
                slot.token=token;
                slot.cached_key.reset();
                groups_.emplace(token,std::move(group));
                ready_.emplace(key,token);
                waiter_to_group_.emplace(waiter_id,token);
                counters_.peak_groups=std::max<std::uint64_t>(
                    counters_.peak_groups,groups_.size());
                counters_.peak_waiters=std::max<std::uint64_t>(
                    counters_.peak_waiters,waiter_to_group_.size());
                ++counters_.buffer_hits;
                return DeviceArrival{token,true};
            }
        }
    }
    groups_.emplace(token,std::move(group));
    if (inflight_coalescing_) joinable_.emplace(key,token);
    waiter_to_group_.emplace(waiter_id,token);
    counters_.peak_groups=std::max<std::uint64_t>(
        counters_.peak_groups,groups_.size());
    counters_.peak_waiters=std::max<std::uint64_t>(
        counters_.peak_waiters,waiter_to_group_.size());
    if (waiting_group_count_==std::numeric_limits<std::size_t>::max())
        throw std::overflow_error("coalescer waiting-group count exhausted");
    banks_[{key.native_channel,key.native_chip}].waiting.push_back(token);
    ++waiting_group_count_;
    ++counters_.media_misses;
    return DeviceArrival{token,false};
}

std::vector<DeviceMediaStart> DeviceReadCoalescer::take_submittable()
{
    std::vector<DeviceMediaStart> result;
    if (empty_wait_fastpath_ && waiting_group_count_==0) return result;
    for (auto& [bank_id,bank]:banks_) {
        (void)bank_id;
        while (!bank.waiting.empty()) {
            // Prefer a genuinely empty slot. When both contain unpinned cache
            // entries, choose the lower slot index (scenario FIFO-like rule).
            auto free=std::find_if(bank.slots.begin(),bank.slots.end(),
                [](const Slot& slot){return slot.token==0 && !slot.cached_key;});
            if (free==bank.slots.end())
                free=std::find_if(bank.slots.begin(),bank.slots.end(),
                    [](const Slot& slot){return slot.token==0;});
            if (free==bank.slots.end()) break;
            const auto token=bank.waiting.front();
            if (waiting_group_count_==0) throw std::logic_error("coalescer waiting-group count underflow");
            bank.waiting.pop_front();
            --waiting_group_count_;
            auto& group=groups_.at(token);
            if (group.phase!=Phase::Waiting || group.waiters.empty())
                throw std::logic_error("invalid pre-media bank FIFO state");
            group.phase=Phase::Submitted;
            group.slot=static_cast<int>(free-bank.slots.begin());
            free->token=token;
            free->cached_key.reset(); // evict only an unpinned completed page
            counters_.peak_slots_in_one_bank=std::max<std::uint64_t>(
                counters_.peak_slots_in_one_bank,
                std::count_if(bank.slots.begin(),bank.slots.end(),
                    [](const Slot& slot){return slot.token!=0 || slot.cached_key;}));
            result.push_back(DeviceMediaStart{token,group.key});
        }
    }
    return result;
}

DeviceMediaReady DeviceReadCoalescer::complete(std::uint64_t token, bool success)
{
    auto it=groups_.find(token);
    if (it==groups_.end() || it->second.phase!=Phase::Submitted)
        throw std::invalid_argument("unknown or duplicate media completion");
    auto& group=it->second;
    const auto join=joinable_.find(group.key);
    if (join!=joinable_.end() && join->second==token) joinable_.erase(join);
    group.phase=Phase::Ready;
    group.success=success;
    if (success && buffer_cache_) ready_[group.key]=token;
    DeviceMediaReady result{token,{},success};
    result.waiters.assign(group.waiters.begin(),group.waiters.end());
    if (!success) {
        // No R payload can refer to a failed media read. The frontend retains
        // software error ordering; native page storage is released now.
        for (const auto waiter:group.waiters) waiter_to_group_.erase(waiter);
        group.waiters.clear();
    }
    release_if_unreferenced(token);
    return result;
}

bool DeviceReadCoalescer::detach(std::uint64_t waiter_id)
{
    const auto wit=waiter_to_group_.find(waiter_id);
    if (wit==waiter_to_group_.end()) throw std::invalid_argument("unknown waiter");
    const auto token=wit->second;
    auto& group=groups_.at(token);
    const bool submitted=group.phase==Phase::Submitted;
    group.waiters.erase(waiter_id);
    waiter_to_group_.erase(wit);
    if (group.waiters.empty() && group.phase==Phase::Waiting) {
        const BankId bank_id{group.key.native_channel,group.key.native_chip};
        auto& queue=banks_.at(bank_id).waiting;
        const auto pos=std::find(queue.begin(),queue.end(),token);
        if (pos==queue.end()) throw std::logic_error("queued group missing from bank FIFO");
        if (waiting_group_count_==0) throw std::logic_error("coalescer waiting-group count underflow");
        queue.erase(pos);
        --waiting_group_count_;
        const auto join=joinable_.find(group.key);
        if (join!=joinable_.end() && join->second==token) joinable_.erase(join);
        groups_.erase(token);
        prune_empty_bank(bank_id);
    } else if (group.phase==Phase::Ready) {
        // Valid only before the caller has queued a physical R. The caller
        // retains ownership once R enqueue has occurred.
        release_if_unreferenced(token);
    }
    return submitted;
}

void DeviceReadCoalescer::consume(std::uint64_t waiter_id)
{
    const auto wit=waiter_to_group_.find(waiter_id);
    if (wit==waiter_to_group_.end()) throw std::invalid_argument("unknown waiter");
    const auto token=wit->second;
    auto& group=groups_.at(token);
    if (group.phase!=Phase::Ready)
        throw std::logic_error("media page not ready for response consume");
    group.waiters.erase(waiter_id);
    waiter_to_group_.erase(wit);
    release_if_unreferenced(token);
}

void DeviceReadCoalescer::release_if_unreferenced(std::uint64_t token)
{
    auto it=groups_.find(token);
    if (it==groups_.end() || it->second.phase!=Phase::Ready ||
        !it->second.waiters.empty()) return;
    auto& group=it->second;
    const BankId bank_id{group.key.native_channel,group.key.native_chip};
    if (buffer_cache_ && group.success) {
        const auto ready=ready_.find(group.key);
        if (ready!=ready_.end() && ready->second==token) ready_.erase(ready);
    }
    auto& slot=banks_.at(bank_id)
                  .slots.at(static_cast<std::size_t>(group.slot));
    if (slot.token!=token) throw std::logic_error("bank slot owner mismatch");
    slot.token=0;
    if (buffer_cache_ && group.success) slot.cached_key=group.key;
    else slot.cached_key.reset();
    groups_.erase(it);
    prune_empty_bank(bank_id);
}

void DeviceReadCoalescer::prune_empty_bank(const BankId& id)
{
    const auto it=banks_.find(id);
    if (it==banks_.end() || !it->second.waiting.empty()) return;
    if (std::all_of(it->second.slots.begin(),it->second.slots.end(),
                    [](const Slot& slot){return slot.token==0 && !slot.cached_key;}))
        banks_.erase(it);
}

std::size_t DeviceReadCoalescer::occupied_slots(std::uint32_t channel,
                                                 std::uint32_t chip) const
{
    const auto it=banks_.find({channel,chip});
    if (it==banks_.end()) return 0;
    return std::count_if(it->second.slots.begin(),it->second.slots.end(),
                         [](const Slot& slot){return slot.token!=0 || slot.cached_key;});
}
} // namespace hbfsim::ucie
