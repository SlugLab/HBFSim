#include <cstdlib>
#include <fcntl.h>
#include <unistd.h>

__attribute__((constructor)) static void record_preload()
{
    const char* marker = std::getenv("HBFSIM_PRELOAD_MARKER_PATH");
    if (marker == nullptr || marker[0] == '\0') {
        return;
    }
    const auto descriptor =
        ::open(marker, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (descriptor >= 0) {
        constexpr char value[] = "inherited\n";
        const auto write_result =
            ::write(descriptor, value, sizeof(value) - 1);
        (void)write_result;
        (void)::close(descriptor);
    }
}
