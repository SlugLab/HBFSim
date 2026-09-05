#include "ptx_ir.hpp"

#include <iostream>
#include <stdexcept>
#include <string>

namespace {

std::string module_with(const std::string& body)
{
    return ".version 8.8\n.target sm_120\n.address_size 64\n"
           ".visible .entry gold()\n{\n" + body + "}\n";
}

void require(bool condition, const char* reason)
{
    if (!condition) {
        throw std::runtime_error(reason);
    }
}

void decorated_load()
{
    const auto module = hbfsim::ptx::parse_module(module_with(
        ".reg .b32 %r1;\n.reg .b64 %rd1;\n"
        "/* a comment spanning\nphysical lines */\n"
        ".loc 1 17 0\n"
        "ld.global.u32 /* inline comment */ %r1,\n"
        "  [%rd1]; // trailing comment\nret;\n"));
    const auto& function = module.function("gold");
    require(function.instructions.size() == 2,
            "comments or location directives became instructions");
    const auto& load = function.instructions.front();
    require(load.opcode == "ld.global.u32" && load.memory.has_value() &&
                load.memory->bytes == 4 && load.location.line == 11,
            "decorated multiline load lost its memory operation or location");
}

void packed_declaration_must_not_hide_load()
{
    try {
        const auto module = hbfsim::ptx::parse_module(module_with(
            ".reg .b32 %r1; ld.global.u32 %r1, [%rd1];\nret;\n"));
        for (const auto& instruction : module.function("gold").instructions) {
            if (instruction.memory.has_value()) {
                return;
            }
        }
    } catch (const hbfsim::ptx::ParseError&) {
        return;  // Explicitly rejecting unsupported packing is safe.
    }
    throw std::runtime_error("declaration swallowed a memory operation");
}

void unterminated_comment_is_rejected()
{
    try {
        (void)hbfsim::ptx::parse_module(
            module_with("ret;\n") + "/* unterminated\n");
    } catch (const hbfsim::ptx::ParseError&) {
        return;
    }
    throw std::runtime_error("unterminated block comment accepted");
}

void packed_continuation_is_rejected()
{
    try {
        (void)hbfsim::ptx::parse_module(module_with(
            "add.u32 %r2, %r3,\n"
            "1; ld.global.u32 %r1, [%rd1];\nret;\n"));
    } catch (const hbfsim::ptx::ParseError&) {
        return;
    }
    throw std::runtime_error("continuation swallowed a memory operation");
}

void packed_location_is_rejected()
{
    try {
        (void)hbfsim::ptx::parse_module(module_with(
            ".loc 1 17 0; ld.global.u32 %r1, [%rd1];\nret;\n"));
    } catch (const hbfsim::ptx::ParseError&) {
        return;
    }
    throw std::runtime_error("location directive swallowed a memory operation");
}

void reject_module(const std::string& ptx)
{
    try {
        (void)hbfsim::ptx::parse_module(ptx);
    } catch (const hbfsim::ptx::ParseError&) {
        return;
    }
    throw std::runtime_error("unsupported syntax hid a memory operation");
}

void location_with_instruction_is_rejected()
{
    reject_module(module_with(
        ".loc 1 17 0 ld.global.u32 %r1, [%rd1];\nret;\n"));
}

void inline_label_is_rejected()
{
    reject_module(module_with(
        "load_path: ld.global.u32 %r1, [%rd1];\nret;\n"));
}

void unsupported_function_header_is_rejected()
{
    reject_module(
        ".version 8.8\n.target sm_120\n.address_size 64\n"
        ".visible .func (.param .b32 retval) helper()\n{\n"
        "ld.global.u32 %r1, [%rd1];\nret;\n}\n");
}

}  // namespace

int main()
{
    unsigned failures = 0;
    const auto run = [&](const char* name, void (*test)()) {
        try {
            test();
            std::cout << "PASS " << name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL " << name << ": " << error.what() << '\n';
        }
    };
    run("decorated_multiline_load", decorated_load);
    run("packed_declaration_coverage", packed_declaration_must_not_hide_load);
    run("unterminated_comment", unterminated_comment_is_rejected);
    run("packed_continuation", packed_continuation_is_rejected);
    run("packed_location", packed_location_is_rejected);
    run("location_with_instruction", location_with_instruction_is_rejected);
    run("inline_label", inline_label_is_rejected);
    run("unsupported_function_header", unsupported_function_header_is_rejected);
    return failures == 0 ? 0 : 1;
}
