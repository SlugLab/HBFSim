#include "ptx_ir.hpp"
#include "ptx_analysis.hpp"
#include <cstdio>
#include <cstdlib>
#include <string>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)

int main()
{
    const std::string text=R"PTX(.version 9.0
.target sm_120
.address_size 64
// Unrelated text and function must survive byte replacement.
.visible .func unrelated() { ret; }
.visible .entry kernel(.param .u64 input, .param .u64 output)
{
 .reg .b64 %rd<8>; .reg .b32 %r<8>; .reg .pred %p<3>;
 ld.param.u64 %rd0, [input]; ld.param.u64 %rd1, [output];
 cvta.to.global.u64 %rd2, %rd0;
 mov.u32 %r0, %tid.x; mul.wide.u32 %rd3, %r0, 4;
 add.u64 %rd4, %rd2, %rd3;
 .loc 1 17 0
 @%p0 ld.global.u32 /* frozen comment */ %r1,
     [%rd4]; add.u32 %r2, %r1, 1; st.global.u32 [%rd1], %r2;
 ret;
}
)PTX";
    try {
#ifdef HBFSIM_PTX_SOURCE_SPANS
        const auto module=hbfsim::ptx::parse_module_spanned(text);
#else
        const auto module=hbfsim::ptx::parse_module(text);
#endif
        const auto& function=module.function("kernel");
        CHECK(function.instructions.size()==10);
        const auto plan=hbfsim::ptx::analyze_futures(function);
        CHECK(plan.exact_safe());
        CHECK(function.instructions[0].defs==std::vector<std::string>{"%rd0"});
        CHECK(function.instructions[2].defs==std::vector<std::string>{"%rd2"});
        CHECK(function.instructions[3].uses==std::vector<std::string>{"%tid.x"});
#ifdef HBFSIM_PTX_SOURCE_SPANS
        const auto& load=function.instructions[6];
        const auto original=text.substr(load.span.begin,load.span.end-load.span.begin);
        CHECK(original=="@%p0 ld.global.u32 /* frozen comment */ %r1,\n     [%rd4];");
        CHECK(function.register_types.at("%rd4")=="b64");
        CHECK(function.parameter_types.at("input")=="u64");
        CHECK(text.at(function.body_begin)=='{' && text.at(function.body_end)=='}');
#endif
    } catch(const std::exception& error) {
        std::fprintf(stderr,"FAIL: real setup/packed byte spans: %s\n",error.what());return 1;
    }
    std::puts("PASS: exact packed/multiline statement spans and real kernel setup def-use");
}
