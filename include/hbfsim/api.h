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

enum hbfsim_fidelity {
    HBFSIM_FIDELITY_EMULATION = 0,
    HBFSIM_FIDELITY_EXACT_SM120 = 1
};

typedef struct hbfsim_options {
    const char* profile_path;
    const char* report_dir;
    uint32_t mode;
    uint32_t ring_capacity;
    uint64_t request_timeout_ns;
} hbfsim_options;

typedef struct hbfsim_options_v2 {
    uint32_t struct_bytes;
    hbfsim_options base;
    uint32_t fidelity;
    const char* exact_profile_path;
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

typedef struct hbfsim_future_stats {
    uint64_t issued;
    uint64_t issue_throttle_ns;
    uint64_t dependency_wait_ns;
    uint64_t ordering_wait_ns;
    uint64_t drained;
    uint64_t leaked;
    uint64_t faults;
} hbfsim_future_stats;

typedef struct hbfsim_tma_stats {
    uint64_t issued;
    uint64_t hbm_bytes;
    uint64_t hbf_bytes;
    uint64_t oob_bytes;
    uint64_t fanout_targets;
    uint64_t barrier_wait_ns;
    uint64_t group_wait_ns;
    uint64_t stale_generations;
    uint64_t faults;
    uint64_t leaked;
} hbfsim_tma_stats;

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
int hbfsim_get_future_stats(hbfsim_context* context,
                            hbfsim_future_stats* out);
int hbfsim_get_tma_stats(hbfsim_context* context, hbfsim_tma_stats* out);
int hbfsim_unregister(hbfsim_context* context, void* range_base);
void hbfsim_context_destroy(hbfsim_context* context);

#ifdef __cplusplus
}
#endif
