#include <hbfsim/package_thermal_plugin.h>

namespace {

const hbfsim_package_thermal_plugin_metadata_v1 kMetadata{
    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION,
    sizeof(hbfsim_package_thermal_plugin_metadata_v1),
    1,
    2,
    2,
    1000,
    "synthetic-create-failure-plugin",
    "gpu,hbf",
    "gpu_temp,hbf_temp",
};

const hbfsim_package_thermal_plugin_metadata_v1* metadata()
{
    return &kMetadata;
}

void* create() { return nullptr; }
void destroy(void*) {}
int reset(void*, double) { return 1; }
int step(void*, const double*, size_t, double*, size_t) { return 1; }
const char* last_error(void*) { return "intentional create failure"; }

const hbfsim_package_thermal_plugin_api_v1 kApi{
    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION,
    sizeof(hbfsim_package_thermal_plugin_api_v1),
    metadata, create, destroy, reset, step, last_error,
};

}  // namespace

extern "C" const hbfsim_package_thermal_plugin_api_v1*
hbfsim_package_thermal_plugin_v1()
{
    return &kApi;
}
