#include <hbfsim/package_thermal.hpp>

#include <cassert>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace thermal = hbfsim::package_thermal;

namespace {

bool rejects(const std::function<void()>& operation)
{
    try {
        operation();
    } catch (const thermal::ThermalError&) {
        return true;
    }
    return false;
}

}  // namespace

int main(int argc, char** argv)
{
    assert(argc == 4);
    const std::vector<std::string> inputs{"gpu", "hbf"};
    const std::vector<std::string> outputs{"gpu_temp", "hbf_temp"};
    const auto missing = std::filesystem::path(argv[1]).parent_path() /
                         "intentionally-absent-package-plugin.so";
    assert(rejects([&] {
        (void)thermal::load_thermal_plugin(missing, inputs, outputs);
    }));
    assert(rejects([&] {
        (void)thermal::load_thermal_plugin(argv[1], inputs, outputs);
    }));
    assert(rejects([&] {
        (void)thermal::load_thermal_plugin(argv[2], inputs, outputs);
    }));
    assert(rejects([&] {
        (void)thermal::load_thermal_plugin(argv[3], inputs, outputs);
    }));
    return 0;
}
