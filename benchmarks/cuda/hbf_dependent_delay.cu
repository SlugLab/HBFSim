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
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
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
    std::string trace_mode = "legacy";
    unsigned hops = 0, warps = 0, delay_ns = 0, diagnostic_blocks = 0;
    bool diagnostic_blocks_set = false;
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
        else if (key == "--trace-mode") o.trace_mode = value;
        else if (key == "--hops") o.hops = number(value);
        else if (key == "--warps") o.warps = number(value);
        else if (key == "--delay-ns") o.delay_ns = number(value);
        else if (key == "--diagnostic-blocks") {
            o.diagnostic_blocks = number(value);
            o.diagnostic_blocks_set = true;
        } else throw std::runtime_error("unknown option " + key);
    }
    require(o.treatment == "native" || o.treatment == "fast_logical" || o.treatment == "hbf_logical", "explicit treatment required");
    require(o.occupancy == "low" || o.occupancy == "high", "explicit occupancy required");
    require(o.trace_mode == "legacy" || o.trace_mode == "per_chain" ||
                o.trace_mode == "per_chain_abba",
            "invalid trace mode");
    require(o.hops == 1 || o.hops == 16 || o.hops == 64, "K must be 1/16/64");
    require(o.warps && o.warps <= 16 && (o.warps & (o.warps - 1)) == 0, "invalid warp count");
    require(!o.diagnostic_blocks_set ||
                (o.trace_mode == "per_chain_abba" &&
                 o.diagnostic_blocks == 1),
            "diagnostic block override requires per_chain_abba and one block");
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

#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
struct Span {
    std::uint64_t address;
    std::uint64_t bytes;
    const char* name;
};

void require_disjoint(const std::vector<Span>& spans)
{
    for (std::size_t i = 0; i < spans.size(); ++i) {
        require(eval_chain_span_valid(spans[i].address, spans[i].bytes),
                std::string("invalid device span: ") + spans[i].name);
        for (std::size_t j = i + 1; j < spans.size(); ++j) {
            require(eval_chain_spans_disjoint(
                        spans[i].address, spans[i].bytes,
                        spans[j].address, spans[j].bytes),
                    std::string("overlapping device spans: ") + spans[i].name +
                        "/" + spans[j].name);
        }
    }
}

std::uint64_t chain_row_stride(unsigned hops)
{
    std::uint64_t events_bytes = 0;
    std::uint64_t row_bytes = 0;
    const auto capacity = std::uint64_t{hops} + 7;
    require(eval_chain_checked_multiply(capacity, sizeof(EvalChainEvent),
                                        &events_bytes) &&
                eval_chain_checked_add(sizeof(EvalChainRow), events_bytes,
                                       &row_bytes) &&
                row_bytes <= std::numeric_limits<std::uint64_t>::max() - 63,
            "chain diagnostic row size overflow");
    return (row_bytes + 63) & ~std::uint64_t{63};
}

struct ChainObservation {
    EvalChainRow row{};
    std::vector<EvalChainEvent> events;
    bool unused_slots_zero = true;
};

bool zero_event(const EvalChainEvent& event)
{
    const EvalChainEvent zero{};
    return std::memcmp(&event, &zero, sizeof(event)) == 0;
}

std::vector<ChainObservation> decode_chain_storage(
    const std::vector<std::byte>& storage, std::uint64_t row_count,
    std::uint64_t row_stride, std::uint64_t capacity)
{
    require(row_count <= std::numeric_limits<std::size_t>::max(),
            "chain diagnostic row count exceeds host size");
    std::vector<ChainObservation> result(static_cast<std::size_t>(row_count));
    for (std::uint64_t index = 0; index < row_count; ++index) {
        const auto offset = index * row_stride;
        auto& observed = result[static_cast<std::size_t>(index)];
        std::memcpy(&observed.row, storage.data() + offset,
                    sizeof(observed.row));
        const auto retained = std::min(observed.row.event_count, capacity);
        observed.events.resize(static_cast<std::size_t>(retained));
        for (std::uint64_t slot = 0; slot < capacity; ++slot) {
            EvalChainEvent event{};
            std::memcpy(&event,
                        storage.data() + offset + sizeof(EvalChainRow) +
                            slot * sizeof(EvalChainEvent),
                        sizeof(event));
            if (slot < retained) {
                observed.events[static_cast<std::size_t>(slot)] = event;
            } else {
                observed.unused_slots_zero &= zero_event(event);
            }
        }
    }
    return result;
}

struct ExpectedChainEvent {
    std::uint64_t address;
    std::uint32_t bytes;
    std::uint32_t operation;
    EvalChainEventClass event_class;
};

std::vector<ExpectedChainEvent> expected_chain_events(
    unsigned row, unsigned warps, unsigned hops, std::uint64_t input_address,
    std::uint64_t chain_address, std::uint64_t block_address)
{
    const auto block = row / warps;
    const auto warp = row % warps;
    std::vector<ExpectedChainEvent> result;
    result.reserve(hops + (warp == 0 ? 7 : 4));
    if (warp == 0) {
        result.push_back({block_address + std::uint64_t{block} * sizeof(DelayBlock),
                          8, 1, EvalChainEventClass::BlockOutputStore});
        result.push_back({block_address + std::uint64_t{block} * sizeof(DelayBlock) + 16,
                          8, 1, EvalChainEventClass::BlockOutputStore});
    }
    auto next = row & 4095U;
    for (unsigned hop = 0; hop < hops; ++hop) {
        result.push_back({input_address + std::uint64_t{next} * 4096,
                          4, 0, EvalChainEventClass::CoveredLoad});
        next = (next * 17 + 1) & 4095U;
    }
    for (std::uint64_t offset = 0; offset != sizeof(DelayChain); offset += 8) {
        result.push_back({chain_address + std::uint64_t{row} * sizeof(DelayChain) + offset,
                          8, 1, EvalChainEventClass::ChainOutputStore});
    }
    if (warp == 0) {
        result.push_back({block_address + std::uint64_t{block} * sizeof(DelayBlock) + 8,
                          8, 1, EvalChainEventClass::BlockOutputStore});
    }
    return result;
}

void add_counters(EvalDelayCounters* total, const EvalDelayCounters& row)
{
    total->covered_accesses += row.covered_accesses;
    total->covered_bytes += row.covered_bytes;
    total->bypass_accesses += row.bypass_accesses;
    total->bypass_bytes += row.bypass_bytes;
    total->rejected_accesses += row.rejected_accesses;
    total->trace_overflow += row.trace_overflow;
}

void validate_chain_observations(
    const std::vector<ChainObservation>& rows,
    const std::vector<DelayChain>& chains, const std::vector<DelayBlock>& blocks,
    unsigned warps, unsigned hops, std::uint64_t launch_epoch,
    std::uint64_t delay_ns, std::uint64_t input_address,
    std::uint64_t chain_address, std::uint64_t block_address)
{
    require(rows.size() == chains.size(), "chain diagnostic row count mismatch");
    for (std::size_t index = 0; index < rows.size(); ++index) {
        const auto expected = expected_chain_events(
            static_cast<unsigned>(index), warps, hops, input_address,
            chain_address, block_address);
        const auto& observed = rows[index];
        const auto block = static_cast<unsigned>(index) / warps;
        const auto warp = static_cast<unsigned>(index) % warps;
        require(block < blocks.size(), "chain diagnostic block identity mismatch");
        require(observed.row.writer_observed == 1 &&
                    observed.row.launch_epoch == launch_epoch &&
                    observed.row.writer_thread_id == index * 32 &&
                    observed.row.reserved == 0,
                "chain diagnostic row owner/epoch mismatch");
        require(observed.row.event_count == expected.size() &&
                    observed.events.size() == expected.size() &&
                    observed.unused_slots_zero,
                "chain diagnostic event/reset mismatch");
        require(observed.row.counters.covered_accesses == hops &&
                    observed.row.counters.covered_bytes ==
                        std::uint64_t{hops} * 4 &&
                    observed.row.counters.bypass_accesses ==
                        (warp == 0 ? 7U : 4U) &&
                    observed.row.counters.bypass_bytes ==
                        (warp == 0 ? 56U : 32U) &&
                    observed.row.counters.rejected_accesses == 0 &&
                    observed.row.counters.trace_overflow == 0,
                "chain diagnostic counter mismatch");
        std::uint64_t previous_event_end = 0;
        for (std::size_t order = 0; order < expected.size(); ++order) {
            const auto& event = observed.events[order];
            const auto& wanted = expected[order];
            require(event.thread_id == index * 32 && event.order == order &&
                        event.address == wanted.address &&
                        event.bytes == wanted.bytes &&
                        event.operation == wanted.operation &&
                        event.event_class ==
                            static_cast<std::uint32_t>(wanted.event_class) &&
                        event.status ==
                            static_cast<std::uint32_t>(RequestStatus::Ready),
                    "chain diagnostic event identity mismatch");
            require(event.begin_ns >= previous_event_end,
                    "chain diagnostic event time order mismatch");
            if (wanted.event_class == EvalChainEventClass::CoveredLoad) {
                require(event.begin_ns >= chains[index].begin_ns &&
                            event.end_ns >= event.begin_ns &&
                            event.end_ns - event.begin_ns >= delay_ns &&
                            (delay_ns != 0 || event.end_ns == event.begin_ns) &&
                            event.end_ns <= chains[index].end_ns,
                        "chain diagnostic load timing mismatch");
            } else {
                require(event.begin_ns == event.end_ns,
                        "chain diagnostic store timestamp mismatch");
                if (wanted.event_class ==
                    EvalChainEventClass::ChainOutputStore) {
                    require(event.begin_ns >= chains[index].end_ns,
                            "chain output store precedes chain completion");
                } else {
                    const auto block_base =
                        block_address + std::uint64_t{block} * sizeof(DelayBlock);
                    const auto recorded = event.address == block_base + 8
                                              ? blocks[block].end_ns
                                              : blocks[block].begin_ns;
                    require(event.begin_ns >= recorded,
                            "block output store precedes recorded timestamp");
                }
            }
            previous_event_end = event.end_ns;
        }
    }
}

json chain_diagnostic_json(const EvalChainDiagnosticConfig& config,
                           const std::vector<ChainObservation>& rows)
{
    json value = {{"enabled", true},
                  {"magic", config.magic},
                  {"symbol_bytes", sizeof(EvalChainDiagnosticConfig)},
                  {"version", config.version},
                  {"config_bytes", config.config_bytes},
                  {"delay_ns", config.delay_ns},
                  {"launch_epoch", config.launch_epoch},
                  {"grid_x", config.grid_x},
                  {"grid_y", config.grid_y},
                  {"grid_z", config.grid_z},
                  {"block_x", config.block_x},
                  {"block_y", config.block_y},
                  {"block_z", config.block_z},
                  {"warps_per_block", config.warps_per_block},
                  {"hops", config.hops},
                  {"row_count", config.row_count},
                  {"row_stride", config.row_stride},
                  {"trace_capacity", config.trace_capacity},
                  {"storage_address", config.storage_address},
                  {"storage_bytes", config.storage_bytes},
                  {"chain_output_address", config.chain_output_address},
                  {"chain_output_bytes", config.chain_output_bytes},
                  {"block_output_address", config.block_output_address},
                  {"block_output_bytes", config.block_output_bytes},
                  {"rows", json::array()}};
    for (std::size_t index = 0; index < rows.size(); ++index) {
        const auto& observed = rows[index];
        json row = {{"row", index},
                    {"covered_accesses", observed.row.counters.covered_accesses},
                    {"covered_bytes", observed.row.counters.covered_bytes},
                    {"bypass_accesses", observed.row.counters.bypass_accesses},
                    {"bypass_bytes", observed.row.counters.bypass_bytes},
                    {"rejected_accesses", observed.row.counters.rejected_accesses},
                    {"trace_overflow", observed.row.counters.trace_overflow},
                    {"event_count", observed.row.event_count},
                    {"launch_epoch", observed.row.launch_epoch},
                    {"writer_thread_id", observed.row.writer_thread_id},
                    {"writer_observed", observed.row.writer_observed},
                    {"reserved", observed.row.reserved},
                    {"unused_slots_zero", observed.unused_slots_zero},
                    {"events", json::array()}};
        for (const auto& event : observed.events) {
            row["events"].push_back(
                {{"thread_id", event.thread_id}, {"address", event.address},
                 {"order", event.order}, {"begin_ns", event.begin_ns},
                 {"end_ns", event.end_ns}, {"bytes", event.bytes},
                 {"operation", event.operation},
                 {"event_class", event.event_class}, {"status", event.status}});
        }
        value["rows"].push_back(std::move(row));
    }
    return value;
}
#endif
}  // namespace

int main(int argc, char** argv)
{
    // All setup, including runtime initialization, follows explicit argument
    // validation. This executable is only launched by a guarded experiment.
    CUmodule module = nullptr; hbfsim_context* context = nullptr;
    std::uint32_t* input = nullptr; DelayChain* chains_device = nullptr;
    DelayBlock* blocks_device = nullptr; EvalDelayTrace* traces_device = nullptr;
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
    std::byte* chain_storage_device = nullptr;
    CUdeviceptr chain_config_address = 0;
    bool chain_enabled = false;
#endif
    cudaEvent_t begin_event = nullptr, end_event = nullptr;
    bool registered = false;
    try {
        const auto o = options(argc, argv);
        const bool modeled = o.treatment != "native";
        const bool abba = o.trace_mode == "per_chain_abba";
        const bool per_chain = o.trace_mode == "per_chain" || abba;
        const bool chain_trace = modeled && per_chain;
        require(!abba || (o.treatment == "hbf_logical" && o.delay_ns == 500 &&
                          o.hops == 1 && o.warps == 1 &&
                          o.occupancy == "low"),
                "ABBA diagnostic requires hbf_logical/D500/K1/W1/low");
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
        // The established default geometry remains fixed across treatments.
        // The explicit ABBA-only diagnostic selector instead launches one block.
        const unsigned default_block_count =
            props.multiProcessorCount *
            (o.occupancy == "low" ? 1 : 32 / o.warps);
        const unsigned block_count =
            o.diagnostic_blocks_set ? o.diagnostic_blocks : default_block_count;
        const unsigned chain_count = block_count * o.warps;
        const std::size_t trace_count = std::size_t{chain_count} * o.hops;
        runtime(cudaMalloc(&chains_device, chain_count * sizeof(DelayChain)), "allocate chains");
        runtime(cudaMalloc(&blocks_device, block_count * sizeof(DelayBlock)), "allocate block stamps");
        if (!chain_trace) {
            runtime(cudaMalloc(&traces_device, trace_count * sizeof(EvalDelayTrace)), "allocate helper traces");
        }
        EvalDelayConfig config{kEvalDelayMagic, o.treatment == "hbf_logical" ? o.delay_ns : 0,
                               reinterpret_cast<std::uintptr_t>(traces_device), trace_count};
        CUdeviceptr config_address = 0, counters_address = 0;
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        EvalChainDiagnosticConfig chain_config{};
        std::uint64_t chain_storage_bytes = 0;
        if (chain_trace) {
            const auto row_stride = chain_row_stride(o.hops);
            require(eval_chain_checked_multiply(chain_count, row_stride,
                                                &chain_storage_bytes) &&
                        chain_storage_bytes <=
                            std::numeric_limits<std::size_t>::max(),
                    "chain diagnostic storage size overflow");
            runtime(cudaMalloc(&chain_storage_device,
                               static_cast<std::size_t>(chain_storage_bytes)),
                    "allocate chain diagnostic rows");
            require_disjoint({
                {reinterpret_cast<std::uintptr_t>(input), words * sizeof(*input), "input"},
                {reinterpret_cast<std::uintptr_t>(chains_device),
                 std::uint64_t{chain_count} * sizeof(DelayChain), "chain output"},
                {reinterpret_cast<std::uintptr_t>(blocks_device),
                 std::uint64_t{block_count} * sizeof(DelayBlock), "block output"},
                {reinterpret_cast<std::uintptr_t>(chain_storage_device),
                 chain_storage_bytes, "diagnostic rows"},
            });
            chain_config = {
                kEvalChainDiagnosticMagic,
                kEvalChainDiagnosticVersion,
                sizeof(EvalChainDiagnosticConfig),
                o.treatment == "hbf_logical" ? o.delay_ns : 0,
                1,
                block_count, 1, 1,
                o.warps * 32, 1, 1,
                o.warps, o.hops,
                chain_count,
                reinterpret_cast<std::uintptr_t>(chain_storage_device),
                chain_storage_bytes,
                row_stride,
                std::uint64_t{o.hops} + 7,
                reinterpret_cast<std::uintptr_t>(chains_device),
                std::uint64_t{chain_count} * sizeof(DelayChain),
                reinterpret_cast<std::uintptr_t>(blocks_device),
                std::uint64_t{block_count} * sizeof(DelayBlock),
            };
            require(eval_chain_config_valid(chain_config),
                    "invalid chain diagnostic declaration");
            runtime(cudaMemset(chain_storage_device, 0,
                               static_cast<std::size_t>(chain_storage_bytes)),
                    "initialize chain diagnostic rows");
        }
#else
        require(!per_chain, "per-chain diagnostic was not built");
#endif
        if (modeled) {
            config_address = global<EvalDelayConfig>(module, "__hbfsim_eval_delay_config");
            if (chain_trace) {
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
                EvalDelayConfig disabled{};
                driver(cuMemcpyHtoD(config_address, &disabled, sizeof(disabled)),
                       "keep legacy experiment disabled");
                chain_config_address = global<EvalChainDiagnosticConfig>(
                    module, "__hbfsim_eval_chain_diagnostic_config");
                driver(cuMemcpyHtoD(chain_config_address, &chain_config,
                                    sizeof(chain_config)),
                       "enable per-chain experiment for warmup");
                chain_enabled = true;
#endif
            } else {
                counters_address = global<EvalDelayCounters>(module, "__hbfsim_eval_delay_counters");
                driver(cuMemcpyHtoD(config_address, &config, sizeof(config)), "enable explicit experiment");
            }
        }
        unsigned hops = o.hops;
        void* arguments[]{&input, &hops, &shared, &chains_device, &blocks_device};
        auto launch = [&] { driver(cuLaunchKernel(kernel, block_count, 1, 1, o.warps * 32, 1, 1, shared, nullptr, arguments, nullptr), "launch dependent reads"); };
        launch(); runtime(cudaDeviceSynchronize(), "warmup completion");
        if (abba) {
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
            constexpr std::uint64_t delays[]{0, 500, 500, 0};
            const json runtime_identity = {
                {"module_handle", reinterpret_cast<std::uintptr_t>(module)},
                {"context_handle", reinterpret_cast<std::uintptr_t>(context)},
                {"input_address", reinterpret_cast<std::uintptr_t>(input)},
                {"chain_output_address",
                 reinterpret_cast<std::uintptr_t>(chains_device)},
                {"block_output_address",
                 reinterpret_cast<std::uintptr_t>(blocks_device)},
                {"storage_address",
                 reinterpret_cast<std::uintptr_t>(chain_storage_device)},
                {"config_symbol_address", chain_config_address},
            };
            json result = {
                {"schema_version", 1},
                {"evidence", "GPU_ACQUISITION"},
                {"validation_status", "UNVALIDATED"},
                {"scientific_claim", false},
                {"g2_gate_closed", false},
                {"trace_mode", "per_chain_abba"},
                {"treatment", o.treatment},
                {"diagnostic_blocks_requested", o.diagnostic_blocks},
                {"actual_grid_blocks", block_count},
                {"actual_chain_rows", chain_count},
                {"actual_events_per_row", o.hops + 7},
                {"warmup_delay_ns", 500},
                {"sequence_delay_ns", json::array({0, 500, 500, 0})},
                {"module_load_count", 1},
                {"context_create_count", 1},
                {"shared_runtime_identity", runtime_identity},
                {"launches", json::array()},
            };
            runtime(cudaEventCreate(&begin_event), "create ABBA begin Event");
            runtime(cudaEventCreate(&end_event), "create ABBA end Event");
            for (std::size_t launch_index = 0; launch_index < 4;
                 ++launch_index) {
                runtime(cudaDeviceSynchronize(),
                        "synchronize before ABBA reset");
                runtime(cudaMemset(chain_storage_device, 0,
                                   static_cast<std::size_t>(chain_storage_bytes)),
                        "reset every ABBA chain row and event");
                runtime(cudaMemset(chains_device, 0,
                                   chain_count * sizeof(DelayChain)),
                        "reset every ABBA chain output");
                runtime(cudaMemset(blocks_device, 0,
                                   block_count * sizeof(DelayBlock)),
                        "reset every ABBA block output");
                chain_config.delay_ns = delays[launch_index];
                chain_config.launch_epoch = 2 + launch_index;
                driver(cuMemcpyHtoD(chain_config_address, &chain_config,
                                    sizeof(chain_config)),
                       "publish ABBA delay and epoch");
                EvalChainDiagnosticConfig before_config{};
                driver(cuMemcpyDtoH(&before_config, chain_config_address,
                                    sizeof(before_config)),
                       "read back ABBA config before launch");
                require(std::memcmp(&before_config, &chain_config,
                                    sizeof(chain_config)) == 0,
                        "ABBA config publication readback mismatch");

                runtime(cudaEventRecord(begin_event), "begin ABBA Event");
                launch();
                runtime(cudaEventRecord(end_event), "end ABBA Event");
                runtime(cudaEventSynchronize(end_event),
                        "ABBA kernel completion");
                float elapsed_ms = 0;
                runtime(cudaEventElapsedTime(&elapsed_ms, begin_event,
                                             end_event),
                        "ABBA Event elapsed time");

                EvalChainDiagnosticConfig after_config{};
                driver(cuMemcpyDtoH(&after_config, chain_config_address,
                                    sizeof(after_config)),
                       "read back ABBA config after launch");
                require(std::memcmp(&after_config, &chain_config,
                                    sizeof(chain_config)) == 0,
                        "ABBA config changed during launch");
                std::vector<DelayChain> chains(chain_count);
                std::vector<DelayBlock> blocks(block_count);
                runtime(cudaMemcpy(chains.data(), chains_device,
                                   chains.size() * sizeof(DelayChain),
                                   cudaMemcpyDeviceToHost),
                        "copy ABBA chains");
                runtime(cudaMemcpy(blocks.data(), blocks_device,
                                   blocks.size() * sizeof(DelayBlock),
                                   cudaMemcpyDeviceToHost),
                        "copy ABBA block stamps");
                std::vector<std::byte> storage(
                    static_cast<std::size_t>(chain_storage_bytes));
                runtime(cudaMemcpy(storage.data(), chain_storage_device,
                                   storage.size(), cudaMemcpyDeviceToHost),
                        "copy exact ABBA chain diagnostic storage");
                auto rows = decode_chain_storage(
                    storage, after_config.row_count, after_config.row_stride,
                    after_config.trace_capacity);
                EvalDelayCounters counters{};
                for (const auto& row : rows) {
                    add_counters(&counters, row.row.counters);
                }

                json observed = {
                    {"launch_index", launch_index},
                    {"label", delays[launch_index] == 0 ? "A_D0" : "B_D500"},
                    {"config_readback_before_launch_exact", true},
                    {"config_readback_after_launch_exact", true},
                    {"shared_runtime_identity", runtime_identity},
                    {"schema_version", 1},
                    {"evidence", "GPU_ACQUISITION"},
                    {"treatment", o.treatment},
                    {"trace_mode", "per_chain"},
                    {"requested_delay_ns", delays[launch_index]},
                    {"applied_delay_ns", after_config.delay_ns},
                    {"hops", o.hops},
                    {"warps", o.warps},
                    {"occupancy", o.occupancy},
                    {"blocks", block_count},
                    {"sm_count", props.multiProcessorCount},
                    {"registers", registers},
                    {"theoretical_blocks_per_sm", maximum},
                    {"dynamic_shared_bytes", shared},
                    {"event_ns", double(elapsed_ms) * 1e6},
                    {"covered_accesses", counters.covered_accesses},
                    {"covered_bytes", counters.covered_bytes},
                    {"bypass_accesses", counters.bypass_accesses},
                    {"bypass_bytes", counters.bypass_bytes},
                    {"rejected_accesses", counters.rejected_accesses},
                    {"trace_overflow", counters.trace_overflow},
                    {"rewritten_instructions",
                     coverage.at("rewritten_instructions")},
                    {"unsupported_instructions",
                     coverage.at("unsupported_instructions")},
                    {"unknown_bytes", 0},
                    {"eligible_bytes", trace_count * 4},
                    {"seed", 0},
                    {"permutation_rule",
                     "next=(17*i+1)%4096; stride=4096 bytes; version=1"},
                    {"input_base", reinterpret_cast<std::uintptr_t>(input)},
                    {"input_bytes", words * sizeof(*input)},
                    {"chains", json::array()},
                    {"block_intervals", json::array()},
                    {"waits", json::array()},
                    {"validation", "NOT_RUN"},
                };
                bool checksums = true;
                for (unsigned index = 0; index < chain_count; ++index) {
                    const auto& chain = chains[index];
                    const auto expected = expected_checksum(index, o.hops);
                    checksums &= chain.checksum == expected;
                    observed["chains"].push_back(
                        {{"block", index / o.warps},
                         {"warp", index % o.warps},
                         {"sm", chain.sm},
                         {"begin_ns", chain.begin_ns},
                         {"end_ns", chain.end_ns},
                         {"checksum", chain.checksum},
                         {"expected_checksum", expected}});
                }
                for (unsigned index = 0; index < block_count; ++index) {
                    observed["block_intervals"].push_back(
                        {{"block", index}, {"sm", blocks[index].sm},
                         {"begin_ns", blocks[index].begin_ns},
                         {"end_ns", blocks[index].end_ns}});
                }
                observed["chain_diagnostic"] =
                    chain_diagnostic_json(after_config, rows);
                for (const auto& row : rows) {
                    for (const auto& event : row.events) {
                        if (event.event_class == static_cast<std::uint32_t>(
                                                     EvalChainEventClass::CoveredLoad)) {
                            observed["waits"].push_back(
                                {{"thread_id", event.thread_id},
                                 {"address", event.address},
                                 {"wait_enter_ns", event.begin_ns},
                                 {"wait_exit_ns", event.end_ns},
                                 {"delay_ns", after_config.delay_ns}});
                        }
                    }
                }

                result["launches"].push_back(observed);
                write(o.output + ".partial.json", result.dump(2));
                require(checksums,
                        "ABBA checksum differs from deterministic CPU reference");
                validate_chain_observations(
                    rows, chains, blocks, o.warps, o.hops,
                    after_config.launch_epoch, after_config.delay_ns,
                    reinterpret_cast<std::uintptr_t>(input),
                    reinterpret_cast<std::uintptr_t>(chains_device),
                    reinterpret_cast<std::uintptr_t>(blocks_device));
                result["launches"].back()["validation"] = "PASS";
                write(o.output + ".partial.json", result.dump(2));
            }
            EvalChainDiagnosticConfig disabled{};
            driver(cuMemcpyHtoD(chain_config_address, &disabled,
                                sizeof(disabled)),
                   "disable ABBA experiment before release");
            chain_enabled = false;
            write(o.output, result.dump(2));
            driver(cuModuleUnload(module), "unload ABBA module");
            module = nullptr;
            require(hbfsim_unregister(context, input) == HBFSIM_OK,
                    "unregister ABBA input failed");
            registered = false;
            hbfsim_context_destroy(context);
            context = nullptr;
            runtime(cudaEventDestroy(begin_event), "destroy ABBA begin Event");
            begin_event = nullptr;
            runtime(cudaEventDestroy(end_event), "destroy ABBA end Event");
            end_event = nullptr;
            runtime(cudaFree(chain_storage_device),
                    "free ABBA chain diagnostic storage");
            chain_storage_device = nullptr;
            runtime(cudaFree(blocks_device), "free ABBA block outputs");
            blocks_device = nullptr;
            runtime(cudaFree(chains_device), "free ABBA chain outputs");
            chains_device = nullptr;
            runtime(cudaFree(input), "free ABBA input");
            input = nullptr;
            return 0;
#else
            throw std::runtime_error("ABBA diagnostic was not built");
#endif
        }
        if (chain_trace) {
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
            runtime(cudaMemset(chain_storage_device, 0,
                               static_cast<std::size_t>(chain_storage_bytes)),
                    "reset every chain row and event after warmup");
            runtime(cudaMemset(chains_device, 0,
                               chain_count * sizeof(DelayChain)),
                    "reset chain outputs after warmup");
            runtime(cudaMemset(blocks_device, 0,
                               block_count * sizeof(DelayBlock)),
                    "reset block outputs after warmup");
            chain_config.launch_epoch = 2;
            driver(cuMemcpyHtoD(chain_config_address, &chain_config,
                                sizeof(chain_config)),
                   "publish immutable measured chain epoch");
#endif
        } else if (modeled) {
            EvalDelayCounters zero{};
            driver(cuMemcpyHtoD(counters_address, &zero, sizeof(zero)),
                   "reset counters after warmup");
        }
        runtime(cudaEventCreate(&begin_event), "create begin Event"); runtime(cudaEventCreate(&end_event), "create end Event");
        runtime(cudaEventRecord(begin_event), "begin Event"); launch(); runtime(cudaEventRecord(end_event), "end Event");
        runtime(cudaEventSynchronize(end_event), "kernel completion");
        float elapsed_ms = 0; runtime(cudaEventElapsedTime(&elapsed_ms, begin_event, end_event), "Event elapsed time");
        std::vector<DelayChain> chains(chain_count); std::vector<DelayBlock> blocks(block_count);
        std::vector<EvalDelayTrace> traces(
            modeled && !chain_trace ? trace_count : 0);
        EvalDelayCounters counters{};
        runtime(cudaMemcpy(chains.data(), chains_device, chains.size()*sizeof(DelayChain), cudaMemcpyDeviceToHost), "copy chains");
        runtime(cudaMemcpy(blocks.data(), blocks_device, blocks.size()*sizeof(DelayBlock), cudaMemcpyDeviceToHost), "copy block stamps");
        std::vector<ChainObservation> chain_rows;
        if (chain_trace) {
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
            std::vector<std::byte> storage(
                static_cast<std::size_t>(chain_storage_bytes));
            runtime(cudaMemcpy(storage.data(), chain_storage_device,
                               storage.size(), cudaMemcpyDeviceToHost),
                    "copy exact chain diagnostic storage");
            chain_rows = decode_chain_storage(
                storage, chain_config.row_count, chain_config.row_stride,
                chain_config.trace_capacity);
            for (const auto& row : chain_rows) add_counters(&counters, row.row.counters);
            EvalChainDiagnosticConfig disabled{};
            driver(cuMemcpyHtoD(chain_config_address, &disabled,
                                sizeof(disabled)),
                   "disable per-chain experiment before release");
            chain_enabled = false;
#endif
        } else if (modeled) {
            driver(cuMemcpyDtoH(&counters, counters_address, sizeof(counters)), "copy experiment counters");
            require(counters.covered_accesses == trace_count && counters.trace_overflow == 0, "dynamic helper coverage mismatch");
            runtime(cudaMemcpy(traces.data(), traces_device, traces.size()*sizeof(EvalDelayTrace), cudaMemcpyDeviceToHost), "copy wait stamps");
            EvalDelayConfig disabled{}; driver(cuMemcpyHtoD(config_address, &disabled, sizeof(disabled)), "disable experiment");
        }
        json result={{"schema_version",1},{"evidence","GPU_ACQUISITION"},{"treatment",o.treatment},
            {"trace_mode",o.trace_mode},
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
        if (chain_trace) {
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
            result["chain_diagnostic"] = chain_diagnostic_json(chain_config, chain_rows);
            for (const auto& row : chain_rows) {
                for (const auto& event : row.events) {
                    if (event.event_class == static_cast<std::uint32_t>(
                                                 EvalChainEventClass::CoveredLoad)) {
                        result["waits"].push_back(
                            {{"thread_id", event.thread_id},
                             {"address", event.address},
                             {"wait_enter_ns", event.begin_ns},
                             {"wait_exit_ns", event.end_ns},
                             {"delay_ns", chain_config.delay_ns}});
                    }
                }
            }
#endif
        } else {
            for (const auto& t : traces) result["waits"].push_back({{"thread_id",t.thread_id},{"address",t.address},{"wait_enter_ns",t.wait_enter_ns},{"wait_exit_ns",t.wait_exit_ns},{"delay_ns",t.delay_ns}});
        }
        if (per_chain && !modeled) {
            result["chain_diagnostic"] = {
                {"enabled", false}, {"reason", "native_uninstrumented"},
                {"rows", json::array()}};
        }
        // Save actual output and diagnostic observations before strict checks,
        // so a rejected acquisition retains the data that caused rejection.
        write(o.output, result.dump(2));
        require(checksums, "checksum differs from deterministic CPU reference");
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        if (chain_trace) {
            validate_chain_observations(
                chain_rows, chains, blocks, o.warps, o.hops,
                chain_config.launch_epoch, chain_config.delay_ns,
                reinterpret_cast<std::uintptr_t>(input),
                reinterpret_cast<std::uintptr_t>(chains_device),
                reinterpret_cast<std::uintptr_t>(blocks_device));
        }
#endif
        driver(cuModuleUnload(module), "unload module"); module = nullptr;
        if (registered) { require(hbfsim_unregister(context, input) == HBFSIM_OK, "unregister failed"); registered = false; }
        if (context) { hbfsim_context_destroy(context); context = nullptr; }
        cudaEventDestroy(begin_event); cudaEventDestroy(end_event);
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        cudaFree(chain_storage_device);
#endif
        cudaFree(traces_device); cudaFree(blocks_device); cudaFree(chains_device); cudaFree(input);
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "hbf_dependent_delay: %s\n", error.what());
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        if (chain_enabled) {
            const EvalChainDiagnosticConfig disabled{};
            const auto disable_result = cuMemcpyHtoD(
                chain_config_address, &disabled, sizeof(disabled));
            if (disable_result != CUDA_SUCCESS) {
                std::fprintf(stderr,
                             "hbf_dependent_delay: per-chain disable failed; "
                             "skipping explicit resource release\n");
                return 1;
            }
            chain_enabled = false;
        }
#endif
        if (module) (void)cuModuleUnload(module);
        if (registered && context) (void)hbfsim_unregister(context, input);
        if (context) hbfsim_context_destroy(context);
        if (begin_event) cudaEventDestroy(begin_event);
        if (end_event) cudaEventDestroy(end_event);
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        if (chain_storage_device) cudaFree(chain_storage_device);
#endif
        if (traces_device) cudaFree(traces_device);
        if (blocks_device) cudaFree(blocks_device);
        if (chains_device) cudaFree(chains_device);
        if (input) cudaFree(input);
        return 1;
    }
}
#endif
