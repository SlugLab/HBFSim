#include "scoped_accounting_policy.hpp"
#include "scoped_aux_range.hpp"
#include <algorithm>
#include "hbfsim/coverage.hpp"
#include "hbfsim/launch_gate_abi.hpp"
#include "hbfsim/module_identity.hpp"
#include "hbfsim/timing_binding.hpp"
#include "hbfsim/strict_direct_policy.hpp"
#include "../ptxpass_hbf/transform.hpp"

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>
#include <openssl/evp.h>

#include <atomic>
#include <array>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <sstream>
#include <set>
#include <utility>
#include <vector>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#ifdef cuGetProcAddress
#undef cuGetProcAddress
#endif
#ifdef cuCtxDestroy
#undef cuCtxDestroy
#endif
#ifdef cuDevicePrimaryCtxRelease
#undef cuDevicePrimaryCtxRelease
#endif
#ifdef cuDevicePrimaryCtxReset
#undef cuDevicePrimaryCtxReset
#endif

namespace {

hbfsim::TimingBindingRegistry& timing_bindings();

struct CudaDomain {
    std::uintptr_t context{0};
    int device{-1};
};

std::optional<CudaDomain> current_cuda_domain();
std::string handle_id(CUfunction function);
std::string function_name(CUfunction function);

const char* environment_or(const char* name, const char* fallback)
{
    const char* value = std::getenv(name);
    return value != nullptr && value[0] != '\0' ? value : fallback;
}

class RuntimeGate {
  public:
    RuntimeGate()
        : writer_(environment_or("HBFSIM_COVERAGE_PATH", "coverage.json"))
    {
    }

    hbfsim::CoverageGate& gate()
    {
        return gate_;
    }
    std::shared_lock<std::shared_mutex> launch_guard()
    {
        return range_launch_sync_.launch_guard();
    }
    auto accounting_transition_guard()
    {
        return range_launch_sync_.mutation_guard();
    }
    int register_range(std::uintptr_t owner, std::uint64_t generation,
                       std::uintptr_t begin, std::uintptr_t end,
                       hbfsim::RangePolicy policy,
                       hbfsim::LaunchGatePublishRange publish,
                       void* publish_state) noexcept
    {
        if (publish == nullptr) {
            return -1;
        }
        try {
            auto lock = range_launch_sync_.registration_guard();
            if (!timing_bindings().owns(owner, generation)) {
                return -1;
            }
            std::lock_guard ranges_lock(ranges_mutex_);
            registered_ranges_.reserve(registered_ranges_.size() + 1);
            gate_.add_range(begin, end, policy);
            if (publish(publish_state) != 0) {
                gate_.remove_range(begin, end);
                return -1;
            }
            registered_ranges_.push_back({begin, end - begin});
            return 0;
        } catch (const std::exception& error) {
            std::cerr << "hbfsim range registration: " << error.what()
                      << '\n';
            return -1;
        }
    }
    int unregister_range(std::uintptr_t owner, std::uint64_t generation,
                         std::uintptr_t begin, std::uintptr_t end,
                         hbfsim::LaunchGatePublishRange publish,
                         void* publish_state) noexcept
    {
        if (publish == nullptr) {
            return -1;
        }
        try {
            auto lock = range_launch_sync_.mutation_guard();
            if (!timing_bindings().owns(owner, generation)) {
                return -1;
            }
            const auto domain = current_cuda_domain();
            if (!domain.has_value() ||
                !timing_bindings().active_domain(domain->context,
                                                 domain->device) ||
                ::cudaDeviceSynchronize() != cudaSuccess) {
                return -1;
            }
            if (publish(publish_state) != 0) {
                return -1;
            }
            // Publication is the no-fail point. The exclusive mutation lock
            // keeps launches out until the exact gate range is removed.
            gate_.remove_range(begin, end);
            {
                std::lock_guard ranges_lock(ranges_mutex_);
                const auto found = std::find_if(registered_ranges_.rbegin(),
                    registered_ranges_.rend(), [&](const auto& range) {
                        return range.base == begin && range.bytes == end - begin;
                    });
                if (found != registered_ranges_.rend())
                    registered_ranges_.erase(std::next(found).base());
            }
            return 0;
        } catch (const std::exception& error) {
            std::cerr << "hbfsim range unregistration: " << error.what()
                      << '\n';
            return -1;
        }
    }
    std::unique_lock<std::shared_mutex> retirement_guard()
    {
        return range_launch_sync_.retirement_guard();
    }
    std::unique_lock<std::shared_mutex> activation_guard()
    {
        return range_launch_sync_.mutation_guard();
    }
    void finish_activation() noexcept
    {
        range_launch_sync_.reset_launch_seen();
    }
    void finish_retirement() noexcept
    {
        gate_.clear_ranges();
        {
            std::lock_guard ranges_lock(ranges_mutex_);
            registered_ranges_.clear();
        }
        range_launch_sync_.reset_launch_seen();
    }
    std::vector<hbfsim::scoped_aux::RegisteredSpan> registered_snapshot()
    {
        std::lock_guard ranges_lock(ranges_mutex_);
        return registered_ranges_;
    }
    void mark_launch_seen() noexcept
    {
        range_launch_sync_.mark_launch_seen();
    }

    void refresh_manifests()
    {
        std::scoped_lock lock(manifest_mutex_);
        std::set<std::string> present;
        const char* path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
        if (path == nullptr || path[0] == '\0') {
            present_future_manifests_.clear();
            return;
        }
        std::ifstream input(path);
        std::string line;
        while (std::getline(input, line)) {
            try {
                auto manifest=hbfsim::module_manifest_from_json(line);
                if(manifest.future_requirements)present.insert(manifest.future_manifest_sha256);
                gate_.add_module(std::move(manifest));
            } catch (const std::exception&) {
                // A concurrent pass may still be appending its last line.
                if(!input.eof() && line.find("timing_load_future_v1")!=std::string::npos)
                    future_manifest_error_.store(true,std::memory_order_release);
            }
        }
        present_future_manifests_=std::move(present);
    }
    bool future_manifests_valid() const noexcept { return !future_manifest_error_.load(std::memory_order_acquire); }
    bool future_manifest_present(const hbfsim::ModuleManifest& manifest) {
        std::scoped_lock lock(manifest_mutex_);
        return present_future_manifests_.contains(manifest.future_manifest_sha256);
    }

    bool approve(const hbfsim::GateDecision& decision) noexcept
    {
        const bool approved =
            hbfsim::coverage_decision_permits_launch(writer_, decision);
        if (!approved && decision.allowed) {
            std::cerr << "hbfsim coverage writer failed; rejecting launch\n";
        }
        return approved;
    }

  private:
    hbfsim::CoverageGate gate_;
    hbfsim::CoverageWriter writer_;
    std::mutex manifest_mutex_;
    std::mutex ranges_mutex_;
    std::vector<hbfsim::scoped_aux::RegisteredSpan> registered_ranges_;
    hbfsim::LaunchRangeSynchronizer range_launch_sync_;
    std::atomic_bool future_manifest_error_{false};
    std::set<std::string> present_future_manifests_;
};

RuntimeGate& runtime_gate()
{
    static RuntimeGate runtime;
    return runtime;
}

hbfsim::ModuleIdentityRegistry& module_identities()
{
    static hbfsim::ModuleIdentityRegistry registry;
    return registry;
}

std::mutex& function_alias_mutex()
{
    static std::mutex mutex;
    return mutex;
}

std::map<CUfunction, CUfunction>& function_aliases()
{
    static std::map<CUfunction, CUfunction> aliases;
    return aliases;
}

CUfunction canonical_function(CUfunction function)
{
    std::lock_guard lock(function_alias_mutex());
    const auto found = function_aliases().find(function);
    return found == function_aliases().end() ? function : found->second;
}

hbfsim::TimingBindingRegistry& timing_bindings()
{
    static hbfsim::TimingBindingRegistry registry;
    return registry;
}

std::recursive_mutex& lifecycle_transition_mutex()
{
    static std::recursive_mutex mutex;
    return mutex;
}

#if defined(HBFSIM_ENABLE_TEST_HOOKS)
std::atomic<bool> activation_attempt_armed{false};
std::atomic<bool> activation_attempt_seen{false};
std::atomic<bool> activation_contention_observed{false};
#endif

std::unique_lock<std::recursive_mutex> activation_transition_lock()
{
#if defined(HBFSIM_ENABLE_TEST_HOOKS)
    std::unique_lock transition(lifecycle_transition_mutex(), std::defer_lock);
    if (activation_attempt_armed.exchange(false, std::memory_order_acq_rel)) {
        const bool acquired = transition.try_lock();
        activation_contention_observed.store(!acquired,
                                             std::memory_order_release);
        activation_attempt_seen.store(true, std::memory_order_release);
        activation_attempt_seen.notify_all();
        if (!acquired) {
            transition.lock();
        }
    } else {
        transition.lock();
    }
    return transition;
#else
    return std::unique_lock(lifecycle_transition_mutex());
#endif
}

hbfsim::ModuleLoadTransactionStore& module_load_transactions()
{
    static hbfsim::ModuleLoadTransactionStore transactions;
    return transactions;
}

void* driver_symbol(const char* name)
{
    static void* driver = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
    return driver == nullptr ? nullptr : dlsym(driver, name);
}

std::optional<hbfsim::ModuleIdentity> live_module_identity(CUmodule module)
{
    using get_global_type =
        CUresult (*)(CUdeviceptr*, std::size_t*, CUmodule, const char*);
    using copy_type = CUresult (*)(void*, CUdeviceptr, std::size_t);
    static auto get_global = reinterpret_cast<get_global_type>(
        driver_symbol("cuModuleGetGlobal_v2"));
    static auto copy =
        reinterpret_cast<copy_type>(driver_symbol("cuMemcpyDtoH_v2"));
    CUdeviceptr address = 0;
    std::size_t size = 0;
    hbfsim::ModuleIdentity identity{};
    if (get_global != nullptr && copy != nullptr &&
        get_global(&address, &size, module, "__hbfsim_module_identity") ==
            CUDA_SUCCESS &&
        size == identity.size() &&
        copy(identity.data(), address, identity.size()) == CUDA_SUCCESS) {
        return identity;
    }
    return std::nullopt;
}

hbfsim::ModuleHandle module_handle(CUmodule module)
{
    return reinterpret_cast<hbfsim::ModuleHandle>(module);
}

std::optional<CudaDomain> current_cuda_domain()
{
    using get_context_type = CUresult (*)(CUcontext*);
    using get_device_type = CUresult (*)(CUdevice*);
    static auto get_context = reinterpret_cast<get_context_type>(
        driver_symbol("cuCtxGetCurrent"));
    static auto get_device =
        reinterpret_cast<get_device_type>(driver_symbol("cuCtxGetDevice"));
    CUcontext context = nullptr;
    CUdevice device = -1;
    if (get_context == nullptr || get_device == nullptr ||
        get_context(&context) != CUDA_SUCCESS || context == nullptr ||
        get_device(&device) != CUDA_SUCCESS || device < 0) {
        return std::nullopt;
    }
    return CudaDomain{.context = reinterpret_cast<std::uintptr_t>(context),
                      .device = static_cast<int>(device)};
}

// Request-scoped, module-local counters used only by the rebuttal harness.
// They deliberately do not extend the production control ABI.
constexpr std::uint64_t kAccessAccountingMagic = 0x4842464143435431ULL;
constexpr std::uint32_t kAccessAccountingVersion = 2;
struct AccessAccountingConfig {
    std::uint64_t magic;
    std::uint32_t version;
    std::uint32_t struct_bytes;
    std::uint64_t enabled;
    std::uint64_t request_epoch;
};
struct AccessAccountingCounters {
    std::uint64_t supported_accesses, supported_bytes;
    std::uint64_t in_range_accesses, in_range_intersection_bytes;
    std::uint64_t native_out_of_range_accesses, native_out_of_range_bytes;
    std::uint64_t modeled_admitted_accesses, modeled_admitted_bytes;
    std::uint64_t service_completed_accesses, service_completed_bytes;
    std::uint64_t failed_after_issue_accesses, failed_after_issue_bytes;
    std::uint64_t unsupported_preissue_accesses, unsupported_preissue_bytes;
    std::uint64_t failed_preissue_accesses, failed_preissue_bytes;
    std::uint64_t translation_failed_accesses, translation_failed_bytes;
    std::uint64_t service_requests;
    std::uint64_t unclassified_accesses, unclassified_bytes;
    std::uint64_t counter_overflow;
};
static_assert(sizeof(AccessAccountingConfig) == 32);
static_assert(sizeof(AccessAccountingCounters) == 176);

constexpr std::uint64_t kEvalDelayMagic = 0x4556414c444c5931ULL;
struct EvalDelayConfig {
    std::uint64_t magic, delay_ns, trace_address, trace_capacity;
};
struct EvalDelayCounters {
    std::uint64_t covered_accesses, covered_bytes, bypass_accesses,
        bypass_bytes, rejected_accesses, trace_overflow;
};
struct EvalDelayTrace {
    std::uint64_t thread_id, address, wait_enter_ns, wait_exit_ns, delay_ns;
};
static_assert(sizeof(EvalDelayConfig) == 32);
static_assert(sizeof(EvalDelayCounters) == 48);
static_assert(sizeof(EvalDelayTrace) == 40);

constexpr std::uint64_t kFirstFaultConfigMagic = 0x4842464641554c54ULL;
constexpr std::uint64_t kFirstFaultReadyMagic = 0x4842464646524459ULL;
constexpr std::uint64_t kFirstFaultFileMagic = 0x4842464646494c45ULL;
constexpr std::uint32_t kFirstFaultSchemaVersion = 1;
constexpr std::size_t kFirstFaultSlotCapacity = 8;
struct FirstFaultConfig {
    std::uint64_t magic;
    std::uint32_t schema_version;
    std::uint32_t struct_bytes;
    std::uint64_t enabled;
    std::uint64_t epoch;
    std::uint64_t compact_address;
    std::uint64_t record_address;
    std::uint64_t module_identity[4];
};
struct alignas(8) FirstFaultRecord {
    std::uint64_t schema_version, epoch, module_identity[4];
    std::uint32_t status, reason, phase, reserved0;
    std::uint64_t valid_bits, gpu_now_ns, arrival_ns, deadline_ns, target_ns;
    std::uint64_t heartbeat_value, heartbeat_observed_ns, heartbeat_current;
    std::uint64_t shutdown, fault;
    std::uint64_t expected_generation, header_generation;
    std::uint64_t sidecar_generation, sidecar_poisoned;
    std::uint64_t ticket, position, slot_index, ring_capacity;
    std::uint64_t request_slot_sequence, completion_slot_sequence;
    std::uint64_t device_request_producer, device_completion_consumer;
    std::uint64_t host_request_consumer, host_completion_producer;
    std::uint64_t admission_count, completion_request_id;
    std::uint64_t completion_modeled_ns;
    std::uint32_t completion_status;
    std::uint32_t block_x, block_y, block_z;
    std::uint32_t thread_x, thread_y, thread_z, lane, reserved1;
    std::uint64_t ready;
};
struct alignas(64) FirstFaultFileHeader {
    std::uint64_t magic;
    std::uint32_t schema_version;
    std::uint32_t header_bytes;
    std::uint64_t epoch;
    std::uint32_t slot_capacity;
    std::uint32_t module_count;
    std::uint32_t incomplete;
    std::uint32_t reserved;
    char reason[128];
};
struct alignas(64) FirstFaultFileSlot {
    char identity[96];
    std::uint32_t bound;
    std::uint32_t reserved;
    alignas(8) std::uint64_t compact_word;
    FirstFaultRecord record;
};
static_assert(sizeof(FirstFaultConfig) == 80);
static_assert(sizeof(FirstFaultRecord) == 328);

struct AccessTrackedModule {
    CUmodule module{};
    std::uintptr_t context{};
    int device{-1};
    std::string identity;
};
struct FirstFaultBoundModule {
    AccessTrackedModule tracked;
    std::size_t slot{};
    CUdeviceptr config_address{};
    CUdeviceptr claimed_address{};
    bool unloaded{false};
};
struct FirstFaultSession {
    bool active{false};
    std::uint64_t epoch{};
    int fd{-1};
    void* mapping{MAP_FAILED};
    std::size_t mapping_bytes{};
    void* device_mapping{};
    std::string path;
    std::vector<FirstFaultBoundModule> modules;
    bool domain_set{false};
    std::uintptr_t context{0};
    int device{-1};
    bool incomplete{false};
    std::string error;
};
struct AccessBoundModule {
    AccessTrackedModule tracked;
    CUdeviceptr config_address{};
    CUdeviceptr counters_address{};
    bool unloaded{false};
};
struct AccessSession {
    bool active{false};
    CudaDomain domain{};
    std::uint64_t epoch{};
    std::vector<AccessBoundModule> modules;
    std::vector<AccessTrackedModule> late_modules;
    std::string cached_json;
};

std::recursive_mutex access_accounting_mutex;
std::map<CUmodule, AccessTrackedModule> access_tracked_modules;
AccessSession access_session;
FirstFaultSession first_fault_session;
void first_fault_bind_module(const AccessTrackedModule&) noexcept;

struct EvalBoundModule {
    AccessTrackedModule tracked;
    CUdeviceptr config_address{}, counters_address{}, trace_address{};
    std::uint64_t trace_capacity{};
    bool unloaded{false};
};
struct EvalSession {
    bool active{false};
    CudaDomain domain{};
    std::uint64_t epoch{}, delay_ns{};
    std::vector<EvalBoundModule> modules;
    std::vector<AccessTrackedModule> late_modules;
    std::string cached_json;
};
EvalSession eval_session;
std::string joint_session_cached_json;
thread_local bool joint_begin_in_progress{false};
thread_local bool joint_internal_call{false};
bool joint_session_owned{false};
bool accounting_poisoned{false};
std::vector<CUdeviceptr> eval_quarantined_traces;

using module_global_type =
    CUresult (*)(CUdeviceptr*, std::size_t*, CUmodule, const char*);
using copy_htod_type = CUresult (*)(CUdeviceptr, const void*, std::size_t);
using copy_dtoh_type = CUresult (*)(void*, CUdeviceptr, std::size_t);
using context_sync_type = CUresult (*)();
using mem_alloc_type = CUresult (*)(CUdeviceptr*, std::size_t);
using mem_free_type = CUresult (*)(CUdeviceptr);

std::string json_escape(std::string_view value)
{
    std::ostringstream out;
    for (const unsigned char ch : value) {
        switch (ch) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (ch < 0x20) {
                out << "\\u" << std::hex << std::setw(4)
                    << std::setfill('0') << static_cast<unsigned>(ch)
                    << std::dec;
            } else {
                out << static_cast<char>(ch);
            }
        }
    }
    return out.str();
}

std::array<std::uint64_t, 4> first_fault_identity_words(
    std::string_view identity) noexcept
{
    std::array<std::uint64_t, 4> words{
        0xcbf29ce484222325ULL, 0x84222325cbf29ce4ULL,
        0x9e3779b97f4a7c15ULL, 0xd6e8feb86659fd93ULL};
    for (const unsigned char ch : identity)
        for (std::size_t i = 0; i != words.size(); ++i) {
            words[i] ^= static_cast<std::uint64_t>(ch) + (i << 8);
            words[i] *= 0x100000001b3ULL + 2 * i;
        }
    return words;
}

FirstFaultFileHeader* first_fault_header() noexcept
{
    return first_fault_session.mapping == MAP_FAILED
               ? nullptr
               : static_cast<FirstFaultFileHeader*>(first_fault_session.mapping);
}

FirstFaultFileSlot* first_fault_slots() noexcept
{
    auto* header = first_fault_header();
    return header == nullptr ? nullptr
        : reinterpret_cast<FirstFaultFileSlot*>(
              static_cast<std::byte*>(first_fault_session.mapping) +
              sizeof(FirstFaultFileHeader));
}

void first_fault_mark_incomplete(std::string reason) noexcept
{
    first_fault_session.incomplete = true;
    if (first_fault_session.error.empty()) first_fault_session.error = std::move(reason);
    if (auto* header = first_fault_header(); header != nullptr) {
        header->incomplete = 1;
        std::snprintf(header->reason, sizeof(header->reason), "%s",
                      first_fault_session.error.c_str());
        (void)::msync(header, sizeof(*header), MS_ASYNC);
    }
}

void first_fault_bind_module(const AccessTrackedModule& tracked) noexcept
{
    if (!first_fault_session.active) return;
    for (const auto& existing : first_fault_session.modules)
        if (existing.tracked.module == tracked.module) return;
    auto* header = first_fault_header();
    auto* slots = first_fault_slots();
    if (header == nullptr || slots == nullptr) {
        first_fault_mark_incomplete("mapping unavailable");
        return;
    }
    const auto slot_index = first_fault_session.modules.size();
    if (slot_index >= kFirstFaultSlotCapacity) {
        first_fault_mark_incomplete("module slot capacity exceeded");
        return;
    }
    auto& slot = slots[slot_index];
    std::memset(&slot, 0, sizeof(slot));
    std::snprintf(slot.identity, sizeof(slot.identity), "%s",
                  tracked.identity.c_str());
    header->module_count = static_cast<std::uint32_t>(slot_index + 1);
    (void)::msync(first_fault_session.mapping,
                  first_fault_session.mapping_bytes, MS_ASYNC);

    if (!first_fault_session.domain_set) {
        first_fault_session.domain_set = true;
        first_fault_session.context = tracked.context;
        first_fault_session.device = tracked.device;
    } else if (first_fault_session.context != tracked.context ||
               first_fault_session.device != tracked.device) {
        first_fault_session.modules.push_back({tracked, slot_index, 0, 0, false});
        first_fault_mark_incomplete("multiple CUDA domains are unsupported");
        return;
    }

    auto get = reinterpret_cast<module_global_type>(
        driver_symbol("cuModuleGetGlobal_v2"));
    auto put = reinterpret_cast<copy_htod_type>(
        driver_symbol("cuMemcpyHtoD_v2"));
    CUdeviceptr config_address = 0, claimed_address = 0;
    std::size_t config_bytes = 0, claimed_bytes = 0;
    if (get == nullptr || put == nullptr ||
        get(&config_address, &config_bytes, tracked.module,
            "__hbfsim_first_fault_config") != CUDA_SUCCESS ||
        config_bytes != sizeof(FirstFaultConfig) ||
        get(&claimed_address, &claimed_bytes, tracked.module,
            "__hbfsim_first_fault_claimed") != CUDA_SUCCESS ||
        claimed_bytes != sizeof(std::uint32_t)) {
        first_fault_session.modules.push_back(
            {tracked, slot_index, 0, 0, false});
        first_fault_mark_incomplete("module diagnostic symbols missing");
        return;
    }
    const std::uint32_t zero = 0;
    const auto identity_words = first_fault_identity_words(tracked.identity);
    const auto record_offset = sizeof(FirstFaultFileHeader) +
        slot_index * sizeof(FirstFaultFileSlot) +
        offsetof(FirstFaultFileSlot, record);
    const auto compact_offset = sizeof(FirstFaultFileHeader) +
        slot_index * sizeof(FirstFaultFileSlot) +
        offsetof(FirstFaultFileSlot, compact_word);
    FirstFaultConfig config{
        .magic = kFirstFaultConfigMagic,
        .schema_version = kFirstFaultSchemaVersion,
        .struct_bytes = sizeof(FirstFaultConfig),
        .enabled = 1,
        .epoch = first_fault_session.epoch,
        .compact_address = reinterpret_cast<std::uintptr_t>(
            static_cast<std::byte*>(first_fault_session.device_mapping) +
            compact_offset),
        .record_address = reinterpret_cast<std::uintptr_t>(
            static_cast<std::byte*>(first_fault_session.device_mapping) +
            record_offset),
        .module_identity = {identity_words[0], identity_words[1],
                            identity_words[2], identity_words[3]},
    };
    if (put(claimed_address, &zero, sizeof(zero)) != CUDA_SUCCESS ||
        put(config_address, &config, sizeof(config)) != CUDA_SUCCESS) {
        first_fault_session.modules.push_back(
            {tracked, slot_index, config_address, claimed_address, false});
        first_fault_mark_incomplete("module diagnostic config write failed");
        return;
    }
    slot.bound = 1;
    first_fault_session.modules.push_back(
        {tracked, slot_index, config_address, claimed_address, false});
    (void)::msync(first_fault_session.mapping,
                  first_fault_session.mapping_bytes, MS_ASYNC);
}

int first_fault_begin(std::uint64_t epoch, const char* path) noexcept
{
    try {
        if (epoch == 0 || path == nullptr || path[0] == '\0') return -1;
        auto launch_exclusion = runtime_gate().activation_guard();
        std::lock_guard lock(access_accounting_mutex);
        if (first_fault_session.active) return -2;
        FirstFaultSession fresh{};
        fresh.epoch = epoch;
        fresh.path = path;
        fresh.mapping_bytes = sizeof(FirstFaultFileHeader) +
            kFirstFaultSlotCapacity * sizeof(FirstFaultFileSlot);
        fresh.fd = ::open(path, O_CREAT | O_EXCL | O_RDWR, 0600);
        if (fresh.fd < 0 || ::ftruncate(fresh.fd, fresh.mapping_bytes) != 0) {
            if (fresh.fd >= 0) ::close(fresh.fd);
            return -3;
        }
        fresh.mapping = ::mmap(nullptr, fresh.mapping_bytes,
            PROT_READ | PROT_WRITE, MAP_SHARED, fresh.fd, 0);
        if (fresh.mapping == MAP_FAILED) {
            ::close(fresh.fd);
            return -4;
        }
        std::memset(fresh.mapping, 0, fresh.mapping_bytes);
        if (::cudaHostRegister(fresh.mapping, fresh.mapping_bytes,
                cudaHostRegisterMapped | cudaHostRegisterPortable) != cudaSuccess) {
            ::munmap(fresh.mapping, fresh.mapping_bytes);
            ::close(fresh.fd);
            return -5;
        }
        if (::cudaHostGetDevicePointer(&fresh.device_mapping, fresh.mapping, 0) !=
                cudaSuccess) {
            if (::cudaHostUnregister(fresh.mapping) == cudaSuccess) {
                ::munmap(fresh.mapping, fresh.mapping_bytes);
                ::close(fresh.fd);
            } else {
                first_fault_session = std::move(fresh);
                first_fault_session.active = true;
                first_fault_mark_incomplete(
                    "device pointer failed; registered mapping quarantined");
            }
            return -5;
        }
        first_fault_session = std::move(fresh);
        auto* header = first_fault_header();
        header->magic = kFirstFaultFileMagic;
        header->schema_version = kFirstFaultSchemaVersion;
        header->header_bytes = sizeof(FirstFaultFileHeader);
        header->epoch = epoch;
        header->slot_capacity = kFirstFaultSlotCapacity;
        first_fault_session.active = true;
        for (const auto& [module, tracked] : access_tracked_modules)
            first_fault_bind_module(tracked);
        (void)::msync(first_fault_session.mapping,
                      first_fault_session.mapping_bytes, MS_SYNC);
        return first_fault_session.incomplete ? -6 : 0;
    } catch (...) {
        return -9;
    }
}

bool first_fault_record_nonzero(const FirstFaultRecord& record) noexcept
{
    const auto* bytes = reinterpret_cast<const unsigned char*>(&record);
    for (std::size_t i = 0; i != offsetof(FirstFaultRecord, ready); ++i)
        if (bytes[i] != 0) return true;
    return false;
}

long long first_fault_snapshot(char* out_json, std::size_t capacity) noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (!first_fault_session.active || first_fault_header() == nullptr)
            return -1;
        auto* header = first_fault_header();
        auto* slots = first_fault_slots();
        bool incomplete = first_fault_session.incomplete;
        std::size_t captured = 0, partial = 0, compact_observed = 0;
        std::ostringstream out;
        out << "{\"schema\":1,\"epoch\":" << first_fault_session.epoch
            << ",\"backing_path\":\"" << json_escape(first_fault_session.path)
            << "\",\"module_count\":" << first_fault_session.modules.size()
            << ",\"slot_capacity\":" << kFirstFaultSlotCapacity
            << ",\"modules\":[";
        bool first = true;
        for (const auto& bound : first_fault_session.modules) {
            if (!first) out << ',';
            first = false;
            const auto& slot = slots[bound.slot];
            const auto compact = std::atomic_ref<const std::uint64_t>(
                slot.compact_word).load(std::memory_order_acquire);
            const auto compact_magic = static_cast<std::uint16_t>(compact >> 48);
            const auto compact_schema = static_cast<std::uint8_t>((compact >> 44) & 0xf);
            const auto compact_reason = static_cast<std::uint8_t>((compact >> 40) & 0xf);
            const auto compact_phase = static_cast<std::uint8_t>((compact >> 36) & 0xf);
            const auto compact_status = static_cast<std::uint8_t>((compact >> 28) & 0xff);
            const auto compact_epoch = compact & 0x0fffffffULL;
            const bool compact_valid = compact != 0 && compact_magic == 0x4846 &&
                compact_schema == 2 && compact_reason >= 1 && compact_reason <= 5 &&
                compact_phase >= 1 && compact_phase <= 5 &&
                compact_status >= 2 && compact_status <= 7 &&
                compact_epoch == (first_fault_session.epoch & 0x0fffffffULL);
            const auto ready = std::atomic_ref<const std::uint64_t>(
                slot.record.ready).load(std::memory_order_acquire);
            const auto words = first_fault_identity_words(bound.tracked.identity);
            bool identity_match = true;
            for (std::size_t i = 0; i != words.size(); ++i)
                identity_match &= slot.record.module_identity[i] == words[i];
            const bool valid = ready == kFirstFaultReadyMagic &&
                slot.record.schema_version == kFirstFaultSchemaVersion &&
                slot.record.epoch == first_fault_session.epoch && identity_match;
            const bool is_partial = ready == 0 && first_fault_record_nonzero(slot.record);
            if (valid) ++captured;
            if (is_partial) ++partial;
            if (compact_valid) ++compact_observed;
            if ((ready != 0 && !valid) || (is_partial && !compact_valid) ||
                (compact != 0 && !compact_valid) || slot.bound != 1 ||
                bound.unloaded) incomplete = true;
            out << "{\"identity\":\"" << json_escape(bound.tracked.identity)
                << "\",\"slot\":" << bound.slot
                << ",\"bound\":" << (slot.bound == 1 ? "true" : "false")
                << ",\"unloaded\":" << (bound.unloaded ? "true" : "false")
                << ",\"ready\":" << (valid ? "true" : "false")
                << ",\"partial\":" << (is_partial ? "true" : "false")
                << ",\"compact_state\":\""
                << (compact == 0 ? "NO_COMPACT_RECORD" :
                    compact_valid ? "COMPACT_OBSERVED" : "INVALID_COMPACT")
                << "\"";
            if (compact_valid)
                out << ",\"compact_status\":" << unsigned(compact_status)
                    << ",\"compact_reason\":" << unsigned(compact_reason)
                    << ",\"compact_phase\":" << unsigned(compact_phase)
                    << ",\"compact_epoch_tag\":" << compact_epoch
                    << ",\"compact_ordering\":\"UNSPECIFIED_MULTI_WRITER\"";
            if (valid) {
                const auto& r = slot.record;
                out << ",\"status\":" << r.status << ",\"reason\":" << r.reason
                    << ",\"phase\":" << r.phase << ",\"valid_bits\":" << r.valid_bits
                    << ",\"gpu_now_ns\":" << r.gpu_now_ns
                    << ",\"arrival_ns\":" << r.arrival_ns
                    << ",\"deadline_ns\":" << r.deadline_ns
                    << ",\"target_ns\":" << r.target_ns
                    << ",\"heartbeat_value\":" << r.heartbeat_value
                    << ",\"heartbeat_observed_ns\":" << r.heartbeat_observed_ns
                    << ",\"heartbeat_current\":" << r.heartbeat_current
                    << ",\"shutdown\":" << r.shutdown << ",\"fault\":" << r.fault
                    << ",\"expected_generation\":" << r.expected_generation
                    << ",\"header_generation\":" << r.header_generation
                    << ",\"sidecar_generation\":" << r.sidecar_generation
                    << ",\"sidecar_poisoned\":" << r.sidecar_poisoned
                    << ",\"ticket\":" << r.ticket << ",\"position\":" << r.position
                    << ",\"slot_index\":" << r.slot_index
                    << ",\"ring_capacity\":" << r.ring_capacity
                    << ",\"request_slot_sequence\":" << r.request_slot_sequence
                    << ",\"completion_slot_sequence\":" << r.completion_slot_sequence
                    << ",\"device_request_producer\":" << r.device_request_producer
                    << ",\"device_completion_consumer\":" << r.device_completion_consumer
                    << ",\"host_request_consumer\":" << r.host_request_consumer
                    << ",\"host_completion_producer\":" << r.host_completion_producer
                    << ",\"admission_count\":" << r.admission_count
                    << ",\"completion_request_id\":" << r.completion_request_id
                    << ",\"completion_status\":" << r.completion_status
                    << ",\"completion_modeled_ns\":" << r.completion_modeled_ns
                    << ",\"block\":[" << r.block_x << ',' << r.block_y << ',' << r.block_z
                    << "],\"thread\":[" << r.thread_x << ',' << r.thread_y << ',' << r.thread_z
                    << "],\"lane\":" << r.lane;
            }
            out << '}';
        }
        const char* status = incomplete ? "INCOMPLETE"
            : captured != 0 ? "FULL_COMMITTED"
            : compact_observed != 0 ? "COMPACT_OBSERVED"
            : "NO_COMPACT_RECORD";
        out << "],\"captured_count\":" << captured
            << ",\"partial_count\":" << partial
            << ",\"compact_observed_count\":" << compact_observed
            << ",\"status\":\"" << status << "\",\"error\":\""
            << json_escape(first_fault_session.error) << "\"}";
        const auto json = out.str();
        const auto required = json.size() + 1;
        if (out_json != nullptr && capacity >= required)
            std::memcpy(out_json, json.c_str(), required);
        return static_cast<long long>(required);
    } catch (...) { return -9; }
}

int first_fault_abort() noexcept
{
    try {
        auto launch_exclusion = runtime_gate().activation_guard();
        std::lock_guard lock(access_accounting_mutex);
        if (!first_fault_session.active) return 0;
        const auto domain = current_cuda_domain();
        if (!domain.has_value() || !first_fault_session.domain_set ||
            domain->context != first_fault_session.context ||
            domain->device != first_fault_session.device)
            return -5;
        if (::cudaDeviceSynchronize() != cudaSuccess) return -2;
        auto put = reinterpret_cast<copy_htod_type>(
            driver_symbol("cuMemcpyHtoD_v2"));
        const std::uint64_t disabled = 0;
        bool complete = put != nullptr;
        for (const auto& bound : first_fault_session.modules)
            if (bound.unloaded || bound.config_address == 0 ||
                put(bound.config_address + offsetof(FirstFaultConfig, enabled),
                    &disabled, sizeof(disabled)) != CUDA_SUCCESS)
                complete = false;
        if (!complete || ::cudaDeviceSynchronize() != cudaSuccess) return -3;
        if (::cudaHostUnregister(first_fault_session.mapping) != cudaSuccess)
            return -4;
        (void)::msync(first_fault_session.mapping,
                      first_fault_session.mapping_bytes, MS_SYNC);
        ::munmap(first_fault_session.mapping, first_fault_session.mapping_bytes);
        ::close(first_fault_session.fd);
        first_fault_session = {};
        return 0;
    } catch (...) { return -9; }
}

void access_track_module(CUmodule module, const hbfsim::ModuleIdentity& identity,
                         const CudaDomain& domain)
{
    std::lock_guard lock(access_accounting_mutex);
    AccessTrackedModule tracked{module, domain.context, domain.device,
        hbfsim::module_id_from_identity(identity)};
    access_tracked_modules.insert_or_assign(module, tracked);
    if (first_fault_session.active) first_fault_bind_module(tracked);
    if (access_session.active && access_session.domain.context == domain.context &&
        access_session.domain.device == domain.device) {
        access_session.late_modules.push_back(std::move(tracked));
    }
    if (eval_session.active && eval_session.domain.context == domain.context &&
        eval_session.domain.device == domain.device)
        eval_session.late_modules.push_back(access_tracked_modules.at(module));
}

void access_untrack_module(CUmodule module)
{
    std::lock_guard lock(access_accounting_mutex);
    access_tracked_modules.erase(module);
    if (first_fault_session.active)
        for (auto& bound : first_fault_session.modules)
            if (bound.tracked.module == module) bound.unloaded = true;
    if (access_session.active)
        for (auto& bound : access_session.modules)
            if (bound.tracked.module == module) bound.unloaded = true;
    if (eval_session.active)
        for (auto& bound : eval_session.modules)
            if (bound.tracked.module == module) bound.unloaded = true;
}

void access_erase_context(std::uintptr_t context)
{
    std::lock_guard lock(access_accounting_mutex);
    for (auto item = access_tracked_modules.begin();
         item != access_tracked_modules.end();) {
        if (item->second.context == context) item = access_tracked_modules.erase(item);
        else ++item;
    }
    if (access_session.active && access_session.domain.context == context)
        for (auto& bound : access_session.modules) bound.unloaded = true;
    if (eval_session.active && eval_session.domain.context == context)
        for (auto& bound : eval_session.modules) bound.unloaded = true;
}

void access_erase_device(int device)
{
    std::lock_guard lock(access_accounting_mutex);
    for (auto item = access_tracked_modules.begin();
         item != access_tracked_modules.end();) {
        if (item->second.device == device) item = access_tracked_modules.erase(item);
        else ++item;
    }
    if (access_session.active && access_session.domain.device == device)
        for (auto& bound : access_session.modules) bound.unloaded = true;
    if (eval_session.active && eval_session.domain.device == device)
        for (auto& bound : eval_session.modules) bound.unloaded = true;
}

bool access_disable(const std::vector<AccessBoundModule>& modules,
                    copy_htod_type put) noexcept
{
    bool complete = put != nullptr;
    const std::uint64_t disabled = 0;
    if (put != nullptr) {
        for (const auto& bound : modules) {
            if (bound.unloaded || bound.config_address == 0 ||
                put(bound.config_address + offsetof(AccessAccountingConfig, enabled),
                    &disabled, sizeof(disabled)) != CUDA_SUCCESS)
                complete = false;
        }
    }
    return complete;
}

int access_begin(std::uint64_t request_epoch) noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (accounting_poisoned) return -10;
        if (!joint_internal_call) joint_session_cached_json.clear();
        if (request_epoch == 0 || access_session.active ||
            (eval_session.active && !joint_begin_in_progress)) return -1;
        access_session.cached_json.clear();
        const auto domain = current_cuda_domain();
        auto get = reinterpret_cast<module_global_type>(driver_symbol("cuModuleGetGlobal_v2"));
        auto put = reinterpret_cast<copy_htod_type>(driver_symbol("cuMemcpyHtoD_v2"));
        auto read = reinterpret_cast<copy_dtoh_type>(driver_symbol("cuMemcpyDtoH_v2"));
        auto sync = reinterpret_cast<context_sync_type>(driver_symbol("cuCtxSynchronize"));
        if (!domain || !get || !put || !read || !sync ||
            sync() != CUDA_SUCCESS) return -2;
        if (eval_session.active &&
            (eval_session.epoch != request_epoch ||
             eval_session.domain.context != domain->context ||
             eval_session.domain.device != domain->device))
            return -1;
        std::vector<AccessBoundModule> resolved;
        for (const auto& [module, tracked] : access_tracked_modules) {
            if (tracked.context != domain->context || tracked.device != domain->device)
                continue;
            CUdeviceptr config_address = 0, counters_address = 0;
            std::size_t config_bytes = 0, counters_bytes = 0;
            if (get(&config_address, &config_bytes, module,
                    "__hbfsim_access_accounting_config") != CUDA_SUCCESS ||
                config_address == 0 || config_bytes != sizeof(AccessAccountingConfig) ||
                get(&counters_address, &counters_bytes, module,
                    "__hbfsim_access_accounting_counters") != CUDA_SUCCESS ||
                counters_address == 0 || counters_bytes != sizeof(AccessAccountingCounters)) {
                (void)access_disable(resolved, put);
                (void)sync();
                return -3;
            }
            resolved.push_back({tracked, config_address, counters_address, false});
        }
        if (resolved.empty()) return -4;
        AccessAccountingCounters zero{};
        AccessAccountingConfig disabled{kAccessAccountingMagic,
            kAccessAccountingVersion, sizeof(AccessAccountingConfig), 0,
            request_epoch};
        AccessAccountingConfig observed{};
        for (const auto& bound : resolved) {
            if (put(bound.config_address, &disabled, sizeof(disabled)) != CUDA_SUCCESS ||
                put(bound.counters_address, &zero, sizeof(zero)) != CUDA_SUCCESS ||
                read(&observed, bound.config_address, sizeof(observed)) != CUDA_SUCCESS ||
                std::memcmp(&observed, &disabled, sizeof(observed)) != 0) {
                const bool disabled = access_disable(resolved, put);
                const bool synchronized = sync() == CUDA_SUCCESS;
                if (!disabled || !synchronized) accounting_poisoned = true;
                return -5;
            }
        }
        auto enabled = disabled;
        enabled.enabled = 1;
        for (const auto& bound : resolved) {
            if (put(bound.config_address, &enabled, sizeof(enabled)) != CUDA_SUCCESS ||
                read(&observed, bound.config_address, sizeof(observed)) != CUDA_SUCCESS ||
                std::memcmp(&observed, &enabled, sizeof(observed)) != 0) {
                const bool disabled = access_disable(resolved, put);
                const bool synchronized = sync() == CUDA_SUCCESS;
                if (!disabled || !synchronized) accounting_poisoned = true;
                return -6;
            }
        }
        access_session.active = true;
        access_session.domain = *domain;
        access_session.epoch = request_epoch;
        access_session.modules = std::move(resolved);
        access_session.late_modules.clear();
        return 0;
    } catch (...) {
        return -9;
    }
}

constexpr std::array<const char*, 22> access_counter_names{{
    "supported_accesses", "supported_bytes", "in_range_accesses",
    "in_range_intersection_bytes", "native_out_of_range_accesses",
    "native_out_of_range_bytes", "modeled_admitted_accesses",
    "modeled_admitted_bytes", "service_completed_accesses",
    "service_completed_bytes", "failed_after_issue_accesses",
    "failed_after_issue_bytes", "unsupported_preissue_accesses",
    "unsupported_preissue_bytes", "failed_preissue_accesses",
    "failed_preissue_bytes", "translation_failed_accesses",
    "translation_failed_bytes", "service_requests", "unclassified_accesses",
    "unclassified_bytes", "counter_overflow"}};

std::string access_snapshot_json(bool initial_sync_ok,
                                 const std::vector<std::optional<AccessAccountingCounters>>& values,
                                 const std::vector<std::string>& errors,
                                 bool disable_ok, bool final_sync_ok)
{
    bool complete = initial_sync_ok && disable_ok && final_sync_ok &&
                    access_session.late_modules.empty();
    AccessAccountingCounters aggregate{};
    auto* aggregate_values = reinterpret_cast<std::uint64_t*>(&aggregate);
    std::ostringstream modules;
    for (std::size_t i = 0; i < access_session.modules.size(); ++i) {
        if (i) modules << ',';
        const auto& bound = access_session.modules[i];
        modules << "{\"identity\":\"" << json_escape(bound.tracked.identity) << "\"";
        if (!values[i]) {
            complete = false;
            modules << ",\"status\":\"INCOMPLETE\",\"error\":\""
                    << json_escape(errors[i]) << "\"}";
            continue;
        }
        modules << ",\"status\":\"COMPLETE\",\"counters\":{";
        const auto* counters = reinterpret_cast<const std::uint64_t*>(&*values[i]);
        for (std::size_t field = 0; field < access_counter_names.size(); ++field) {
            if (field) modules << ',';
            modules << '\"' << access_counter_names[field] << "\":" << counters[field];
            const auto before = aggregate_values[field];
            aggregate_values[field] += counters[field];
            if (aggregate_values[field] < before) complete = false;
        }
        modules << "}}";
    }
    for (const auto& late : access_session.late_modules) {
        if (!access_session.modules.empty() || &late != &access_session.late_modules.front())
            modules << ',';
        modules << "{\"identity\":\"" << json_escape(late.identity)
                << "\",\"status\":\"INCOMPLETE\",\"error\":\"late_module_after_begin\"}";
    }
    std::ostringstream out;
    out << "{\"status\":\"" << (complete ? "COMPLETE" : "INCOMPLETE")
        << "\",\"epoch\":" << access_session.epoch
        << ",\"module_count\":"
        << access_session.modules.size() + access_session.late_modules.size()
        << ",\"per_module\":[" << modules.str() << "],\"aggregate\":{";
    for (std::size_t field = 0; field < access_counter_names.size(); ++field) {
        if (field) out << ',';
        out << '\"' << access_counter_names[field] << "\":" << aggregate_values[field];
    }
    out << "},\"synchronization\":{\"before_snapshot\":"
        << (initial_sync_ok ? "true" : "false")
        << ",\"after_disable\":" << (final_sync_ok ? "true" : "false")
        << "},\"disable_complete\":" << (disable_ok ? "true" : "false") << '}';
    return out.str();
}

long long access_snapshot(char* out_json, std::size_t capacity) noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (joint_session_owned && !joint_internal_call) return -11;
        if (access_session.cached_json.empty()) {
            if (!access_session.active) return -1;
            auto put = reinterpret_cast<copy_htod_type>(driver_symbol("cuMemcpyHtoD_v2"));
            auto read = reinterpret_cast<copy_dtoh_type>(driver_symbol("cuMemcpyDtoH_v2"));
            auto sync = reinterpret_cast<context_sync_type>(driver_symbol("cuCtxSynchronize"));
            const bool initial_sync_ok = sync != nullptr && sync() == CUDA_SUCCESS;
            std::vector<std::optional<AccessAccountingCounters>> values(
                access_session.modules.size());
            std::vector<std::string> errors(access_session.modules.size());
            for (std::size_t i = 0; i < access_session.modules.size(); ++i) {
                const auto& bound = access_session.modules[i];
                if (bound.unloaded) errors[i] = "module_unloaded_during_request";
                else if (!initial_sync_ok) errors[i] = "pre_snapshot_synchronize_failed";
                else {
                    AccessAccountingCounters counters{};
                    if (read != nullptr &&
                        read(&counters, bound.counters_address, sizeof(counters)) == CUDA_SUCCESS)
                        values[i] = counters;
                    else errors[i] = "counter_read_failed";
                }
            }
            const bool disable_ok = access_disable(access_session.modules, put);
            const bool final_sync_ok = sync != nullptr && sync() == CUDA_SUCCESS;
            access_session.cached_json = access_snapshot_json(
                initial_sync_ok, values, errors, disable_ok, final_sync_ok);
            if (!disable_ok || !final_sync_ok) accounting_poisoned = true;
            access_session.active = false;
        }
        const auto required = access_session.cached_json.size() + 1;
        if (out_json != nullptr && capacity >= required)
            std::memcpy(out_json, access_session.cached_json.c_str(), required);
        return static_cast<long long>(required);
    } catch (...) {
        return -9;
    }
}

int access_abort() noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (joint_session_owned && !joint_internal_call) return -11;
        if (!access_session.active) return 0;
        auto put = reinterpret_cast<copy_htod_type>(driver_symbol("cuMemcpyHtoD_v2"));
        auto sync = reinterpret_cast<context_sync_type>(driver_symbol("cuCtxSynchronize"));
        const bool disabled = access_disable(access_session.modules, put);
        const bool synchronized = sync != nullptr && sync() == CUDA_SUCCESS;
        access_session = {};
        if (!disabled || !synchronized) accounting_poisoned = true;
        return disabled && synchronized ? 0 : -1;
    } catch (...) {
        return -9;
    }
}

bool eval_disable(std::vector<EvalBoundModule>& modules,
                  copy_htod_type put) noexcept
{
    bool complete = put != nullptr;
    const EvalDelayConfig disabled{};
    for (auto& bound : modules) {
        if (bound.unloaded || !put ||
            put(bound.config_address, &disabled, sizeof(disabled)) != CUDA_SUCCESS)
            complete = false;
    }
    return complete;
}

bool eval_free(std::vector<EvalBoundModule>& modules,
               mem_free_type free_memory) noexcept
{
    bool complete = free_memory != nullptr;
    for (auto& bound : modules) {
        if (!bound.trace_address) continue;
        if (!free_memory || free_memory(bound.trace_address) != CUDA_SUCCESS)
            complete = false;
        else bound.trace_address = 0;
    }
    return complete;
}

bool eval_rollback(std::vector<EvalBoundModule>& modules, copy_htod_type put,
                   context_sync_type sync, mem_free_type free_memory) noexcept
{
    const bool disabled = eval_disable(modules, put);
    const bool synchronized = sync != nullptr && sync() == CUDA_SUCCESS;
    if (!disabled || !synchronized) {
        for (auto& bound : modules)
            if (bound.trace_address) {
                eval_quarantined_traces.push_back(bound.trace_address);
                bound.trace_address = 0;
            }
        accounting_poisoned = true;
        return false;
    }
    const bool freed = eval_free(modules, free_memory);
    if (!freed) accounting_poisoned = true;
    return freed;
}

int eval_begin(std::uint64_t delay_ns, std::uint64_t request_epoch,
               std::uint64_t trace_capacity) noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (accounting_poisoned) return -10;
        if (!joint_internal_call) joint_session_cached_json.clear();
        if (request_epoch == 0 || trace_capacity == 0 || delay_ns > 20'000 ||
            eval_session.active || access_session.active ||
            trace_capacity > SIZE_MAX / sizeof(EvalDelayTrace))
            return -1;
        eval_session.cached_json.clear();
        const auto domain = current_cuda_domain();
        auto get = reinterpret_cast<module_global_type>(driver_symbol("cuModuleGetGlobal_v2"));
        auto put = reinterpret_cast<copy_htod_type>(driver_symbol("cuMemcpyHtoD_v2"));
        auto read = reinterpret_cast<copy_dtoh_type>(driver_symbol("cuMemcpyDtoH_v2"));
        auto sync = reinterpret_cast<context_sync_type>(driver_symbol("cuCtxSynchronize"));
        auto allocate = reinterpret_cast<mem_alloc_type>(driver_symbol("cuMemAlloc_v2"));
        auto free_memory = reinterpret_cast<mem_free_type>(driver_symbol("cuMemFree_v2"));
        if (!domain || !get || !put || !read || !sync || !allocate ||
            !free_memory || sync() != CUDA_SUCCESS) return -2;
        std::vector<EvalBoundModule> resolved;
        for (const auto& [module, tracked] : access_tracked_modules) {
            if (tracked.context != domain->context || tracked.device != domain->device)
                continue;
            CUdeviceptr config = 0, counters = 0, trace = 0;
            std::size_t config_bytes = 0, counter_bytes = 0;
            if (get(&config, &config_bytes, module, "__hbfsim_eval_delay_config") != CUDA_SUCCESS ||
                !config || config_bytes != sizeof(EvalDelayConfig) ||
                get(&counters, &counter_bytes, module, "__hbfsim_eval_delay_counters") != CUDA_SUCCESS ||
                !counters || counter_bytes != sizeof(EvalDelayCounters) ||
                allocate(&trace, trace_capacity * sizeof(EvalDelayTrace)) != CUDA_SUCCESS || !trace) {
                (void)eval_rollback(resolved, put, sync, free_memory);
                return -3;
            }
            resolved.push_back({tracked, config, counters, trace, trace_capacity, false});
        }
        if (resolved.empty()) return -4;
        const EvalDelayCounters zero{};
        EvalDelayConfig config{kEvalDelayMagic, delay_ns, 0, trace_capacity}, observed{};
        for (auto& bound : resolved) {
            config.trace_address = bound.trace_address;
            if (put(bound.counters_address, &zero, sizeof(zero)) != CUDA_SUCCESS ||
                put(bound.config_address, &config, sizeof(config)) != CUDA_SUCCESS ||
                read(&observed, bound.config_address, sizeof(observed)) != CUDA_SUCCESS ||
                std::memcmp(&observed, &config, sizeof(config)) != 0) {
                (void)eval_rollback(resolved, put, sync, free_memory);
                return -5;
            }
        }
        eval_session.active = true;
        eval_session.domain = *domain;
        eval_session.epoch = request_epoch;
        eval_session.delay_ns = delay_ns;
        eval_session.modules = std::move(resolved);
        eval_session.late_modules.clear();
        return 0;
    } catch (...) { return -9; }
}

long long eval_snapshot(char* out_json, std::size_t capacity) noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (joint_session_owned && !joint_internal_call) return -11;
        if (eval_session.cached_json.empty()) {
            if (!eval_session.active) return -1;
            auto put = reinterpret_cast<copy_htod_type>(driver_symbol("cuMemcpyHtoD_v2"));
            auto read = reinterpret_cast<copy_dtoh_type>(driver_symbol("cuMemcpyDtoH_v2"));
            auto sync = reinterpret_cast<context_sync_type>(driver_symbol("cuCtxSynchronize"));
            auto free_memory = reinterpret_cast<mem_free_type>(driver_symbol("cuMemFree_v2"));
            const bool initial_sync = sync && sync() == CUDA_SUCCESS;
            bool complete = initial_sync && eval_session.late_modules.empty();
            EvalDelayCounters aggregate{};
            auto* aggregate_fields = reinterpret_cast<std::uint64_t*>(&aggregate);
            std::ostringstream per_module;
            for (std::size_t index = 0; index < eval_session.modules.size(); ++index) {
                auto& bound = eval_session.modules[index];
                if (index) per_module << ',';
                per_module << "{\"identity\":\"" << json_escape(bound.tracked.identity) << "\"";
                EvalDelayCounters counters{};
                std::string error;
                if (bound.unloaded) error = "module_unloaded_during_request";
                else if (!initial_sync)
                    error = "pre_snapshot_synchronize_failed";
                else if (!read || read(&counters, bound.counters_address, sizeof(counters)) != CUDA_SUCCESS)
                    error = "counter_read_failed";
                std::vector<EvalDelayTrace> traces;
                if (error.empty()) {
                    const auto count = std::min(counters.covered_accesses,
                                                bound.trace_capacity);
                    traces.resize(static_cast<std::size_t>(count));
                    if (count && read(traces.data(), bound.trace_address,
                                      count * sizeof(EvalDelayTrace)) != CUDA_SUCCESS)
                        error = "trace_read_failed";
                    if (counters.trace_overflow != 0) {
                        complete = false;
                        if (error.empty()) error = "trace_overflow";
                    }
                }
                if (!error.empty()) {
                    complete = false;
                    per_module << ",\"status\":\"INCOMPLETE\",\"error\":\""
                               << error << "\"}";
                    continue;
                }
                const auto* fields = reinterpret_cast<const std::uint64_t*>(&counters);
                static constexpr std::array<const char*, 6> names{{
                    "covered_accesses", "covered_bytes", "bypass_accesses",
                    "bypass_bytes", "rejected_accesses", "trace_overflow"}};
                per_module << ",\"status\":\"COMPLETE\",\"counters\":{";
                for (std::size_t field = 0; field < names.size(); ++field) {
                    if (field) per_module << ',';
                    per_module << '\"' << names[field] << "\":" << fields[field];
                    aggregate_fields[field] += fields[field];
                }
                per_module << "},\"trace_drop\":null,\"traces\":[";
                for (std::size_t trace_index = 0; trace_index < traces.size(); ++trace_index) {
                    if (trace_index) per_module << ',';
                    const auto& trace = traces[trace_index];
                    per_module << "{\"thread_id\":" << trace.thread_id
                        << ",\"address\":" << trace.address
                        << ",\"wait_enter_ns\":" << trace.wait_enter_ns
                        << ",\"wait_exit_ns\":" << trace.wait_exit_ns
                        << ",\"delay_ns\":" << trace.delay_ns << '}';
                }
                per_module << "]}";
            }
            for (const auto& late : eval_session.late_modules) {
                if (!eval_session.modules.empty() || &late != &eval_session.late_modules.front())
                    per_module << ',';
                per_module << "{\"identity\":\"" << json_escape(late.identity)
                           << "\",\"status\":\"INCOMPLETE\",\"error\":\"late_module_after_begin\"}";
            }
            const bool disabled = eval_disable(eval_session.modules, put);
            const bool final_sync = sync && sync() == CUDA_SUCCESS;
            bool freed = false;
            if (disabled && final_sync) {
                freed = eval_free(eval_session.modules, free_memory);
            } else {
                for (auto& bound : eval_session.modules)
                    if (bound.trace_address) {
                        eval_quarantined_traces.push_back(bound.trace_address);
                        bound.trace_address = 0;
                    }
            }
            complete = complete && disabled && final_sync && freed;
            if (!disabled || !final_sync || !freed) accounting_poisoned = true;
            static constexpr std::array<const char*, 6> names{{
                "covered_accesses", "covered_bytes", "bypass_accesses",
                "bypass_bytes", "rejected_accesses", "trace_overflow"}};
            std::ostringstream out;
            out << "{\"status\":\"" << (complete ? "COMPLETE" : "INCOMPLETE")
                << "\",\"epoch\":" << eval_session.epoch
                << ",\"delay_ns\":" << eval_session.delay_ns
                << ",\"module_count\":"
                << eval_session.modules.size() + eval_session.late_modules.size()
                << ",\"per_module\":[" << per_module.str() << "],\"aggregate\":{";
            for (std::size_t field = 0; field < names.size(); ++field) {
                if (field) out << ',';
                out << '\"' << names[field] << "\":" << aggregate_fields[field];
            }
            out << "},\"trace_drop\":null,\"disable_complete\":" << (disabled ? "true" : "false")
                << ",\"free_complete\":" << (freed ? "true" : "false")
                << ",\"final_sync\":" << (final_sync ? "true" : "false") << '}';
            eval_session.cached_json = out.str();
            eval_session.active = false;
        }
        const auto required = eval_session.cached_json.size() + 1;
        if (out_json && capacity >= required)
            std::memcpy(out_json, eval_session.cached_json.c_str(), required);
        return static_cast<long long>(required);
    } catch (...) { return -9; }
}

int eval_abort() noexcept
{
    try {
        std::lock_guard lock(access_accounting_mutex);
        if (joint_session_owned && !joint_internal_call) return -11;
        if (!eval_session.active) return 0;
        auto put = reinterpret_cast<copy_htod_type>(driver_symbol("cuMemcpyHtoD_v2"));
        auto sync = reinterpret_cast<context_sync_type>(driver_symbol("cuCtxSynchronize"));
        auto free_memory = reinterpret_cast<mem_free_type>(driver_symbol("cuMemFree_v2"));
        const bool disabled = eval_disable(eval_session.modules, put);
        const bool synchronized = sync && sync() == CUDA_SUCCESS;
        bool freed = false;
        if (disabled && synchronized) {
            freed = eval_free(eval_session.modules, free_memory);
        } else {
            for (auto& bound : eval_session.modules)
                if (bound.trace_address) {
                    eval_quarantined_traces.push_back(bound.trace_address);
                    bound.trace_address = 0;
                }
        }
        eval_session = {};
        if (!disabled || !synchronized || !freed) accounting_poisoned = true;
        return disabled && synchronized && freed ? 0 : -1;
    } catch (...) { return -9; }
}

bool initialize_module_control(hbfsim::ModuleHandle raw_module,
                               std::uintptr_t control_alias,
                               std::uint64_t generation, void*) noexcept
{
    using get_global_type =
        CUresult (*)(CUdeviceptr*, std::size_t*, CUmodule, const char*);
    using copy_type = CUresult (*)(CUdeviceptr, const void*, std::size_t);
    static auto get_global = reinterpret_cast<get_global_type>(
        driver_symbol("cuModuleGetGlobal_v2"));
    static auto copy =
        reinterpret_cast<copy_type>(driver_symbol("cuMemcpyHtoD_v2"));
    if (get_global == nullptr || copy == nullptr) {
        return false;
    }
    const auto module = reinterpret_cast<CUmodule>(raw_module);
    CUdeviceptr alias_address = 0;
    CUdeviceptr generation_address = 0;
    std::size_t alias_bytes = 0;
    std::size_t generation_bytes = 0;
    if (get_global(&alias_address, &alias_bytes, module,
                   "__hbfsim_control") != CUDA_SUCCESS ||
        alias_bytes != sizeof(control_alias) ||
        get_global(&generation_address, &generation_bytes, module,
                   "__hbfsim_control_generation") != CUDA_SUCCESS ||
        generation_bytes != sizeof(generation)) {
        return false;
    }
    if (control_alias != 0) {
        // Publish generation before alias. A failed second write leaves the
        // dereference-enabling alias at zero; best-effort generation rollback
        // keeps diagnostics clean but is not required for safety.
        if (copy(generation_address, &generation, sizeof(generation)) !=
            CUDA_SUCCESS) {
            return false;
        }
        if (copy(alias_address, &control_alias, sizeof(control_alias)) !=
            CUDA_SUCCESS) {
            const std::uint64_t zero = 0;
            (void)copy(generation_address, &zero, sizeof(zero));
            return false;
        }
    } else {
        // Invalidate the alias first. Even if clearing generation fails, the
        // module can no longer dereference the retiring control mapping.
        if (copy(alias_address, &control_alias, sizeof(control_alias)) !=
                CUDA_SUCCESS ||
            copy(generation_address, &generation, sizeof(generation)) !=
                CUDA_SUCCESS) {
            return false;
        }
    }
    return true;
}

void erase_module_identity(hbfsim::ModuleHandle module, void*) noexcept
{
    module_identities().erase(module);
}

struct FutureBudget { std::uint64_t alias,generation,remaining; };
std::mutex future_budget_mutex;
std::map<hbfsim::ModuleHandle,FutureBudget> future_budgets;
struct FutureGeometry { std::array<std::uint32_t,3> grid,block; };
thread_local std::optional<FutureGeometry> future_geometry;
struct FutureGeometryScope {
    std::optional<FutureGeometry> old{future_geometry};
    explicit FutureGeometryScope(FutureGeometry geometry) { future_geometry=geometry; }
    ~FutureGeometryScope(){future_geometry=old;}
};

enum class FutureManifestMatch { Absent, Matched, Invalid };

FutureManifestMatch future_manifest_match(CUmodule module,const hbfsim::ModuleManifest& manifest)
{
    using get_type=CUresult (*)(CUdeviceptr*,std::size_t*,CUmodule,const char*);
    using read_type=CUresult (*)(void*,CUdeviceptr,std::size_t);
    static auto get=reinterpret_cast<get_type>(driver_symbol("cuModuleGetGlobal_v2"));
    static auto read=reinterpret_cast<read_type>(driver_symbol("cuMemcpyDtoH_v2"));
    CUdeviceptr address=0;std::size_t size=0;std::array<unsigned char,32> digest{};
    const auto symbol=hbfsim::future_kernel_contract_symbol(manifest.kernel);
    if(!get || !read)return FutureManifestMatch::Invalid;
    const auto lookup=get(&address,&size,module,symbol.c_str());
    if(lookup==CUDA_ERROR_NOT_FOUND)return FutureManifestMatch::Absent;
    if(lookup!=CUDA_SUCCESS || !address || size!=digest.size() ||
        read(digest.data(),address,size)!=CUDA_SUCCESS)return FutureManifestMatch::Invalid;
    std::ostringstream hex;hex<<std::hex<<std::setfill('0');for(auto b:digest)hex<<std::setw(2)<<static_cast<unsigned>(b);
    return hex.str()==manifest.future_manifest_sha256 ? FutureManifestMatch::Matched : FutureManifestMatch::Invalid;
}

bool read_future_requirements(CUmodule module,
    std::optional<hbfsim::timing_future::ModuleRequirements>& result) noexcept
{
    using get_type=CUresult (*)(CUdeviceptr*,std::size_t*,CUmodule,const char*);
    using copy_type=CUresult (*)(void*,CUdeviceptr,std::size_t);
    static auto get=reinterpret_cast<get_type>(driver_symbol("cuModuleGetGlobal_v2"));
    static auto copy=reinterpret_cast<copy_type>(driver_symbol("cuMemcpyDtoH_v2"));
    if (!get || !copy) return false;
    CUdeviceptr address=0;std::size_t bytes=0;
    const auto lookup=get(&address,&bytes,module,"__hbfsim_timing_future_requirements_v1");
    if (lookup==CUDA_ERROR_NOT_FOUND) return true;
    if (lookup!=CUDA_SUCCESS) return false; // Inaccessible is not an absent contract.
    hbfsim::timing_future::ModuleRequirements requirements{};
    if (bytes!=sizeof(requirements) || copy(&requirements,address,bytes)!=CUDA_SUCCESS ||
        !hbfsim::timing_future::valid_requirements(requirements)) return false;
    result=requirements;
    return true;
}

hbfsim::FutureInitialization initialize_future_control(hbfsim::ModuleHandle raw_module,
    std::uintptr_t alias,std::uint64_t generation,
    const hbfsim::timing_future::Capabilities& capabilities,
    const hbfsim::timing_future::ModuleRequirements& requirements,void*) noexcept
{
    using namespace hbfsim::timing_future;
    using get_type=CUresult (*)(CUdeviceptr*,std::size_t*,CUmodule,const char*);
    using put_type=CUresult (*)(CUdeviceptr,const void*,std::size_t);
    using read_type=CUresult (*)(void*,CUdeviceptr,std::size_t);
    static auto get=reinterpret_cast<get_type>(driver_symbol("cuModuleGetGlobal_v2"));
    static auto put=reinterpret_cast<put_type>(driver_symbol("cuMemcpyHtoD_v2"));
    static auto read=reinterpret_cast<read_type>(driver_symbol("cuMemcpyDtoH_v2"));
    if (!get || !put || !read) return hbfsim::FutureInitialization::Quarantine;
    const auto module=reinterpret_cast<CUmodule>(raw_module);
    CUdeviceptr config_address=0,helper_address=0;std::size_t config_bytes=0,helper_bytes=0;
    const auto config_found=get(&config_address,&config_bytes,module,"__hbfsim_timing_future_config_v1")==CUDA_SUCCESS;
    const auto rollback=[&]() {
        const std::uint32_t disabled=0;
        bool clear=config_found && config_bytes>=offsetof(ModuleConfig,enabled)+sizeof(disabled) &&
            put(config_address+offsetof(ModuleConfig,enabled),&disabled,sizeof(disabled))==CUDA_SUCCESS;
        // Attempt every clear even when the earlier write failed.
        const bool alias_clear=initialize_module_control(raw_module,0,0,nullptr);
        ModuleConfig zero{};
        const bool config_clear=config_found && config_bytes==sizeof(zero) &&
            put(config_address,&zero,sizeof(zero))==CUDA_SUCCESS;
        if(clear && alias_clear && config_clear) {
            std::lock_guard lock(future_budget_mutex);future_budgets.erase(raw_module);
            return true;
        }
        return false;
    };
    if (!alias) return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    ModuleRequirements helper{};ModuleConfig previous{};
    const bool valid=config_found && config_bytes==sizeof(previous) &&
        read(&previous,config_address,config_bytes)==CUDA_SUCCESS &&
        previous.abi_version==kAbiVersion && previous.struct_bytes==sizeof(previous) &&
        previous.enabled==0 && previous.reserved==0 && previous.reserved2==0 &&
        get(&helper_address,&helper_bytes,module,"__hbfsim_timing_future_helper_abi_v1")==CUDA_SUCCESS &&
        helper_bytes==sizeof(helper) && read(&helper,helper_address,helper_bytes)==CUDA_SUCCESS &&
        valid_requirements(helper) && valid_requirements(requirements) &&
        requirements.maximum_thread_futures<=helper.maximum_thread_futures &&
        requirements.maximum_block_threads<=helper.maximum_block_threads;
    if (!valid) return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    const auto identity=live_module_identity(module);
    auto& runtime=runtime_gate();runtime.refresh_manifests();
    if(!kUnitComplete || !generation || !supports(capabilities,requirements) ||
        std::memcmp(&helper,&requirements,sizeof(helper)) || !identity || !runtime.future_manifests_valid() ||
        !runtime.gate().future_module_contract(hbfsim::module_id_from_identity(*identity),requirements,
            hbfsim::ptx::embedded_device_helper_sha256()))
        return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    bool present_manifest=false;
    // Same-original-PTX images can contain different immutable subsets of the
    // cached kernel manifests. Only an exact NOT_FOUND is absent; inaccessible
    // or mismatched present constants invalidate this image's initialization.
    for(const auto& manifest:runtime.gate().module_manifests(hbfsim::module_id_from_identity(*identity))) {
        const auto match=future_manifest_match(module,manifest);
        if(match==FutureManifestMatch::Absent)continue;
        if(match!=FutureManifestMatch::Matched || !runtime.future_manifest_present(manifest))
            return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
        present_manifest=true;
    }
    if(!present_manifest)
        return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    CUdeviceptr trace_address=0,counter_address=0,hash_address=0;
    std::size_t trace_bytes=0,counter_bytes=0,hash_bytes=0;
    std::array<unsigned char,32> hash{};
    std::ostringstream hash_hex;
    const bool storage=get(&trace_address,&trace_bytes,module,"__hbfsim_timing_future_trace_v1")==CUDA_SUCCESS &&
        trace_bytes==kTraceCapacity*sizeof(Trace) && trace_address && trace_address%alignof(Trace)==0 &&
        trace_address<=UINT64_MAX-trace_bytes &&
        get(&counter_address,&counter_bytes,module,"__hbfsim_timing_future_counters_v1")==CUDA_SUCCESS &&
        counter_bytes==sizeof(Counters) && counter_address && counter_address%alignof(Counters)==0 &&
        get(&hash_address,&hash_bytes,module,"__hbfsim_timing_future_helper_sha256_v1")==CUDA_SUCCESS &&
        hash_bytes==hash.size() && read(hash.data(),hash_address,hash.size())==CUDA_SUCCESS;
    hash_hex<<std::hex<<std::setfill('0');for(auto byte:hash)hash_hex<<std::setw(2)<<static_cast<unsigned>(byte);
    if(!storage || hash_hex.str()!=hbfsim::ptx::embedded_device_helper_sha256())
        return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    ModuleConfig disabled{};
    disabled.control_alias=alias;disabled.control_generation=generation;
    disabled.trace_address=trace_address;disabled.trace_capacity=kTraceCapacity;
    disabled.maximum_thread_futures=requirements.maximum_thread_futures;
    disabled.maximum_block_threads=requirements.maximum_block_threads;
    const std::uint32_t off=0;Counters counters{};Counters observed{};ModuleConfig copied{};
    if (put(config_address+offsetof(ModuleConfig,enabled),&off,sizeof(off))!=CUDA_SUCCESS ||
        put(counter_address,&counters,sizeof(counters))!=CUDA_SUCCESS ||
        put(config_address,&disabled,sizeof(disabled))!=CUDA_SUCCESS ||
        !initialize_module_control(raw_module,alias,generation,nullptr) ||
        read(&copied,config_address,sizeof(copied))!=CUDA_SUCCESS || std::memcmp(&copied,&disabled,sizeof(copied)) ||
        read(&observed,counter_address,sizeof(observed))!=CUDA_SUCCESS || std::memcmp(&observed,&counters,sizeof(counters)))
        return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    const std::uint32_t on=1;
    auto expected=disabled;expected.enabled=1;
    if(put(config_address+offsetof(ModuleConfig,enabled),&on,sizeof(on))!=CUDA_SUCCESS ||
        read(&copied,config_address,sizeof(copied))!=CUDA_SUCCESS || !valid_trace_span(copied) ||
        std::memcmp(&copied,&expected,sizeof(copied)))
        return rollback() ? hbfsim::FutureInitialization::Unavailable : hbfsim::FutureInitialization::Quarantine;
    { std::lock_guard lock(future_budget_mutex);future_budgets.insert_or_assign(raw_module,FutureBudget{alias,generation,kTraceCapacity}); }
    return hbfsim::FutureInitialization::Ready;
}

void erase_context_state(std::uintptr_t cuda_context) noexcept
{
    access_erase_context(cuda_context);
    timing_bindings().erase_context(cuda_context, erase_module_identity,
                                    nullptr);
}

void erase_unbound_device_state(int device_ordinal) noexcept
{
    access_erase_device(device_ordinal);
    timing_bindings().erase_unbound_device(
        device_ordinal, erase_module_identity, nullptr);
}

bool timing_binding_ready(CUfunction function) noexcept
{
    function = canonical_function(function);
    using get_module_type = CUresult (*)(CUmodule*, CUfunction);
    static auto get_module =
        reinterpret_cast<get_module_type>(driver_symbol("cuFuncGetModule"));
    CUmodule module = nullptr;
    const auto domain = current_cuda_domain();
    return get_module != nullptr && domain.has_value() &&
           get_module(&module, function) == CUDA_SUCCESS && module != nullptr &&
           timing_bindings().ready_for_active(
               module_handle(module), domain->context, domain->device);
}

hbfsim::GateDecision require_timing_binding(hbfsim::GateDecision decision,
                                            CUfunction function)
{
    using get_module_type=CUresult (*)(CUmodule*,CUfunction);
    static auto get_module=reinterpret_cast<get_module_type>(driver_symbol("cuFuncGetModule"));
    CUmodule module=nullptr;
    const bool known=function && get_module && get_module(&module,function)==CUDA_SUCCESS && module;
    if ((known && timing_bindings().future_module(module_handle(module))) ||
        (!known && timing_bindings().future_unit_observed())) {
        decision.allowed=false;
        decision.reason="timing_future_unit_incomplete";
        if(!hbfsim::timing_future::kUnitComplete)return decision;
        decision.reason="timing_future_binding_unavailable";
        if(!known || !future_geometry || !timing_binding_ready(function))return decision;
        auto& runtime=runtime_gate();runtime.refresh_manifests();
        const auto id=handle_id(function);const auto kernel=function_name(function);
        auto manifest=runtime.gate().manifest(id,kernel);
        if(!manifest || !manifest->future_requirements || !runtime.future_manifests_valid() ||
            !runtime.future_manifest_present(*manifest))return decision;
        if(future_manifest_match(module,*manifest)!=FutureManifestMatch::Matched)return decision;
        decision=runtime.gate().check_launch({.module_id=id,.kernel=kernel,
            .grid=future_geometry->grid,.block=future_geometry->block});
        if(!decision.allowed)return decision;
        std::uint64_t records=hbfsim::timing_future::kMaximumRecordsPerProducer*manifest->future_kernel->static_producers;
        for(auto axis:future_geometry->grid)records*=axis;
        for(auto axis:future_geometry->block)records*=axis;
        std::lock_guard lock(future_budget_mutex);
        auto budget=future_budgets.find(module_handle(module));
        if(budget==future_budgets.end() || records>budget->second.remaining) {
            decision.allowed=false;decision.reason="timing_future_trace_budget_exhausted";
        } else {
            // Reservations are serialized and never returned after an enqueue
            // error: lack of execution has not been proved.
            budget->second.remaining-=records;
        }
        return decision;
    }
    if (decision.allowed &&
        (decision.requires_instrumented_execution ||
         (decision.modeled && decision.address != 0)) &&
        !timing_binding_ready(function)) {
        decision.allowed = false;
        decision.reason = "control_binding_unavailable";
    }
    return decision;
}

struct RetireToken {
    std::uintptr_t owner;
    std::uint64_t generation;
    std::unique_lock<std::shared_mutex> launch_lock;
    bool invalidated{false};
};

int activate_timing_owner_with_capabilities(std::uintptr_t owner,
                          std::uintptr_t control_alias,
                          std::uintptr_t cuda_context, int device_ordinal,
                          const hbfsim::timing_future::Capabilities* capabilities,
                          std::uint64_t* generation_out) noexcept
{
    auto transition = activation_transition_lock();
    if (generation_out == nullptr) {
        return -1;
    }
    *generation_out=0;
    if (!capabilities || !hbfsim::timing_future::valid_capabilities(*capabilities)) return -1;
    const auto live_domain = current_cuda_domain();
    if (!live_domain.has_value() || live_domain->context != cuda_context ||
        live_domain->device != device_ordinal) {
        *generation_out = 0;
        return -1;
    }
    if (!timing_bindings().can_activate()) {
        *generation_out = 0;
        return -1;
    }
    auto range_transition = runtime_gate().activation_guard();
    std::uint64_t generation = 0;
    if (!timing_bindings().activate_with_capabilities(owner, control_alias, cuda_context,
                                    device_ordinal,*capabilities,
                                    initialize_module_control, nullptr,
                                    generation)) {
        *generation_out = generation;
        return -1;
    }
    runtime_gate().finish_activation();
    *generation_out = generation;
    return 0;
}

int activate_timing_owner(std::uintptr_t owner,std::uintptr_t control_alias,
    std::uintptr_t cuda_context,int device_ordinal,std::uint64_t* generation_out) noexcept
{
    const hbfsim::timing_future::Capabilities legacy{};
    return activate_timing_owner_with_capabilities(owner,control_alias,cuda_context,
        device_ordinal,&legacy,generation_out);
}

int register_range(std::uintptr_t owner, std::uint64_t generation,
                   std::uintptr_t begin, std::uintptr_t end,
                   hbfsim::LaunchGatePublishRange publish,
                   void* publish_state) noexcept
{
    if (owner == 0 || generation == 0 || begin >= end) {
        return -1;
    }
    return runtime_gate().register_range(
        owner, generation, begin, end, hbfsim::RangePolicy::LegacyStrict,
        publish, publish_state);
}

int register_range_with_policy(
    std::uintptr_t owner, std::uint64_t generation, std::uintptr_t begin,
    std::uintptr_t end, hbfsim::LaunchGateRangePolicy policy,
    hbfsim::LaunchGatePublishRange publish, void* publish_state) noexcept
{
    if (owner == 0 || generation == 0 || begin >= end) {
        return -1;
    }
    hbfsim::RangePolicy coverage_policy;
    switch (policy) {
    case hbfsim::LaunchGateRangePolicy::LegacyStrict:
        coverage_policy = hbfsim::RangePolicy::LegacyStrict;
        break;
    case hbfsim::LaunchGateRangePolicy::TimingBacked:
        coverage_policy = hbfsim::RangePolicy::TimingBacked;
        break;
    case hbfsim::LaunchGateRangePolicy::CapacityUnbacked:
        coverage_policy = hbfsim::RangePolicy::CapacityUnbacked;
        break;
    default:
        return -1;
    }
    return runtime_gate().register_range(owner, generation, begin, end,
                                         coverage_policy, publish,
                                         publish_state);
}

int unregister_range(std::uintptr_t owner, std::uint64_t generation,
                     std::uintptr_t begin, std::uintptr_t end,
                     hbfsim::LaunchGatePublishRange publish,
                     void* publish_state) noexcept
{
    if (owner == 0 || generation == 0 || begin >= end) {
        return -1;
    }
    return runtime_gate().unregister_range(
        owner, generation, begin, end, publish, publish_state);
}

int begin_retire(std::uintptr_t owner, std::uint64_t generation,
                 std::uintptr_t* token_out) noexcept
{
    if (owner == 0 || generation == 0 || token_out == nullptr) {
        return -1;
    }
    *token_out = 0;
    try {
        auto token = std::make_unique<RetireToken>(RetireToken{
            .owner = owner,
            .generation = generation,
            .launch_lock = runtime_gate().retirement_guard(),
        });
        if (!timing_bindings().quiesce(owner, generation)) {
            return -1;
        }
        *token_out = reinterpret_cast<std::uintptr_t>(token.release());
        return 0;
    } catch (...) {
        return -1;
    }
}

int invalidate_retire(std::uintptr_t raw_token) noexcept
{
    if (raw_token == 0) {
        return -1;
    }
    auto* token = reinterpret_cast<RetireToken*>(raw_token);
    if (token->invalidated) {
        return 0;
    }
    if(timing_bindings().has_future_modules()) {
        const auto domain=current_cuda_domain();
        using sync_type=CUresult (*)();
        const auto sync=reinterpret_cast<sync_type>(driver_symbol("cuCtxSynchronize"));
        if(!domain || !timing_bindings().active_domain(domain->context,domain->device) ||
            !sync || sync()!=CUDA_SUCCESS)return -1;
    }
    if (!timing_bindings().invalidate(token->owner, token->generation,
                                      initialize_module_control, nullptr)) {
        return -1;
    }
    token->invalidated = true;
    return 0;
}

int finish_retire(std::uintptr_t raw_token) noexcept
{
    if (raw_token == 0) {
        return -1;
    }
    auto* token = reinterpret_cast<RetireToken*>(raw_token);
    if (!token->invalidated ||
        !timing_bindings().finish_retire(token->owner, token->generation)) {
        return -1;
    }
    runtime_gate().finish_retirement();
    delete token;
    return 0;
}

int quarantine_retire(std::uintptr_t raw_token) noexcept
{
    if (raw_token == 0) {
        return -1;
    }
    // Quiesce already made the binding permanently not-ready. Keep that
    // state and all registered ranges, but release the exclusive lock so future
    // relevant launches can acquire a shared guard and fail closed.
    delete reinterpret_cast<RetireToken*>(raw_token);
    return 0;
}

const hbfsim::LaunchGateApiV2 launch_gate_api_v2{
    .abi_version = hbfsim::kLaunchGateAbiVersionV2,
    .struct_bytes = sizeof(hbfsim::LaunchGateApiV2),
    .activate = activate_timing_owner,
    .register_range = register_range,
    .unregister_range = unregister_range,
    .begin_retire = begin_retire,
    .invalidate_retire = invalidate_retire,
    .finish_retire = finish_retire,
    .quarantine_retire = quarantine_retire,
};

const hbfsim::LaunchGateApiV3 launch_gate_api_v3{
    .abi_version = hbfsim::kLaunchGateAbiVersion,
    .struct_bytes = sizeof(hbfsim::LaunchGateApiV3),
    .activate = activate_timing_owner,
    .register_range = register_range,
    .unregister_range = unregister_range,
    .begin_retire = begin_retire,
    .invalidate_retire = invalidate_retire,
    .finish_retire = finish_retire,
    .quarantine_retire = quarantine_retire,
    .register_range_with_policy = register_range_with_policy,
};

const hbfsim::LaunchGateApiV4 launch_gate_api_v4{
    .abi_version=hbfsim::kLaunchGateAbiVersionV4,.struct_bytes=sizeof(hbfsim::LaunchGateApiV4),
    .activate=activate_timing_owner,.register_range=register_range,
    .unregister_range=unregister_range,.begin_retire=begin_retire,
    .invalidate_retire=invalidate_retire,.finish_retire=finish_retire,
    .quarantine_retire=quarantine_retire,.register_range_with_policy=register_range_with_policy,
    .activate_with_capabilities=activate_timing_owner_with_capabilities};

std::string handle_id(CUfunction function)
{
    function = canonical_function(function);
    using get_module_type = CUresult (*)(CUmodule*, CUfunction);
    static auto get_module =
        reinterpret_cast<get_module_type>(driver_symbol("cuFuncGetModule"));
    CUmodule module = nullptr;
    if (get_module == nullptr ||
        get_module(&module, function) != CUDA_SUCCESS) {
        return {};
    }
    const auto identity = module_identities().lookup(module_handle(module));
    return identity.has_value() ? hbfsim::module_id_from_identity(*identity)
                                : std::string{};
}

std::string function_name(CUfunction function)
{
    using get_name_type = CUresult (*)(const char**, CUfunction);
    static auto get_name =
        reinterpret_cast<get_name_type>(driver_symbol("cuFuncGetName"));
    const char* name = nullptr;
    return get_name != nullptr && get_name(&name, function) == CUDA_SUCCESS &&
                   name != nullptr
               ? name
               : "unknown_cufunction";
}

CUfunction kernel_function(cudaKernel_t kernel)
{
    using get_function_type = CUresult (*)(CUfunction*, CUkernel);
    static auto get_function = reinterpret_cast<get_function_type>(
        driver_symbol("cuKernelGetFunction"));
    CUfunction function = nullptr;
    if (get_function != nullptr) {
        get_function(&function, reinterpret_cast<CUkernel>(kernel));
    }
    return function;
}

hbfsim::GateDecision unavailable(std::string module_id, std::string kernel)
{
    auto& gate = runtime_gate().gate();
    if (!gate.has_ranges()) {
        return {.allowed = true,
                .module_id = std::move(module_id),
                .kernel = std::move(kernel)};
    }
    if (!gate.has_strict_ranges()) {
        auto decision = hbfsim::uninspectable_launch_decision(
            true, false, "opaque_pointer_access");
        decision.module_id = std::move(module_id);
        decision.kernel = std::move(kernel);
        return decision;
    }
    return {.allowed = false,
            .module_id = std::move(module_id),
            .kernel = std::move(kernel),
            .reason = "launch_metadata_unavailable",
            .operation = "opaque_pointer_access",
            .range_policy = hbfsim::RangePolicy::LegacyStrict};
}

template <typename Handle, typename GetInfo, typename ReadBytes>
hbfsim::GateDecision
inspect_bounded(Handle handle, std::string runtime_module_id,
                std::string kernel, GetInfo get_info, ReadBytes read_bytes)
{
    auto& runtime = runtime_gate();
    runtime.refresh_manifests();
    if (get_info == nullptr) {
        return runtime.gate().has_ranges()
                   ? unavailable(std::move(runtime_module_id),
                                 std::move(kernel))
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id =
                                              std::move(runtime_module_id),
                                          .kernel = std::move(kernel)};
    }

    hbfsim::KernelLaunch launch{.module_id = runtime_module_id,
                                .kernel = kernel};
    const auto manifest = runtime.gate().manifest(runtime_module_id, kernel);
    const auto parameter_metadata = [&](std::size_t index,
                                        std::size_t offset,
                                        std::size_t width)
        -> const hbfsim::ParameterMetadata* {
        if (!manifest.has_value()) return nullptr;
        const auto found = std::ranges::find_if(
            manifest->parameters, [&](const hbfsim::ParameterMetadata& value) {
                return value.index == index && value.offset == offset &&
                       value.width == width;
            });
        return found == manifest->parameters.end() ? nullptr : &*found;
    };
    for (std::size_t index = 0;; ++index) {
        std::size_t offset = 0;
        std::size_t width = 0;
        const auto status = get_info(handle, index, &offset, &width);
        if (status == CUDA_ERROR_INVALID_VALUE) {
            break;
        }
        if (status != CUDA_SUCCESS) {
            return runtime.gate().has_ranges()
                       ? unavailable(std::move(runtime_module_id),
                                     std::move(kernel))
                       : hbfsim::GateDecision{.allowed = true,
                                              .module_id =
                                                  std::move(runtime_module_id),
                                              .kernel = std::move(kernel)};
        }
        hbfsim::LaunchParameter parameter{
            .index = index,
            .offset = offset,
            .width = width,
            .opaque_aggregate = width > sizeof(std::uintptr_t),
        };
        if (width == sizeof(std::uintptr_t)) {
            std::uintptr_t value = 0;
            if (read_bytes(index, offset, 0, &value, sizeof(value))) {
                parameter.slots.push_back({.offset = 0, .value = value});
            }
        } else if (const auto* metadata =
                       parameter_metadata(index, offset, width);
                   metadata != nullptr &&
                   metadata->kind == hbfsim::ParameterKind::OpaqueAggregate &&
                   metadata->fields_complete &&
                   metadata->opaque_reason.empty()) {
            for (const auto& field : metadata->pointer_fields) {
                if (field.width != sizeof(std::uintptr_t) ||
                    field.byte_offset > width ||
                    field.width > width - field.byte_offset) {
                    parameter.slots.clear();
                    break;
                }
                std::uintptr_t value = 0;
                if (!read_bytes(index, offset, field.byte_offset, &value,
                                sizeof(value))) {
                    parameter.slots.clear();
                    break;
                }
                parameter.slots.push_back(
                    {.offset = field.byte_offset, .value = value});
            }
        }
        launch.parameters.push_back(std::move(parameter));
    }
    auto decision = runtime.gate().check_launch(launch);
    if (decision.module_id.empty()) {
        decision.module_id = std::move(runtime_module_id);
    }
    return decision;
}

bool packed_launch_buffer(void** extra, const std::byte** buffer,
                          std::size_t* bytes)
{
    if (extra == nullptr || buffer == nullptr || bytes == nullptr) return false;
    const void* raw_buffer = nullptr;
    const std::size_t* raw_size = nullptr;
    bool ended = false;
    for (std::size_t cursor = 0; cursor < 32; cursor += 2) {
        void* key = extra[cursor];
        if (key == CU_LAUNCH_PARAM_END) {
            ended = true;
            break;
        }
        if (key == CU_LAUNCH_PARAM_BUFFER_POINTER) {
            if (raw_buffer != nullptr || extra[cursor + 1] == nullptr)
                return false;
            raw_buffer = extra[cursor + 1];
        } else if (key == CU_LAUNCH_PARAM_BUFFER_SIZE) {
            if (raw_size != nullptr || extra[cursor + 1] == nullptr)
                return false;
            raw_size = static_cast<const std::size_t*>(extra[cursor + 1]);
        } else {
            return false;
        }
    }
    if (!ended || raw_buffer == nullptr || raw_size == nullptr) return false;
    *buffer = static_cast<const std::byte*>(raw_buffer);
    *bytes = *raw_size;
    return true;
}

bool read_packed_bytes(const std::byte* buffer, std::size_t buffer_bytes,
                       std::size_t parameter_offset,
                       std::size_t field_offset, void* destination,
                       std::size_t bytes)
{
    if (buffer == nullptr || destination == nullptr ||
        parameter_offset > buffer_bytes ||
        field_offset > buffer_bytes - parameter_offset ||
        bytes > buffer_bytes - parameter_offset - field_offset)
        return false;
    std::memcpy(destination, buffer + parameter_offset + field_offset, bytes);
    return true;
}

bool read_argument_bytes(void** arguments, std::size_t index,
                         std::size_t field_offset, void* destination,
                         std::size_t bytes)
{
    if (arguments == nullptr || arguments[index] == nullptr ||
        destination == nullptr)
        return false;
    std::memcpy(destination,
                static_cast<const std::byte*>(arguments[index]) + field_offset,
                bytes);
    return true;
}

hbfsim::GateDecision inspect_function_launch(CUfunction function,
                                             void** arguments, void** extra)
{
    const auto module_id = handle_id(function);
    const auto kernel = function_name(function);
    using get_info_type =
        CUresult (*)(CUfunction, std::size_t, std::size_t*, std::size_t*);
    static auto get_info =
        reinterpret_cast<get_info_type>(driver_symbol("cuFuncGetParamInfo"));
    if (extra != nullptr) {
        const std::byte* buffer = nullptr;
        std::size_t buffer_bytes = 0;
        if (!packed_launch_buffer(extra, &buffer, &buffer_bytes)) {
            const auto saved = future_geometry;
            future_geometry.reset();
            const auto result = require_timing_binding(
                runtime_gate().gate().has_ranges()
                    ? unavailable(module_id, kernel)
                    : hbfsim::GateDecision{.allowed = true,
                                           .module_id = module_id,
                                           .kernel = kernel},
                function);
            future_geometry = saved;
            return result;
        }
        const auto read_packed =
            [buffer, buffer_bytes](std::size_t, std::size_t parameter_offset,
                                   std::size_t field_offset, void* destination,
                                   std::size_t bytes) {
                return read_packed_bytes(buffer, buffer_bytes,
                                         parameter_offset, field_offset,
                                         destination, bytes);
            };
        return require_timing_binding(
            inspect_bounded(function, module_id, kernel, get_info, read_packed),
            function);
    }
    if (arguments == nullptr) {
        return require_timing_binding(
            runtime_gate().gate().has_ranges()
                ? unavailable(module_id, kernel)
                : hbfsim::GateDecision{.allowed = true,
                                       .module_id = module_id,
                                       .kernel = kernel},
            function);
    }
    const auto read_arguments =
        [arguments](std::size_t index, std::size_t, std::size_t field_offset,
                    void* destination, std::size_t bytes) {
            return read_argument_bytes(arguments, index, field_offset,
                                       destination, bytes);
        };
    return require_timing_binding(
        inspect_bounded(function, module_id, kernel, get_info, read_arguments),
        function);
}

hbfsim::GateDecision inspect_kernel_launch(cudaKernel_t kernel,
                                           void** arguments,
                                           CUfunction* resolved = nullptr)
{
    const CUfunction function = kernel_function(kernel);
    if (resolved != nullptr) {
        *resolved = function;
    }
    if (function != nullptr) {
        return inspect_function_launch(function, arguments, nullptr);
    }
    using get_name_type = CUresult (*)(const char**, CUkernel);
    using get_info_type =
        CUresult (*)(CUkernel, std::size_t, std::size_t*, std::size_t*);
    static auto get_name =
        reinterpret_cast<get_name_type>(driver_symbol("cuKernelGetName"));
    static auto get_info =
        reinterpret_cast<get_info_type>(driver_symbol("cuKernelGetParamInfo"));
    const char* raw_name = nullptr;
    const std::string name =
        get_name != nullptr &&
                get_name(&raw_name, reinterpret_cast<CUkernel>(kernel)) ==
                    CUDA_SUCCESS &&
                raw_name != nullptr
            ? raw_name
            : "unknown_cukernel";
    return require_timing_binding(
        inspect_bounded(reinterpret_cast<CUkernel>(kernel),
                        "cuda-module:unknown", name, get_info,
                        [arguments](std::size_t index, std::size_t,
                                    std::size_t field_offset, void* destination,
                                    std::size_t bytes) {
                            return read_argument_bytes(
                                arguments, index, field_offset, destination,
                                bytes);
                        }),
        function);
}

enum class RuntimeDomain { Cudart12, Cudart13 };

const char* runtime_version(RuntimeDomain domain) noexcept
{
    return domain == RuntimeDomain::Cudart12 ? "libcudart.so.12"
                                              : "libcudart.so.13";
}

const char* runtime_soname(RuntimeDomain domain) noexcept
{
    return runtime_version(domain);
}

using libc_dlvsym_type = void* (*)(void*, const char*, const char*);

libc_dlvsym_type real_dlvsym() noexcept
{
    static auto function = reinterpret_cast<libc_dlvsym_type>(
        dlvsym(RTLD_NEXT, "dlvsym", "GLIBC_2.2.5"));
    return function;
}

struct RuntimeLibrary {
    void* handle{nullptr};
};

const RuntimeLibrary& runtime_library(RuntimeDomain domain) noexcept
{
    if (domain == RuntimeDomain::Cudart12) {
        static const RuntimeLibrary cudart12{
            dlopen("libcudart.so.12", RTLD_NOW | RTLD_LOCAL)};
        return cudart12;
    }
    static const RuntimeLibrary cudart13{
        dlopen("libcudart.so.13", RTLD_NOW | RTLD_LOCAL)};
    return cudart13;
}

void* runtime_symbol(RuntimeDomain domain, const char* symbol) noexcept
{
    auto lookup = real_dlvsym();
    const auto& library = runtime_library(domain);
    return lookup == nullptr || library.handle == nullptr || symbol == nullptr
               ? nullptr
               : lookup(library.handle, symbol, runtime_version(domain));
}

using get_runtime_function_type =
    cudaError_t (*)(cudaFunction_t*, const void*);

get_runtime_function_type runtime_get_function_symbol(RuntimeDomain domain)
{
    return reinterpret_cast<get_runtime_function_type>(
        runtime_symbol(domain, "cudaGetFuncBySymbol"));
}

hbfsim::GateDecision inspect_symbol_launch(RuntimeDomain domain,
                                           const void* symbol,
                                           void** arguments,
                                           CUfunction* resolved = nullptr)
{
    auto get_function = runtime_get_function_symbol(domain);
    cudaFunction_t runtime_function = nullptr;
    if (resolved != nullptr) {
        *resolved = nullptr;
    }
    if (get_function == nullptr ||
        get_function(&runtime_function, symbol) != cudaSuccess ||
        runtime_function == nullptr) {
        return require_timing_binding(runtime_gate().gate().has_ranges()
                   ? unavailable("cuda-module:unknown",
                                 "unknown_runtime_kernel")
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id = "cuda-module:unknown",
                                          .kernel = "unknown_runtime_kernel"}, nullptr);
    }
    if (resolved != nullptr) {
        *resolved = reinterpret_cast<CUfunction>(runtime_function);
    }
    return inspect_function_launch(
        reinterpret_cast<CUfunction>(runtime_function), arguments, nullptr);
}

thread_local bool runtime_launch_in_progress = false;
thread_local int runtime_launch_approval = 1;
thread_local CUfunction runtime_launch_expected_function = nullptr;

int approval_code(const hbfsim::GateDecision& decision) noexcept
{
    return (decision.modeled || decision.requires_instrumented_execution) ? 2
                                                                          : 1;
}

struct RuntimeLaunchScope {
    explicit RuntimeLaunchScope(int approval = 1,
                                CUfunction expected_function = nullptr)
        : previous_in_progress(runtime_launch_in_progress),
          previous_approval(runtime_launch_approval),
          previous_expected_function(runtime_launch_expected_function)
    {
        runtime_launch_in_progress = true;
        runtime_launch_approval = approval;
        runtime_launch_expected_function = expected_function;
    }
    ~RuntimeLaunchScope()
    {
        runtime_launch_approval = previous_approval;
        runtime_launch_expected_function = previous_expected_function;
        runtime_launch_in_progress = previous_in_progress;
    }

    bool previous_in_progress;
    int previous_approval;
    CUfunction previous_expected_function;
};

// Same-thread, single-consume receipt for the strict approval=1 path.  The
// agent starts a frame immediately before entering this gate.  Only the gate
// may complete it, after the real cudart call returns.  A stack preserves
// nested launches without allowing a child to consume its parent receipt.
struct StrictOriginalCompletion {
    std::uint64_t token = 0;
    const void* function = nullptr;
    int domain = 0;
    int api_kind = 0;
    int decision = 0;
    int runtime_result = static_cast<int>(cudaErrorUnknown);
    bool completed = false;
};
thread_local std::vector<StrictOriginalCompletion> strict_original_completions;
std::atomic<std::uint64_t> strict_original_next_token{1};

extern "C" std::uint64_t hbfsim_strict_original_begin_v1(
    const void* function, int domain, int api_kind) noexcept
{
    if (function == nullptr || (domain != 12 && domain != 13) ||
        (api_kind != 1 && api_kind != 2)) return 0;
    const auto token = strict_original_next_token.fetch_add(
        1, std::memory_order_relaxed);
    strict_original_completions.push_back(
        {.token=token,.function=function,.domain=domain,.api_kind=api_kind});
    return token;
}

void complete_strict_original(const void* function, int domain, int api_kind,
                              int decision, cudaError_t result) noexcept
{
    if (strict_original_completions.empty()) return;
    auto& frame = strict_original_completions.back();
    if (frame.function != function || frame.domain != domain ||
        frame.api_kind != api_kind || frame.completed) return;
    frame.decision = decision;
    frame.runtime_result = static_cast<int>(result);
    frame.completed = true;
}

extern "C" int hbfsim_strict_original_consume_v1(
    std::uint64_t token, const void* function, int domain, int api_kind,
    int observed_result, int* decision, int* gate_result) noexcept
{
    if (strict_original_completions.empty()) return 0;
    auto frame = strict_original_completions.back();
    if (frame.token != token) return 0;
    strict_original_completions.pop_back();
    if (!frame.completed || frame.function != function ||
        frame.domain != domain || frame.api_kind != api_kind ||
        frame.decision != 1 || frame.runtime_result != observed_result ||
        decision == nullptr || gate_result == nullptr) return 0;
    *decision = frame.decision;
    *gate_result = frame.runtime_result;
    return 1;
}

bool approve(const hbfsim::GateDecision& decision)
{
    if (!runtime_gate().approve(decision)) {
        return false;
    }
    runtime_gate().mark_launch_seen();
    return true;
}

struct LaunchDiagnostic {
    const char* wrapper;
    const void* original;
    const void* lookup;
    const void* function;
    unsigned int grid_x, grid_y, grid_z;
    unsigned int block_x, block_y, block_z;
    unsigned int shared_memory;
    const void* stream;
    void** parameters;
    void** extra;
    const void* config;
    const void* attrs;
    unsigned int num_attrs;
    long long result;
};

bool launch_diagnostics_enabled() noexcept
{
    static const bool enabled = [] {
        const char* value = std::getenv("HBFSIM_LAUNCH_DIAGNOSTICS");
        return value != nullptr && value[0] != '\0' &&
               std::strcmp(value, "0") != 0;
    }();
    return enabled;
}

void emit_launch_diagnostic(const LaunchDiagnostic& record) noexcept
{
    static std::atomic<unsigned int> count{0};
    thread_local bool emitting = false;
    if (!launch_diagnostics_enabled() || emitting) {
        return;
    }
    const unsigned int index = count.fetch_add(1, std::memory_order_relaxed);
    if (index >= 64) {
        return;
    }
    emitting = true;
    Dl_info original_info{};
    Dl_info lookup_info{};
    const char* original_dso =
        record.original != nullptr &&
                dladdr(record.original, &original_info) != 0 &&
                original_info.dli_fname != nullptr
            ? original_info.dli_fname
            : "";
    const char* lookup_dso =
        record.lookup != nullptr && dladdr(record.lookup, &lookup_info) != 0 &&
                lookup_info.dli_fname != nullptr
            ? lookup_info.dli_fname
            : "";
    const auto domain = current_cuda_domain();
    const std::uintptr_t context = domain ? domain->context : 0;
    const int device = domain ? domain->device : -1;
    char line[2048];
    const int length = std::snprintf(
        line, sizeof(line),
        "{\"hbfsim_launch_diag\":1,\"index\":%u,"
        "\"wrapper\":\"%s\",\"original\":\"%p\","
        "\"original_dso\":\"%s\",\"lookup\":\"%p\","
        "\"lookup_dso\":\"%s\",\"context\":\"0x%llx\","
        "\"device\":%d,\"function\":\"%p\","
        "\"grid\":[%u,%u,%u],\"block\":[%u,%u,%u],"
        "\"shared\":%u,\"stream\":\"%p\","
        "\"parameters\":\"%p\",\"extra\":\"%p\","
        "\"config\":\"%p\",\"attrs\":\"%p\","
        "\"num_attrs\":%u,\"result\":%lld}\n",
        index, record.wrapper, record.original, original_dso, record.lookup,
        lookup_dso, static_cast<unsigned long long>(context), device,
        record.function, record.grid_x, record.grid_y, record.grid_z,
        record.block_x, record.block_y, record.block_z, record.shared_memory,
        record.stream, static_cast<void*>(record.parameters),
        static_cast<void*>(record.extra), record.config, record.attrs,
        record.num_attrs, record.result);
    if (length > 0) {
        const std::size_t bytes =
            static_cast<std::size_t>(length) < sizeof(line)
                ? static_cast<std::size_t>(length)
                : sizeof(line) - 1;
        (void)::write(STDERR_FILENO, line, bytes);
    }
    emitting = false;
}

std::string diagnostic_json_escape(const char* value)
{
    std::ostringstream out;
    const unsigned char* cursor = reinterpret_cast<const unsigned char*>(
        value != nullptr ? value : "");
    for (; *cursor != 0; ++cursor) {
        switch (*cursor) {
        case '\\': out << "\\\\"; break;
        case '"': out << "\\\""; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (*cursor < 0x20) {
                out << "\\u" << std::hex << std::setw(4)
                    << std::setfill('0') << static_cast<unsigned>(*cursor)
                    << std::dec << std::setfill(' ');
            } else {
                out << static_cast<char>(*cursor);
            }
        }
    }
    return out.str();
}

[[noreturn]] void strict_denial_stop(
    const hbfsim::GateDecision& decision, const char* api, const char* domain,
    const char* version, const void* original, const void* lookup,
    const void* function) noexcept
{
    static std::mutex writer_mutex;
    std::lock_guard lock(writer_mutex);
    const char* path = std::getenv("HBFSIM_STRICT_STOP_ON_DENIAL_PATH");
    if (path == nullptr || path[0] == '\0') std::_Exit(86);
    const int fd = ::open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (fd >= 0) {
        Dl_info original_info{};
        Dl_info lookup_info{};
        const char* original_dso =
            original != nullptr && ::dladdr(original, &original_info) != 0 &&
                    original_info.dli_fname != nullptr
                ? original_info.dli_fname : "";
        const char* lookup_dso =
            lookup != nullptr && ::dladdr(lookup, &lookup_info) != 0 &&
                    lookup_info.dli_fname != nullptr
                ? lookup_info.dli_fname : "";
        std::string maps_path(path);
        maps_path += ".maps";
        const int maps_out = ::open(maps_path.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
        if (maps_out >= 0) {
            const int maps_in = ::open("/proc/self/maps", O_RDONLY | O_CLOEXEC);
            if (maps_in >= 0) {
                char buffer[16384];
                for (;;) {
                    const ssize_t count = ::read(maps_in, buffer, sizeof(buffer));
                    if (count <= 0) break;
                    ssize_t written = 0;
                    while (written < count) {
                        const ssize_t step = ::write(maps_out, buffer + written,
                            static_cast<std::size_t>(count - written));
                        if (step <= 0) break;
                        written += step;
                    }
                    if (written != count) break;
                }
                ::close(maps_in);
            }
            (void)::fsync(maps_out);
            ::close(maps_out);
        }
        std::ostringstream json;
        json << "{\"schema_version\":1,\"event\":\"strict_first_denial\""
             << ",\"api\":\"" << diagnostic_json_escape(api) << "\""
             << ",\"domain\":\"" << diagnostic_json_escape(domain) << "\""
             << ",\"symbol_version\":\"" << diagnostic_json_escape(version) << "\""
             << ",\"reason\":\"" << diagnostic_json_escape(decision.reason.c_str()) << "\""
             << ",\"module_id\":\"" << diagnostic_json_escape(decision.module_id.c_str()) << "\""
             << ",\"kernel\":\"" << diagnostic_json_escape(decision.kernel.c_str()) << "\""
             << ",\"original_function\":\"0x" << std::hex
             << reinterpret_cast<std::uintptr_t>(original) << "\""
             << ",\"lookup_function\":\"0x"
             << reinterpret_cast<std::uintptr_t>(lookup) << "\""
             << ",\"launch_function\":\"0x"
             << reinterpret_cast<std::uintptr_t>(function) << "\"" << std::dec
             << ",\"original_dso\":\"" << diagnostic_json_escape(original_dso) << "\""
             << ",\"lookup_dso\":\"" << diagnostic_json_escape(lookup_dso) << "\""
             << ",\"maps_path\":\"" << diagnostic_json_escape(maps_path.c_str()) << "\""
             << ",\"exit_code\":86}\n";
        const std::string payload = json.str();
        std::size_t written = 0;
        while (written < payload.size()) {
            const ssize_t step = ::write(fd, payload.data() + written,
                                         payload.size() - written);
            if (step <= 0) break;
            written += static_cast<std::size_t>(step);
        }
        (void)::fsync(fd);
        ::close(fd);
    }
    std::_Exit(86);
}

void strict_denial_stop_if_enabled(
    const hbfsim::GateDecision& decision, const char* api, const char* domain,
    const char* version, const void* original, const void* lookup,
    const void* function) noexcept
{
    const char* path = std::getenv("HBFSIM_STRICT_STOP_ON_DENIAL_PATH");
    if (path && path[0] != '\0')
        strict_denial_stop(decision, api, domain, version, original, lookup, function);
}

using driver_launch_type = CUresult (*)(CUfunction, unsigned int, unsigned int,
                                        unsigned int, unsigned int,
                                        unsigned int, unsigned int,
                                        unsigned int, CUstream, void**, void**);

CUresult driver_launch(const char* symbol, CUfunction function,
                       unsigned int grid_x, unsigned int grid_y,
                       unsigned int grid_z, unsigned int block_x,
                       unsigned int block_y, unsigned int block_z,
                       unsigned int shared_memory, CUstream stream,
                       void** parameters, void** extra)
{
    auto original =
        reinterpret_cast<driver_launch_type>(dlsym(RTLD_NEXT, symbol));
    const auto finish = [&](CUresult result) {
        emit_launch_diagnostic({
            .wrapper = symbol,
            .original = reinterpret_cast<const void*>(original),
            .function = reinterpret_cast<const void*>(function),
            .grid_x = grid_x, .grid_y = grid_y, .grid_z = grid_z,
            .block_x = block_x, .block_y = block_y, .block_z = block_z,
            .shared_memory = shared_memory,
            .stream = reinterpret_cast<const void*>(stream),
            .parameters = parameters, .extra = extra,
            .result = static_cast<long long>(result),
        });
        return result;
    };
    if (original == nullptr) {
        return finish(CUDA_ERROR_NOT_INITIALIZED);
    }
    if (runtime_launch_in_progress) {
        return finish(original(function, grid_x, grid_y, grid_z, block_x,
                               block_y, block_z, shared_memory, stream,
                               parameters, extra));
    }
    auto launch_guard = runtime_gate().launch_guard();
    FutureGeometryScope geometry(
        {{grid_x, grid_y, grid_z}, {block_x, block_y, block_z}});
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        strict_denial_stop_if_enabled(decision, symbol, "driver",
            "libcuda.so.1", reinterpret_cast<const void*>(original), nullptr,
            reinterpret_cast<const void*>(function));
        return finish(CUDA_ERROR_NOT_SUPPORTED);
    }
    RuntimeLaunchScope scope(approval_code(decision), function);
    return finish(original(function, grid_x, grid_y, grid_z, block_x, block_y,
                           block_z, shared_memory, stream, parameters, extra));
}

using runtime_launch_type = cudaError_t (*)(const void*, dim3, dim3, void**,
                                            std::size_t, cudaStream_t);

using strict_direct_driver_launch_type = CUresult (*)(
    CUfunction, unsigned int, unsigned int, unsigned int, unsigned int,
    unsigned int, unsigned int, unsigned int, CUstream, void**, void**);

cudaError_t runtime_error_from_driver(CUresult result) noexcept;
bool strict_instrumentation_requested() noexcept;

std::uint64_t strict_bridge_capabilities() noexcept
{
    using getter_type = std::uint64_t (*)();
    auto getter = reinterpret_cast<getter_type>(
        dlsym(RTLD_DEFAULT, "bpftime_nv_strict_bridge_capabilities_v1"));
    return getter != nullptr ? getter() : 0;
}

std::string observed_m1_moe_dso_sha256(const char* path)
{
    static const std::string observed_sha = [path] {
        std::ifstream file(path, std::ios::binary);
        if (!file) return std::string{};
        std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(
            EVP_MD_CTX_new(), EVP_MD_CTX_free);
        if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1)
            return std::string{};
        std::array<char, 1 << 16> buffer{};
        while (file) {
            file.read(buffer.data(), buffer.size());
            const auto count = file.gcount();
            if (count > 0 && EVP_DigestUpdate(context.get(), buffer.data(),
                                               static_cast<std::size_t>(count)) != 1)
                return std::string{};
        }
        if (!file.eof()) return std::string{};
        std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
        unsigned int length = 0;
        if (EVP_DigestFinal_ex(context.get(), digest.data(), &length) != 1 ||
            length != 32) return std::string{};
        static constexpr char hex[] = "0123456789abcdef";
        std::array<char, 65> observed{};
        for (std::size_t i = 0; i < 32; ++i) {
            observed[2 * i] = hex[digest[i] >> 4];
            observed[2 * i + 1] = hex[digest[i] & 15];
        }
        return std::string(observed.data());
    }();
    return observed_sha;
}

struct ScopedAlignProof {
    std::array<hbfsim::scoped_aux::AccessSpan, 4> accesses{};
    std::size_t registered_count{0};
    std::string module_id;
    std::string kernel;
};

std::string align_proof_json_line(std::string_view reason,
                                  std::string_view detail)
{
    std::ostringstream out;
    out << "{\"hbfsim_align_proof\":1,\"reason\":\""
        << json_escape(reason) << "\",\"detail\":\""
        << json_escape(detail) << "\"}\n";
    return out.str();
}

#if HBFSIM_ENABLE_TEST_HOOKS
extern "C" const char* hbfsim_test_align_proof_json_v1(
    const char* reason, const char* detail) noexcept
{
    static thread_local std::string result;
    try {
        result = align_proof_json_line(reason ? reason : "",
                                       detail ? detail : "");
        return result.c_str();
    } catch (...) {
        return nullptr;
    }
}
#endif

// This is a sufficient, deliberately narrow proof for the exact M1 align
// entry. Unknown layouts and failed allocator queries use the ordinary strict
// path; they never become native approvals by absence of a range hit.
std::optional<ScopedAlignProof> prove_m1_align_unbound(
    RuntimeDomain domain, const char* api, const void* symbol,
    dim3 grid, dim3 block, std::size_t shared_memory, void** arguments)
{
    using namespace hbfsim::scoped_aux;
    static constexpr char kKernel[] =
        "_ZN4vllm3moe46moe_align_block_size_small_batch_expert_kernelIiLi256EEEvPKT_PiS5_S5_S5_iimiib";
    static constexpr char kDso[] =
        "/root/hbfsim-exp/rebuttal_20260921/env-restore-v1/runtime-v2/lib/python3.13/site-packages/vllm/_moe_C.abi3.so";
    Dl_info dso{};
    const bool has_dso = symbol != nullptr && ::dladdr(symbol, &dso) != 0 &&
                         dso.dli_fname != nullptr;
    const bool trace = has_dso &&
        std::strstr(dso.dli_fname, "/vllm/_moe_C.abi3.so") != nullptr;
    const auto fail = [&](const char* reason, const std::string& detail = "")
        -> std::optional<ScopedAlignProof> {
        if (trace) {
            static std::mutex log_mutex;
            std::lock_guard log_lock(log_mutex);
            std::cerr << align_proof_json_line(reason, detail);
        }
        return std::nullopt;
    };
    {
        std::ostringstream info;
        info << "domain=" << static_cast<int>(domain) << " api="
             << (api ? api : "null") << " dso="
             << (has_dso ? dso.dli_fname : "null") << " grid="
             << grid.x << ',' << grid.y << ',' << grid.z << " block="
             << block.x << ',' << block.y << ',' << block.z << " shared="
             << shared_memory << " args=" << (arguments != nullptr);
        if (domain != RuntimeDomain::Cudart12 || api == nullptr ||
            std::strcmp(api, "cudaLaunchKernel") != 0 || symbol == nullptr ||
            arguments == nullptr || grid.x != 1 || grid.y != 1 || grid.z != 1 ||
            block.x != 320 || block.y != 1 || block.z != 1 ||
            shared_memory != 16900)
            return fail("domain_api_geometry", info.str());
    }
    if (!has_dso || std::strcmp(dso.dli_fname, kDso) != 0)
        return fail("dso_path", has_dso ? dso.dli_fname : "dladdr_failed");
    struct stat dso_stat{};
    if (::stat(kDso, &dso_stat) != 0)
        return fail("dso_stat", std::to_string(errno));
    const auto observed_sha = observed_m1_moe_dso_sha256(kDso);
    if (dso_stat.st_size != 241840696 || observed_sha !=
        "44942f2b9909ee5b687e928055c2de802b76d9e3fb11812be9f421288ff273ef") {
        std::ostringstream info;
        info << "size=" << dso_stat.st_size << " expected_sha="
             << "44942f2b9909ee5b687e928055c2de802b76d9e3fb11812be9f421288ff273ef"
             << " observed_sha=" << observed_sha;
        return fail("dso_identity", info.str());
    }
    auto get_function = runtime_get_function_symbol(domain);
    cudaFunction_t runtime_function = nullptr;
    const auto lookup_result = get_function == nullptr
        ? cudaErrorNotSupported : get_function(&runtime_function, symbol);
    if (lookup_result != cudaSuccess || runtime_function == nullptr) {
        std::ostringstream info;
        info << "lookup_present=" << (get_function != nullptr)
             << " result=" << static_cast<int>(lookup_result)
             << " function=" << reinterpret_cast<std::uintptr_t>(runtime_function);
        return fail("cuda_get_func_by_symbol", info.str());
    }
    const auto function = reinterpret_cast<CUfunction>(runtime_function);
    const auto observed_name = function_name(function);
    if (observed_name != kKernel)
        return fail("function_name", observed_name);
    using get_info_type = CUresult (*)(CUfunction, std::size_t,
                                       std::size_t*, std::size_t*);
    static auto get_info = reinterpret_cast<get_info_type>(
        driver_symbol("cuFuncGetParamInfo"));
    if (get_info == nullptr) return fail("param_info_symbol_missing");
    static constexpr std::array<std::size_t, 11> kOffsets =
        {0, 8, 16, 24, 32, 40, 44, 48, 56, 60, 64};
    static constexpr std::array<std::size_t, 11> kWidths =
        {8, 8, 8, 8, 8, 4, 4, 8, 4, 4, 1};
    for (std::size_t index = 0; index < kWidths.size(); ++index) {
        std::size_t offset = 0, width = 0;
        const auto result = get_info(function, index, &offset, &width);
        if (result != CUDA_SUCCESS || offset != kOffsets[index] ||
            width != kWidths[index]) {
            std::ostringstream info;
            info << "index=" << index << " result=" << static_cast<int>(result)
                 << " offset=" << offset << " width=" << width
                 << " expected_offset=" << kOffsets[index]
                 << " expected_width=" << kWidths[index];
            return fail("param_abi", info.str());
        }
    }
    std::size_t extra_offset = 0, extra_width = 0;
    const auto extra_result = get_info(function, kWidths.size(),
                                       &extra_offset, &extra_width);
    if (extra_result != CUDA_ERROR_INVALID_VALUE) {
        std::ostringstream info;
        info << "result=" << static_cast<int>(extra_result)
             << " offset=" << extra_offset << " width=" << extra_width;
        return fail("param_extra", info.str());
    }
    std::array<std::uint64_t, 5> pointers{};
    for (std::size_t i = 0; i < pointers.size(); ++i)
        if (!read_argument_bytes(arguments, i, 0, &pointers[i], 8))
            return fail("pointer_decode", std::to_string(i));
    std::uint32_t experts = 0, block_size = 0, padded = 0, topk = 0;
    std::uint64_t numel = 0;
    std::uint8_t has_map = 1;
    if (!read_argument_bytes(arguments, 5, 0, &experts, 4) ||
        !read_argument_bytes(arguments, 6, 0, &block_size, 4) ||
        !read_argument_bytes(arguments, 7, 0, &numel, 8) ||
        !read_argument_bytes(arguments, 8, 0, &padded, 4) ||
        !read_argument_bytes(arguments, 9, 0, &topk, 4) ||
        !read_argument_bytes(arguments, 10, 0, &has_map, 1))
        return fail("scalar_decode");
    if (experts != 64 || block_size == 0 || block_size > 1024 ||
        numel != 8 || topk != 8 || has_map != 0 ||
        padded != 8 * block_size) {
        std::ostringstream info;
        info << "experts=" << experts << " block_size=" << block_size
             << " numel=" << numel << " padded=" << padded
             << " topk=" << topk << " has_map=" << unsigned(has_map);
        return fail("scalar_values", info.str());
    }
    using address_range_type = CUresult (*)(CUdeviceptr*, std::size_t*,
                                            CUdeviceptr);
    static auto address_range = reinterpret_cast<address_range_type>(
        driver_symbol("cuMemGetAddressRange_v2"));
    if (address_range == nullptr) return fail("address_range_symbol_missing");
    const std::array<std::uint64_t, 4> required =
        {32, 4ULL * padded, 32, 4};
    ScopedAlignProof proof;
    for (std::size_t i = 0; i < required.size(); ++i) {
        CUdeviceptr backing = 0;
        std::size_t backing_bytes = 0;
        const auto result = pointers[i] == 0 ? CUDA_ERROR_INVALID_VALUE :
            address_range(&backing, &backing_bytes,
                          static_cast<CUdeviceptr>(pointers[i]));
        if (result != CUDA_SUCCESS) {
            std::ostringstream info;
            info << "index=" << i << " pointer=" << pointers[i]
                 << " required=" << required[i]
                 << " result=" << static_cast<int>(result);
            return fail("address_range_query", info.str());
        }
        proof.accesses[i] = {pointers[i], required[i],
                             static_cast<std::uint64_t>(backing),
                             static_cast<std::uint64_t>(backing_bytes)};
    }
    // The fifth pointer is the C++ empty expert-map tensor. The exact kernel
    // does not dereference it when the decoded has_map flag is false.
    const auto registered = runtime_gate().registered_snapshot();
    if (!complete_disjoint(registered, proof.accesses, true)) {
        std::ostringstream info;
        info << "registered=" << registered.size();
        for (std::size_t i = 0; i < proof.accesses.size(); ++i) {
            const auto& a = proof.accesses[i];
            info << " access" << i << '=' << a.base << '+' << a.bytes
                 << " backing=" << a.backing_base << '+' << a.backing_bytes;
        }
        for (std::size_t i = 0; i < registered.size(); ++i)
            info << " range" << i << '=' << registered[i].base
                 << '+' << registered[i].bytes;
        return fail("allocation_disjoint", info.str());
    }
    proof.registered_count = registered.size();
    // The installed native cubin is intentionally absent from the HBF module
    // registry. The exact host DSO hash above supplies this scope's identity.
    proof.module_id = "native-unbound:vllm-moe-C:44942f2b9909ee5b687e928055c2de802b76d9e3fb11812be9f421288ff273ef";
    proof.kernel = kKernel;
    return proof;
}

#if HBFSIM_ENABLE_TEST_HOOKS
extern "C" int hbfsim_test_strict_direct_action_v1(int gate_decision) noexcept
{
    return static_cast<int>(hbfsim::strict_direct_action(
        strict_bridge_capabilities(), gate_decision));
}
#endif

cudaError_t runtime_launch(RuntimeDomain domain, const char* symbol,
                           const void* function, dim3 grid,
                           dim3 block, void** arguments,
                           std::size_t shared_memory, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<runtime_launch_type>(runtime_symbol(domain, symbol));
    const auto finish = [&](cudaError_t result) {
        emit_launch_diagnostic({
            .wrapper = symbol,
            .original = reinterpret_cast<const void*>(original),
            .lookup =
                reinterpret_cast<const void*>(runtime_get_function_symbol(domain)),
            .function = function,
            .grid_x = grid.x, .grid_y = grid.y, .grid_z = grid.z,
            .block_x = block.x, .block_y = block.y, .block_z = block.z,
            .shared_memory = static_cast<unsigned int>(shared_memory),
            .stream = reinterpret_cast<const void*>(stream),
            .parameters = arguments,
            .result = static_cast<long long>(result),
        });
        return result;
    };
    if (original == nullptr) {
        return finish(cudaErrorInitializationError);
    }
    auto launch_guard = runtime_gate().launch_guard();
    FutureGeometryScope geometry(
        {{grid.x, grid.y, grid.z}, {block.x, block.y, block.z}});
    if (std::strcmp(symbol, "cudaLaunchKernel") != 0 &&
        std::strcmp(symbol, "cudaLaunchKernel_ptsz") != 0) {
        future_geometry.reset();
    }
    CUfunction resolved_function = nullptr;
    const auto align_proof = prove_m1_align_unbound(
        domain, symbol, function, grid, block, shared_memory, arguments);
    const auto decision = align_proof.has_value()
        ? hbfsim::GateDecision{.allowed = true,
              .module_id = align_proof->module_id,
              .kernel = align_proof->kernel,
              .reason = "native_unbound_exact_align_full_allocation_disjoint"}
        : inspect_symbol_launch(domain, function, arguments,
                                &resolved_function);
    if (!approve(decision)) {
        strict_denial_stop_if_enabled(decision, symbol,
            domain == RuntimeDomain::Cudart12 ? "cudart12" : "cudart13",
            runtime_version(domain), reinterpret_cast<const void*>(original),
            reinterpret_cast<const void*>(runtime_get_function_symbol(domain)),
            function);
        return finish(cudaErrorNotSupported);
    }

    // The bounded CUDA 12 native-source path uses the public runtime entry.
    // In strict mode it must dispatch the exact resolved CUfunction through
    // the reviewed driver bridge and must never fall back to cudart.
    if (strict_instrumentation_requested()) {
        const auto reject_after_approval =
            [&](std::string reason, cudaError_t result) {
                auto rejected = decision;
                rejected.allowed = false;
                rejected.reason = std::move(reason);
                strict_denial_stop_if_enabled(rejected, symbol,
                    domain == RuntimeDomain::Cudart12 ? "cudart12" : "cudart13",
                    runtime_version(domain),
                    reinterpret_cast<const void*>(original),
                    reinterpret_cast<const void*>(
                        runtime_get_function_symbol(domain)), function);
                return finish(result);
            };
        if (domain != RuntimeDomain::Cudart12)
            return reject_after_approval("strict_direct_wrong_runtime_domain",
                                         cudaErrorNotSupported);
        if (std::strcmp(symbol, "cudaLaunchKernel") != 0)
            return reject_after_approval("strict_direct_unsupported_runtime_api",
                                         cudaErrorNotSupported);
        const auto strict_action = hbfsim::strict_direct_action(
            strict_bridge_capabilities(),
            approval_code(decision));
        if (strict_action == hbfsim::StrictDirectAction::Reject)
            return reject_after_approval("strict_direct_gate_capability_or_decision",
                                         cudaErrorNotSupported);
        if (strict_action == hbfsim::StrictDirectAction::Original) {
            RuntimeLaunchScope scope(1, resolved_function);
            const auto result = original(function, grid, block, arguments,
                                         shared_memory, stream);
            complete_strict_original(function,
                domain == RuntimeDomain::Cudart12 ? 12 : 13, 1, 1, result);
            if (align_proof.has_value()) {
                static std::mutex log_mutex;
                std::lock_guard log_lock(log_mutex);
                std::cerr << "{\"hbfsim_native_unbound\":1,\"kind\":\"moe_align_m1\""
                          << ",\"scope\":\"non_weight_auxiliary_not_hbf_coverage\""
                          << ",\"registered_count\":" << align_proof->registered_count
                          << ",\"cuda_result\":" << static_cast<int>(result)
                          << ",\"allocations\":[";
                for (std::size_t i = 0; i < align_proof->accesses.size(); ++i) {
                    const auto& item = align_proof->accesses[i];
                    if (i) std::cerr << ',';
                    std::cerr << "{\"access_base\":" << item.base
                              << ",\"access_bytes\":" << item.bytes
                              << ",\"backing_base\":" << item.backing_base
                              << ",\"backing_bytes\":" << item.backing_bytes << '}';
                }
                std::cerr << "]}\n";
            }
            return finish(result);
        }
        if (resolved_function == nullptr)
            return reject_after_approval("strict_direct_missing_cufunction",
                                         cudaErrorInvalidResourceHandle);
        if (shared_memory >
            static_cast<std::size_t>(std::numeric_limits<unsigned int>::max()))
            return reject_after_approval("strict_direct_shared_memory_overflow",
                                         cudaErrorInvalidValue);
        auto direct = reinterpret_cast<strict_direct_driver_launch_type>(
            dlsym(RTLD_DEFAULT,
                  "bpftime_nv_strict_direct_cu_launch_kernel_v1"));
        if (direct == nullptr)
            return reject_after_approval("strict_direct_bridge_missing",
                                         cudaErrorNotSupported);
        RuntimeLaunchScope scope(2, resolved_function);
        const CUresult driver_result = direct(
            resolved_function, grid.x, grid.y, grid.z, block.x, block.y,
            block.z, static_cast<unsigned int>(shared_memory),
            reinterpret_cast<CUstream>(stream), arguments, nullptr);
        if (driver_result != CUDA_SUCCESS)
            return reject_after_approval(
                "strict_direct_driver_error_" +
                    std::to_string(static_cast<int>(driver_result)),
                runtime_error_from_driver(driver_result));
        return finish(cudaSuccess);
    }
    RuntimeLaunchScope scope(approval_code(decision), resolved_function);
    return finish(original(function, grid, block, arguments, shared_memory,
                           stream));
}

using kernel_launch_type = cudaError_t (*)(cudaKernel_t, dim3, dim3, void**,
                                           std::size_t, cudaStream_t);

cudaError_t runtime_error_from_driver(CUresult result) noexcept
{
    switch (result) {
    case CUDA_SUCCESS: return cudaSuccess;
    case CUDA_ERROR_INVALID_VALUE: return cudaErrorInvalidValue;
    case CUDA_ERROR_INVALID_HANDLE: return cudaErrorInvalidResourceHandle;
    case CUDA_ERROR_NOT_READY: return cudaErrorNotReady;
    case CUDA_ERROR_NOT_SUPPORTED: return cudaErrorNotSupported;
    case CUDA_ERROR_LAUNCH_FAILED: return cudaErrorLaunchFailure;
    case CUDA_ERROR_LAUNCH_TIMEOUT: return cudaErrorLaunchTimeout;
    case CUDA_ERROR_LAUNCH_OUT_OF_RESOURCES:
        return cudaErrorLaunchOutOfResources;
    case CUDA_ERROR_ILLEGAL_ADDRESS: return cudaErrorIllegalAddress;
    default: return cudaErrorUnknown;
    }
}

bool strict_instrumentation_requested() noexcept
{
    const char* value = std::getenv("HBFSIM_INSTRUMENTATION_POLICY");
    return value != nullptr && std::strcmp(value, "strict") == 0;
}

cudaError_t kernel_launch(RuntimeDomain domain, const char* symbol,
                          cudaKernel_t kernel, dim3 grid,
                          dim3 block, void** arguments,
                          std::size_t shared_memory, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<kernel_launch_type>(runtime_symbol(domain, symbol));
    const auto finish = [&](cudaError_t result) {
        emit_launch_diagnostic({
            .wrapper = symbol,
            .original = reinterpret_cast<const void*>(original),
            .function = reinterpret_cast<const void*>(kernel),
            .grid_x = grid.x, .grid_y = grid.y, .grid_z = grid.z,
            .block_x = block.x, .block_y = block.y, .block_z = block.z,
            .shared_memory = static_cast<unsigned int>(shared_memory),
            .stream = reinterpret_cast<const void*>(stream),
            .parameters = arguments,
            .result = static_cast<long long>(result),
        });
        return result;
    };
    if (original == nullptr) {
        return finish(cudaErrorInitializationError);
    }
    auto launch_guard = runtime_gate().launch_guard();
    FutureGeometryScope geometry(
        {{grid.x, grid.y, grid.z}, {block.x, block.y, block.z}});
    CUfunction resolved_function = nullptr;
    const auto decision =
        inspect_kernel_launch(kernel, arguments, &resolved_function);
    if (!approve(decision)) {
        strict_denial_stop_if_enabled(decision, symbol,
            domain == RuntimeDomain::Cudart12 ? "cudart12" : "cudart13",
            runtime_version(domain), reinterpret_cast<const void*>(original),
            nullptr, reinterpret_cast<const void*>(kernel));
        return finish(cudaErrorNotSupported);
    }

    // The bounded direct bridge currently covers only the normal private
    // CUDA 13 eager entry used by the native-chain fixture.  In strict mode an
    // instrumented decision must take this path; gate=1, other domains/APIs,
    // and a missing bridge all fail closed without calling cudart.
    if (strict_instrumentation_requested()) {
        const auto reject_after_approval =
            [&](std::string reason, cudaError_t result) {
                auto rejected = decision;
                rejected.allowed = false;
                rejected.reason = std::move(reason);
                strict_denial_stop_if_enabled(rejected, symbol,
                    domain == RuntimeDomain::Cudart12 ? "cudart12" : "cudart13",
                    runtime_version(domain),
                    reinterpret_cast<const void*>(original), nullptr,
                    reinterpret_cast<const void*>(kernel));
                return finish(result);
            };
        if (domain != RuntimeDomain::Cudart13)
            return reject_after_approval("strict_direct_wrong_runtime_domain",
                                         cudaErrorNotSupported);
        if (std::strcmp(symbol, "__cudaLaunchKernel") != 0)
            return reject_after_approval("strict_direct_unsupported_runtime_api",
                                         cudaErrorNotSupported);
        const auto strict_action = hbfsim::strict_direct_action(
            strict_bridge_capabilities(),
            approval_code(decision));
        if (strict_action == hbfsim::StrictDirectAction::Reject)
            return reject_after_approval("strict_direct_gate_capability_or_decision",
                                         cudaErrorNotSupported);
        if (strict_action == hbfsim::StrictDirectAction::Original) {
            RuntimeLaunchScope scope(1, resolved_function);
            const auto result = original(kernel, grid, block, arguments,
                                         shared_memory, stream);
            complete_strict_original(reinterpret_cast<const void*>(kernel),
                domain == RuntimeDomain::Cudart12 ? 12 : 13, 2, 1, result);
            return finish(result);
        }
        if (resolved_function == nullptr)
            return reject_after_approval("strict_direct_missing_cufunction",
                                         cudaErrorInvalidResourceHandle);
        if (shared_memory >
            static_cast<std::size_t>(std::numeric_limits<unsigned int>::max()))
            return reject_after_approval("strict_direct_shared_memory_overflow",
                                         cudaErrorInvalidValue);
        auto direct = reinterpret_cast<strict_direct_driver_launch_type>(
            dlsym(RTLD_DEFAULT,
                  "bpftime_nv_strict_direct_cu_launch_kernel_v1"));
        if (direct == nullptr)
            return reject_after_approval("strict_direct_bridge_missing",
                                         cudaErrorNotSupported);
        RuntimeLaunchScope scope(2, resolved_function);
        const CUresult driver_result = direct(
            resolved_function, grid.x, grid.y, grid.z, block.x, block.y,
            block.z, static_cast<unsigned int>(shared_memory),
            reinterpret_cast<CUstream>(stream), arguments, nullptr);
        if (driver_result != CUDA_SUCCESS)
            return reject_after_approval(
                "strict_direct_driver_error_" +
                    std::to_string(static_cast<int>(driver_result)),
                runtime_error_from_driver(driver_result));
        return finish(cudaSuccess);
    }
    RuntimeLaunchScope scope(approval_code(decision), resolved_function);
    return finish(original(kernel, grid, block, arguments, shared_memory,
                           stream));
}

bool joint_modules_match() noexcept
{
    if (access_session.modules.size() != eval_session.modules.size()) return false;
    for (std::size_t index = 0; index < access_session.modules.size(); ++index) {
        const auto& access = access_session.modules[index].tracked;
        const auto& eval = eval_session.modules[index].tracked;
        if (access.module != eval.module || access.context != eval.context ||
            access.device != eval.device || access.identity != eval.identity)
            return false;
    }
    return true;
}


std::vector<std::string> tracked_ids_for_domain(const CudaDomain& domain)
{
    std::vector<std::string> ids;
    for (const auto& [module, tracked] : access_tracked_modules) {
        (void)module;
        if (tracked.context == domain.context && tracked.device == domain.device)
            ids.push_back(tracked.identity);
    }
    std::sort(ids.begin(), ids.end());
    return ids;
}

long long tracked_modules_snapshot(char* out_json, std::size_t capacity) noexcept
{
    try {
        auto launch_exclusion = runtime_gate().accounting_transition_guard();
        std::lock_guard lock(access_accounting_mutex);
        if (access_session.active || eval_session.active || joint_session_owned) return -2;
        const auto domain = current_cuda_domain();
        if (!domain) return -3;
        const auto ids = tracked_ids_for_domain(*domain);
        std::ostringstream out;
        out << "{\"status\":\"READY\",\"device\":" << domain->device
            << ",\"module_count\":" << ids.size() << ",\"identities\":[";
        for (std::size_t i = 0; i < ids.size(); ++i) {
            if (i) out << ',';
            out << '"' << json_escape(ids[i]) << '"';
        }
        out << "]}";
        const auto text = out.str();
        const auto required = text.size() + 1;
        if (out_json && capacity >= required)
            std::memcpy(out_json, text.c_str(), required);
        return static_cast<long long>(required);
    } catch (...) { return -9; }
}

int joint_begin(std::uint64_t delay_ns, std::uint64_t request_epoch,
                std::uint64_t trace_capacity,
                const std::vector<std::string>* expected_ids = nullptr,
                std::uint64_t max_total_trace_bytes = 0) noexcept
{
    try {
        auto launch_exclusion = runtime_gate().accounting_transition_guard();
        std::lock_guard lock(access_accounting_mutex);
        if (accounting_poisoned) return -10;
        if (request_epoch == 0 || access_session.active || eval_session.active ||
            joint_session_owned)
            return -1;
        joint_session_cached_json.clear();
        if (expected_ids != nullptr) {
            const auto domain = current_cuda_domain();
            if (!domain) return -12;
            const auto actual_ids = tracked_ids_for_domain(*domain);
            const int policy = hbfsim_scoped_accounting_policy(
                actual_ids, *expected_ids, trace_capacity,
                sizeof(EvalDelayTrace), max_total_trace_bytes);
            if (policy != 0) return policy;
        }
        struct JointInternalScope {
            JointInternalScope()
            {
                joint_begin_in_progress = true;
                joint_internal_call = true;
            }
            ~JointInternalScope()
            {
                joint_internal_call = false;
                joint_begin_in_progress = false;
            }
        } scope;
        const int eval_status = eval_begin(delay_ns, request_epoch, trace_capacity);
        if (eval_status != 0) return eval_status;
        const int access_status = access_begin(request_epoch);
        if (access_status != 0 || !joint_modules_match()) {
            const int access_rollback = access_abort();
            const int eval_rollback = eval_abort();
            return access_status != 0 ? access_status
                                      : (access_rollback == 0 && eval_rollback == 0 ? -7 : -8);
        }
        joint_session_owned = true;
        return 0;
    } catch (...) { return -9; }
}

long long joint_snapshot(char* out_json, std::size_t capacity) noexcept
{
    try {
        auto launch_exclusion = runtime_gate().accounting_transition_guard();
        std::lock_guard lock(access_accounting_mutex);
        if (joint_session_cached_json.empty()) {
            if (!joint_session_owned || !access_session.active || !eval_session.active ||
                access_session.epoch != eval_session.epoch ||
                access_session.domain.context != eval_session.domain.context ||
                access_session.domain.device != eval_session.domain.device ||
                !joint_modules_match())
                return -1;
            const auto epoch = access_session.epoch;
            struct JointInternalScope {
                JointInternalScope() { joint_internal_call = true; }
                ~JointInternalScope() { joint_internal_call = false; }
            } scope;
            const auto access_required = access_snapshot(nullptr, 0);
            const auto eval_required = eval_snapshot(nullptr, 0);
            if (access_required <= 1 || eval_required <= 1) {
                (void)access_abort();
                (void)eval_abort();
                joint_session_owned = false;
                return -2;
            }
            const bool access_complete =
                access_session.cached_json.rfind("{\"status\":\"COMPLETE\"", 0) == 0;
            const bool eval_complete =
                eval_session.cached_json.rfind("{\"status\":\"COMPLETE\"", 0) == 0;
            std::ostringstream out;
            out << "{\"status\":\""
                << (access_complete && eval_complete ? "COMPLETE" : "INCOMPLETE")
                << "\",\"epoch\":" << epoch << ",\"access\":"
                << access_session.cached_json << ",\"eval_delay\":"
                << eval_session.cached_json << '}';
            joint_session_cached_json = out.str();
            joint_session_owned = false;
        }
        const auto required = joint_session_cached_json.size() + 1;
        if (out_json != nullptr && capacity >= required)
            std::memcpy(out_json, joint_session_cached_json.c_str(), required);
        return static_cast<long long>(required);
    } catch (...) { return -9; }
}

int joint_abort() noexcept
{
    try {
        auto launch_exclusion = runtime_gate().accounting_transition_guard();
        std::lock_guard lock(access_accounting_mutex);
        if (!joint_session_owned && !access_session.active && !eval_session.active)
            return 0;
        struct JointInternalScope {
            JointInternalScope() { joint_internal_call = true; }
            ~JointInternalScope() { joint_internal_call = false; }
        } scope;
        const int access_status = access_abort();
        const int eval_status = eval_abort();
        joint_session_owned = false;
        joint_session_cached_json.clear();
        return access_status == 0 && eval_status == 0 ? 0 : -1;
    } catch (...) { return -9; }
}

}  // namespace

extern "C" int
hbfsim_access_accounting_begin_v2(std::uint64_t request_epoch) noexcept
{
    return access_begin(request_epoch);
}

extern "C" long long hbfsim_access_accounting_snapshot_v2(
    char* out_json, std::size_t capacity) noexcept
{
    return access_snapshot(out_json, capacity);
}

extern "C" int hbfsim_access_accounting_abort_v2() noexcept
{
    return access_abort();
}

extern "C" int hbfsim_eval_delay_begin_v1(
    std::uint64_t delay_ns, std::uint64_t request_epoch,
    std::uint64_t trace_capacity_per_module) noexcept
{
    return eval_begin(delay_ns, request_epoch, trace_capacity_per_module);
}

extern "C" long long hbfsim_eval_delay_snapshot_v1(
    char* out_json, std::size_t capacity) noexcept
{
    return eval_snapshot(out_json, capacity);
}

extern "C" int hbfsim_eval_delay_abort_v1() noexcept
{
    return eval_abort();
}


extern "C" long long hbfsim_tracked_modules_snapshot_v1(
    char* out_json, std::size_t capacity) noexcept
{
    return tracked_modules_snapshot(out_json, capacity);
}

extern "C" int hbfsim_request_accounting_begin_scoped_v2(
    std::uint64_t delay_ns, std::uint64_t request_epoch,
    std::uint64_t trace_capacity_per_module,
    const char* const* expected_module_ids, std::size_t expected_module_count,
    std::uint64_t max_total_trace_bytes) noexcept
{
    try {
        if (!expected_module_ids || expected_module_count == 0) return -11;
        std::vector<std::string> expected;
        expected.reserve(expected_module_count);
        for (std::size_t i = 0; i < expected_module_count; ++i) {
            if (!expected_module_ids[i]) return -11;
            expected.emplace_back(expected_module_ids[i]);
        }
        std::sort(expected.begin(), expected.end());
        if (std::adjacent_find(expected.begin(), expected.end()) != expected.end())
            return -11;
        return joint_begin(delay_ns, request_epoch, trace_capacity_per_module,
                           &expected, max_total_trace_bytes);
    } catch (...) { return -9; }
}

extern "C" int hbfsim_request_accounting_begin_v1(
    std::uint64_t delay_ns, std::uint64_t request_epoch,
    std::uint64_t trace_capacity_per_module) noexcept
{
    return joint_begin(delay_ns, request_epoch, trace_capacity_per_module);
}

extern "C" long long hbfsim_request_accounting_snapshot_v1(
    char* out_json, std::size_t capacity) noexcept
{
    return joint_snapshot(out_json, capacity);
}

extern "C" int hbfsim_request_accounting_abort_v1() noexcept
{
    return joint_abort();
}

extern "C" int hbfsim_first_fault_begin_v1(
    std::uint64_t epoch, const char* backing_path) noexcept
{
    return first_fault_begin(epoch, backing_path);
}

extern "C" long long hbfsim_first_fault_snapshot_v1(
    char* out_json, std::size_t capacity) noexcept
{
    return first_fault_snapshot(out_json, capacity);
}

extern "C" int hbfsim_first_fault_abort_v1() noexcept
{
    return first_fault_abort();
}

#if defined(HBFSIM_ENABLE_TEST_HOOKS)
extern "C" void hbfsim_test_accounting_track_module(
    CUmodule module, const char* identity, std::uintptr_t context,
    int device) noexcept
{
    try {
        hbfsim::ModuleIdentity bytes{};
        if (identity != nullptr)
            std::memcpy(bytes.data(), identity,
                        std::min(bytes.size(), std::strlen(identity)));
        access_track_module(module, bytes, CudaDomain{context, device});
    } catch (...) {}
}

extern "C" void hbfsim_test_accounting_untrack_module(CUmodule module) noexcept
{
    try { access_untrack_module(module); } catch (...) {}
}

extern "C" void hbfsim_test_arm_activation_attempt() noexcept
{
    activation_attempt_seen.store(false, std::memory_order_release);
    activation_contention_observed.store(false, std::memory_order_release);
    activation_attempt_armed.store(true, std::memory_order_release);
}

extern "C" int hbfsim_test_wait_activation_attempt() noexcept
{
    activation_attempt_seen.wait(false, std::memory_order_acquire);
    return activation_contention_observed.load(std::memory_order_acquire) ? 1
                                                                          : 0;
}

extern "C" int hbfsim_test_read_packed_pointer(
    void** extra, std::size_t parameter_offset, std::size_t field_offset,
    std::uintptr_t* output) noexcept
{
    try {
        const std::byte* buffer = nullptr;
        std::size_t bytes = 0;
        return packed_launch_buffer(extra, &buffer, &bytes) &&
                       read_packed_bytes(buffer, bytes, parameter_offset,
                                         field_offset, output, sizeof(*output))
                   ? 0
                   : -1;
    } catch (...) {
        return -1;
    }
}

extern "C" int hbfsim_test_read_argument_pointer(
    void** arguments, std::size_t index, std::size_t field_offset,
    std::uintptr_t* output) noexcept
{
    try {
        return read_argument_bytes(arguments, index, field_offset, output,
                                   sizeof(*output))
                   ? 0
                   : -1;
    } catch (...) {
        return -1;
    }
}
#endif

extern "C" const void*
hbfsim_launch_gate_get_api(std::uint32_t requested_version) noexcept
{
    if (requested_version==hbfsim::kLaunchGateAbiVersionV4) return &launch_gate_api_v4;
    if (requested_version == hbfsim::kLaunchGateAbiVersion) {
        return &launch_gate_api_v3;
    }
    if (requested_version == hbfsim::kLaunchGateAbiVersionV2) {
        return &launch_gate_api_v2;
    }
    return nullptr;
}

#define HBFSIM_DRIVER_LAUNCH(name)                                             \
    extern "C" CUresult name(CUfunction function, unsigned int grid_x,         \
                             unsigned int grid_y, unsigned int grid_z,         \
                             unsigned int block_x, unsigned int block_y,       \
                             unsigned int block_z, unsigned int shared_memory, \
                             CUstream stream, void** parameters, void** extra) \
    {                                                                          \
        return driver_launch(#name, function, grid_x, grid_y, grid_z, block_x, \
                             block_y, block_z, shared_memory, stream,          \
                             parameters, extra);                               \
    }

HBFSIM_DRIVER_LAUNCH(cuLaunchKernel)
HBFSIM_DRIVER_LAUNCH(cuLaunchKernel_ptsz)

#define HBFSIM_DRIVER_COOPERATIVE(name)                                        \
    extern "C" CUresult name(CUfunction function, unsigned int grid_x,         \
                             unsigned int grid_y, unsigned int grid_z,         \
                             unsigned int block_x, unsigned int block_y,       \
                             unsigned int block_z, unsigned int shared_memory, \
                             CUstream stream, void** parameters)               \
    {                                                                          \
        using type =                                                           \
            CUresult (*)(CUfunction, unsigned int, unsigned int, unsigned int, \
                         unsigned int, unsigned int, unsigned int,             \
                         unsigned int, CUstream, void**);                      \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return CUDA_ERROR_NOT_INITIALIZED;                                 \
        if (runtime_launch_in_progress && runtime_launch_approval == 2)        \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        if (runtime_launch_in_progress)                                        \
            return original(function, grid_x, grid_y, grid_z, block_x,         \
                            block_y, block_z, shared_memory, stream,           \
                            parameters);                                       \
        auto guard = runtime_gate().launch_guard();                            \
        const auto decision =                                                  \
            inspect_function_launch(function, parameters, nullptr);            \
        if (!approve(decision))                                                \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        if (decision.requires_instrumented_execution)                          \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        RuntimeLaunchScope scope(1, function);                                 \
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,    \
                        block_z, shared_memory, stream, parameters);           \
    }
HBFSIM_DRIVER_COOPERATIVE(cuLaunchCooperativeKernel)
HBFSIM_DRIVER_COOPERATIVE(cuLaunchCooperativeKernel_ptsz)

#define HBFSIM_RUNTIME_DOMAIN_ENTRY(export_name, implementation_name, domain,                                      symbol_name)                                   extern "C" cudaError_t implementation_name(                                       const void* function, dim3 grid, dim3 block, void** arguments,                 std::size_t shared_memory, cudaStream_t stream)                            {                                                                                  return runtime_launch(domain, symbol_name, function, grid, block,                                    arguments, shared_memory, stream);                   }

HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchKernel, hbfsim_cudaLaunchKernel_12,
                            RuntimeDomain::Cudart12, "cudaLaunchKernel")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchKernel, hbfsim_cudaLaunchKernel_13,
                            RuntimeDomain::Cudart13, "cudaLaunchKernel")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchKernel_ptsz,
                            hbfsim_cudaLaunchKernel_ptsz_12,
                            RuntimeDomain::Cudart12, "cudaLaunchKernel_ptsz")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchKernel_ptsz,
                            hbfsim_cudaLaunchKernel_ptsz_13,
                            RuntimeDomain::Cudart13, "cudaLaunchKernel_ptsz")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchCooperativeKernel,
                            hbfsim_cudaLaunchCooperativeKernel_12,
                            RuntimeDomain::Cudart12,
                            "cudaLaunchCooperativeKernel")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchCooperativeKernel,
                            hbfsim_cudaLaunchCooperativeKernel_13,
                            RuntimeDomain::Cudart13,
                            "cudaLaunchCooperativeKernel")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchCooperativeKernel_ptsz,
                            hbfsim_cudaLaunchCooperativeKernel_ptsz_12,
                            RuntimeDomain::Cudart12,
                            "cudaLaunchCooperativeKernel_ptsz")
HBFSIM_RUNTIME_DOMAIN_ENTRY(cudaLaunchCooperativeKernel_ptsz,
                            hbfsim_cudaLaunchCooperativeKernel_ptsz_13,
                            RuntimeDomain::Cudart13,
                            "cudaLaunchCooperativeKernel_ptsz")

extern "C" cudaError_t cudaLaunchKernel(
    const void*, dim3, dim3, void**, std::size_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}
extern "C" cudaError_t cudaLaunchKernel_ptsz(
    const void*, dim3, dim3, void**, std::size_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}
extern "C" cudaError_t cudaLaunchCooperativeKernel(
    const void*, dim3, dim3, void**, std::size_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}
extern "C" cudaError_t cudaLaunchCooperativeKernel_ptsz(
    const void*, dim3, dim3, void**, std::size_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}

#define HBFSIM_KERNEL_DOMAIN_ENTRY(implementation_name, domain, symbol_name)       extern "C" cudaError_t implementation_name(                                       cudaKernel_t kernel, dim3 grid, dim3 block, void** arguments,                  std::size_t shared_memory, cudaStream_t stream)                            {                                                                                  return kernel_launch(domain, symbol_name, kernel, grid, block,                                      arguments, shared_memory, stream);                    }

HBFSIM_KERNEL_DOMAIN_ENTRY(hbfsim___cudaLaunchKernel_12,
                           RuntimeDomain::Cudart12, "__cudaLaunchKernel")
HBFSIM_KERNEL_DOMAIN_ENTRY(hbfsim___cudaLaunchKernel_13,
                           RuntimeDomain::Cudart13, "__cudaLaunchKernel")
HBFSIM_KERNEL_DOMAIN_ENTRY(hbfsim___cudaLaunchKernel_ptsz_12,
                           RuntimeDomain::Cudart12, "__cudaLaunchKernel_ptsz")
HBFSIM_KERNEL_DOMAIN_ENTRY(hbfsim___cudaLaunchKernel_ptsz_13,
                           RuntimeDomain::Cudart13, "__cudaLaunchKernel_ptsz")

extern "C" cudaError_t __cudaLaunchKernel(
    cudaKernel_t, dim3, dim3, void**, std::size_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}
extern "C" cudaError_t __cudaLaunchKernel_ptsz(
    cudaKernel_t, dim3, dim3, void**, std::size_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}

__asm__(".symver hbfsim_cudaLaunchKernel_12,cudaLaunchKernel@libcudart.so.12");
__asm__(".symver hbfsim_cudaLaunchKernel_13,cudaLaunchKernel@libcudart.so.13");
__asm__(".symver hbfsim_cudaLaunchKernel_ptsz_12,cudaLaunchKernel_ptsz@libcudart.so.12");
__asm__(".symver hbfsim_cudaLaunchKernel_ptsz_13,cudaLaunchKernel_ptsz@libcudart.so.13");
__asm__(".symver hbfsim_cudaLaunchCooperativeKernel_12,cudaLaunchCooperativeKernel@libcudart.so.12");
__asm__(".symver hbfsim_cudaLaunchCooperativeKernel_13,cudaLaunchCooperativeKernel@libcudart.so.13");
__asm__(".symver hbfsim_cudaLaunchCooperativeKernel_ptsz_12,cudaLaunchCooperativeKernel_ptsz@libcudart.so.12");
__asm__(".symver hbfsim_cudaLaunchCooperativeKernel_ptsz_13,cudaLaunchCooperativeKernel_ptsz@libcudart.so.13");
__asm__(".symver hbfsim___cudaLaunchKernel_12,__cudaLaunchKernel@libcudart.so.12");
__asm__(".symver hbfsim___cudaLaunchKernel_13,__cudaLaunchKernel@libcudart.so.13");
__asm__(".symver hbfsim___cudaLaunchKernel_ptsz_12,__cudaLaunchKernel_ptsz@libcudart.so.12");
__asm__(".symver hbfsim___cudaLaunchKernel_ptsz_13,__cudaLaunchKernel_ptsz@libcudart.so.13");

extern "C" CUresult cuLaunchKernelEx_ptsz(
    const CUlaunchConfig*, CUfunction, void**, void**);

using driver_ex_type =
    CUresult (*)(const CUlaunchConfig*, CUfunction, void**, void**);
using libc_dlsym_type = void* (*)(void*, const char*);

libc_dlsym_type real_dlsym() noexcept
{
    static auto function = reinterpret_cast<libc_dlsym_type>(
        dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5"));
    return function;
}

thread_local bool ex_driver_resolution_in_progress = false;

struct ExDriverEntrypoints {
    void* handle{nullptr};
    driver_ex_type legacy{nullptr};
    driver_ex_type per_thread{nullptr};
    void* image_base{nullptr};
    bool ready{false};
};

const ExDriverEntrypoints& ex_driver_entrypoints() noexcept
{
    static const ExDriverEntrypoints entrypoints = []() noexcept {
        ExDriverEntrypoints result;
        auto lookup = real_dlsym();
        if (lookup == nullptr || ex_driver_resolution_in_progress) {
            return result;
        }
        struct ResolutionScope {
            ResolutionScope() { ex_driver_resolution_in_progress = true; }
            ~ResolutionScope() { ex_driver_resolution_in_progress = false; }
        } scope;
        result.handle = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
        if (result.handle == nullptr) {
            return result;
        }
        void* legacy = lookup(result.handle, "cuLaunchKernelEx");
        void* per_thread = lookup(result.handle, "cuLaunchKernelEx_ptsz");
        void* legacy_wrapper = reinterpret_cast<void*>(&cuLaunchKernelEx);
        void* per_thread_wrapper =
            reinterpret_cast<void*>(&cuLaunchKernelEx_ptsz);
        Dl_info legacy_info{};
        Dl_info per_thread_info{};
        const bool legacy_valid =
            legacy != nullptr && legacy != legacy_wrapper &&
            legacy != per_thread_wrapper && dladdr(legacy, &legacy_info) != 0 &&
            legacy_info.dli_fbase != nullptr;
        const bool per_thread_valid =
            per_thread != nullptr && per_thread != legacy_wrapper &&
            per_thread != per_thread_wrapper &&
            dladdr(per_thread, &per_thread_info) != 0 &&
            per_thread_info.dli_fbase != nullptr;
        if (legacy_valid) {
            result.legacy = reinterpret_cast<driver_ex_type>(legacy);
        }
        if (per_thread_valid) {
            result.per_thread = reinterpret_cast<driver_ex_type>(per_thread);
        }
        if (legacy_valid && per_thread_valid &&
            legacy_info.dli_fbase == per_thread_info.dli_fbase) {
            result.image_base = legacy_info.dli_fbase;
            result.ready = true;
        }
        return result;
    }();
    return entrypoints;
}

driver_ex_type ex_driver_entrypoint(const char* symbol) noexcept
{
    if (symbol == nullptr || ex_driver_resolution_in_progress) {
        return nullptr;
    }
    const auto& entrypoints = ex_driver_entrypoints();
    if (!entrypoints.ready) {
        return nullptr;
    }
    if (std::strcmp(symbol, "cuLaunchKernelEx") == 0) {
        return entrypoints.legacy;
    }
    if (std::strcmp(symbol, "cuLaunchKernelEx_ptsz") == 0) {
        return entrypoints.per_thread;
    }
    return nullptr;
}

bool ex_direct_passthrough_enabled() noexcept
{
    static const bool enabled = [] {
        const char* value =
            std::getenv("HBFSIM_GATE_DIAG_EX_DIRECT_PASSTHROUGH");
        return value != nullptr && value[0] != '\0' &&
               std::strcmp(value, "0") != 0;
    }();
    return enabled;
}

bool strict_timing_policy_active() noexcept
{
    const char* value = std::getenv("HBFSIM_INSTRUMENTATION_POLICY");
    return value != nullptr && std::strcmp(value, "strict") == 0 &&
           runtime_gate().gate().has_ranges();
}

CUresult driver_ex_launch(const char* symbol, const CUlaunchConfig* config,
                          CUfunction function, void** parameters, void** extra)
{
    auto original = ex_driver_entrypoint(symbol);
    const auto finish = [&](CUresult result) {
        if (launch_diagnostics_enabled()) {
            emit_launch_diagnostic({
                .wrapper = symbol,
                .original = reinterpret_cast<const void*>(original),
                .function = reinterpret_cast<const void*>(function),
                .grid_x = config ? config->gridDimX : 0,
                .grid_y = config ? config->gridDimY : 0,
                .grid_z = config ? config->gridDimZ : 0,
                .block_x = config ? config->blockDimX : 0,
                .block_y = config ? config->blockDimY : 0,
                .block_z = config ? config->blockDimZ : 0,
                .shared_memory = config ? config->sharedMemBytes : 0,
                .stream =
                    config ? reinterpret_cast<const void*>(config->hStream)
                           : nullptr,
                .parameters = parameters,
                .extra = extra,
                .config = config,
                .attrs = config ? config->attrs : nullptr,
                .num_attrs = config ? config->numAttrs : 0,
                .result = static_cast<long long>(result),
            });
        }
        return result;
    };
    if (original == nullptr) {
        return finish(CUDA_ERROR_NOT_INITIALIZED);
    }
    if (ex_direct_passthrough_enabled()) {
        if (strict_timing_policy_active()) {
            return finish(CUDA_ERROR_NOT_SUPPORTED);
        }
        return finish(original(config, function, parameters, extra));
    }
    if (runtime_launch_in_progress) {
        return finish(original(config, function, parameters, extra));
    }
    auto guard = runtime_gate().launch_guard();
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        strict_denial_stop_if_enabled(decision, symbol, "driver",
            "libcuda.so.1", reinterpret_cast<const void*>(original), nullptr,
            reinterpret_cast<const void*>(function));
        return finish(CUDA_ERROR_NOT_SUPPORTED);
    }
    RuntimeLaunchScope scope(approval_code(decision), function);
    return finish(original(config, function, parameters, extra));
}

extern "C" CUresult cuLaunchKernelEx(const CUlaunchConfig* config,
                                      CUfunction function, void** parameters,
                                      void** extra)
{
    return driver_ex_launch("cuLaunchKernelEx", config, function, parameters,
                            extra);
}

extern "C" CUresult cuLaunchKernelEx_ptsz(const CUlaunchConfig* config,
                                           CUfunction function,
                                           void** parameters, void** extra)
{
    return driver_ex_launch("cuLaunchKernelEx_ptsz", config, function,
                            parameters, extra);
}

using runtime_ex_type =
    cudaError_t (*)(const cudaLaunchConfig_t*, const void*, void**);

cudaError_t runtime_ex_launch(RuntimeDomain domain, const char* symbol,
                              const cudaLaunchConfig_t* config,
                              const void* function, void** arguments)
{
    auto original =
        reinterpret_cast<runtime_ex_type>(runtime_symbol(domain, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    auto guard = runtime_gate().launch_guard();
    CUfunction resolved_function = nullptr;
    const auto decision =
        inspect_symbol_launch(domain, function, arguments, &resolved_function);
    if (!approve(decision)) {
        strict_denial_stop_if_enabled(decision, symbol,
            domain == RuntimeDomain::Cudart12 ? "cudart12" : "cudart13",
            runtime_version(domain), reinterpret_cast<const void*>(original),
            reinterpret_cast<const void*>(runtime_get_function_symbol(domain)),
            function);
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope(approval_code(decision), resolved_function);
    return original(config, function, arguments);
}

#define HBFSIM_RUNTIME_EX_DOMAIN_ENTRY(implementation_name, domain, symbol_name)     extern "C" cudaError_t implementation_name(                                        const cudaLaunchConfig_t* config, const void* function,                         void** arguments)                                                           {                                                                                   return runtime_ex_launch(domain, symbol_name, config, function,                                          arguments);                                        }

HBFSIM_RUNTIME_EX_DOMAIN_ENTRY(hbfsim_cudaLaunchKernelExC_12,
                               RuntimeDomain::Cudart12,
                               "cudaLaunchKernelExC")
HBFSIM_RUNTIME_EX_DOMAIN_ENTRY(hbfsim_cudaLaunchKernelExC_13,
                               RuntimeDomain::Cudart13,
                               "cudaLaunchKernelExC")
HBFSIM_RUNTIME_EX_DOMAIN_ENTRY(hbfsim_cudaLaunchKernelExC_ptsz_12,
                               RuntimeDomain::Cudart12,
                               "cudaLaunchKernelExC_ptsz")
HBFSIM_RUNTIME_EX_DOMAIN_ENTRY(hbfsim_cudaLaunchKernelExC_ptsz_13,
                               RuntimeDomain::Cudart13,
                               "cudaLaunchKernelExC_ptsz")

extern "C" cudaError_t cudaLaunchKernelExC(
    const cudaLaunchConfig_t*, const void*, void**)
{
    return cudaErrorInitializationError;
}
extern "C" cudaError_t cudaLaunchKernelExC_ptsz(
    const cudaLaunchConfig_t*, const void*, void**)
{
    return cudaErrorInitializationError;
}

__asm__(".symver hbfsim_cudaLaunchKernelExC_12,cudaLaunchKernelExC@libcudart.so.12");
__asm__(".symver hbfsim_cudaLaunchKernelExC_13,cudaLaunchKernelExC@libcudart.so.13");
__asm__(".symver hbfsim_cudaLaunchKernelExC_ptsz_12,cudaLaunchKernelExC_ptsz@libcudart.so.12");
__asm__(".symver hbfsim_cudaLaunchKernelExC_ptsz_13,cudaLaunchKernelExC_ptsz@libcudart.so.13");

hbfsim::GateDecision opaque_launch(const char* kind)
{
    auto& gate = runtime_gate().gate();
    return require_timing_binding(hbfsim::uninspectable_launch_decision(
        gate.has_ranges(), gate.has_strict_ranges(), kind), nullptr);
}

extern "C" CUresult cuLaunch(CUfunction function)
{
    using type = CUresult (*)(CUfunction);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuLaunch"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress && runtime_launch_approval == 2)
        return CUDA_ERROR_NOT_SUPPORTED;
    if (runtime_launch_in_progress)
        return original(function);
    auto guard = runtime_gate().launch_guard();
    return approve(opaque_launch("legacy_driver_launch"))
               ? original(function)
               : CUDA_ERROR_NOT_SUPPORTED;
}

extern "C" CUresult cuLaunchGrid(CUfunction function, int grid_width,
                                 int grid_height)
{
    using type = CUresult (*)(CUfunction, int, int);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuLaunchGrid"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress && runtime_launch_approval == 2) {
        return CUDA_ERROR_NOT_SUPPORTED;
    }
    if (runtime_launch_in_progress) {
        return original(function, grid_width, grid_height);
    }
    auto guard = runtime_gate().launch_guard();
    return approve(opaque_launch("legacy_driver_launch"))
               ? original(function, grid_width, grid_height)
               : CUDA_ERROR_NOT_SUPPORTED;
}

extern "C" CUresult cuLaunchGridAsync(CUfunction function, int grid_width,
                                      int grid_height, CUstream stream)
{
    using type = CUresult (*)(CUfunction, int, int, CUstream);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuLaunchGridAsync"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress && runtime_launch_approval == 2) {
        return CUDA_ERROR_NOT_SUPPORTED;
    }
    if (runtime_launch_in_progress) {
        return original(function, grid_width, grid_height, stream);
    }
    auto guard = runtime_gate().launch_guard();
    return approve(opaque_launch("legacy_driver_launch"))
               ? original(function, grid_width, grid_height, stream)
               : CUDA_ERROR_NOT_SUPPORTED;
}

#define HBFSIM_DRIVER_GRAPH(name)                                              \
    extern "C" CUresult name(CUgraphExec graph, CUstream stream)               \
    {                                                                          \
        using type = CUresult (*)(CUgraphExec, CUstream);                      \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return CUDA_ERROR_NOT_INITIALIZED;                                 \
        if (runtime_launch_in_progress && runtime_launch_approval == 2)        \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        if (runtime_launch_in_progress)                                        \
            return original(graph, stream);                                    \
        auto guard = runtime_gate().launch_guard();                            \
        return approve(opaque_launch("graph_launch"))                          \
                   ? original(graph, stream)                                   \
                   : CUDA_ERROR_NOT_SUPPORTED;                                 \
    }
HBFSIM_DRIVER_GRAPH(cuGraphLaunch)
HBFSIM_DRIVER_GRAPH(cuGraphLaunch_ptsz)

using runtime_graph_type =
    cudaError_t (*)(cudaGraphExec_t, cudaStream_t);

cudaError_t runtime_graph_launch(RuntimeDomain domain, const char* symbol,
                                 cudaGraphExec_t graph, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<runtime_graph_type>(runtime_symbol(domain, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    auto guard = runtime_gate().launch_guard();
    if (!approve(opaque_launch("graph_launch"))) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(graph, stream);
}

#define HBFSIM_RUNTIME_GRAPH_DOMAIN_ENTRY(implementation_name, domain, symbol_name)     extern "C" cudaError_t implementation_name(                                          cudaGraphExec_t graph, cudaStream_t stream)                                  {                                                                                     return runtime_graph_launch(domain, symbol_name, graph, stream);              }

HBFSIM_RUNTIME_GRAPH_DOMAIN_ENTRY(hbfsim_cudaGraphLaunch_12,
                                  RuntimeDomain::Cudart12,
                                  "cudaGraphLaunch")
HBFSIM_RUNTIME_GRAPH_DOMAIN_ENTRY(hbfsim_cudaGraphLaunch_13,
                                  RuntimeDomain::Cudart13,
                                  "cudaGraphLaunch")
HBFSIM_RUNTIME_GRAPH_DOMAIN_ENTRY(hbfsim_cudaGraphLaunch_ptsz_12,
                                  RuntimeDomain::Cudart12,
                                  "cudaGraphLaunch_ptsz")
HBFSIM_RUNTIME_GRAPH_DOMAIN_ENTRY(hbfsim_cudaGraphLaunch_ptsz_13,
                                  RuntimeDomain::Cudart13,
                                  "cudaGraphLaunch_ptsz")

extern "C" cudaError_t cudaGraphLaunch(cudaGraphExec_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}
extern "C" cudaError_t cudaGraphLaunch_ptsz(cudaGraphExec_t, cudaStream_t)
{
    return cudaErrorInitializationError;
}

__asm__(".symver hbfsim_cudaGraphLaunch_12,cudaGraphLaunch@libcudart.so.12");
__asm__(".symver hbfsim_cudaGraphLaunch_13,cudaGraphLaunch@libcudart.so.13");
__asm__(".symver hbfsim_cudaGraphLaunch_ptsz_12,cudaGraphLaunch_ptsz@libcudart.so.12");
__asm__(".symver hbfsim_cudaGraphLaunch_ptsz_13,cudaGraphLaunch_ptsz@libcudart.so.13");

extern "C" CUresult
cuLaunchCooperativeKernelMultiDevice(CUDA_LAUNCH_PARAMS* launches,
                                     unsigned int count, unsigned int flags)
{
    using type = CUresult (*)(CUDA_LAUNCH_PARAMS*, unsigned int, unsigned int);
    auto original = reinterpret_cast<type>(
        dlsym(RTLD_NEXT, "cuLaunchCooperativeKernelMultiDevice"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress && runtime_launch_approval == 2)
        return CUDA_ERROR_NOT_SUPPORTED;
    if (runtime_launch_in_progress)
        return original(launches, count, flags);
    auto guard = runtime_gate().launch_guard();
    if (launches == nullptr)
        return CUDA_ERROR_INVALID_VALUE;
    for (unsigned int index = 0; index < count; ++index) {
        const auto decision =
            inspect_function_launch(launches[index].function,
                                    launches[index].kernelParams, nullptr);
        if (!approve(decision) || decision.requires_instrumented_execution) {
            // This aggregate API cannot prove a one-to-one exact alias for
            // every member of the launch array.  Strict transformed launches
            // therefore fail closed; legacy/partial behavior is unchanged.
            return CUDA_ERROR_NOT_SUPPORTED;
        }
    }
    return original(launches, count, flags);
}

#if CUDART_VERSION < 13000
extern "C" cudaError_t
cudaLaunchCooperativeKernelMultiDevice(struct cudaLaunchParams* launches,
                                       unsigned int count, unsigned int flags)
{
    using type =
        cudaError_t (*)(struct cudaLaunchParams*, unsigned int, unsigned int);
    auto original = reinterpret_cast<type>(
        dlsym(RTLD_NEXT, "cudaLaunchCooperativeKernelMultiDevice"));
    if (original == nullptr)
        return cudaErrorInitializationError;
    if (runtime_launch_in_progress && runtime_launch_approval == 2)
        return cudaErrorNotSupported;
    if (runtime_launch_in_progress)
        return original(launches, count, flags);
    auto guard = runtime_gate().launch_guard();
    if (launches == nullptr)
        return cudaErrorInvalidValue;
    for (unsigned int index = 0; index < count; ++index) {
        CUfunction resolved_function = nullptr;
        const auto decision = inspect_symbol_launch(
            RuntimeDomain::Cudart12, launches[index].func,
            launches[index].args, &resolved_function);
        if (!approve(decision) || decision.requires_instrumented_execution) {
            return cudaErrorNotSupported;
        }
    }
    RuntimeLaunchScope scope;
    return original(launches, count, flags);
}
#endif

extern "C" std::uint64_t
hbfsim_begin_module_load_from_ptx(const char* ptx, std::size_t size) noexcept
{
    if (ptx == nullptr) {
        return 0;
    }
    return module_load_transactions().begin(std::string_view(ptx, size));
}

extern "C" void hbfsim_end_module_load(std::uint64_t token) noexcept
{
    module_load_transactions().end(token);
}

extern "C" int hbfsim_bind_original_cuda_function(
    CUfunction original, CUfunction patched) noexcept
{
    if (original == nullptr || patched == nullptr ||
        handle_id(patched).empty() || !timing_binding_ready(patched)) {
        return -1;
    }
    std::lock_guard lock(function_alias_mutex());
    const auto [found, inserted] = function_aliases().emplace(original, patched);
    return inserted || found->second == patched ? 0 : -1;
}

extern "C" int hbfsim_approve_original_cuda_function(
    CUfunction function, void** parameters, void** extra) noexcept
{
    if (function == nullptr) {
        return 0;
    }
    if (runtime_launch_in_progress) {
        if (runtime_launch_approval == 2 &&
            (runtime_launch_expected_function == nullptr ||
             runtime_launch_expected_function != function)) {
            return 0;
        }
        return runtime_launch_approval;
    }
    auto guard = runtime_gate().launch_guard();
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        return 0;
    }
    return (decision.modeled || decision.requires_instrumented_execution)
               ? 2
               : 1;
}

// Additive process-local handshake.  This is intentionally separate from the
// shared-control ABI: strict Python loaders must refuse older gate/agent
// combinations instead of assuming that an environment variable took effect.
extern "C" std::uint64_t
hbfsim_instrumentation_policy_capabilities_v1() noexcept
{
    return hbfsim::kStrictBridgeRequiredCapabilities;
}

// Handshake for strict-mode callers that require a durable first-denial
// receipt before a rejected CUDA launch can be ignored by an upstream caller.
extern "C" std::uint64_t hbfsim_strict_denial_capabilities_v1() noexcept
{
    constexpr std::uint64_t kHostReceiptAndImmediateExit = 1ull << 0;
    return kHostReceiptAndImmediateExit;
}

extern "C" CUresult cuModuleLoadDataEx(CUmodule* module, const void* image,
                                       unsigned int option_count,
                                       CUjit_option* options,
                                       void** option_values)
{
    const auto trusted_identity = module_load_transactions().take();
    using type = CUresult (*)(CUmodule*, const void*, unsigned int,
                              CUjit_option*, void**);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuModuleLoadDataEx"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    const auto result =
        original(module, image, option_count, options, option_values);
    if (result != CUDA_SUCCESS) {
        return result;
    }
    if (module != nullptr && *module != nullptr) {
        std::optional<hbfsim::timing_future::ModuleRequirements> future;
        const bool valid_contract=read_future_requirements(*module,future);
        const auto domain=current_cuda_domain();
        const auto identity=trusted_identity ? live_module_identity(*module) : std::nullopt;
        const bool trusted=trusted_identity && identity && *identity==*trusted_identity && domain;
        if (!valid_contract || (future && !trusted)) {
            // Explicit future modules never inherit a native fallback when
            // their transaction, identity, layout or live domain is missing.
            timing_bindings().reject_future_module(module_handle(*module),
                domain ? domain->context : 0,domain ? domain->device : -1);
            using unload_type=CUresult (*)(CUmodule);
            auto unload=reinterpret_cast<unload_type>(dlsym(RTLD_NEXT,"cuModuleUnload"));
            if (unload && unload(*module)==CUDA_SUCCESS) {
                timing_bindings().erase(module_handle(*module));
                *module=nullptr;
            }
            return CUDA_ERROR_NOT_SUPPORTED;
        }
        if (trusted) {
            if (future) {
                if (!timing_bindings().add_future_module(module_handle(*module),
                    domain->context,domain->device,*future,initialize_future_control,nullptr)) {
                    // Retain the owned handle/classification after failed clear.
                    return CUDA_ERROR_NOT_SUPPORTED;
                }
            } else {
                (void)timing_bindings().add_module(module_handle(*module),
                    domain->context,domain->device,initialize_module_control,nullptr);
            }
            (void)module_identities().associate(module_handle(*module),*identity);
            access_track_module(*module, *identity, *domain);
        }
    }
    return result;
}

extern "C" CUresult cuModuleUnload(CUmodule module)
{
    using type = CUresult (*)(CUmodule);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuModuleUnload"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    auto guard=runtime_gate().retirement_guard();
    if(timing_bindings().future_module(module_handle(module))) {
        bool owned=false;
        {std::lock_guard lock(future_budget_mutex);owned=future_budgets.contains(module_handle(module));}
        if(owned) {
            const auto domain=current_cuda_domain();
            using sync_type=CUresult (*)();
            const auto sync=reinterpret_cast<sync_type>(driver_symbol("cuCtxSynchronize"));
            if(!domain || !timing_bindings().active_domain(domain->context,domain->device) ||
                !sync || sync()!=CUDA_SUCCESS)return CUDA_ERROR_NOT_PERMITTED;
            const auto cleared=initialize_future_control(module_handle(module),0,0,{}, {},nullptr);
            if(cleared==hbfsim::FutureInitialization::Quarantine) {
                timing_bindings().quarantine_future_module(module_handle(module));
                return CUDA_ERROR_NOT_PERMITTED;
            }
        }
    }
    const auto result = original(module);
    if (result == CUDA_SUCCESS) {
        access_untrack_module(module);
        module_identities().erase(module_handle(module));
        timing_bindings().erase(module_handle(module));
    }
    return result;
}

extern "C" CUresult cuCtxDestroy(CUcontext context)
{
    using type = CUresult (*)(CUcontext);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuCtxDestroy"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_context(
            reinterpret_cast<std::uintptr_t>(context))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(context);
    if (result == CUDA_SUCCESS) {
        erase_context_state(reinterpret_cast<std::uintptr_t>(context));
    }
    return result;
}

extern "C" CUresult cuCtxDestroy_v2(CUcontext context)
{
    using type = CUresult (*)(CUcontext);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuCtxDestroy_v2"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_context(
            reinterpret_cast<std::uintptr_t>(context))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(context);
    if (result == CUDA_SUCCESS) {
        erase_context_state(reinterpret_cast<std::uintptr_t>(context));
    }
    return result;
}

extern "C" CUresult cuCtxDetach(CUcontext context)
{
    using type = CUresult (*)(CUcontext);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuCtxDetach"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_context(
            reinterpret_cast<std::uintptr_t>(context))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(context);
    if (result == CUDA_SUCCESS) {
        erase_context_state(reinterpret_cast<std::uintptr_t>(context));
    }
    return result;
}

extern "C" CUresult cuDevicePrimaryCtxReset(CUdevice device)
{
    using type = CUresult (*)(CUdevice);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuDevicePrimaryCtxReset"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_device(static_cast<int>(device))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(device);
    if (result == CUDA_SUCCESS) {
        erase_unbound_device_state(static_cast<int>(device));
    }
    return result;
}

extern "C" CUresult cuDevicePrimaryCtxReset_v2(CUdevice device)
{
    using type = CUresult (*)(CUdevice);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuDevicePrimaryCtxReset_v2"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_device(static_cast<int>(device))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(device);
    if (result == CUDA_SUCCESS) {
        erase_unbound_device_state(static_cast<int>(device));
    }
    return result;
}

extern "C" CUresult cuDevicePrimaryCtxRelease(CUdevice device)
{
    using type = CUresult (*)(CUdevice);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuDevicePrimaryCtxRelease"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_device(static_cast<int>(device))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(device);
    if (result == CUDA_SUCCESS) {
        erase_unbound_device_state(static_cast<int>(device));
    }
    return result;
}

extern "C" CUresult cuDevicePrimaryCtxRelease_v2(CUdevice device)
{
    using type = CUresult (*)(CUdevice);
    auto original = reinterpret_cast<type>(
        dlsym(RTLD_NEXT, "cuDevicePrimaryCtxRelease_v2"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (timing_bindings().active_device(static_cast<int>(device))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(device);
    if (result == CUDA_SUCCESS) {
        erase_unbound_device_state(static_cast<int>(device));
    }
    return result;
}

#if CUDA_VERSION >= 12040
extern "C" CUresult cuGreenCtxDestroy(CUgreenCtx context)
{
    using type = CUresult (*)(CUgreenCtx);
    using from_green_type = CUresult (*)(CUcontext*, CUgreenCtx);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuGreenCtxDestroy"));
    auto from_green = reinterpret_cast<from_green_type>(
        driver_symbol("cuCtxFromGreenCtx"));
    CUcontext cuda_context = nullptr;
    if (original == nullptr || from_green == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    if (from_green(&cuda_context, context) != CUDA_SUCCESS ||
        cuda_context == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    if (timing_bindings().active_context(
            reinterpret_cast<std::uintptr_t>(cuda_context))) {
        return CUDA_ERROR_NOT_PERMITTED;
    }
    const auto result = original(context);
    if (result == CUDA_SUCCESS) {
        erase_context_state(reinterpret_cast<std::uintptr_t>(cuda_context));
    }
    return result;
}
#endif

extern "C" cudaError_t cudaDeviceReset()
{
    using type = cudaError_t (*)();
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cudaDeviceReset"));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    const auto domain = current_cuda_domain();
    if (domain.has_value() && timing_bindings().active_domain(
                                  domain->context, domain->device)) {
        return cudaErrorNotPermitted;
    }
    const auto result = original();
    if (result == cudaSuccess && domain.has_value()) {
        erase_context_state(domain->context);
    }
    return result;
}

#if CUDART_VERSION < 13000
extern "C" cudaError_t cudaThreadExit()
{
    using type = cudaError_t (*)();
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cudaThreadExit"));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    std::lock_guard transition(lifecycle_transition_mutex());
    const auto domain = current_cuda_domain();
    if (domain.has_value() && timing_bindings().active_domain(
                                  domain->context, domain->device)) {
        return cudaErrorNotPermitted;
    }
    const auto result = original();
    if (result == cudaSuccess && domain.has_value()) {
        erase_context_state(domain->context);
    }
    return result;
}
#endif

namespace {

template <typename Function> void* wrapper_address(Function function)
{
    return reinterpret_cast<void*>(function);
}

void* interposed_wrapper_address(const char* symbol, bool per_thread)
{
    if (symbol == nullptr) {
        return nullptr;
    }
    struct WrapperPair {
        const char* name;
        void* legacy;
        void* per_thread;
    };
    const WrapperPair wrappers[] = {
        {"cuLaunch", wrapper_address(&cuLaunch), nullptr},
        {"cuLaunchGrid", wrapper_address(&cuLaunchGrid), nullptr},
        {"cuLaunchGridAsync", wrapper_address(&cuLaunchGridAsync), nullptr},
        {"cuLaunchKernel", wrapper_address(&cuLaunchKernel),
         wrapper_address(&cuLaunchKernel_ptsz)},
        {"cuLaunchKernel_ptsz", wrapper_address(&cuLaunchKernel_ptsz),
         wrapper_address(&cuLaunchKernel_ptsz)},
        {"cuLaunchKernelEx", wrapper_address(&cuLaunchKernelEx),
         wrapper_address(&cuLaunchKernelEx_ptsz)},
        {"cuLaunchKernelEx_ptsz", wrapper_address(&cuLaunchKernelEx_ptsz),
         wrapper_address(&cuLaunchKernelEx_ptsz)},
        {"cuLaunchCooperativeKernel",
         wrapper_address(&cuLaunchCooperativeKernel),
         wrapper_address(&cuLaunchCooperativeKernel_ptsz)},
        {"cuLaunchCooperativeKernel_ptsz",
         wrapper_address(&cuLaunchCooperativeKernel_ptsz),
         wrapper_address(&cuLaunchCooperativeKernel_ptsz)},
        {"cuLaunchCooperativeKernelMultiDevice",
         wrapper_address(&cuLaunchCooperativeKernelMultiDevice), nullptr},
        {"cuGraphLaunch", wrapper_address(&cuGraphLaunch),
         wrapper_address(&cuGraphLaunch_ptsz)},
        {"cuGraphLaunch_ptsz", wrapper_address(&cuGraphLaunch_ptsz),
         wrapper_address(&cuGraphLaunch_ptsz)},
        {"cuModuleLoadDataEx", wrapper_address(&cuModuleLoadDataEx), nullptr},
        {"cuModuleUnload", wrapper_address(&cuModuleUnload), nullptr},
        {"cuCtxDestroy", wrapper_address(&cuCtxDestroy), nullptr},
        {"cuCtxDestroy_v2", wrapper_address(&cuCtxDestroy_v2), nullptr},
        {"cuCtxDetach", wrapper_address(&cuCtxDetach), nullptr},
        {"cuDevicePrimaryCtxReset", wrapper_address(&cuDevicePrimaryCtxReset),
         nullptr},
        {"cuDevicePrimaryCtxReset_v2",
         wrapper_address(&cuDevicePrimaryCtxReset_v2), nullptr},
        {"cuDevicePrimaryCtxRelease",
         wrapper_address(&cuDevicePrimaryCtxRelease), nullptr},
        {"cuDevicePrimaryCtxRelease_v2",
         wrapper_address(&cuDevicePrimaryCtxRelease_v2), nullptr},
#if CUDA_VERSION >= 12040
        {"cuGreenCtxDestroy", wrapper_address(&cuGreenCtxDestroy), nullptr},
#endif
    };
    for (const auto& wrapper : wrappers) {
        if (std::strcmp(symbol, wrapper.name) == 0) {
            return per_thread && wrapper.per_thread != nullptr
                       ? wrapper.per_thread
                       : wrapper.legacy;
        }
    }
    return nullptr;
}

void substitute_gated_launch(const char* symbol, void** function,
                             bool per_thread)
{
    if (function == nullptr || *function == nullptr) {
        return;
    }
    if (void* replacement = interposed_wrapper_address(symbol, per_thread)) {
        *function = replacement;
    }
}

}  // namespace

extern "C" CUresult cuGetProcAddress(const char* symbol, void** function,
                                     int cuda_version, cuuint64_t flags)
{
    using type = CUresult (*)(const char*, void**, int, cuuint64_t);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuGetProcAddress"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    const auto result = original(symbol, function, cuda_version, flags);
    if (result == CUDA_SUCCESS) {
        substitute_gated_launch(
            symbol, function,
            (flags & CU_GET_PROC_ADDRESS_PER_THREAD_DEFAULT_STREAM) != 0);
    }
    return result;
}

extern "C" CUresult cuGetProcAddress_v2(const char* symbol, void** function,
                                        int cuda_version, cuuint64_t flags,
                                        CUdriverProcAddressQueryResult* status)
{
    using type = CUresult (*)(const char*, void**, int, cuuint64_t,
                              CUdriverProcAddressQueryResult*);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuGetProcAddress_v2"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    const auto result = original(symbol, function, cuda_version, flags, status);
    if (result == CUDA_SUCCESS) {
        substitute_gated_launch(
            symbol, function,
            (flags & CU_GET_PROC_ADDRESS_PER_THREAD_DEFAULT_STREAM) != 0);
    }
    return result;
}

namespace {

cudaError_t runtime_driver_entry_point(const char* lookup_symbol,
                                       const char* symbol, void** function,
                                       unsigned int* cuda_version,
                                       unsigned long long flags,
                                       cudaDriverEntryPointQueryResult* status)
{
    cudaError_t result = cudaErrorInitializationError;
    if (cuda_version == nullptr) {
        using type = cudaError_t (*)(const char*, void**, unsigned long long,
                                     cudaDriverEntryPointQueryResult*);
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, lookup_symbol));
        if (original != nullptr) {
            result = original(symbol, function, flags, status);
        }
    } else {
        using type = cudaError_t (*)(const char*, void**, unsigned int,
                                     unsigned long long,
                                     cudaDriverEntryPointQueryResult*);
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, lookup_symbol));
        if (original != nullptr) {
            result = original(symbol, function, *cuda_version, flags, status);
        }
    }
    if (result == cudaSuccess) {
        const std::size_t lookup_length = std::strlen(lookup_symbol);
        const bool lookup_is_ptsz =
            lookup_length >= 5 &&
            std::strcmp(lookup_symbol + lookup_length - 5, "_ptsz") == 0;
        substitute_gated_launch(
            symbol, function,
            lookup_is_ptsz || (flags & cudaEnablePerThreadDefaultStream) != 0);
    }
    return result;
}

}  // namespace

#define HBFSIM_RUNTIME_DRIVER_ENTRY(name)                                      \
    extern "C" cudaError_t name(const char* symbol, void** function,           \
                                unsigned long long flags,                      \
                                cudaDriverEntryPointQueryResult* status)       \
    {                                                                          \
        return runtime_driver_entry_point(#name, symbol, function, nullptr,    \
                                          flags, status);                      \
    }

HBFSIM_RUNTIME_DRIVER_ENTRY(cudaGetDriverEntryPoint)
HBFSIM_RUNTIME_DRIVER_ENTRY(cudaGetDriverEntryPoint_ptsz)

#define HBFSIM_RUNTIME_DRIVER_ENTRY_VERSIONED(name)                            \
    extern "C" cudaError_t name(                                               \
        const char* symbol, void** function, unsigned int cuda_version,        \
        unsigned long long flags, cudaDriverEntryPointQueryResult* status)     \
    {                                                                          \
        return runtime_driver_entry_point(#name, symbol, function,             \
                                          &cuda_version, flags, status);       \
    }

HBFSIM_RUNTIME_DRIVER_ENTRY_VERSIONED(cudaGetDriverEntryPointByVersion)
HBFSIM_RUNTIME_DRIVER_ENTRY_VERSIONED(cudaGetDriverEntryPointByVersion_ptsz)

// Triton 3.5 resolves cuLaunchKernelEx from a private libcuda handle. A normal
// LD_PRELOAD export and CUDA's cuGetProcAddress interposition cannot see that
// handle-specific lookup. Pin one libcuda object and substitute only when the
// caller lookup resolves to the corresponding entry in that exact object.
extern "C" void* dlsym(void* handle, const char* symbol)
{
    auto lookup = real_dlsym();
    if (lookup == nullptr) {
        return nullptr;
    }
    void* resolved = lookup(handle, symbol);
    if (handle == RTLD_NEXT || resolved == nullptr || symbol == nullptr ||
        ex_driver_resolution_in_progress) {
        return resolved;
    }
    if (std::strcmp(symbol, "cuLaunchKernelEx") != 0 &&
        std::strcmp(symbol, "cuLaunchKernelEx_ptsz") != 0) {
        return resolved;
    }
    const auto& entrypoints = ex_driver_entrypoints();
    if (std::strcmp(symbol, "cuLaunchKernelEx") == 0) {
        if (resolved == reinterpret_cast<void*>(entrypoints.legacy)) {
            return wrapper_address(&cuLaunchKernelEx);
        }
    } else if (std::strcmp(symbol, "cuLaunchKernelEx_ptsz") == 0) {
        if (resolved == reinterpret_cast<void*>(entrypoints.per_thread)) {
            return wrapper_address(&cuLaunchKernelEx_ptsz);
        }
    }
    return resolved;
}
