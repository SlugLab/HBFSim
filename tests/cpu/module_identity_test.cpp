#include "hbfsim/module_identity.hpp"

#include <barrier>
#include <future>
#include <iomanip>
#include <sstream>
#include <string>

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

std::string canonical_ptx(const hbfsim::ModuleIdentity& identity)
{
    std::ostringstream output;
    output << ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {"
           << std::hex << std::setfill('0');
    for (std::size_t index = 0; index < identity.size(); ++index) {
        if (index != 0) {
            output << ", ";
        }
        output << "0x" << std::setw(2)
               << static_cast<unsigned int>(identity[index]);
    }
    output << "};\n";
    return output.str();
}

}  // namespace

int main()
{
    const auto first = identity(0x11);
    const auto second = identity(0x22);
    const auto first_ptx = canonical_ptx(first);
    const auto second_ptx = canonical_ptx(second);

    hbfsim::ModuleLoadTransactionStore transactions;
    CHECK(transactions.begin(".visible .entry kernel() { ret; }") == 0);
    CHECK(transactions.begin(first_ptx + first_ptx) == 0);
    CHECK(transactions.begin(first_ptx.substr(0, first_ptx.size() - 3)) == 0);

    const auto token = transactions.begin(first_ptx);
    CHECK(token != 0);
    CHECK(transactions.take() == first);
    CHECK(!transactions.take().has_value());

    const auto cancel_token = transactions.begin(first_ptx);
    CHECK(cancel_token != 0);
    transactions.end(cancel_token + 1);
    CHECK(transactions.take() == first);

    const auto matching_cancel = transactions.begin(first_ptx);
    CHECK(matching_cancel != 0);
    transactions.end(matching_cancel);
    CHECK(!transactions.take().has_value());

    const auto nested = transactions.begin(first_ptx);
    CHECK(nested != 0);
    CHECK(transactions.begin(second_ptx) == 0);
    transactions.end(nested);
    CHECK(!transactions.take().has_value());

    std::barrier workers_ready(3);
    auto worker = [&](const std::string& ptx) {
        workers_ready.arrive_and_wait();
        const auto worker_token = transactions.begin(ptx);
        workers_ready.arrive_and_wait();
        return std::pair{worker_token, transactions.take()};
    };
    auto first_worker =
        std::async(std::launch::async, worker, std::cref(first_ptx));
    auto second_worker =
        std::async(std::launch::async, worker, std::cref(second_ptx));
    workers_ready.arrive_and_wait();
    workers_ready.arrive_and_wait();
    const auto [first_worker_token, first_worker_identity] = first_worker.get();
    const auto [second_worker_token, second_worker_identity] =
        second_worker.get();
    CHECK(first_worker_token != 0);
    CHECK(second_worker_token != 0);
    CHECK(first_worker_identity == first);
    CHECK(second_worker_identity == second);

    hbfsim::ModuleIdentityRegistry registry;
    CHECK(registry.associate(0x100, first));
    CHECK(registry.lookup(0x100) == first);
    CHECK(!registry.associate(0x100, second));
    CHECK(registry.associate(0x200, second));
    registry.erase(0x100);
    CHECK(!registry.lookup(0x100).has_value());
    CHECK(registry.lookup(0x200) == second);
    registry.clear();
    CHECK(!registry.lookup(0x200).has_value());
    CHECK(registry.associate(0x100, second));
    CHECK(registry.lookup(0x100) == second);
    return 0;
}
