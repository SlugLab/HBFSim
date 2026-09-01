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

typedef struct hbfsim_stats {
    uint64_t requests_submitted;
    uint64_t requests_completed;
    uint64_t fast_requests;
    uint64_t reference_requests;
    uint64_t fast_modeled_ns;
} hbfsim_stats;

#define HBFSIM_STATS_V2_SCHEMA_VERSION 2u
#define HBFSIM_STATS_V2_VALID_MODELED_DEVICE_TIME (UINT64_C(1) << 0)
#define HBFSIM_STATS_V2_VALID_HOST_SERVICE_TIME (UINT64_C(1) << 1)
#define HBFSIM_STATS_V2_VALID_BACKING_IO_TIME (UINT64_C(1) << 2)
#define HBFSIM_STATS_V2_VALID_H2D_COPY_TIME (UINT64_C(1) << 3)
#define HBFSIM_STATS_V2_VALID_DTOH_COPY_TIME (UINT64_C(1) << 4)
#define HBFSIM_STATS_V2_VALID_CAPACITY (UINT64_C(1) << 5)
#define HBFSIM_STATS_V2_VALID_BYTE_HIT_RATIO (UINT64_C(1) << 6)
#define HBFSIM_STATS_V2_VALID_PAGE_HIT_RATIO (UINT64_C(1) << 7)
#define HBFSIM_STATS_V2_VALID_PAGE_RESIDENCE_TIME (UINT64_C(1) << 8)
#define HBFSIM_STATS_V2_VALID_EMULATOR_DISPATCHER_TIME (UINT64_C(1) << 9)
#define HBFSIM_STATS_V2_VALID_APPLICATION_WALL_TIME (UINT64_C(1) << 10)
#define HBFSIM_STATS_V2_VALID_SCHEDULING (UINT64_C(1) << 11)

// Versioned, additive observability for the synchronous capacity baseline.
// Consumers must test valid_fields before serializing optional values. In
// particular, a zero denominator leaves the corresponding ratio invalid so a
// JSON writer can emit null instead of a fabricated zero or infinity.
typedef struct hbfsim_stats_v2 {
    uint32_t schema_version;
    uint32_t reserved0;
    uint64_t valid_fields;
    uint64_t requests_total;
    uint64_t demand_requests;
    uint64_t speculative_requests;
    uint64_t modeled_device_time_ns;
    uint64_t host_service_time_ns;
    uint64_t backing_io_wall_time_ns;
    uint64_t h2d_copy_time_ns;
    uint64_t dtoh_copy_time_ns;
    uint64_t emulator_dispatcher_wall_time_ns;
    uint64_t application_wall_time_ns;
    uint64_t configured_hbm_cache_bytes;
    uint64_t actual_page_aligned_hbm_cache_bytes;
    uint64_t hbf_logical_bytes;
    uint64_t hbf_actually_accessed_bytes;
    uint64_t resident_bytes_current;
    uint64_t resident_bytes_peak;
    uint64_t free_frames;
    uint64_t frame_count;
    uint64_t hits;
    uint64_t misses;
    double byte_hit_ratio;
    double page_hit_ratio;
    uint64_t clean_evictions;
    uint64_t dirty_evictions;
    uint64_t writeback_bytes;
    uint64_t hbf_read_bytes;
    uint64_t hbf_program_bytes;
    uint64_t duplicate_misses;
    uint64_t coalesced_misses;
    uint64_t in_flight_pages;
    uint64_t page_residence_time_ns;
    uint64_t completed_residences;
    uint64_t engine_outstanding_requests;
    uint64_t demand_waiting_time_ns;
    uint64_t speculative_waiting_time_ns;
    uint64_t demand_exposed_stall_ns;
    uint64_t hidden_prefetched_stall_ns;
    uint64_t late_prefetch_stall_ns;
} hbfsim_stats_v2;

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
int hbfsim_get_stats(hbfsim_context* context, hbfsim_stats* out);
int hbfsim_get_stats_v2(hbfsim_context* context, hbfsim_stats_v2* out,
                        size_t out_size);
int hbfsim_unregister(hbfsim_context* context, void* range_base);
void hbfsim_context_destroy(hbfsim_context* context);

#ifdef __cplusplus
}
#endif
