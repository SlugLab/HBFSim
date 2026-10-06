#include <hbfsim/ucie/stack_router.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>

namespace hbfsim::ucie {
namespace {
std::uint64_t end_of(std::uint64_t start,std::uint64_t bytes)
{
    if (bytes==0 || start>std::numeric_limits<std::uint64_t>::max()-bytes)
        throw std::invalid_argument("empty or overflowing global span");
    return start+bytes;
}

void validate_virtual(std::uint64_t start,std::uint64_t bytes)
{
    if (!bytes || bytes-1>
        std::numeric_limits<std::uint64_t>::max()-start)
        throw std::invalid_argument("empty or overflowing virtual span");
}

bool overlaps(std::uint64_t first,std::uint64_t first_bytes,
              std::uint64_t second,std::uint64_t second_bytes)
{
    return first<=second?second-first<first_bytes:
                         first-second<second_bytes;
}

std::uint64_t aligned_end(std::uint64_t end)
{
    if (end>std::numeric_limits<std::uint64_t>::max()-63)
        throw std::invalid_argument("aligned global span overflows");
    return (end+63)&~std::uint64_t{63};
}
}

ContiguousStackMap::ContiguousStackMap(std::uint64_t total,
        std::uint32_t stacks,std::uint32_t modules)
    : total_(total),stack_(0),module_(0),stacks_(stacks),modules_(modules)
{
    if (!total || stacks==0 || modules==0 || modules>16 ||
        total%stacks || (total/stacks)%modules ||
        (total/stacks/modules)%4096)
        throw std::invalid_argument("unequal or non-page-aligned stack geometry");
    stack_=total/stacks;
    module_=stack_/modules;
    if (!module_ || module_>(1ULL<<36))
        throw std::invalid_argument("module exceeds 36-bit AXI local capacity");
}

StackLocation ContiguousStackMap::locate(std::uint64_t address) const
{
    if (address>=total_) throw std::out_of_range("global HBF address exceeds capacity");
    const auto stack=address/stack_;
    const auto within=address%stack_;
    return {static_cast<std::uint32_t>(stack),
        static_cast<std::uint32_t>(within/module_),within%module_};
}

GlobalSpanRegistry::GlobalSpanRegistry(ContiguousStackMap map,
        std::size_t max_ranges,std::uint64_t max_read_bytes)
    : map_(map),max_ranges_(max_ranges),max_read_bytes_(max_read_bytes)
{
    if (!max_ranges_ || !max_read_bytes_)
        throw std::invalid_argument("empty global backing or read bound");
}

std::vector<BackingRange> GlobalSpanRegistry::add(const GlobalBackingRange& r)
{
    validate_virtual(r.address,r.bytes);
    const auto canonical_end=end_of(r.canonical_physical_address,r.bytes);
    if (!r.region_id || !r.canonical_id || !r.generation ||
        canonical_end>map_.total_bytes())
        throw std::invalid_argument("invalid global backing range");
    if (disabled_generations_.contains(
        {r.canonical_id,r.generation,r.endpoint_id}))
        throw std::invalid_argument("retiring global generation cannot gain aliases");
    if (ranges_.size()>=max_ranges_)
        throw std::overflow_error("global backing registry full");
    const auto identity=std::pair{r.canonical_id,r.endpoint_id};
    const auto floor=generation_floor_.find(identity);
    if (floor!=generation_floor_.end()) {
        if (r.generation<floor->second)
            throw std::invalid_argument("stale global backing generation");
        if (r.generation==floor->second) {
            bool active=false;
            for (const auto& [id,existing]:ranges_) {
                (void)id;
                if (existing.canonical_id==r.canonical_id &&
                    existing.endpoint_id==r.endpoint_id &&
                    existing.generation==r.generation) active=true;
            }
            if (!active)
                throw std::invalid_argument("retired global generation cannot revive");
        }
    } else if (generation_floor_.size()>=max_ranges_) {
        throw std::overflow_error("global backing history full");
    }
    for (const auto& [id,existing]:ranges_) {
        if (id==r.region_id ||
            (existing.endpoint_id==r.endpoint_id &&
             overlaps(r.address,r.bytes,existing.address,existing.bytes)))
            throw std::invalid_argument("overlapping endpoint backing range");
    }

    // Worker registrations cover exact physical bytes. Separate objects in
    // one 64 B AXI envelope retain independent canonical identities.
    const auto physical_begin=r.canonical_physical_address;
    const auto physical_end=canonical_end;
    std::vector<std::pair<std::uint64_t,std::uint64_t>> uncovered;
    auto position=physical_begin;
    while (position<physical_end) {
        const auto module_end=position+(map_.module_bytes()-
                                              map_.locate(position).module_local_address);
        const auto segment_end=std::min(physical_end,module_end);
        while (position<segment_end) {
            auto next=physical_.upper_bound(position);
            if (next!=physical_.begin()) {
                const auto prior=std::prev(next);
                if (prior->second.end>position) {
                    const auto& seen=prior->second;
                    if (seen.canonical_id!=r.canonical_id ||
                        seen.generation!=r.generation ||
                        seen.endpoint_id!=r.endpoint_id)
                        throw std::invalid_argument("conflicting physical backing identity");
                    position=std::min(segment_end,seen.end);
                    continue;
                }
            }
            const auto gap_end=std::min(segment_end,
                next==physical_.end()?segment_end:next->first);
            if (gap_end<=position) throw std::logic_error("non-progressing physical gap");
            uncovered.emplace_back(position,gap_end);
            position=gap_end;
        }
    }
    const auto max_segments=max_ranges_>
        std::numeric_limits<std::size_t>::max()/64?
        std::numeric_limits<std::size_t>::max():max_ranges_*64;
    if (physical_.size()>max_segments ||
        uncovered.size()>max_segments-physical_.size() ||
        next_segment_id_>std::numeric_limits<std::uint64_t>::max()-uncovered.size())
        throw std::overflow_error("worker backing segments exhausted");
    std::vector<BackingRange> segments;
    segments.reserve(uncovered.size());
    for (const auto [begin,end]:uncovered) {
        const auto location=map_.locate(begin);
        segments.push_back({next_segment_id_++,location.module_local_address,
            end-begin,r.canonical_id,location.module_local_address,
            r.generation,location.stack_id,location.module_id,r.endpoint_id,true});
        physical_.emplace(begin,PhysicalRegistration{end,r.canonical_id,
                                                      r.generation,r.endpoint_id});
    }
    ranges_.emplace(r.region_id,r);
    if (floor==generation_floor_.end() || r.generation>floor->second)
        generation_floor_[identity]=r.generation;
    return segments;
}

std::vector<BackingRange> GlobalSpanRegistry::add_page_striped(
    const PageStripeBacking& stripe)
{
    const auto& r = stripe.parent;
    validate_virtual(r.address, r.bytes);
    constexpr std::uint64_t page = 16384;
    constexpr std::uint32_t channels = 64;
    if (map_.stack_count() != 4 || map_.modules_per_stack() != 16 ||
        stripe.page_bytes != page || !stripe.actual_storage_bytes ||
        stripe.actual_storage_bytes > UINT64_MAX - (page - 1) ||
        r.bytes != ((stripe.actual_storage_bytes + page - 1) / page) * page ||
        r.address % page || !r.region_id || !r.canonical_id || !r.generation ||
        !r.endpoint_id || stripe.phase >= channels ||
        stripe.descriptor_sha256.size() != 64 ||
        !std::all_of(stripe.descriptor_sha256.begin(), stripe.descriptor_sha256.end(),
            [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }))
        throw std::invalid_argument("invalid compact page-striped parent");
    if (ranges_.size() >= max_ranges_)
        throw std::overflow_error("global backing registry full");
    const auto identity = std::pair{r.canonical_id, r.endpoint_id};
    const auto floor = generation_floor_.find(identity);
    if (disabled_generations_.contains({r.canonical_id, r.generation, r.endpoint_id}))
        throw std::invalid_argument("retiring striped generation cannot gain aliases");
    if (floor != generation_floor_.end()) {
        if (r.generation <= floor->second)
            throw std::invalid_argument("striped generation is stale or already registered");
    } else if (generation_floor_.size() >= max_ranges_) {
        throw std::overflow_error("global backing history full");
    }
    for (const auto& [id, existing] : ranges_) {
        if (id == r.region_id || (existing.endpoint_id == r.endpoint_id &&
            overlaps(r.address, r.bytes, existing.address, existing.bytes)))
            throw std::invalid_argument("overlapping striped logical registration");
    }
    const auto pages = r.bytes / page;
    std::set<std::uint32_t> seen;
    std::vector<BackingRange> result;
    std::vector<std::pair<std::uint64_t, std::uint64_t>> intervals;
    if (stripe.segments.empty() || stripe.segments.size() > channels)
        throw std::invalid_argument("invalid striped segment count");
    std::uint64_t total_pages = 0;
    for (const auto& segment : stripe.segments) {
        if (segment.channel >= channels || !seen.insert(segment.channel).second)
            throw std::invalid_argument("duplicate or invalid stripe channel");
        const auto p0 = (segment.channel + channels - stripe.phase) % channels;
        const auto expected = p0 < pages ? 1 + (pages - 1 - p0) / channels : 0;
        if (!expected || segment.page_count != expected ||
            segment.page_count > UINT64_MAX / page ||
            segment.canonical_physical_address % page)
            throw std::invalid_argument("stripe page count or alignment differs");
        const auto begin = segment.canonical_physical_address;
        const auto end = end_of(begin, segment.page_count * page);
        if (end > map_.total_bytes())
            throw std::invalid_argument("stripe exceeds physical capacity");
        const auto location = map_.locate(begin);
        const auto last = map_.locate(end - 1);
        if (location.stack_id != segment.channel % 4 ||
            location.module_id != segment.channel / 4 ||
            location.stack_id != last.stack_id || location.module_id != last.module_id)
            throw std::invalid_argument("stripe packed segment crosses its module");
        auto next = physical_.lower_bound(begin);
        if ((next != physical_.end() && next->first < end) ||
            (next != physical_.begin() && std::prev(next)->second.end > begin))
            throw std::invalid_argument("stripe conflicts with physical registration");
        intervals.emplace_back(begin, end);
        total_pages += segment.page_count;
    }
    std::sort(intervals.begin(), intervals.end());
    for (std::size_t i = 1; i < intervals.size(); ++i)
        if (intervals[i].first < intervals[i - 1].second)
            throw std::invalid_argument("overlapping packed stripe segments");
    if (total_pages != pages)
        throw std::invalid_argument("striped page union is incomplete");
    const auto maximum = max_ranges_ > std::numeric_limits<std::size_t>::max() / 64 ?
        std::numeric_limits<std::size_t>::max() : max_ranges_ * 64;
    if (physical_.size() > maximum || stripe.segments.size() > maximum - physical_.size() ||
        next_segment_id_ > UINT64_MAX - stripe.segments.size())
        throw std::overflow_error("worker backing segments exhausted");
    result.reserve(stripe.segments.size());
    for (const auto& segment : stripe.segments) {
        const auto location = map_.locate(segment.canonical_physical_address);
        result.push_back({next_segment_id_++, location.module_local_address,
            segment.page_count * page, r.canonical_id, location.module_local_address,
            r.generation, location.stack_id, location.module_id, r.endpoint_id, r.readable});
        physical_.emplace(segment.canonical_physical_address,
            PhysicalRegistration{end_of(segment.canonical_physical_address,
                                       segment.page_count * page),
                                 r.canonical_id, r.generation, r.endpoint_id});
    }
    ranges_.emplace(r.region_id, r);
    auto indexed = stripe;
    indexed.channel_physical_bases.fill(0);
    for (const auto& segment : stripe.segments)
        indexed.channel_physical_bases[segment.channel] = segment.canonical_physical_address;
    page_striped_.emplace(r.region_id, std::move(indexed));
    generation_floor_[identity] = r.generation;
    return result;
}

PageStripeInverse GlobalSpanRegistry::inverse_page_striped(
    std::uint64_t physical, std::uint64_t generation, std::uint64_t endpoint) const
{
    for (const auto& [id, stripe] : page_striped_) {
        (void)id;
        const auto& r = stripe.parent;
        if (r.generation != generation || r.endpoint_id != endpoint) continue;
        for (const auto& segment : stripe.segments) {
            const auto begin = segment.canonical_physical_address;
            if (physical < begin || physical - begin >= segment.page_count * stripe.page_bytes)
                continue;
            const auto within = physical - begin;
            const auto p0 = (segment.channel + 64 - stripe.phase) % 64;
            const auto offset = (64 * (within / stripe.page_bytes) + p0) * stripe.page_bytes +
                                within % stripe.page_bytes;
            if (offset >= r.bytes) throw std::logic_error("stripe inverse exceeds parent");
            return {r.canonical_id, generation, endpoint, offset,
                    stripe.actual_storage_bytes, stripe.descriptor_sha256};
        }
    }
    throw std::invalid_argument("unknown striped physical page/generation/endpoint");
}

void GlobalSpanRegistry::disable_generation(std::uint64_t canonical_id,
        std::uint64_t generation,std::uint64_t endpoint)
{
    bool found=false;
    for (const auto& [id,r]:ranges_) {
        (void)id;
        if (r.canonical_id==canonical_id && r.generation==generation &&
            r.endpoint_id==endpoint) found=true;
    }
    if (!found) throw std::invalid_argument("unknown global generation");
    disabled_generations_.insert({canonical_id,generation,endpoint});
}

void GlobalSpanRegistry::retire_generation(std::uint64_t canonical_id,
        std::uint64_t generation,std::uint64_t endpoint)
{
    std::size_t removed=0;
    for (auto it=ranges_.begin();it!=ranges_.end();) {
        const auto& r=it->second;
        if (r.canonical_id==canonical_id && r.generation==generation &&
            r.endpoint_id==endpoint) {
            page_striped_.erase(it->first);
            it=ranges_.erase(it);
            ++removed;
        } else ++it;
    }
    if (!removed) throw std::invalid_argument("unknown global generation");
    disabled_generations_.erase({canonical_id,generation,endpoint});
    for (auto it=physical_.begin();it!=physical_.end();) {
        const auto& p=it->second;
        if (p.canonical_id==canonical_id && p.generation==generation &&
            p.endpoint_id==endpoint) it=physical_.erase(it);
        else ++it;
    }
}

RoutedBacking GlobalSpanRegistry::resolve_span(std::uint64_t address,
        std::uint64_t bytes,std::uint64_t endpoint) const
{
    validate_virtual(address,bytes);
    for (const auto& [id,r]:ranges_) {
        (void)id;
        if (address>=r.address && address-r.address<=r.bytes &&
            bytes<=r.bytes-(address-r.address) &&
            r.endpoint_id==endpoint && r.readable &&
            !disabled_generations_.contains(
                {r.canonical_id,r.generation,r.endpoint_id})) {
            const auto striped = page_striped_.find(id);
            if (striped != page_striped_.end()) {
                const auto& stripe = striped->second;
                const auto offset = address - r.address;
                const auto within = offset % stripe.page_bytes;
                if (bytes > stripe.page_bytes - within)
                    throw std::invalid_argument("read crosses compact stripe page boundary");
                const auto p = offset / stripe.page_bytes;
                const auto channel = static_cast<std::uint32_t>((p + stripe.phase) % 64);
                return {r.canonical_id, stripe.channel_physical_bases[channel] +
                    (p / 64) * stripe.page_bytes + within, r.generation, endpoint};
            }
            return {r.canonical_id,
                r.canonical_physical_address+(address-r.address),
                r.generation,endpoint};
        }
    }
    throw std::invalid_argument("full original virtual span unregistered or unreadable");
}

std::vector<RoutedChild> GlobalSpanRegistry::split_read(
        std::uint64_t address,std::uint64_t bytes,
        std::uint64_t endpoint,std::uint64_t first_child_id) const
{
    // Permission is checked over the exact original virtual bytes. Padding
    // creates internal physical AXI traffic but grants no caller permissions.
    const auto backing=resolve_span(address,bytes,endpoint);
    if (!first_child_id || bytes>max_read_bytes_)
        throw std::invalid_argument("invalid or unbounded global read");
    const auto canonical_end=end_of(backing.canonical_physical_address,bytes);
    const auto first=backing.canonical_physical_address&~std::uint64_t{63};
    const auto final=aligned_end(canonical_end);
    if (final>map_.total_bytes())
        throw std::out_of_range("physical AXI envelope exceeds HBF capacity");
    const auto count=(final-first)/64;
    if (count>max_read_bytes_/64+2 || first_child_id>
        std::numeric_limits<std::uint64_t>::max()-count)
        throw std::overflow_error("global child ID/count overflow");
    std::vector<RoutedChild> children;
    children.reserve(static_cast<std::size_t>(count));
    for (auto aligned=first;aligned<final;aligned+=64) {
        const auto location=map_.locate(aligned);
        const auto original_begin=std::max(backing.canonical_physical_address,aligned);
        const auto original_end=std::min(canonical_end,aligned+64);
        children.push_back({first_child_id+children.size(),location,aligned,
            original_begin,
            static_cast<std::uint32_t>(original_end-original_begin)});
    }
    return children;
}
} // namespace hbfsim::ucie
