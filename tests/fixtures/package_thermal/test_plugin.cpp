#include <hbfsim/package_thermal_plugin.h>

#include <cmath>
#include <new>

namespace {

struct State {
    double temperature_c{25.0};
    const char* error{""};
};

const hbfsim_package_thermal_plugin_metadata_v1 kMetadata{
    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION,
    sizeof(hbfsim_package_thermal_plugin_metadata_v1),
    1,
    2,
    2,
    1000,
    "synthetic-test-plugin",
    "gpu,hbf",
    "gpu_temp,hbf_temp",
};

const hbfsim_package_thermal_plugin_metadata_v1* metadata()
{
    return &kMetadata;
}

void* create() { return new (std::nothrow) State{}; }

void destroy(void* instance) { delete static_cast<State*>(instance); }

int reset(void* instance, double initial_temperature_c)
{
    auto* state = static_cast<State*>(instance);
    if (state == nullptr || !std::isfinite(initial_temperature_c)) return 1;
    state->temperature_c = initial_temperature_c;
    state->error = "";
    return 0;
}

int step(void* instance,
         const double* input,
         size_t input_count,
         double* output,
         size_t output_count)
{
    auto* state = static_cast<State*>(instance);
    if (state == nullptr || input == nullptr || output == nullptr ||
        input_count != 2 || output_count != 2 || !std::isfinite(input[0]) ||
        !std::isfinite(input[1]) || input[0] < 0.0 || input[1] < 0.0) {
        if (state != nullptr) state->error = "invalid synthetic plugin step";
        return 1;
    }
    state->temperature_c = 0.9 * state->temperature_c +
                           0.05 * (input[0] + input[1]) + 2.5;
    output[0] = state->temperature_c;
    output[1] = state->temperature_c + 0.5;
    return 0;
}

const char* last_error(void* instance)
{
    const auto* state = static_cast<State*>(instance);
    return state == nullptr ? "null synthetic plugin" : state->error;
}

const hbfsim_package_thermal_plugin_api_v1 kApi{
    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION,
    sizeof(hbfsim_package_thermal_plugin_api_v1),
    metadata,
    create,
    destroy,
    reset,
    step,
    last_error,
};

}  // namespace

extern "C" const hbfsim_package_thermal_plugin_api_v1*
hbfsim_package_thermal_plugin_v1()
{
    return &kApi;
}
