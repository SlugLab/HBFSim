#include <hbfsim/tensormap.hpp>

#include <cuda.h>
#include <dlfcn.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <utility>

namespace {

static_assert(sizeof(CUtensorMap) == 128);

struct Domain {
    std::uintptr_t context{0};
    int device{-1};
    bool valid() const noexcept { return context != 0 && device >= 0; }
};

Domain current_domain() noexcept
{
    using context_type = CUresult (*)(CUcontext*);
    using device_type = CUresult (*)(CUdevice*);
    static auto get_context = reinterpret_cast<context_type>(
        dlsym(RTLD_NEXT, "cuCtxGetCurrent"));
    static auto get_device = reinterpret_cast<device_type>(
        dlsym(RTLD_NEXT, "cuCtxGetDevice"));
    CUcontext context = nullptr;
    CUdevice device = -1;
    if (get_context == nullptr || get_device == nullptr ||
        get_context(&context) != CUDA_SUCCESS || context == nullptr ||
        get_device(&device) != CUDA_SUCCESS || device < 0) {
        return {};
    }
    return {.context = reinterpret_cast<std::uintptr_t>(context),
            .device = static_cast<int>(device)};
}

std::array<std::byte, 128> descriptor_bytes(const CUtensorMap& descriptor)
{
    std::array<std::byte, 128> result{};
    std::memcpy(result.data(), &descriptor, result.size());
    return result;
}

void fill_common(hbfsim::TensorMapRecord& record, CUtensorMapDataType type,
                 cuuint32_t rank, void* address, const cuuint64_t* dimensions,
                 const cuuint64_t* strides, const cuuint32_t* element_strides,
                 CUtensorMapInterleave interleave,
                 CUtensorMapSwizzle swizzle,
                 CUtensorMapL2promotion promotion,
                 CUtensorMapFloatOOBfill fill)
{
    record.base_address = reinterpret_cast<std::uintptr_t>(address);
    record.shape.rank = rank;
    for (std::uint32_t index = 0; index < rank && index < 5; ++index) {
        record.shape.global_dim[index] = dimensions[index];
        record.shape.element_stride[index] = element_strides[index];
        if (index != 0) record.shape.global_stride[index] = strides[index - 1];
    }
    constexpr std::uint32_t ptx_types[16]{
        0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 8, 11, 12, 13, 14, 15};
    const auto raw_type = static_cast<std::uint32_t>(type);
    record.element_type = raw_type < std::size(ptx_types)
                              ? ptx_types[raw_type]
                              : UINT32_MAX;
    record.interleave = static_cast<std::uint32_t>(interleave);
    const auto raw_swizzle = static_cast<std::uint32_t>(swizzle);
    record.swizzle = raw_swizzle <= 3 ? raw_swizzle : 3;
    record.swizzle_atomicity = raw_swizzle <= 3 ? 0 : raw_swizzle - 3;
    record.l2_promotion = static_cast<std::uint32_t>(promotion);
    record.oob_fill = static_cast<std::uint32_t>(fill);
}

bool valid_inputs(CUtensorMap* map, cuuint32_t rank, void* address,
                  const cuuint64_t* dimensions,
                  const cuuint64_t* strides,
                  const cuuint32_t* element_strides) noexcept
{
    return map != nullptr && rank >= 1 && rank <= 5 && address != nullptr &&
           dimensions != nullptr && element_strides != nullptr &&
           (rank == 1 || strides != nullptr);
}

void publish_record(const Domain& domain, hbfsim::TensorMapRecord record)
{
    // A host-created descriptor is immutable for the duration of a kernel
    // launch and CUDA launch ordering publishes it to the tensormap proxy.
    record.fenced = true;
    const auto descriptor = record.descriptor;
    if (!hbfsim::global_tensormap_registry().publish(
            domain.context, domain.device, std::move(record))) {
        return;
    }
    const auto published = hbfsim::global_tensormap_registry().lookup(
        domain.context, domain.device, descriptor);
    if (!published) return;
    using publish_type = int (*)(std::uintptr_t, int,
                                 const hbfsim::TensorMapRecord*) noexcept;
    static auto publish_device = reinterpret_cast<publish_type>(
        dlsym(RTLD_DEFAULT, "hbfsim_publish_tensormap_device_v1"));
    if (publish_device != nullptr) {
        (void)publish_device(domain.context, domain.device, &*published);
    }
}

}  // namespace

extern "C" CUresult cuTensorMapEncodeTiled(
    CUtensorMap* map, CUtensorMapDataType type, cuuint32_t rank, void* address,
    const cuuint64_t* dimensions, const cuuint64_t* strides,
    const cuuint32_t* box, const cuuint32_t* element_strides,
    CUtensorMapInterleave interleave, CUtensorMapSwizzle swizzle,
    CUtensorMapL2promotion promotion, CUtensorMapFloatOOBfill fill)
{
    using function_type = decltype(&cuTensorMapEncodeTiled);
    static auto original = reinterpret_cast<function_type>(
        dlsym(RTLD_NEXT, "cuTensorMapEncodeTiled"));
    if (original == nullptr) return CUDA_ERROR_NOT_INITIALIZED;
    const auto result = original(map, type, rank, address, dimensions, strides,
                                 box, element_strides, interleave, swizzle,
                                 promotion, fill);
    if (result != CUDA_SUCCESS || !valid_inputs(map, rank, address, dimensions,
                                                strides, element_strides) ||
        box == nullptr) {
        return result;
    }
    const auto domain = current_domain();
    if (!domain.valid()) return result;
    hbfsim::TensorMapRecord record;
    record.mode = hbfsim::TensorMapMode::Tiled;
    record.descriptor = descriptor_bytes(*map);
    fill_common(record, type, rank, address, dimensions, strides,
                element_strides, interleave, swizzle, promotion, fill);
    for (std::uint32_t index = 0; index < rank; ++index) {
        record.shape.box_dim[index] = box[index];
    }
    publish_record(domain, std::move(record));
    return result;
}

extern "C" CUresult cuTensorMapEncodeIm2col(
    CUtensorMap* map, CUtensorMapDataType type, cuuint32_t rank, void* address,
    const cuuint64_t* dimensions, const cuuint64_t* strides,
    const int* lower, const int* upper, cuuint32_t channels,
    cuuint32_t pixels, const cuuint32_t* element_strides,
    CUtensorMapInterleave interleave, CUtensorMapSwizzle swizzle,
    CUtensorMapL2promotion promotion, CUtensorMapFloatOOBfill fill)
{
    using function_type = decltype(&cuTensorMapEncodeIm2col);
    static auto original = reinterpret_cast<function_type>(
        dlsym(RTLD_NEXT, "cuTensorMapEncodeIm2col"));
    if (original == nullptr) return CUDA_ERROR_NOT_INITIALIZED;
    const auto result = original(map, type, rank, address, dimensions, strides,
                                 lower, upper, channels, pixels,
                                 element_strides, interleave, swizzle,
                                 promotion, fill);
    if (result != CUDA_SUCCESS || rank < 3 ||
        !valid_inputs(map, rank, address, dimensions, strides,
                      element_strides) || lower == nullptr || upper == nullptr) {
        return result;
    }
    const auto domain = current_domain();
    if (!domain.valid()) return result;
    hbfsim::TensorMapRecord record;
    record.mode = hbfsim::TensorMapMode::Im2col;
    record.descriptor = descriptor_bytes(*map);
    fill_common(record, type, rank, address, dimensions, strides,
                element_strides, interleave, swizzle, promotion, fill);
    const auto corners = std::min<std::uint32_t>(rank - 2, 3);
    for (std::uint32_t index = 0; index < corners; ++index) {
        record.shape.lower_corner[index] = lower[index];
        record.shape.upper_corner[index] = upper[index];
    }
    record.shape.channels_per_pixel = channels;
    record.shape.pixels_per_column = pixels;
    publish_record(domain, std::move(record));
    return result;
}

extern "C" CUresult cuTensorMapEncodeIm2colWide(
    CUtensorMap* map, CUtensorMapDataType type, cuuint32_t rank, void* address,
    const cuuint64_t* dimensions, const cuuint64_t* strides, int lower,
    int upper, cuuint32_t channels, cuuint32_t pixels,
    const cuuint32_t* element_strides, CUtensorMapInterleave interleave,
    CUtensorMapIm2ColWideMode mode, CUtensorMapSwizzle swizzle,
    CUtensorMapL2promotion promotion, CUtensorMapFloatOOBfill fill)
{
    using function_type = decltype(&cuTensorMapEncodeIm2colWide);
    static auto original = reinterpret_cast<function_type>(
        dlsym(RTLD_NEXT, "cuTensorMapEncodeIm2colWide"));
    if (original == nullptr) return CUDA_ERROR_NOT_INITIALIZED;
    const auto result = original(map, type, rank, address, dimensions, strides,
                                 lower, upper, channels, pixels,
                                 element_strides, interleave, mode, swizzle,
                                 promotion, fill);
    if (result != CUDA_SUCCESS || rank < 3 ||
        !valid_inputs(map, rank, address, dimensions, strides,
                      element_strides)) {
        return result;
    }
    const auto domain = current_domain();
    if (!domain.valid()) return result;
    hbfsim::TensorMapRecord record;
    record.mode = hbfsim::TensorMapMode::Im2colWide;
    record.descriptor = descriptor_bytes(*map);
    fill_common(record, type, rank, address, dimensions, strides,
                element_strides, interleave, swizzle, promotion, fill);
    record.shape.lower_corner[0] = lower;
    record.shape.upper_corner[0] = upper;
    record.shape.channels_per_pixel = channels;
    record.shape.pixels_per_column = pixels;
    record.shape.wide_mode = static_cast<std::uint32_t>(mode);
    publish_record(domain, std::move(record));
    return result;
}

extern "C" CUresult cuTensorMapReplaceAddress(CUtensorMap* map,
                                                void* address)
{
    using function_type = decltype(&cuTensorMapReplaceAddress);
    static auto original = reinterpret_cast<function_type>(
        dlsym(RTLD_NEXT, "cuTensorMapReplaceAddress"));
    if (original == nullptr) return CUDA_ERROR_NOT_INITIALIZED;
    std::array<std::byte, 128> before{};
    if (map != nullptr) before = descriptor_bytes(*map);
    const auto result = original(map, address);
    if (result != CUDA_SUCCESS || map == nullptr || address == nullptr) {
        return result;
    }
    const auto domain = current_domain();
    if (domain.valid()) {
        const auto after = descriptor_bytes(*map);
        const auto replaced =
            hbfsim::global_tensormap_registry().replace_address(
            domain.context, domain.device, before, after,
            reinterpret_cast<std::uintptr_t>(address));
        if (replaced) {
            // Host descriptor mutation is ordered before a later launch by
            // the CUDA API call boundary, so publish the new generation as a
            // launch-visible fenced descriptor.
            (void)hbfsim::global_tensormap_registry().fence(
                domain.context, domain.device, after);
            const auto record = hbfsim::global_tensormap_registry().lookup(
                domain.context, domain.device, after);
            using publish_type = int (*)(
                std::uintptr_t, int,
                const hbfsim::TensorMapRecord*) noexcept;
            static auto publish_device = reinterpret_cast<publish_type>(
                dlsym(RTLD_DEFAULT,
                      "hbfsim_publish_tensormap_device_v1"));
            if (record && publish_device != nullptr) {
                (void)publish_device(domain.context, domain.device, &*record);
            }
        }
    }
    return result;
}

extern "C" int hbfsim_tensormap_lookup_for_test(
    std::uintptr_t context, int device, const CUtensorMap* map,
    std::uint64_t* generation, std::uintptr_t* address,
    std::uint32_t* mode) noexcept
{
    if (map == nullptr) return -1;
    const auto bytes = descriptor_bytes(*map);
    const auto record = hbfsim::global_tensormap_registry().lookup(
        context, device, bytes);
    if (!record) return -1;
    if (generation != nullptr) *generation = record->generation;
    if (address != nullptr) *address = record->base_address;
    if (mode != nullptr) *mode = static_cast<std::uint32_t>(record->mode);
    return 0;
}
