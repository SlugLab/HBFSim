#include <cstdint>
#include <cstring>

extern "C" {

static int lookup(const char* symbol, void** function)
{
    *function = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x1234));
    return std::strcmp(symbol, "fail") == 0 ? 1 : 0;
}

int cuGetProcAddress(const char* symbol, void** function, int version,
                     std::uint64_t flags)
{
    (void)version;
    (void)flags;
    return lookup(symbol, function);
}

int cuGetProcAddress_v2(const char* symbol, void** function, int version,
                        std::uint64_t flags, int* status)
{
    (void)version;
    (void)flags;
    if (status != 0) {
        *status = 0;
    }
    return lookup(symbol, function);
}

int cudaGetDriverEntryPoint(const char* symbol, void** function,
                            std::uint64_t flags, int* status)
{
    (void)flags;
    if (status != 0) {
        *status = 0;
    }
    return lookup(symbol, function);
}

int cudaGetDriverEntryPoint_ptsz(const char* symbol, void** function,
                                 std::uint64_t flags, int* status)
{
    return cudaGetDriverEntryPoint(symbol, function, flags, status);
}

int cudaGetDriverEntryPointByVersion(const char* symbol, void** function,
                                     unsigned int version, std::uint64_t flags,
                                     int* status)
{
    (void)version;
    return cudaGetDriverEntryPoint(symbol, function, flags, status);
}

int cudaGetDriverEntryPointByVersion_ptsz(const char* symbol, void** function,
                                          unsigned int version,
                                          std::uint64_t flags, int* status)
{
    return cudaGetDriverEntryPointByVersion(symbol, function, version, flags,
                                            status);
}
}
