#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <Engine.h>
#include <Flash_Chip.h>
#include <Flash_Command.h>
#include <Media_Activity_Observer.h>

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <memory>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace {

struct RunResult {
    std::vector<hbfsim::HbfCompletion> completions;
    std::vector<hbfsim::MediaActivity> activities;
    std::uint64_t final_time_ns{};
};

hbfsim::HbfRequest request(
    std::uint64_t index,
    hbfsim::RequestOperation operation = hbfsim::RequestOperation::Read)
{
    return hbfsim::HbfRequest{
        .request_id = index + 1,
        .sequence = index + 1,
        .arrival_ns = (index % 5) * 10,
        .logical_address = index * 16'384,
        .deadline_ns = 0,
        .bytes = 16'384,
        .range_id = 1,
        .stream_id = 0,
        .operation = static_cast<std::uint32_t>(operation),
        .page_generation = 1,
        .flags = 0,
    };
}

bool same_completion(const hbfsim::HbfCompletion& left,
                     const hbfsim::HbfCompletion& right)
{
    return left.request_id == right.request_id &&
           left.modeled_completion_ns == right.modeled_completion_ns &&
           left.modeled_ns == right.modeled_ns &&
           left.service_ns == right.service_ns &&
           left.cache_frame_address == right.cache_frame_address &&
           left.page_generation == right.page_generation &&
           left.status == right.status && left.checksum == right.checksum &&
           left.reserved == right.reserved;
}

RunResult run_workload(const hbfsim::Profile& profile,
                       int observer_mode,
                       hbfsim::RequestOperation operation,
                       std::uint64_t request_count,
                       bool random_pattern = false,
                       bool mixed_operations = false,
                       bool zero_arrival_gap = false)
{
    RunResult result;
    std::unique_ptr<hbfsim::MqsimOnlineEngine> engine;
    if (observer_mode == 0) {
        engine = std::make_unique<hbfsim::MqsimOnlineEngine>(profile);
    } else if (observer_mode == 1) {
        engine = std::make_unique<hbfsim::MqsimOnlineEngine>(
            profile, [](const hbfsim::MediaActivity&) {});
    } else {
        engine = std::make_unique<hbfsim::MqsimOnlineEngine>(
            profile, [&](const hbfsim::MediaActivity& activity) {
                result.activities.push_back(activity);
            });
    }

    for (std::uint64_t index = 0; index < request_count; ++index) {
        const auto selected_operation =
            mixed_operations && (index % 2 != 0)
                ? hbfsim::RequestOperation::Write
                : operation;
        auto item = request(index, selected_operation);
        if (random_pattern) {
            item.logical_address =
                ((index * 17) % request_count) * 16'384;
        }
        if (zero_arrival_gap) item.arrival_ns = 0;
        engine->submit(item);
    }
    while (engine->pending() != 0) {
        auto completion = engine->run_next_completion();
        if (!completion.has_value()) {
            throw std::runtime_error(
                operation == hbfsim::RequestOperation::Read
                    ? "read workload ended with pending requests"
                    : "program workload ended with pending requests");
        }
        result.completions.push_back(*completion);
    }
    result.final_time_ns = engine->current_time_ns();
    return result;
}

void assert_invariant(const RunResult& without_observer,
                      const RunResult& no_op_observer,
                      const RunResult& recording_observer)
{
    assert(without_observer.final_time_ns == no_op_observer.final_time_ns);
    assert(without_observer.final_time_ns == recording_observer.final_time_ns);
    assert(without_observer.completions.size() ==
           no_op_observer.completions.size());
    assert(without_observer.completions.size() ==
           recording_observer.completions.size());
    for (std::size_t index = 0; index < without_observer.completions.size();
         ++index) {
        assert(same_completion(without_observer.completions[index],
                               no_op_observer.completions[index]));
        assert(same_completion(without_observer.completions[index],
                               recording_observer.completions[index]));
    }
}

struct InternalCapture {
    std::vector<NVM::FlashMemory::Media_Activity> activities;
};

void capture_internal(
    const NVM::FlashMemory::Media_Activity& activity,
    void* context) noexcept
{
    try {
        static_cast<InternalCapture*>(context)->activities.push_back(activity);
    } catch (...) {
        std::terminate();
    }
}

void discard_internal(
    const NVM::FlashMemory::Media_Activity&, void*) noexcept
{
}

std::pair<std::uint64_t, InternalCapture> run_internal_program(int mode)
{
    using namespace NVM::FlashMemory;
    Simulator->Reset();
    sim_time_type read_latency[] = {100};
    sim_time_type program_latency[] = {200};
    auto chip = std::make_unique<Flash_Chip>(
        "observer-program-chip", 1, 0, Flash_Technology_Type::SLC,
        1, 1, 8, 16, read_latency, program_latency, 300, 0, 0);
    Simulator->AddObject(chip.get());

    InternalCapture capture;
    if (mode == 1) {
        chip->Connect_to_media_activity_observer(discard_internal, nullptr);
    } else if (mode == 2) {
        chip->Connect_to_media_activity_observer(capture_internal, &capture);
    }

    Flash_Command command;
    command.CommandCode = CMD_PROGRAM_PAGE;
    command.Address.emplace_back(1, 0, 0, 0, 2, 3);
    command.Observer_source = {Media_Transaction_Source::USER_IO};
    command.Observer_bytes = {16'384};
    PageMetadata metadata;
    metadata.LPA = 7;
    command.Meta_data.push_back(metadata);

    chip->StartCMDDataInXfer();
    chip->EndCMDDataInXfer(&command);
    while (Simulator->Run_next_event()) {
    }
    const auto final_time = static_cast<std::uint64_t>(Simulator->Time());
    Simulator->Reset();
    return {final_time, std::move(capture)};
}

void test_internal_multiplane_erase()
{
    using namespace NVM::FlashMemory;
    Simulator->Reset();
    sim_time_type read_latency[] = {100};
    sim_time_type program_latency[] = {200};
    auto chip = std::make_unique<Flash_Chip>(
        "observer-erase-chip", 3, 4, Flash_Technology_Type::SLC,
        2, 2, 8, 16, read_latency, program_latency, 300, 0, 0);
    Simulator->AddObject(chip.get());

    InternalCapture capture;
    chip->Connect_to_media_activity_observer(capture_internal, &capture);
    Flash_Command command;
    command.CommandCode = CMD_ERASE_BLOCK_MULTIPLANE;
    command.Address.emplace_back(3, 4, 1, 0, 6, 0);
    command.Address.emplace_back(3, 4, 1, 1, 6, 0);
    command.Observer_source = {
        Media_Transaction_Source::GC_WL,
        Media_Transaction_Source::GC_WL,
    };
    command.Observer_bytes = {16'384, 16'384};

    chip->StartCMDXfer();
    chip->EndCMDXfer(&command);
    assert(capture.activities.size() == 2);
    for (std::size_t index = 0; index < capture.activities.size(); ++index) {
        const auto& activity = capture.activities[index];
        assert(activity.Kind == Media_Activity_Kind::ERASE);
        assert(activity.Start_time_ns == 0);
        assert(activity.End_time_ns == 300);
        assert(activity.Channel == 3 && activity.Chip == 4);
        assert(activity.Die == 1 && activity.Plane == index);
        assert(activity.Block == 6 && activity.Page == 0);
        assert(activity.Bytes == 0);
        assert(activity.Source == Media_Transaction_Source::GC_WL);
    }

    Simulator->Reset();
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc > 2) {
        throw std::invalid_argument("expected at most one observer case");
    }
    const std::string_view case_name =
        argc == 2 ? std::string_view(argv[1]) : "sequential-read";
    struct WorkloadSpec {
        hbfsim::RequestOperation operation;
        bool random_pattern;
        bool mixed_operations;
        bool zero_arrival_gap;
    };
    WorkloadSpec spec{};
    if (case_name == "sequential-read") {
        spec = {hbfsim::RequestOperation::Read, false, false, false};
    } else if (case_name == "random-read") {
        spec = {hbfsim::RequestOperation::Read, true, false, false};
    } else if (case_name == "sequential-write") {
        spec = {hbfsim::RequestOperation::Write, false, false, false};
    } else if (case_name == "random-mixed") {
        spec = {hbfsim::RequestOperation::Read, true, true, false};
    } else if (case_name == "saturated-mixed") {
        spec = {hbfsim::RequestOperation::Read, true, true, true};
    } else {
        throw std::invalid_argument("unknown observer case");
    }

    auto profile = hbfsim::load_profile("configs/profiles/nominal.json");
    profile.capacity_bytes = 64ULL * 1024 * 1024 * 1024;
    profile.hbm_cache_bytes = 64ULL * 1024 * 1024;
    hbfsim::validate_profile(profile);

    const auto verify_workload = [&](hbfsim::RequestOperation operation,
                                     std::uint64_t request_count,
                                     bool random_pattern,
                                     bool mixed_operations,
                                     bool zero_arrival_gap) {
        const auto without_observer = run_workload(
            profile, 0, operation, request_count, random_pattern,
            mixed_operations, zero_arrival_gap);
        const auto no_op_observer = run_workload(
            profile, 1, operation, request_count, random_pattern,
            mixed_operations, zero_arrival_gap);
        const auto recording = run_workload(
            profile, 2, operation, request_count, random_pattern,
            mixed_operations, zero_arrival_gap);
        assert_invariant(without_observer, no_op_observer, recording);
        return recording;
    };
    const auto recording_observer = verify_workload(
        spec.operation, 64, spec.random_pattern, spec.mixed_operations,
        spec.zero_arrival_gap);

    assert(!recording_observer.activities.empty());
    bool saw_read = false;
    bool saw_program = false;
    std::set<std::uint32_t> channels;
    std::set<std::pair<std::uint32_t, std::uint32_t>> channel_dies;
    std::set<std::uint32_t> planes;
    for (const auto& activity : recording_observer.activities) {
        assert(activity.end_time_ns > activity.start_time_ns);
        assert(activity.chip == 0);
        if (activity.source == hbfsim::MediaTransactionSource::UserIo) {
            assert(activity.bytes == 16'384);
            saw_read = saw_read ||
                       activity.kind == hbfsim::MediaActivityKind::Read;
            saw_program = saw_program ||
                          activity.kind == hbfsim::MediaActivityKind::Program;
        }
        channels.insert(activity.channel);
        channel_dies.emplace(activity.channel, activity.die);
        planes.insert(activity.plane);
    }
    if (spec.mixed_operations) {
        assert(saw_read && saw_program);
    } else if (spec.operation == hbfsim::RequestOperation::Write) {
        assert(saw_program);
    } else {
        assert(saw_read);
    }
    assert(channels.size() > 1);
    assert(channel_dies.size() > channels.size());
    assert(!planes.empty());

    const auto program_without_observer = run_internal_program(0);
    const auto program_no_op_observer = run_internal_program(1);
    const auto program_recording_observer = run_internal_program(2);
    assert(program_without_observer.first == 200);
    assert(program_no_op_observer.first == program_without_observer.first);
    assert(program_recording_observer.first == program_without_observer.first);
    assert(program_recording_observer.second.activities.size() == 1);
    const auto& program_activity =
        program_recording_observer.second.activities.front();
    assert(program_activity.Kind ==
           NVM::FlashMemory::Media_Activity_Kind::PROGRAM);
    assert(program_activity.Start_time_ns == 0);
    assert(program_activity.End_time_ns == 200);
    assert(program_activity.Bytes == 16'384);
    assert(program_activity.Source ==
           NVM::FlashMemory::Media_Transaction_Source::USER_IO);

    bool observer_failure_reported = false;
    try {
        hbfsim::MqsimOnlineEngine failing_engine(
            profile, [](const hbfsim::MediaActivity&) {
                throw std::runtime_error("test observer failure");
            });
        failing_engine.submit(request(0));
        (void)failing_engine.run_next_completion();
    } catch (const std::runtime_error&) {
        observer_failure_reported = true;
    }
    assert(observer_failure_reported);

    test_internal_multiplane_erase();
    return 0;
}
