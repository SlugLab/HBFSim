#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct hbfsim_vllm_session hbfsim_vllm_session;

typedef struct hbfsim_vllm_options {
    const char* profile_path;
    const char* report_dir;
    uint32_t ring_capacity;
    uint32_t timing_model;
    uint64_t request_timeout_ns;
} hbfsim_vllm_options;

#define HBFSIM_VLLM_OPTIONS_V2_VERSION 1u

typedef struct hbfsim_vllm_options_v2 {
    uint32_t struct_size;
    uint32_t version;
    hbfsim_vllm_options base;
    uint32_t package_thermal_stage;
    const char* package_thermal_profile_path;
    const char* package_thermal_model_path;
} hbfsim_vllm_options_v2;

uint32_t hbfsim_vllm_abi_version(void);
int hbfsim_vllm_session_create(const hbfsim_vllm_options* options,
                               hbfsim_vllm_session** out);
int hbfsim_vllm_session_create_v2(const hbfsim_vllm_options_v2* options,
                                  hbfsim_vllm_session** out);
int hbfsim_vllm_register_storage(hbfsim_vllm_session* session,
                                 uintptr_t device_address, size_t bytes);
int hbfsim_vllm_session_close(hbfsim_vllm_session** session);
const char* hbfsim_vllm_status_string(int status);

#ifdef __cplusplus
}
#endif
