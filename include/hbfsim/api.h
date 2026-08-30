#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct hbfsim_context hbfsim_context;

enum hbfsim_error {
    HBFSIM_OK = 0,
    HBFSIM_INVALID_ARGUMENT = 1,
    HBFSIM_IO_ERROR = 2,
    HBFSIM_CUDA_ERROR = 3,
    HBFSIM_TIMEOUT = 4,
    HBFSIM_UNSUPPORTED = 5,
    HBFSIM_DAEMON_LOST = 6
};

enum hbfsim_range_mode {
    HBFSIM_RANGE_MODE_TIMING = 1,
    HBFSIM_RANGE_MODE_CAPACITY = 2
};

enum hbfsim_range_permission {
    HBFSIM_RANGE_READ = 1u << 0,
    HBFSIM_RANGE_WRITE = 1u << 1,
    HBFSIM_RANGE_READ_WRITE = HBFSIM_RANGE_READ | HBFSIM_RANGE_WRITE
};

enum hbfsim_cache_policy {
    HBFSIM_CACHE_POLICY_NONE = 0
};

enum hbfsim_model_mode {
    HBFSIM_MODEL_REFERENCE = 0,
    HBFSIM_MODEL_FAST = 1,
    HBFSIM_MODEL_HYBRID = 2
};

enum hbfsim_package_thermal_mode {
    HBFSIM_PACKAGE_THERMAL_OFF = 0,
    HBFSIM_PACKAGE_THERMAL_PACKAGE_RC = 1
};

enum hbfsim_package_thermal_stage {
    HBFSIM_PACKAGE_THERMAL_READ_ONLY = 1,
    HBFSIM_PACKAGE_THERMAL_SHADOW = 2,
    HBFSIM_PACKAGE_THERMAL_ACTIVE = 3
};

enum hbfsim_package_thermal_model_kind {
    HBFSIM_PACKAGE_THERMAL_MODEL_ROM = 1,
    HBFSIM_PACKAGE_THERMAL_MODEL_PLUGIN = 2
};

typedef struct hbfsim_options {
    const char* profile_path;
    const char* report_dir;
    uint32_t mode;
    uint32_t ring_capacity;
    uint64_t request_timeout_ns;
} hbfsim_options;

#define HBFSIM_OPTIONS_V2_VERSION 2u

typedef struct hbfsim_options_v2 {
    uint32_t struct_size;
    uint32_t version;
    hbfsim_options base;
    uint32_t package_thermal_mode;
    uint32_t package_thermal_stage;
    uint32_t package_thermal_model_kind;
    uint32_t reserved;
    const char* package_thermal_profile_path;
    const char* package_thermal_model_path;
} hbfsim_options_v2;

typedef struct hbfsim_range_options {
    uint32_t mode;
    uint32_t permissions;
    uint32_t cache_policy;
    uint32_t stream_id;
} hbfsim_range_options;

typedef struct hbfsim_stats {
    uint64_t requests_submitted;
    uint64_t requests_completed;
    uint64_t fast_requests;
    uint64_t reference_requests;
    uint64_t fast_modeled_ns;
} hbfsim_stats;

uint32_t hbfsim_abi_version(void);
int hbfsim_context_create(const hbfsim_options* options,
                          hbfsim_context** out);
int hbfsim_context_create_v2(const hbfsim_options_v2* options,
                             hbfsim_context** out);
int hbfsim_register_device(hbfsim_context* context, void* device_ptr,
                           size_t length,
                           const hbfsim_range_options* options);
int hbfsim_map_file(hbfsim_context* context, const char* path,
                    uint64_t file_offset, size_t length,
                    const hbfsim_range_options* options,
                    void** logical_device_ptr_out);
int hbfsim_flush(hbfsim_context* context);
int hbfsim_get_stats(hbfsim_context* context, hbfsim_stats* out);
int hbfsim_unregister(hbfsim_context* context, void* range_base);
void hbfsim_context_destroy(hbfsim_context* context);

#ifdef __cplusplus
}
#endif
