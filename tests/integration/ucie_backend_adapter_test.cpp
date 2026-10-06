#include "../../src/host_service/ucie_backend_adapter.hpp"

#include <hbfsim/api.h>
#include <json.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>
#include "ucie_context_wait_seam.hpp"

namespace {
using json = nlohmann::json;
using namespace hbfsim;
using namespace hbfsim::host_service;

void require(bool okay, const char* message)
{ if (!okay) throw std::runtime_error(message); }

json read_json(const std::filesystem::path& path)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot read adapter test input");
    return json::parse(input);
}

void write_json(const std::filesystem::path& path, const json& value)
{
    std::ofstream output(path);
    if (!output) throw std::runtime_error("cannot write adapter test input");
    output << value.dump(2) << '\n';
}

void run_case(const std::filesystem::path& top,
              const std::filesystem::path& worker,
              const std::filesystem::path& placement,
              const std::filesystem::path& report,
              UcieWaitMode mode, std::uint32_t page_bytes,
              std::uint64_t storage_bytes,
              std::uint64_t logical_page,
              std::uint64_t expected_media_commands,
              bool blocked_ring=false, bool earlier_second=false)
{
    std::vector<std::byte> memory(control_region_bytes(8));
    ControlView control(memory.data(),memory.size());
    require(control.initialize(8),"control init failed");
    control.header()->control_generation=1;
    control.ranges()[0]=SharedRangeRecord{
        .base=0x100000,
        .length=storage_bytes,
        .file_offset=0,
        .range_id=1,
        .mode=HBFSIM_RANGE_MODE_TIMING,
        .permissions=HBFSIM_RANGE_READ,
        .cache_policy=HBFSIM_CACHE_POLICY_NONE,
        .stream_id=0,
        .flags=0,
        .page_bytes=page_bytes,
        .reserved1=0,
    };
    atomic_store(control.header()->range_count,1,
                 std::memory_order_release);
    UcieBackendAdapter adapter(control,{.top_profile=top,
        .worker_executable=worker,.placement_manifest=placement,
        .wait_mode=mode});
    std::uint64_t ticket=0;
    const HbfRequest request{
        .request_id=11,
        .sequence=0,
        .arrival_ns=1'000'000'000'000ULL, // GPU globaltimer-like epoch
        .logical_address=logical_page,
        .deadline_ns=1'000'000'100'000ULL,
        .bytes=page_bytes,
        .range_id=1,
        .stream_id=0,
        .operation=static_cast<std::uint32_t>(RequestOperation::Read),
        .page_generation=3,
        .flags=0,
    };
    require(control.try_push_request(request,ticket),"push failed");
    std::uint64_t blocked_native=0;
    if (blocked_ring) {
        // Simulate a completion slot that cannot yet be published. Device R
        // must remain held until the original ticket can be published.
        auto& sequence=control.completion_slots()[ticket&7].sequence;
        atomic_store(sequence,ticket+1,std::memory_order_release);
        bool ready_while_blocked=false;
        for (std::uint32_t i=0;i<60'000 && !ready_while_blocked;++i) {
            (void)adapter.poll_once();
            if (i%128==0) {
                adapter.write_report(report);
                const auto snapshot=read_json(report);
                ready_while_blocked=snapshot.at("max_sim_ready_ns")>0;
            }
        }
        require(ready_while_blocked,"blocked-ring media did not reach R");
        adapter.write_report(report);
        const auto blocked=read_json(report);
        blocked_native=blocked.at("native_commands").get<std::uint64_t>();
        require(blocked.at("published")==0 &&
                blocked.at("caller_outstanding")==1 &&
                blocked_native>0,
                "blocked completion lost request or released too early");
        for (int i=0;i<5;++i) (void)adapter.poll_once();
        adapter.write_report(report);
        require(read_json(report).at("native_commands")==blocked_native,
                "blocked ticket caused another native submission");
        atomic_store(sequence,ticket,std::memory_order_release);
    }
    HbfCompletion completion{};
    bool consumed=false;
    for (std::uint32_t i=0;i<60'000 && !consumed;++i) {
        (void)adapter.poll_once();
        consumed=control.try_consume_completion(ticket,completion);
    }
    require(consumed,"adapter did not publish a real service completion");
    require(completion.request_id==11 && completion.page_generation==3 &&
            completion.status==static_cast<std::uint32_t>(RequestStatus::Ready),
            "original HBF ticket/identity/status changed");
    if (blocked_ring) {
        adapter.write_report(report);
        require(read_json(report).at("native_commands")==blocked_native,
                "unblocking old ticket resubmitted media");
    }
    if (earlier_second) {
        auto second=request;
        second.request_id=12;
        second.arrival_ns=request.arrival_ns-1; // later ticket, earlier GPU sample
        second.deadline_ns=request.deadline_ns;
        second.logical_address=page_bytes;
        std::uint64_t second_ticket=0;
        require(control.try_push_request(second,second_ticket),
                "earlier-timestamp second push failed");
        HbfCompletion second_completion{};
        bool second_consumed=false;
        for (std::uint32_t i=0;i<60'000 && !second_consumed;++i) {
            (void)adapter.poll_once();
            second_consumed=control.try_consume_completion(
                second_ticket,second_completion);
        }
        require(second_consumed &&
                second_completion.status==
                    static_cast<std::uint32_t>(RequestStatus::Ready),
                "earlier GPU timestamp underflowed or lost request");
    }
    if (mode==UcieWaitMode::Nominal)
        require(completion.modeled_ns>0 &&
                completion.modeled_completion_ns==request.arrival_ns+
                                                 completion.modeled_ns,
                "nominal simulated wait missing");
    else
        require(completion.modeled_ns==0 &&
                completion.modeled_completion_ns==request.arrival_ns &&
                completion.service_ns>0,
                "zero injection bypassed service or changed GPU epoch");
    adapter.write_report(report);
    const auto proof=read_json(report);
    const auto multiplier=earlier_second ? 2 : 1;
    require(proof.at("original_hbf_page_bytes")==page_bytes*multiplier &&
            proof.at("original_hbf_request_bytes_submitted")==
                page_bytes*multiplier &&
            proof.at("original_frontend_bytes")==page_bytes*multiplier &&
            proof.at("axi_payload_bytes")==page_bytes*multiplier &&
            proof.at("axi_child_transactions")==page_bytes*multiplier/64 &&
            proof.at("media_submit_bytes")==
                expected_media_commands*4096*multiplier &&
            proof.at("native_commands")==expected_media_commands*multiplier &&
            proof.at("gpu_globaltimer_epoch_ns")==request.arrival_ns &&
            proof.at("max_sim_ready_ns")>0,
            "page/AXI/media/native/clock report did not close");
    const auto& stacks=proof.at("stacks");
    require(stacks.is_array() && !stacks.empty(),
            "per-stack native service report missing");
    std::set<std::uint64_t> worker_pids;
    std::uint64_t native_sum=0;
    std::uint64_t media_sum=0;
    for (std::size_t index=0;index<stacks.size();++index) {
        const auto& stack=stacks.at(index);
        require(stack.at("stack")==index &&
                stack.at("current_time_ns")==proof.at("sim_now_ns"),
                "per-stack identity or common simulation time differs");
        worker_pids.insert(stack.at("process_id").get<std::uint64_t>());
        native_sum+=stack.at("native_commands").get<std::uint64_t>();
        media_sum+=stack.at("media_submit_bytes").get<std::uint64_t>();
        if (index>0)
            require(stack.at("native_commands")==0,
                    "single-stack fixture reached another stack's NAND");
    }
    require(worker_pids.size()==stacks.size() &&
            !worker_pids.contains(0) &&
            native_sum==proof.at("native_commands") &&
            media_sum==proof.at("media_submit_bytes") &&
            stacks.at(0).at("native_commands")>0,
            "per-stack worker identities or native totals do not close");
    if (earlier_second)
        require(proof.at("pre_epoch_arrivals")==1 &&
                proof.at("late_arrival_clamps")>=1,
                "earlier later-ticket GPU sample was not recorded/clamped");
}

void run_released_capacity_case(const std::filesystem::path& top,
                                const std::filesystem::path& worker,
                                const std::filesystem::path& folder)
{
    constexpr std::uint32_t page=16384;
    const auto placement=folder/"tight-placement.json";
    write_json(placement,{{"schema","hbfsim.ucie.host_placement.v1"},
        {"placements",json::array({{{"range_id",1},{"file_offset",0},
            {"length",2*page},{"page_bytes",page},
            {"registered_address",0x100000},
            {"canonical_physical_address",0},{"endpoint_id",7},
            {"generation",1}}})}});
    std::vector<std::byte> memory(control_region_bytes(8));
    ControlView control(memory.data(),memory.size());
    require(control.initialize(8),"tight control init failed");
    control.header()->control_generation=1;
    control.ranges()[0]=SharedRangeRecord{
        .base=0x100000,.length=2*page,.file_offset=0,.range_id=1,
        .mode=HBFSIM_RANGE_MODE_TIMING,.permissions=HBFSIM_RANGE_READ,
        .cache_policy=HBFSIM_CACHE_POLICY_NONE,.stream_id=0,.flags=0,
        .page_bytes=page,.reserved1=0};
    atomic_store(control.header()->range_count,1,std::memory_order_release);
    UcieBackendAdapter adapter(control,{.top_profile=top,
        .worker_executable=worker,.placement_manifest=placement,
        .wait_mode=UcieWaitMode::Nominal});
    std::uint64_t tickets[2]{};
    for (std::uint64_t i=0;i<2;++i) {
        HbfRequest request{.request_id=i+31,.sequence=0,
            .arrival_ns=1'000'000'000'000ULL+i,
            .logical_address=i*page,
            .deadline_ns=1'000'000'500'000ULL+i,
            .bytes=page,.range_id=1,.stream_id=0,
            .operation=static_cast<std::uint32_t>(RequestOperation::Read),
            .page_generation=1,.flags=0};
        require(control.try_push_request(request,tickets[i]),
                "tight request push failed");
    }
    std::uint32_t completed=0;
    for (std::uint32_t iteration=0;iteration<60000 && completed<2;
         ++iteration) {
        (void)adapter.poll_once();
        for (std::uint32_t i=0;i<2;++i) {
            if (tickets[i]==UINT64_MAX) continue;
            HbfCompletion completion{};
            if (control.try_consume_completion(tickets[i],completion)) {
                require(completion.status==static_cast<std::uint32_t>(
                            RequestStatus::Ready),
                        "tight request did not complete Ready");
                tickets[i]=UINT64_MAX;
                ++completed;
            }
        }
    }
    require(completed==2,"released capacity did not admit staged read");
    const auto report=folder/"tight-report.json";
    adapter.write_report(report);
    const auto proof=read_json(report);
    require(proof.at("published")==2 &&
            proof.at("caller_outstanding")==0 &&
            proof.at("original_hbf_request_bytes_submitted")==2*page &&
            proof.at("staged_admission_clamps")==1 &&
            proof.at("staged_admission_wait_sum_ns")>0 &&
            proof.at("sim_service_delta_sum_ns")>=
                proof.at("staged_admission_wait_sum_ns"),
            "released capacity lost staging or request time");
}
} // namespace

int main(int argc,char** argv)
{
    if (argc==5 && std::string(argv[4])=="--context-wait-seam")
        return hbfsim::publication_test::context_wait_seam(argv[3],argv[1],argv[2]);
    if (argc!=3) throw std::invalid_argument(
        "usage: ucie_backend_adapter_test WORKER SOURCE_TOP_PROFILE");
    const std::filesystem::path worker(argv[1]);
    const std::filesystem::path original_top(argv[2]);
    const auto folder=std::filesystem::temp_directory_path()/
        ("ucie-host-adapter-test-"+std::to_string(::getpid()));
    std::filesystem::create_directories(folder);
    auto top=read_json(original_top);
    top["link_profile"]=std::filesystem::absolute(
        original_top.parent_path()/top.at("link_profile").get<std::string>()).string();
    require(read_json(top.at("link_profile").get<std::string>())
                .at("scenario_assumption").at("max_accepted")>=128,
            "exact one-sense oracle requires wide fixture request credits");
    top["media_profile"]=std::filesystem::absolute(
        original_top.parent_path()/top.at("media_profile").get<std::string>()).string();
    top["software_limits"]["max_child_records"]=512;
    top["software_limits"]["max_read_bytes"]=16384;
    top["software_limits"]["host_reassembly_capacity_bytes"]=32768;
    const auto top_path=folder/"top.json";
    write_json(top_path,top);
    const auto placement=folder/"placement.json";
    write_json(placement,{{"schema","hbfsim.ucie.host_placement.v1"},
        {"placements",json::array({{{"range_id",1},
            {"file_offset",0},{"length",8192},{"page_bytes",4096},
            {"registered_address",0x100000},
            {"canonical_physical_address",0},{"endpoint_id",7},
            {"generation",1}}})}});
    run_case(top_path,worker,placement,folder/"nominal-4k.json",
             UcieWaitMode::Nominal,4096,8192,0,1,true,true);
    run_case(top_path,worker,placement,folder/"zero-4k.json",
             UcieWaitMode::ZeroInjected,4096,8192,0,1);
    // The original nominal resolver page is 16KiB. A 16KiB request for the
    // final page of a 16KiB+8B storage consumes trusted padded media extent,
    // while registered storage length remains 16KiB+8B.
    const auto partial=folder/"partial-final-page.json";
    write_json(partial,{{"schema","hbfsim.ucie.host_placement.v1"},
        {"placements",json::array({{{"range_id",1},
            {"file_offset",0},{"length",16392},{"page_bytes",16384},
            {"registered_address",0x100000},
            {"canonical_physical_address",0},{"endpoint_id",7},
            {"generation",1}}})}});
    run_case(top_path,worker,partial,folder/"nominal-16k-partial.json",
             UcieWaitMode::Nominal,16384,16392,16384,4);
    auto tight_top=top;
    tight_top["software_limits"]["max_parent_requests"]=1;
    tight_top["software_limits"]["max_child_records"]=256;
    tight_top["software_limits"]["host_reassembly_capacity_bytes"]=32768;
    const auto tight_top_path=folder/"tight-top.json";
    write_json(tight_top_path,tight_top);
    run_released_capacity_case(tight_top_path,worker,folder);
    auto invalid=read_json(placement);
    invalid["placements"].push_back(invalid["placements"][0]);
    const auto duplicate=folder/"duplicate.json";
    write_json(duplicate,invalid);
    std::vector<std::byte> memory(control_region_bytes(8));
    ControlView control(memory.data(),memory.size());
    require(control.initialize(8),"negative control init failed");
    control.header()->control_generation=1;
    bool rejected=false;
    try {
        UcieBackendAdapter bad(control,{.top_profile=top_path,
            .worker_executable=worker,.placement_manifest=duplicate});
    } catch (const std::invalid_argument&) { rejected=true; }
    require(rejected,"duplicate physical/range placement accepted");
    rejected=false;
    try {
        UcieBackendAdapter missing(control,{.top_profile=top_path,
            .worker_executable=worker,
            .placement_manifest=folder/"does-not-exist.json"});
    } catch (const std::runtime_error&) { rejected=true; }
    require(rejected,"missing placement manifest accepted");

    // A valid placement never turns a capacity-mode or write request into
    // a successful UCIe read. These are explicit old-ticket failures.
    control.ranges()[0]=SharedRangeRecord{
        .base=0x100000,.length=8192,.file_offset=0,.range_id=1,
        .mode=HBFSIM_RANGE_MODE_CAPACITY,.permissions=HBFSIM_RANGE_READ,
        .cache_policy=HBFSIM_CACHE_POLICY_NONE,.stream_id=0,.flags=0,
        .page_bytes=4096,.reserved1=0,
    };
    atomic_store(control.header()->range_count,1,
                 std::memory_order_release);
    UcieBackendAdapter strict(control,{.top_profile=top_path,
        .worker_executable=worker,.placement_manifest=placement});
    HbfRequest wrong{
        .request_id=21,.sequence=0,.arrival_ns=1'000'000,
        .logical_address=0,.deadline_ns=1'100'000,.bytes=4096,
        .range_id=1,.stream_id=0,
        .operation=static_cast<std::uint32_t>(RequestOperation::Read),
        .page_generation=1,.flags=0,
    };
    std::uint64_t wrong_ticket=0;
    require(control.try_push_request(wrong,wrong_ticket),"wrong-mode push failed");
    require(strict.poll_once(),"wrong-mode was not handled");
    HbfCompletion wrong_completion{};
    require(control.try_consume_completion(wrong_ticket,wrong_completion) &&
            wrong_completion.status==
                static_cast<std::uint32_t>(RequestStatus::Unsupported),
            "capacity-mode request silently became UCIe success");
    control.ranges()[0].mode=HBFSIM_RANGE_MODE_TIMING;
    wrong.request_id=22;
    wrong.operation=static_cast<std::uint32_t>(RequestOperation::Write);
    require(control.try_push_request(wrong,wrong_ticket),"write push failed");
    require(strict.poll_once(),"write was not handled");
    require(control.try_consume_completion(wrong_ticket,wrong_completion) &&
            wrong_completion.status==
                static_cast<std::uint32_t>(RequestStatus::Unsupported),
            "write silently became UCIe read success");
    strict.write_report(folder/"unsupported-report.json");
    require(read_json(folder/"unsupported-report.json")
                .at("native_commands")==0,
            "unsupported request reached native MQSim");
    // An equal-size but different live storage must not inherit this range's
    // canonical location merely because its id/offset/length are identical.
    control.ranges()[0].base=0x200000;
    wrong.request_id=23;
    wrong.operation=static_cast<std::uint32_t>(RequestOperation::Read);
    require(control.try_push_request(wrong,wrong_ticket),"swapped-base push failed");
    bool swapped_rejected=false;
    try { (void)strict.poll_once(); }
    catch (const std::runtime_error&) { swapped_rejected=true; }
    require(swapped_rejected,"same-size swapped live storage was accepted");
    std::cout << "ucie_backend_adapter_test 4k nominal/zero + 16k partial-page + negative PASS\n";
}
