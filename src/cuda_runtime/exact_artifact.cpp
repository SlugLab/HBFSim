#include <hbfsim/exact_artifact.hpp>

#include <json.hpp>
#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <limits>
#include <set>
#include <unordered_set>

namespace hbfsim {
namespace {

using json = nlohmann::json;
constexpr std::size_t kMaxArtifactBytes = 16 * 1024 * 1024;
constexpr std::size_t kMaxCubinBytes = 1024ULL * 1024 * 1024;

bool valid_sha256(std::string_view value)
{
    return value.size() == 64 &&
           std::all_of(value.begin(), value.end(), [](char character) {
               return (character >= '0' && character <= '9') ||
                      (character >= 'a' && character <= 'f');
           });
}

void exact_keys(const json& value,
                std::initializer_list<std::string_view> keys)
{
    if (!value.is_object() || value.size() != keys.size()) {
        throw std::invalid_argument("artifact object shape mismatch");
    }
    for (const auto key : keys) {
        if (!value.contains(std::string(key))) {
            throw std::invalid_argument("artifact field missing");
        }
    }
}

template <class T>
T field(const json& value, std::string_view name)
{
    return value.at(std::string(name)).get<T>();
}

std::string nonempty(const json& value, std::string_view name)
{
    auto result = field<std::string>(value, name);
    if (result.empty()) {
        throw std::invalid_argument("empty artifact field");
    }
    return result;
}

std::string digest(const json& value, std::string_view name)
{
    auto result = nonempty(value, name);
    if (!valid_sha256(result)) {
        throw std::invalid_argument("invalid artifact digest");
    }
    return result;
}

std::uint32_t positive_u32(const json& value, std::string_view name)
{
    const auto result = field<std::uint32_t>(value, name);
    if (result == 0) {
        throw std::invalid_argument("zero artifact resource");
    }
    return result;
}

ModuleIdentity identity_from_module_id(std::string_view module_id)
{
    constexpr std::string_view prefix = "ptx:sha256:";
    if (!module_id.starts_with(prefix) ||
        !valid_sha256(module_id.substr(prefix.size()))) {
        throw std::invalid_argument("invalid artifact module ID");
    }
    ModuleIdentity identity{};
    auto hex = module_id.substr(prefix.size());
    for (std::size_t index = 0; index < identity.size(); ++index) {
        const auto digit = [](char value) -> std::uint8_t {
            return static_cast<std::uint8_t>(value <= '9' ? value - '0'
                                                          : value - 'a' + 10);
        };
        identity[index] = static_cast<std::uint8_t>(
            (digit(hex[index * 2]) << 4) | digit(hex[index * 2 + 1]));
    }
    return identity;
}

ExactKernelArtifact parse_kernel(const json& value)
{
    exact_keys(value,
               {"name", "registers", "spill_store_bytes", "spill_load_bytes",
                "static_shared_bytes", "max_dynamic_shared_bytes",
                "block_threads", "occupancy_blocks_per_sm"});
    ExactKernelArtifact result{
        .name = nonempty(value, "name"),
        .registers = positive_u32(value, "registers"),
        .spill_store_bytes = field<std::uint64_t>(value, "spill_store_bytes"),
        .spill_load_bytes = field<std::uint64_t>(value, "spill_load_bytes"),
        .static_shared_bytes =
            field<std::uint64_t>(value, "static_shared_bytes"),
        .max_dynamic_shared_bytes =
            field<std::uint64_t>(value, "max_dynamic_shared_bytes"),
        .block_threads = positive_u32(value, "block_threads"),
        .occupancy_blocks_per_sm =
            positive_u32(value, "occupancy_blocks_per_sm"),
    };
    if (result.block_threads > 1024) {
        throw std::invalid_argument("artifact block size exceeds CUDA limit");
    }
    return result;
}

LoadedModuleEvidence parse_artifact(std::string_view text,
                                    std::span<const std::byte> cubin)
{
    if (text.empty() || text.size() > kMaxArtifactBytes || cubin.empty() ||
        cubin.size() > kMaxCubinBytes) {
        throw std::invalid_argument("artifact or cubin size is invalid");
    }
    const auto root = json::parse(text);
    exact_keys(root, {"schema_version", "module_id", "ptx_target",
                      "toolchain", "hashes", "kernels"});
    if (field<std::uint32_t>(root, "schema_version") != 1) {
        throw std::invalid_argument("unsupported artifact schema");
    }
    const auto module_id = nonempty(root, "module_id");
    const auto target = nonempty(root, "ptx_target");
    if (target != "sm_120" && target != "sm_120a" && target != "sm_120f") {
        throw std::invalid_argument("unsupported artifact target");
    }
    const auto& toolchain = root.at("toolchain");
    exact_keys(toolchain, {"cuda_release", "ptxas_version",
                           "nvdisasm_version", "cuobjdump_version"});
    const auto& hashes = root.at("hashes");
    exact_keys(hashes,
               {"original_ptx_sha256", "transformed_ptx_sha256",
                "cubin_sha256", "sass_sha256"});
    LoadedModuleEvidence result{
        .identity = identity_from_module_id(module_id),
        .module_id = module_id,
        .ptx_target = target,
        .original_ptx_sha256 = digest(hashes, "original_ptx_sha256"),
        .transformed_ptx_sha256 = digest(hashes, "transformed_ptx_sha256"),
        .cubin_sha256 = digest(hashes, "cubin_sha256"),
        .sass_sha256 = digest(hashes, "sass_sha256"),
        .toolchain =
            {.cuda_release = nonempty(toolchain, "cuda_release"),
             .ptxas_version = nonempty(toolchain, "ptxas_version"),
             .nvdisasm_version = nonempty(toolchain, "nvdisasm_version"),
             .cuobjdump_version = nonempty(toolchain, "cuobjdump_version")},
        .aot_verified = true,
    };
    if (result.cubin_sha256 != sha256_hex(cubin)) {
        throw std::invalid_argument("artifact cubin digest mismatch");
    }
    const auto& kernels = root.at("kernels");
    if (!kernels.is_array() || kernels.empty()) {
        throw std::invalid_argument("artifact has no kernel resources");
    }
    std::unordered_set<std::string> names;
    for (const auto& item : kernels) {
        auto kernel = parse_kernel(item);
        if (!names.insert(kernel.name).second) {
            throw std::invalid_argument("duplicate artifact kernel");
        }
        result.kernels.push_back(std::move(kernel));
    }
    return result;
}

}  // namespace

std::string sha256_hex(std::span<const std::byte> bytes)
{
    std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
    unsigned int digest_bytes = 0;
    if (EVP_Digest(bytes.data(), bytes.size(), digest.data(), &digest_bytes,
                   EVP_sha256(), nullptr) != 1 || digest_bytes != 32) {
        throw std::runtime_error("SHA-256 computation failed");
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result(digest_bytes * 2, '0');
    for (std::size_t index = 0; index < digest_bytes; ++index) {
        result[index * 2] = hex[digest[index] >> 4];
        result[index * 2 + 1] = hex[digest[index] & 0xf];
    }
    return result;
}

thread_local std::optional<AotLoadTransactionStore::Entry>
    AotLoadTransactionStore::current_;

ModuleLoadToken
AotLoadTransactionStore::begin(std::span<const std::byte> cubin,
                               std::string_view artifact_json) noexcept
{
    if (current_.has_value()) {
        current_.reset();
        return 0;
    }
    try {
        auto evidence = parse_artifact(artifact_json, cubin);
        ModuleLoadToken token = 0;
        while (token == 0) {
            token = next_token_.fetch_add(1, std::memory_order_relaxed);
        }
        current_ = Entry{.token = token,
                         .image = cubin.data(),
                         .image_bytes = cubin.size(),
                         .evidence = std::move(evidence)};
        return token;
    } catch (...) {
        current_.reset();
        return 0;
    }
}

std::optional<LoadedModuleEvidence>
AotLoadTransactionStore::take_for_image(const void* image) noexcept
{
    if (!current_.has_value()) {
        return std::nullopt;
    }
    auto entry = std::move(*current_);
    current_.reset();
    if (image != entry.image) {
        return std::nullopt;
    }
    try {
        const auto bytes = std::span(entry.image, entry.image_bytes);
        if (sha256_hex(bytes) != entry.evidence.cubin_sha256) {
            return std::nullopt;
        }
        return entry.evidence;
    } catch (...) {
        return std::nullopt;
    }
}

void AotLoadTransactionStore::end(ModuleLoadToken token) noexcept
{
    if (token != 0 && current_.has_value() && current_->token == token) {
        current_.reset();
    }
}

}  // namespace hbfsim
