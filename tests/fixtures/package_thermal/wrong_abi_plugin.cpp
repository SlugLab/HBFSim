#include <hbfsim/package_thermal_plugin.h>

namespace {

const hbfsim_package_thermal_plugin_api_v1 kApi{
    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION + 1,
    sizeof(hbfsim_package_thermal_plugin_api_v1),
    nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

extern "C" const hbfsim_package_thermal_plugin_api_v1*
hbfsim_package_thermal_plugin_v1()
{
    return &kApi;
}
