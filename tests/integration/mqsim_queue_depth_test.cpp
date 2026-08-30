// The profile field `queue_depth` reaches MQSim as
// Device_Parameter_Set::IO_Queue_Depth, which upstream MQSim reads only when
// it builds the SATA or the NVMe host interface (third_party/mqsim
// src/exec/SSD_Device.cpp lines 355 and 360). HBFSim configures
// HostInterface_Types::HBF, whose constructor takes no queue depth, so before
// the admission bound in src/mqsim_adapter/mqsim_online.cpp the field was
// parsed, validated and then read by nothing.
//
// This test pins the behaviour the bound is supposed to produce: with every
// request arriving at the same instant, a shallow queue must serialise them
// and finish later than a deep queue, and both must complete every request.

#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstdint>
#include <cstdio>
#include <vector>

namespace {

constexpr std::uint32_t kRequestBytes = 16'384;
constexpr std::uint64_t kRequestCount = 32;

hbfsim::HbfRequest read_request(std::uint64_t id, std::uint64_t address)
{
    return hbfsim::HbfRequest{
        .request_id = id,
        .sequence = id,
        .arrival_ns = 0,
        .logical_address = address,
        .deadline_ns = 0,
        .bytes = kRequestBytes,
        .range_id = 1,
        .stream_id = 0,
        .operation =
            static_cast<std::uint32_t>(hbfsim::RequestOperation::Read),
        .page_generation = 1,
        .flags = 0,
    };
}

// Returns the latest modeled completion across every request, or 0 if any
// request failed to complete.
std::uint64_t drain(const hbfsim::Profile& profile, std::uint64_t& completed)
{
    hbfsim::MqsimOnlineEngine engine(profile);
    for (std::uint64_t i = 0; i < kRequestCount; ++i) {
        engine.submit(read_request(i + 1, i * kRequestBytes));
    }

    std::uint64_t last_completion = 0;
    completed = 0;
    while (engine.pending() != 0) {
        const auto completion = engine.run_next_completion();
        if (!completion.has_value() ||
            completion->status !=
                static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)) {
            return 0;
        }
        if (completion->modeled_completion_ns > last_completion) {
            last_completion = completion->modeled_completion_ns;
        }
        ++completed;
    }
    return last_completion;
}

}  // namespace

int main()
{
    auto base = hbfsim::load_profile("configs/profiles/nominal.json");
    base.capacity_bytes = 16ULL * 1024 * 1024 * 1024;
    base.hbm_cache_bytes = 64ULL * 1024 * 1024;

    auto shallow = base;
    shallow.queue_depth = 1;
    hbfsim::validate_profile(shallow);

    auto deep = base;
    deep.queue_depth = static_cast<std::uint32_t>(kRequestCount);
    hbfsim::validate_profile(deep);

    std::uint64_t shallow_completed = 0;
    const auto shallow_last = drain(shallow, shallow_completed);
    std::uint64_t deep_completed = 0;
    const auto deep_last = drain(deep, deep_completed);

    std::printf("queue_depth=1  completed=%llu last_completion_ns=%llu\n",
                static_cast<unsigned long long>(shallow_completed),
                static_cast<unsigned long long>(shallow_last));
    std::printf("queue_depth=%llu completed=%llu last_completion_ns=%llu\n",
                static_cast<unsigned long long>(kRequestCount),
                static_cast<unsigned long long>(deep_completed),
                static_cast<unsigned long long>(deep_last));

    // Neither depth may drop or duplicate a request.
    if (shallow_completed != kRequestCount) {
        return __LINE__;
    }
    if (deep_completed != kRequestCount) {
        return __LINE__;
    }

    // The bound has to bite: one slot cannot service a simultaneous burst as
    // quickly as kRequestCount slots. Before the admission bound both runs
    // produced the same completion time, because every request was handed to
    // MQSim the moment its arrival event fired.
    if (shallow_last <= deep_last) {
        std::printf("queue_depth had no effect: %llu <= %llu\n",
                    static_cast<unsigned long long>(shallow_last),
                    static_cast<unsigned long long>(deep_last));
        return __LINE__;
    }

    // A single slot serialises, so the burst cannot finish before one media
    // read per request has been charged.
    if (shallow_last < kRequestCount * base.read_latency_ns) {
        return __LINE__;
    }

    return 0;
}
