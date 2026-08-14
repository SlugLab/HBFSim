#include <cuda.h>
#include <cuda/ptx>
#include <cuda_runtime_api.h>
#include <json.hpp>
#include <openssl/evp.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using json = nlohmann::json;

[[noreturn]] void fail(const std::string& message)
{
    throw std::runtime_error(message);
}

void cuda_check(cudaError_t status, const char* operation)
{
    if (status != cudaSuccess)
        fail(std::string(operation) + ": " + cudaGetErrorString(status));
}

void driver_check(CUresult status, const char* operation)
{
    if (status == CUDA_SUCCESS) return;
    const char* text = nullptr;
    (void)cuGetErrorString(status, &text);
    fail(std::string(operation) + ": " +
         (text == nullptr ? "unknown CUDA error" : text));
}

std::string sha256(const void* data, std::size_t bytes)
{
    std::array<unsigned char, 32> digest{};
    unsigned int size = 0;
    auto* context = EVP_MD_CTX_new();
    if (context == nullptr || EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1 ||
        EVP_DigestUpdate(context, data, bytes) != 1 ||
        EVP_DigestFinal_ex(context, digest.data(), &size) != 1 || size != 32) {
        if (context != nullptr) EVP_MD_CTX_free(context);
        fail("SHA-256 failed");
    }
    EVP_MD_CTX_free(context);
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (const auto value : digest) result << std::setw(2) << unsigned(value);
    return result.str();
}

__host__ __device__ std::uint64_t step(std::uint64_t value)
{
    value ^= value << 13;
    value ^= value >> 7;
    value ^= value << 17;
    return value;
}

__device__ std::uint64_t timer()
{
    std::uint64_t value;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(value));
    return value;
}

extern "C" __global__ void ordinary_calibration_kernel(
    const std::uint64_t* input, std::uint64_t* output, std::uint64_t* stamps,
    std::uint32_t words, std::uint32_t iterations,
    std::uint32_t distance, std::uint32_t operation, std::uint64_t seed)
{
    const auto tid = threadIdx.x + blockIdx.x * blockDim.x;
    if (tid == 0) {
        std::uint32_t smid = 0, warpid = 0, rank = 0;
        asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
        asm volatile("mov.u32 %0, %%warpid;" : "=r"(warpid));
        asm volatile("mov.u32 %0, %%cluster_ctarank;" : "=r"(rank));
        stamps[0] = timer();
        stamps[2] = smid;
        stamps[3] = warpid;
        stamps[4] = rank;
    }
    std::uint64_t value = seed ^ (std::uint64_t{tid} << 32) ^ tid;
    for (std::uint32_t iteration = 0; iteration < iterations; ++iteration) {
        if (operation != 1) {
            const auto index = (tid * 131U + iteration + distance) % words;
            value ^= input[index];
        }
        value = step(value);
    }
    output[tid % words] = value;
    __syncthreads();
    if (tid == 0) stamps[1] = timer();
}

__device__ std::uint32_t shared_address(const void* value)
{
    return static_cast<std::uint32_t>(__cvta_generic_to_shared(value));
}

extern "C" __global__ void tma_load_calibration_kernel(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    if (threadIdx.x != 0) return;
    std::uint32_t smid = 0, warpid = 0, rank = 0;
    asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
    asm volatile("mov.u32 %0, %%warpid;" : "=r"(warpid));
    asm volatile("mov.u32 %0, %%cluster_ctarank;" : "=r"(rank));
    stamps[2] = smid;
    stamps[3] = warpid;
    stamps[4] = rank;
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile("mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
                 : "=l"(state) : "r"(barrier_address) : "memory");
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    stamps[0] = timer();
    asm volatile(
        "cp.async.bulk.tensor.2d.shared::cta.global.tile."
        "mbarrier::complete_tx::bytes [%0], [%1, {%2, %3}], [%4];"
        : : "r"(tile_address), "l"(descriptor), "r"(0), "r"(0),
            "r"(barrier_address) : "memory");
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {}
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    for (std::uint32_t index = 0; index < 64; ++index) output[index] = tile[index];
    __threadfence_system();
    stamps[1] = timer();
}

extern "C" __global__ void tma_store_calibration_kernel(
    const __grid_constant__ CUtensorMap tensor_map,
    const std::uint32_t* input, std::uint64_t* stamps)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    if (threadIdx.x != 0) return;
    std::uint32_t smid = 0, warpid = 0, rank = 0;
    asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
    asm volatile("mov.u32 %0, %%warpid;" : "=r"(warpid));
    asm volatile("mov.u32 %0, %%cluster_ctarank;" : "=r"(rank));
    stamps[2] = smid;
    stamps[3] = warpid;
    stamps[4] = rank;
    for (std::uint32_t index = 0; index < 64; ++index) tile[index] = input[index];
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    const auto tile_address = shared_address(tile);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    stamps[0] = timer();
    asm volatile(
        "cp.async.bulk.tensor.2d.global.shared::cta.tile.bulk_group "
        "[%0, {%1, %2}], [%3];"
        : : "l"(descriptor), "r"(0), "r"(0), "r"(tile_address) : "memory");
    asm volatile("cp.async.bulk.commit_group;" : : : "memory");
    asm volatile("cp.async.bulk.wait_group.read 0;" : : : "memory");
    asm volatile("cp.async.bulk.wait_group 0;" : : : "memory");
    __threadfence_system();
    stamps[1] = timer();
}

json find_case(const json& manifest, const std::string& id)
{
    for (const auto& item : manifest.at("cases"))
        if (item.at("id").get<std::string>() == id) return item;
    fail("case id not found");
}

std::vector<std::byte> read_file(const std::string& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) fail("cannot open case manifest");
    const std::string text{std::istreambuf_iterator<char>(input), {}};
    return {reinterpret_cast<const std::byte*>(text.data()),
            reinterpret_cast<const std::byte*>(text.data() + text.size())};
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        std::string cases_path, case_id;
        for (int index = 1; index < argc; index += 2) {
            if (index + 1 >= argc) fail("missing option value");
            const std::string key = argv[index];
            if (key == "--cases") cases_path = argv[index + 1];
            else if (key == "--case-id") case_id = argv[index + 1];
            else fail("unknown option " + key);
        }
        if (cases_path.empty() || case_id.empty()) fail("missing cases or case id");
        const auto raw = read_file(cases_path);
        const std::string manifest_text(
            reinterpret_cast<const char*>(raw.data()), raw.size());
        const auto manifest = json::parse(manifest_text);
        const auto selected = find_case(manifest, case_id);
        const auto operation = selected.at("operation_class").get<std::string>();
        const auto bytes = selected.at("bytes").get<std::uint32_t>();
        if (bytes < 256 || bytes % sizeof(std::uint64_t) != 0)
            fail("case bytes must be aligned and at least 256");
        const auto words = bytes / sizeof(std::uint64_t);
        const auto warps = selected.at("warps").get<std::uint32_t>();
        const auto iterations = selected.at("iterations").get<std::uint32_t>();
        const auto distance = selected.at("load_use_distance").get<std::uint32_t>();
        const auto seed = selected.at("seed").get<std::uint64_t>();
        std::vector<std::uint64_t> input(words), expected(words), output(words);
        for (std::uint32_t index = 0; index < words; ++index)
            input[index] = step(seed ^ (std::uint64_t{index} * 0x9e3779b97f4a7c15ULL));
        std::uint64_t *device_input = nullptr, *device_output = nullptr,
                      *device_stamps = nullptr;
        cuda_check(cudaMalloc(&device_input, bytes), "allocate input");
        cuda_check(cudaMalloc(&device_output, bytes), "allocate output");
        cuda_check(cudaMalloc(&device_stamps, 5 * sizeof(std::uint64_t)),
                   "allocate stamps");
        cuda_check(cudaMemcpy(device_input, input.data(), bytes,
                              cudaMemcpyHostToDevice), "copy input");
        cuda_check(cudaMemset(device_output, 0, bytes), "clear output");
        cuda_check(cudaMemset(device_stamps, 0, 5 * sizeof(std::uint64_t)),
                   "clear stamps");
        const bool tma = operation == "tma_load" || operation == "tma_store" ||
                         operation == "unicast" || operation == "multicast";
        std::string hardware_opcode_class = "ordinary";
        if (!tma) {
            const std::uint32_t code = operation == "ordinary_store" ? 1U :
                                       operation == "mixed_hbm_hbf" ? 2U : 0U;
            const auto threads = warps * 32U;
            ordinary_calibration_kernel<<<1, threads>>>(
                device_input, device_output, device_stamps, words,
                iterations, distance, code, seed);
            for (std::uint32_t tid = 0; tid < threads; ++tid) {
                auto value = seed ^ (std::uint64_t{tid} << 32) ^ tid;
                for (std::uint32_t iteration = 0; iteration < iterations; ++iteration) {
                    if (code != 1) value ^= input[(tid * 131U + iteration + distance) % words];
                    value = step(value);
                }
                expected[tid % words] = value;
            }
        } else {
            alignas(64) CUtensorMap tensor_map{};
            const cuuint64_t global_dim[2]{64, 1};
            const cuuint64_t global_stride[1]{256};
            const cuuint32_t box_dim[2]{64, 1};
            const cuuint32_t element_stride[2]{1, 1};
            void* map_base = operation == "tma_store" ?
                static_cast<void*>(device_output) : static_cast<void*>(device_input);
            driver_check(cuTensorMapEncodeTiled(
                &tensor_map, CU_TENSOR_MAP_DATA_TYPE_UINT32, 2, map_base,
                global_dim, global_stride, box_dim, element_stride,
                CU_TENSOR_MAP_INTERLEAVE_NONE, CU_TENSOR_MAP_SWIZZLE_NONE,
                CU_TENSOR_MAP_L2_PROMOTION_NONE,
                CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE), "encode TensorMap");
            if (operation == "tma_store") {
                tma_store_calibration_kernel<<<1, 1>>>(
                    tensor_map, reinterpret_cast<const std::uint32_t*>(device_input),
                    device_stamps);
                hardware_opcode_class = "tma_store_2d";
            } else {
                tma_load_calibration_kernel<<<1, 1>>>(
                    tensor_map, reinterpret_cast<std::uint32_t*>(device_output),
                    device_stamps);
                hardware_opcode_class = operation == "multicast"
                    ? "tma_load_2d_multicast_proxy" : "tma_load_2d";
            }
            std::copy_n(input.begin(), 256 / sizeof(std::uint64_t), expected.begin());
        }
        cuda_check(cudaGetLastError(), "launch calibration kernel");
        cuda_check(cudaDeviceSynchronize(), "synchronize calibration kernel");
        std::array<std::uint64_t, 5> stamps{};
        cuda_check(cudaMemcpy(output.data(), device_output, bytes,
                              cudaMemcpyDeviceToHost), "copy output");
        cuda_check(cudaMemcpy(stamps.data(), device_stamps, sizeof(stamps),
                              cudaMemcpyDeviceToHost), "copy stamps");
        cuda_check(cudaFree(device_stamps), "free stamps");
        cuda_check(cudaFree(device_output), "free output");
        cuda_check(cudaFree(device_input), "free input");
        const auto output_hash = sha256(output.data(), bytes);
        const auto expected_hash = sha256(expected.data(), bytes);
        const auto case_metadata = selected.dump();
        const json report{
            {"schema_version", 1}, {"case_id", case_id},
            {"suite", manifest.at("suite")},
            {"operation_class", operation},
            {"hardware_opcode_class", hardware_opcode_class},
            {"case_manifest_sha256", sha256(raw.data(), raw.size())},
            {"case_metadata_sha256",
             sha256(case_metadata.data(), case_metadata.size())},
            {"output_sha256", output_hash}, {"expected_sha256", expected_hash},
            {"bit_exact", output_hash == expected_hash},
            {"smid", stamps[2]}, {"warpid", stamps[3]},
            {"cluster_ctarank", stamps[4]},
            {"timestamps", {{"issue", stamps[0]}, {"end", stamps[1]}}},
            {"requested_dimension_count", selected.at("dimension_count")},
            {"multicast_mask", selected.at("multicast_mask")},
        };
        std::printf("%s\n", report.dump().c_str());
        return output_hash == expected_hash ? 0 : 2;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "sm120_calibration: %s\n", error.what());
        return 70;
    }
}
