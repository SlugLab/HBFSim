#include <hbfsim/ucie/backing_registry.hpp>

#include <limits>
#include <stdexcept>

namespace hbfsim::ucie {
namespace {
std::uint64_t checked_end(std::uint64_t start, std::uint64_t bytes)
{
    if (bytes==0 || start>std::numeric_limits<std::uint64_t>::max()-bytes)
        throw std::invalid_argument("empty or overflowing backing span");
    return start+bytes;
}
}

BackingRegistry::BackingRegistry(std::uint64_t module_capacity_bytes,
                                 std::size_t max_ranges)
    : capacity_(module_capacity_bytes), max_ranges_(max_ranges)
{
    if (capacity_==0 || capacity_>(1ULL<<36) || max_ranges_==0)
        throw std::invalid_argument("invalid module backing registry capacity");
}

void BackingRegistry::add(const BackingRange& range)
{
    const auto end=checked_end(range.address,range.bytes);
    const auto canonical_end=checked_end(range.canonical_offset,range.bytes);
    if (range.region_id==0 || range.canonical_id==0 || range.generation==0 ||
        end>capacity_ || canonical_end>capacity_)
        throw std::invalid_argument("invalid read-only backing range");
    if (ranges_.size()>=max_ranges_)
        throw std::overflow_error("backing registry full");
    const Identity identity{range.canonical_id,range.stack_id,
                            range.module_id,range.endpoint_id};
    const auto history=generation_floor_.find(identity);
    if (history!=generation_floor_.end()) {
        if (range.generation<history->second)
            throw std::invalid_argument("stale backing generation");
        if (range.generation==history->second) {
            bool has_active_alias=false;
            for (const auto& [id,r]:ranges_) {
                (void)id;
                if (r.canonical_id==range.canonical_id &&
                    r.stack_id==range.stack_id && r.module_id==range.module_id &&
                    r.endpoint_id==range.endpoint_id &&
                    r.generation==range.generation) has_active_alias=true;
            }
            if (!has_active_alias)
                throw std::invalid_argument("retired backing generation cannot revive");
        }
    } else if (generation_floor_.size()>=max_ranges_) {
        throw std::overflow_error("backing generation history full");
    }
    for (const auto& [id,existing]:ranges_) {
        if (id==range.region_id ||
            (range.address<existing.address+existing.bytes &&
             existing.address<end))
            throw std::invalid_argument("duplicate or overlapping backing range");
    }
    ranges_.emplace(range.region_id,range);
    if (history==generation_floor_.end() || range.generation>history->second)
        generation_floor_[identity]=range.generation;
}

void BackingRegistry::retire(std::uint64_t region_id)
{
    if (ranges_.erase(region_id)!=1)
        throw std::invalid_argument("unknown backing region");
}

void BackingRegistry::retire_generation(std::uint64_t canonical_id,
                                        std::uint64_t generation,
                                        std::uint32_t stack_id,
                                        std::uint32_t module_id,
                                        std::uint64_t endpoint_id)
{
    std::size_t removed=0;
    for (auto it=ranges_.begin();it!=ranges_.end();) {
        const auto& r=it->second;
        if (r.canonical_id==canonical_id && r.generation==generation &&
            r.stack_id==stack_id && r.module_id==module_id &&
            r.endpoint_id==endpoint_id) {
            it=ranges_.erase(it);
            ++removed;
        } else ++it;
    }
    if (!removed) throw std::invalid_argument("unknown backing generation");
}

std::optional<ResolvedBacking> BackingRegistry::resolve_span(
    std::uint64_t address, std::uint64_t bytes,
    std::uint32_t stack_id, std::uint32_t module_id,
    std::uint64_t endpoint_id) const
{
    const auto end=checked_end(address,bytes);
    if (end>capacity_) return std::nullopt;
    for (const auto& [id,r]:ranges_) {
        (void)id;
        if (address>=r.address && end<=r.address+r.bytes &&
            stack_id==r.stack_id && module_id==r.module_id &&
            endpoint_id==r.endpoint_id && r.readable)
            return ResolvedBacking{r.canonical_id,
                r.canonical_offset+(address-r.address),r.generation,
                r.stack_id,r.module_id,r.endpoint_id};
    }
    return std::nullopt;
}
} // namespace hbfsim::ucie
