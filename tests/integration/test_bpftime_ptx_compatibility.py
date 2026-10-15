#!/usr/bin/env python3
"""CPU-only regression: compile actual patched helper, wrapper and worker body.

The fake nvPTXCompiler API deliberately fails; this is not CUDA or GB10 testing.
Use --source pointing to the copy prepared by PreparePatchedBpftime.cmake.
"""
import argparse
import pathlib
import subprocess
import tempfile

METADATA = r'''
#include "nv_attach_ptx_compatibility.hpp"
#include <cassert>
using namespace bpftime::attach;
std::string ptx(std::string version, std::string target) {
    return ".version " + version + "\n.target " + target + ", texmode_unified\n.address_size 64\n.visible .entry k(){ret;}\n";
}
void rejected(std::string source, std::string target) {
    bool threw = false;
    try { prepare_ptx_for_target(source, target); } catch (const std::exception &) { threw = true; }
    assert(threw);
}
int main() {
    for (auto item : {std::pair{"sm_70", "6.0"}, {"sm_72", "6.1"},
                      {"sm_75", "6.3"}, {"sm_80", "7.0"},
                      {"sm_86", "7.1"}, {"sm_87", "7.4"},
                      {"sm_89", "7.8"}, {"sm_90", "7.8"}, {"sm_90a", "8.0"}}) {
        auto output = prepare_ptx_for_target(ptx("5.0", "sm_60"), item.first);
        assert(output.text.find(std::string(".version ") + item.second) == 0);
    }
    for (auto item : {std::pair{"sm_100", "8.6"}, {"sm_120", "8.7"},
                      {"sm_121", "8.8"}, {"sm_121a", "8.8"},
                      {"sm_100f", "8.8"}, {"sm_120f", "8.8"}}) {
        auto output = prepare_ptx_for_target(ptx("7.0", "sm_60"), item.first);
        assert(output.text.find(std::string(".version ") + item.second) == 0);
        assert(output.target == item.first);
        assert(output.text.find(", texmode_unified") != std::string::npos);
        assert(output.text.find(".visible .entry k(){ret;}") != std::string::npos);
    }
    assert(prepare_ptx_for_target(ptx("9.10", "sm_121"), "sm_121").text == ptx("9.10", "sm_121"));
    assert(prepare_ptx_for_target(ptx("8.8", "sm_121a"), "sm_121").target == "sm_121a");
    assert(prepare_ptx_for_target(ptx("8.8", "sm_120f"), "sm_121").target == "sm_121f");
    assert(prepare_ptx_for_target(ptx("8.8", "sm_100f"), "sm_103a").target == "sm_103a");
    assert(prepare_ptx_for_target(ptx("9.10", "sm_130"), "sm_130").text == ptx("9.10", "sm_130"));
    assert(prepare_ptx_for_target(ptx("7.0", "sm_60"), "sm_61").text.find(".version 7.0") == 0);
    rejected(ptx("8.8", "sm_120a"), "sm_121");
    rejected(ptx("8.8", "sm_121a"), "sm_121f");
    rejected(ptx("8.8", "sm_101f"), "sm_103");
    rejected(ptx("8.8", "sm_100f"), "sm_121");
    rejected(ptx("8.8", "sm_121"), "sm_120");
    rejected(ptx("8.8", "sm_121"), "sm_130");
    rejected(ptx("8.8", "sm_80a"), "sm_80");
    rejected(ptx("8.8", "sm_80"), "sm_80a");
    rejected(".version 8.8\n.version 8.8\n.target sm_121\n", "sm_121");
    rejected(".version x.y\n.target sm_121\n", "sm_121");
    rejected(ptx("8.8", "sm_121") + ".target sm_120a\n", "sm_121");
    auto commented = "// .version 1.0\n/* .target sm_120a */\n" + ptx("8.8", "sm_121a");
    assert(prepare_ptx_for_target(commented, "sm_121").text == commented);
    auto crlf = std::string(".version 8.7\r\n.target sm_121\r\n.address_size 64\r\n");
    assert(prepare_ptx_for_target(crlf, "sm_121").text.find(".version 8.8\r\n") == 0);
    auto multiple = prepare_ptx_for_target(ptx("6.0", "sm_70") + ".target sm_70, debug\n", "sm_89");
    assert(multiple.text.find(".version 7.8") == 0);
    assert(multiple.text.find(".target sm_70") == std::string::npos);
    assert(multiple.text.find(".target sm_89, texmode_unified") != std::string::npos);
    assert(multiple.text.find(".target sm_89, debug") != std::string::npos);
    auto multiple_features = prepare_ptx_for_target(ptx("8.8", "sm_121") + ".target sm_121a\n", "sm_121");
    assert(multiple_features.target == "sm_121a");
    assert(multiple_features.text.find(".target sm_121,") == std::string::npos);
    assert(multiple_features.text.find(".target sm_121a, texmode_unified") != std::string::npos);
    assert(multiple_features.text.find(".target sm_121a\n") != std::string::npos);
}
'''

API = r'''
#pragma once
#include <cstddef>
struct FakeCompiler {};
using nvPTXCompilerHandle = FakeCompiler *;
using nvPTXCompileResult = int;
constexpr int NVPTXCOMPILE_SUCCESS = 0;
extern int fake_mode, fake_destroyed;
int nvPTXCompilerCreate(nvPTXCompilerHandle *, std::size_t, const char *);
int nvPTXCompilerDestroy(nvPTXCompilerHandle *);
int nvPTXCompilerCompile(nvPTXCompilerHandle, int, const char **);
int nvPTXCompilerGetVersion(unsigned *, unsigned *);
int nvPTXCompilerGetErrorLogSize(nvPTXCompilerHandle, std::size_t *);
int nvPTXCompilerGetErrorLog(nvPTXCompilerHandle, char *);
int nvPTXCompilerGetCompiledProgramSize(nvPTXCompilerHandle, std::size_t *);
int nvPTXCompilerGetCompiledProgram(nvPTXCompilerHandle, void *);
int nvPTXCompilerGetInfoLogSize(nvPTXCompilerHandle, std::size_t *);
int nvPTXCompilerGetInfoLog(nvPTXCompilerHandle, char *);
'''
FAKE_API = r'''
#include "nvPTXCompiler.h"
#include <cstring>
int fake_mode = 0, fake_destroyed = 0;
int nvPTXCompilerCreate(nvPTXCompilerHandle *h, std::size_t, const char *) { *h = new FakeCompiler; return 0; }
int nvPTXCompilerDestroy(nvPTXCompilerHandle *h) { delete *h; *h = nullptr; ++fake_destroyed; return 0; }
int nvPTXCompilerCompile(nvPTXCompilerHandle, int, const char **) { return 3; }
int nvPTXCompilerGetVersion(unsigned *a, unsigned *b) { *a = 13; *b = 7; return 0; }
int nvPTXCompilerGetErrorLogSize(nvPTXCompilerHandle, std::size_t *n) { *n=64; return fake_mode == 1 ? 2 : 0; }
int nvPTXCompilerGetErrorLog(nvPTXCompilerHandle, char *out) { std::strcpy(out, "FAKE detailed PTX target diagnostic"); return fake_mode == 2 ? 2 : 0; }
int nvPTXCompilerGetCompiledProgramSize(nvPTXCompilerHandle, std::size_t *n) { *n=0; return 0; }
int nvPTXCompilerGetCompiledProgram(nvPTXCompilerHandle, void *) { return 0; }
int nvPTXCompilerGetInfoLogSize(nvPTXCompilerHandle, std::size_t *n) { *n=0; return 0; }
int nvPTXCompilerGetInfoLog(nvPTXCompilerHandle, char *) { return 0; }
'''
WRAPPER = r'''
#include "nvPTXCompiler.h"
#include <cassert>
#include <string>
struct nv_attach_impl_ptx_compiler;
extern "C" {
nv_attach_impl_ptx_compiler *nv_attach_impl_create_compiler();
void nv_attach_impl_destroy_compiler(nv_attach_impl_ptx_compiler *);
int nv_attach_impl_compile(nv_attach_impl_ptx_compiler *, const char *, const char **, int);
const char *nv_attach_impl_get_error_log(nv_attach_impl_ptx_compiler *);
}
int main() {
    const char *options[] = {"--gpu-name=sm_121a", "--verbose", "-O3"};
    for (fake_mode = 0; fake_mode < 3; ++fake_mode) {
        auto *compiler = nv_attach_impl_create_compiler();
        assert(nv_attach_impl_compile(compiler, ".version 8.8\n.target sm_121a\n", options, 3) == -1);
        std::string error = nv_attach_impl_get_error_log(compiler);
        assert(error.find("result=3") != std::string::npos);
        assert(error.find("nvPTXCompiler=13.7") != std::string::npos);
        assert(error.find(" compiler_owner=") != std::string::npos);
        assert(error.find(" version_owner=") != std::string::npos);
        assert(error.find("compiler_owner=unknown") == std::string::npos);
        assert(error.find("version_owner=unknown") == std::string::npos);
        assert(error.find("--gpu-name=sm_121a --verbose -O3") != std::string::npos);
        if (fake_mode == 0) assert(error.find("FAKE detailed PTX target diagnostic") != std::string::npos);
        if (fake_mode == 1) assert(error.find("size_status=2") != std::string::npos);
        if (fake_mode == 2) assert(error.find("log_status=2") != std::string::npos);
        nv_attach_impl_destroy_compiler(compiler);
    }
    assert(fake_destroyed == 3);
}
'''
WORKER_PRELUDE = r'''
#include "nv_attach_ptx_compatibility.hpp"
#include <atomic>
#include <cassert>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <thread>
#include <tuple>
#include <vector>
#include <spdlog/spdlog.h>
#define NVPTXCOMPILER_CHECK_EXCEPTION(expr,message) do { if ((expr) != 0) throw std::runtime_error(message); } while(0)
constexpr int NVPTXCOMPILE_SUCCESS = 0;
int nvPTXCompilerGetVersion(unsigned *a, unsigned *b) { *a=12; *b=8; return 0; }
std::atomic<bool> fixture_mutex_live{false};
// Transparent lifetime observer: inherited std::mutex remains the lock used by
// the actual worker. This makes destruction order observable without UB/ASAN.
struct lifetime_observer_mutex : std::mutex {
    lifetime_observer_mutex() { fixture_mutex_live=true; }
    ~lifetime_observer_mutex() { fixture_mutex_live=false; }
};
namespace boost::asio {
inline void (*before_post)()=nullptr, (*after_post)()=nullptr, (*before_pool_join)()=nullptr;
struct thread_pool {
    std::vector<std::thread> tasks;
    explicit thread_pool(unsigned) {}
    ~thread_pool() { if(before_pool_join)before_pool_join(); join(); }
    void join() { for(auto &t:tasks)if(t.joinable())t.join(); }
};
template<class F> void post(thread_pool &pool, F f) {
    if(before_post)before_post();
    pool.tasks.emplace_back(f);
    if(after_post)after_post();
}
}
struct nv_attach_impl_ptx_compiler {};
struct handler_type {
    nv_attach_impl_ptx_compiler *(*create)();
    void (*destroy)(nv_attach_impl_ptx_compiler *);
    int (*compile)(nv_attach_impl_ptx_compiler *,const char *,const char **,int);
    const char *(*get_error_log)(nv_attach_impl_ptx_compiler *);
    const char *(*get_info_log)(nv_attach_impl_ptx_compiler *);
    int (*get_compiled_program)(nv_attach_impl_ptx_compiler *,uint8_t **,std::size_t *);
};
namespace bpftime::attach {
std::atomic<int> destroyed{0};
std::string get_gpu_sm_arch() { return "sm_121"; }
std::string sha256(const void *p,std::size_t n) { return std::string(static_cast<const char *>(p),n); }
struct nv_attach_impl { handler_type ptx_compiler; };
struct fatbin_record {
    std::shared_ptr<std::map<std::string,std::vector<uint8_t>>> ptx_pool = std::make_shared<std::map<std::string,std::vector<uint8_t>>>();
    std::map<std::string,std::vector<uint8_t>> compile_ptxs(nv_attach_impl &,std::map<std::string,std::tuple<std::string,bool>>);
};
'''
WORKER_END = r'''
std::atomic<bool> hold_compile{false},compile_entered{false},release_compile{false};
int fixture_posts=0;
} // namespace bpftime::attach
int main() {
    using namespace bpftime::attach;
    nv_attach_impl impl;
    impl.ptx_compiler = {
        +[](){return new nv_attach_impl_ptx_compiler;},
        +[](nv_attach_impl_ptx_compiler *p){delete p; ++destroyed;},
        +[](nv_attach_impl_ptx_compiler *,const char *,const char **args,int){
            assert(std::string(args[0])=="--gpu-name=sm_121a");
            if(hold_compile){compile_entered=true;while(!release_compile)std::this_thread::yield();}
            return -1;
        },
        +[](nv_attach_impl_ptx_compiler *){return "FAKE worker compile error";},
        +[](nv_attach_impl_ptx_compiler *){return "";},
        +[](nv_attach_impl_ptx_compiler *,uint8_t **,std::size_t *){return 0;}
    };
    fatbin_record record;
    std::string text = ".version 8.8\n.target sm_121a\n.address_size 64\n";
    bool caught = false;
    try { record.compile_ptxs(impl,{{"b",{text,false}},{"a",{text,false}}}); }
    catch(const std::exception &error) {
        std::string message=error.what();
        assert(message.find("a:") < message.find("b:"));
        assert(message.find("FAKE worker compile error") != std::string::npos);
        caught=true;
    }
    assert(caught && destroyed==2);
    // Force a real submitted worker into compile, then throw from the next
    // submission. The pool must join before the referenced mutex is destroyed.
    hold_compile=true;
    boost::asio::before_post=+[]{if(++fixture_posts==2)throw std::runtime_error("FAKE submission failure");};
    boost::asio::after_post=+[]{while(!compile_entered)std::this_thread::yield();};
    boost::asio::before_pool_join=+[]{assert(fixture_mutex_live);release_compile=true;};
    fatbin_record second;
    caught=false;
    try {second.compile_ptxs(impl,{{"a",{text,false}},{"b",{text,false}}});}
    catch(const std::exception &error){assert(std::string(error.what()).find("FAKE submission failure")!=std::string::npos);caught=true;}
    assert(caught && compile_entered && release_compile && destroyed==3);
    boost::asio::before_post=nullptr;
    boost::asio::after_post=nullptr;
    boost::asio::before_pool_join=nullptr;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=pathlib.Path, required=True)
    parser.add_argument('--cxx', default='c++')
    parser.add_argument('--case', action='append', choices=('metadata', 'wrapper', 'worker'),
                        help='Run only selected affected cases; default runs all three.')
    args = parser.parse_args()
    source = args.source.resolve() / 'attach/nv_attach_impl'
    helper = source / 'nv_attach_ptx_compatibility.hpp'
    assert helper.is_file(), 'apply both patches to a clean copy first'
    with tempfile.TemporaryDirectory(prefix='hbfsim-ptx-compatibility-') as directory:
        root = pathlib.Path(directory)
        (root / 'spdlog').mkdir()
        (root / 'spdlog/spdlog.h').write_text(
            '#pragma once\n#include <iostream>\n'
            'template<class... T> void fake_log(const T&... args){((std::cerr << args << " "),...);std::cerr << "\\n";}\n'
            '#define SPDLOG_INFO(...) fake_log(__VA_ARGS__)\n'
            '#define SPDLOG_ERROR(...) fake_log(__VA_ARGS__)\n'
            '#define SPDLOG_DEBUG(...) fake_log(__VA_ARGS__)\n')
        (root / 'nvPTXCompiler.h').write_text(API)
        (root / 'fake_api.cpp').write_text(FAKE_API)
        (root / 'metadata.cpp').write_text(METADATA)
        (root / 'wrapper.cpp').write_text(WRAPPER)
        fatbin = (source / 'nv_attach_fatbin_record.cpp').read_text()
        start = fatbin.index('std::map<std::string, std::vector<uint8_t>> fatbin_record::compile_ptxs(')
        end = fatbin.index('void fatbin_record::try_loading_ptxs', start)
        worker_body = fatbin[start:end].replace('std::mutex map_lock;', 'lifetime_observer_mutex map_lock;')
        (root / 'worker.cpp').write_text(WORKER_PRELUDE + worker_body + WORKER_END)
        cases = [
            ('metadata', [root / 'metadata.cpp']),
            ('wrapper', [root / 'wrapper.cpp', root / 'fake_api.cpp', source / 'ptx_compiler/ptx_compiler.cpp']),
            ('worker', [root / 'worker.cpp'])]
        for name, files in cases:
            if args.case and name not in args.case:
                continue
            binary = root / name
            subprocess.run([args.cxx, '-std=c++20', '-pthread', '-I', str(root), '-I', str(source),
                            *map(str, files), '-ldl', '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            print(name + ': PASS (CPU fixture; no CUDA/GPU)')


if __name__ == '__main__':
    main()
