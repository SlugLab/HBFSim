#include "hbfsim/coverage.hpp"
#include "hbfsim/launch_gate_abi.hpp"
#include "hbfsim/module_identity.hpp"
#include "hbfsim/timing_binding.hpp"
#include "../ptxpass_hbf/transform.hpp"

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>

#include <atomic>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <sstream>
#include <set>
#include <utility>

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
            gate_.add_range(begin, end, policy);
            if (publish(publish_state) != 0) {
                gate_.remove_range(begin, end);
                return -1;
            }
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
        range_launch_sync_.reset_launch_seen();
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
    timing_bindings().erase_context(cuda_context, erase_module_identity,
                                    nullptr);
}

void erase_unbound_device_state(int device_ordinal) noexcept
{
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
    if (decision.allowed && decision.modeled && decision.address != 0 &&
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

template <typename Handle, typename GetInfo>
hbfsim::GateDecision
inspect_bounded(Handle handle, std::string runtime_module_id,
                std::string kernel, void** arguments, GetInfo get_info)
{
    auto& runtime = runtime_gate();
    runtime.refresh_manifests();
    if (arguments == nullptr || get_info == nullptr) {
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
        // CUDA guarantees arguments[index] points to exactly width bytes. Only
        // a single pointer-width scalar is read. Wider values are opaque and
        // fail closed while any HBF range is active; no aggregate slot
        // guessing.
        if (width == sizeof(std::uintptr_t) && arguments[index] != nullptr) {
            std::uintptr_t value = 0;
            std::memcpy(&value, arguments[index], sizeof(value));
            parameter.slots.push_back({.offset = 0, .value = value});
        }
        launch.parameters.push_back(std::move(parameter));
    }
    auto decision = runtime.gate().check_launch(launch);
    if (decision.module_id.empty()) {
        decision.module_id = std::move(runtime_module_id);
    }
    return decision;
}

hbfsim::GateDecision inspect_function_launch(CUfunction function,
                                             void** arguments, void** extra)
{
    const auto module_id = handle_id(function);
    const auto kernel = function_name(function);
    if (extra != nullptr) {
        const auto saved=future_geometry;future_geometry.reset();
        const auto result=require_timing_binding(runtime_gate().gate().has_ranges()
                   ? unavailable(module_id, kernel)
                   : hbfsim::GateDecision{.allowed=true,.module_id=module_id,.kernel=kernel},function);
        future_geometry=saved;return result;
    }
    using get_info_type =
        CUresult (*)(CUfunction, std::size_t, std::size_t*, std::size_t*);
    static auto get_info =
        reinterpret_cast<get_info_type>(driver_symbol("cuFuncGetParamInfo"));
    return require_timing_binding(
        inspect_bounded(function, module_id, kernel, arguments, get_info),
        function);
}

hbfsim::GateDecision inspect_kernel_launch(cudaKernel_t kernel,
                                           void** arguments)
{
    const CUfunction function = kernel_function(kernel);
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
                        "cuda-module:unknown", name, arguments, get_info),
        function);
}

hbfsim::GateDecision inspect_symbol_launch(const void* symbol, void** arguments)
{
    using get_function_type = cudaError_t (*)(cudaFunction_t*, const void*);
    static auto get_function = reinterpret_cast<get_function_type>(
        dlsym(RTLD_NEXT, "cudaGetFuncBySymbol"));
    cudaFunction_t runtime_function = nullptr;
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
    return inspect_function_launch(
        reinterpret_cast<CUfunction>(runtime_function), arguments, nullptr);
}

thread_local bool runtime_launch_in_progress = false;
struct RuntimeLaunchScope {
    RuntimeLaunchScope()
    {
        runtime_launch_in_progress = true;
    }
    ~RuntimeLaunchScope()
    {
        runtime_launch_in_progress = false;
    }
};

bool approve(const hbfsim::GateDecision& decision)
{
    if (!runtime_gate().approve(decision)) {
        return false;
    }
    runtime_gate().mark_launch_seen();
    return true;
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
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    if (runtime_launch_in_progress) {
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,
                        block_z, shared_memory, stream, parameters, extra);
    }
    auto launch_guard = runtime_gate().launch_guard();
    FutureGeometryScope geometry({{grid_x,grid_y,grid_z},{block_x,block_y,block_z}});
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        return CUDA_ERROR_NOT_SUPPORTED;
    }
    return original(function, grid_x, grid_y, grid_z, block_x, block_y, block_z,
                    shared_memory, stream, parameters, extra);
}

using runtime_launch_type = cudaError_t (*)(const void*, dim3, dim3, void**,
                                            std::size_t, cudaStream_t);

cudaError_t runtime_launch(const char* symbol, const void* function, dim3 grid,
                           dim3 block, void** arguments,
                           std::size_t shared_memory, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<runtime_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    auto launch_guard = runtime_gate().launch_guard();
    FutureGeometryScope geometry({{grid.x,grid.y,grid.z},{block.x,block.y,block.z}});
    // Geometry alone does not admit cooperative runtime semantics. This
    // dispatch also serves those wrappers, which remain unsupported for futures.
    if(std::strcmp(symbol,"cudaLaunchKernel")!=0 &&
       std::strcmp(symbol,"cudaLaunchKernel_ptsz")!=0)future_geometry.reset();
    const auto decision = inspect_symbol_launch(function, arguments);
    if (!approve(decision)) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(function, grid, block, arguments, shared_memory, stream);
}

using kernel_launch_type = cudaError_t (*)(cudaKernel_t, dim3, dim3, void**,
                                           std::size_t, cudaStream_t);

cudaError_t kernel_launch(const char* symbol, cudaKernel_t kernel, dim3 grid,
                          dim3 block, void** arguments,
                          std::size_t shared_memory, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<kernel_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    auto launch_guard = runtime_gate().launch_guard();
    FutureGeometryScope geometry({{grid.x,grid.y,grid.z},{block.x,block.y,block.z}});
    const auto decision = inspect_kernel_launch(kernel, arguments);
    if (!approve(decision)) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(kernel, grid, block, arguments, shared_memory, stream);
}

}  // namespace

#if defined(HBFSIM_ENABLE_TEST_HOOKS)
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
        if (runtime_launch_in_progress)                                        \
            return original(function, grid_x, grid_y, grid_z, block_x,         \
                            block_y, block_z, shared_memory, stream,           \
                            parameters);                                       \
        auto guard = runtime_gate().launch_guard();                            \
        if (!approve(inspect_function_launch(function, parameters, nullptr)))  \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,    \
                        block_z, shared_memory, stream, parameters);           \
    }
HBFSIM_DRIVER_COOPERATIVE(cuLaunchCooperativeKernel)
HBFSIM_DRIVER_COOPERATIVE(cuLaunchCooperativeKernel_ptsz)

#define HBFSIM_RUNTIME_LAUNCH(name)                                            \
    extern "C" cudaError_t name(const void* function, dim3 grid, dim3 block,   \
                                void** arguments, std::size_t shared_memory,   \
                                cudaStream_t stream)                           \
    {                                                                          \
        return runtime_launch(#name, function, grid, block, arguments,         \
                              shared_memory, stream);                          \
    }

HBFSIM_RUNTIME_LAUNCH(cudaLaunchKernel)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchKernel_ptsz)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchCooperativeKernel)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchCooperativeKernel_ptsz)

#define HBFSIM_KERNEL_LAUNCH(name)                                             \
    extern "C" cudaError_t name(cudaKernel_t kernel, dim3 grid, dim3 block,    \
                                void** arguments, std::size_t shared_memory,   \
                                cudaStream_t stream)                           \
    {                                                                          \
        return kernel_launch(#name, kernel, grid, block, arguments,            \
                             shared_memory, stream);                           \
    }

HBFSIM_KERNEL_LAUNCH(__cudaLaunchKernel)
HBFSIM_KERNEL_LAUNCH(__cudaLaunchKernel_ptsz)

#define HBFSIM_DRIVER_EX(name)                                                 \
    extern "C" CUresult name(const CUlaunchConfig* config,                     \
                             CUfunction function, void** parameters,           \
                             void** extra)                                     \
    {                                                                          \
        using type =                                                           \
            CUresult (*)(const CUlaunchConfig*, CUfunction, void**, void**);   \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return CUDA_ERROR_NOT_INITIALIZED;                                 \
        if (runtime_launch_in_progress)                                        \
            return original(config, function, parameters, extra);              \
        auto guard = runtime_gate().launch_guard();                            \
        const auto decision =                                                  \
            inspect_function_launch(function, parameters, extra);              \
        if (!approve(decision))                                                \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        RuntimeLaunchScope scope;                                              \
        return original(config, function, parameters, extra);                  \
    }
HBFSIM_DRIVER_EX(cuLaunchKernelEx)
HBFSIM_DRIVER_EX(cuLaunchKernelEx_ptsz)

#define HBFSIM_RUNTIME_EX(name)                                                \
    extern "C" cudaError_t name(const cudaLaunchConfig_t* config,              \
                                const void* function, void** arguments)        \
    {                                                                          \
        using type =                                                           \
            cudaError_t (*)(const cudaLaunchConfig_t*, const void*, void**);   \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return cudaErrorInitializationError;                               \
        auto guard = runtime_gate().launch_guard();                            \
        const auto decision = inspect_symbol_launch(function, arguments);      \
        if (!approve(decision))                                                \
            return cudaErrorNotSupported;                                      \
        RuntimeLaunchScope scope;                                              \
        return original(config, function, arguments);                          \
    }
HBFSIM_RUNTIME_EX(cudaLaunchKernelExC)
HBFSIM_RUNTIME_EX(cudaLaunchKernelExC_ptsz)

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
        if (runtime_launch_in_progress)                                        \
            return original(graph, stream);                                    \
        auto guard = runtime_gate().launch_guard();                            \
        return approve(opaque_launch("graph_launch"))                          \
                   ? original(graph, stream)                                   \
                   : CUDA_ERROR_NOT_SUPPORTED;                                 \
    }
HBFSIM_DRIVER_GRAPH(cuGraphLaunch)
HBFSIM_DRIVER_GRAPH(cuGraphLaunch_ptsz)

#define HBFSIM_RUNTIME_GRAPH(name)                                             \
    extern "C" cudaError_t name(cudaGraphExec_t graph, cudaStream_t stream)    \
    {                                                                          \
        using type = cudaError_t (*)(cudaGraphExec_t, cudaStream_t);           \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return cudaErrorInitializationError;                               \
        auto guard = runtime_gate().launch_guard();                            \
        if (!approve(opaque_launch("graph_launch")))                           \
            return cudaErrorNotSupported;                                      \
        RuntimeLaunchScope scope;                                              \
        return original(graph, stream);                                        \
    }
HBFSIM_RUNTIME_GRAPH(cudaGraphLaunch)
HBFSIM_RUNTIME_GRAPH(cudaGraphLaunch_ptsz)

extern "C" CUresult
cuLaunchCooperativeKernelMultiDevice(CUDA_LAUNCH_PARAMS* launches,
                                     unsigned int count, unsigned int flags)
{
    using type = CUresult (*)(CUDA_LAUNCH_PARAMS*, unsigned int, unsigned int);
    auto original = reinterpret_cast<type>(
        dlsym(RTLD_NEXT, "cuLaunchCooperativeKernelMultiDevice"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress)
        return original(launches, count, flags);
    auto guard = runtime_gate().launch_guard();
    if (launches == nullptr)
        return CUDA_ERROR_INVALID_VALUE;
    for (unsigned int index = 0; index < count; ++index) {
        if (!approve(inspect_function_launch(launches[index].function,
                                             launches[index].kernelParams,
                                             nullptr))) {
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
    auto guard = runtime_gate().launch_guard();
    if (launches == nullptr)
        return cudaErrorInvalidValue;
    for (unsigned int index = 0; index < count; ++index) {
        if (!approve(inspect_symbol_launch(launches[index].func,
                                           launches[index].args))) {
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
        return 1;
    }
    auto guard = runtime_gate().launch_guard();
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        return 0;
    }
    return decision.modeled ? 2 : 1;
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
// handle-specific lookup, so substitute only these launch entry points. Calls
// made by this gate use RTLD_NEXT and continue to resolve the real driver.
extern "C" void* dlsym(void* handle, const char* symbol)
{
    using type = void* (*)(void*, const char*);
    static auto original = reinterpret_cast<type>(
        dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5"));
    if (original == nullptr) {
        return nullptr;
    }
    void* resolved = original(handle, symbol);
    if (handle != RTLD_NEXT && resolved != nullptr) {
        if (std::strcmp(symbol, "cuLaunchKernelEx") == 0) {
            return wrapper_address(&cuLaunchKernelEx);
        }
        if (std::strcmp(symbol, "cuLaunchKernelEx_ptsz") == 0) {
            return wrapper_address(&cuLaunchKernelEx_ptsz);
        }
    }
    return resolved;
}
