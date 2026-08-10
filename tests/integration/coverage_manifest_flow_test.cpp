#include "hbfsim/coverage.hpp"

#include <dlfcn.h>
#include <json.hpp>
#include <unistd.h>

#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

int main(int argc, char** argv)
{
    assert(argc == 3);
    void* plugin = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    assert(plugin != nullptr);
    using process_type = int (*)(const char*, int, char*);
    auto process = reinterpret_cast<process_type>(dlsym(plugin, "process_input"));
    assert(process != nullptr);

    std::ifstream ptx_input(argv[2]);
    const std::string ptx{std::istreambuf_iterator<char>(ptx_input), {}};
    const nlohmann::json request{
        {"input",
         {{"full_ptx", ptx},
          {"to_patch_kernel", "unsupported_pointer"},
          {"global_ebpf_map_info_symbol", "map_info"},
          {"ebpf_communication_data_symbol", "constData"}}},
        {"ebpf_instructions", nlohmann::json::array()},
    };

    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-plugin-flow-" + std::to_string(getpid()) + ".jsonl");
    setenv("HBFSIM_PASS_MANIFEST_PATH", path.c_str(), 1);
    std::vector<char> output(16 * 1024 * 1024);
    assert(process(request.dump().c_str(), static_cast<int>(output.size()),
                   output.data()) == 0);

    std::ifstream manifest_input(path);
    const std::string manifest_json{
        std::istreambuf_iterator<char>(manifest_input), {}};
    const auto manifest = hbfsim::module_manifest_from_json(manifest_json);
    assert(manifest.module_id.starts_with("ptx:"));
    assert(manifest.kernel == "unsupported_pointer");
    assert(manifest.ptx_target == "sm_120");
    assert(!manifest.instrumented);
    assert(manifest.parameters.size() == 1);
    assert(manifest.parameters[0].kind == hbfsim::ParameterKind::Pointer);

    hbfsim::CoverageGate gate;
    gate.add_module(manifest);
    gate.add_range(0x100000, 0x101000);
    const auto decision = gate.check_launch({
        .module_id = manifest.module_id,
        .kernel = manifest.kernel,
        .parameters = {{.index = 0,
                        .offset = 0,
                        .width = 8,
                        .slots = {{.offset = 0, .value = 0x100100}}}},
    });
    assert(!decision.allowed);
    assert(decision.reason == "unsupported_operation");
    assert(decision.operation.starts_with("atom.global"));

    std::filesystem::remove(path);
    dlclose(plugin);
}
