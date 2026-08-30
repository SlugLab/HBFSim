#include <hbfsim/package_thermal_plugin.h>

#include <algorithm>
#include <cmath>
#include <new>

namespace {

constexpr std::size_t kNodeCount = 19;

struct State {
    double initial_c{30.0};
    double hbf_c{30.0};
    bool heat_triggered{false};
    bool cooling_started{false};
    const char* error{""};
};

const hbfsim_package_thermal_plugin_metadata_v1 kMetadata{
    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION,
    sizeof(hbfsim_package_thermal_plugin_metadata_v1),
    1,
    kNodeCount,
    kNodeCount,
    1000,
    "synthetic-package-service-test-only",
    "gpu,hbm,hbf.base,hbf.s0.l0,hbf.s0.l1,hbf.s0.l2,hbf.s0.l3,hbf.s0.l4,hbf.s0.l5,hbf.s0.l6,hbf.s0.l7,hbf.s1.l0,hbf.s1.l1,hbf.s1.l2,hbf.s1.l3,hbf.s1.l4,hbf.s1.l5,hbf.s1.l6,hbf.s1.l7",
    "gpu,hbm,hbf.base,hbf.s0.l0,hbf.s0.l1,hbf.s0.l2,hbf.s0.l3,hbf.s0.l4,hbf.s0.l5,hbf.s0.l6,hbf.s0.l7,hbf.s1.l0,hbf.s1.l1,hbf.s1.l2,hbf.s1.l3,hbf.s1.l4,hbf.s1.l5,hbf.s1.l6,hbf.s1.l7",
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
    state->initial_c = initial_temperature_c;
    state->hbf_c = initial_temperature_c;
    state->heat_triggered = false;
    state->cooling_started = false;
    state->error = "";
    return 0;
}

int step(void* instance, const double* input, size_t input_count,
         double* output, size_t output_count)
{
    auto* state = static_cast<State*>(instance);
    if (state == nullptr || input == nullptr || output == nullptr ||
        input_count != kNodeCount || output_count != kNodeCount) {
        if (state != nullptr) state->error = "invalid package test step";
        return 1;
    }
    bool nand_active = false;
    for (std::size_t index = 0; index < kNodeCount; ++index) {
        if (!std::isfinite(input[index]) || input[index] < 0.0) {
            state->error = "invalid package test power";
            return 1;
        }
        if (index >= 3 && input[index] > 0.0) nand_active = true;
    }
    if (nand_active && !state->cooling_started) {
        state->heat_triggered = true;
        state->hbf_c = 95.0;
    } else {
        if (state->heat_triggered) state->cooling_started = true;
        state->hbf_c = std::max(state->initial_c, state->hbf_c - 1.0);
    }
    for (std::size_t index = 0; index < kNodeCount; ++index) {
        output[index] = index < 2 ? state->initial_c : state->hbf_c;
    }
    return 0;
}

const char* last_error(void* instance)
{
    const auto* state = static_cast<State*>(instance);
    return state == nullptr ? "null package test plugin" : state->error;
}

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
