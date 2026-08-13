#include <hbfsim/tensormap.hpp>

#include <openssl/sha.h>

#include <algorithm>
#include <cstring>
#include <limits>
#include <mutex>

namespace hbfsim {
namespace {

bool valid_record(const TensorMapRecord& record) noexcept
{
    return record.base_address != 0 && record.shape.rank >= 1 &&
           record.shape.rank <= 5;
}

bool same_descriptor(const TensorMapRecord& record,
                     std::span<const std::byte, 128> descriptor) noexcept
{
    return std::equal(record.descriptor.begin(), record.descriptor.end(),
                      descriptor.begin(), descriptor.end());
}

template <typename Records>
std::optional<std::size_t> latest_record(
    const Records& records,
    std::span<const std::byte, 128> descriptor)
{
    for (std::size_t index = records.records.size(); index != 0; --index) {
        if (same_descriptor(records.records[index - 1], descriptor)) {
            return index - 1;
        }
    }
    return std::nullopt;
}

}  // namespace

std::array<std::byte, 32> tensormap_sha256(
    std::span<const std::byte, 128> descriptor)
{
    std::array<std::byte, 32> result{};
    SHA256(reinterpret_cast<const unsigned char*>(descriptor.data()),
           descriptor.size(),
           reinterpret_cast<unsigned char*>(result.data()));
    return result;
}

std::size_t TensorMapRegistry::DomainHash::operator()(
    const Domain& value) const noexcept
{
    const auto first = std::hash<std::uintptr_t>{}(value.context);
    const auto second = std::hash<int>{}(value.device);
    return first ^ (second + 0x9e3779b9U + (first << 6) + (first >> 2));
}

bool TensorMapRegistry::publish(std::uintptr_t context, int device,
                                TensorMapRecord record)
{
    if (context == 0 || device < 0 || !valid_record(record)) return false;
    record.descriptor_sha256 = tensormap_sha256(record.descriptor);
    record.fenced = false;
    std::unique_lock lock(mutex_);
    auto& domain = domains_[{context, device}];
    if (domain.next_generation == 0) return false;
    record.generation = domain.next_generation++;
    domain.records.push_back(std::move(record));
    return true;
}

std::optional<TensorMapRecord> TensorMapRegistry::lookup(
    std::uintptr_t context, int device,
    std::span<const std::byte, 128> descriptor) const
{
    std::shared_lock lock(mutex_);
    const auto domain = domains_.find({context, device});
    if (domain == domains_.end()) return std::nullopt;
    const auto found = latest_record(domain->second, descriptor);
    return found ? std::optional<TensorMapRecord>{
                       domain->second.records[*found]}
                 : std::nullopt;
}

std::optional<TensorMapRecord> TensorMapRegistry::lookup_fenced(
    std::uintptr_t context, int device,
    std::span<const std::byte, 128> descriptor) const
{
    auto record = lookup(context, device, descriptor);
    return record && record->fenced ? record : std::nullopt;
}

bool TensorMapRegistry::replace_address(
    std::uintptr_t context, int device,
    std::span<const std::byte, 128> before,
    std::span<const std::byte, 128> after, std::uintptr_t new_address)
{
    if (context == 0 || device < 0 || new_address == 0) return false;
    std::unique_lock lock(mutex_);
    auto domain = domains_.find({context, device});
    if (domain == domains_.end()) return false;
    const auto source = latest_record(domain->second, before);
    if (!source || domain->second.next_generation == 0) return false;
    auto replacement = domain->second.records[*source];
    std::copy(after.begin(), after.end(), replacement.descriptor.begin());
    replacement.descriptor_sha256 = tensormap_sha256(after);
    replacement.base_address = new_address;
    replacement.generation = domain->second.next_generation++;
    replacement.fenced = false;
    domain->second.records.push_back(std::move(replacement));
    return true;
}

bool TensorMapRegistry::copy_descriptor(
    std::uintptr_t context, int device,
    std::span<const std::byte, 128> source,
    std::span<const std::byte, 128> destination)
{
    if (context == 0 || device < 0) return false;
    std::unique_lock lock(mutex_);
    auto domain = domains_.find({context, device});
    if (domain == domains_.end()) return false;
    const auto found = latest_record(domain->second, source);
    if (!found || domain->second.next_generation == 0) return false;
    auto copy = domain->second.records[*found];
    std::copy(destination.begin(), destination.end(), copy.descriptor.begin());
    copy.descriptor_sha256 = tensormap_sha256(destination);
    copy.generation = domain->second.next_generation++;
    copy.fenced = false;
    domain->second.records.push_back(std::move(copy));
    return true;
}

bool TensorMapRegistry::fence(
    std::uintptr_t context, int device,
    std::span<const std::byte, 128> descriptor)
{
    std::unique_lock lock(mutex_);
    auto domain = domains_.find({context, device});
    if (domain == domains_.end()) return false;
    const auto found = latest_record(domain->second, descriptor);
    if (!found) return false;
    domain->second.records[*found].fenced = true;
    return true;
}

void TensorMapRegistry::erase_context(std::uintptr_t context)
{
    std::unique_lock lock(mutex_);
    std::erase_if(domains_, [context](const auto& entry) {
        return entry.first.context == context;
    });
}

void TensorMapRegistry::clear()
{
    std::unique_lock lock(mutex_);
    domains_.clear();
}

TensorMapRegistry& global_tensormap_registry()
{
    static TensorMapRegistry registry;
    return registry;
}

}  // namespace hbfsim
