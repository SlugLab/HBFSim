#include "../../src/cuda_runtime/device_tensormap.hpp"
#include "../../src/cuda_runtime/device/hbf_device.cuh"

#include <hbfsim/tensormap.hpp>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <stdexcept>
#include <thread>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::TensorMapRecord record(std::uint64_t generation, std::byte tag)
{
    hbfsim::TensorMapRecord result;
    result.descriptor.fill(tag);
    result.descriptor_sha256 = hbfsim::tensormap_sha256(result.descriptor);
    result.generation = generation;
    result.base_address = 0x100000 + generation * 0x1000;
    result.shape.rank = 2;
    result.shape.global_dim = {64, 32, 0, 0, 0};
    result.shape.global_stride = {0, 256, 0, 0, 0};
    result.shape.box_dim = {16, 8, 0, 0, 0};
    result.shape.element_stride = {1, 1, 0, 0, 0};
    result.shape.lower_corner = {-2, -1, 0, 0, 0};
    result.shape.upper_corner = {2, 1, 0, 0, 0};
    result.shape.channels_per_pixel = 8;
    result.shape.pixels_per_column = 4;
    result.shape.wide_mode = 2;
    result.element_type = 2;
    result.fenced = true;
    return result;
}

}  // namespace

int main()
{
    const auto software_token = hbfsim::device::encode_tma_software_token(37, 9);
    require(hbfsim::device::is_tma_software_token(software_token) &&
                hbfsim::device::tma_software_token_slot(software_token) == 9 &&
                hbfsim::device::tma_software_token_generation(software_token) == 37 &&
                !hbfsim::device::is_tma_software_token(1),
            "TMA deferred-materialization token encoding differs");
    const auto timing_token = hbfsim::device::encode_tma_timing_token(41, 7);
    require(hbfsim::device::is_tma_timing_token(timing_token) &&
                !hbfsim::device::is_tma_software_token(timing_token) &&
                hbfsim::device::tma_tracked_token_slot(timing_token) == 7 &&
                hbfsim::device::tma_tracked_token_generation(timing_token) ==
                    41,
            "TMA timing-state token encoding differs");
    require(hbfsim::device::tma_reduction_supported(7, 0) &&
                hbfsim::device::tma_reduction_supported(5, 6) &&
                !hbfsim::device::tma_reduction_supported(7, 6) &&
                !hbfsim::device::tma_reduction_supported(13, 0),
            "TMA reduction element/operator matrix differs");
    require(hbfsim::device::tma_barrier_target_mask(0x5, 0, 1) == 0x5 &&
                hbfsim::device::tma_barrier_target_mask(0x5, 0, 2) == 0x5 &&
                hbfsim::device::tma_barrier_target_mask(0x5, 1, 2) == 0xa &&
                hbfsim::device::tma_barrier_target_mask(0x3, 1, 2) == 0x2 &&
                hbfsim::device::tma_barrier_target_mask(0, 1, 2) == 0,
            "CTA-group barrier parity routing differs");
    require(hbfsim::device::tma_data_target_mask(0, 3, 7, 0) == 0x8 &&
                hbfsim::device::tma_data_target_mask(0, 3, 7, 1) == 0x80 &&
                hbfsim::device::tma_data_target_mask(0x25, 3, 7, 1) == 0x25 &&
                hbfsim::device::tma_data_target_mask(0, 16, 7, 0) == 0 &&
                hbfsim::device::tma_data_target_mask(0, 3, 16, 1) == 0 &&
                hbfsim::device::tma_data_target_mask(0x10000, 3, 7, 1) == 0,
            "cluster TMA data target routing differs");

    hbfsim::device::SharedTensorMapSlot update{};
    update.base_address = 0x1000;
    update.rank = 2;
    update.global_dim[0] = 64;
    update.global_dim[1] = 16;
    update.global_stride[1] = 256;
    update.box_dim[0] = 64;
    update.box_dim[1] = 1;
    update.element_stride[0] = 1;
    update.element_stride[1] = 1;
    update.element_type = 2;
    require(hbfsim::device::apply_tensormap_replace(
                update, hbfsim::device::TensorMapReplaceField::GlobalAddress,
                0, 0x8000) && update.base_address == 0x8000 &&
                hbfsim::device::apply_tensormap_replace(
                    update, hbfsim::device::TensorMapReplaceField::Rank,
                    0, 0) && update.rank == 1 &&
                hbfsim::device::apply_tensormap_replace(
                    update, hbfsim::device::TensorMapReplaceField::GlobalDim,
                    0, 32) && update.global_dim[0] == 32 &&
                !hbfsim::device::apply_tensormap_replace(
                    update, hbfsim::device::TensorMapReplaceField::Rank,
                    0, 5),
            "device TensorMap structured replace differs");

    constexpr std::uint32_t ring_capacity = 8;
    const auto bytes =
        hbfsim::host_service::control_region_bytes(ring_capacity);
    void* storage = nullptr;
    require(::posix_memalign(&storage, 64, bytes) == 0,
            "control allocation failed");
    hbfsim::host_service::ControlView control(storage, bytes);
    require(control.initialize(ring_capacity), "control initialization failed");
    hbfsim::runtime::DeviceTensorMapTable table(control);
    require(hbfsim::runtime::bind_device_tensormap_domain(
                0xCA00, 3, control),
            "device TensorMap domain bind failed");

    auto first = record(1, std::byte{0x11});
    std::array<std::byte, 32> device_digest{};
    hbfsim::device::tensormap_sha256_bytes(first.descriptor.data(),
                                           device_digest.data());
    require(device_digest == first.descriptor_sha256,
            "device TensorMap SHA-256 differs from host provenance hash");
    require(hbfsim_publish_tensormap_device_v1(0xCA00, 3, &first) == 0,
            "device TensorMap callback publish failed");
    auto found = table.lookup(first.descriptor_sha256, 1);
    require(found && found->publication_generation == 1 &&
                found->descriptor_generation == 1 &&
                found->base_address == first.base_address &&
                found->lower_corner[0] == -2 &&
                found->upper_corner[1] == 1 &&
                found->channels_per_pixel == 8 &&
                found->pixels_per_column == 4 &&
                found->wide_mode == 2 &&
                found->fenced == 1,
            "device TensorMap slot differs");
    require(hbfsim::device::find_tensormap_slot(
                reinterpret_cast<const hbfsim::device::SharedTensorMapSlot*>(
                    control.tensormap_slots()),
                control.header()->tensormap_count,
                first.descriptor_sha256.data(), 1) == 0,
            "device reference lookup differs");

    hbfsim::device::SharedTensorMapSlot mixed{};
    mixed.base_address = 0x1000;
    mixed.rank = 1;
    mixed.global_dim[0] = 32;
    mixed.box_dim[0] = 8;
    mixed.element_stride[0] = 1;
    mixed.element_type = 2;
    hbfsim::device::SharedRangeRecord timing_ranges[2]{};
    timing_ranges[0].base = 0x1008;
    timing_ranges[0].length = 8;
    timing_ranges[0].mode = 1;
    timing_ranges[0].permissions = 3;
    timing_ranges[1].base = 0x1018;
    timing_ranges[1].length = 4;
    timing_ranges[1].mode = 1;
    timing_ranges[1].permissions = 3;
    const std::int32_t zero[5]{};
    const auto mixed_classification = hbfsim::device::classify_tma_tiled(
        mixed, zero, timing_ranges, 2, 0);
    require(mixed_classification.valid &&
                mixed_classification.hbm_bytes == 20 &&
                mixed_classification.hbf_bytes == 12 &&
                mixed_classification.oob_bytes == 0 &&
                !mixed_classification.capacity,
            "mixed HBM/HBF device classification differs");

    const std::int32_t tail[5]{30, 0, 0, 0, 0};
    const auto oob_classification = hbfsim::device::classify_tma_tiled(
        mixed, tail, timing_ranges, 2, 0);
    require(oob_classification.valid &&
                oob_classification.hbm_bytes == 8 &&
                oob_classification.hbf_bytes == 0 &&
                oob_classification.oob_bytes == 24,
            "runtime coordinate/OOB classification differs");

    timing_ranges[0].mode = 2;
    const auto capacity_classification = hbfsim::device::classify_tma_tiled(
        mixed, zero, timing_ranges, 2, 0);
    require(capacity_classification.valid &&
                capacity_classification.capacity,
            "capacity-backed tile classification differs");

    hbfsim::device::SharedTensorMapSlot strided{};
    strided.base_address = 0x2000;
    strided.rank = 2;
    strided.global_dim[0] = 8;
    strided.global_dim[1] = 4;
    strided.global_stride[1] = 64;
    strided.box_dim[0] = 4;
    strided.box_dim[1] = 2;
    strided.element_stride[0] = 1;
    strided.element_stride[1] = 1;
    strided.element_type = 2;
    hbfsim::device::SharedRangeRecord strided_range{};
    strided_range.base = 0x2088;
    strided_range.length = 8;
    strided_range.mode = 1;
    strided_range.permissions = 3;
    const std::int32_t strided_origin[5]{2, 1, 0, 0, 0};
    const auto strided_classification = hbfsim::device::classify_tma_tiled(
        strided, strided_origin, &strided_range, 1, 0);
    require(strided_classification.valid &&
                strided_classification.hbm_bytes == 24 &&
                strided_classification.hbf_bytes == 8,
            "strided/nonzero TensorMap classification differs");

    hbfsim::device::SharedTensorMapSlot gather{};
    gather.base_address = 0x3000;
    gather.rank = 2;
    gather.global_dim[0] = 8;
    gather.global_dim[1] = 8;
    gather.global_stride[1] = 32;
    gather.box_dim[0] = 4;
    gather.box_dim[1] = 1;
    gather.element_stride[0] = 1;
    gather.element_stride[1] = 1;
    gather.element_type = 2;
    hbfsim::device::SharedRangeRecord gather_range{};
    gather_range.base = 0x3044;
    gather_range.length = 8;
    gather_range.mode = 1;
    gather_range.permissions = 3;
    const std::int32_t gather_coordinates[5]{1, 0, 2, 4, 6};
    const auto gather_classification = hbfsim::device::classify_tma_access(
        gather, gather_coordinates, &gather_range, 1, 0, 1);
    require(gather_classification.valid &&
                gather_classification.hbm_bytes == 56 &&
                gather_classification.hbf_bytes == 8 &&
                gather_classification.oob_bytes == 0,
            "gather4 device classification differs");
    const auto gathered_row = hbfsim::device::tma_element_address(
        gather, gather_coordinates, 1, 4);
    require(gathered_row.valid && !gathered_row.oob &&
                gathered_row.global_address == 0x3044 &&
                gathered_row.destination_offset == 16 &&
                gathered_row.bytes == 4,
            "gather4 element address differs");
    const auto scatter_classification = hbfsim::device::classify_tma_access(
        gather, gather_coordinates, &gather_range, 1, 1, 2);
    require(scatter_classification.valid &&
                scatter_classification.hbm_bytes == 56 &&
                scatter_classification.hbf_bytes == 8,
            "scatter4 device classification differs");
    gather_range.mode = 2;
    require(hbfsim::device::classify_tma_access(
                gather, gather_coordinates, &gather_range, 1, 0, 1)
                .capacity,
            "gather4 capacity classification differs");

    hbfsim::device::SharedTensorMapSlot im2col{};
    im2col.base_address = 0x4000;
    im2col.rank = 3;
    im2col.mode = 1;
    im2col.global_dim[0] = 4;
    im2col.global_dim[1] = 4;
    im2col.global_dim[2] = 1;
    im2col.global_stride[1] = 16;
    im2col.global_stride[2] = 64;
    im2col.box_dim[0] = 2;
    im2col.box_dim[1] = 2;
    im2col.box_dim[2] = 1;
    im2col.element_stride[0] = 1;
    im2col.element_stride[1] = 2;
    im2col.element_stride[2] = 1;
    im2col.lower_corner[0] = -1;
    im2col.upper_corner[0] = 0;
    im2col.channels_per_pixel = 2;
    im2col.pixels_per_column = 2;
    im2col.element_type = 2;
    const std::int32_t im2col_coordinates[5]{};
    const auto im2col_classification = hbfsim::device::classify_tma_access(
        im2col, im2col_coordinates, nullptr, 0, 0, 3);
    require(im2col_classification.valid &&
                im2col_classification.hbm_bytes == 8 &&
                im2col_classification.hbf_bytes == 0 &&
                im2col_classification.oob_bytes == 8,
            "im2col signed-halo device classification differs");
    const auto halo = hbfsim::device::tma_element_address(
        im2col, im2col_coordinates, 3, 0);
    const auto image = hbfsim::device::tma_element_address(
        im2col, im2col_coordinates, 3, 2);
    require(halo.valid && halo.oob && image.valid && !image.oob &&
                image.global_address == 0x4010 &&
                image.destination_offset == 8,
            "im2col element addresses differ");
    const std::int32_t im2col_offsets[3]{1, 0, 0};
    const auto offset_image = hbfsim::device::tma_element_address(
        im2col, im2col_coordinates, 3, 0, im2col_offsets);
    require(offset_image.valid && !offset_image.oob &&
                offset_image.global_address == 0x4000,
            "runtime im2col offsets were not applied");
    auto im2col_store = im2col;
    im2col_store.lower_corner[0] = 0;
    im2col_store.pixels_per_column = 1;
    const auto im2col_store_classification =
        hbfsim::device::classify_tma_access(
            im2col_store, im2col_coordinates, nullptr, 0, 1, 3);
    const std::int32_t negative_im2col_coordinates[5]{0, -1, 0, 0, 0};
    require(im2col_store_classification.valid &&
                im2col_store_classification.hbm_bytes == 8 &&
                !hbfsim::device::classify_tma_access(
                    im2col_store, negative_im2col_coordinates, nullptr, 0,
                    1, 3).valid,
            "im2col_no_offs store/OOB restriction differs");
    im2col.mode = 2;
    im2col.wide_mode = 0;
    im2col.swizzle = 1;
    const auto wide_classification = hbfsim::device::classify_tma_access(
        im2col, im2col_coordinates, nullptr, 0, 0, 4);
    require(wide_classification.valid &&
                wide_classification.hbm_bytes == 8 &&
                wide_classification.oob_bytes == 8,
            "im2col wide device classification differs");
    const std::int32_t wide_coordinates[5]{0, -1, 0, 0, 0};
    const std::int32_t wide_offsets[3]{0, 1, 0};
    const auto wide_offset_image = hbfsim::device::tma_element_address(
        im2col, wide_coordinates, 4, 0, wide_offsets);
    require(wide_offset_image.valid && !wide_offset_image.oob &&
                wide_offset_image.global_address == 0x4000,
            "im2col wide wOffset did not adjust box and W coordinate");
    auto wide_layout = im2col;
    wide_layout.global_dim[0] = 16;
    wide_layout.global_dim[1] = 4;
    wide_layout.global_stride[1] = 64;
    wide_layout.channels_per_pixel = 16;
    wide_layout.pixels_per_column = 4;
    wide_layout.swizzle = 2;
    const auto wide_second_pixel = hbfsim::device::tma_element_address(
        wide_layout, im2col_coordinates, 4, 16);
    const auto wide_third_pixel = hbfsim::device::tma_element_address(
        wide_layout, im2col_coordinates, 4, 32);
    const auto wide_third_pixel_second_atom =
        hbfsim::device::tma_element_address(
            wide_layout, im2col_coordinates, 4, 36);
    require(wide_second_pixel.valid && wide_third_pixel.valid &&
                wide_third_pixel_second_atom.valid &&
                wide_second_pixel.destination_offset == 64 &&
                wide_third_pixel.destination_offset == 144 &&
                wide_third_pixel_second_atom.destination_offset == 128,
            "im2col wide shared column order differs from native SM120");
    const std::int32_t wide_bad_halo[3]{512, 0, 0};
    const std::int32_t wide_bad_offset[3]{0, 32, 0};
    require(hbfsim::device::tma_access_element_count(
                im2col, 4, wide_bad_halo) == 0 &&
                hbfsim::device::tma_access_element_count(
                    im2col, 4, wide_bad_offset) == 0,
            "im2col::w operand limits were not enforced");
    im2col.wide_mode = 1;
    const std::int32_t w128_offsets[3]{3, 31, 0};
    const std::int32_t w128_bad_halo[3]{32, 0, 0};
    require(hbfsim::device::tma_access_element_count(
                im2col, 4, w128_offsets) == 280 &&
                hbfsim::device::tma_access_element_count(
                    im2col, 4, w128_bad_halo) == 0,
            "im2col::w::128 halo count/limit differs");

    hbfsim::device::SharedTensorMapSlot interleaved{};
    interleaved.base_address = 0x5000;
    interleaved.rank = 3;
    interleaved.global_dim[0] = 16;
    interleaved.global_dim[1] = 2;
    interleaved.global_dim[2] = 1;
    interleaved.global_stride[1] = 16;
    interleaved.global_stride[2] = 64;
    interleaved.box_dim[0] = 1;
    interleaved.box_dim[1] = 1;
    interleaved.box_dim[2] = 1;
    interleaved.element_stride[0] = 1;
    interleaved.element_stride[1] = 1;
    interleaved.element_stride[2] = 1;
    interleaved.element_type = 1;
    interleaved.interleave = 1;
    const std::int32_t interleaved_coordinates[5]{9, 0, 0, 0, 0};
    const auto interleaved_element = hbfsim::device::tma_element_address(
        interleaved, interleaved_coordinates, 0, 0);
    require(interleaved_element.valid && !interleaved_element.oob &&
                interleaved_element.global_address == 0x5022,
            "NC/8WC8 interleaved global address differs");

    hbfsim::device::SharedTensorMapSlot traversed{};
    traversed.base_address = 0x5800;
    traversed.rank = 2;
    traversed.global_dim[0] = 16;
    traversed.global_dim[1] = 16;
    traversed.global_stride[1] = 64;
    traversed.box_dim[0] = 4;
    traversed.box_dim[1] = 5;
    traversed.element_stride[0] = 8;  // Ignored without interleave.
    traversed.element_stride[1] = 2;
    traversed.element_type = 2;
    const std::int32_t traversed_coordinates[5]{};
    const auto traversed_last = hbfsim::device::tma_element_address(
        traversed, traversed_coordinates, 0, 11);
    require(hbfsim::device::tma_access_element_count(traversed, 0) == 12 &&
                traversed_last.valid &&
                traversed_last.global_address == 0x590c &&
                traversed_last.destination_offset == 44,
            "tiled traversal-stride count/address differs");

    hbfsim::device::SharedTensorMapSlot packed{};
    packed.base_address = 0x6000;
    packed.rank = 1;
    packed.global_dim[0] = 128;
    packed.box_dim[0] = 32;
    packed.element_stride[0] = 1;
    packed.element_type = 13;
    const std::int32_t packed_coordinates[5]{};
    const auto b4_second = hbfsim::device::tma_element_address(
        packed, packed_coordinates, 0, 1);
    require(hbfsim::device::tma_access_element_count(packed, 0) == 2 &&
                b4_second.valid && b4_second.global_address == 0x6008 &&
                b4_second.destination_offset == 8 &&
                b4_second.bytes == 8 && b4_second.shared_bytes == 8,
            "b4x16 packed tile address differs");

    packed.global_dim[0] = 128;
    packed.box_dim[0] = 128;
    packed.element_type = 14;
    const auto b4p_last = hbfsim::device::tma_element_address(
        packed, packed_coordinates, 0, 7);
    require(hbfsim::device::tma_access_element_count(packed, 0) == 8 &&
                b4p_last.valid && b4p_last.global_address == 0x6038 &&
                b4p_last.destination_offset == 112 &&
                b4p_last.bytes == 8 && b4p_last.shared_bytes == 16 &&
                !hbfsim::device::classify_tma_access(
                    packed, packed_coordinates, nullptr, 0, 1, 0).valid,
            "b4x16_p64 packed tile restrictions differ");

    packed.element_type = 15;
    const auto b6_last = hbfsim::device::tma_element_address(
        packed, packed_coordinates, 0, 7);
    require(b6_last.valid && b6_last.global_address == 0x6054 &&
                b6_last.destination_offset == 112 &&
                b6_last.bytes == 12 && b6_last.shared_bytes == 16,
            "b6x16_p32 packed tile address differs");

    hbfsim::device::SharedTensorMapSlot swizzled{};
    swizzled.base_address = 0x7000;
    swizzled.rank = 2;
    swizzled.global_dim[0] = 512;
    swizzled.global_dim[1] = 16;
    swizzled.global_stride[1] = 512;
    swizzled.box_dim[0] = 32;
    swizzled.box_dim[1] = 2;
    swizzled.element_stride[0] = 1;
    swizzled.element_stride[1] = 1;
    swizzled.element_type = 0;
    swizzled.swizzle = 3;
    const std::int32_t swizzled_coordinates[5]{};
    const auto narrow_second_row = hbfsim::device::tma_element_address(
        swizzled, swizzled_coordinates, 0, 32);
    const auto base_offset_first = hbfsim::device::tma_element_address(
        swizzled, swizzled_coordinates, 0, 0, nullptr, 128);
    require(narrow_second_row.valid &&
                narrow_second_row.destination_offset == 144 &&
                base_offset_first.valid &&
                base_offset_first.destination_offset == 16,
            "128B swizzle row pitch/base offset differs");

    swizzled.box_dim[0] = 128;
    swizzled.swizzle_atomicity = 1;
    const auto atom32 = hbfsim::device::tma_element_address(
        swizzled, swizzled_coordinates, 0, 128);
    swizzled.swizzle_atomicity = 2;
    const auto atom32_flip = hbfsim::device::tma_element_address(
        swizzled, swizzled_coordinates, 0, 128);
    swizzled.swizzle_atomicity = 3;
    const auto atom64 = hbfsim::device::tma_element_address(
        swizzled, swizzled_coordinates, 0, 128);
    require(atom32.valid && atom32.destination_offset == 160 &&
                atom32_flip.valid &&
                atom32_flip.destination_offset == 168 &&
                atom64.valid && atom64.destination_offset == 192,
            "128B swizzle atomicity mapping differs");

    swizzled.swizzle = 4;
    swizzled.swizzle_atomicity = 0;
    swizzled.box_dim[0] = 96;
    const auto swizzle96 = hbfsim::device::tma_element_address(
        swizzled, swizzled_coordinates, 0, 96);
    require(swizzle96.valid && swizzle96.destination_offset == 112,
            "96B swizzle mapping differs");

    swizzled.swizzle = 3;
    swizzled.swizzle_atomicity = 1;
    require(!hbfsim::device::tma_copy_supported(
                swizzled, 0, 0, 1) &&
                hbfsim::device::tma_copy_supported(swizzled, 0, 0, 0) &&
                hbfsim::device::tma_software_copy_supported(
                    swizzled, 0, 0, 1),
            "sm_120a cluster swizzle-atomicity restriction differs");
    packed.element_type = 13;
    packed.box_dim[0] = 32;
    require(!hbfsim::device::tma_copy_supported(packed, 0, 0, 1) &&
                hbfsim::device::tma_copy_supported(packed, 0, 0, 0) &&
                hbfsim::device::tma_software_copy_supported(
                    packed, 0, 0, 1),
            "sm_120a cluster sub-byte restriction differs");
    packed.element_type = 14;
    packed.box_dim[0] = 128;
    packed.global_dim[0] = 128;
    packed.base_address = 0x8000;
    require(hbfsim::device::tma_software_copy_supported(
                packed, 0, 0, 0) &&
                !hbfsim::device::tma_software_copy_supported(
                    packed, 1, 0, 0),
            "software TMA accepted an undefined packed copy direction");
    std::byte unpacked[16]{};
    for (std::uint32_t index = 0; index < 16; ++index) {
        unpacked[index] = static_cast<std::byte>(index | 0xc0U);
    }
    std::byte packed_b6[12]{};
    require(hbfsim::device::tma_pack_b6p2x16(unpacked, packed_b6),
            "b6p2x16 packing failed");
    std::uint32_t bit = 0;
    for (std::uint32_t index = 0; index < 16; ++index, bit += 6) {
        const auto byte = bit / 8;
        const auto shift = bit % 8;
        auto value = static_cast<std::uint32_t>(packed_b6[byte]) >> shift;
        if (shift > 2) {
            value |= static_cast<std::uint32_t>(packed_b6[byte + 1])
                     << (8 - shift);
        }
        require((value & 0x3fU) == index,
                "b6p2x16 packed value differs");
    }

    auto replacement = record(2, std::byte{0x22});
    require(table.publish(replacement) &&
                table.lookup(replacement.descriptor_sha256, 2) &&
                !table.lookup(replacement.descriptor_sha256, 1),
            "generation-safe device replacement differs");

    std::atomic_bool failed{false};
    std::vector<std::thread> readers;
    for (int thread = 0; thread < 8; ++thread) {
        readers.emplace_back([&] {
            for (int iteration = 0; iteration < 10000; ++iteration) {
                const auto value = table.lookup(first.descriptor_sha256, 1);
                if (!value || value->base_address != first.base_address) {
                    failed = true;
                }
            }
        });
    }
    for (std::uint64_t generation = 3;
         generation <= hbfsim::host_service::kTensorMapCapacity;
         ++generation) {
        require(table.publish(record(
                    generation,
                    static_cast<std::byte>(generation & 0xffU))),
                "bounded slot fixture publish failed");
    }
    for (auto& reader : readers) reader.join();
    require(!failed, "concurrent immutable slot reader was inconsistent");
    require(!table.publish(record(999, std::byte{0x77})),
            "TensorMap slot capacity was not enforced");

    hbfsim::runtime::unbind_device_tensormap_domain(0xCA00, 3);
    require(control.header()->tensormap_count == 0 &&
                !table.lookup(first.descriptor_sha256, 1),
            "stale slot invalidation failed");
    std::free(storage);
    return 0;
}
