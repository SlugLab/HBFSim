#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

// Host-only, exact QKV aggregate adapter. The source has one 152-byte
// parameter; the recovered image has 19 independent slots. No CUDA calls.
namespace qkv_exact_abi {
constexpr std::size_t kAggregateBytes = 152;
constexpr std::size_t kSlotCount = 19;
constexpr std::array<std::size_t, kSlotCount> kWidths = {
    8, 8, 8, 8, 8, 8, 8, 8, 8, 4, 8, 8, 8, 8, 4, 8, 8, 8, 4};

struct alignas(8) Prepared {
  std::array<std::array<std::uint8_t, 8>, kSlotCount> cells{};
  std::array<void*, kSlotCount> parameters{};
};

// Query returns the CUDA result as an int; the caller supplies the actual
// driver's cuFuncGetParamInfo. CUDA_ERROR_INVALID_VALUE is 1 in CUDA 12.8.
template <typename Query>
bool validate_original_metadata(Query query, void* original) {
  std::size_t offset = 0, width = 0;
  if (query(original, 0, &offset, &width) != 0 || offset != 0 ||
      width != kAggregateBytes) return false;
  return query(original, 1, &offset, &width) == 1;
}

template <typename Query>
bool validate_metadata(Query query, void* original, void* candidate) {
  if (!validate_original_metadata(query, original)) return false;
  std::size_t offset = 0, width = 0;
  for (std::size_t i = 0; i < kSlotCount; ++i) {
    offset = width = 0;
    if (query(candidate, i, &offset, &width) != 0 || offset != 8 * i ||
        width != kWidths[i]) return false;
  }
  return query(candidate, kSlotCount, &offset, &width) == 1;
}

inline bool convert(void** aggregate_parameters, void** extra,
                    Prepared* output) {
  if (!output || extra || !aggregate_parameters ||
      !aggregate_parameters[0]) return false;
  const auto* source = static_cast<const std::uint8_t*>(aggregate_parameters[0]);
  for (std::size_t i = 0; i < kSlotCount; ++i) {
    std::memcpy(output->cells[i].data(), source + 8 * i, kWidths[i]);
    output->parameters[i] = output->cells[i].data();
  }
  return true;
}
}  // namespace qkv_exact_abi
