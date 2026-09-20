#include <hbfsim/eq3_thermal/mqsim_stack_map.hpp>
#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>
#include <NVM_PHY_ONFI_NVDDR2.h>
#include <json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/resource.h>
#include <vector>

namespace {
using Json=nlohmann::json;
namespace fs=std::filesystem;

struct Options {
  fs::path profile,stack_map,output;
  std::uint64_t pages{},verify_pages{},maintenance_pages{};
  std::size_t outstanding_window{256};
  std::string source_label,source_sha256;
};

std::uint64_t u64(std::string_view text,const char* name) {
  if(text.empty()||text.find_first_not_of("0123456789")!=std::string_view::npos)
    throw std::invalid_argument(std::string("invalid ")+name);
  return std::stoull(std::string(text));
}
Options parse(int argc,char** argv) {
  Options o;
  for(int i=1;i<argc;i+=2) {
    if(i+1>=argc)throw std::invalid_argument("missing option value");
    const std::string k=argv[i],v=argv[i+1];
    if(k=="--profile")o.profile=v; else if(k=="--stack-map")o.stack_map=v;
    else if(k=="--output-dir")o.output=v; else if(k=="--pages")o.pages=u64(v,"pages");
    else if(k=="--verify-pages")o.verify_pages=u64(v,"verify-pages");
    else if(k=="--maintenance-pages")o.maintenance_pages=u64(v,"maintenance-pages");
    else if(k=="--outstanding-window"||k=="--batch-size")o.outstanding_window=static_cast<std::size_t>(u64(v,"outstanding-window"));
    else if(k=="--source-label")o.source_label=v; else if(k=="--source-sha256")o.source_sha256=v;
    else throw std::invalid_argument("unknown option: "+k);
  }
  if(!o.verify_pages)o.verify_pages=o.pages;
  if(o.profile.empty()||o.stack_map.empty()||o.output.empty()||!o.pages||!o.outstanding_window||
     o.verify_pages>o.pages||o.maintenance_pages>o.pages||o.source_label.empty()||o.source_sha256.size()!=64||
     o.source_sha256.find_first_not_of("0123456789abcdef")!=std::string::npos)
    throw std::invalid_argument("required: --profile --stack-map --output-dir --pages N "
      "[--verify-pages N] --maintenance-pages N --outstanding-window N --source-label TEXT --source-sha256 64-lower-hex");
  return o;
}

Json read_json(const fs::path& path) {
  std::ifstream in(path);if(!in)throw std::runtime_error("cannot open "+path.string());
  return Json::parse(in);
}
std::uint64_t integer(const Json& v,const char* field) {
  if(v.is_number_unsigned())return v.get<std::uint64_t>();
  if(!v.is_number_integer()||v.get<std::int64_t>()<0)throw std::invalid_argument(std::string("invalid ")+field);
  return static_cast<std::uint64_t>(v.get<std::int64_t>());
}

struct Mapping {
  hbfsim::eq3_thermal::MqsimStackMapAdapter adapter;
  std::vector<std::string> stacks;
};
Mapping load_map(const fs::path& path,const hbfsim::Profile& profile) {
  const auto j=read_json(path);
  if(integer(j.at("schema_version"),"schema_version")!=1||j.at("physical_kind")!="HBF"||
     j.at("route")!="direct"||j.at("address_layout")!="GLOBAL_PAGE_STRIPE_V1"||
     integer(j.at("page_bytes"),"page_bytes")!=profile.page_bytes||
     integer(j.at("channels"),"channels")!=profile.channels||
     integer(j.at("dies_per_channel"),"dies_per_channel")!=profile.dies_per_channel)
    throw std::invalid_argument("stack map/profile identity mismatch");
  std::vector<hbfsim::eq3_thermal::MqsimStackChannelGroup> groups;
  std::vector<std::string> names;
  for(const auto& row:j.at("stacks")) {
    hbfsim::eq3_thermal::MqsimStackChannelGroup g;
    g.stack_id=row.at("id").get<std::string>();names.push_back(g.stack_id);
    g.declared_dies=static_cast<std::uint32_t>(integer(row.at("declared_dies"),"declared_dies"));
    for(const auto& c:row.at("channels"))g.channels.push_back(static_cast<std::uint32_t>(integer(c,"channel")));
    groups.push_back(std::move(g));
  }
  return {hbfsim::eq3_thermal::MqsimStackMapAdapter(profile,std::move(groups)),std::move(names)};
}

std::string csv(std::string value) {
  std::string out="\"";for(char c:value){if(c=='\"')out+="\"\"";else out+=c;}return out+="\"";
}
const char* maintenance_status(hbfsim::MqsimMaintenanceStatus s) {
  using S=hbfsim::MqsimMaintenanceStatus;
  switch(s) {
    case S::Committed:return "COMMITTED";case S::CommittedReclaimDeferred:return "COMMITTED_RECLAIM_DEFERRED";
    case S::RejectedUnsupported:return "REJECTED_UNSUPPORTED";case S::RejectedInvalidTarget:return "REJECTED_INVALID_TARGET";
    case S::RejectedUnmapped:return "REJECTED_UNMAPPED";case S::RejectedNoSpare:return "REJECTED_NO_SPARE";
    case S::FailedRead:return "FAILED_READ";case S::FailedProgram:return "FAILED_PROGRAM";
    case S::FailedStaleVersion:return "FAILED_STALE_VERSION";
    case S::FailedAfterCommitNeedsReconcile:return "FAILED_AFTER_COMMIT_NEEDS_RECONCILE";
    case S::RejectedSourceBusy:return "REJECTED_SOURCE_BUSY";case S::FailedVersionOverflow:return "FAILED_VERSION_OVERFLOW";
  } throw std::logic_error("unknown maintenance status");
}

struct RequestRecord {
  std::uint64_t id{},external_page{},backend_page{},arrival{},completion{};
  std::string phase,stack;std::uint32_t expected_channel{};
};

void publish(const fs::path& path,const Json& value) {
  std::ofstream out(path,std::ios::out|std::ios::trunc);if(!out)throw std::runtime_error("cannot create "+path.string());
  out<<std::setw(2)<<value<<'\n';out.flush();if(!out)throw std::runtime_error("cannot write "+path.string());
}
}

int main(int argc,char** argv) {
  try {
    const auto o=parse(argc,argv);
    if(fs::exists(o.output)||!fs::create_directories(o.output))throw std::invalid_argument("output directory already exists or cannot be created");
    const auto started=std::chrono::steady_clock::now();
    const auto profile=hbfsim::load_profile(o.profile);auto mapping=load_map(o.stack_map,profile);
    if(o.pages>profile.capacity_bytes/profile.page_bytes)throw std::out_of_range("page count exceeds profile capacity");
    if(hbfsim::blocks_per_plane(profile)<=10)
      throw std::invalid_argument("write diagnostic requires more than 10 blocks per plane for MQSim spare/frontier safety");
    std::vector<SSD_Components::HBF_Command_Observation> native;
    SSD_Components::NVM_PHY_ONFI_NVDDR2::Set_hbf_command_observation_sink(
      [&native](const auto& event){native.push_back(event);});
    std::vector<RequestRecord> records;records.reserve(static_cast<std::size_t>(o.pages+o.verify_pages));
    std::map<std::uint64_t,hbfsim::eq3_thermal::MqsimStackPlacement> placements;
    std::vector<hbfsim::MqsimObservation> observations;
    std::vector<hbfsim::MqsimMaintenanceEvent> maintenance_events;
    std::vector<hbfsim::MqsimMaintenanceCompletion> maintenance_completions;
    std::uint64_t write_end=0,read_end=0,maintenance_end=0;
    {
      hbfsim::MqsimOnlineEngine engine(profile);engine.enable_observations();
      const auto run_phase=[&](std::string phase,std::uint64_t id_base,hbfsim::RequestOperation operation,
                               const std::vector<std::uint64_t>& pages) {
        std::size_t next=0;std::map<std::uint64_t,std::size_t> pending;
        const auto refill=[&]() {
          while(next<pages.size()&&pending.size()<o.outstanding_window) {
            const auto page=pages[next++];
            const auto id=id_base+page;const auto& stack=mapping.stacks.at(page%mapping.stacks.size());
            hbfsim::HbfRequest external{.request_id=id,.sequence=id,.arrival_ns=engine.current_time_ns(),
              .logical_address=0,.bytes=profile.page_bytes,.operation=static_cast<std::uint32_t>(operation)};
            auto p=mapping.adapter.map_stack_page(external,stack,page/mapping.stacks.size());
            if(!p.expected_channel)throw std::logic_error("mapped request lacks expected channel");
            if(placements.contains(id))throw std::logic_error("duplicate request identity");
            placements.emplace(id,p);engine.submit(p.backend_request);
            records.push_back({id,p.external_page,p.backend_page,p.backend_request.arrival_ns,0,phase,stack,*p.expected_channel});
            if(!pending.emplace(id,records.size()-1).second)throw std::logic_error("duplicate pending identity");
          }
        };
        refill();
        while(!pending.empty()) {
          auto c=engine.run_next_completion();
          if(!c)throw std::runtime_error("pending rolling window returned no completion");
          const auto found=pending.find(c->request_id);
          if(found==pending.end())throw std::runtime_error(
              "unknown/duplicate completion id="+std::to_string(c->request_id)+
              " pending_first="+(pending.empty()?std::string("NONE"):std::to_string(pending.begin()->first)));
          records.at(found->second).completion=c->modeled_completion_ns;pending.erase(found);
          auto batch=engine.take_observations();observations.insert(observations.end(),batch.begin(),batch.end());
          refill();
        }
      };
      std::vector<std::uint64_t> write_pages(static_cast<std::size_t>(o.pages));
      for(std::uint64_t page=0;page<o.pages;++page)write_pages[page]=page;
      std::vector<std::uint64_t> verify_pages;verify_pages.reserve(static_cast<std::size_t>(o.verify_pages));
      if(o.verify_pages==1)verify_pages.push_back(o.pages-1);
      else for(std::uint64_t index=0;index<o.verify_pages;++index)
        verify_pages.push_back(static_cast<std::uint64_t>((static_cast<unsigned __int128>(index)*(o.pages-1))/(o.verify_pages-1)));
      if(std::adjacent_find(verify_pages.begin(),verify_pages.end())!=verify_pages.end())throw std::logic_error("verification sample is not unique");
      run_phase("STARTUP_WRITE",1,hbfsim::RequestOperation::Write,write_pages);
      write_end=engine.current_time_ns();
      run_phase("VERIFY_READ",1000000001ULL,hbfsim::RequestOperation::Read,verify_pages);
      read_end=engine.current_time_ns();
      for(std::uint64_t page=0;page<o.maintenance_pages;++page) {
        const auto& p=placements.at(1+page);const auto backend=p.backend_page;
        const auto channel=static_cast<std::uint32_t>(backend%profile.channels);
        const auto die=static_cast<std::uint32_t>((backend/profile.channels)%profile.dies_per_channel);
        const auto plane=static_cast<std::uint32_t>((backend/(static_cast<std::uint64_t>(profile.channels)*profile.dies_per_channel))%profile.planes_per_die);
        engine.submit_maintenance({.request_id=2000000001ULL+page,.parent_id=3000000001ULL+page,
          .due_ns=read_end,.deadline_ns=read_end+1000000000ULL,.logical_page=backend,
          .channel=channel,.chip=0,.die=die,.plane=plane,.plane_is_exact=true});
      }
      while(engine.pending_maintenance()) {
        (void)engine.run_next_completion_until(engine.current_time_ns()+1000000000ULL);
        auto e=engine.take_maintenance_events();maintenance_events.insert(maintenance_events.end(),e.begin(),e.end());
        auto c=engine.take_maintenance_completions();maintenance_completions.insert(maintenance_completions.end(),c.begin(),c.end());
        auto obs=engine.take_observations();observations.insert(observations.end(),obs.begin(),obs.end());
      }
      auto e=engine.take_maintenance_events();maintenance_events.insert(maintenance_events.end(),e.begin(),e.end());
      auto c=engine.take_maintenance_completions();maintenance_completions.insert(maintenance_completions.end(),c.begin(),c.end());
      if(engine.pending())throw std::runtime_error("foreground work remains pending");
      maintenance_end=engine.current_time_ns();
      if(SSD_Components::NVM_PHY_ONFI_NVDDR2::Hbf_command_observation_failed())throw std::runtime_error("native observation sink failed");
    }
    SSD_Components::NVM_PHY_ONFI_NVDDR2::Clear_hbf_command_observation_sink();
    if(records.size()!=o.pages+o.verify_pages||maintenance_completions.size()!=o.maintenance_pages)
      throw std::runtime_error("final request/maintenance conservation failed");
    if(std::any_of(records.begin(),records.end(),[](const auto& r){return !r.completion;}))throw std::runtime_error("missing completion time");
    if(std::any_of(maintenance_completions.begin(),maintenance_completions.end(),[](const auto& c){return !c.mapping_committed;}))
      throw std::runtime_error("maintenance did not commit every requested page");

    std::ofstream req(o.output/"requests.csv");req<<"phase,request_id,stack,external_page,backend_page,expected_channel,arrival_ns,completion_ns,bytes\n";
    for(const auto& r:records)req<<r.phase<<','<<r.id<<','<<csv(r.stack)<<','<<r.external_page<<','<<r.backend_page<<','<<r.expected_channel<<','<<r.arrival<<','<<r.completion<<','<<profile.page_bytes<<'\n';
    std::ofstream obs(o.output/"request-observations.csv");obs<<"kind,request_id,arrival_ns,time_ns,reported_complete_ns,bytes,device_outstanding\n";
    for(const auto& e:observations)obs<<static_cast<unsigned>(e.kind)<<','<<e.request_id<<','<<e.arrival_ns<<','<<e.time_ns<<','<<e.modeled_completion_ns<<','<<e.bytes<<','<<e.device_outstanding<<'\n';
    std::ofstream nev(o.output/"native-events.csv");nev<<"command_id,phase,time_ns,command_code,transaction_id,external_request_id,maintenance_request_id,maintenance_parent_id,source,type,logical_page,logical_page_known,bytes,stack,channel,chip,die,plane,block,page\n";
    for(const auto& e:native)for(const auto& t:e.transactions){auto stack=mapping.adapter.stack_for_channel(t.channel);nev<<e.command_id<<','<<static_cast<unsigned>(e.phase)<<','<<e.time<<','<<static_cast<unsigned>(e.command_code)<<','<<t.transaction_id<<','<<t.external_request_id<<','<<t.maintenance_request_id<<','<<t.maintenance_parent_id<<','<<t.source<<','<<t.type<<','<<t.logical_page<<','<<(t.logical_page_known?1:0)<<','<<t.bytes<<','<<csv(stack.value_or("UNKNOWN"))<<','<<t.channel<<','<<t.chip<<','<<t.die<<','<<t.plane<<','<<t.block<<','<<t.page<<'\n';}
    std::ofstream jsonl(o.output/"native-events.jsonl");std::uint64_t prior_time=0;
    for(const auto& e:native) {
      if(e.time<prior_time)throw std::runtime_error("native command events are not chronological");prior_time=e.time;
      Json transactions=Json::array();
      for(const auto& t:e.transactions) {
        auto stack=mapping.adapter.stack_for_channel(t.channel);
        transactions.push_back({{"transaction_id",t.transaction_id},{"external_request_id",t.external_request_id?t.external_request_id:0},
          {"maintenance_request_id",t.maintenance_request_id?t.maintenance_request_id:0},
          {"maintenance_parent_id",t.maintenance_parent_id?t.maintenance_parent_id:0},
          {"source",t.source},{"type",t.type},{"logical_page",t.logical_page_known?Json(t.logical_page):Json(nullptr)},
          {"bytes",t.bytes},{"stack",stack?Json(*stack):Json("UNKNOWN")},{"channel",t.channel},{"chip",t.chip},
          {"die",t.die},{"plane",t.plane},{"block",t.block},{"page",t.page}});
      }
      jsonl<<Json{{"command_id",e.command_id},{"phase",static_cast<unsigned>(e.phase)},
                  {"time_ns",e.time},{"command_code",static_cast<unsigned>(e.command_code)},
                  {"transactions",std::move(transactions)}}.dump()<<'\n';
    }
    std::ofstream m(o.output/"maintenance.csv");m<<"request_id,status,enqueue_ns,start_ns,end_ns,logical_page,source_version,committed_version,mapping_committed,source_retired,erase_completed,transaction_count\n";
    for(const auto& c:maintenance_completions)m<<c.request_id<<','<<maintenance_status(c.status)<<','<<c.enqueue_ns<<','<<c.start_ns<<','<<c.end_ns<<','<<c.logical_page<<','<<c.source_version<<','<<c.committed_version<<','<<(c.mapping_committed?1:0)<<','<<(c.source_retired?1:0)<<','<<(c.erase_completed?1:0)<<','<<c.transaction_ids.size()<<'\n';
    for(auto* stream:{&req,&obs,&nev,&jsonl,&m}){stream->flush();if(!*stream)throw std::runtime_error("raw evidence write failed");}
    const auto wall=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
    rusage usage{};getrusage(RUSAGE_SELF,&usage);
    std::set<std::uint64_t> write_ids,read_ids;for(const auto& r:records)(r.phase=="STARTUP_WRITE"?write_ids:read_ids).insert(r.id);
    Json summary={{"schema_version","eq3-startup-write-probe-v1"},{"classification","BACKEND_FIXED_TRACE_DIAGNOSTIC"},
      {"source_provenance",{{"label",o.source_label},{"sha256",o.source_sha256}}},
      {"profile",fs::absolute(o.profile).string()},{"stack_map",fs::absolute(o.stack_map).string()},
      {"payload_semantics","METADATA_AND_NATIVE_COMMANDS_ONLY_PAYLOAD_BYTES_UNAVAILABLE"},
      {"maintenance_semantics","SOFTWARE_LIFECYCLE_TEST_ON_FRESHLY_PROGRAMMED_PAGES_NOT_RETENTION_DUE"},
      {"page_age_origin","EACH_STARTUP_PROGRAM_COMPLETION"},
      {"request_id_ranges",{{"startup_write","1..pages"},{"verify_read","1000000001..1000000000+pages"},
                            {"software_lifecycle_maintenance","2000000001..2000000000+maintenance_pages"}}},
      {"host_ingress_energy","UNAVAILABLE_NOT_FABRIC_RETURN_DELIVERY"},{"page_bytes",profile.page_bytes},{"pages",o.pages},
      {"submission_policy","BOUNDED_ROLLING_REFILL_ON_EACH_COMPLETION"},{"outstanding_window",o.outstanding_window},
      {"write_requests",write_ids.size()},{"read_requests",read_ids.size()},
      {"write_mapping_validation",{{"same_page_reads_completed",read_ids.size()},
                                    {"read_coverage",{{"method",o.verify_pages==o.pages?"ALL_LOADED_PAGES":
                                                                    (o.verify_pages==1?"LAST_PAGE_ONLY":"EVENLY_STRATIFIED_INCLUDING_FIRST_AND_LAST")},
                                                      {"loaded_pages",o.pages},{"verified_pages",o.verify_pages},
                                                      {"includes_last_page",true}}},
                                    {"fresh_pages_maintenance_committed",maintenance_completions.size()},
                                    {"payload_validation","UNAVAILABLE"}}},
      {"phase_boundaries_ns",{{"startup_write_start",0},{"startup_write_end",write_end},
                               {"verify_read_start",write_end},{"verify_read_end",read_end},
                               {"software_lifecycle_maintenance_start",read_end},
                               {"software_lifecycle_maintenance_end",maintenance_end}}},
      {"maintenance_requests",o.maintenance_pages},{"maintenance_committed",maintenance_completions.size()},
      {"native_command_events",native.size()},{"request_observations",observations.size()},
      {"wall_seconds",wall},{"max_rss_kib",usage.ru_maxrss},{"status","PASS"}};
    publish(o.output/"summary.json",summary);publish(o.output/"DONE.json",{{"status","PASS"},{"wall_seconds",wall},{"max_rss_kib",usage.ru_maxrss}});
    std::cout<<summary.dump()<<'\n';return 0;
  } catch(const std::exception& e) {
    SSD_Components::NVM_PHY_ONFI_NVDDR2::Clear_hbf_command_observation_sink();
    std::cerr<<"eq3_startup_write_probe: "<<e.what()<<'\n';return 2;
  }
}
