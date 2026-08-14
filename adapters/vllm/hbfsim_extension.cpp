#include "hbfsim_extension.h"

#include <hbfsim/api.h>

#include <new>

struct hbfsim_vllm_session {
    hbfsim_context* context{nullptr};
    hbfsim_exact_run_contract exact_contract{};
    bool exact_requested{false};
    bool exact_contract_published{false};
    bool exact_finalized{false};
};

extern "C" uint32_t hbfsim_vllm_abi_version(void)
{
    return 3;
}

extern "C" int
hbfsim_vllm_session_create(const hbfsim_vllm_options* options,
                           hbfsim_vllm_session** out)
{
    if (out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    *out = nullptr;
    if (options == nullptr ||
        options->struct_bytes != sizeof(hbfsim_vllm_options) ||
        options->profile_path == nullptr ||
        options->profile_path[0] == '\0' || options->report_dir == nullptr ||
        options->report_dir[0] == '\0' || options->ring_capacity == 0 ||
        options->timing_model > HBFSIM_MODEL_HYBRID ||
        options->request_timeout_ns == 0) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const bool exact_requested = options->exact_profile_path != nullptr &&
                                 options->exact_profile_path[0] != '\0';
    if (exact_requested &&
        ((options->exact_cache_condition != HBFSIM_EXACT_CACHE_WARM_L2 &&
          options->exact_cache_condition != HBFSIM_EXACT_CACHE_COLD) ||
         options->exact_concurrency_condition !=
             HBFSIM_EXACT_CONCURRENCY_EXCLUSIVE_PROCESS ||
         options->exact_cluster_x == 0 || options->exact_cluster_y == 0 ||
         options->exact_cluster_z == 0)) {
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
    int status = HBFSIM_OK;
    if (exact_requested) {
        const hbfsim_options_v2 exact_options{
            .struct_bytes = sizeof(hbfsim_options_v2),
            .base = context_options,
            .fidelity = HBFSIM_FIDELITY_EXACT_SM120,
            .exact_profile_path = options->exact_profile_path,
        };
        status = hbfsim_context_create_v2(&exact_options, &session->context);
        session->exact_contract = {
            .struct_bytes = sizeof(hbfsim_exact_run_contract),
            .cache_condition = options->exact_cache_condition,
            .concurrency_condition = options->exact_concurrency_condition,
            .cluster_x = options->exact_cluster_x,
            .cluster_y = options->exact_cluster_y,
            .cluster_z = options->exact_cluster_z,
        };
        session->exact_requested = true;
    } else {
        status = hbfsim_context_create(&context_options, &session->context);
    }
    if (status != HBFSIM_OK) {
        delete session;
        return status;
    }
    *out = session;
    return HBFSIM_OK;
}

extern "C" int
hbfsim_vllm_publish_exact_contract(hbfsim_vllm_session* session)
{
    if (session == nullptr || session->context == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (!session->exact_requested || session->exact_finalized) {
        return HBFSIM_UNSUPPORTED;
    }
    const auto status = hbfsim_publish_exact_run_contract(
        session->context, &session->exact_contract);
    if (status == HBFSIM_OK) session->exact_contract_published = true;
    return status;
}

extern "C" int hbfsim_vllm_finalize_exact(hbfsim_vllm_session* session)
{
    if (session == nullptr || session->context == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (!session->exact_requested || !session->exact_contract_published) {
        return HBFSIM_UNSUPPORTED;
    }
    if (session->exact_finalized) return HBFSIM_OK;
    const auto status = ::hbfsim_finalize_exact(session->context);
    if (status == HBFSIM_OK) session->exact_finalized = true;
    return status;
}

extern "C" int hbfsim_vllm_register_storage(hbfsim_vllm_session* session,
                                             uintptr_t device_address,
                                             size_t bytes)
{
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
    return hbfsim_register_device(
        session->context, reinterpret_cast<void*>(device_address), bytes,
        &range_options);
}

extern "C" int hbfsim_vllm_session_close(hbfsim_vllm_session** session)
{
    if (session == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (*session == nullptr) {
        return HBFSIM_OK;
    }
    int status = HBFSIM_OK;
    if ((*session)->exact_requested && !(*session)->exact_finalized) {
        status = (*session)->exact_contract_published
                     ? hbfsim_vllm_finalize_exact(*session)
                     : HBFSIM_UNSUPPORTED;
    }
    hbfsim_context_destroy((*session)->context);
    (*session)->context = nullptr;
    delete *session;
    *session = nullptr;
    return status;
}

extern "C" const char* hbfsim_vllm_status_string(int status)
{
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
