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

typedef struct hbfsim_options {
    const char* profile_path;
    const char* report_dir;
    uint32_t mode;
    uint32_t ring_capacity;
    uint64_t request_timeout_ns;
} hbfsim_options;

typedef struct hbfsim_range_options {
    uint32_t mode;
    uint32_t permissions;
    uint32_t cache_policy;
    uint32_t stream_id;
} hbfsim_range_options;

uint32_t hbfsim_abi_version(void);
int hbfsim_context_create(const hbfsim_options* options,
                          hbfsim_context** out);
int hbfsim_register_device(hbfsim_context* context, void* device_ptr,
                           size_t length,
                           const hbfsim_range_options* options);
int hbfsim_map_file(hbfsim_context* context, const char* path,
                    uint64_t file_offset, size_t length,
                    const hbfsim_range_options* options,
                    void** logical_device_ptr_out);
int hbfsim_flush(hbfsim_context* context);
int hbfsim_unregister(hbfsim_context* context, void* range_base);
void hbfsim_context_destroy(hbfsim_context* context);

#ifdef __cplusplus
}
#endif
