#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace bpftime::attach {


inline const char *strict_pointer_json(std::uintptr_t value, char *buffer,
				      std::size_t capacity)
{
	if (value == 0) {
		if (capacity >= 5)
			std::memcpy(buffer, "null", 5);
		return buffer;
	}
	std::snprintf(buffer, capacity, "\"0x%llx\"",
		      static_cast<unsigned long long>(value));
	return buffer;
}

enum class strict_launch_action {
	reject,
	original,
	patched,
};

inline strict_launch_action strict_launch_policy(bool implementation_ready,
						 int gate_decision,
						 bool exact_alias_found)
{
	if (!implementation_ready)
		return strict_launch_action::reject;
	if (gate_decision == 1)
		return strict_launch_action::original;
	if (gate_decision == 2 && exact_alias_found)
		return strict_launch_action::patched;
	return strict_launch_action::reject;
}

} // namespace bpftime::attach
