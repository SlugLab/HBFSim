#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include "../../src/cuda_runtime/device/hbf_device.cuh"

#include <cassert>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace {

hbfsim::HbfRequest read_request(std::uint64_t id,
                                std::uint64_t sequence,
                                std::uint64_t arrival_ns,
                                std::uint64_t address,
                                std::uint32_t bytes)
{
    return hbfsim::HbfRequest{
        .request_id = id,
        .sequence = sequence,
        .arrival_ns = arrival_ns,
        .logical_address = address,
        .deadline_ns = 0,
        .bytes = bytes,
        .range_id = 1,
        .stream_id = 0,
        .operation =
            static_cast<std::uint32_t>(hbfsim::RequestOperation::Read),
        .page_generation = 1,
        .flags = 0,
    };
}

}  // namespace

int main()
{
    auto profile = hbfsim::load_profile("configs/profiles/nominal.json");
    profile.capacity_bytes = 4ULL * 1024 * 1024 * 1024;
    profile.hbm_cache_bytes = 64ULL * 1024 * 1024;
    hbfsim::validate_profile(profile);

    bool geometry_rejected = false;
    try {
        hbfsim::MqsimOnlineEngine too_small(profile);
    } catch (const std::invalid_argument&) {
        geometry_rejected = true;
    }
    assert(geometry_rejected);

    profile.capacity_bytes = 16ULL * 1024 * 1024 * 1024;
    hbfsim::validate_profile(profile);

    const hbfsim::device::SharedRangeRecord gpu_range{
        .base = 0x7f00'0000'0000ULL,
        .length = 3 * 16'384,
        .file_offset = 2 * 16'384,
        .range_id = 9,
        .mode = 1,
        .permissions = 3,
        .stream_id = 4,
        .page_bytes = 16'384,
    };
    const auto media = hbfsim::device::media_descriptor(
        gpu_range, gpu_range.base + 16'385, 8, 0);
    if (!media.valid || media.logical_address != 3 * 16'384 ||
        media.bytes != 16'384) {
        return __LINE__;
    }
    {
        hbfsim::MqsimOnlineEngine engine(profile);
        engine.submit(read_request(100, 0, 0, media.logical_address,
                                   media.bytes));
        const auto completion = engine.run_next_completion();
        if (!completion.has_value() ||
            completion->status !=
                static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)) {
            return __LINE__;
        }
    }

    std::vector<hbfsim::HbfCompletion> direct_reference;
    {
        hbfsim::MqsimOnlineEngine engine(profile);
        engine.submit(read_request(2, 2, 100, 0x4000, 16384));
        engine.submit(read_request(1, 1, 0, 0x0000, 16384));
        assert(engine.pending() == 2);

        const auto first = engine.run_next_completion();
        const auto second = engine.run_next_completion();
        assert(first.has_value());
        assert(second.has_value());
        assert(first->request_id == 1);
        assert(second->modeled_completion_ns >= first->modeled_completion_ns);
        assert(first->modeled_ns >= profile.read_latency_ns);
        assert(engine.pending() == 0);
        assert(!engine.run_next_completion().has_value());
        direct_reference = {*first, *second};

        const auto next_arrival = second->modeled_completion_ns + 100;
        engine.submit(read_request(4, 4, next_arrival, 0x8000, 16384));
        engine.submit(read_request(3, 3, next_arrival, 0x8000, 16384));
        const auto third = engine.run_next_completion();
        const auto fourth = engine.run_next_completion();
        assert(third.has_value());
        assert(fourth.has_value());
        assert(third->request_id == 3);
        assert(fourth->request_id == 4);

        bool rejected = false;
        try {
            engine.submit(read_request(5, 5, next_arrival + 100, 1, 511));
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        assert(rejected);
    }

    const auto trace_reference = hbfsim::run_mqsim_trace(
        profile, "tests/fixtures/mqsim/read_only.trace");
    assert(trace_reference.size() == direct_reference.size());
    for (std::size_t index = 0; index < direct_reference.size(); ++index) {
        assert(trace_reference[index].request_id ==
               direct_reference[index].request_id);
        assert(trace_reference[index].modeled_completion_ns ==
               direct_reference[index].modeled_completion_ns);
        assert(trace_reference[index].modeled_ns ==
               direct_reference[index].modeled_ns);
    }

    const auto burst_path =
        std::filesystem::temp_directory_path() / "hbfsim-mqsim-burst.trace";
    {
        std::ofstream burst(burst_path);
        assert(burst);
        for (std::uint64_t index = 0; index < 4096; ++index) {
            burst << "0 0 " << index * 32 << " 32 1\n";
        }
    }
    const auto burst_reference =
        hbfsim::run_mqsim_trace(profile, burst_path);
    std::filesystem::remove(burst_path);
    assert(burst_reference.size() == 4096);

    return 0;
}
