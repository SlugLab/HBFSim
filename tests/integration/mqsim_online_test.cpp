#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include "../../src/host_service/control_layout.hpp"
#include "../../src/host_service/request_dispatcher.hpp"

#include <cassert>
#include <cstdint>
#include <cstdlib>
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

        const auto current_time = engine.current_time_ns();
        assert(current_time != 0);
        bool past_arrival_rejected = false;
        try {
            engine.submit(
                read_request(6, 6, current_time - 1, 0xC000, 16'384));
        } catch (const std::invalid_argument&) {
            past_arrival_rejected = true;
        }
        assert(past_arrival_rejected);

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

    {
        constexpr std::uint32_t capacity = 4;
        const auto control_bytes =
            hbfsim::host_service::control_region_bytes(capacity);
        void* storage = nullptr;
        if (::posix_memalign(&storage, 64, control_bytes) != 0) {
            return __LINE__;
        }
        hbfsim::host_service::ControlView control(storage, control_bytes);
        if (!control.initialize(capacity)) {
            return __LINE__;
        }

        auto dirty = read_request(201, 0, 0, 0x4000, profile.page_bytes);
        auto unrelated =
            read_request(202, 0, 0, 0x10000, profile.page_bytes);
        std::uint64_t dirty_ticket = 0;
        std::uint64_t unrelated_ticket = 0;
        if (!control.try_push_request(dirty, dirty_ticket) ||
            !control.try_push_request(unrelated, unrelated_ticket)) {
            return __LINE__;
        }

        auto coordinated_profile = profile;
        coordinated_profile.capacity_bytes = 64ULL * 1024 * 1024 * 1024;
        hbfsim::validate_profile(coordinated_profile);
        hbfsim::MqsimOnlineEngine engine(coordinated_profile);
        std::vector<hbfsim::HbfRequest> submitted;
        hbfsim::host_service::RequestDispatcher dispatcher(
            control,
            hbfsim::host_service::RequestDispatcher::Engine{
                .prepare = [&](const hbfsim::HbfRequest& value) {
                    auto completion = hbfsim::HbfCompletion{
                        .request_id = value.request_id,
                        .cache_frame_address =
                            value.logical_address == dirty.logical_address
                                ? 0x1000ULL
                                : 0x2000ULL,
                        .page_generation = value.page_generation,
                        .status = static_cast<std::uint32_t>(
                            hbfsim::RequestStatus::Ready),
                    };
                    auto read = value;
                    read.operation = static_cast<std::uint32_t>(
                        hbfsim::RequestOperation::Read);
                    if (value.logical_address == dirty.logical_address) {
                        auto program = value;
                        program.logical_address = 0x20000;
                        program.operation = static_cast<std::uint32_t>(
                            hbfsim::RequestOperation::Write);
                        return hbfsim::host_service::PreparedDispatch{
                            .completion = completion,
                            .media_actions = {program, read},
                            .media_action_count = 2,
                        };
                    }
                    return hbfsim::host_service::PreparedDispatch{
                        .completion = completion,
                        .media_actions = {read},
                        .media_action_count = 1,
                    };
                },
                .submit = [&](const hbfsim::HbfRequest& action) {
                    submitted.push_back(action);
                    engine.submit(action);
                },
                .run_next_completion = [&] {
                    return engine.run_next_completion();
                },
            });
        if (!dispatcher.poll_once() || submitted.size() != 3 ||
            submitted[0].operation != static_cast<std::uint32_t>(
                                          hbfsim::RequestOperation::Write) ||
            submitted[1].logical_address != unrelated.logical_address ||
            submitted[2].logical_address != dirty.logical_address ||
            submitted[2].operation != static_cast<std::uint32_t>(
                                          hbfsim::RequestOperation::Read)) {
            return __LINE__;
        }

        hbfsim::HbfCompletion dirty_completion{};
        hbfsim::HbfCompletion unrelated_completion{};
        if (!control.try_consume_completion(dirty_ticket,
                                            dirty_completion)) {
            return __LINE__;
        }
        if (!control.try_consume_completion(unrelated_ticket,
                                            unrelated_completion)) {
            return __LINE__;
        }
        if (dirty_completion.status != static_cast<std::uint32_t>(
                                           hbfsim::RequestStatus::Ready)) {
            return __LINE__;
        }
        if (dirty_completion.modeled_ns <
            coordinated_profile.program_latency_ns +
                coordinated_profile.read_latency_ns) {
            return __LINE__;
        }
        if (unrelated_completion.status != static_cast<std::uint32_t>(
                                               hbfsim::RequestStatus::Ready)) {
            return __LINE__;
        }
        std::free(storage);
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
