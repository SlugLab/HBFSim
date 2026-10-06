#pragma once

#include <hbfsim/ucie/backing_registry.hpp>

#include <cstddef>
#include <array>
#include <cstdint>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

namespace hbfsim::ucie {

struct StackLocation {
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t module_local_address{};
};

class ContiguousStackMap {
public:
    ContiguousStackMap(std::uint64_t total_bytes,std::uint32_t stack_count,
                       std::uint32_t modules_per_stack);
    [[nodiscard]] StackLocation locate(std::uint64_t address) const;
    [[nodiscard]] std::uint64_t total_bytes() const noexcept { return total_; }
    [[nodiscard]] std::uint64_t stack_bytes() const noexcept { return stack_; }
    [[nodiscard]] std::uint64_t module_bytes() const noexcept { return module_; }
    [[nodiscard]] std::uint32_t stack_count() const noexcept { return stacks_; }
    [[nodiscard]] std::uint32_t modules_per_stack() const noexcept
    { return modules_; }
private:
    std::uint64_t total_;
    std::uint64_t stack_;
    std::uint64_t module_;
    std::uint32_t stacks_;
    std::uint32_t modules_;
};

// `address` is an arbitrary virtual span. Only canonical physical placement
// belongs to the continuous HBF capacity; aliases may cross virtual ranges.
struct GlobalBackingRange {
    std::uint64_t region_id{};
    std::uint64_t address{};
    std::uint64_t bytes{};
    std::uint64_t canonical_id{};
    std::uint64_t canonical_physical_address{};
    std::uint64_t generation{};
    std::uint64_t endpoint_id{};
    bool readable{};
};

struct RoutedBacking {
    std::uint64_t canonical_id{};
    std::uint64_t canonical_physical_address{};
    std::uint64_t generation{};
    std::uint64_t endpoint_id{};
};

// Packed physical bytes are not a contiguous slice of the parent payload.
struct PageStripeSegment {
    std::uint32_t channel{};
    std::uint64_t canonical_physical_address{};
    std::uint64_t page_count{};
};

struct PageStripeBacking {
    GlobalBackingRange parent;
    std::uint64_t actual_storage_bytes{};
    std::uint64_t page_bytes{16384};
    std::uint32_t phase{};
    std::string descriptor_sha256;
    std::vector<PageStripeSegment> segments;
    // Derived at validated registration; not a manifest or wire field.
    std::array<std::uint64_t,64> channel_physical_bases{};
};

struct PageStripeInverse {
    std::uint64_t canonical_id{};
    std::uint64_t generation{};
    std::uint64_t endpoint_id{};
    std::uint64_t logical_parent_offset{};
    std::uint64_t actual_storage_bytes{};
    std::string descriptor_sha256;
};

struct RoutedChild {
    std::uint64_t child_id{};
    StackLocation location;
    std::uint64_t aligned_canonical_address{};
    std::uint64_t original_canonical_address{};
    std::uint32_t original_bytes{};
};

class GlobalSpanRegistry {
public:
    explicit GlobalSpanRegistry(ContiguousStackMap map,
                                std::size_t max_ranges=1024,
                                std::uint64_t max_read_bytes=65536);
    // Returns exact original physical segments, never 64 B padding. Trusted
    // packet proofs later permit the modeled AXI envelope.
    [[nodiscard]] std::vector<BackingRange> add(const GlobalBackingRange& range);
    [[nodiscard]] std::vector<BackingRange> add_page_striped(
        const PageStripeBacking& range);
    [[nodiscard]] PageStripeInverse inverse_page_striped(
        std::uint64_t physical_address, std::uint64_t generation,
        std::uint64_t endpoint_id) const;
    void disable_generation(std::uint64_t canonical_id,
                            std::uint64_t generation,
                            std::uint64_t endpoint_id);
    void retire_generation(std::uint64_t canonical_id,
                           std::uint64_t generation,
                           std::uint64_t endpoint_id);
    [[nodiscard]] RoutedBacking resolve_span(std::uint64_t address,
                                              std::uint64_t bytes,
                                              std::uint64_t endpoint_id) const;
    [[nodiscard]] std::vector<RoutedChild> split_read(
        std::uint64_t address,std::uint64_t bytes,
        std::uint64_t endpoint_id,std::uint64_t first_child_id) const;
    [[nodiscard]] const ContiguousStackMap& map() const noexcept { return map_; }
private:
    struct PhysicalRegistration {
        std::uint64_t end{};
        std::uint64_t canonical_id{};
        std::uint64_t generation{};
        std::uint64_t endpoint_id{};
    };
    ContiguousStackMap map_;
    std::size_t max_ranges_;
    std::uint64_t max_read_bytes_;
    std::uint64_t next_segment_id_{1};
    std::map<std::uint64_t,GlobalBackingRange> ranges_;
    std::map<std::uint64_t,PageStripeBacking> page_striped_;
    std::map<std::uint64_t,PhysicalRegistration> physical_;
    std::set<std::tuple<std::uint64_t,std::uint64_t,std::uint64_t>>
        disabled_generations_;
    std::map<std::pair<std::uint64_t,std::uint64_t>,std::uint64_t>
        generation_floor_;
};

} // namespace hbfsim::ucie
