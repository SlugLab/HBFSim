#include "hbfsim/module_identity.hpp"

namespace hbfsim {
namespace {

constexpr std::string_view identity_symbol = "__hbfsim_module_identity";
constexpr std::string_view declaration_prefix =
    ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {";

int hex_digit(char digit) noexcept
{
    if (digit >= '0' && digit <= '9') {
        return digit - '0';
    }
    if (digit >= 'a' && digit <= 'f') {
        return digit - 'a' + 10;
    }
    return -1;
}

std::optional<ModuleIdentity> identity_from_ptx(std::string_view ptx) noexcept
{
    const auto symbol = ptx.find(identity_symbol);
    if (symbol == std::string_view::npos ||
        ptx.find(identity_symbol, symbol + identity_symbol.size()) !=
            std::string_view::npos) {
        return std::nullopt;
    }
    const auto declaration = ptx.find(declaration_prefix);
    if (declaration == std::string_view::npos ||
        declaration + declaration_prefix.find(identity_symbol) != symbol) {
        return std::nullopt;
    }

    ModuleIdentity identity{};
    auto cursor = declaration + declaration_prefix.size();
    for (std::size_t index = 0; index < identity.size(); ++index) {
        if (cursor + 4 > ptx.size() || ptx.substr(cursor, 2) != "0x") {
            return std::nullopt;
        }
        const int high = hex_digit(ptx[cursor + 2]);
        const int low = hex_digit(ptx[cursor + 3]);
        if (high < 0 || low < 0) {
            return std::nullopt;
        }
        identity[index] = static_cast<std::uint8_t>((high << 4) | low);
        cursor += 4;
        const std::string_view separator =
            index + 1 == identity.size() ? "};" : ", ";
        if (cursor + separator.size() > ptx.size() ||
            ptx.substr(cursor, separator.size()) != separator) {
            return std::nullopt;
        }
        cursor += separator.size();
    }
    return identity;
}

}  // namespace

thread_local std::optional<ModuleLoadTransactionStore::Entry>
    ModuleLoadTransactionStore::current_;

ModuleLoadToken ModuleLoadTransactionStore::begin(std::string_view ptx) noexcept
{
    if (current_.has_value()) {
        current_.reset();
        return 0;
    }
    const auto identity = identity_from_ptx(ptx);
    if (!identity.has_value()) {
        return 0;
    }
    ModuleLoadToken token = 0;
    while (token == 0) {
        token = next_token_.fetch_add(1, std::memory_order_relaxed);
    }
    current_ = Entry{token, *identity};
    return token;
}

std::optional<ModuleIdentity> ModuleLoadTransactionStore::take() noexcept
{
    if (!current_.has_value()) {
        return std::nullopt;
    }
    const auto identity = current_->identity;
    current_.reset();
    return identity;
}

void ModuleLoadTransactionStore::end(ModuleLoadToken token) noexcept
{
    if (token != 0 && current_.has_value() && current_->token == token) {
        current_.reset();
    }
}

bool ModuleIdentityRegistry::associate(ModuleHandle module,
                                       const ModuleIdentity& identity)
{
    LoadedModuleEvidence evidence{.identity = identity,
                                  .aot_verified = false};
    return associate(module, evidence);
}

bool ModuleIdentityRegistry::associate(ModuleHandle module,
                                       const LoadedModuleEvidence& evidence)
{
    std::scoped_lock lock(mutex_);
    if (associations_.contains(module)) {
        return false;
    }
    associations_.emplace(module, evidence);
    return true;
}

std::optional<ModuleIdentity>
ModuleIdentityRegistry::lookup(ModuleHandle module) const
{
    std::scoped_lock lock(mutex_);
    const auto found = associations_.find(module);
    return found == associations_.end()
               ? std::nullopt
               : std::optional<ModuleIdentity>{found->second.identity};
}

std::optional<LoadedModuleEvidence>
ModuleIdentityRegistry::lookup_evidence(ModuleHandle module) const
{
    std::scoped_lock lock(mutex_);
    const auto found = associations_.find(module);
    return found == associations_.end()
               ? std::nullopt
               : std::optional<LoadedModuleEvidence>{found->second};
}

void ModuleIdentityRegistry::erase(ModuleHandle module)
{
    std::scoped_lock lock(mutex_);
    associations_.erase(module);
}

void ModuleIdentityRegistry::clear()
{
    std::scoped_lock lock(mutex_);
    associations_.clear();
}

}  // namespace hbfsim
