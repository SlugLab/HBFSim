#include "hbfsim_extension.h"

#include <hbfsim/api.h>

#include <new>

struct hbfsim_vllm_session {
    hbfsim_context* context{nullptr};
};

extern "C" uint32_t hbfsim_vllm_abi_version(void)
{
    return 2;
}

extern "C" int hbfsim_vllm_session_create(const hbfsim_vllm_options *options,
                                          hbfsim_vllm_session **out) {
    if (out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    *out = nullptr;
    if (options == nullptr || options->profile_path == nullptr ||
        options->profile_path[0] == '\0' || options->report_dir == nullptr ||
        options->report_dir[0] == '\0' || options->ring_capacity == 0 ||
        options->timing_model > HBFSIM_MODEL_HYBRID ||
        options->request_timeout_ns == 0) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    auto* session = new (std::nothrow) hbfsim_vllm_session;
    if (session == nullptr) {
        return HBFSIM_IO_ERROR;
    }
    const hbfsim_options context_options{
        .profile_path = options->profile_path,
        .report_dir = options->report_dir,
        .mode = options->timing_model,
        .ring_capacity = options->ring_capacity,
        .request_timeout_ns = options->request_timeout_ns,
    };
    const auto status =
        hbfsim_context_create(&context_options, &session->context);
    if (status != HBFSIM_OK) {
        delete session;
        return status;
    }
    *out = session;
    return HBFSIM_OK;
}

extern "C" int
hbfsim_vllm_session_create_v2(const hbfsim_vllm_options_v2* options,
                              hbfsim_vllm_session** out)
{
    if (out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    *out = nullptr;
    if (options == nullptr ||
        options->struct_size != sizeof(hbfsim_vllm_options_v2) ||
        options->version != HBFSIM_VLLM_OPTIONS_V2_VERSION ||
        options->base.profile_path == nullptr ||
        options->base.profile_path[0] == '\0' ||
        options->base.report_dir == nullptr ||
        options->base.report_dir[0] == '\0' ||
        options->base.ring_capacity == 0 ||
        options->base.timing_model > HBFSIM_MODEL_HYBRID ||
        options->base.request_timeout_ns == 0 ||
        options->package_thermal_stage < HBFSIM_PACKAGE_THERMAL_READ_ONLY ||
        options->package_thermal_stage > HBFSIM_PACKAGE_THERMAL_ACTIVE ||
        options->package_thermal_profile_path == nullptr ||
        options->package_thermal_profile_path[0] == '\0' ||
        options->package_thermal_model_path == nullptr ||
        options->package_thermal_model_path[0] == '\0') {
        return HBFSIM_INVALID_ARGUMENT;
    }
    auto* session = new (std::nothrow) hbfsim_vllm_session;
    if (session == nullptr) {
        return HBFSIM_IO_ERROR;
    }
    const hbfsim_options context_options{
        .profile_path = options->base.profile_path,
        .report_dir = options->base.report_dir,
        .mode = options->base.timing_model,
        .ring_capacity = options->base.ring_capacity,
        .request_timeout_ns = options->base.request_timeout_ns,
    };
    const hbfsim_options_v2 context_options_v2{
        .struct_size = sizeof(hbfsim_options_v2),
        .version = HBFSIM_OPTIONS_V2_VERSION,
        .base = context_options,
        .package_thermal_mode = HBFSIM_PACKAGE_THERMAL_PACKAGE_RC,
        .package_thermal_stage = options->package_thermal_stage,
        .package_thermal_model_kind = HBFSIM_PACKAGE_THERMAL_MODEL_ROM,
        .reserved = 0,
        .package_thermal_profile_path = options->package_thermal_profile_path,
        .package_thermal_model_path = options->package_thermal_model_path,
    };
    const auto status =
        hbfsim_context_create_v2(&context_options_v2, &session->context);
    if (status != HBFSIM_OK) {
        delete session;
        return status;
    }
    *out = session;
    return HBFSIM_OK;
}

extern "C" int hbfsim_vllm_register_storage(hbfsim_vllm_session* session,
                                             uintptr_t device_address,
                                            size_t bytes) {
    if (session == nullptr || session->context == nullptr ||
        device_address == 0 || bytes == 0) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const hbfsim_range_options range_options{
        .mode = HBFSIM_RANGE_MODE_TIMING,
        .permissions = HBFSIM_RANGE_READ,
        .cache_policy = HBFSIM_CACHE_POLICY_NONE,
        .stream_id = 0,
    };
  return hbfsim_register_device(session->context,
                                reinterpret_cast<void *>(device_address), bytes,
        &range_options);
}

extern "C" const char *
hbfsim_vllm_timing_backend(const hbfsim_vllm_session *session) {
  return session == nullptr ? "unavailable"
                            : hbfsim_timing_backend(session->context);
}

extern "C" int hbfsim_vllm_session_close(hbfsim_vllm_session **session) {
    if (session == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (*session == nullptr) {
        return HBFSIM_OK;
    }
    hbfsim_context_destroy((*session)->context);
    (*session)->context = nullptr;
    delete *session;
    *session = nullptr;
    return HBFSIM_OK;
}

extern "C" const char *hbfsim_vllm_status_string(int status) {
    switch (status) {
    case HBFSIM_OK:
        return "ok";
    case HBFSIM_INVALID_ARGUMENT:
        return "invalid_argument";
    case HBFSIM_IO_ERROR:
        return "io_error";
    case HBFSIM_CUDA_ERROR:
        return "cuda_error";
    case HBFSIM_TIMEOUT:
        return "timeout";
    case HBFSIM_UNSUPPORTED:
        return "unsupported";
    case HBFSIM_DAEMON_LOST:
        return "daemon_lost";
    default:
        return "unknown";
    }
}
