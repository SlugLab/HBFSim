// SPDX-License-Identifier: Apache-2.0
// Copy BF16 payloads from HBFSim logical capacity mappings into ordinary CUDA
// tensors.  Only the ordinary destination tensors are passed to fused MoE.

#include <stdint.h>

extern "C" __global__ void hbfsim_capacity_copy_bf16(
    const uint8_t* __restrict__ source_base,
    uint8_t* __restrict__ destination_base,
    uint64_t source_offset_bytes,
    uint64_t destination_offset_bytes,
    uint64_t elements) {
  const uint16_t* source = reinterpret_cast<const uint16_t*>(
      source_base + source_offset_bytes);
  uint16_t* destination = reinterpret_cast<uint16_t*>(
      destination_base + destination_offset_bytes);
  const uint64_t index =
      static_cast<uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  const uint64_t stride =
      static_cast<uint64_t>(blockDim.x) * gridDim.x;
  for (uint64_t i = index; i < elements; i += stride) {
    destination[i] = source[i];
  }
}
