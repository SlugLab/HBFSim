#pragma once

#include "backing_store.hpp"

#include <hbfsim/protocol.hpp>

#include <array>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <vector>

namespace hbfsim::host_service {

struct RoutedPage {
    RequestStatus status{RequestStatus::IoError};
    std::uint32_t range_id{0};
    std::vector<std::byte> bytes;
};

class CapacityBackingRouter {
  public:
    using PublicationToken = std::uint64_t;
    using Token = PublicationToken;
    using AdmissionHook = std::function<void()>;
    using DeactivationHook = std::function<void()>;

    explicit CapacityBackingRouter(
        AdmissionHook admission_hook = {},
        DeactivationHook deactivation_hook = {});

    Token stage(std::uint32_t range_id, std::uint64_t first_page,
                std::uint64_t page_count, bool writable,
                std::shared_ptr<BackingStore> backing);
    bool activate(Token token) noexcept;
    void cancel(Token token) noexcept;
    RequestStatus deactivate(std::uint32_t range_id);
    RoutedPage read_page(std::uint64_t global_page,
                         std::size_t page_bytes);
    RequestStatus write_page(std::uint64_t global_page,
                             std::size_t page_bytes,
                             std::span<const std::byte> bytes);
    RequestStatus flush(
        std::optional<std::uint32_t> range_id = std::nullopt);

  private:
    friend class CapacityBackingRouterTestAccess;

    enum class EntryState { Empty, Staged, Active, Deactivating };

    struct Entry {
        EntryState state{EntryState::Empty};
        Token token{0};
        std::uint32_t range_id{0};
        std::uint64_t first_page{0};
        std::uint64_t page_count{0};
        bool writable{false};
        std::shared_ptr<BackingStore> backing;
        std::size_t admissions{0};
    };

    [[nodiscard]] std::optional<std::size_t> admit(
        std::uint64_t global_page);
    void release(std::size_t index) noexcept;
    void invoke_admission_hook();
    [[nodiscard]] Token next_token_locked() noexcept;

    static constexpr std::size_t kCapacity = 64;
    std::mutex mutex_;
    std::condition_variable drained_;
    std::array<Entry, kCapacity> entries_{};
    Token next_token_{1};
    bool tokens_exhausted_{false};
    AdmissionHook admission_hook_;
    DeactivationHook deactivation_hook_;
};

}  // namespace hbfsim::host_service
