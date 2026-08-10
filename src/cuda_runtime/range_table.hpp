#pragma once

#include "../host_service/control_layout.hpp"

#include <hbfsim/api.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <mutex>

namespace hbfsim::runtime {

enum class RangeLookupKind { Outside, Matched, CrossesBoundary };

struct RangeLookup {
    RangeLookupKind kind{RangeLookupKind::Outside};
    const host_service::SharedRangeRecord* record{nullptr};
};

using PublishRange = void (*)(void* state) noexcept;
using RangePublishTransaction = int (*)(
    const host_service::SharedRangeRecord& record, PublishRange publish,
    void* publish_state, void* transaction_state) noexcept;

class RangeTable {
  public:
    RangeTable(host_service::ControlView control,
               std::uint32_t page_bytes) noexcept;

    int add(std::uintptr_t base, std::size_t length,
            const hbfsim_range_options& options,
            RangePublishTransaction transaction,
            void* transaction_state) noexcept;

    [[nodiscard]] RangeLookup lookup(std::uintptr_t address,
                                     std::uint32_t bytes) const noexcept;
    [[nodiscard]] std::size_t size() const noexcept;

  private:
    host_service::ControlView control_;
    std::uint32_t page_bytes_;
    std::uint32_t next_range_id_{1};
    std::size_t count_{0};
    mutable std::mutex mutex_;
    std::array<host_service::SharedRangeRecord,
               host_service::kRangeCapacity>
        records_{};
};

}  // namespace hbfsim::runtime
