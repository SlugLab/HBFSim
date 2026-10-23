#pragma once
// Private to nv_attach_impl_frida_setup.cpp, after launch_frontend_observer.hpp.
// The C2 observer state lives in that translation unit's anonymous namespace.
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <utility>

namespace { namespace hbfsim_binding_auth_span {
template <class F> int invoke(unsigned route, F&& call) {
    const int incoming_errno = errno;
    const char* flag = ::getenv("HBFSIM_NATIVE_BINDING_AUTH_SPANS_V1");
    if (flag == nullptr || std::strcmp(flag, "1") != 0) {
        errno = incoming_errno;
        return std::forward<F>(call)();
    }
    hbfsim_frontend_c2::entry span(route);
    span.mark(hbfsim_frontend_c2::metadata_begin);
    errno = incoming_errno;
    try {
        const int result = std::forward<F>(call)();
        const int callback_errno = errno;
        span.mark(hbfsim_frontend_c2::metadata_end);
        span.ret(result);
        errno = callback_errno;
        return result;
    } catch (...) {
        const int callback_errno = errno;
        span.mark(hbfsim_frontend_c2::metadata_end);
        errno = callback_errno;
        throw;
    }
}
} }
