#pragma once

#include <cstdint>
#include <cstddef>
#include <map>
#include <optional>
#include <tuple>

namespace hbfsim::ucie {

// Addresses are one module's AXI address. Canonical offsets name the actual
// backing, so an explicit alias can resolve to the same 4 KiB media page.
struct BackingRange {
    std::uint64_t region_id{};
    std::uint64_t address{};
    std::uint64_t bytes{};
    std::uint64_t canonical_id{};
    std::uint64_t canonical_offset{};
    std::uint64_t generation{};
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t endpoint_id{};
    bool readable{};
};

struct ResolvedBacking {
    std::uint64_t canonical_id{};
    std::uint64_t canonical_offset{};
    std::uint64_t generation{};
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t endpoint_id{};
};

class BackingRegistry {
public:
    explicit BackingRegistry(std::uint64_t module_capacity_bytes,
                             std::size_t max_ranges=1024);
    void add(const BackingRange& range);
    void retire(std::uint64_t region_id);
    void retire_generation(std::uint64_t canonical_id,
                           std::uint64_t generation,
                           std::uint32_t stack_id,
                           std::uint32_t module_id,
                           std::uint64_t endpoint_id);
    [[nodiscard]] std::optional<ResolvedBacking> resolve_span(
        std::uint64_t address, std::uint64_t bytes,
        std::uint32_t stack_id, std::uint32_t module_id,
        std::uint64_t endpoint_id) const;
    [[nodiscard]] std::size_t size() const noexcept { return ranges_.size(); }
private:
    std::uint64_t capacity_;
    std::size_t max_ranges_;
    std::map<std::uint64_t,BackingRange> ranges_;
    using Identity=std::tuple<std::uint64_t,std::uint32_t,std::uint32_t,std::uint64_t>;
    std::map<Identity,std::uint64_t> generation_floor_;
};

} // namespace hbfsim::ucie
