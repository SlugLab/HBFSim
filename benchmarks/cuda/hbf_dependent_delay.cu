#include <cstddef>
#include <cstdint>

struct DelayChain { std::uint64_t begin_ns, end_ns, checksum, sm; };
struct DelayBlock { std::uint64_t begin_ns, end_ns, sm; };

#if !defined(HBFSIM_DELAY_HOST_ONLY)
__device__ __forceinline__ std::uint64_t delay_timer()
{
    std::uint64_t value;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(value) :: "memory");
    return value;
}
extern "C" __global__ void hbf_dependent_delay(
    const volatile std::uint32_t* input, std::uint32_t hops,
    std::uint32_t shared_bytes, DelayChain* chains, DelayBlock* blocks)
{
    std::uint32_t sm, actual_shared;
    asm volatile("mov.u32 %0, %%smid;" : "=r"(sm));
    asm volatile("mov.u32 %0, %%dynamic_smem_size;" : "=r"(actual_shared));
    if (actual_shared != shared_bytes) asm volatile("trap;");
    if (threadIdx.x == 0) {
        blocks[blockIdx.x].begin_ns = delay_timer();
        blocks[blockIdx.x].sm = sm;
    }
    __syncthreads();
    if ((threadIdx.x & 31) == 0) {
        const auto chain = blockIdx.x * (blockDim.x / 32) + threadIdx.x / 32;
        std::uint32_t next = chain & 4095;
        std::uint64_t checksum = 0;
        const auto begin = delay_timer();
        #pragma unroll 1
        for (std::uint32_t hop = 0; hop < hops; ++hop) {
            // Exactly one active lane per warp. The next load's address
            // depends on the value returned by the previous global load.
            next = input[std::size_t{next} * 1024];
            checksum = checksum * 131 ^ next;
        }
        const auto end = delay_timer();
        chains[chain] = {begin, end, checksum, sm};
    }
    __syncthreads();
    if (threadIdx.x == 0) blocks[blockIdx.x].end_ns = delay_timer();
}
#endif

#if !defined(HBFSIM_DELAY_KERNEL_ONLY)
#include <hbfsim/api.h>
#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include <cuda.h>
#include <cuda_runtime_api.h>
#include <json.hpp>
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using nlohmann::json;
using namespace hbfsim::device;
void require(bool value, const std::string& message)
{
    if (!value) throw std::runtime_error(message);
}
void driver(CUresult result, const char* what)
{
    const char* message = nullptr;
    (void)cuGetErrorString(result, &message);
    require(result == CUDA_SUCCESS, std::string(what) + ": " + (message ? message : "CUDA error"));
}
void runtime(cudaError_t result, const char* what)
{
    require(result == cudaSuccess, std::string(what) + ": " + cudaGetErrorString(result));
}
std::string read(const std::string& path)
{
    std::ifstream stream(path, std::ios::binary);
    require(bool(stream), "cannot read " + path);
    return {std::istreambuf_iterator<char>(stream), {}};
}
void write(const std::string& path, const std::string& value)
{
    std::ofstream stream(path);
    require(bool(stream), "cannot open " + path);
    stream << value << '\n'; stream.flush();
    require(bool(stream), "write failed " + path);
}
struct Options {
    std::string treatment, occupancy, profile, plugin, ptx, output, report_dir;
    unsigned hops = 0, warps = 0, delay_ns = 0;
};
unsigned number(const std::string& text)
{
    std::size_t used = 0;
    auto value = std::stoul(text, &used);
    require(used == text.size() && value <= 20000, "invalid integer");
    return static_cast<unsigned>(value);
}
Options options(int argc, char** argv)
{
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string key = argv[i];
        require(i + 1 < argc, "missing value for " + key);
        const std::string value = argv[++i];
        if (key == "--treatment") o.treatment = value;
        else if (key == "--occupancy") o.occupancy = value;
        else if (key == "--profile") o.profile = value;
        else if (key == "--plugin") o.plugin = value;
        else if (key == "--ptx") o.ptx = value;
        else if (key == "--output") o.output = value;
        else if (key == "--report-dir") o.report_dir = value;
        else if (key == "--hops") o.hops = number(value);
        else if (key == "--warps") o.warps = number(value);
        else if (key == "--delay-ns") o.delay_ns = number(value);
        else throw std::runtime_error("unknown option " + key);
    }
    require(o.treatment == "native" || o.treatment == "fast_logical" || o.treatment == "hbf_logical", "explicit treatment required");
    require(o.occupancy == "low" || o.occupancy == "high", "explicit occupancy required");
    require(o.hops == 1 || o.hops == 16 || o.hops == 64, "K must be 1/16/64");
    require(o.warps && o.warps <= 16 && (o.warps & (o.warps - 1)) == 0, "invalid warp count");
    require(!o.profile.empty() && !o.plugin.empty() && !o.ptx.empty() && !o.output.empty() && !o.report_dir.empty(), "missing explicit artifact paths");
    const auto profile = json::parse(read(o.profile));
    require(profile.at("time_scale").get<unsigned>() == 1 && profile.at("read_latency_ns").get<std::uint64_t>() > 0 &&
            profile.at("program_latency_ns").get<std::uint64_t>() > 0 && profile.at("aggregate_bandwidth_bytes_per_s").get<std::uint64_t>() > 0,
            "positive normal profile and time_scale=1 required; no zero-profile shortcut");
    return o;
}
std::uint64_t expected_checksum(unsigned chain, unsigned hops)
{
    unsigned next = chain & 4095;
    std::uint64_t result = 0;
    for (unsigned i = 0; i < hops; ++i) {
        next = (next * 17 + 1) & 4095;
        result = result * 131 ^ next;
    }
    return result;
}
template <class T> CUdeviceptr global(CUmodule module, const char* name)
{
    CUdeviceptr address = 0; std::size_t bytes = 0;
    driver(cuModuleGetGlobal(&address, &bytes, module, name), name);
    require(bytes == sizeof(T), std::string("wrong experiment symbol size: ") + name);
    return address;
}
}  // namespace

int main(int argc, char** argv)
{
    // All setup, including runtime initialization, follows explicit argument
    // validation. This executable is only launched by a guarded experiment.
    CUmodule module = nullptr; hbfsim_context* context = nullptr;
    std::uint32_t* input = nullptr; DelayChain* chains_device = nullptr;
    DelayBlock* blocks_device = nullptr; EvalDelayTrace* traces_device = nullptr;
    cudaEvent_t begin_event = nullptr, end_event = nullptr;
    bool registered = false;
    try {
        const auto o = options(argc, argv);
        const bool modeled = o.treatment != "native";
        auto ptx = read(o.ptx);
        json coverage = {{"rewritten_instructions", 0}, {"unsupported_instructions", 0}};
        std::uint64_t (*begin_load)(const char*, std::size_t) = nullptr;
        void (*end_load)(std::uint64_t) = nullptr;
        if (modeled) {
            auto* library = dlopen(o.plugin.c_str(), RTLD_NOW | RTLD_GLOBAL);
            require(library != nullptr, "cannot load trusted PTX pass");
            auto process = reinterpret_cast<int (*)(const char*, int, char*)>(dlsym(library, "process_input"));
            begin_load = reinterpret_cast<decltype(begin_load)>(dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
            end_load = reinterpret_cast<decltype(end_load)>(dlsym(RTLD_DEFAULT, "hbfsim_end_module_load"));
            require(process && begin_load && end_load, "trusted pass/loader API unavailable");
            const auto request = json({{"input", {{"full_ptx", ptx}, {"to_patch_kernel", "hbf_dependent_delay"},
                {"global_ebpf_map_info_symbol", "map_info"}, {"ebpf_communication_data_symbol", "constData"}}},
                {"ebpf_instructions", json::array()}}).dump();
            std::vector<char> response(32 * 1024 * 1024);
            require(process(request.c_str(), response.size(), response.data()) == 0, "PTX transform failed");
            auto parsed = json::parse(response.data());
            const char* manifest_path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
            require(manifest_path != nullptr, "explicit pass manifest destination required");
            // The authoritative pass manifest excludes ld.param traffic from
            // HBF-relevant unsupported operations. Keep raw transform counts.
            coverage = json::parse(read(manifest_path));
            require(parsed.at("modified").get<bool>() && coverage.at("instrumented").get<bool>() &&
                    coverage.at("kernel") == "hbf_dependent_delay" && coverage.at("unsupported_instructions").get<unsigned>() == 0,
                    "known-delay PTX coverage unsupported");
            write(o.output + ".transform-coverage.json", parsed.at("coverage").dump(2));
            ptx = parsed.at("output_ptx").get<std::string>();
            write(o.output + ".transformed.ptx", ptx);
        }
        runtime(cudaFree(nullptr), "initialize CUDA");
        cudaDeviceProp props{}; runtime(cudaGetDeviceProperties(&props, 0), "device properties");
        require(props.warpSize == 32 && props.multiProcessorCount > 0, "unsupported GPU geometry");
        constexpr std::size_t words = 4096 * 1024;
        std::vector<std::uint32_t> data(words, 0);
        for (unsigned i = 0; i < 4096; ++i) data[i * 1024] = (i * 17 + 1) & 4095;
        runtime(cudaMalloc(&input, words * sizeof(*input)), "allocate input");
        runtime(cudaMemcpy(input, data.data(), words * sizeof(*input), cudaMemcpyHostToDevice), "copy permutation");
        if (modeled) {
            hbfsim_options opts{.profile_path=o.profile.c_str(),.report_dir=o.report_dir.c_str(),.mode=HBFSIM_MODEL_FAST,.ring_capacity=256,.request_timeout_ns=1000000000};
            require(hbfsim_context_create(&opts, &context) == HBFSIM_OK, "context creation failed");
            hbfsim_range_options range{.mode=HBFSIM_RANGE_MODE_TIMING,.permissions=HBFSIM_RANGE_READ,.cache_policy=HBFSIM_CACHE_POLICY_NONE,.stream_id=0};
            require(hbfsim_register_device(context, input, words * sizeof(*input), &range) == HBFSIM_OK, "timing range registration failed");
            registered = true;
        }
        const auto token = modeled ? begin_load(ptx.data(), ptx.size()) : 0;
        require(!modeled || token != 0, "untrusted transformed module");
        const auto load_result = cuModuleLoadDataEx(&module, ptx.c_str(), 0, nullptr, nullptr);
        if (modeled) end_load(token);
        driver(load_result, "load exact module");
        CUfunction kernel = nullptr; driver(cuModuleGetFunction(&kernel, module, "hbf_dependent_delay"), "kernel lookup");
        int registers = 0; driver(cuFuncGetAttribute(&registers, CU_FUNC_ATTRIBUTE_NUM_REGS, kernel), "register count");
        unsigned shared = 0;
        if (o.occupancy == "low") {
            shared = static_cast<unsigned>(props.sharedMemPerMultiprocessor / 2 + 256);
            require(shared <= props.sharedMemPerBlockOptin, "low occupancy target unsupported by shared-memory limit");
            driver(cuFuncSetAttribute(kernel, CU_FUNC_ATTRIBUTE_MAX_DYNAMIC_SHARED_SIZE_BYTES, shared), "opt-in shared memory");
        }
        int maximum = 0;
        driver(cuOccupancyMaxActiveBlocksPerMultiprocessor(&maximum, kernel, o.warps * 32, shared), "occupancy limit");
        require(maximum > 0 && (o.occupancy == "low" ? maximum == 1 : maximum > 1), "requested occupancy unsupported");
        // Geometry is fixed across all treatments by SM count and warp count,
        // not by the treatment's changed register allocation/theoretical limit.
        const unsigned block_count = props.multiProcessorCount * (o.occupancy == "low" ? 1 : 32 / o.warps);
        const unsigned chain_count = block_count * o.warps;
        const std::size_t trace_count = std::size_t{chain_count} * o.hops;
        runtime(cudaMalloc(&chains_device, chain_count * sizeof(DelayChain)), "allocate chains");
        runtime(cudaMalloc(&blocks_device, block_count * sizeof(DelayBlock)), "allocate block stamps");
        runtime(cudaMalloc(&traces_device, trace_count * sizeof(EvalDelayTrace)), "allocate helper traces");
        EvalDelayConfig config{kEvalDelayMagic, o.treatment == "hbf_logical" ? o.delay_ns : 0,
                               reinterpret_cast<std::uintptr_t>(traces_device), trace_count};
        CUdeviceptr config_address = 0, counters_address = 0;
        if (modeled) {
            config_address = global<EvalDelayConfig>(module, "__hbfsim_eval_delay_config");
            counters_address = global<EvalDelayCounters>(module, "__hbfsim_eval_delay_counters");
            driver(cuMemcpyHtoD(config_address, &config, sizeof(config)), "enable explicit experiment");
        }
        unsigned hops = o.hops;
        void* arguments[]{&input, &hops, &shared, &chains_device, &blocks_device};
        auto launch = [&] { driver(cuLaunchKernel(kernel, block_count, 1, 1, o.warps * 32, 1, 1, shared, nullptr, arguments, nullptr), "launch dependent reads"); };
        launch(); runtime(cudaDeviceSynchronize(), "warmup completion");
        if (modeled) { EvalDelayCounters zero{}; driver(cuMemcpyHtoD(counters_address, &zero, sizeof(zero)), "reset counters after warmup"); }
        runtime(cudaEventCreate(&begin_event), "create begin Event"); runtime(cudaEventCreate(&end_event), "create end Event");
        runtime(cudaEventRecord(begin_event), "begin Event"); launch(); runtime(cudaEventRecord(end_event), "end Event");
        runtime(cudaEventSynchronize(end_event), "kernel completion");
        float elapsed_ms = 0; runtime(cudaEventElapsedTime(&elapsed_ms, begin_event, end_event), "Event elapsed time");
        std::vector<DelayChain> chains(chain_count); std::vector<DelayBlock> blocks(block_count);
        std::vector<EvalDelayTrace> traces(modeled ? trace_count : 0); EvalDelayCounters counters{};
        runtime(cudaMemcpy(chains.data(), chains_device, chains.size()*sizeof(DelayChain), cudaMemcpyDeviceToHost), "copy chains");
        runtime(cudaMemcpy(blocks.data(), blocks_device, blocks.size()*sizeof(DelayBlock), cudaMemcpyDeviceToHost), "copy block stamps");
        if (modeled) {
            driver(cuMemcpyDtoH(&counters, counters_address, sizeof(counters)), "copy experiment counters");
            require(counters.covered_accesses == trace_count && counters.trace_overflow == 0, "dynamic helper coverage mismatch");
            runtime(cudaMemcpy(traces.data(), traces_device, traces.size()*sizeof(EvalDelayTrace), cudaMemcpyDeviceToHost), "copy wait stamps");
            EvalDelayConfig disabled{}; driver(cuMemcpyHtoD(config_address, &disabled, sizeof(disabled)), "disable experiment");
        }
        json result={{"schema_version",1},{"evidence","GPU_ACQUISITION"},{"treatment",o.treatment},
            {"requested_delay_ns",o.delay_ns},{"applied_delay_ns",modeled?config.delay_ns:0},{"hops",o.hops},{"warps",o.warps},
            {"occupancy",o.occupancy},{"blocks",block_count},{"sm_count",props.multiProcessorCount},{"registers",registers},
            {"theoretical_blocks_per_sm",maximum},{"dynamic_shared_bytes",shared},{"event_ns",double(elapsed_ms)*1e6},
            {"covered_accesses",counters.covered_accesses},{"covered_bytes",counters.covered_bytes},
            {"bypass_accesses",counters.bypass_accesses},{"bypass_bytes",counters.bypass_bytes},
            {"rejected_accesses",counters.rejected_accesses},{"trace_overflow",counters.trace_overflow},
            {"rewritten_instructions",coverage.at("rewritten_instructions")},{"unsupported_instructions",coverage.at("unsupported_instructions")},
            {"unknown_bytes",0},{"eligible_bytes",trace_count*4},{"seed",0},{"permutation_rule","next=(17*i+1)%4096; stride=4096 bytes; version=1"},
            {"input_base",reinterpret_cast<std::uintptr_t>(input)},{"input_bytes",words*sizeof(*input)},
            {"chains",json::array()},{"block_intervals",json::array()},{"waits",json::array()}};
        bool checksums = true;
        for (unsigned i = 0; i < chain_count; ++i) {
            const auto& c = chains[i]; const auto expected = expected_checksum(i, o.hops);
            checksums &= c.checksum == expected;
            result["chains"].push_back({{"block",i/o.warps},{"warp",i%o.warps},{"sm",c.sm},{"begin_ns",c.begin_ns},
                {"end_ns",c.end_ns},{"checksum",c.checksum},{"expected_checksum",expected}});
        }
        for (unsigned i = 0; i < block_count; ++i) result["block_intervals"].push_back({{"block",i},{"sm",blocks[i].sm},{"begin_ns",blocks[i].begin_ns},{"end_ns",blocks[i].end_ns}});
        for (const auto& t : traces) result["waits"].push_back({{"thread_id",t.thread_id},{"address",t.address},{"wait_enter_ns",t.wait_enter_ns},{"wait_exit_ns",t.wait_exit_ns},{"delay_ns",t.delay_ns}});
        write(o.output, result.dump(2)); require(checksums, "checksum differs from deterministic CPU reference");
        driver(cuModuleUnload(module), "unload module"); module = nullptr;
        if (registered) { require(hbfsim_unregister(context, input) == HBFSIM_OK, "unregister failed"); registered = false; }
        if (context) { hbfsim_context_destroy(context); context = nullptr; }
        cudaEventDestroy(begin_event); cudaEventDestroy(end_event);
        cudaFree(traces_device); cudaFree(blocks_device); cudaFree(chains_device); cudaFree(input);
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "hbf_dependent_delay: %s\n", error.what());
        if (module) (void)cuModuleUnload(module);
        if (registered && context) (void)hbfsim_unregister(context, input);
        if (context) hbfsim_context_destroy(context);
        if (begin_event) cudaEventDestroy(begin_event);
        if (end_event) cudaEventDestroy(end_event);
        if (traces_device) cudaFree(traces_device);
        if (blocks_device) cudaFree(blocks_device);
        if (chains_device) cudaFree(chains_device);
        if (input) cudaFree(input);
        return 1;
    }
}
#endif
