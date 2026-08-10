#include "hbfsim/coverage.hpp"

#include <json.hpp>

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <unistd.h>

namespace hbfsim {
namespace {

nlohmann::json to_json(const GateDecision& decision)
{
    return {
        {"allowed", decision.allowed},
        {"module_id", decision.module_id},
        {"kernel", decision.kernel},
        {"ptx_target", decision.ptx_target},
        {"cubin_only", decision.cubin_only},
        {"reason", decision.reason},
        {"operation", decision.operation},
        {"inspected_parameters", decision.inspected_parameters},
        {"parameter_index", decision.parameter_index},
        {"parameter_offset", decision.parameter_offset},
        {"address", decision.address},
    };
}

}  // namespace

CoverageWriter::CoverageWriter(std::filesystem::path path)
    : path_(std::move(path))
{
}

void CoverageWriter::append(const GateDecision& decision)
{
    std::scoped_lock lock(mutex_);
    const std::string line = to_json(decision).dump() + '\n';
    const int fd =
        ::open(path_.c_str(), O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (fd < 0) {
        throw std::runtime_error("unable to open coverage report: " +
                                 path_.string());
    }
    std::size_t offset = 0;
    while (offset < line.size()) {
        const auto count =
            ::write(fd, line.data() + offset, line.size() - offset);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            const int error = errno;
            ::close(fd);
            throw std::runtime_error("unable to append coverage report: " +
                                     std::string(std::strerror(error)));
        }
        offset += static_cast<std::size_t>(count);
    }
    if (::fdatasync(fd) != 0) {
        const int error = errno;
        ::close(fd);
        throw std::runtime_error("unable to sync coverage report: " +
                                 std::string(std::strerror(error)));
    }
    if (::close(fd) != 0) {
        throw std::runtime_error("unable to close coverage report");
    }
}

bool try_append_coverage(CoverageWriter& writer,
                         const GateDecision& decision) noexcept
{
    try {
        writer.append(decision);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool coverage_decision_permits_launch(CoverageWriter& writer,
                                      const GateDecision& decision) noexcept
{
    // Auditability is part of the launch policy: a launch is approved only
    // when its decision is safe and durably reportable.
    return try_append_coverage(writer, decision) && decision.allowed;
}

}  // namespace hbfsim
