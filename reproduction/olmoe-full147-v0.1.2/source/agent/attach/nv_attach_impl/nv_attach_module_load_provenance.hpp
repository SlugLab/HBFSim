#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace bpftime::attach
{

using begin_module_load_fn = uint64_t (*)(const char *, size_t);
using end_module_load_fn = void (*)(uint64_t);

struct module_load_provenance_hooks {
	begin_module_load_fn begin = nullptr;
	end_module_load_fn end = nullptr;
};

module_load_provenance_hooks find_module_load_provenance_hooks();

class module_load_provenance_scope {
    public:
	module_load_provenance_scope(std::string_view ptx,
				     module_load_provenance_hooks hooks);
	~module_load_provenance_scope();

	module_load_provenance_scope(const module_load_provenance_scope &) =
		delete;
	module_load_provenance_scope &
	operator=(const module_load_provenance_scope &) = delete;

	[[nodiscard]] bool permits_load() const;

    private:
	end_module_load_fn end_ = nullptr;
	uint64_t token_ = 0;
	bool permitted_ = false;
};

} // namespace bpftime::attach
