#!/usr/bin/env python3
"""Actual supported-profile worker body and the shared metadata/wrapper cases.

CPU compiler fixtures use fake CUDA APIs. They do not validate a GPU or GB10.
"""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile


def added_file(patch, target):
    text = patch.read_text()
    begin = text.index('diff --git a/' + target + ' b/' + target + '\n')
    end = text.find('\ndiff --git ', begin + 1)
    section = text[begin:end if end >= 0 else None]
    return ''.join(line[1:] + '\n' for line in section.splitlines()
                   if line.startswith('+') and not line.startswith('+++'))


END = r'''
std::atomic<int> calls{0};
std::atomic<bool> fail_compile{false};
std::atomic<bool> hold_compile{false},compile_entered{false},release_compile{false};
std::mutex observed_lock;
std::vector<std::vector<std::string>> observed;
} // namespace bpftime::attach
int main() {
    using namespace bpftime::attach;
    nv_attach_impl impl;
    impl.ptx_compiler = {
        +[](){return new nv_attach_impl_ptx_compiler;},
        +[](nv_attach_impl_ptx_compiler *p){delete p; ++destroyed;},
        +[](nv_attach_impl_ptx_compiler *,const char *,const char **args,int count){
            ++calls;
            std::vector<std::string> row;
            for (int i=0;i<count;++i) row.emplace_back(args[i]);
            assert(row[0]=="--gpu-name=sm_121a");
            {std::lock_guard<std::mutex> guard(observed_lock);observed.push_back(row);}
            if(hold_compile){compile_entered=true;while(!release_compile)std::this_thread::yield();}
            return fail_compile.load() ? -1 : 0;
        },
        +[](nv_attach_impl_ptx_compiler *){return "FAKE supported worker error";},
        +[](nv_attach_impl_ptx_compiler *){return "";},
        +[](nv_attach_impl_ptx_compiler *,uint8_t **out,std::size_t *size){static uint8_t byte=42;*out=&byte;*size=1;return 0;}
    };
    const std::string a(64,'a'),b(64,'b');
    setenv("HBFSIM_NVPTX_OPT_LEVEL","1",1);
    setenv("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256",(a+"=0,"+b+"=1").c_str(),1);
    setenv("HBFSIM_NVPTX_MAX_REGISTERS","64",1);
    fatbin_record record;
    record.original_ptx["raw.ptx"]="RAW_A";
    const std::string text=".version 8.8\n.target sm_121a\n.address_size 64\n";
    auto candidate=std::map<std::string,std::tuple<std::string,bool>>{{"patched.raw.ptx",{text,false}}};
    assert(record.compile_ptxs(impl,candidate,false).size()==1);
    assert(calls==1 && observed.back()[2]=="-O0" && observed.back()[3]=="--maxrregcount=64");
    assert(record.compile_ptxs(impl,candidate,false).size()==1 && calls==1); // cache hit
    setenv("HBFSIM_NVPTX_MAX_REGISTERS","65",1);
    record.compile_ptxs(impl,candidate,false);
    assert(calls==2 && observed.back()[3]=="--maxrregcount=65"); // option-sensitive key
    setenv("HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256",(a+"=2,"+b+"=1").c_str(),1);
    record.compile_ptxs(impl,candidate,false);
    assert(calls==3 && observed.back()[2]=="-O2");
    const auto staged=b+".ptx";
    record.original_ptx[staged]="RAW_A"; // would select a if prepatched identity were lost
    record.compile_ptxs(impl,{{"patched."+staged,{text,false}}},true);
    assert(calls==4 && observed.back()[2]=="-O1");
    fail_compile=true;
    record.original_ptx["a"]="RAW_A";
    record.original_ptx["b"]="RAW_B";
    bool caught=false;
    try {record.compile_ptxs(impl,{{"patched.b",{text+"//fail\n",false}},
                                  {"patched.a",{text+"//fail\n",false}}},false);}
    catch(const std::exception &error) {
        const std::string message=error.what();
        assert(message.find("patched.a:") < message.find("patched.b:"));
        assert(message.find("FAKE supported worker error") != std::string::npos);
        caught=true;
    }
    assert(caught && calls==6 && destroyed==6);
    // Keep the first worker inside compile while the next module throws from
    // the real main-thread lookup / prepatched raw-digest validation paths.
    for(int kind=0;kind<2;++kind) {
        hold_compile=true;compile_entered=false;release_compile=false;
        boost::asio::after_post=+[]{while(!compile_entered)std::this_thread::yield();};
        boost::asio::before_pool_join=+[]{assert(fixture_mutex_live);release_compile=true;};
        fatbin_record probe;
        std::map<std::string,std::tuple<std::string,bool>> plans;
        if(kind==0) {
            probe.original_ptx["a"]="RAW_A";
            plans={{"patched.a",{text,false}},{"patched.b",{text,false}}};
        } else {
            probe.original_ptx[a+".ptx"]="RAW_A";
            probe.original_ptx["z.ptx"]="RAW_B";
            plans={{"patched."+a+".ptx",{text,false}},{"patched.z.ptx",{text,false}}};
        }
        caught=false;
        try {probe.compile_ptxs(impl,plans,kind==1);}
        catch(const std::exception &error) {
            const std::string message=error.what();
            assert(message.find(kind==0 ? "Unable to associate" : "Prepatched module filename")!=std::string::npos);
            caught=true;
        }
        assert(caught && compile_entered && release_compile && calls==7+kind && destroyed==7+kind);
        boost::asio::after_post=nullptr;
        boost::asio::before_pool_join=nullptr;
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--cxx', default='c++')
    parser.add_argument('--case', action='append', choices=('metadata', 'wrapper', 'worker'))
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('shared_compatibility_cases', here / 'test_bpftime_ptx_compatibility.py')
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    source = args.source.resolve() / 'attach/nv_attach_impl'
    ordinary = here.parent.parent / 'patches/bpftime/0002-ptx-target-compatibility-and-diagnostics.patch'
    expected = added_file(ordinary, 'attach/nv_attach_impl/nv_attach_ptx_compatibility.hpp').encode()
    assert (source / 'nv_attach_ptx_compatibility.hpp').read_bytes() == expected, 'shared metadata helper forked'
    prelude = '#include "nvptx_opt_policy.hpp"\n' + common.WORKER_PRELUDE
    prelude = prelude.replace('std::string(static_cast<const char *>(p),n);',
        '(std::string(static_cast<const char *>(p),n)=="RAW_A" ? std::string(64,\'a\') : '
        'std::string(static_cast<const char *>(p),n)=="RAW_B" ? std::string(64,\'b\') : std::string(static_cast<const char *>(p),n));')
    prelude = prelude.replace('struct fatbin_record {', 'struct fatbin_record {\nstd::map<std::string,std::string> original_ptx;')
    prelude = prelude.replace('std::map<std::string,std::tuple<std::string,bool>>);',
                              'std::map<std::string,std::tuple<std::string,bool>>,bool);')
    body = (source / 'nv_attach_fatbin_record.cpp').read_text()
    begin = body.index('std::map<std::string, std::vector<uint8_t>> fatbin_record::compile_ptxs(')
    end = body.index('void fatbin_record::try_loading_ptxs', begin)
    with tempfile.TemporaryDirectory(prefix='hbfsim-supported-compatibility-') as directory:
        root = Path(directory)
        (root / 'spdlog').mkdir()
        (root / 'spdlog/spdlog.h').write_text(
            '#pragma once\n#include <iostream>\n'
            'template<class... T> void fake_log(const T&... a){((std::cerr<<a<<" "),...);std::cerr<<"\\n";}\n'
            '#define SPDLOG_INFO(...) fake_log(__VA_ARGS__)\n#define SPDLOG_ERROR(...) fake_log(__VA_ARGS__)\n#define SPDLOG_DEBUG(...) fake_log(__VA_ARGS__)\n')
        worker_body = body[begin:end].replace('std::mutex map_lock;', 'lifetime_observer_mutex map_lock;')
        for name, text in {'nvPTXCompiler.h': common.API, 'fake_api.cpp': common.FAKE_API,
                           'metadata.cpp': common.METADATA, 'wrapper.cpp': common.WRAPPER,
                           'worker.cpp': prelude + worker_body + END}.items():
            (root / name).write_text(text)
        for name, files in [('metadata', [root / 'metadata.cpp']),
                            ('wrapper', [root / 'wrapper.cpp', root / 'fake_api.cpp', source / 'ptx_compiler/ptx_compiler.cpp']),
                            ('worker', [root / 'worker.cpp'])]:
            if args.case and name not in args.case:
                continue
            binary = root / name
            subprocess.run([args.cxx, '-std=c++20', '-pthread', '-I', str(root), '-I', str(source),
                            *map(str, files), '-ldl', '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            print(name + ': PASS (supported profile CPU fixture, no GPU)')


if __name__ == '__main__':
    main()
