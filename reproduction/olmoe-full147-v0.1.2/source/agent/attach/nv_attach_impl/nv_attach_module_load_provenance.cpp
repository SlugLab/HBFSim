#include "nv_attach_module_load_provenance.hpp"

#include <dlfcn.h>

namespace bpftime::attach
{

module_load_provenance_hooks find_module_load_provenance_hooks()
{
	return {
		.begin = reinterpret_cast<begin_module_load_fn>(dlsym(
			RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx")),
		.end = reinterpret_cast<end_module_load_fn>(
			dlsym(RTLD_DEFAULT, "hbfsim_end_module_load")),
	};
}

module_load_provenance_scope::module_load_provenance_scope(
	std::string_view ptx, module_load_provenance_hooks hooks)
{
	if (hooks.begin == nullptr && hooks.end == nullptr) {
		permitted_ = true;
		return;
	}
	if (hooks.begin == nullptr || hooks.end == nullptr) {
		return;
	}
	token_ = hooks.begin(ptx.data(), ptx.size());
	if (token_ == 0) {
		return;
	}
	end_ = hooks.end;
	permitted_ = true;
}

module_load_provenance_scope::~module_load_provenance_scope()
{
	if (token_ != 0 && end_ != nullptr)
		end_(token_);
}

bool module_load_provenance_scope::permits_load() const
{
	return permitted_;
}

} // namespace bpftime::attach
