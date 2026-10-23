// Fixed CPU-only fixture: includes the actual private adapter and unchanged C2 header.
#include "launch_frontend_observer.hpp"
#include "binding_auth_span.hpp"
#include <cassert>
#include <cerrno>
#include <cstring>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <unistd.h>

static std::string read_text(const std::string& path) {
    std::ifstream f(path);
    return std::string(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}
static size_t occurrences(const std::string& s, const std::string& token) {
    size_t n = 0, at = 0;
    while ((at = s.find(token, at)) != std::string::npos) { ++n; at += token.size(); }
    return n;
}
int main() {
    char dir[] = "/tmp/hbf-binding-auth-span-XXXXXX";
    assert(::mkdtemp(dir));
    const std::string raw = std::string(dir) + "/rows.jsonl";
    const std::string health = std::string(dir) + "/health.json";
    const std::string pending = health + ".pending";
    hbfsim_frontend_c2::state s;
    s.raw = ::open(raw.c_str(), O_CREAT | O_EXCL | O_WRONLY, 0600);
    s.health = ::open(pending.c_str(), O_CREAT | O_EXCL | O_WRONLY, 0600);
    assert(s.raw >= 0 && s.health >= 0);
    std::snprintf(s.health_final, sizeof s.health_final, "%s", health.c_str());
    std::snprintf(s.health_pending, sizeof s.health_pending, "%s", pending.c_str());
    s.p = hbfsim_frontend_c2::phase::open;
    s.activation = 1;
    s.pid = ::getpid(); s.start = 1;
    std::snprintf(s.boot, sizeof s.boot, "00000000-0000-0000-0000-000000000000");
    hbfsim_frontend_c2::current.store(&s);

    ::unsetenv("HBFSIM_NATIVE_BINDING_AUTH_SPANS_V1");
    int calls = 0; errno = 17;
    assert(hbfsim_binding_auth_span::invoke(403, [&] { ++calls; assert(errno == 17); errno = 29; return 1; }) == 1);
    assert(calls == 1 && errno == 29 && s.rows == 0);
    ::setenv("HBFSIM_NATIVE_BINDING_AUTH_SPANS_V1", "0", 1);
    errno = 31;
    assert(hbfsim_binding_auth_span::invoke(402, [&] { ++calls; assert(errno == 31); errno = 37; return -4; }) == -4);
    assert(calls == 2 && errno == 37 && s.rows == 0);
    ::setenv("HBFSIM_NATIVE_BINDING_AUTH_SPANS_V1", "1", 1);
    {
        hbfsim_frontend_c2::entry parent(301);
        errno = 41;
        assert(hbfsim_binding_auth_span::invoke(402, [&] { ++calls; assert(errno == 41); errno = 43; return 0; }) == 0);
        assert(errno == 43);
        errno = 47;
        assert(hbfsim_binding_auth_span::invoke(403, [&] { ++calls; assert(errno == 47); errno = 53; return -7; }) == -7);
        assert(errno == 53);
        errno = 59;
        try {
            hbfsim_binding_auth_span::invoke(402, [&]() -> int { ++calls; assert(errno == 59); errno = 61; throw std::runtime_error("fixture"); });
            assert(false);
        } catch (const std::runtime_error&) { assert(errno == 61); }
        assert(hbfsim_frontend_c2::parent_id == parent.id);
    }
    assert(calls == 5 && s.rows == 4 && s.active == 0 && hbfsim_frontend_c2::parent_id == 0);
    hbfsim_frontend_c2::seal_callback();
    const std::string rows = read_text(raw), h = read_text(health);
    assert(occurrences(rows, "\"route\":402") == 2);
    assert(occurrences(rows, "\"route\":403") == 1);
    assert(occurrences(rows, "\"parent\":1") == 3);
    assert(occurrences(rows, "\"result\":-7") == 1);
    assert(occurrences(rows, "\"exception\":true") == 1);
    assert(h.find("SEALED_REQUIRES_AUTHENTICATED_TARGET_CONTAINMENT") != std::string::npos);
    assert(h.find("\"rows\":4") != std::string::npos);
    hbfsim_frontend_c2::current.store(nullptr);
    ::unlink(raw.c_str()); ::unlink(health.c_str()); ::rmdir(dir);
}
