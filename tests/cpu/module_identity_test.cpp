#include "hbfsim/module_identity.hpp"

#include <future>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

namespace {

hbfsim::ModuleIdentity identity(std::uint8_t first)
{
    hbfsim::ModuleIdentity result{};
    result.front() = first;
    return result;
}

}  // namespace

int main()
{
    hbfsim::ModuleIdentityRegistry registry;
    const auto first = identity(0x11);
    const auto second = identity(0x22);

    CHECK(!registry.associate(0x100, first));
    CHECK(!registry.lookup(0x100).has_value());

    registry.expect(first);
    registry.expect(first);
    CHECK(registry.associate(0x100, first));
    CHECK(registry.lookup(0x100) == first);
    CHECK(!registry.associate(0x101, first));
    registry.expect(first);
    CHECK(!registry.associate(0x102, first));
    registry.erase(0x100);

    registry.expect(first);
    registry.expect(second);
    auto first_association = std::async(
        std::launch::async, [&] { return registry.associate(0x200, first); });
    auto second_association = std::async(
        std::launch::async, [&] { return registry.associate(0x201, second); });
    CHECK(first_association.get());
    CHECK(second_association.get());
    CHECK(registry.lookup(0x200) == first);
    CHECK(registry.lookup(0x201) == second);

    // A failed unload leaves the association untouched because the caller does
    // not erase it.
    CHECK(registry.lookup(0x200) == first);
    registry.erase(0x200);
    CHECK(!registry.lookup(0x200).has_value());

    registry.expect(second);
    registry.discard_expectations();
    CHECK(!registry.associate(0x200, second));
    CHECK(!registry.lookup(0x200).has_value());
    return 0;
}
