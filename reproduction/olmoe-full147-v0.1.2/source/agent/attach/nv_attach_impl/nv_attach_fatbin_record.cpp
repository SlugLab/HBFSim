#include "nv_attach_fatbin_record.hpp"
#include "nvptx_opt_policy.hpp"
#include "module_register_policy.hpp"
#include "module_host_timing.hpp"
#include "nv_attach_module_load_provenance.hpp"
#include "cuda.h"
#include "nvPTXCompiler.h"
#include "nv_attach_utils.hpp"
#include "spdlog/spdlog.h"
#include "nv_attach_impl.hpp"
#include <boost/asio/post.hpp>
#include <boost/asio/thread_pool.hpp>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <dlfcn.h>
#include <iterator>
#include <stdexcept>
#include <ptx_pass_config.h>
#include "ptx_compiler/ptx_compiler.hpp"
#include <utility>
#define CUDA_DRIVER_CHECK_NO_EXCEPTION(expr, message)                          \
	do {                                                                   \
		if (auto err = expr; err != CUDA_SUCCESS) {                    \
			SPDLOG_ERROR("{}: {}", message, (int)err);             \
		}                                                              \
	} while (false)
#define CUDA_DRIVER_CHECK_EXCEPTION(expr, message)                             \
	do {                                                                   \
		if (auto err = expr; err != CUDA_SUCCESS) {                    \
			SPDLOG_ERROR("{}: {}", message, (int)err);             \
			throw std::runtime_error(message);                     \
		}                                                              \
	} while (false)
#define NVPTXCOMPILER_CHECK_EXCEPTION(x, message)                              \
	do {                                                                   \
		nvPTXCompileResult result = x;                                 \
		if (result != NVPTXCOMPILE_SUCCESS) {                          \
			SPDLOG_ERROR("error: {} failed with error code {}\n",  \
				     #x, (int)result);                         \
			throw std::runtime_error(message);                     \
		}                                                              \
	} while (0)
namespace bpftime::attach
{
using bpftime::attach::rewrite_ptx_target;

fatbin_record::~fatbin_record()
{
}
ptx_in_module::~ptx_in_module()
{
	CUDA_DRIVER_CHECK_NO_EXCEPTION(cuModuleUnload(this->module_ptr),
				       "Unable to unload module");
}

bool fatbin_record::find_and_fill_variable_info(void *ptr,
						const char *symbol_name)
{
	for (const auto &ptx : ptxs) {
		CUdeviceptr dptr;
		size_t size;
		auto err = cuModuleGetGlobal(&dptr, &size, ptx->module_ptr,
					     symbol_name);
		if (err == CUDA_SUCCESS) {
			variable_addr_to_symbol[ptr] =
				variable_info{ .symbol_name =
						       std::string(symbol_name),
					       .ptr = dptr,
					       .size = size,
					       .ptx = ptx.get() };
			return true;
		} else if (err == CUDA_ERROR_NOT_FOUND) {
			continue;
		} else {
			SPDLOG_ERROR("Unable to lookup symbol: {}", (int)err);
			return false;
		}
	}
	return false;
}
bool fatbin_record::find_and_fill_function_info(void *ptr,
						const char *symbol_name)
{
	for (const auto &ptx : ptxs) {
		CUfunction func;
		auto err = cuModuleGetFunction(&func, ptx->module_ptr,
					       symbol_name);
		if (err == CUDA_SUCCESS) {
			function_addr_to_symbol[ptr] =
				kernel_info{ .symbol_name =
						     std::string(symbol_name),
					     .func = func,
					     .ptx = ptx.get() };
			return true;
		} else if (err == CUDA_ERROR_NOT_FOUND) {
			continue;
		} else {
			SPDLOG_ERROR("Unable to lookup function: {}", (int)err);
			return false;
		}
	}
	return false;
}

std::map<std::string, std::vector<uint8_t>> fatbin_record::compile_ptxs(
	class nv_attach_impl &impl,
	std::map<std::string, std::tuple<std::string, bool>> patched_ptx,
	bool prepatched)
{
	hbfsim_module_host_timing::Batch timing_batch("COMPILE_BATCH");
	if(timing_batch.on){timing_batch.record_ptr=(uintptr_t)this;timing_batch.impl_ptr=(uintptr_t)&impl;timing_batch.ptx_pool_ptr=(uintptr_t)ptx_pool.get();timing_batch.module_pool_ptr=(uintptr_t)module_pool.get();}
	std::string sm_arch = get_gpu_sm_arch();
	SPDLOG_INFO("Compiling PTXs with sm_arch {}", sm_arch);

	unsigned major, minor;
	NVPTXCOMPILER_CHECK_EXCEPTION(nvPTXCompilerGetVersion(&major, &minor),
				      "Unable to get compiler version");
	SPDLOG_INFO("Compiler version: {}.{}", major, minor);

	std::map<std::string, std::vector<uint8_t>> compiled_ptx;
	const auto &handler = impl.ptx_compiler;
	const auto opt_config = nvptx_opt_policy::from_environment();
	SPDLOG_INFO("nvPTXCompiler default option -O{}; raw overrides {}",
		    opt_config.fallback, opt_config.by_raw_sha256.size());
	std::string max_register_option;
	if (const char *value = std::getenv("HBFSIM_NVPTX_MAX_REGISTERS");
	    value != nullptr && value[0] != '\0') {
		char *end = nullptr;
		const long parsed = std::strtol(value, &end, 10);
		if (end == value || *end != '\0' || parsed < 1 || parsed > 255)
			throw std::runtime_error(
				"HBFSIM_NVPTX_MAX_REGISTERS must be an integer in [1,255]");
		max_register_option = "--maxrregcount=" + std::to_string(parsed);
		SPDLOG_INFO("Using explicit nvPTXCompiler option {}",
			    max_register_option);
	}
	const auto module_reg_config = module_register_policy::parse(
		std::getenv("HBFSIM_NVPTX_MAX_REGISTERS_BY_RAW_SHA256"));
	boost::asio::thread_pool pool(std::thread::hardware_concurrency());
	std::mutex map_lock;
	for (const auto &[name, ptx_and_trampoline_flag] : patched_ptx) {
		const auto &ptx = std::get<0>(ptx_and_trampoline_flag);
		const auto original_name = nvptx_opt_policy::original_name(name);
		const auto original_itr = original_ptx.find(original_name);
		if (original_itr == original_ptx.end())
			throw std::runtime_error("Unable to associate compiled module with original PTX");
		const auto &raw_ptx = original_itr->second;
		const auto raw_digest = nvptx_opt_policy::raw_digest_for_module(
			original_name, raw_ptx, prepatched,
			[](std::string_view bytes) {
				return sha256(bytes.data(), bytes.size());
			});
		const auto opt_level_option = opt_config.option_for_raw(raw_digest);
		const auto selected_register_policy =
			module_reg_config.select(raw_digest, max_register_option);
		const std::string module_max_register_option = selected_register_policy.option;
		const bool module_reg_override = selected_register_policy.raw_override;
		const bool module_reg_evidence = module_reg_config.enabled;

		SPDLOG_INFO("nvPTXCompiler module={} raw_sha256={} option={}",
			    name, raw_digest, opt_level_option);

			const auto timing_task_id=hbfsim_module_host_timing::next_id();
			const auto timing_enqueue_ns=hbfsim_module_host_timing::stamp();
			const auto timing_parent_id=timing_batch.id;
			boost::asio::post(
				pool,
				[&handler, ptx, name, &compiled_ptx, &map_lock, this,
				 sm_arch, module_max_register_option, opt_level_option, raw_digest,
				 module_reg_override, module_reg_evidence, timing_task_id, timing_enqueue_ns, timing_parent_id]() -> void {
					hbfsim_module_host_timing::Observation timing_task("COMPILE_TASK",timing_task_id,timing_parent_id,timing_enqueue_ns);
					timing_task.meta(name.c_str(),raw_digest.c_str());
					if(timing_task.on){timing_task.record_ptr=(uintptr_t)this;timing_task.ptx_pool_ptr=(uintptr_t)ptx_pool.get();timing_task.module_pool_ptr=(uintptr_t)module_pool.get();timing_task.prep_start=hbfsim_module_host_timing::stamp();}
					const auto ptx_fixed =
						rewrite_ptx_target(ptx, sm_arch);
					const std::string gpu_name = "--gpu-name=" + sm_arch;
					std::vector<std::string> effective_options = {
						gpu_name, "--verbose", opt_level_option};
					if (!module_max_register_option.empty())
						effective_options.push_back(module_max_register_option);
					std::string cache_material = ptx_fixed;
					for (const auto &option : effective_options)
						cache_material +=
							"\n// hbfsim nvPTXCompiler option: " + option;
					auto sha256_string =
						sha256(cache_material.data(), cache_material.size());
					if(timing_task.on)timing_task.prep_end=hbfsim_module_host_timing::stamp();
					if (module_reg_evidence) {
						const int saved_errno = errno;
						if (std::fprintf(stderr,
						    "HBFSIM_MODULE_REGISTER_POLICY_V1 raw=%s module=%s source=%s effective=%s cache=%s\n",
						    raw_digest.c_str(), name.c_str(),
						    module_reg_override ? "RAW" : "GLOBAL",
						    module_max_register_option.empty() ? "none" : module_max_register_option.c_str(),
						    sha256_string.c_str()) < 0)
							SPDLOG_WARN("Module register policy evidence write failed");
						errno = saved_errno;
					}

					if (auto itr =
						    this->ptx_pool->find(sha256_string);
					    itr != this->ptx_pool->end()) {
						timing_task.lookup(sha256_string.c_str(),"HIT");
						SPDLOG_INFO(
						"PTX {} ({}) found in cache",
						name, sha256_string);
					std::lock_guard<std::mutex> _guard(
						map_lock);
					compiled_ptx[name] = itr->second;
				} else {
					timing_task.lookup(sha256_string.c_str(),"MISS");
					SPDLOG_INFO(
						"Start compiling {}, not found in cache",
						name);
					auto compiler = handler.create();
					if (!compiler) {
						throw std::runtime_error(
							"Unable to create nv_attach_impl_ptx_compiler");
					}
						std::vector<const char *> compile_options;
						compile_options.reserve(effective_options.size());
						for (const auto &option : effective_options)
							compile_options.push_back(option.c_str());
						SPDLOG_INFO(
							"nvPTXCompiler effective options: {} {} {} {}",
							effective_options[0], effective_options[1],
							effective_options[2],
							module_max_register_option.empty() ? "(no maxrregcount)" :
							module_max_register_option);
						if (auto err = timing_task.call([&](){ return handler.compile(
							    compiler, ptx_fixed.c_str(),
							    compile_options.data(),
							    compile_options.size()); });
						    err != 0) {
							SPDLOG_ERROR(
								"Unable to compile: {}, error = {}",
							err,
							handler.get_error_log(
								compiler));
						throw std::runtime_error(
							"Unable to compile");
					}
					SPDLOG_DEBUG(
						"Info: {}",
						handler.get_info_log(compiler));
					uint8_t *data;
					size_t size;
					handler.get_compiled_program(
						compiler, &data, &size);
					std::vector<uint8_t> compiled_program(
						data, data + size);
					handler.destroy(compiler);
					std::lock_guard<std::mutex> _guard(
						map_lock);
					compiled_ptx[name] = compiled_program;
					this->ptx_pool->insert(std::make_pair(
						sha256_string,
						compiled_program));
					SPDLOG_INFO("Compile of {} done", name);
				}
			});
	}
	if(timing_batch.on)timing_batch.join_start=hbfsim_module_host_timing::stamp();
	pool.join();
	if(timing_batch.on)timing_batch.join_end=hbfsim_module_host_timing::stamp();
	return compiled_ptx;
}
void fatbin_record::try_loading_ptxs(class nv_attach_impl &impl)
{
	if (ptx_loaded)
		return;
	if (original_ptx.empty()) {
		SPDLOG_INFO("Skipping fatbin without extractable PTX");
		ptx_loaded = true;
		return;
	}
	if (impl.shared_mem_ptr == 0) {
		throw std::runtime_error(
			"shared_mem_ptr is not initialized before loading PTX");
	}
	SPDLOG_INFO("Loading & patching current fatbin..");

	hbfsim_module_host_timing::Batch timing_load_batch("LOAD_BATCH");
	if(timing_load_batch.on){timing_load_batch.record_ptr=(uintptr_t)this;timing_load_batch.impl_ptr=(uintptr_t)&impl;timing_load_batch.ptx_pool_ptr=(uintptr_t)ptx_pool.get();timing_load_batch.module_pool_ptr=(uintptr_t)module_pool.get();}
	const char *prepatched_env =
		getenv("BPFTIME_CUDA_LATE_PTX_PREPATCHED");
	const bool prepatched = prepatched_env != nullptr &&
		prepatched_env[0] == '1' && prepatched_env[1] == '\0';
	std::map<std::string, std::tuple<std::string, bool>> patched_ptx;
	if (prepatched) {
		if(timing_load_batch.on){timing_load_batch.phase="PREPATCHED_REUSED";timing_load_batch.prep_start=hbfsim_module_host_timing::stamp();}
		SPDLOG_INFO("Using prepatched external PTX ({} file(s))",
			    original_ptx.size());
		for (const auto &[name, ptx] : original_ptx) {
			patched_ptx.emplace("patched." + name,
					    std::make_tuple(ptx, false));
		}
		if(timing_load_batch.on)timing_load_batch.prep_end=hbfsim_module_host_timing::stamp();
	} else {
		if(timing_load_batch.on)timing_load_batch.phase="INSTRUMENTATION_WRAPPER";
		patched_ptx = *timing_load_batch.prepare([&](){return impl.hack_fatbin(original_ptx);});
	}
	auto compiled_ptx = compile_ptxs(impl, patched_ptx, prepatched);

	for (const auto &[name, ptx_and_trampoline_flag] : patched_ptx) {
		hbfsim_module_host_timing::Observation timing_load("MODULE",0,timing_load_batch.id);
		if(timing_load.on){timing_load.record_ptr=(uintptr_t)this;timing_load.module_pool_ptr=(uintptr_t)module_pool.get();}
		const auto &ptx = std::get<0>(ptx_and_trampoline_flag);
		bool added_trampoline = std::get<1>(ptx_and_trampoline_flag);
		const auto &compiled_elf = compiled_ptx.at(name);
		const std::string patched_prefix = "patched.";
		const auto original_name = name.rfind(patched_prefix, 0) == 0 ?
			name.substr(patched_prefix.size()) : name;
		auto original_itr = original_ptx.find(original_name);
		if (original_itr == original_ptx.end()) {
			throw std::runtime_error(
				"Unable to associate patched module with original PTX");
		}
		const auto &original_ptx = original_itr->second;
		auto original_ptx_sha256 =
			sha256(original_ptx.data(), original_ptx.size());
		if (prepatched) {
			// Preserve the original PTX digest from staged filename; the
			// file contents are the automatically rewritten PTX.
			auto staged_digest = original_name;
			if (staged_digest.ends_with(".ptx"))
				staged_digest.resize(staged_digest.size() - 4);
			if (staged_digest.size() == 64 &&
			    staged_digest.find_first_not_of("0123456789abcdef") ==
				    std::string::npos)
				original_ptx_sha256 = staged_digest;
			else
				throw std::runtime_error(
					"Prepatched PTX filename must be a lowercase SHA256 digest");
		}
		timing_load.meta(name.c_str(),original_ptx_sha256.c_str());
		auto sha256_string =
			sha256(compiled_elf.data(), compiled_elf.size());
		if (auto itr = module_pool->find(sha256_string);
		    itr != module_pool->end()) {
			timing_load.lookup(sha256_string.c_str(),"HIT");
			SPDLOG_INFO("Module {} found in cache", name);
			ptxs.push_back(itr->second);
			original_ptx_sha256_by_module.push_back(
				original_ptx_sha256);
		} else {
			timing_load.lookup(sha256_string.c_str(),"MISS");
			CUmodule module;
			SPDLOG_INFO("Loading module: {}, not found in cache",
				    name);
			char error_buf[8192] = { 0 }, info_buf[8192] = { 0 };
			CUjit_option options[] = {
				CU_JIT_INFO_LOG_BUFFER,
				CU_JIT_INFO_LOG_BUFFER_SIZE_BYTES,
				CU_JIT_ERROR_LOG_BUFFER,
				CU_JIT_ERROR_LOG_BUFFER_SIZE_BYTES
			};
			void *option_values[] = {
				(void *)info_buf, (void *)std::size(info_buf),
				(void *)error_buf, (void *)std::size(error_buf)
			};
			CUresult load_result;
			{
				module_load_provenance_scope provenance(
					ptx, find_module_load_provenance_hooks());
				if (!provenance.permits_load()) {
					if(timing_load.on)timing_load.phase="PROVENANCE_REJECTED";
					SPDLOG_ERROR(
						"Module {} load provenance rejected",
						name);
					throw std::runtime_error(
						"Module load provenance rejected");
				}
				load_result = timing_load.call([&](){return cuModuleLoadDataEx(
					&module, compiled_elf.data(),
					std::size(options), options, option_values); });
				if(timing_load.on&&load_result==CUDA_SUCCESS)timing_load.module=(uintptr_t)module;
			}
			if (load_result != CUDA_SUCCESS) {
				SPDLOG_ERROR("Unable to compile module {}: {}",
					     name, (int)load_result);
				SPDLOG_ERROR("Info: {}", info_buf);
				SPDLOG_ERROR("Error: {}", error_buf);
				throw std::runtime_error(
					"Unable to compile module");
			}
			if (added_trampoline) {
				CUdeviceptr const_data_ptr, map_basic_info_ptr;
				size_t const_data_size, map_basic_info_size;
				SPDLOG_INFO(
					"Copying trampoline data to device");
				CUDA_DRIVER_CHECK_EXCEPTION(
					cuModuleGetGlobal(&const_data_ptr,
							  &const_data_size,
							  module, "constData"),
					"Unable to get pointer of constData");
				SPDLOG_INFO(
			"constData symbol device_ptr={:x} size={} shared_mem_ptr={:x}",
			(uintptr_t)const_data_ptr, const_data_size,
			(uintptr_t)impl.shared_mem_ptr);
		CUDA_DRIVER_CHECK_EXCEPTION(
					cuModuleGetGlobal(&map_basic_info_ptr,
							  &map_basic_info_size,
							  module, "map_info"),
					"Unable to get pointer of map_info");
				SPDLOG_INFO("map_info symbol device_ptr={:x} size={}",
			    (uintptr_t)map_basic_info_ptr, map_basic_info_size);
		CUDA_DRIVER_CHECK_EXCEPTION(
					cuMemcpyHtoD(const_data_ptr,
						     &impl.shared_mem_ptr,
						     const_data_size),
					"Unable to copy constData pointer to device");
				CUDA_DRIVER_CHECK_EXCEPTION(
					cuMemcpyHtoD(map_basic_info_ptr,
						     impl.map_basic_info->data(),
						     map_basic_info_size),
					"Unable to copy constData pointer to device");
				SPDLOG_INFO("Trampoline data copied");
			}
			auto ptr = std::make_shared<ptx_in_module>(module);
			module_pool->insert(std::make_pair(sha256_string, ptr));
			ptxs.push_back(ptr);
			original_ptx_sha256_by_module.push_back(
				original_ptx_sha256);
			SPDLOG_INFO("Loaded module: {}", name);
		}
	}
	ptx_loaded = true;
}

} // namespace bpftime::attach
