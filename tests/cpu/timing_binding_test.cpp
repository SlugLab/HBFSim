#include <hbfsim/timing_binding.hpp>

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <unordered_map>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "timing binding CHECK failed at line %d: %s\n", line,
                 expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

struct Initializer {
    std::unordered_map<hbfsim::ModuleHandle,
                       std::pair<std::uintptr_t, std::uint64_t>> values;
    hbfsim::ModuleHandle failing_module{0};
};

bool initialize(hbfsim::ModuleHandle module, std::uintptr_t control_alias,
                std::uint64_t generation, void* opaque) noexcept
{
    auto& state = *static_cast<Initializer*>(opaque);
    if (module == state.failing_module) {
        return false;
    }
    state.values[module] = {control_alias, generation};
    return true;
}

}  // namespace

int main()
{
    hbfsim::TimingBindingRegistry bindings;
    CHECK(bindings.can_activate());
    Initializer initializer;
    constexpr hbfsim::ModuleHandle before_context = 0x1000;
    constexpr hbfsim::ModuleHandle after_context = 0x2000;
    constexpr hbfsim::ModuleHandle failing = 0x3000;
    constexpr std::uintptr_t owner_a = 0xa000;
    constexpr std::uintptr_t context_a = 0xca00;

    CHECK(bindings.add_module(before_context, context_a, 3, initialize,
                              &initializer));
    CHECK(bindings.add_module(0x1100, 0xcf00, 9, initialize, &initializer));
    CHECK((initializer.values.at(before_context) ==
            std::pair<std::uintptr_t, std::uint64_t>{0, 0}));
    std::uint64_t generation_a = 0;
    CHECK(bindings.activate(owner_a, 0xfeed'0000, context_a, 3, initialize,
                             &initializer, generation_a));
    CHECK(!bindings.can_activate());
    CHECK(generation_a != 0);
    CHECK(bindings.owns(owner_a, generation_a));
    CHECK(!bindings.owns(0xb000, generation_a));
    CHECK((initializer.values.at(before_context) ==
            std::pair<std::uintptr_t, std::uint64_t>{0xfeed'0000,
                                                     generation_a}));
    CHECK((initializer.values.at(0x1100) ==
           std::pair<std::uintptr_t, std::uint64_t>{0, 0}));
    CHECK(bindings.ready(before_context, context_a, 3, generation_a));
    CHECK(bindings.ready_for_active(before_context, context_a, 3));
    CHECK(!bindings.ready(before_context, 0xcb00, 3, generation_a));
    CHECK(!bindings.ready(before_context, context_a, 4, generation_a));
    CHECK(!bindings.ready(before_context, context_a, 3, generation_a + 1));

    CHECK(bindings.add_module(after_context, context_a, 3, initialize,
                              &initializer));
    CHECK(bindings.ready(after_context, context_a, 3, generation_a));
    CHECK((initializer.values.at(after_context) ==
            std::pair<std::uintptr_t, std::uint64_t>{0xfeed'0000,
                                                     generation_a}));

    std::uint64_t rejected_generation = 0;
    CHECK(!bindings.activate(0xb000, 0xbeef'0000, 0xcb00, 4, initialize,
                              &initializer, rejected_generation));
    CHECK(rejected_generation == 0);
    CHECK(bindings.quiesce(owner_a, generation_a));
    CHECK(!bindings.ready(before_context, context_a, 3, generation_a));
    CHECK(!bindings.add_module(0x2500, context_a, 3, initialize,
                               &initializer));
    CHECK(bindings.invalidate(owner_a, generation_a, initialize, &initializer));
    CHECK(bindings.finish_retire(owner_a, generation_a));
    CHECK(bindings.can_activate());
    CHECK(!bindings.owns(owner_a, generation_a));
    CHECK((initializer.values.at(before_context) ==
            std::pair<std::uintptr_t, std::uint64_t>{0, 0}));
    CHECK((initializer.values.at(after_context) ==
            std::pair<std::uintptr_t, std::uint64_t>{0, 0}));

    std::uint64_t generation_b = 0;
    CHECK(bindings.activate(0xb000, 0xbeef'0000, 0xcb00, 4, initialize,
                             &initializer, generation_b));
    CHECK(generation_b > generation_a);
    CHECK(!bindings.ready(before_context, 0xcb00, 4, generation_b));
    bindings.erase(before_context);
    CHECK(!bindings.ready(before_context, 0xcb00, 4, generation_b));
    CHECK(bindings.add_module(before_context, 0xcb00, 4, initialize,
                              &initializer));
    CHECK(bindings.ready(before_context, 0xcb00, 4, generation_b));

    initializer.failing_module = failing;
    CHECK(!bindings.add_module(failing, 0xcb00, 4, initialize,
                               &initializer));
    CHECK(!bindings.ready(failing, 0xcb00, 4, generation_b));
    initializer.failing_module = 0;
    CHECK(bindings.quiesce(0xb000, generation_b));
    CHECK(bindings.invalidate(0xb000, generation_b, initialize, &initializer));
    CHECK(bindings.finish_retire(0xb000, generation_b));

    hbfsim::TimingBindingRegistry partial;
    Initializer partial_initializer;
    CHECK(partial.add_module(0x4000, 0xcd00, 5, initialize,
                             &partial_initializer));
    partial_initializer.failing_module = 0x5000;
    CHECK(!partial.add_module(0x5000, 0xcd00, 5, initialize,
                              &partial_initializer));
    CHECK(partial.add_module(0x6000, 0xce00, 6, initialize,
                             &partial_initializer));
    std::uint64_t partial_generation = 0;
    CHECK(partial.activate(0xd000, 0xabcd'0000, 0xcd00, 5, initialize,
                           &partial_initializer, partial_generation));
    CHECK(partial_generation != 0);
    CHECK(partial.ready(0x4000, 0xcd00, 5, partial_generation));
    CHECK(!partial.ready(0x5000, 0xcd00, 5, partial_generation));
    CHECK(!partial.ready(0x6000, 0xce00, 6, partial_generation));
    CHECK(partial.quiesce(0xd000, partial_generation));
    CHECK(partial.invalidate(0xd000, partial_generation, initialize,
                             &partial_initializer));
    CHECK(partial.finish_retire(0xd000, partial_generation));

    hbfsim::TimingBindingRegistry lifecycle;
    Initializer lifecycle_initializer;
    CHECK(lifecycle.add_module(0x7000, 0xd100, 8, initialize,
                               &lifecycle_initializer));
    CHECK(lifecycle.add_module(0x8000, 0xd200, 9, initialize,
                               &lifecycle_initializer));
    std::uint64_t lifecycle_generation = 0;
    CHECK(lifecycle.activate(0xd000, 0xdddd'0000, 0xd100, 8, initialize,
                             &lifecycle_initializer, lifecycle_generation));
    lifecycle.erase_context(0xd200, nullptr, nullptr);
    CHECK(lifecycle.ready(0x7000, 0xd100, 8, lifecycle_generation));
    lifecycle.erase_context(0xd100, nullptr, nullptr);
    CHECK(!lifecycle.owns(0xd000, lifecycle_generation));

    hbfsim::TimingBindingRegistry generation_wrap;
    Initializer wrap_initializer;
    generation_wrap.set_next_generation_for_test(UINT64_MAX);
    std::uint64_t last_generation = 0;
    CHECK(generation_wrap.activate(0xf000, 0xffff'0000, 0xcf00, 7,
                                   initialize, &wrap_initializer,
                                   last_generation));
    CHECK(last_generation == UINT64_MAX);
    CHECK(generation_wrap.quiesce(0xf000, last_generation));
    CHECK(generation_wrap.invalidate(0xf000, last_generation, initialize,
                                     &wrap_initializer));
    CHECK(generation_wrap.finish_retire(0xf000, last_generation));
    std::uint64_t wrapped_generation = 0;
    CHECK(!generation_wrap.activate(0xf100, 0xfffe'0000, 0xcf10, 7,
                                    initialize, &wrap_initializer,
                                    wrapped_generation));
    CHECK(wrapped_generation == 0);
    return 0;
}
