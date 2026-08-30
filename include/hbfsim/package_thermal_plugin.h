#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION 1u
#define HBFSIM_PACKAGE_THERMAL_PLUGIN_SYMBOL \
    "hbfsim_package_thermal_plugin_v1"

typedef struct hbfsim_package_thermal_plugin_metadata_v1 {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t state_count;
    uint32_t input_count;
    uint32_t output_count;
    uint64_t sample_period_ns;
    const char* model_id;
    const char* input_names_csv;
    const char* output_names_csv;
} hbfsim_package_thermal_plugin_metadata_v1;

typedef struct hbfsim_package_thermal_plugin_api_v1 {
    uint32_t abi_version;
    uint32_t struct_size;
    const hbfsim_package_thermal_plugin_metadata_v1* (*metadata)(void);
    void* (*create)(void);
    void (*destroy)(void* instance);
    int (*reset)(void* instance, double initial_temperature_c);
    int (*step)(void* instance, const double* input_power_w,
                size_t input_count, double* output_temperature_c,
                size_t output_count);
    const char* (*last_error)(void* instance);
} hbfsim_package_thermal_plugin_api_v1;

typedef const hbfsim_package_thermal_plugin_api_v1* (
    *hbfsim_package_thermal_plugin_query_v1)(void);

#ifdef __cplusplus
}
#endif

