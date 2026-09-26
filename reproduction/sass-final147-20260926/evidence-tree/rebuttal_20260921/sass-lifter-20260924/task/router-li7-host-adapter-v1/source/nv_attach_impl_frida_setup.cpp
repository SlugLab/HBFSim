// #include "pos/cuda_impl/utils/fatbin.h"
#include "cuda.h"
#include "cuda_runtime_api.h"
#include "driver_types.h"
#include "spdlog/spdlog.h"
#include "vector_types.h"
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <mutex>
#include <dlfcn.h>
#include <frida-gum.h>
#include <iterator>
#include <memory>
#include <optional>
#include <vector>
#include "nv_attach_impl.hpp"
#include "nv_attach_utils.hpp"
#include "strict_launch_policy.hpp"
#include "qkv_exact_abi_adapter.hpp"
#include "qkv_live_identity_v1.hpp"
#include <stdexcept>
using namespace bpftime;
using namespace attach;

#define CUDA_DRIVER_CHECK_EXCEPTION(expr, message)                             \
	do {                                                                   \
		if (auto err = expr; err != CUDA_SUCCESS) {                    \
			SPDLOG_ERROR("{}: {}", message, (int)err);             \
			throw std::runtime_error(message);                     \
		}                                                              \
	} while (false)

extern "C" {

typedef struct __attribute__((__packed__)) fat_elf_header {
	uint32_t magic;
	uint16_t version;
	uint16_t header_size;
	uint64_t size;
} fat_elf_header_t;
}

typedef struct _CUDARuntimeFunctionHooker {
	GObject parent;
} CUDARuntimeFunctionHooker;

struct pending_fatbin_registration {
	RuntimeRegistrationDomain domain = RuntimeRegistrationDomain::Unknown;
	fatbin_record *record = nullptr;
	void *wrapper = nullptr;
};
static thread_local std::vector<pending_fatbin_registration>
	pending_fatbin_registrations;

static int registration_domain_id(RuntimeRegistrationDomain domain)
{
	return static_cast<int>(domain);
}

static const char *registration_domain_name(RuntimeRegistrationDomain domain)
{
	switch (domain) {
	case RuntimeRegistrationDomain::Cudart12: return "cudart12";
	case RuntimeRegistrationDomain::Cudart13: return "cudart13";
	default: return "unknown";
	}
}

static std::string registration_json_escape(const char *text)
{
	std::string result;
	for (const unsigned char *p = reinterpret_cast<const unsigned char *>(
		     text != nullptr ? text : "");
	     *p != 0; ++p) {
		switch (*p) {
		case '\\': result += "\\\\"; break;
		case '"': result += "\\\""; break;
		case '\n': result += "\\n"; break;
		case '\r': result += "\\r"; break;
		case '\t': result += "\\t"; break;
		default:
			if (*p >= 0x20) result.push_back(static_cast<char>(*p));
		}
	}
	return result;
}

static void native_registration_log(RuntimeRegistrationDomain domain,
				    const char *event, const char *status,
				    void *handle, void *object,
				    std::uint64_t generation,
				    const char *symbol)
{
	const char *path = std::getenv("HBFSIM_NATIVE_REGISTRATION_LOG_PATH");
	if (path == nullptr || path[0] == '\0') return;
	Dl_info info{};
	const char *dso = object != nullptr && dladdr(object, &info) != 0 &&
				       info.dli_fname != nullptr
			  ? info.dli_fname : "";
	static std::mutex log_mutex;
	std::lock_guard<std::mutex> guard(log_mutex);
	std::ofstream out(path, std::ios::app);
	if (!out) return;
	out << "{\"schema_version\":1,\"event\":\""
	    << registration_json_escape(event) << "\",\"status\":\""
	    << registration_json_escape(status) << "\",\"runtime_domain\":\""
	    << registration_domain_name(domain) << "\",\"fatbin_handle\":\"0x"
	    << std::hex << reinterpret_cast<std::uintptr_t>(handle)
	    << "\",\"object\":\"0x"
	    << reinterpret_cast<std::uintptr_t>(object) << std::dec
	    << "\",\"generation\":" << generation << ",\"symbol\":\""
	    << registration_json_escape(symbol) << "\",\"object_dso\":\""
	    << registration_json_escape(dso) << "\"}\n";
	out.flush();
}

static void cuda_runtime_function_hooker_iface_init(gpointer g_iface,
						    gpointer iface_data);

// #define EXAMPLE_TYPE_LISTENER (cuda_runtime_function_hooker_iface_init())
G_DECLARE_FINAL_TYPE(CUDARuntimeFunctionHooker, cuda_runtime_function_hooker,
		     BPFTIME, NV_ATTACH_IMPL, GObject)
G_DEFINE_TYPE_EXTENDED(
	CUDARuntimeFunctionHooker, cuda_runtime_function_hooker, G_TYPE_OBJECT,
	0,
	G_IMPLEMENT_INTERFACE(GUM_TYPE_INVOCATION_LISTENER,
			      cuda_runtime_function_hooker_iface_init))

using cu_graph_add_kernel_node_v1_fn_t =
	CUresult (*)(CUgraphNode *, CUgraph, const CUgraphNode *, size_t,
		     const CUDA_KERNEL_NODE_PARAMS_v1 *);
using cu_graph_add_kernel_node_v2_fn_t = decltype(&cuGraphAddKernelNode_v2);
using cu_graph_exec_kernel_node_set_params_v1_fn_t = CUresult (*)(
	CUgraphExec, CUgraphNode, const CUDA_KERNEL_NODE_PARAMS_v1 *);
using cu_graph_exec_kernel_node_set_params_v2_fn_t =
	decltype(&cuGraphExecKernelNodeSetParams_v2);
using cu_graph_kernel_node_set_params_v1_fn_t =
	CUresult (*)(CUgraphNode, const CUDA_KERNEL_NODE_PARAMS_v1 *);
using cu_graph_kernel_node_set_params_v2_fn_t =
	decltype(&cuGraphKernelNodeSetParams_v2);
using cuda_memcpy_from_symbol_async_fn_t = decltype(&cudaMemcpyFromSymbolAsync);
using cuda_memcpy_from_symbol_fn_t = decltype(&cudaMemcpyFromSymbol);

using cuda_launch_kernel_fn_t = cudaError_t (*)(const void *, dim3, dim3,
						void **, size_t, cudaStream_t);
using cuda_private_launch_kernel_fn_t = cudaError_t (*)(
	cudaKernel_t, dim3, dim3, void **, size_t, cudaStream_t);
using cu_launch_kernel_fn_t = CUresult (*)(CUfunction, unsigned int,
					   unsigned int, unsigned int,
					   unsigned int, unsigned int,
					   unsigned int, unsigned int, CUstream,
					   void **, void **);
using cu_launch_kernel_ex_fn_t = CUresult (*)(const CUlaunchConfig *,
					      CUfunction, void **, void **);

// Strict runtime launches must reach the strict driver bridge on the same
// thread.  A stack of frames keeps nested runtime launches isolated.
struct strict_runtime_driver_frame {
	const void *host_stub = nullptr;
	const void *kernel_handle = nullptr;
	const char *api = "cudaLaunchKernel";
	const char *argument_kind = "host_stub";
	const char *runtime_domain = "unversioned";
	std::uint64_t call_id = 0;
	std::uint64_t parent_call_id = 0;
	unsigned int driver_calls = 0;
	int gate_decision = 0;
	CUfunction original_function = nullptr;
	CUfunction selected_function = nullptr;
	CUresult driver_result = CUDA_ERROR_UNKNOWN;
	bool exact_alias_found = false;
	bool original_completion = false;
	int gate_runtime_result = static_cast<int>(cudaErrorUnknown);
};

static thread_local std::vector<strict_runtime_driver_frame *>
	strict_runtime_driver_frames;
static std::atomic<std::uint64_t> strict_runtime_next_call_id{ 1 };

static bool strict_launch_bridge_enabled()
{
	const char *value = std::getenv("HBFSIM_INSTRUMENTATION_POLICY");
	return value != nullptr && std::strcmp(value, "strict") == 0;
}

class strict_runtime_driver_scope {
      public:
	explicit strict_runtime_driver_scope(strict_runtime_driver_frame &frame)
	{
		strict_runtime_driver_frames.push_back(&frame);
	}
	~strict_runtime_driver_scope()
	{
		if (!strict_runtime_driver_frames.empty())
			strict_runtime_driver_frames.pop_back();
	}
	strict_runtime_driver_scope(const strict_runtime_driver_scope &) = delete;
	strict_runtime_driver_scope &
	operator=(const strict_runtime_driver_scope &) = delete;
};

static void strict_runtime_log(const strict_runtime_driver_frame &frame,
			       cudaError_t runtime_result,
			       const char *outcome)
{
	const char *path = std::getenv("HBFSIM_STRICT_RUNTIME_LOG_PATH");
	if (path == nullptr || path[0] == '\0')
		return;
	static std::mutex log_mutex;
	std::lock_guard<std::mutex> guard(log_mutex);
	FILE *stream = std::fopen(path, "a");
	if (stream == nullptr)
		return;
	auto pointer_text = [](const void *pointer, char *buffer,
			       std::size_t capacity) {
		if (pointer == nullptr)
			std::snprintf(buffer, capacity, "null");
		else
			std::snprintf(buffer, capacity, "\"0x%llx\"",
				      static_cast<unsigned long long>(
					      reinterpret_cast<std::uintptr_t>(pointer)));
	};
	char host_stub[40] = {}, kernel_handle[40] = {}, original_function[40] = {},
	     selected_function[40] = {};
	pointer_text(frame.host_stub, host_stub, sizeof(host_stub));
	pointer_text(frame.kernel_handle, kernel_handle, sizeof(kernel_handle));
	pointer_text(frame.original_function, original_function,
		     sizeof(original_function));
	pointer_text(frame.selected_function, selected_function,
		     sizeof(selected_function));
	std::fprintf(
		stream,
		"{\"schema_version\":1,\"api\":\"%s\",\"runtime_domain\":\"%s\","
		"\"argument_kind\":\"%s\",\"call_id\":%llu,"
		"\"parent_call_id\":%llu,\"host_stub\":%s,\"kernel_handle\":%s,"
		"\"driver_calls\":%u,\"gate_decision\":%d,"
		"\"original_function\":%s,\"selected_function\":%s,"
		"\"exact_alias_found\":%s,\"driver_result\":%d,"
		"\"original_completion\":%s,\"gate_runtime_result\":%d,"
		"\"runtime_result\":%d,\"outcome\":\"%s\"}\n",
		frame.api, frame.runtime_domain, frame.argument_kind,
		static_cast<unsigned long long>(frame.call_id),
		static_cast<unsigned long long>(frame.parent_call_id),
		host_stub, kernel_handle, frame.driver_calls, frame.gate_decision,
		original_function, selected_function,
		frame.exact_alias_found ? "true" : "false",
		static_cast<int>(frame.driver_result),
		frame.original_completion ? "true" : "false",
		frame.gate_runtime_result,
		static_cast<int>(runtime_result), outcome);
	std::fflush(stream);
	std::fclose(stream);
}

static void strict_runtime_note_driver(int gate_decision,
				       CUfunction original_function,
				       CUfunction selected_function,
				       bool exact_alias_found, CUresult result)
{
	if (strict_runtime_driver_frames.empty())
		return;
	auto *frame = strict_runtime_driver_frames.back();
	frame->driver_calls += 1;
	frame->gate_decision = gate_decision;
	frame->original_function = original_function;
	frame->selected_function = selected_function;
	frame->exact_alias_found = exact_alias_found;
	frame->driver_result = result;
}

struct strict_runtime_validation {
	cudaError_t result;
	const char *outcome;
};

static strict_runtime_validation
strict_runtime_validate(const strict_runtime_driver_frame &frame,
			cudaError_t runtime_result)
{
	if (frame.driver_calls == 0 && frame.original_completion &&
	    frame.gate_decision == 1 &&
	    frame.gate_runtime_result == static_cast<int>(runtime_result))
		return { runtime_result,
			runtime_result == cudaSuccess ? "ORIGINAL" :
				"ORIGINAL_RUNTIME_ERROR" };
	if (frame.driver_calls != 1 || frame.gate_decision == 0)
		return { cudaErrorNotSupported, "REJECTED_DRIVER_CORRELATION" };
	if (frame.gate_decision == 2 &&
	    (!frame.exact_alias_found ||
	     frame.selected_function == frame.original_function))
		return { cudaErrorNotSupported, "REJECTED_EXACT_ALIAS" };
	if (frame.driver_result != CUDA_SUCCESS && runtime_result == cudaSuccess)
		return { cudaErrorUnknown, "REJECTED_DRIVER_RESULT" };
	return { runtime_result,
		 frame.gate_decision == 2 ? "PATCHED" : "ORIGINAL" };
}

using strict_original_begin_fn = std::uint64_t (*)(const void*, int, int);
using strict_original_consume_fn = int (*)(std::uint64_t, const void*, int,
					    int, int, int*, int*);
struct strict_original_roundtrip_api {
	strict_original_begin_fn begin = nullptr;
	strict_original_consume_fn consume = nullptr;
	explicit operator bool() const { return begin != nullptr && consume != nullptr; }
};
static strict_original_roundtrip_api strict_original_roundtrip_resolve()
{
	return {
		reinterpret_cast<strict_original_begin_fn>(
			dlsym(RTLD_DEFAULT, "hbfsim_strict_original_begin_v1")),
		reinterpret_cast<strict_original_consume_fn>(
			dlsym(RTLD_DEFAULT, "hbfsim_strict_original_consume_v1"))
	};
}
static bool strict_original_roundtrip_begin(
	const strict_original_roundtrip_api &api, const void *function, int domain,
	int api_kind, std::uint64_t &token)
{
	if (!api) return false;
	token = api.begin(function, domain, api_kind);
	return token != 0;
}
static void strict_original_roundtrip_consume(
	const strict_original_roundtrip_api &api,
	strict_runtime_driver_frame &frame, std::uint64_t token,
	const void *function, int domain, int api_kind, cudaError_t observed)
{
	if (!api) return;
	int decision = 0, gate_result = static_cast<int>(cudaErrorUnknown);
	if (api.consume(token, function, domain, api_kind,
		    static_cast<int>(observed), &decision, &gate_result) == 1) {
		frame.original_completion = true;
		frame.gate_decision = decision;
		frame.gate_runtime_result = gate_result;
	}
}

static bool cuda_graph_stream_is_capturing(cudaStream_t stream)
{
	cudaStreamCaptureStatus status = cudaStreamCaptureStatusNone;
	auto err = cudaStreamIsCapturing(stream, &status);
	if (err != cudaSuccess) {
		SPDLOG_WARN("Call to cudaStreamIsCapturing failed: {}",
			    (int)err);
		return false;
	}
	return status != cudaStreamCaptureStatusNone;
}

static int native_partial_bind_public12(nv_attach_impl *impl,
					 const void *host_stub, CUfunction &patched);

static cudaError_t
cuda_launch_kernel_common(nv_attach_impl *impl, void *original_fn_ptr,
			  const void *func, dim3 grid_dim, dim3 block_dim,
			  void **args, size_t shared_mem, cudaStream_t stream,
			  bool native_public12 = false)
{
	auto original =
		reinterpret_cast<cuda_launch_kernel_fn_t>(original_fn_ptr);
	if (!original) {
		SPDLOG_ERROR("Original cudaLaunchKernel function is null");
		return cudaErrorUnknown;
	}
	const bool strict = strict_launch_bridge_enabled();
	if (strict) {
		strict_runtime_driver_frame frame{};
		frame.host_stub = func;
		frame.call_id = strict_runtime_next_call_id.fetch_add(
			1, std::memory_order_relaxed);
		frame.parent_call_id = strict_runtime_driver_frames.empty()
					       ? 0
					       : strict_runtime_driver_frames.back()->call_id;
		strict_runtime_driver_scope scope(frame);
		if (impl == nullptr || !impl->is_enabled() ||
		    !impl->is_late_bootstrap_done()) {
			strict_runtime_log(frame, cudaErrorNotSupported,
					   "REJECTED_BRIDGE_UNAVAILABLE");
			return cudaErrorNotSupported;
		}
		const auto result =
			original(func, grid_dim, block_dim, args, shared_mem, stream);
		const auto validation = strict_runtime_validate(frame, result);
		strict_runtime_log(frame, validation.result, validation.outcome);
		return validation.result;
	}
	if (impl == nullptr)
		return original(func, grid_dim, block_dim, args, shared_mem,
				stream);
	if (!impl->is_enabled())
		return original(func, grid_dim, block_dim, args, shared_mem,
				stream);
	if (cuda_graph_stream_is_capturing(stream))
		return original(func, grid_dim, block_dim, args, shared_mem,
				stream);

	// Ensure a CUDA context is current for this thread before calling
	// driver APIs
	{
		CUcontext current = nullptr;
		if (cuCtxGetCurrent(&current) != CUDA_SUCCESS ||
		    current == nullptr) {
			cuInit(0);
			int dev_index = 0;
			if (const char *p = getenv("BPFTIME_CUDA_DEVICE");
			    p && p[0] != '\0') {
				try {
					dev_index = std::stoi(std::string(p));
					if (dev_index < 0)
						dev_index = 0;
				} catch (...) {
					dev_index = 0;
				}
			}
			CUdevice dev = 0;
			if (cuDeviceGet(&dev, dev_index) == CUDA_SUCCESS) {
				CUcontext ctx = nullptr;
				if (cuDevicePrimaryCtxRetain(&ctx, dev) ==
					    CUDA_SUCCESS &&
				    ctx != nullptr) {
					cuCtxSetCurrent(ctx);
				}
			}
		}
	}

	// In partial mode, only a registered and manifest-selected public CUDA 12
	// stub may take the exact native binding route.  The existing name-only
	// fallback below remains for unselected kernels, but cannot certify a
	// selected weight kernel whose host symbol is local or absent from ELF.
	if (native_public12) {
		const int candidate = impl->native_binding_candidate(
			12, const_cast<void *>(func));
		if (candidate < 0) {
			SPDLOG_ERROR("Native partial binding candidate has stale or invalid identity");
			return cudaErrorNotSupported;
		}
		if (candidate == 1) {
			if (!impl->is_late_bootstrap_done()) {
				SPDLOG_ERROR("Native partial binding candidate before bootstrap");
				return cudaErrorNotSupported;
			}
			CUfunction patched = nullptr;
			if (native_partial_bind_public12(impl, func, patched) != 0 ||
			    patched == nullptr) {
				SPDLOG_ERROR("Native partial exact binding failed for host stub {:x}",
					     (uintptr_t)func);
				return cudaErrorNotSupported;
			}
			const auto err = cuLaunchKernel(
				patched, grid_dim.x, grid_dim.y, grid_dim.z,
				block_dim.x, block_dim.y, block_dim.z, shared_mem,
				stream, args, nullptr);
			if (err != CUDA_SUCCESS) {
				SPDLOG_ERROR("Native partial patched launch failed CUDA result={}",
					     (int)err);
				return cudaErrorLaunchFailure;
			}
			impl->record_patched_launch(stream);
			return cudaSuccess;
		}
	}

	const auto log_cufunc_attrs = [](CUfunction f) {
		if (f == nullptr)
			return;
		int local_bytes = 0;
		int shared_bytes = 0;
		int const_bytes = 0;
		int regs = 0;
		int max_tpb = 0;
		cuFuncGetAttribute(&local_bytes,
				   CU_FUNC_ATTRIBUTE_LOCAL_SIZE_BYTES, f);
		cuFuncGetAttribute(&shared_bytes,
				   CU_FUNC_ATTRIBUTE_SHARED_SIZE_BYTES, f);
		cuFuncGetAttribute(&const_bytes,
				   CU_FUNC_ATTRIBUTE_CONST_SIZE_BYTES, f);
		cuFuncGetAttribute(&regs, CU_FUNC_ATTRIBUTE_NUM_REGS, f);
		cuFuncGetAttribute(&max_tpb,
				   CU_FUNC_ATTRIBUTE_MAX_THREADS_PER_BLOCK, f);
		SPDLOG_WARN(
			"Patched CUfunction attrs: local={}B shared={}B const={}B regs={} max_tpb={}",
			local_bytes, shared_bytes, const_bytes, regs, max_tpb);
	};

	if (auto itr1 = impl->symbol_address_to_fatbin.find((void *)func);
	    itr1 != impl->symbol_address_to_fatbin.end()) {
		const auto &fatbin = *itr1->second;
		const auto &handle =
			fatbin.function_addr_to_symbol.at((void *)func);
		SPDLOG_DEBUG("Launching kernel..");
		if (auto err = cuLaunchKernel(
			    handle.func, grid_dim.x, grid_dim.y, grid_dim.z,
			    block_dim.x, block_dim.y, block_dim.z, shared_mem,
			    stream, args, nullptr);
		    err != CUDA_SUCCESS) {
			const char *error_name = nullptr;
			const char *error_string = nullptr;
			cuGetErrorName(err, &error_name);
			cuGetErrorString(err, &error_string);
			SPDLOG_ERROR("Unable to launch kernel: {} ({})",
				     error_name ? error_name : "UNKNOWN",
				     error_string ? error_string :
						    "No description");
			SPDLOG_ERROR("Error code: {}", (int)err);
			log_cufunc_attrs(handle.func);
			// Preserve target semantics: fall back to the original
			// runtime launch path if patched launch fails.
			return original(func, grid_dim, block_dim, args,
					shared_mem, stream);
		}
		impl->record_patched_launch(stream);
		return cudaSuccess;
	}

	// Late attach: if bootstrap hasn't completed yet, fall back to the
	// original launch path (bootstrap is kicked off during attach/refresh).
	if (!impl->is_late_bootstrap_done())
		return original(func, grid_dim, block_dim, args, shared_mem,
				stream);

	// Fallback path for late attach: resolve host stub symbol name to
	// locate the patched CUfunction mapping.
	if (auto name = impl->resolve_host_function_symbol((void *)func);
	    name) {
		if (auto patched = impl->find_patched_kernel_function(*name);
		    patched) {
			SPDLOG_DEBUG(
				"Late attach launch: resolved {} -> patched CUfunction",
				name->c_str());
			if (auto err = cuLaunchKernel(*patched, grid_dim.x,
						      grid_dim.y, grid_dim.z,
						      block_dim.x, block_dim.y,
						      block_dim.z, shared_mem,
						      stream, args, nullptr);
			    err != CUDA_SUCCESS) {
				const char *error_name = nullptr;
				const char *error_string = nullptr;
				cuGetErrorName(err, &error_name);
				cuGetErrorString(err, &error_string);
				SPDLOG_ERROR(
					"Unable to launch patched kernel {}: {} ({})",
					name->c_str(),
					error_name ? error_name : "UNKNOWN",
					error_string ? error_string :
						       "No description");
				log_cufunc_attrs(*patched);
				// Preserve target semantics: fall back to
				// original runtime launch path if patched
				// launch fails.
				return original(func, grid_dim, block_dim, args,
						shared_mem, stream);
			}
			impl->record_patched_launch(stream);
			return cudaSuccess;
		}
		SPDLOG_DEBUG(
			"Late attach launch: resolved {} but no patched CUfunction is registered",
			name->c_str());
	} else {
		SPDLOG_DEBUG(
			"Late attach launch: unable to resolve host function {:x}",
			(uintptr_t)func);
	}

	return original(func, grid_dim, block_dim, args, shared_mem, stream);
}

static cudaError_t cuda_private_launch_kernel_common(
	nv_attach_impl *impl, void *original_fn_ptr, const char *api,
	const char *runtime_domain, cudaKernel_t kernel, dim3 grid_dim,
	dim3 block_dim, void **args, size_t shared_mem, cudaStream_t stream)
{
	auto original = reinterpret_cast<cuda_private_launch_kernel_fn_t>(
		original_fn_ptr);
	if (!original) {
		SPDLOG_ERROR("Original {} ({}) function is null", api,
			     runtime_domain);
		return cudaErrorUnknown;
	}
	if (!strict_launch_bridge_enabled())
		return original(kernel, grid_dim, block_dim, args, shared_mem,
				stream);
	strict_runtime_driver_frame frame{};
	frame.kernel_handle = reinterpret_cast<const void *>(kernel);
	frame.api = api;
	frame.argument_kind = "cudaKernel_t";
	frame.runtime_domain = runtime_domain;
	frame.call_id = strict_runtime_next_call_id.fetch_add(
		1, std::memory_order_relaxed);
	frame.parent_call_id = strict_runtime_driver_frames.empty()
				       ? 0
				       : strict_runtime_driver_frames.back()->call_id;
	strict_runtime_driver_scope scope(frame);
	if (impl == nullptr || !impl->is_enabled() ||
	    !impl->is_late_bootstrap_done()) {
		strict_runtime_log(frame, cudaErrorNotSupported,
				   "REJECTED_BRIDGE_UNAVAILABLE");
		return cudaErrorNotSupported;
	}
	const int domain = std::strcmp(runtime_domain, "cudart12") == 0 ? 12 :
		(std::strcmp(runtime_domain, "cudart13") == 0 ? 13 : 0);
	const auto roundtrip = strict_original_roundtrip_resolve();
	if (domain == 0 || !roundtrip) {
		strict_runtime_log(frame, cudaErrorNotSupported,
				   "REJECTED_ORIGINAL_HANDSHAKE_MIXED_COHORT");
		return cudaErrorNotSupported;
	}
	const void *kernel_identity = reinterpret_cast<const void *>(kernel);
	std::uint64_t original_token = 0;
	if (!strict_original_roundtrip_begin(roundtrip, kernel_identity, domain, 2,
					     original_token)) {
		strict_runtime_log(frame, cudaErrorNotSupported,
				   "REJECTED_ORIGINAL_HANDSHAKE_MISSING");
		return cudaErrorNotSupported;
	}
	const auto result = original(kernel, grid_dim, block_dim, args,
				     shared_mem, stream);
	strict_original_roundtrip_consume(roundtrip, frame, original_token,
					  kernel_identity, domain, 2, result);
	const auto validation = strict_runtime_validate(frame, result);
	strict_runtime_log(frame, validation.result, validation.outcome);
	return validation.result;
}

static int native_partial_bind_public12(nv_attach_impl *impl,
					 const void *host_stub, CUfunction &patched)
{
	patched = nullptr;
	const char *build = std::getenv("HBFSIM_BUILD_DIR");
	if (build == nullptr || build[0] == '\0') return -1;
	static std::mutex gate_mutex;
	static void *gate = nullptr;
	void *gate_handle = nullptr;
	{
		std::lock_guard<std::mutex> guard(gate_mutex);
		if (gate == nullptr) {
			const auto path = std::filesystem::path(build) /
				"libhbfsim_launch_gate.so";
			gate = dlopen(path.c_str(), RTLD_NOW | RTLD_NOLOAD | RTLD_LOCAL);
		}
		gate_handle = gate;
	}
	using get_function_type = cudaError_t (*)(cudaFunction_t *, const void *);
	auto get_function = gate_handle == nullptr ? nullptr
		: reinterpret_cast<get_function_type>(
			dlvsym(gate_handle, "cudaGetFuncBySymbol", "libcudart.so.12"));
	if (get_function == nullptr) return -2;
	cudaFunction_t runtime_function = nullptr;
	if (get_function(&runtime_function, host_stub) != cudaSuccess ||
	    runtime_function == nullptr)
		return -3;
	const auto original = reinterpret_cast<CUfunction>(runtime_function);
	const int result = impl->auto_bind_registered_function(
		12, const_cast<void *>(host_stub), original);
	if (result != 0) {
		SPDLOG_ERROR("Native partial exact auto-bind failed result={}", result);
		return -4;
	}
	const auto alias = impl->find_patched_kernel_function_for_original(original);
	if (!alias || *alias == nullptr) return -5;
	patched = *alias;
	return 0;
}

static void try_native_auto_bind_public12(nv_attach_impl *impl,
					  const void *host_stub)
{
	if (impl == nullptr || host_stub == nullptr ||
	    std::getenv("HBFSIM_NATIVE_BINDING_MANIFEST_PATH") == nullptr)
		return;
	const char *build = std::getenv("HBFSIM_BUILD_DIR");
	if (build == nullptr || build[0] == '\0') return;
	static void *gate = nullptr;
	if (gate == nullptr) {
		auto path = std::filesystem::path(build) /
			    "libhbfsim_launch_gate.so";
		gate = dlopen(path.c_str(), RTLD_NOW | RTLD_NOLOAD | RTLD_LOCAL);
	}
	using get_function_type = cudaError_t (*)(cudaFunction_t *, const void *);
	auto get_function = gate == nullptr ? nullptr
		: reinterpret_cast<get_function_type>(
			dlvsym(gate, "cudaGetFuncBySymbol", "libcudart.so.12"));
	if (get_function == nullptr) return;
	cudaFunction_t runtime_function = nullptr;
	if (get_function(&runtime_function, host_stub) != cudaSuccess ||
	    runtime_function == nullptr)
		return;
	const int result = impl->auto_bind_registered_function(
		12, const_cast<void *>(host_stub),
		reinterpret_cast<CUfunction>(runtime_function));
	if (result != 0)
		SPDLOG_ERROR("Native public12 automatic binding failed result={}",
			     result);
}

static nv_attach_hook_state *current_hook_state()
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	return state == nullptr ? &nv_attach_get_hook_state() : state;
}

static cudaError_t cuda_public_launch_kernel_12_common(
	nv_attach_impl *impl, void *original_fn_ptr, const void *func,
	dim3 grid_dim, dim3 block_dim, void **args, size_t shared_mem,
	cudaStream_t stream)
{
	if (!strict_launch_bridge_enabled())
		return cuda_launch_kernel_common(impl, original_fn_ptr, func,
			grid_dim, block_dim, args, shared_mem, stream, true);
	auto original = reinterpret_cast<cuda_launch_kernel_fn_t>(original_fn_ptr);
	strict_runtime_driver_frame frame{};
	frame.host_stub = func;
	frame.api = "cudaLaunchKernel";
	frame.argument_kind = "host_stub";
	frame.runtime_domain = "cudart12";
	frame.call_id = strict_runtime_next_call_id.fetch_add(
		1, std::memory_order_relaxed);
	frame.parent_call_id = strict_runtime_driver_frames.empty()
			       ? 0
			       : strict_runtime_driver_frames.back()->call_id;
	strict_runtime_driver_scope scope(frame);
	if (original == nullptr || impl == nullptr || !impl->is_enabled() ||
	    !impl->is_late_bootstrap_done()) {
		strict_runtime_log(frame, cudaErrorNotSupported,
				   "REJECTED_BRIDGE_UNAVAILABLE");
		return cudaErrorNotSupported;
	}
	try_native_auto_bind_public12(impl, func);
	const auto roundtrip = strict_original_roundtrip_resolve();
	if (!roundtrip) {
		strict_runtime_log(frame, cudaErrorNotSupported,
				   "REJECTED_ORIGINAL_HANDSHAKE_MIXED_COHORT");
		return cudaErrorNotSupported;
	}
	std::uint64_t original_token = 0;
	if (!strict_original_roundtrip_begin(roundtrip, func, 12, 1,
					     original_token)) {
		strict_runtime_log(frame, cudaErrorNotSupported,
				   "REJECTED_ORIGINAL_HANDSHAKE_MISSING");
		return cudaErrorNotSupported;
	}
	const auto result = original(func, grid_dim, block_dim, args,
				     shared_mem, stream);
	strict_original_roundtrip_consume(roundtrip, frame, original_token, func,
					  12, 1, result);
	const auto validation = strict_runtime_validate(frame, result);
	strict_runtime_log(frame, validation.result, validation.outcome);
	return validation.result;
}

extern "C" cudaError_t cuda_runtime_function__cudaLaunchKernel_12(
	const void *func, dim3 grid_dim, dim3 block_dim, void **args,
	size_t shared_mem, cudaStream_t stream)
{
	auto *state = current_hook_state();
	return cuda_public_launch_kernel_12_common(
		state->active_impl.load(std::memory_order_acquire),
		state->orig_public_launch_kernel_12.load(std::memory_order_acquire),
		func, grid_dim, block_dim, args, shared_mem, stream);
}

#define DEFINE_PRIVATE_RUNTIME_WRAPPER(function_name, slot_name, api_name, domain_name) \
	extern "C" cudaError_t function_name(                                      \
		cudaKernel_t kernel, dim3 grid_dim, dim3 block_dim, void **args,     \
		size_t shared_mem, cudaStream_t stream)                              \
	{                                                                         \
		auto *state = current_hook_state();                                  \
		return cuda_private_launch_kernel_common(                            \
			state->active_impl.load(std::memory_order_acquire),            \
			state->slot_name.load(std::memory_order_acquire), api_name,     \
			domain_name, kernel, grid_dim, block_dim, args, shared_mem,     \
			stream);                                                        \
	}

DEFINE_PRIVATE_RUNTIME_WRAPPER(cuda_runtime_function___cudaLaunchKernel_12,
	orig_private_launch_kernel_12, "__cudaLaunchKernel", "cudart12")
DEFINE_PRIVATE_RUNTIME_WRAPPER(cuda_runtime_function___cudaLaunchKernel_13,
	orig_private_launch_kernel_13, "__cudaLaunchKernel", "cudart13")
DEFINE_PRIVATE_RUNTIME_WRAPPER(cuda_runtime_function___cudaLaunchKernel_ptsz_12,
	orig_private_launch_kernel_ptsz_12, "__cudaLaunchKernel_ptsz", "cudart12")
DEFINE_PRIVATE_RUNTIME_WRAPPER(cuda_runtime_function___cudaLaunchKernel_ptsz_13,
	orig_private_launch_kernel_ptsz_13, "__cudaLaunchKernel_ptsz", "cudart13")

#undef DEFINE_PRIVATE_RUNTIME_WRAPPER

static bool fatbin_scan_disabled()
{
	const char *disabled = getenv("BPFTIME_CUDA_DISABLE_CUOBJDUMP");
	return disabled != nullptr && disabled[0] == '1';
}

static void example_listener_on_enter(GumInvocationListener *listener,
				      GumInvocationContext *ic)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto context =
		GUM_IC_GET_FUNC_DATA(ic, CUDARuntimeFunctionHookerContext *);
	if (context->to_function == AttachedToFunction::RegisterFatbin) {
		SPDLOG_DEBUG("Entering __cudaRegisterFatBinary..");
		auto wrapper = gum_invocation_context_get_nth_argument(gum_ctx, 0);
		if (fatbin_scan_disabled()) {
			auto record = std::make_unique<struct fatbin_record>();
			record->module_pool = context->impl->module_pool;
			record->ptx_pool = context->impl->ptx_pool;
			auto *record_ptr = record.get();
			{
				std::lock_guard<std::mutex> guard(
					context->impl->fatbin_identity_mutex);
				context->impl->current_fatbin = record_ptr;
				context->impl->fatbin_records.emplace_back(
					std::move(record));
			}
			pending_fatbin_registrations.push_back(
				{ context->runtime_domain, record_ptr, wrapper });
			SPDLOG_INFO("Skipping CUDA fatbin registration scan");
			return;
		}

		auto header = (__fatBinC_Wrapper_t *)
			gum_invocation_context_get_nth_argument(gum_ctx, 0);
		auto data = (const char *)header->data;
		fat_elf_header_t *curr_header = (fat_elf_header_t *)data;
		const char *tail = (const char *)curr_header;
		while (true) {
			// #define FATBIN_TEXT_MAGIC 0xBA55ED50
			if (curr_header->magic == 0xBA55ED50) {
				SPDLOG_DEBUG(
					"Got CUBIN section header size = {}, size = {}",
					static_cast<int>(
						curr_header->header_size),
					static_cast<int>(curr_header->size));
				tail = ((const char *)curr_header) +
				       curr_header->header_size +
				       curr_header->size;
				curr_header = (fat_elf_header_t *)tail;
			} else {
				break;
			}
		};
		std::vector<uint8_t> data_vec((uint8_t *)data, (uint8_t *)tail);
		SPDLOG_INFO("Finally size = {}", data_vec.size());
		auto extracted_ptx =
			context->impl->extract_ptxs(std::move(data_vec));
		SPDLOG_INFO("Patching PTXs");
		auto fatbin_record = std::make_unique<struct fatbin_record>();
		fatbin_record->original_ptx = extracted_ptx;
		fatbin_record->module_pool = context->impl->module_pool;
		fatbin_record->ptx_pool = context->impl->ptx_pool;

		auto *record_ptr = fatbin_record.get();
		{
			std::lock_guard<std::mutex> guard(
				context->impl->fatbin_identity_mutex);
			context->impl->current_fatbin = record_ptr;
			context->impl->fatbin_records.emplace_back(
				std::move(fatbin_record));
		}
		pending_fatbin_registrations.push_back(
			{ context->runtime_domain, record_ptr, wrapper });

	} else if (context->to_function ==
		   AttachedToFunction::UnregisterFatbin) {
		auto handle = gum_invocation_context_get_nth_argument(gum_ctx, 0);
		auto &impl = *context->impl;
		const auto handle_key = std::make_pair(
			registration_domain_id(context->runtime_domain), handle);
		std::uint64_t generation = 0;
		bool removed = false;
		{
			std::lock_guard<std::mutex> guard(impl.fatbin_identity_mutex);
			auto found = impl.fatbin_handle_to_record.find(handle_key);
			if (found != impl.fatbin_handle_to_record.end()) {
				generation = found->second.generation;
				impl.fatbin_handle_to_record.erase(found);
				removed = true;
			}
			for (auto it = impl.registered_symbol_identity.begin();
			     it != impl.registered_symbol_identity.end();) {
				if (it->second.handle_key == handle_key &&
				    it->second.generation == generation) {
					impl.symbol_address_to_fatbin.erase(it->first.second);
					it = impl.registered_symbol_identity.erase(it);
				} else {
					++it;
				}
			}
		}
		native_registration_log(context->runtime_domain, "unregister_fatbin",
			removed ? "INVALIDATED" : "UNKNOWN_HANDLE", handle,
			nullptr, generation, "");
	} else if (context->to_function ==
		   AttachedToFunction::RegisterFunction) {
		SPDLOG_DEBUG("Entering __cudaRegisterFunction..");
		auto &impl = *context->impl;
		auto fatbin_handle =
			gum_invocation_context_get_nth_argument(gum_ctx, 0);
		auto func_addr =
			gum_invocation_context_get_nth_argument(gum_ctx, 1);
		auto symbol_name =
			(const char *)gum_invocation_context_get_nth_argument(
				gum_ctx, 3);
		fatbin_record *current_fatbin = nullptr;
		std::uint64_t generation = 0;
		const auto handle_key = std::make_pair(
			registration_domain_id(context->runtime_domain), fatbin_handle);
		{
			std::lock_guard<std::mutex> guard(impl.fatbin_identity_mutex);
			auto found = impl.fatbin_handle_to_record.find(handle_key);
			if (found != impl.fatbin_handle_to_record.end()) {
				current_fatbin = found->second.record;
				generation = found->second.generation;
				Dl_info host_info{};
				const char *host_dso =
					dladdr(func_addr, &host_info) != 0 &&
							host_info.dli_fname != nullptr
						? host_info.dli_fname : "";
				impl.registered_symbol_identity[std::make_pair(
					registration_domain_id(context->runtime_domain),
					func_addr)] = { handle_key, generation,
						       symbol_name != nullptr ? symbol_name : "",
						       host_dso };
			}
		}
		if (current_fatbin == nullptr) {
			native_registration_log(context->runtime_domain,
				"register_function", "UNKNOWN_HANDLE", fatbin_handle,
				func_addr, 0, symbol_name);
			return;
		}
		native_registration_log(context->runtime_domain,
			"register_function", "EXACT_HANDLE", fatbin_handle,
			func_addr, generation, symbol_name);
		if (fatbin_scan_disabled()) return;
		current_fatbin->try_loading_ptxs(*context->impl);
		if (auto ok = current_fatbin->find_and_fill_function_info(
			    func_addr, symbol_name);
		    !ok) {
			SPDLOG_WARN(
				"Unable to find_and_fill function info of symbol named {}, the PTX may not be compiled due to not modifying by nv_attach_impl",
				symbol_name);
		} else {
			context->impl->symbol_address_to_fatbin[func_addr] =
				current_fatbin;
			if (auto itr = current_fatbin->function_addr_to_symbol
					       .find(func_addr);
			    itr !=
			    current_fatbin->function_addr_to_symbol.end())
				impl.record_patched_kernel_function(
					std::string(symbol_name),
					itr->second.func);
			SPDLOG_DEBUG(
				"Registered kernel function name {} addr {:x}",
				symbol_name, (uintptr_t)func_addr);
		}

	} else if (context->to_function ==
		   AttachedToFunction::RegisterVariable) {
		SPDLOG_DEBUG("Entering __cudaRegisterVar");
		auto fatbin_handle =
			gum_invocation_context_get_nth_argument(gum_ctx, 0);
		auto var_addr =
			gum_invocation_context_get_nth_argument(gum_ctx, 1);
		auto symbol_name =
			(const char *)gum_invocation_context_get_nth_argument(
				gum_ctx, 3);
		fatbin_record *current_fatbin = nullptr;
		std::uint64_t generation = 0;
		{
			std::lock_guard<std::mutex> guard(
				context->impl->fatbin_identity_mutex);
			auto found = context->impl->fatbin_handle_to_record.find(
				{ registration_domain_id(context->runtime_domain),
				  fatbin_handle });
			if (found != context->impl->fatbin_handle_to_record.end()) {
				current_fatbin = found->second.record;
				generation = found->second.generation;
				const auto handle_key = std::make_pair(
					registration_domain_id(context->runtime_domain),
					fatbin_handle);
				Dl_info host_info{};
				const char *host_dso =
					dladdr(var_addr, &host_info) != 0 &&
							host_info.dli_fname != nullptr
						? host_info.dli_fname : "";
				context->impl->registered_symbol_identity[std::make_pair(
					registration_domain_id(context->runtime_domain),
					var_addr)] = { handle_key, generation,
						       symbol_name != nullptr ? symbol_name : "",
						       host_dso };
			}
		}
		if (current_fatbin == nullptr) {
			native_registration_log(context->runtime_domain,
				"register_variable", "UNKNOWN_HANDLE", fatbin_handle,
				var_addr, 0, symbol_name);
			return;
		}
		native_registration_log(context->runtime_domain,
			"register_variable", "EXACT_HANDLE", fatbin_handle,
			var_addr, generation, symbol_name);
		if (fatbin_scan_disabled()) return;
		current_fatbin->try_loading_ptxs(*context->impl);
		SPDLOG_DEBUG("Registering variable named {}", symbol_name);

		if (bool ok = current_fatbin->find_and_fill_variable_info(
			    var_addr, symbol_name);
		    !ok) {
			SPDLOG_WARN(
				"Unable to find_and_fill variable info of symbol names {}, the PTX may not be compiled due to not modifying by nv_attach_impl",
				symbol_name);
		} else {
			context->impl->symbol_address_to_fatbin[var_addr] =
				current_fatbin;
			SPDLOG_DEBUG("Registered variable name {} addr {:x}",
				     symbol_name, (uintptr_t)var_addr);
		}

	} else if (context->to_function ==
		   AttachedToFunction::RegisterFatbinEnd) {
		SPDLOG_DEBUG("Entering __cudaRegisterFatBinaryEnd..");
		auto &current_fatbin = context->impl->current_fatbin;

		current_fatbin = nullptr;
	} else if (context->to_function == AttachedToFunction::CudaMalloc) {
		SPDLOG_DEBUG("Entering cudaMalloc..");
	} else if (context->to_function ==
			   AttachedToFunction::CudaMemcpyToSymbol ||
		   context->to_function ==
			   AttachedToFunction::CudaMemcpyToSymbolAsync) {
		auto symbol =
			(const void *)gum_invocation_context_get_nth_argument(
				gum_ctx, 0);
		auto src =
			(const void *)gum_invocation_context_get_nth_argument(
				gum_ctx, 1);
		auto count = static_cast<size_t>(reinterpret_cast<uintptr_t>(
			gum_invocation_context_get_nth_argument(gum_ctx, 2)));
		auto offset = static_cast<size_t>(reinterpret_cast<uintptr_t>(
			gum_invocation_context_get_nth_argument(gum_ctx, 3)));
		auto kind =
			static_cast<cudaMemcpyKind>(reinterpret_cast<uintptr_t>(
				gum_invocation_context_get_nth_argument(gum_ctx,
									4)));
		cudaStream_t stream = nullptr;
		bool async = context->to_function ==
			     AttachedToFunction::CudaMemcpyToSymbolAsync;
		if (async) {
			stream = (cudaStream_t)
				gum_invocation_context_get_nth_argument(gum_ctx,
									5);
		}
		context->impl->mirror_cuda_memcpy_to_symbol(
			symbol, src, count, offset, kind, stream, async);
	}
}

static void example_listener_on_leave(GumInvocationListener *listener,
				      GumInvocationContext *ic)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto context =
		GUM_IC_GET_FUNC_DATA(ic, CUDARuntimeFunctionHookerContext *);
	if (context->to_function == AttachedToFunction::RegisterFatbin) {
		SPDLOG_DEBUG("Leaving RegisterFatbin");
		if (pending_fatbin_registrations.empty()) {
			native_registration_log(context->runtime_domain,
				"register_fatbin", "MISSING_INVOCATION_STATE", nullptr,
				nullptr, 0, "");
			return;
		}
		auto pending = pending_fatbin_registrations.back();
		pending_fatbin_registrations.pop_back();
		auto handle = gum_invocation_context_get_return_value(gum_ctx);
		if (handle == nullptr || pending.record == nullptr ||
		    pending.domain == RuntimeRegistrationDomain::Unknown) {
			native_registration_log(pending.domain, "register_fatbin",
				"UNKNOWN_IDENTITY", handle, pending.wrapper, 0, "");
			return;
		}
		std::uint64_t generation = 0;
		{
			std::lock_guard<std::mutex> guard(
				context->impl->fatbin_identity_mutex);
			const auto key = std::make_pair(
				registration_domain_id(pending.domain), handle);
			auto previous = context->impl->fatbin_handle_to_record.find(key);
			if (previous != context->impl->fatbin_handle_to_record.end()) {
				const auto old_generation = previous->second.generation;
				for (auto it = context->impl->registered_symbol_identity.begin();
				     it != context->impl->registered_symbol_identity.end();) {
					if (it->second.handle_key == key &&
					    it->second.generation == old_generation) {
						context->impl->symbol_address_to_fatbin.erase(
							it->first.second);
						it = context->impl->registered_symbol_identity.erase(it);
					} else {
						++it;
					}
				}
			}
			generation = context->impl->next_fatbin_generation++;
			context->impl->fatbin_handle_to_record[key] =
				{ pending.record, generation };
		}
		native_registration_log(pending.domain, "register_fatbin",
			"EXACT_HANDLE", handle, pending.wrapper, generation, "");
	} else if (context->to_function ==
		   AttachedToFunction::UnregisterFatbin) {
		SPDLOG_DEBUG("Leaving __cudaUnregisterFatBinary");
	} else if (context->to_function ==
		   AttachedToFunction::RegisterFunction) {
		SPDLOG_DEBUG("Leaving RegisterFunction");
	} else if (context->to_function ==
		   AttachedToFunction::RegisterVariable) {
		SPDLOG_DEBUG("Leaving __cudaRegisterVar");
	} else if (context->to_function ==
		   AttachedToFunction::RegisterFatbinEnd) {
		SPDLOG_DEBUG("Leaving __cudaRegisterFatBinaryEnd..");
	}
}

static void
cuda_runtime_function_hooker_class_init(CUDARuntimeFunctionHookerClass *klass)
{
}

static void cuda_runtime_function_hooker_iface_init(gpointer g_iface,
						    gpointer iface_data)
{
	auto iface = (GumInvocationListenerInterface *)g_iface;

	iface->on_enter = example_listener_on_enter;
	iface->on_leave = example_listener_on_leave;
}

static void cuda_runtime_function_hooker_init(CUDARuntimeFunctionHooker *self)
{
}

extern "C" cudaError_t
cuda_runtime_function__cudaLaunchKernel(const void *func, dim3 grid_dim,
					dim3 block_dim, void **args,
					size_t shared_mem, cudaStream_t stream)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	if (impl != nullptr) {
		SPDLOG_DEBUG("grid_dim: {}, {}, {}", grid_dim.x, grid_dim.y,
			     grid_dim.z);
		SPDLOG_DEBUG("block_dim: {}, {}, {}", block_dim.x, block_dim.y,
			     block_dim.z);
	}
	void *original =
		state->orig_cuda_launch_kernel.load(std::memory_order_acquire);
	return cuda_launch_kernel_common(impl, original, func, grid_dim,
					 block_dim, args, shared_mem, stream);
}

static std::optional<std::string>
cuda_graph_maybe_get_kernel_name_from_cufunction(nv_attach_impl &impl,
						 CUfunction function)
{
	if (auto cached = impl.find_original_kernel_name(function); cached)
		return cached;
	using cu_func_get_name_fn_t = CUresult (*)(const char **, CUfunction);
	static cu_func_get_name_fn_t cu_func_get_name =
		(cu_func_get_name_fn_t)dlsym(RTLD_DEFAULT, "cuFuncGetName");
	if (!cu_func_get_name)
		return std::nullopt;
	const char *name = nullptr;
	if (auto err = cu_func_get_name(&name, function); err != CUDA_SUCCESS)
		return std::nullopt;
	if (name == nullptr || name[0] == '\0')
		return std::nullopt;
	impl.record_original_cufunction_name(function, std::string(name));
	return std::string(name);
}

static std::optional<std::string>
cuda_graph_maybe_get_kernel_name_from_cukernel(CUkernel kernel)
{
	using cu_kernel_get_name_fn_t = CUresult (*)(const char **, CUkernel);
	static cu_kernel_get_name_fn_t cu_kernel_get_name =
		(cu_kernel_get_name_fn_t)dlsym(RTLD_DEFAULT, "cuKernelGetName");
	if (!cu_kernel_get_name)
		return std::nullopt;
	const char *name = nullptr;
	if (auto err = cu_kernel_get_name(&name, kernel); err != CUDA_SUCCESS)
		return std::nullopt;
	if (name == nullptr)
		return std::nullopt;
	return std::string(name);
}


extern "C" __attribute__((visibility("default"))) uint64_t
 bpftime_nv_strict_bridge_capabilities_v1()
{
	// bit0: strict driver tri-state bridge; bit1: strict runtime launch must
	// traverse and complete the same-thread exact driver bridge.
	return 3ULL;
}

static void strict_bridge_log(const char *api, int gate_decision,
			      bool exact_alias_found, CUfunction original_function,
			      CUfunction patched_function,
			      CUfunction selected_function,
			      const char *selected_path, CUresult result)
{
	const char *path = std::getenv("HBFSIM_STRICT_BRIDGE_LOG_PATH");
	if (path == nullptr || path[0] == '\0')
		return;
	static std::mutex log_mutex;
	std::lock_guard<std::mutex> guard(log_mutex);
	FILE *stream = std::fopen(path, "a");
	if (stream == nullptr)
		return;
	char original_json[40] = {};
	char patched_json[40] = {};
	char selected_json[40] = {};
	strict_pointer_json(reinterpret_cast<std::uintptr_t>(original_function),
			    original_json, sizeof(original_json));
	strict_pointer_json(reinterpret_cast<std::uintptr_t>(patched_function),
			    patched_json, sizeof(patched_json));
	strict_pointer_json(reinterpret_cast<std::uintptr_t>(selected_function),
			    selected_json, sizeof(selected_json));
	std::fprintf(
		stream,
		"{\"schema_version\":1,\"api\":\"%s\",\"gate_decision\":%d,"
		"\"exact_alias_found\":%s,\"original_function\":%s,"
		"\"patched_function\":%s,\"selected_function\":%s,"
		"\"selected_path\":\"%s\",\"cuda_result\":%d}\n",
		api, gate_decision, exact_alias_found ? "true" : "false",
		original_json, patched_json, selected_json, selected_path,
		static_cast<int>(result));
	std::fclose(stream);
}

static int strict_gate_decision(CUfunction func, void **kernel_params,
				 void **extra)
{
	using gate_approve_fn = int (*)(CUfunction, void **, void **);
	auto gate_approve = reinterpret_cast<gate_approve_fn>(
		dlsym(RTLD_DEFAULT, "hbfsim_approve_original_cuda_function"));
	if (gate_approve == nullptr)
		return 0;
	const int decision = gate_approve(func, kernel_params, extra);
	return decision == 1 || decision == 2 ? decision : 0;
}

// The selected model call reaches this Frida replacement before the gate's
// interposed Driver entrypoint.  Keep the original 152-byte aggregate away
// from the gate until the exact identity has been routed to the adapter.
static bool qkv_optin_exact_original(CUfunction func);
static bool qkv_converted_bridge_active(CUfunction func, void **parameters);
static bool qkv_converted_bridge_enter_direct(CUfunction func, void **parameters);
static int qkv_converted_bridge_enter_common(CUfunction func, void **parameters);
extern "C" void *bpftime_nv_qkv_real_cu_launch_trampoline_v1();
extern "C" CUresult bpftime_nv_qkv_exact_aggregate_launch_v1(
    CUfunction, unsigned int, unsigned int, unsigned int, unsigned int,
    unsigned int, unsigned int, unsigned int, CUstream, void **, void **);

static CUresult cu_launch_kernel_common(
	nv_attach_impl *impl, void *original_fn_ptr, CUfunction func,
	unsigned int grid_dim_x, unsigned int grid_dim_y, unsigned int grid_dim_z,
	unsigned int block_dim_x, unsigned int block_dim_y,
	unsigned int block_dim_z, unsigned int shared_mem_bytes, CUstream stream,
	void **kernel_params, void **extra)
{
	auto original = reinterpret_cast<cu_launch_kernel_fn_t>(original_fn_ptr);
	if (!original) {
		SPDLOG_ERROR("Original cuLaunchKernel function is null");
		return CUDA_ERROR_UNKNOWN;
	}
	const int qkv_bridge = qkv_converted_bridge_enter_common(func, kernel_params);
	if (qkv_bridge < 0) return CUDA_ERROR_NOT_SUPPORTED;
	if (qkv_bridge == 0 && qkv_optin_exact_original(func))
		return bpftime_nv_qkv_exact_aggregate_launch_v1(
			func, grid_dim_x, grid_dim_y, grid_dim_z,
			block_dim_x, block_dim_y, block_dim_z,
			shared_mem_bytes, stream, kernel_params, extra);
	const bool strict = strict_launch_bridge_enabled() || qkv_bridge == 1;
	if (strict) {
		const CUfunction original_func = func;
		if (impl == nullptr || !impl->is_enabled() ||
		    !impl->is_late_bootstrap_done()) {
			strict_runtime_note_driver(0, original_func, nullptr, false,
					   CUDA_ERROR_NOT_SUPPORTED);
			strict_bridge_log("cuLaunchKernel", 0, false, original_func,
					  nullptr, nullptr, "REJECTED",
					  CUDA_ERROR_NOT_SUPPORTED);
			return CUDA_ERROR_NOT_SUPPORTED;
		}
		const int gate_decision =
			strict_gate_decision(func, kernel_params, extra);
		const auto no_alias_action = strict_launch_policy(
			true, gate_decision, false);
		if (no_alias_action == strict_launch_action::reject &&
		    gate_decision != 2) {
			strict_runtime_note_driver(0, original_func, nullptr, false,
					   CUDA_ERROR_NOT_SUPPORTED);
			strict_bridge_log("cuLaunchKernel", 0, false, original_func,
					  nullptr, nullptr, "REJECTED",
					  CUDA_ERROR_NOT_SUPPORTED);
			return CUDA_ERROR_NOT_SUPPORTED;
		}
		if (no_alias_action == strict_launch_action::original) {
			const CUresult result =
				original(func, grid_dim_x, grid_dim_y, grid_dim_z,
					 block_dim_x, block_dim_y, block_dim_z,
					 shared_mem_bytes, stream, kernel_params,
					 extra);
			strict_runtime_note_driver(1, original_func, original_func,
					   false, result);
			strict_bridge_log("cuLaunchKernel", 1, false, original_func,
					  nullptr, original_func, "ORIGINAL", result);
			return result;
		}
		auto patched =
			impl->find_patched_kernel_function_for_original(func);
		if (!patched || strict_launch_policy(true, gate_decision, bool(patched)) !=
				 strict_launch_action::patched) {
			strict_runtime_note_driver(2, original_func, nullptr, false,
					   CUDA_ERROR_NOT_SUPPORTED);
			strict_bridge_log("cuLaunchKernel", 2, false, original_func,
					  nullptr, nullptr, "REJECTED",
					  CUDA_ERROR_NOT_SUPPORTED);
			return CUDA_ERROR_NOT_SUPPORTED;
		}
		func = *patched;
		impl->record_patched_launch(
			reinterpret_cast<cudaStream_t>(stream));
		const CUresult result =
			original(func, grid_dim_x, grid_dim_y, grid_dim_z,
				 block_dim_x, block_dim_y, block_dim_z,
				 shared_mem_bytes, stream, kernel_params, extra);
		strict_runtime_note_driver(2, original_func, func, true, result);
		strict_bridge_log("cuLaunchKernel", 2, true, original_func, func,
				  func, "PATCHED", result);
		return result;
	}
	if (impl == nullptr)
		return original(func, grid_dim_x, grid_dim_y, grid_dim_z,
				block_dim_x, block_dim_y, block_dim_z,
				shared_mem_bytes, stream, kernel_params, extra);
	if (!impl->is_enabled())
		return original(func, grid_dim_x, grid_dim_y, grid_dim_z,
				block_dim_x, block_dim_y, block_dim_z,
				shared_mem_bytes, stream, kernel_params, extra);
	if (!impl->is_late_bootstrap_done())
		return original(func, grid_dim_x, grid_dim_y, grid_dim_z,
				block_dim_x, block_dim_y, block_dim_z,
				shared_mem_bytes, stream, kernel_params, extra);

	if (auto patched =
		    impl->find_patched_kernel_function_for_original(func);
	    patched) {
		using gate_approve_fn = int (*)(CUfunction, void **, void **);
		auto gate_approve = reinterpret_cast<gate_approve_fn>(dlsym(
			RTLD_DEFAULT, "hbfsim_approve_original_cuda_function"));
		const auto gate_decision = gate_approve == nullptr
						   ? 2
						   : gate_approve(func, kernel_params, extra);
		if (gate_decision == 0)
			return CUDA_ERROR_NOT_SUPPORTED;
		if (gate_decision > 1) {
			func = *patched;
			impl->record_patched_launch(
				reinterpret_cast<cudaStream_t>(stream));
		}
	} else {
		auto kernel_name = cuda_graph_maybe_get_kernel_name_from_cufunction(
			*impl, func);
		if (kernel_name) {
			if (auto patched = impl->find_patched_kernel_function(*kernel_name);
		    patched) {
			func = *patched;
			impl->record_patched_launch(
				reinterpret_cast<cudaStream_t>(stream));
		}
		}
	}
	return original(func, grid_dim_x, grid_dim_y, grid_dim_z, block_dim_x,
			block_dim_y, block_dim_z, shared_mem_bytes, stream,
			kernel_params, extra);
}

extern "C" CUresult cuda_driver_function__cuLaunchKernel(
	CUfunction func, unsigned int gridDimX, unsigned int gridDimY,
	unsigned int gridDimZ, unsigned int blockDimX, unsigned int blockDimY,
	unsigned int blockDimZ, unsigned int sharedMemBytes, CUstream hStream,
	void **kernelParams, void **extra)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	void *original =
		state->orig_cu_launch_kernel.load(std::memory_order_acquire);
	return cu_launch_kernel_common(impl, original, func, gridDimX, gridDimY,
				       gridDimZ, blockDimX, blockDimY, blockDimZ,
				       sharedMemBytes, hStream, kernelParams,
				       extra);
}

// The opt-in real-libcuda hook has its own trampoline. Non-QKV calls pass
// directly to that original entry, preserving the gate/global hook's prior
// behavior without a second inspection. Exact QKV calls enter the existing
// aggregate endpoint; its native and converted branches also use this real
// trampoline, so neither can loop through the preloaded gate export.
extern "C" CUresult cuda_driver_function__cuLaunchKernel_qkv_real(
    CUfunction func, unsigned int grid_x, unsigned int grid_y,
    unsigned int grid_z, unsigned int block_x, unsigned int block_y,
    unsigned int block_z, unsigned int shared_bytes, CUstream stream,
    void **parameters, void **extra) {
    auto original = reinterpret_cast<cu_launch_kernel_fn_t>(
        bpftime_nv_qkv_real_cu_launch_trampoline_v1());
    if (!original) return CUDA_ERROR_NOT_SUPPORTED;
    if (!qkv_optin_exact_original(func))
        return original(func, grid_x, grid_y, grid_z, block_x, block_y,
                        block_z, shared_bytes, stream, parameters, extra);
    return bpftime_nv_qkv_exact_aggregate_launch_v1(
        func, grid_x, grid_y, grid_z, block_x, block_y, block_z,
        shared_bytes, stream, parameters, extra);
}

// Strict runtime launches approved by the launch gate cannot assume that a
// particular cudart implementation will re-enter an interposed Driver API.
// This narrow ABI lets the gate dispatch the already-resolved original
// CUfunction through the existing exact-alias Driver bridge.  It is deliberately
// unavailable outside strict mode and never falls back to the original kernel.
extern "C" __attribute__((visibility("default"))) CUresult
bpftime_nv_strict_direct_cu_launch_kernel_v1(
	CUfunction original_func, unsigned int grid_dim_x,
	unsigned int grid_dim_y, unsigned int grid_dim_z,
	unsigned int block_dim_x, unsigned int block_dim_y,
	unsigned int block_dim_z, unsigned int shared_mem_bytes, CUstream stream,
	void **kernel_params, void **extra)
{
	if (original_func == nullptr)
		return CUDA_ERROR_NOT_SUPPORTED;
	const bool qkv_bridge =
		qkv_converted_bridge_active(original_func, kernel_params);
	if (qkv_bridge &&
	    !qkv_converted_bridge_enter_direct(original_func, kernel_params))
		return CUDA_ERROR_NOT_SUPPORTED;
	if (!strict_launch_bridge_enabled() && !qkv_bridge)
		return CUDA_ERROR_NOT_SUPPORTED;
	// The exported ABI is fail-closed even when called by an unexpected
	// consumer.  Only the gate's same-thread approval=2 for this exact
	// original CUfunction may proceed; approval=1 must never reach either the
	// implementation state or the common bridge's native branch.
	if (strict_gate_decision(original_func, kernel_params, extra) != 2)
		return CUDA_ERROR_NOT_SUPPORTED;
	auto &state = nv_attach_get_hook_state();
	auto *impl = state.active_impl.load(std::memory_order_acquire);
	void *original_driver =
		qkv_bridge ? bpftime_nv_qkv_real_cu_launch_trampoline_v1()
		           : state.orig_cu_launch_kernel.load(std::memory_order_acquire);
	if (impl == nullptr || !impl->is_enabled() ||
	    !impl->is_late_bootstrap_done() || original_driver == nullptr)
		return CUDA_ERROR_NOT_SUPPORTED;
	return cu_launch_kernel_common(
		impl, original_driver, original_func, grid_dim_x, grid_dim_y,
		grid_dim_z, block_dim_x, block_dim_y, block_dim_z,
		shared_mem_bytes, stream, kernel_params, extra);
}

extern "C" int bpftime_nv_qkv_pin_bound_identity_v1(CUfunction original);
namespace {
constexpr char kQkvImageSha[] =
    "d15cf2497649902226341d260eee82160e485d38e76cdcb9cb581eca82167eb0";
constexpr char kQkvAbiMapSha[] =
    "20754dd201073fb033f724c0a61ee0177b39eb2c920807c870e5825136096ed9";
constexpr char kQkvStagedPtxSha[] =
    "7219c1e8f58fdca32bc520a3a8ccdb1055aaccc33d12ff7935d7ef7baf786d67";
constexpr char kQkvSourcePtxSha[] =
    "6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab";
constexpr char kQkvName[] =
    "_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi6ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_";
constexpr char kLi7AbiMapSha[] =
    "bb00420b91dac15c735e30ad2fea52f71d21c583540dd18a8181cc34b2a4bd09";
constexpr char kLi7StagedPtxSha[] =
    "7ac0e8a61283376711e6b13e8af9e4d7e2e5621ab1cb6896e6791280476c689a";
constexpr char kLi7SourcePtxSha[] =
    "e70c7c4bb6429dba28291f4c22d9897b96de3d82e6d87b73689b9beff4bec2d5";
constexpr char kLi7Name[] =
    "_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_";
bool li7_target() {
    const char *value = std::getenv("HBFSIM_QKV_TARGET_KIND");
    return value && std::strcmp(value, "router_li7") == 0;
}
const char *active_name() { return li7_target() ? kLi7Name : kQkvName; }
const char *active_map_sha() { return li7_target() ? kLi7AbiMapSha : kQkvAbiMapSha; }
const char *active_stage_sha() { return li7_target() ? kLi7StagedPtxSha : kQkvStagedPtxSha; }
const char *active_source_sha() { return li7_target() ? kLi7SourcePtxSha : kQkvSourcePtxSha; }
struct QkvBoundIdentity {
    CUfunction original = nullptr;
    CUfunction patched = nullptr;
    CUcontext context = nullptr;
    std::uint64_t association_token = 0;
};
struct QkvSelectedStorage {
    CUcontext context = nullptr;
    CUdeviceptr base = 0;
    std::size_t bytes = 0;
    bool consumed = false;
    int outcome = 0;
};
std::mutex &qkv_bound_mu() { static std::mutex mu; return mu; }
QkvBoundIdentity &qkv_bound() { static QkvBoundIdentity value; return value; }
QkvSelectedStorage &qkv_selected() {
    static thread_local QkvSelectedStorage value;
    return value;
}
struct QkvConvertedBridge {
    CUfunction original = nullptr;
    void **parameters = nullptr;
    enum class Stage { Armed, DirectEntered, CommonEntered } stage = Stage::Armed;
};
QkvConvertedBridge &qkv_converted_bridge() {
    static thread_local QkvConvertedBridge value;
    return value;
}
class QkvConvertedBridgeScope {
    bool valid_ = false;
public:
    QkvConvertedBridgeScope(CUfunction original, void **parameters) {
        auto &slot = qkv_converted_bridge();
        if (original && parameters && !slot.original) {
            slot = {original, parameters, QkvConvertedBridge::Stage::Armed};
            valid_ = true;
        }
    }
    ~QkvConvertedBridgeScope() {
        if (valid_) qkv_converted_bridge() = {};
    }
    bool valid() const { return valid_; }
    QkvConvertedBridgeScope(const QkvConvertedBridgeScope &) = delete;
    QkvConvertedBridgeScope &operator=(const QkvConvertedBridgeScope &) = delete;
};
bool qkv_enabled() {
    const char *value = std::getenv("HBFSIM_QKV_RECOVERED_ABI_V1");
    return value && std::strcmp(value, "1") == 0;
}
// This is an exact representative envelope, not a general GEMVX size rule.
// The QKV ABI/image/entry checks below remain mandatory for every row.
bool li6_representative_envelope(std::size_t bytes, unsigned int grid_x) {
    if (li7_target()) return bytes == 262144 && grid_x == 16;
    const char *target = std::getenv("HBFSIM_LI6_REPRESENTATIVE_V1");
    if (!target || !*target || std::strcmp(target, "qkv_layer0") == 0)
        return bytes == 25165824 && grid_x == 1536;
    if (std::strcmp(target, "o_proj_layer0") == 0)
        return bytes == 8388608 && grid_x == 512;
    if (std::strcmp(target, "lm_head") == 0)
        return bytes == 206045184 && grid_x == 12576;
    if (std::strcmp(target, "o_proj_layer0_and_lm_head") == 0)
        return (bytes == 8388608 && grid_x == 512) ||
               (bytes == 206045184 && grid_x == 12576);
    return false;
}
bool qkv_config_pinned() {
    const char *map = std::getenv("HBFSIM_QKV_ABI_MAP_SHA256");
    const char *stage = std::getenv("HBFSIM_QKV_STAGED_PTX_SHA256");
    return map && stage && std::strcmp(map, active_map_sha()) == 0 &&
           std::strcmp(stage, active_stage_sha()) == 0;
}
using qkv_identity_query_type = int (*)(std::uint64_t, std::uint64_t,
                                        QkvLiveIdentityV1 *);
qkv_identity_query_type qkv_identity_query() {
    return reinterpret_cast<qkv_identity_query_type>(
        dlsym(RTLD_DEFAULT, "hbfsim_qkv_live_identity_v1"));
}
using qkv_param_info_type = CUresult (*)(CUfunction, std::size_t,
                                         std::size_t *, std::size_t *);
qkv_param_info_type qkv_param_info() {
    // Resolve against the loaded real Driver DSO, not the gate's interposer.
    static void *driver = dlopen("libcuda.so.1",
                                RTLD_NOW | RTLD_NOLOAD | RTLD_LOCAL);
    return driver ? reinterpret_cast<qkv_param_info_type>(
                        dlsym(driver, "cuFuncGetParamInfo")) : nullptr;
}
bool qkv_live_match(CUfunction original, CUcontext context,
                    std::uint64_t *token) {
    auto query = qkv_identity_query();
    if (!query || !original || !context || !token) return false;
    QkvLiveIdentityV1 identity{};
    identity.struct_size = sizeof(identity);
    if (query(reinterpret_cast<std::uint64_t>(original),
              reinterpret_cast<std::uint64_t>(context), &identity) != 1 ||
        identity.context != reinterpret_cast<std::uint64_t>(context) ||
        identity.module == 0 || identity.association_token == 0 ||
        std::strcmp(identity.image_sha256, kQkvImageSha) != 0) return false;
    *token = identity.association_token;
    return true;
}
bool qkv_metadata_match(CUfunction original, CUfunction patched) {
    auto info = qkv_param_info();
    return info && qkv_exact_abi::validate_metadata(
        [info](void *f, std::size_t index, std::size_t *offset,
               std::size_t *width) {
            return static_cast<int>(info(reinterpret_cast<CUfunction>(f),
                                         index, offset, width));
        }, original, patched);
}
bool qkv_original_metadata_match(CUfunction original) {
    auto info = qkv_param_info();
    return info && qkv_exact_abi::validate_original_metadata(
        [info](void *f, std::size_t index, std::size_t *offset,
               std::size_t *width) {
            return static_cast<int>(info(reinterpret_cast<CUfunction>(f),
                                         index, offset, width));
        }, original);
}
bool qkv_bind_first_selected(CUfunction original) {
    const char *path = std::getenv("HBFSIM_QKV_SOURCE_PTX_PATH");
    const char *stage_path = std::getenv("HBFSIM_QKV_STAGED_PTX_PATH");
    if (!path || !*path || !stage_path || !*stage_path) return false;
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input || input.tellg() <= 0 || input.tellg() > 1024 * 1024)
        return false;
    const auto bytes = static_cast<std::size_t>(input.tellg());
    std::string ptx(bytes, '\0');
    input.seekg(0);
    if (!input.read(ptx.data(), bytes)) return false;
    if (sha256(ptx.data(), ptx.size()) != active_source_sha())
        return false;
    std::ifstream stage_input(stage_path, std::ios::binary | std::ios::ate);
    if (!stage_input || stage_input.tellg() <= 0 ||
        stage_input.tellg() > 2 * 1024 * 1024) return false;
    const auto stage_bytes = static_cast<std::size_t>(stage_input.tellg());
    std::string staged(stage_bytes, '\0');
    stage_input.seekg(0);
    if (!stage_input.read(staged.data(), stage_bytes) ||
        sha256(staged.data(), staged.size()) != active_stage_sha())
        return false;
    // bind_ptx_variant hashes the exact bytes and finds only a previously
    // loaded variant with that digest plus this exact kernel name.
    using bind_type = int (*)(CUfunction, const char *, std::size_t,
                              const char *);
    auto bind = reinterpret_cast<bind_type>(
        dlsym(RTLD_DEFAULT, "bpftime_nv_bind_ptx_variant"));
    return bind && bind(original, ptx.data(), ptx.size(), active_name()) == 0 &&
           bpftime_nv_qkv_pin_bound_identity_v1(original) == 0;
}
struct QkvDecisionContext {
    CUfunction patched = nullptr;
    CUcontext context = nullptr;
    std::uint64_t token = 0;
    unsigned int grid[3]{};
    unsigned int block[3]{};
    unsigned int shared = 0;
};
QkvDecisionContext &qkv_decision_context() {
    static thread_local QkvDecisionContext value;
    return value;
}
CUresult qkv_record_decision(const char *decision, CUfunction original,
                             CUresult result, const char *reason = "none") {
    if (std::strcmp(decision, "REJECTED") == 0 && qkv_selected().base) {
        qkv_selected().consumed = true;
        qkv_selected().outcome = -1;
    }
    const char *path = std::getenv("HBFSIM_QKV_ABI_DECISION_LOG");
    if (path && *path) {
        static std::mutex mu;
        std::lock_guard<std::mutex> guard(mu);
        if (FILE *out = std::fopen(path, "a")) {
            const auto &c = qkv_decision_context();
            std::fprintf(out,
                "{\"schema_version\":1,\"decision\":\"%s\","
                "\"reason\":\"%s\",\"original_function\":\"%p\","
                "\"patched_function\":\"%p\",\"context\":\"%p\","
                "\"association_token\":%llu,\"grid\":[%u,%u,%u],"
                "\"block\":[%u,%u,%u],\"shared_bytes\":%u,"
                "\"cuda_result\":%d}\n",
                decision, reason, reinterpret_cast<void*>(original),
                reinterpret_cast<void*>(c.patched),
                reinterpret_cast<void*>(c.context),
                static_cast<unsigned long long>(c.token),
                c.grid[0], c.grid[1], c.grid[2],
                c.block[0], c.block[1], c.block[2], c.shared,
                static_cast<int>(result));
            std::fclose(out);
        }
    }
    return result;
}
} // namespace

static bool qkv_converted_bridge_active(CUfunction func, void **parameters) {
    const auto &bridge = qkv_converted_bridge();
    return func && parameters && bridge.original == func &&
           bridge.parameters == parameters;
}

static bool qkv_converted_bridge_enter_direct(CUfunction func,
                                               void **parameters) {
    if (!qkv_converted_bridge_active(func, parameters)) return false;
    auto &bridge = qkv_converted_bridge();
    if (bridge.stage != QkvConvertedBridge::Stage::Armed) return false;
    bridge.stage = QkvConvertedBridge::Stage::DirectEntered;
    return true;
}

static int qkv_converted_bridge_enter_common(CUfunction func,
                                              void **parameters) {
    if (!qkv_converted_bridge_active(func, parameters)) return 0;
    auto &bridge = qkv_converted_bridge();
    if (bridge.stage != QkvConvertedBridge::Stage::DirectEntered) return -1;
    bridge.stage = QkvConvertedBridge::Stage::CommonEntered;
    return 1;
}

static bool qkv_optin_exact_original(CUfunction func) {
    if (!qkv_enabled() || !func) return false;
    {
        std::lock_guard<std::mutex> guard(qkv_bound_mu());
        if (qkv_bound().original == func) return true;
    }
    // A matching name routes even when the live provider is unavailable;
    // the aggregate endpoint then records provider_identity as a rejection.
    static void *driver = dlopen("libcuda.so.1", RTLD_NOW | RTLD_NOLOAD |
                                                RTLD_LOCAL);
    using name_type = CUresult (*)(const char **, CUfunction);
    auto get_name = driver ? reinterpret_cast<name_type>(
        dlsym(driver, "cuFuncGetName")) : nullptr;
    const char *name = nullptr;
    if (get_name && get_name(&name, func) == CUDA_SUCCESS && name &&
        std::strcmp(name, active_name()) == 0) return true;
    CUcontext context = nullptr;
    std::uint64_t token = 0;
    return cuCtxGetCurrent(&context) == CUDA_SUCCESS && context &&
           qkv_live_match(func, context, &token);
}

// Arm only on the model execution thread, after exact weight registration;
// base=0,bytes=0 explicitly disarms at module exit. The model pre-hook need
// not know the CUfunction; the first selected call proves its live identity.
extern "C" __attribute__((visibility("default"))) int
bpftime_nv_qkv_select_weight_storage_v1(CUdeviceptr base,
                                        std::size_t bytes) {
    if (!qkv_enabled()) return -1;
    if (!base && !bytes) {
        qkv_selected() = {};
        return 0;
    }
    if (!base || !bytes || qkv_selected().base) return -2;
    if (base && (bytes > UINT64_MAX - base)) return -2;
    CUcontext context = nullptr;
    if (cuCtxGetCurrent(&context) != CUDA_SUCCESS || !context) return -3;
    using registered_type = int (*)(std::uint64_t, std::size_t);
    auto registered = reinterpret_cast<registered_type>(dlsym(
        RTLD_DEFAULT, "hbfsim_qkv_registered_weight_span_v1"));
    if (!registered || registered(base, bytes) != 1) return -4;
    qkv_selected() = {context, base, bytes, false, 0};
    return 0;
}

// The model post-hook calls this even after an exception. A second selected
// launch, no selected launch, or any refused/failed selected launch is an error.
extern "C" __attribute__((visibility("default"))) int
bpftime_nv_qkv_end_selected_call_v1() {
    const auto scope = qkv_selected();
    qkv_selected() = {};
    return scope.base && scope.consumed && scope.outcome == 1 ? 0 : -1;
}

// Called after the existing exact PTX bind, outside CUPTI callbacks. The
// association token ties one original/patched pair to its current context.
extern "C" __attribute__((visibility("default"))) int
bpftime_nv_qkv_pin_bound_identity_v1(CUfunction original) {
    if (!qkv_enabled() || !qkv_config_pinned() || !original) return -1;
    CUcontext context = nullptr;
    if (cuCtxGetCurrent(&context) != CUDA_SUCCESS || !context) return -2;
    std::uint64_t token = 0;
    if (!qkv_live_match(original, context, &token)) return -3;
    auto &state = nv_attach_get_hook_state();
    auto *impl = state.active_impl.load(std::memory_order_acquire);
    if (!impl || !impl->is_enabled() || !impl->is_late_bootstrap_done())
        return -4;
    auto patched = impl->find_patched_kernel_function_for_original(original);
    if (!patched || !*patched || !qkv_metadata_match(original, *patched))
        return -5;
    std::lock_guard<std::mutex> guard(qkv_bound_mu());
    qkv_bound() = {original, *patched, context, token};
    return 0;
}

// The gate uses this only when cuFuncGetName cannot classify a handle. A
// previously bound original remains recognizable; otherwise the live provider
// must prove this exact image, entry, and current context before routing.
extern "C" __attribute__((visibility("default"))) int
bpftime_nv_qkv_is_exact_original_v1(CUfunction original) {
    if (!qkv_enabled() || !original) return 0;
    {
        std::lock_guard<std::mutex> guard(qkv_bound_mu());
        if (qkv_bound().original == original) return 1;
    }
    CUcontext context = nullptr;
    std::uint64_t token = 0;
    return cuCtxGetCurrent(&context) == CUDA_SUCCESS && context &&
                   qkv_live_match(original, context, &token) ? 1 : 0;
}

// Called only by the gate's exact-name opt-in branch, before its ordinary
// inspect_function_launch. No provider lock survives the read-only query.
extern "C" __attribute__((visibility("default"))) CUresult
bpftime_nv_qkv_exact_aggregate_launch_v1(
    CUfunction original, unsigned int grid_x, unsigned int grid_y,
    unsigned int grid_z, unsigned int block_x, unsigned int block_y,
    unsigned int block_z, unsigned int shared_bytes, CUstream stream,
    void **aggregate_parameters, void **extra) {
    qkv_decision_context() = {{}, {}, 0,
        {grid_x, grid_y, grid_z}, {block_x, block_y, block_z}, shared_bytes};
    if (!qkv_enabled() || !original)
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "disabled_or_null");
    CUcontext context = nullptr;
    if (cuCtxGetCurrent(&context) != CUDA_SUCCESS || !context)
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "no_context");
    qkv_decision_context().context = context;
    const QkvSelectedStorage selected = qkv_selected();
    auto &state = nv_attach_get_hook_state();
    auto original_driver = reinterpret_cast<cu_launch_kernel_fn_t>(
        bpftime_nv_qkv_real_cu_launch_trampoline_v1());
    if (!original_driver)
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "no_saved_driver");
    if (!selected.base)
        return qkv_record_decision("NATIVE_UNSELECTED", original,
               original_driver(original, grid_x, grid_y, grid_z, block_x,
                               block_y, block_z, shared_bytes, stream,
                               aggregate_parameters, extra), "scope_unarmed");
    if (selected.context != context)
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "scope_context");
    if (extra || !aggregate_parameters || !aggregate_parameters[0] ||
        !qkv_original_metadata_match(original))
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "aggregate_abi");
    std::uint64_t weight = 0;
    std::memcpy(&weight, aggregate_parameters[0], sizeof(weight));
    if (weight != selected.base)
        return qkv_record_decision("NATIVE_UNSELECTED", original,
               original_driver(original, grid_x, grid_y, grid_z, block_x,
                               block_y, block_z, shared_bytes, stream,
                               aggregate_parameters, extra), "weight_unselected");
    if (selected.consumed) {
        qkv_selected().outcome = -2;
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "second_selected_call");
    }
    qkv_selected().consumed = true; // later same-weight call refuses until end
    qkv_selected().outcome = -1;
    // Once the selected weight is seen, every identity/configuration/ABI
    // failure must refuse this call; it must never silently compute natively.
    const char *policy = std::getenv("HBFSIM_INSTRUMENTATION_POLICY");
    if (!qkv_config_pinned() ||
        !(strict_launch_bridge_enabled() ||
          (policy && std::strcmp(policy, "partial") == 0)))
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "config_or_strict_mode");
    if (!li6_representative_envelope(selected.bytes, grid_x) || grid_y != 1 ||
        grid_z != 1 || block_x != (li7_target() ? 32u : 16u) || block_y != 4 ||
        block_z != 1 || shared_bytes != (li7_target() ? 528u : 272u))
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "target_envelope");
    std::uint64_t token = 0;
    if (!qkv_live_match(original, context, &token))
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "provider_identity");
    qkv_decision_context().token = token;
    QkvBoundIdentity bound;
    {
        std::lock_guard<std::mutex> guard(qkv_bound_mu());
        bound = qkv_bound();
    }
    if (!bound.original) {
        if (!qkv_bind_first_selected(original))
            return qkv_record_decision("REJECTED", original,
                                       CUDA_ERROR_NOT_SUPPORTED, "bind_or_stage");
        std::lock_guard<std::mutex> guard(qkv_bound_mu());
        bound = qkv_bound();
    }
    if (bound.original != original || bound.context != context ||
        bound.association_token != token || !bound.patched)
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "bound_identity");
    qkv_decision_context().patched = bound.patched;
    auto *impl = state.active_impl.load(std::memory_order_acquire);
    if (!impl) return qkv_record_decision("REJECTED", original,
                                          CUDA_ERROR_NOT_SUPPORTED, "no_agent");
    auto patched = impl->find_patched_kernel_function_for_original(original);
    if (!patched || *patched != bound.patched ||
        !qkv_metadata_match(original, bound.patched))
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "candidate_abi");
    qkv_exact_abi::Prepared prepared;
    if (!qkv_exact_abi::convert(aggregate_parameters, extra, &prepared))
        return qkv_record_decision("REJECTED", original,
                                    CUDA_ERROR_NOT_SUPPORTED, "aggregate_copy");
    QkvConvertedBridgeScope bridge(original, prepared.parameters.data());
    if (!bridge.valid())
        return qkv_record_decision("REJECTED", original,
                                   CUDA_ERROR_NOT_SUPPORTED, "nested_converted_bridge");
    // Re-enter the gate with converted cells; it owns the normal launch
    // guard, geometry and a single inspection before scoped strict dispatch.
    using converted_type = CUresult (*)(CUfunction, unsigned int,
        unsigned int, unsigned int, unsigned int, unsigned int,
        unsigned int, unsigned int, CUstream, void**, void**);
    auto converted = reinterpret_cast<converted_type>(dlsym(
        RTLD_DEFAULT, "hbfsim_qkv_converted_launch_v1"));
    const CUresult result = converted ? converted(
        original, grid_x, grid_y, grid_z, block_x, block_y, block_z,
        shared_bytes, stream, prepared.parameters.data(), nullptr)
        : CUDA_ERROR_NOT_SUPPORTED;
    qkv_selected().outcome = result == CUDA_SUCCESS ? 1 : -3;
    return qkv_record_decision(result == CUDA_SUCCESS ? "PATCHED_SELECTED"
                                                    : "REJECTED",
                               original, result,
                               result == CUDA_SUCCESS ? "one_patched_call"
                                                      : "converted_gate_or_driver");
}

static CUresult cu_launch_kernel_ex_common(
	nv_attach_impl *impl, void *original_fn_ptr, const char *api,
	const CUlaunchConfig *config, CUfunction func, void **kernel_params,
	void **extra)
{
	auto original = reinterpret_cast<cu_launch_kernel_ex_fn_t>(original_fn_ptr);
	if (!original) {
		SPDLOG_ERROR("Original cuLaunchKernelEx function is null");
		return CUDA_ERROR_UNKNOWN;
	}
	if (qkv_selected().base && qkv_optin_exact_original(func)) {
		qkv_decision_context() = {};
		return qkv_record_decision("REJECTED", func,
			CUDA_ERROR_NOT_SUPPORTED, "unsupported_driver_ex");
	}
	const bool strict = strict_launch_bridge_enabled();
	if (strict) {
		const CUfunction original_func = func;
		if (impl == nullptr || !impl->is_enabled() ||
		    !impl->is_late_bootstrap_done()) {
			strict_runtime_note_driver(0, original_func, nullptr, false,
					   CUDA_ERROR_NOT_SUPPORTED);
			strict_bridge_log(api, 0, false, original_func,
					  nullptr, nullptr, "REJECTED",
					  CUDA_ERROR_NOT_SUPPORTED);
			return CUDA_ERROR_NOT_SUPPORTED;
		}
		const int gate_decision =
			strict_gate_decision(func, kernel_params, extra);
		const auto no_alias_action = strict_launch_policy(
			true, gate_decision, false);
		if (no_alias_action == strict_launch_action::reject &&
		    gate_decision != 2) {
			strict_runtime_note_driver(0, original_func, nullptr, false,
					   CUDA_ERROR_NOT_SUPPORTED);
			strict_bridge_log(api, 0, false,
					  original_func, nullptr, nullptr, "REJECTED",
					  CUDA_ERROR_NOT_SUPPORTED);
			return CUDA_ERROR_NOT_SUPPORTED;
		}
		if (no_alias_action == strict_launch_action::original) {
			const CUresult result =
				original(config, func, kernel_params, extra);
			strict_runtime_note_driver(1, original_func, original_func,
					   false, result);
			strict_bridge_log(api, 1, false,
					  original_func, nullptr, original_func,
					  "ORIGINAL", result);
			return result;
		}
		auto patched =
			impl->find_patched_kernel_function_for_original(func);
		if (!patched || strict_launch_policy(true, gate_decision, bool(patched)) !=
				 strict_launch_action::patched) {
			strict_runtime_note_driver(2, original_func, nullptr, false,
					   CUDA_ERROR_NOT_SUPPORTED);
			strict_bridge_log(api, 2, false,
					  original_func, nullptr, nullptr, "REJECTED",
					  CUDA_ERROR_NOT_SUPPORTED);
			return CUDA_ERROR_NOT_SUPPORTED;
		}
		func = *patched;
		impl->record_patched_launch(reinterpret_cast<cudaStream_t>(
			config == nullptr ? nullptr : config->hStream));
		const CUresult result =
			original(config, func, kernel_params, extra);
		strict_runtime_note_driver(2, original_func, func, true, result);
		strict_bridge_log(api, 2, true, original_func,
				  func, func, "PATCHED", result);
		return result;
	}
	if (impl != nullptr && impl->is_enabled() &&
	    impl->is_late_bootstrap_done()) {
		if (auto patched =
			    impl->find_patched_kernel_function_for_original(func);
		    patched) {
			using gate_approve_fn = int (*)(CUfunction, void **, void **);
			auto gate_approve = reinterpret_cast<gate_approve_fn>(dlsym(
				RTLD_DEFAULT,
				"hbfsim_approve_original_cuda_function"));
			const auto gate_decision = gate_approve == nullptr
							   ? 2
							   : gate_approve(func, kernel_params,
									  extra);
			if (gate_decision == 0)
				return CUDA_ERROR_NOT_SUPPORTED;
			if (gate_decision > 1) {
				func = *patched;
				impl->record_patched_launch(reinterpret_cast<cudaStream_t>(
					config == nullptr ? nullptr : config->hStream));
			}
		} else if (auto kernel_name =
				   cuda_graph_maybe_get_kernel_name_from_cufunction(
					   *impl, func);
			   kernel_name) {
			if (auto patched_by_name =
				    impl->find_patched_kernel_function(*kernel_name);
			    patched_by_name) {
				func = *patched_by_name;
				impl->record_patched_launch(
					reinterpret_cast<cudaStream_t>(
						config == nullptr ? nullptr :
							config->hStream));
			}
		}
	}
	return original(config, func, kernel_params, extra);
}

extern "C" CUresult cuda_driver_function__cuLaunchKernelEx(
	const CUlaunchConfig *config, CUfunction func, void **kernelParams,
	void **extra)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr)
		state = &nv_attach_get_hook_state();
	return cu_launch_kernel_ex_common(
		state->active_impl.load(std::memory_order_acquire),
		state->orig_cu_launch_kernel_ex.load(std::memory_order_acquire),
		"cuLaunchKernelEx", config, func, kernelParams, extra);
}

extern "C" CUresult cuda_driver_function__cuLaunchKernelEx_ptsz(
	const CUlaunchConfig *config, CUfunction func, void **kernelParams,
	void **extra)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr)
		state = &nv_attach_get_hook_state();
	return cu_launch_kernel_ex_common(
		state->active_impl.load(std::memory_order_acquire),
		state->orig_cu_launch_kernel_ex_ptsz.load(
			std::memory_order_acquire),
		"cuLaunchKernelEx_ptsz", config, func, kernelParams, extra);
}

extern "C" cudaError_t cuda_runtime_function__cudaLaunchKernel_ptsz(
	const void *func, dim3 grid_dim, dim3 block_dim, void **args,
	size_t shared_mem, cudaStream_t stream)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	if (impl != nullptr) {
		SPDLOG_DEBUG("grid_dim: {}, {}, {}", grid_dim.x, grid_dim.y,
			     grid_dim.z);
		SPDLOG_DEBUG("block_dim: {}, {}, {}", block_dim.x, block_dim.y,
			     block_dim.z);
	}
	void *original = state->orig_cuda_launch_kernel_ptsz.load(
		std::memory_order_acquire);
	return cuda_launch_kernel_common(impl, original, func, grid_dim,
					 block_dim, args, shared_mem, stream);
}

static const CUDA_KERNEL_NODE_PARAMS_v1 *
cuda_graph_maybe_patch_kernel_node_params_v1(
	nv_attach_impl &impl, const CUDA_KERNEL_NODE_PARAMS_v1 *params,
	CUDA_KERNEL_NODE_PARAMS_v1 &patched_params)
{
	if (params == nullptr || params->func == nullptr) {
		return params;
	}
	if (auto exact = impl.find_patched_kernel_function_for_original(
		    params->func); exact) {
		patched_params = *params;
		patched_params.func = *exact;
		return &patched_params;
	}
	auto kernel_name = cuda_graph_maybe_get_kernel_name_from_cufunction(
		impl, params->func);
	if (!kernel_name) {
		return params;
	}
	auto patched_func = impl.find_patched_kernel_function(*kernel_name);
	if (!patched_func) {
		return params;
	}
	patched_params = *params;
	patched_params.func = *patched_func;
	return &patched_params;
}

static const CUDA_KERNEL_NODE_PARAMS_v2 *
cuda_graph_maybe_patch_kernel_node_params_v2(
	nv_attach_impl &impl, const CUDA_KERNEL_NODE_PARAMS_v2 *params,
	CUDA_KERNEL_NODE_PARAMS_v2 &patched_params)
{
	if (params == nullptr) {
		return params;
	}
	std::optional<std::string> kernel_name;
	if (params->func != nullptr) {
		if (auto exact =
			    impl.find_patched_kernel_function_for_original(
				    params->func); exact) {
			patched_params = *params;
			patched_params.func = *exact;
			patched_params.kern = nullptr;
			return &patched_params;
		}
		kernel_name = cuda_graph_maybe_get_kernel_name_from_cufunction(
			impl, params->func);
	} else if (params->kern != nullptr) {
		kernel_name = cuda_graph_maybe_get_kernel_name_from_cukernel(
			params->kern);
	} else {
	}
	if (!kernel_name) {
		return params;
	}
	auto patched_func = impl.find_patched_kernel_function(*kernel_name);
	if (!patched_func) {
		return params;
	}
	patched_params = *params;
	patched_params.func = *patched_func;
	patched_params.kern = nullptr;
	return &patched_params;
}

extern "C" CUresult cuda_driver_function__cuGraphAddKernelNode_v1(
	CUgraphNode *phGraphNode, CUgraph hGraph,
	const CUgraphNode *dependencies, size_t numDependencies,
	const CUDA_KERNEL_NODE_PARAMS_v1 *nodeParams)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original = reinterpret_cast<cu_graph_add_kernel_node_v1_fn_t>(
		state->orig_cu_graph_add_kernel_node_v1.load(
			std::memory_order_acquire));
	if (!original)
		return CUDA_ERROR_UNKNOWN;
	if (impl == nullptr || !impl->is_enabled())
		return original(phGraphNode, hGraph, dependencies,
				numDependencies, nodeParams);
	CUDA_KERNEL_NODE_PARAMS_v1 patched_params;
	auto params_to_use = cuda_graph_maybe_patch_kernel_node_params_v1(
		*impl, nodeParams, patched_params);
	return original(phGraphNode, hGraph, dependencies, numDependencies,
			params_to_use);
}

extern "C" CUresult cuda_driver_function__cuGraphAddKernelNode_v2(
	CUgraphNode *phGraphNode, CUgraph hGraph,
	const CUgraphNode *dependencies, size_t numDependencies,
	const CUDA_KERNEL_NODE_PARAMS_v2 *nodeParams)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original = reinterpret_cast<cu_graph_add_kernel_node_v2_fn_t>(
		state->orig_cu_graph_add_kernel_node_v2.load(
			std::memory_order_acquire));
	if (!original)
		return CUDA_ERROR_UNKNOWN;
	if (impl == nullptr || !impl->is_enabled())
		return original(phGraphNode, hGraph, dependencies,
				numDependencies, nodeParams);
	CUDA_KERNEL_NODE_PARAMS_v2 patched_params;
	auto params_to_use = cuda_graph_maybe_patch_kernel_node_params_v2(
		*impl, nodeParams, patched_params);
	return original(phGraphNode, hGraph, dependencies, numDependencies,
			params_to_use);
}

extern "C" CUresult cuda_driver_function__cuGraphExecKernelNodeSetParams_v1(
	CUgraphExec hGraphExec, CUgraphNode hNode,
	const CUDA_KERNEL_NODE_PARAMS_v1 *nodeParams)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original =
		reinterpret_cast<cu_graph_exec_kernel_node_set_params_v1_fn_t>(
			state->orig_cu_graph_exec_kernel_node_set_params_v1.load(
				std::memory_order_acquire));
	if (!original)
		return CUDA_ERROR_UNKNOWN;
	if (impl == nullptr || !impl->is_enabled())
		return original(hGraphExec, hNode, nodeParams);
	CUDA_KERNEL_NODE_PARAMS_v1 patched_params;
	auto params_to_use = cuda_graph_maybe_patch_kernel_node_params_v1(
		*impl, nodeParams, patched_params);
	return original(hGraphExec, hNode, params_to_use);
}

extern "C" CUresult cuda_driver_function__cuGraphExecKernelNodeSetParams_v2(
	CUgraphExec hGraphExec, CUgraphNode hNode,
	const CUDA_KERNEL_NODE_PARAMS_v2 *nodeParams)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original =
		reinterpret_cast<cu_graph_exec_kernel_node_set_params_v2_fn_t>(
			state->orig_cu_graph_exec_kernel_node_set_params_v2.load(
				std::memory_order_acquire));
	if (!original)
		return CUDA_ERROR_UNKNOWN;
	if (impl == nullptr || !impl->is_enabled())
		return original(hGraphExec, hNode, nodeParams);
	CUDA_KERNEL_NODE_PARAMS_v2 patched_params;
	auto params_to_use = cuda_graph_maybe_patch_kernel_node_params_v2(
		*impl, nodeParams, patched_params);
	return original(hGraphExec, hNode, params_to_use);
}

extern "C" CUresult cuda_driver_function__cuGraphKernelNodeSetParams_v1(
	CUgraphNode hNode, const CUDA_KERNEL_NODE_PARAMS_v1 *nodeParams)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original =
		reinterpret_cast<cu_graph_kernel_node_set_params_v1_fn_t>(
			state->orig_cu_graph_kernel_node_set_params_v1.load(
				std::memory_order_acquire));
	if (!original)
		return CUDA_ERROR_UNKNOWN;
	if (impl == nullptr || !impl->is_enabled())
		return original(hNode, nodeParams);
	CUDA_KERNEL_NODE_PARAMS_v1 patched_params;
	auto params_to_use = cuda_graph_maybe_patch_kernel_node_params_v1(
		*impl, nodeParams, patched_params);
	return original(hNode, params_to_use);
}

extern "C" CUresult cuda_driver_function__cuGraphKernelNodeSetParams_v2(
	CUgraphNode hNode, const CUDA_KERNEL_NODE_PARAMS_v2 *nodeParams)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original =
		reinterpret_cast<cu_graph_kernel_node_set_params_v2_fn_t>(
			state->orig_cu_graph_kernel_node_set_params_v2.load(
				std::memory_order_acquire));
	if (!original)
		return CUDA_ERROR_UNKNOWN;
	if (impl == nullptr || !impl->is_enabled())
		return original(hNode, nodeParams);
	CUDA_KERNEL_NODE_PARAMS_v2 patched_params;
	auto params_to_use = cuda_graph_maybe_patch_kernel_node_params_v2(
		*impl, nodeParams, patched_params);
	return original(hNode, params_to_use);
}

static cudaError_t mirror_cuda_memcpy_from_symbol(
	nv_attach_impl *impl, bool async, void *dst, const void *symbol,
	size_t count, size_t offset, cudaMemcpyKind kind, cudaStream_t stream,
	cuda_memcpy_from_symbol_fn_t original_sync,
	cuda_memcpy_from_symbol_async_fn_t original_async)
{
	auto record_itr = impl->symbol_address_to_fatbin.find((void *)symbol);
	if (record_itr == impl->symbol_address_to_fatbin.end()) {
		impl->bootstrap_existing_fatbins_once();

		// Late attach fallback: resolve host symbol name and read from
		// a patched module global if present.
		if (auto name =
			    impl->resolve_host_function_symbol((void *)symbol);
		    name) {
			for (const auto &rec_uptr : impl->fatbin_records) {
				auto *rec = rec_uptr.get();
				if (rec == nullptr)
					continue;
				for (const auto &ptx : rec->ptxs) {
					CUdeviceptr dptr;
					size_t sz;
					auto err = cuModuleGetGlobal(
						&dptr, &sz, ptx->module_ptr,
						name->c_str());
					if (err != CUDA_SUCCESS)
						continue;
					if (offset >= sz) {
						SPDLOG_WARN(
							"mirror_cuda_memcpy_from_symbol: offset {} exceeds size {} for symbol {}",
							offset, sz,
							name->c_str());
						return cudaErrorUnknown;
					}
					size_t writable = sz - offset;
					size_t bytes_to_copy =
						std::min(count, writable);
					if (bytes_to_copy == 0)
						return cudaErrorUnknown;
					CUdeviceptr src = dptr + offset;
					CUstream cu_stream =
						reinterpret_cast<CUstream>(
							stream);
					CUresult status = CUDA_SUCCESS;

					auto copy_device_ptr =
						[](const void *ptr)
						-> CUdeviceptr {
						return static_cast<CUdeviceptr>(
							reinterpret_cast<
								uintptr_t>(
								ptr));
					};
					switch (kind) {
					case cudaMemcpyDeviceToHost:
					case cudaMemcpyDefault:
						status =
							async ? cuMemcpyDtoHAsync(
									dst,
									src,
									bytes_to_copy,
									cu_stream) :
								cuMemcpyDtoH(
									dst,
									src,
									bytes_to_copy);
						break;
					case cudaMemcpyDeviceToDevice:
						status =
							async ? cuMemcpyDtoDAsync(
									copy_device_ptr(
										dst),
									src,
									bytes_to_copy,
									cu_stream) :
								cuMemcpyDtoD(
									copy_device_ptr(
										dst),
									src,
									bytes_to_copy);
						break;
					default:
						return cudaErrorUnknown;
					}
					if (status != CUDA_SUCCESS)
						return cudaErrorUnknown;
					return cudaSuccess;
				}
			}
		}

		SPDLOG_DEBUG(
			"In mirror_cuda_memcpy_from_symbol: calling original cudaMemcpyFromSymbol");
		if (async) {
			if (!original_async) {
				return cudaErrorUnknown;
			}
			return original_async(dst, symbol, count, offset, kind,
					      stream);
		} else {
			if (!original_sync) {
				return cudaErrorUnknown;
			}
			return original_sync(dst, symbol, count, offset, kind);
		}
		return cudaErrorUnknown;
	}
	auto &record = *record_itr->second;
	auto var_itr = record.variable_addr_to_symbol.find((void *)symbol);
	if (var_itr == record.variable_addr_to_symbol.end()) {
		SPDLOG_DEBUG(
			"mirror_cuda_memcpy_from_symbol: no variable info for symbol pointer {:x}",
			(uintptr_t)symbol);
		return cudaErrorUnknown;
	}
	auto &var_info = var_itr->second;
	if (offset >= var_info.size) {
		SPDLOG_WARN(
			"mirror_cuda_memcpy_from_symbol: offset {} exceeds size {} for symbol {}",
			offset, var_info.size, var_info.symbol_name);
		return cudaErrorUnknown;
	}
	size_t writable = var_info.size - offset;
	size_t bytes_to_copy = std::min(count, writable);
	if (bytes_to_copy == 0)
		return cudaErrorUnknown;
	if (bytes_to_copy != count) {
		SPDLOG_WARN(
			"mirror_cuda_memcpy_from_symbol: truncating copy for symbol {} (requested={}, allowed={})",
			var_info.symbol_name, count, bytes_to_copy);
	}
	CUdeviceptr src = var_info.ptr + offset;
	CUstream cu_stream = reinterpret_cast<CUstream>(stream);
	CUresult status = CUDA_SUCCESS;

	auto copy_device_ptr = [](const void *ptr) -> CUdeviceptr {
		return static_cast<CUdeviceptr>(
			reinterpret_cast<uintptr_t>(ptr));
	};

	switch (kind) {
	case cudaMemcpyDeviceToHost:
	case cudaMemcpyDefault:
		status = async ? cuMemcpyDtoHAsync(dst, src, bytes_to_copy,
						   cu_stream) :
				 cuMemcpyDtoH(dst, src, bytes_to_copy);
		break;
	case cudaMemcpyDeviceToDevice:
		status = async ? cuMemcpyDtoDAsync(copy_device_ptr(dst), src,
						   bytes_to_copy, cu_stream) :
				 cuMemcpyDtoD(copy_device_ptr(dst), src,
					      bytes_to_copy);
		break;
	default:
		SPDLOG_DEBUG(
			"mirror_cuda_memcpy_from_symbol: unsupported memcpy kind {} for symbol {}",
			(int)kind, var_info.symbol_name);
		return cudaErrorUnknown;
	}
	if (status != CUDA_SUCCESS) {
		SPDLOG_WARN(
			"mirror_cuda_memcpy_from_symbol: failed to copy symbol {} (err={})",
			var_info.symbol_name, (int)status);
		return cudaErrorUnknown;
	}
	return cudaSuccess;
}

extern "C" cudaError_t cuda_runtime_function__cudaMemcpyFromSymbol(
	void *dst, const void *symbol, size_t count, size_t offset = 0,
	cudaMemcpyKind kind = cudaMemcpyDeviceToHost)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original_sync = reinterpret_cast<cuda_memcpy_from_symbol_fn_t>(
		state->orig_cuda_memcpy_from_symbol.load(
			std::memory_order_acquire));
	auto original_async =
		reinterpret_cast<cuda_memcpy_from_symbol_async_fn_t>(
			state->orig_cuda_memcpy_from_symbol_async.load(
				std::memory_order_acquire));
	if (impl == nullptr || !impl->is_enabled()) {
		if (!original_sync)
			return cudaErrorUnknown;
		return original_sync(dst, symbol, count, offset, kind);
	}
	SPDLOG_DEBUG(
		"call cudaMemcpyFromSymbol with args: {:x}, {:x}, {}, {}, {}",
		(uintptr_t)dst, (uintptr_t)symbol, count, offset, (int)kind);
	return mirror_cuda_memcpy_from_symbol(impl, false, dst, symbol, count,
					      offset, kind, 0, original_sync,
					      original_async);
}

extern "C" cudaError_t cuda_runtime_function__cudaMemcpyFromSymbolAsync(
	void *dst, const void *symbol, size_t count, size_t offset,
	cudaMemcpyKind kind, cudaStream_t stream = 0)
{
	auto gum_ctx = gum_interceptor_get_current_invocation();
	auto *state = (nv_attach_hook_state *)
		gum_invocation_context_get_replacement_data(gum_ctx);
	if (state == nullptr) {
		state = &nv_attach_get_hook_state();
	}
	auto *impl = state->active_impl.load(std::memory_order_acquire);
	auto original_sync = reinterpret_cast<cuda_memcpy_from_symbol_fn_t>(
		state->orig_cuda_memcpy_from_symbol.load(
			std::memory_order_acquire));
	auto original_async =
		reinterpret_cast<cuda_memcpy_from_symbol_async_fn_t>(
			state->orig_cuda_memcpy_from_symbol_async.load(
				std::memory_order_acquire));
	if (impl == nullptr || !impl->is_enabled()) {
		if (!original_async)
			return cudaErrorUnknown;
		return original_async(dst, symbol, count, offset, kind, stream);
	}
	SPDLOG_DEBUG(
		"call cudaMemcpyFromSymbolAsync with args: {:x}, {:x}, {}, {}, {}, {:x}",
		(uintptr_t)dst, (uintptr_t)symbol, count, offset, (int)kind,
		(uintptr_t)stream);
	return mirror_cuda_memcpy_from_symbol(impl, true, dst, symbol, count,
					      offset, kind, stream,
					      original_sync, original_async);
}
