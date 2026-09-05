#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <json.hpp>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

namespace {
using Json = nlohmann::json;

void publish(const std::string& path, const Json& result)
{
    const std::string pattern = path + ".tmp.XXXXXX";
    std::vector<char> temporary(pattern.begin(), pattern.end());
    temporary.push_back('\0');
    const int descriptor = ::mkstemp(temporary.data());
    if (descriptor < 0) throw std::runtime_error("cannot create output temporary file");
    std::FILE* output = ::fdopen(descriptor, "w");
    if (output == nullptr) {
        ::close(descriptor);
        ::unlink(temporary.data());
        throw std::runtime_error("cannot open output stream");
    }
    const auto text = result.dump(2) + '\n';
    bool good = std::fwrite(text.data(), 1, text.size(), output) == text.size();
    good = std::fflush(output) == 0 && good;
    good = ::fsync(descriptor) == 0 && good;
    good = std::fclose(output) == 0 && good;
    // Hard-link publication is atomic and refuses an existing target. Never
    // truncate a previous result or a similarly named temporary artifact.
    const bool published = good && ::link(temporary.data(), path.c_str()) == 0;
    ::unlink(temporary.data());
    if (!published) throw std::runtime_error("cannot publish output; target may already exist");
}

std::uint64_t integer(const Json& object, const char* key)
{
    const auto& value = object.at(key);
    if (value.is_number_unsigned()) return value.get<std::uint64_t>();
    if (!value.is_number_integer() || value.get<std::int64_t>() < 0) {
        throw std::invalid_argument(std::string(key) + " requires a nonnegative integer");
    }
    return static_cast<std::uint64_t>(value.get<std::int64_t>());
}

struct Options {
    std::string profile, events, output;
    std::string arrival_mode{"fixed_arrival_trace"};
    std::uint64_t parallel_units{0};
};

Options options(int argc, char** argv)
{
    Options result;
    for (int index = 1; index < argc; index += 2) {
        if (index + 1 == argc) throw std::invalid_argument("missing option value");
        const std::string key = argv[index], value = argv[index + 1];
        if (key == "--profile") result.profile = value;
        else if (key == "--events") result.events = value;
        else if (key == "--output") result.output = value;
        else if (key == "--arrival-mode") result.arrival_mode = value;
        else if (key == "--parallel-units") {
            if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos) {
                throw std::invalid_argument("invalid parallel-unit count");
            }
            result.parallel_units = std::stoull(value);
            if (!result.parallel_units) throw std::invalid_argument("zero parallel-unit count");
        } else throw std::invalid_argument("unknown option: " + key);
    }
    if (result.arrival_mode != "fixed_arrival_trace" && result.arrival_mode != "closed_loop_qd") {
        throw std::invalid_argument("unknown arrival process");
    }
    if (result.profile.empty() || result.events.empty() || result.output.empty()) {
        throw std::invalid_argument(
            "usage: hbf_concurrent_trace_timing --profile FILE --events JSONL --output FILE "
            "[--parallel-units EXACT_PROFILE_PRODUCT] [--arrival-mode fixed_arrival_trace|closed_loop_qd]");
    }
    return result;
}

struct Request {
    Json input;
    hbfsim::HbfRequest descriptor;
    std::uint64_t consume_deadline;
    unsigned stage{0};
    std::uint64_t entered{0}, admitted{0}, media_complete{0}, reported_complete{0};
    std::size_t admission_qd{0};
};

std::vector<Request> read_requests(const std::string& path)
{
    std::ifstream stream(path);
    if (!stream) throw std::invalid_argument("cannot open arrival trace");
    std::vector<Request> result;
    std::set<std::uint64_t> ids;
    std::uint64_t previous_issue = 0;
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty()) throw std::invalid_argument("empty arrival record");
        const auto row = Json::parse(line);
        const auto id = integer(row, "request_id");
        const auto issue = integer(row, "issue_ns");
        const auto deadline = integer(row, "consume_deadline");
        const auto bytes = integer(row, "bytes");
        if (!id || !ids.insert(id).second) throw std::invalid_argument("zero or duplicate request ID");
        if (issue < previous_issue) {
            throw std::invalid_argument("arrival order is invalid");
        }
        if (!bytes || bytes > std::numeric_limits<std::uint32_t>::max()) {
            throw std::invalid_argument("request bytes exceed engine domain");
        }
        if (row.at("resource") != "mqsim_media" || row.at("channel") != "profile") {
            throw std::invalid_argument("unmapped resource/channel; MQSim must use profile address mapping");
        }
        for (const auto* field : {"layer", "step", "sequence"}) (void)integer(row, field);
        const auto operation = row.at("operation").get<std::string>();
        if (operation != "read" && operation != "write") {
            throw std::invalid_argument("unsupported media operation");
        }
        result.push_back(Request{
            .input = row,
            .descriptor = {.request_id = id, .sequence = result.size() + 1,
                           .arrival_ns = issue,
                           .logical_address = integer(row, "logical_address"),
                           .bytes = static_cast<std::uint32_t>(bytes),
                           .operation = static_cast<std::uint32_t>(operation == "write"
                                            ? hbfsim::RequestOperation::Write
                                            : hbfsim::RequestOperation::Read)},
            .consume_deadline = deadline,
        });
        previous_issue = issue;
    }
    if (stream.bad() || result.empty()) throw std::invalid_argument("empty or unreadable arrival trace");
    return result;
}

Json replay(const hbfsim::Profile& profile, std::vector<Request>& requests, bool closed_loop)
{
    hbfsim::MqsimOnlineEngine engine(profile);
    engine.enable_observations();
    std::map<std::uint64_t, std::size_t> by_id;
    std::uint64_t issued_bytes = 0;
    for (std::size_t index = 0; index < requests.size(); ++index) {
        const auto& descriptor = requests[index].descriptor;
        if (issued_bytes > std::numeric_limits<std::uint64_t>::max() - descriptor.bytes) {
            throw std::overflow_error("request byte sum overflow");
        }
        issued_bytes += descriptor.bytes;
        by_id.emplace(descriptor.request_id, index);
    }
    std::size_t next_to_issue = 0;
    const auto submit_next = [&](std::uint64_t issue) {
        auto& request = requests.at(next_to_issue++);
        if (closed_loop) {
            request.input["initial_issue_ns"] = request.descriptor.arrival_ns;
            request.input["issue_ns"] = issue;
            request.descriptor.arrival_ns = issue;
        }
        engine.submit(request.descriptor);
    };
    // Fixed arrivals are all staged before event advancement. Closed-loop
    // arrival times instead follow the separately declared completion policy.
    const auto initial = closed_loop ? std::min<std::size_t>(profile.queue_depth, requests.size())
                                     : requests.size();
    while (next_to_issue < initial) submit_next(0);
    std::set<std::uint64_t> completed;
    std::map<std::uint64_t, std::uint64_t> returned;
    std::map<std::size_t, std::uint64_t> qd_ns;
    std::size_t previous_qd = 0, peak_qd = 0;
    std::uint64_t previous_time = requests.front().descriptor.arrival_ns;
    std::uint64_t previous_reported = 0;
    std::uint64_t completed_bytes = 0;
    while (engine.pending()) {
        const auto completion = engine.run_next_completion();
        if (!completion || completion->status != static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready) ||
            !by_id.contains(completion->request_id) || !completed.insert(completion->request_id).second) {
            throw std::runtime_error("lost, duplicate, unknown or failed completion");
        }
        if (completion->modeled_completion_ns < previous_reported) {
            throw std::runtime_error("reported completion ordering failed");
        }
        previous_reported = completion->modeled_completion_ns;
        completed_bytes += requests.at(by_id.at(completion->request_id)).descriptor.bytes;
        returned.emplace(completion->request_id, completion->modeled_completion_ns);
        for (const auto& event : engine.take_observations()) {
            auto& request = requests.at(by_id.at(event.request_id));
            if (request.stage != static_cast<unsigned>(event.kind) ||
                event.time_ns < previous_time || event.device_outstanding > profile.queue_depth ||
                event.arrival_ns != request.descriptor.arrival_ns || event.bytes != request.descriptor.bytes) {
                throw std::runtime_error("observation order, identity or QD conservation failed");
            }
            qd_ns[previous_qd] += event.time_ns - previous_time;
            previous_time = event.time_ns;
            previous_qd = event.device_outstanding;
            peak_qd = std::max(peak_qd, previous_qd);
            ++request.stage;
            if (event.kind == hbfsim::MqsimEventKind::Arrival) request.entered = event.time_ns;
            else if (event.kind == hbfsim::MqsimEventKind::Admission) {
                request.admitted = event.time_ns;
                request.admission_qd = event.device_outstanding;
            } else {
                request.media_complete = event.time_ns;
                request.reported_complete = event.modeled_completion_ns;
            }
        }
        if (closed_loop && next_to_issue < requests.size()) {
            submit_next(completion->modeled_completion_ns);
        }
    }
    Json output = Json::array();
    for (const auto& request : requests) {
        if (request.stage != 3 || request.entered != request.descriptor.arrival_ns ||
            request.admitted < request.entered || request.media_complete < request.admitted ||
            request.reported_complete < request.media_complete ||
            request.reported_complete != returned.at(request.descriptor.request_id)) {
            throw std::runtime_error("incomplete or nonmonotonic request lifecycle");
        }
        const auto consume = std::max(request.consume_deadline, request.reported_complete);
        auto row = request.input;
        row.update(Json{{"queue_enter", request.entered}, {"service_start", request.admitted},
                        {"service_complete", request.media_complete}, {"reported_complete", request.reported_complete},
                        {"consume", consume}, {"queue_delay", request.admitted - request.entered},
                        {"service_delay", request.media_complete - request.admitted},
                        {"interface_bound_delay", request.reported_complete - request.media_complete},
                        {"residual_delay", consume - request.consume_deadline}, {"qd", request.admission_qd}});
        output.push_back(std::move(row));
    }
    if (completed.size() != requests.size() || next_to_issue != requests.size() ||
        completed_bytes != issued_bytes || previous_qd != 0) {
        throw std::runtime_error("final request/slot conservation failed");
    }
    Json histogram = Json::object();
    for (const auto& [qd, duration] : qd_ns) histogram[std::to_string(qd)] = duration;
    return Json{
        {"schema_version", 1}, {"service_source", "MQSIM_SIMULATED"}, {"provenance", "PROJECTED"},
        {"time_unit", "ns"},
        {"arrival_process", closed_loop ? "closed_loop_qd" : "fixed_arrival_trace"},
        {"scope", "MEDIA_REPLAY_NOT_CAUSAL_DECODE_OR_HARDWARE"},
        {"service_boundary", "MQSIM_HOST_ADMISSION_TO_MEDIA_CALLBACK"},
        {"channel_mapping", "MQSIM_PROFILE_ADDRESS_INTERLEAVING"},
        {"topology", {{"channels", profile.channels}, {"dies_per_channel", profile.dies_per_channel},
                      {"planes_per_die", profile.planes_per_die}, {"queue_depth", profile.queue_depth}}},
        {"summary", {{"issued", requests.size()}, {"completed", completed.size()},
                     {"issued_bytes", issued_bytes}, {"completed_bytes", completed_bytes},
                     {"peak_device_qd", peak_qd}}},
        {"qd_distribution", {{"scope", "HELD_ADMISSION_SLOTS_UNTIL_MEDIA_CALLBACK"},
                             {"start_ns", requests.front().descriptor.arrival_ns},
                             {"end_ns", previous_time}, {"duration_ns", histogram}}},
        {"requests", output}};
}
}  // namespace

int main(int argc, char** argv)
{
    try {
        const auto args = options(argc, argv);
        if (std::filesystem::exists(args.output)) throw std::invalid_argument("output already exists");
        const auto profile = hbfsim::load_profile(args.profile);
        const auto units = static_cast<std::uint64_t>(profile.channels) *
                           profile.dies_per_channel * profile.planes_per_die;
        if (args.parallel_units && args.parallel_units != units) {
            throw std::invalid_argument(
                "PROJECTED_ANALYTICAL required: requested N has no exact configured MQSim topology");
        }
        auto requests = read_requests(args.events);
        if (hbfsim::blocks_per_plane(profile) <= 10 &&
            std::any_of(requests.begin(), requests.end(), [](const auto& request) {
                return request.descriptor.operation == static_cast<std::uint32_t>(hbfsim::RequestOperation::Write);
            })) {
            throw std::invalid_argument("write workloads require more than 10 blocks per plane; increase profile capacity");
        }
        const bool closed_loop = args.arrival_mode == "closed_loop_qd";
        if (closed_loop && std::any_of(requests.begin(), requests.end(), [](const auto& request) {
                return request.descriptor.arrival_ns != 0;
            })) {
            throw std::invalid_argument("closed-loop input must start at zero; fixed schedule cannot be discarded");
        }
        auto result = replay(profile, requests, closed_loop);
        result["inputs"] = {{"profile_path", std::filesystem::absolute(args.profile).string()},
                            {"arrival_trace_path", std::filesystem::absolute(args.events).string()},
                            {"identity_scope", "HASHES_REQUIRED_IN_OUTER_RUN_MANIFEST"}};
        publish(args.output, result);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "hbf_concurrent_trace_timing: " << error.what() << '\n';
        return 2;
    }
}
