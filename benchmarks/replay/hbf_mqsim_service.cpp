// CPU-only JSON-lines transport for an external causal replay controller.
// One process owns one MQSim engine; read requests share its real service queue.
#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>
#include <hbfsim/eq3_thermal/mqsim_observer.hpp>
#include <hbfsim/eq3_thermal/mqsim_stack_map.hpp>
#include <NVM_PHY_ONFI_NVDDR2.h>
#include "replay_json.hpp"

#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

namespace {
using Json = nlohmann::json;
using hbfsim::eval::integer;

std::uint64_t integer_value(const Json& value,const char* field)
{
    if(value.is_number_unsigned())return value.get<std::uint64_t>();
    if(!value.is_number_integer()||value.get<std::int64_t>()<0)
        throw std::invalid_argument(std::string(field)+" requires a nonnegative integer");
    return static_cast<std::uint64_t>(value.get<std::int64_t>());
}

hbfsim::eq3_thermal::MqsimStackMapAdapter load_stack_map(
    const std::string& path,const hbfsim::Profile& profile)
{
    std::ifstream input(path);
    if(!input)throw std::runtime_error("failed to open MQSim stack map");
    const auto config=Json::parse(input);
    if(integer(config,"schema_version")!=1||config.at("physical_kind")!="HBF"||
       config.at("route")!="direct"||config.at("address_layout")!="GLOBAL_PAGE_STRIPE_V1"||
       config.at("plane_allocation_scheme")!="CWDP"||
       integer(config,"page_bytes")!=profile.page_bytes||
       integer(config,"channels")!=profile.channels||
       integer(config,"dies_per_channel")!=profile.dies_per_channel)
        throw std::invalid_argument("MQSim stack map does not match the HBF profile");
    std::vector<hbfsim::eq3_thermal::MqsimStackChannelGroup> groups;
    for(const auto& row:config.at("stacks")) {
        hbfsim::eq3_thermal::MqsimStackChannelGroup group;
        group.stack_id=row.at("id").get<std::string>();
        const auto declared=integer(row,"declared_dies");
        if(declared>std::numeric_limits<std::uint32_t>::max())
            throw std::invalid_argument("MQSim stack-map die count overflows");
        group.declared_dies=static_cast<std::uint32_t>(declared);
        for(const auto& channel:row.at("channels")) {
            const auto value=integer_value(channel,"channel");
            if(value>std::numeric_limits<std::uint32_t>::max())
                throw std::invalid_argument("MQSim stack-map channel overflows");
            group.channels.push_back(static_cast<std::uint32_t>(value));
        }
        groups.push_back(std::move(group));
    }
    return {profile,std::move(groups)};
}

using PlacementLedger=std::map<std::uint64_t,hbfsim::eq3_thermal::MqsimStackPlacement>;

std::optional<std::string> requested_stack(const Json& row,bool mapping_enabled)
{
    if(!mapping_enabled)return std::nullopt;
    if(!row.contains("stack")||!row.at("stack").is_string()||
       !row.contains("route")||row.at("route")!="direct")
        throw std::invalid_argument("enabled MQSim HBF stack map requires stack and direct route");
    const auto stack=row.at("stack").get<std::string>();
    if(stack.empty())throw std::invalid_argument("empty requested HBF stack identity");
    return stack;
}

Json placement_json(const hbfsim::eq3_thermal::MqsimStackPlacement& placement)
{
    return {{"external_logical_page",placement.external_page},
            {"backend_logical_page",placement.backend_page},
            {"stack",placement.stack_id ? Json(*placement.stack_id) : Json(nullptr)},
            {"expected_channel",placement.expected_channel ?
                Json(*placement.expected_channel) : Json(nullptr)}};
}

hbfsim::eq3_thermal::MqsimStackPlacement place_request(
    const Json& row,const hbfsim::HbfRequest& external,
    const hbfsim::eq3_thermal::MqsimStackMapAdapter& stack_map)
{
    if(!stack_map.enabled())return stack_map.map(external);
    const auto requested=requested_stack(row,true);
    if(!row.contains("stack_local_page"))
        throw std::invalid_argument("enabled MQSim HBF stack map requires stack_local_page");
    const auto local_page=integer(row,"stack_local_page");
    const auto placement=stack_map.map_stack_page(external,*requested,local_page);
    if(row.contains("logical_address")&&
       integer(row,"logical_address")!=placement.external_page*external.bytes)
        throw std::invalid_argument("logical_address conflicts with stack-local placement");
    return placement;
}

void send(Json reply, hbfsim::MqsimOnlineEngine& engine,
          std::vector<SSD_Components::HBF_Command_Observation>* native=nullptr,
          const hbfsim::eq3_thermal::MqsimStackMapAdapter* stack_map=nullptr,
          const PlacementLedger* placements=nullptr)
{
    Json events = Json::array();
    for (const auto& event : engine.take_observations()) {
        events.push_back({{"kind", static_cast<unsigned>(event.kind)},
                          {"request_id", event.request_id}, {"arrival_ns", event.arrival_ns},
                          {"time_ns", event.time_ns}, {"reported_complete", event.modeled_completion_ns},
                          {"bytes", event.bytes}, {"device_outstanding", event.device_outstanding}});
    }
    reply["events"] = std::move(events);
    Json native_events=Json::array();
    if (native) {
        if (SSD_Components::NVM_PHY_ONFI_NVDDR2::Hbf_command_observation_failed())
            throw std::runtime_error("native MQSim command observation sink failed");
        for (const auto& event : *native) {
            Json transactions=Json::array();
            for (const auto& tr : event.transactions) {
                std::optional<std::string> actual_stack;
                const hbfsim::eq3_thermal::MqsimStackPlacement* expected=nullptr;
                if(stack_map&&stack_map->enabled())actual_stack=stack_map->stack_for_channel(tr.channel);
                if(placements&&tr.external_request_id) {
                    const auto found=placements->find(tr.external_request_id);
                    if(found!=placements->end())expected=&found->second;
                }
                if(expected&&(!actual_stack||!expected->stack_id||
                    *actual_stack!=*expected->stack_id||
                    !expected->expected_channel||tr.channel!=*expected->expected_channel||
                    (tr.logical_page_known&&tr.logical_page!=expected->backend_page)))
                    throw std::runtime_error("native MQSim command violated configured HBF stack placement");
                transactions.push_back({{"transaction_id",tr.transaction_id},
                    {"external_request_id",tr.external_request_id ? Json(tr.external_request_id) : Json(nullptr)},
                    {"source",tr.source},{"type",tr.type},
                    {"logical_page",tr.logical_page_known ? Json(tr.logical_page) : Json(nullptr)},
                    {"bytes",tr.bytes},
                    {"stack",actual_stack ? Json(*actual_stack) : Json(nullptr)},
                    {"expected_stack",expected&&expected->stack_id ? Json(*expected->stack_id) : Json(nullptr)},
                    {"external_logical_page",expected ? Json(expected->external_page) : Json(nullptr)},
                    {"backend_logical_page",expected ? Json(expected->backend_page) : Json(nullptr)},
                    {"channel",tr.channel},{"chip",tr.chip},
                    {"die",tr.die},{"plane",tr.plane},{"block",tr.block},{"page",tr.page}});
            }
            native_events.push_back({{"command_id",event.command_id},
                {"phase",static_cast<unsigned>(event.phase)},{"time_ns",event.time},
                {"command_code",event.command_code},{"transactions",std::move(transactions)}});
        }
        native->clear();
    }
    reply["native_command_events"] = std::move(native_events);
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
        std::string stack_map_path;
        std::uint64_t requested_units = 0;
        std::optional<std::uint64_t> gate_not_before_ns;
        std::optional<bool> native_command_option;
        for (int i=1; i<argc; i+=2) {
            if (i+1==argc) throw std::invalid_argument("missing option value");
            const std::string key=argv[i], value=argv[i+1];
            if (key=="--profile") profile_path=value;
            else if (key=="--parallel-units") {
                if (value.empty() || value.find_first_not_of("0123456789")!=std::string::npos)
                    throw std::invalid_argument("invalid parallel units");
                requested_units=std::stoull(value);
                if (!requested_units) throw std::invalid_argument("zero parallel units");
            } else if (key=="--gate-not-before-ns") {
                if (value.empty() || value.find_first_not_of("0123456789")!=std::string::npos)
                    throw std::invalid_argument("invalid gate target time");
                gate_not_before_ns=std::stoull(value);
            } else if (key=="--native-command-observations") {
                if (value!="on"&&value!="off") throw std::invalid_argument("invalid native observation mode");
                native_command_option=value=="on";
            } else if (key=="--stack-map") {
                if(value.empty())throw std::invalid_argument("empty MQSim stack-map path");
                stack_map_path=value;
            } else throw std::invalid_argument("unknown service option");
        }
        if (profile_path.empty()) throw std::invalid_argument("--profile is required");
        const auto profile=hbfsim::load_profile(profile_path);
        const auto units=static_cast<std::uint64_t>(profile.channels)*profile.dies_per_channel*profile.planes_per_die;
        if (requested_units && requested_units!=units)
            throw std::invalid_argument("PROJECTED_ANALYTICAL required: no exact configured MQSim topology");
        hbfsim::eq3_thermal::MqsimStackMapAdapter stack_map;
        if(!stack_map_path.empty())stack_map=load_stack_map(stack_map_path,profile);
        if(stack_map.enabled()&&native_command_option&&!*native_command_option)
            throw std::invalid_argument("MQSim stack map requires native command verification");
        const bool native_command_observations=stack_map.enabled()||native_command_option.value_or(false);
        std::vector<SSD_Components::HBF_Command_Observation> native_events;
        hbfsim::MqsimOnlineEngine engine(profile);
        if (native_command_observations) {
            SSD_Components::NVM_PHY_ONFI_NVDDR2::Set_hbf_command_observation_sink(
                [&native_events](const auto& event) { native_events.push_back(event); });
        }
        engine.enable_observations();
        hbfsim::eq3_thermal::MqsimSubmissionGateAdapter gate(
            engine,
            gate_not_before_ns ? hbfsim::eq3_thermal::MqsimGateMode::Enabled
                               : hbfsim::eq3_thermal::MqsimGateMode::Off,
            [gate_not_before_ns](const hbfsim::HbfRequest&,std::uint64_t now) {
                if (gate_not_before_ns&&now<*gate_not_before_ns)
                    return hbfsim::eq3_thermal::MqsimGateDecision{
                        hbfsim::eq3_thermal::MqsimGateDisposition::Defer,
                        *gate_not_before_ns,
                        "ENGINEERING_FIXTURE target-time gate"};
                return hbfsim::eq3_thermal::MqsimGateDecision{};
            });
        send({{"schema_version", 1}, {"service_source", "MQSIM_SIMULATED"}, {"provenance", "PROJECTED"},
              {"scope", "READ_ONLY_MEDIA_SERVICE_NOT_HARDWARE"}, {"queue_depth", profile.queue_depth},
              {"parallel_units", units},
              {"page_bytes",profile.page_bytes},
              {"aggregate_bandwidth_bytes_per_s",profile.aggregate_bandwidth_bytes_per_s},
              {"stack_mapping",stack_map.enabled() ?
                  "ACTUAL_MQSIM_CHANNEL_PARTITIONED_HBF_STACKS" : "OFF"},
              {"stack_mapping_evidence",stack_map.enabled() ?
                  "ENGINEERING_FIXTURE_CONFIGURATION_NOT_RESEARCH_GEOMETRY" : "OFF"},
              {"submission_gate", gate_not_before_ns ? "ENGINEERING_FIXTURE_ENABLED" : "OFF"},
              {"native_command_observations",native_command_observations ? "ON" : "OFF"}},
             engine,native_command_observations ? &native_events : nullptr,
             stack_map.enabled() ? &stack_map : nullptr,nullptr);
        std::map<std::uint64_t, std::uint32_t> accepted;
        PlacementLedger placements;
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
                std::vector<hbfsim::eq3_thermal::MqsimStackPlacement> batch_placements;
                std::set<std::uint64_t> ids;
                auto total=issued_bytes;
                // Validate the whole batch before any request is accepted.
                for (const auto& row : rows) {
                    const auto id=integer(row, "request_id"), issue=integer(row, "issue_ns");
                    const auto bytes=integer(row, "bytes");
                    const auto address=stack_map.enabled() ? 0 : integer(row,"logical_address");
                    if (!id || accepted.contains(id) || !ids.insert(id).second)
                        throw std::invalid_argument("zero or reused request ID");
                    if (row.at("operation")!="read" || !bytes || bytes>std::numeric_limits<std::uint32_t>::max()
                        || bytes%512 || address%512 || bytes>profile.capacity_bytes || address>profile.capacity_bytes-bytes
                        || issue<engine.current_time_ns())
                        throw std::invalid_argument("invalid read extent or past arrival");
                    if (total>std::numeric_limits<std::uint64_t>::max()-bytes)
                        throw std::overflow_error("request byte total overflow");
                    total+=bytes;
                    const hbfsim::HbfRequest external{
                        .request_id=id, .sequence=0, .arrival_ns=issue,
                        .logical_address=address, .bytes=static_cast<std::uint32_t>(bytes), .operation=0};
                    auto placement=place_request(row,external,stack_map);
                    batch.push_back(placement.backend_request);
                    batch_placements.push_back(std::move(placement));
                }
                if (batch.size()>std::numeric_limits<std::uint64_t>::max()-sequence)
                    throw std::overflow_error("service sequence exhausted");
                for (std::size_t i=0;i<batch.size();++i) {
                    auto& request=batch[i];
                    request.sequence=++sequence;
                    batch_placements[i].backend_request.sequence=request.sequence;
                    if(stack_map.enabled())
                        placements.emplace(request.request_id,batch_placements[i]);
                    engine.submit(request);
                    accepted.emplace(request.request_id, request.bytes);
                }
                issued_bytes=total;
                Json mapped=Json::array();
                if(stack_map.enabled())
                    for(const auto& placement:batch_placements)
                        mapped.push_back(placement_json(placement));
                send({{"accepted", batch.size()},
                      {"placements",stack_map.enabled() ? mapped : Json(nullptr)}}, engine,
                     native_command_observations ? &native_events : nullptr,
                     stack_map.enabled() ? &stack_map : nullptr,
                     stack_map.enabled() ? &placements : nullptr);
            } else if (command=="try_submit") {
                const auto& row=input.at("request");
                const auto id=integer(row,"request_id"), issue=integer(row,"issue_ns");
                const auto bytes=integer(row,"bytes");
                const auto address=stack_map.enabled() ? 0 : integer(row,"logical_address");
                if (!id || accepted.contains(id) || row.at("operation")!="read" || !bytes ||
                    bytes>std::numeric_limits<std::uint32_t>::max() || bytes%512 || address%512 ||
                    bytes>profile.capacity_bytes || address>profile.capacity_bytes-bytes ||
                    (!gate_not_before_ns&&issue<engine.current_time_ns()) ||
                    issued_bytes>std::numeric_limits<std::uint64_t>::max()-bytes ||
                    sequence==std::numeric_limits<std::uint64_t>::max())
                    throw std::invalid_argument("invalid gated read extent, identity or arrival");
                const hbfsim::HbfRequest external{.request_id=id,.sequence=sequence+1,.arrival_ns=issue,
                    .logical_address=address,.bytes=static_cast<std::uint32_t>(bytes),.operation=0};
                auto placement=place_request(row,external,stack_map);
                const auto attempt=gate.try_submit(placement.backend_request);
                Json gate_result={{"submitted",attempt.submitted},
                                  {"disposition",static_cast<unsigned>(attempt.decision.disposition)},
                                  {"reason",attempt.decision.reason},
                                  {"original_arrival_ns",attempt.original_arrival_ns},
                                  {"evaluated_ns",attempt.evaluated_ns}};
                gate_result["target_ns"]=attempt.decision.target_time_ns ?
                    Json(*attempt.decision.target_time_ns) : Json(nullptr);
                gate_result["backend_arrival_ns"]=attempt.backend_arrival_ns ?
                    Json(*attempt.backend_arrival_ns) : Json(nullptr);
                gate_result["external_wait_ns"]=attempt.external_wait_ns ?
                    Json(*attempt.external_wait_ns) : Json(nullptr);
                if (attempt.submitted) {
                    ++sequence;issued_bytes+=bytes;
                    accepted.emplace(id,static_cast<std::uint32_t>(bytes));
                    placement.backend_request.sequence=sequence;
                    if(stack_map.enabled())placements.emplace(id,placement);
                }
                send({{"gate",std::move(gate_result)},
                      {"placement",stack_map.enabled() ? placement_json(placement) : Json(nullptr)}},engine,
                     native_command_observations ? &native_events : nullptr,
                     stack_map.enabled() ? &stack_map : nullptr,
                     stack_map.enabled() ? &placements : nullptr);
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
                send({{"completion", value}}, engine,
                     native_command_observations ? &native_events : nullptr,
                     stack_map.enabled() ? &stack_map : nullptr,
                     stack_map.enabled() ? &placements : nullptr);
            } else if (command=="finish") {
                if (engine.pending() || accepted.size()!=completed.size() || issued_bytes!=completed_bytes)
                    throw std::runtime_error("cannot finish with unreturned requests/bytes");
                send({{"status", "FINISHED"}, {"issued", accepted.size()}, {"completed", completed.size()},
                      {"issued_bytes", issued_bytes}, {"completed_bytes", completed_bytes}}, engine,
                     native_command_observations ? &native_events : nullptr,
                     stack_map.enabled() ? &stack_map : nullptr,
                     stack_map.enabled() ? &placements : nullptr);
                if (native_command_observations)
                    SSD_Components::NVM_PHY_ONFI_NVDDR2::Clear_hbf_command_observation_sink();
                return 0;
            } else throw std::invalid_argument("unknown service command");
        }
        throw std::runtime_error("service input closed without a complete finish record");
    } catch (const std::exception& error) {
        std::cerr << "hbf_mqsim_service: " << error.what() << '\n';
        return 2;
    }
}
