#include <cuda_runtime_api.h>

#include <dlfcn.h>

#include <cstdint>
#include <cstdio>
#include <cstring>

extern "C" __global__ void unsupported_hbf_kernel(unsigned long long* value)
{
    atomicAdd(value, 1ULL);
}

namespace {

using add_range_fn = int (*)(std::uintptr_t, std::uintptr_t);

int hbm_run()
{
    unsigned long long* value = nullptr;
    if (cudaMalloc(&value, sizeof(*value)) != cudaSuccess) {
        return 2;
    }
    if (cudaMemset(value, 0, sizeof(*value)) != cudaSuccess) {
        cudaFree(value);
        return 3;
    }
    unsupported_hbf_kernel<<<1, 1>>>(value);
    const auto launch_status = cudaGetLastError();
    const auto sync_status =
        launch_status == cudaSuccess ? cudaDeviceSynchronize() : launch_status;
    unsigned long long observed = 0;
    const auto copy_status = sync_status == cudaSuccess
                                 ? cudaMemcpy(&observed, value, sizeof(observed),
                                              cudaMemcpyDeviceToHost)
                                 : sync_status;
    cudaFree(value);
    if (copy_status != cudaSuccess || observed != 1) {
        std::fprintf(stderr, "HBM kernel failed: %s value=%llu\n",
                     cudaGetErrorString(copy_status), observed);
        return 4;
    }
    std::puts("HBM kernel completed");
    return 0;
}

int hbf_run()
{
    unsigned long long* logical = nullptr;
    if (cudaMalloc(&logical, sizeof(*logical)) != cudaSuccess || logical == nullptr) {
        std::fprintf(stderr, "failed to allocate safe HBF test word\n");
        return 5;
    }
    auto add_range = reinterpret_cast<add_range_fn>(
        dlsym(RTLD_DEFAULT, "hbfsim_coverage_add_range"));
    if (add_range == nullptr) {
        std::fprintf(stderr, "missing hbfsim_coverage_add_range interposer\n");
        cudaFree(logical);
        return 6;
    }
    const auto hbf_begin = reinterpret_cast<std::uintptr_t>(logical);
    if (add_range(hbf_begin, hbf_begin + sizeof(*logical)) != 0) {
        std::fprintf(stderr, "failed to register HBF test range\n");
        cudaFree(logical);
        return 7;
    }

    unsupported_hbf_kernel<<<1, 1>>>(logical);
    const auto status = cudaGetLastError();
    if (status != cudaErrorNotSupported) {
        if (status == cudaSuccess) {
            cudaDeviceSynchronize();
        }
        std::fprintf(stderr, "HBF launch was not rejected: %s\n",
                     cudaGetErrorString(status));
        cudaFree(logical);
        return 8;
    }
    cudaFree(logical);
    std::puts("HBF launch rejected before kernel execution");
    return 0;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 2 ||
        (std::strcmp(argv[1], "--hbm") != 0 &&
         std::strcmp(argv[1], "--hbf") != 0)) {
        std::fprintf(stderr, "usage: %s (--hbm|--hbf)\n", argv[0]);
        return 64;
    }
    return std::strcmp(argv[1], "--hbm") == 0 ? hbm_run() : hbf_run();
}
