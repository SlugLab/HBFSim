#include "ptx_analysis.hpp"
#include "ptx_ir.hpp"

#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct Case {
    const char* name;
    const char* body;
    std::set<std::string> required_consumers;
    bool must_reject{false};
};

const std::vector<Case> kCases{
    {"predicated_first_consumer",
     "ld.global.u32 %r1, [%rd1];\n"
     "@%p1 add.u32 %r2, %r1, 1;\n"
     "sub.u32 %r3, %r1, 2;\nret;\n",
     {"add.u32", "sub.u32"}},
    {"multiple_predicated_consumers",
     "ld.global.u32 %r1, [%rd1];\n"
     "@%p1 add.u32 %r2, %r1, 1;\n"
     "@%p2 sub.u32 %r3, %r1, 2;\n"
     "xor.b32 %r4, %r1, 3;\nret;\n",
     {"add.u32", "sub.u32", "xor.b32"}},
    {"predicated_ordering_drain",
     "ld.global.u32 %r1, [%rd1];\n"
     "@%p1 fence.acq_rel.gpu;\n"
     "add.u32 %r2, %r1, 1;\nret;\n",
     {"add.u32"}},
    {"unconditional_consumer_is_terminal",
     "ld.global.u32 %r1, [%rd1];\n"
     "add.u32 %r2, %r1, 1;\n"
     "sub.u32 %r3, %r1, 2;\nret;\n",
     {"add.u32"}},
    {"diamond_is_outside_gold_subset",
     "ld.global.u32 %r1, [%rd1];\n@%p1 bra right;\n"
     "add.u32 %r2, %r1, 1;\nbra done;\n"
     "right:\nsub.u32 %r3, %r1, 2;\ndone:\nret;\n", {}, true},
    {"loop_is_outside_gold_subset",
     "again:\nld.global.u32 %r1, [%rd1];\n"
     "add.u32 %r2, %r1, 1;\n@%p1 bra again;\nret;\n", {}, true},
    {"predicated_exit_is_outside_gold_subset",
     "ld.global.u32 %r1, [%rd1];\n@%p1 ret;\n"
     "add.u32 %r2, %r1, 1;\nret;\n", {}, true},
};

void check(const Case& test)
{
    const std::string ptx =
        ".version 8.8\n.target sm_120\n.address_size 64\n"
        ".visible .entry gold()\n{\n.reg .b32 %r<8>;\n"
        ".reg .b64 %rd<4>;\n.reg .pred %p<3>;\n" +
        std::string{test.body} + "}\n";
    const auto module = hbfsim::ptx::parse_module(ptx);
    const auto& function = module.function("gold");
    const auto plan = hbfsim::ptx::analyze_futures(function);
    if (test.must_reject) {
        if (plan.exact_safe()) {
            throw std::runtime_error("unproved control flow was admitted");
        }
        return;
    }
    if (!plan.exact_safe()) {
        throw std::runtime_error("supported straight-line program rejected");
    }
    std::uint32_t producer = 0;
    for (const auto& instruction : function.instructions) {
        if (instruction.opcode == "ld.global.u32") {
            producer = instruction.instruction_id;
            break;
        }
    }
    std::set<std::string> actual;
    const auto consumers = plan.first_consumers.find(producer);
    if (consumers != plan.first_consumers.end()) {
        for (const auto& instruction : function.instructions) {
            if (consumers->second.contains(instruction.instruction_id)) {
                actual.insert(instruction.opcode);
            }
        }
    }
    if (actual != test.required_consumers) {
        throw std::runtime_error(
            "may-consume erased a future before all necessary waits");
    }
}

}  // namespace

int main()
{
    unsigned failures = 0;
    for (const auto& test : kCases) {
        try {
            check(test);
            std::cout << "PASS " << test.name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL " << test.name << ": " << error.what() << '\n';
        }
    }
    return failures == 0 ? 0 : 1;
}
