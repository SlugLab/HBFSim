#pragma once

#include <hbfsim/module_identity.hpp>

#include <atomic>
#include <cstddef>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace hbfsim {

[[nodiscard]] std::string sha256_hex(std::span<const std::byte> bytes);

class AotLoadTransactionStore {
  public:
    [[nodiscard]] ModuleLoadToken
    begin(std::span<const std::byte> cubin,
          std::string_view artifact_json) noexcept;
    [[nodiscard]] std::optional<LoadedModuleEvidence>
    take_for_image(const void* image) noexcept;
    void end(ModuleLoadToken token) noexcept;

  private:
    struct Entry {
        ModuleLoadToken token;
        const std::byte* image;
        std::size_t image_bytes;
        LoadedModuleEvidence evidence;
    };

    static thread_local std::optional<Entry> current_;
    std::atomic<ModuleLoadToken> next_token_{1};
};

}  // namespace hbfsim
