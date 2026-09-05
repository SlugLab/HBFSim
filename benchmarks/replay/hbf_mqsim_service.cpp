// CPU-only JSON-lines transport for an external causal replay controller.
// One process owns one MQSim engine; read requests share its real service queue.
#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>
#include "replay_json.hpp"

#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace {
using Json = nlohmann::json;
using hbfsim::eval::integer;

void send(Json reply, hbfsim::MqsimOnlineEngine& engine)
{
    Json events = Json::array();
    for (const auto& event : engine.take_observations()) {
        events.push_back({{"kind", static_cast<unsigned>(event.kind)},
                          {"request_id", event.request_id}, {"arrival_ns", event.arrival_ns},
                          {"time_ns", event.time_ns}, {"reported_complete", event.modeled_completion_ns},
                          {"bytes", event.bytes}, {"device_outstanding", event.device_outstanding}});
    }
    reply["events"] = std::move(events);
    reply["now_ns"] = engine.current_time_ns();
    reply["pending"] = engine.pending();
    std::cout << reply.dump() << '\n' << std::flush;
    if (!std::cout) throw std::runtime_error("service response pipe failed");
}
}

int main(int argc, char** argv)
{
    try {
        std::string profile_path;
        std::uint64_t requested_units = 0;
        for (int i=1; i<argc; i+=2) {
            if (i+1==argc) throw std::invalid_argument("missing option value");
            const std::string key=argv[i], value=argv[i+1];
            if (key=="--profile") profile_path=value;
            else if (key=="--parallel-units") {
                if (value.empty() || value.find_first_not_of("0123456789")!=std::string::npos)
                    throw std::invalid_argument("invalid parallel units");
                requested_units=std::stoull(value);
                if (!requested_units) throw std::invalid_argument("zero parallel units");
            } else throw std::invalid_argument("unknown service option");
        }
        if (profile_path.empty()) throw std::invalid_argument("--profile is required");
        const auto profile=hbfsim::load_profile(profile_path);
        const auto units=static_cast<std::uint64_t>(profile.channels)*profile.dies_per_channel*profile.planes_per_die;
        if (requested_units && requested_units!=units)
            throw std::invalid_argument("PROJECTED_ANALYTICAL required: no exact configured MQSim topology");
        hbfsim::MqsimOnlineEngine engine(profile);
        engine.enable_observations();
        send({{"schema_version", 1}, {"service_source", "MQSIM_SIMULATED"}, {"provenance", "PROJECTED"},
              {"scope", "READ_ONLY_MEDIA_SERVICE_NOT_HARDWARE"}, {"queue_depth", profile.queue_depth},
              {"parallel_units", units}}, engine);
        std::map<std::uint64_t, std::uint32_t> accepted;
        std::set<std::uint64_t> completed;
        std::uint64_t issued_bytes=0, completed_bytes=0, sequence=0;
        std::string line;
        while (std::getline(std::cin, line)) {
            const auto input=Json::parse(line);
            const auto command=input.at("command").get<std::string>();
            if (command=="submit") {
                const auto& rows=input.at("requests");
                if (!rows.is_array() || rows.empty()) throw std::invalid_argument("submit needs a nonempty request array");
                std::vector<hbfsim::HbfRequest> batch;
                std::set<std::uint64_t> ids;
                auto total=issued_bytes;
                // Validate the whole batch before any request is accepted.
                for (const auto& row : rows) {
                    const auto id=integer(row, "request_id"), issue=integer(row, "issue_ns");
                    const auto bytes=integer(row, "bytes"), address=integer(row, "logical_address");
                    if (!id || accepted.contains(id) || !ids.insert(id).second)
                        throw std::invalid_argument("zero or reused request ID");
                    if (row.at("operation")!="read" || !bytes || bytes>std::numeric_limits<std::uint32_t>::max()
                        || bytes%512 || address%512 || bytes>profile.capacity_bytes || address>profile.capacity_bytes-bytes
                        || issue<engine.current_time_ns())
                        throw std::invalid_argument("invalid read extent or past arrival");
                    if (total>std::numeric_limits<std::uint64_t>::max()-bytes)
                        throw std::overflow_error("request byte total overflow");
                    total+=bytes;
                    batch.push_back({.request_id=id, .sequence=0, .arrival_ns=issue,
                                     .logical_address=address, .bytes=static_cast<std::uint32_t>(bytes), .operation=0});
                }
                if (batch.size()>std::numeric_limits<std::uint64_t>::max()-sequence)
                    throw std::overflow_error("service sequence exhausted");
                for (auto& request : batch) {
                    request.sequence=++sequence;
                    engine.submit(request);
                    accepted.emplace(request.request_id, request.bytes);
                }
                issued_bytes=total;
                send({{"accepted", batch.size()}}, engine);
            } else if (command=="until") {
                const auto completion=engine.run_next_completion_until(integer(input, "deadline_ns"));
                Json value=nullptr;
                if (completion) {
                    if (completion->status!=static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)
                        || !accepted.contains(completion->request_id) || !completed.insert(completion->request_id).second)
                        throw std::runtime_error("unknown, duplicate or failed completion");
                    completed_bytes+=accepted.at(completion->request_id);
                    value={{"request_id", completion->request_id}, {"reported_complete", completion->modeled_completion_ns},
                           {"status", completion->status}};
                }
                send({{"completion", value}}, engine);
            } else if (command=="finish") {
                if (engine.pending() || accepted.size()!=completed.size() || issued_bytes!=completed_bytes)
                    throw std::runtime_error("cannot finish with unreturned requests/bytes");
                send({{"status", "FINISHED"}, {"issued", accepted.size()}, {"completed", completed.size()},
                      {"issued_bytes", issued_bytes}, {"completed_bytes", completed_bytes}}, engine);
                return 0;
            } else throw std::invalid_argument("unknown service command");
        }
        throw std::runtime_error("service input closed without a complete finish record");
    } catch (const std::exception& error) {
        std::cerr << "hbf_mqsim_service: " << error.what() << '\n';
        return 2;
    }
}
