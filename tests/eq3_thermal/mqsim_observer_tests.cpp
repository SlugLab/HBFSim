#include <hbfsim/eq3_thermal/mqsim_observer.hpp>
#include <hbfsim/profile.hpp>
#include <chrono>
#include <iostream>
#include <stdexcept>
using namespace hbfsim;
using namespace hbfsim::eq3_thermal;
namespace {
void require(bool v,const char* m){if(!v)throw std::runtime_error(m);}
struct Result {std::vector<HbfCompletion> completions;std::vector<ObservedEvent> events;double wall{},energy{};unsigned advice_samples{};};
HbfRequest request(std::uint64_t id,std::uint64_t arrival=0) {
  return {.request_id=id,.sequence=id,.arrival_ns=arrival,
    .logical_address=(id-1)*16384,.bytes=16384,.operation=0};
}
Result run(Profile profile,RuntimeMode mode){
  auto wall=std::chrono::steady_clock::now();
  ThermalModelConfig model;
  model.nodes={{"fixture_hbf",PhysicalType::Hbf,LogicalRole::CapacityMemory,"hbf0",std::nullopt,2,300,0,1,300}};
  ActivityObserver observer(mode,model);MqsimOnlineEngine engine(profile);
  MqsimObserverAdapter adapter(engine,observer);
  for(std::uint64_t id=1;id<=6;++id){
    ObservedEvent metadata;metadata.operation="read";metadata.source="demand";
    metadata.physical_type="HBF";metadata.stack_id="UNKNOWN_REQUEST_LEVEL";
    metadata.evidence="ENGINEERING_FIXTURE_REQUEST_OCCUPANCY";
    metadata.component_power_w={{"fixture_hbf",1}}; // declared 1W per admitted request, NOT measured
    adapter.bind(id,metadata);
    engine.submit(request(id,id<5?0ULL:15000ULL));
  }
  Result result;
  for(std::uint64_t horizon=10000000;horizon<=1100000000;horizon+=10000000){
    while(auto c=engine.run_next_completion_until(horizon)) {
      result.completions.push_back(*c);adapter.drain_to_current_time();
      require(c->modeled_completion_ns<=engine.current_time_ns(),"future reported completion leaked");
    }
    adapter.drain_to_current_time();
    // Tiny explicit fixture thresholds test advice without changing service.
    const auto advice=observer.advise({"fixture_hbf"},300.0000001,300.000001,300.00001);
    require(advice.has_value()==(mode==RuntimeMode::Shadow),"non-shadow advice");
    if(advice&&*advice)++result.advice_samples;
    for(const auto& e:observer.terminals())require(e.time_ns<=horizon,"future completion bin");
  }
  require(engine.pending()==0&&result.completions.size()==6,"lost actual MQSim requests");
  result.events=observer.journal();
  if(mode!=RuntimeMode::Off) {
    require(observer.terminals().size()==6,"adapter swallowed completion");
    result.energy=observer.energy_j().at("fixture_hbf");
    require(result.energy>0,"actual admission activity not consumed");
    for(const auto& e:result.events)require(!e.die&&!e.plane&&!e.physical_bytes&&!e.link_bytes,"fabricated command detail");
  }
  require(observer.solver_constructed()==(mode==RuntimeMode::Shadow),"solver mode mismatch");
  result.wall=std::chrono::duration<double>(std::chrono::steady_clock::now()-wall).count();return result;
}

struct GateResult {
  std::vector<HbfCompletion> completions;
  std::vector<ObservedEvent> events;
  std::uint64_t admitted_ns{},external_wait_ns{};
};

GateResult gated(Profile profile,RuntimeMode mode) {
  ThermalModelConfig model;
  model.nodes={{"fixture_hbf",PhysicalType::Hbf,LogicalRole::CapacityMemory,
    "hbf0",std::nullopt,2,300,0,1,300}};
  ActivityObserver observer(mode,model);MqsimOnlineEngine engine(profile);
  MqsimObserverAdapter observation(engine,observer);
  auto metadata=[] {
    ObservedEvent value;value.operation="read";value.source="demand";
    value.physical_type="HBF";value.stack_id="UNKNOWN_REQUEST_LEVEL";
    value.evidence="ENGINEERING_FIXTURE_REQUEST_OCCUPANCY";
    value.component_power_w={{"fixture_hbf",1}};return value;
  };
  observation.bind(101,metadata());observation.bind(102,metadata());
  constexpr std::uint64_t recovery_ns=200000000;
  MqsimSubmissionGateAdapter gate(engine,MqsimGateMode::Enabled,
    [=](const HbfRequest& value,std::uint64_t now) {
      if(value.request_id==102&&now<recovery_ns)
        return MqsimGateDecision{MqsimGateDisposition::Defer,recovery_ns,
                                 "fixture controlled endpoint unavailable"};
      return MqsimGateDecision{MqsimGateDisposition::Allow,std::nullopt,""};
    });
  require(gate.try_submit(request(101)).submitted,"first request not submitted");
  const auto deferred=gate.try_submit(request(102));
  require(!deferred.submitted&&deferred.decision.disposition==MqsimGateDisposition::Defer&&
          deferred.decision.target_time_ns==recovery_ns,"gate did not expose target defer");

  GateResult result;
  while(engine.current_time_ns()<recovery_ns) {
    if(auto completion=engine.run_next_completion_until(recovery_ns))
      result.completions.push_back(*completion);
    observation.drain_to_current_time();
  }
  require(engine.current_time_ns()==recovery_ns&&result.completions.size()==1,
          "gate wait did not deliver in-flight completion at the existing horizon");
  const auto admitted=gate.try_submit(request(102));
  require(admitted.submitted&&admitted.backend_arrival_ns==recovery_ns&&
          admitted.external_wait_ns==recovery_ns,"recovered request wait ledger mismatch");
  result.admitted_ns=*admitted.backend_arrival_ns;
  result.external_wait_ns=*admitted.external_wait_ns;
  bool duplicate_rejected=false;
  try {(void)gate.try_submit(request(102));}
  catch(const std::logic_error&) {duplicate_rejected=true;}
  require(duplicate_rejected,"gate submitted a recovered request twice");
  while(engine.pending()) {
    const auto horizon=engine.current_time_ns()+1000000000;
    const auto completion=engine.run_next_completion_until(horizon);
    observation.drain_to_current_time();
    if(completion)result.completions.push_back(*completion);
    else require(engine.current_time_ns()==horizon,"gated request lost before horizon");
  }
  result.events=observer.journal();
  require(observer.time_ns()==engine.current_time_ns()&&observer.terminals().size()==2,
          "observer did not consume wait/completion time");
  return result;
}

void thermal_gate_closed_loop(Profile profile) {
  ThermalModelConfig model;
  model.nodes={{"fixture_hbf",PhysicalType::Hbf,LogicalRole::CapacityMemory,
    "hbf0",std::nullopt,1,301,0,100,300}};
  ActivityObserver observer(RuntimeMode::Shadow,model);MqsimOnlineEngine engine(profile);
  MqsimObserverAdapter observation(engine,observer);
  ObservedEvent metadata;metadata.operation="read";metadata.source="demand";
  metadata.physical_type="HBF";metadata.stack_id="UNKNOWN_REQUEST_LEVEL";
  metadata.evidence="ENGINEERING_FIXTURE_REQUEST_OCCUPANCY";
  metadata.component_power_w={{"fixture_hbf",1}};observation.bind(401,metadata);
  unsigned decisions=0;
  MqsimSubmissionGateAdapter gate(engine,MqsimGateMode::Enabled,
    [&](const HbfRequest&,std::uint64_t now) {
      ++decisions;
      const auto advice=observer.advise({"fixture_hbf"},300.1,301.5,302);
      require(advice.has_value(),"shadow thermal advice unavailable");
      if(*advice!=0)
        return MqsimGateDecision{MqsimGateDisposition::Defer,now+10000000,
          "ENGINEERING_FIXTURE cooling recommendation"};
      return MqsimGateDecision{};
    });
  auto attempt=gate.try_submit(request(401));
  while(!attempt.submitted) {
    require(attempt.decision.disposition==MqsimGateDisposition::Defer&&
            attempt.decision.target_time_ns.has_value(),"thermal gate did not defer");
    const auto target=*attempt.decision.target_time_ns;
    while(engine.current_time_ns()<target) {
      require(!engine.run_next_completion_until(target),
              "unsubmitted thermal-gated request produced a completion");
      observation.drain_to_current_time();
    }
    attempt=gate.try_submit(request(401));
  }
  require(decisions>1&&attempt.backend_arrival_ns==engine.current_time_ns()&&
          attempt.external_wait_ns==engine.current_time_ns()&&
          observer.model()->temperatures_k().at(0)<300.1,
          "thermal cooling/advice did not control real submission");
  std::optional<HbfCompletion> completion;
  while(engine.pending()) {
    const auto horizon=engine.current_time_ns()+1000000000;
    completion=engine.run_next_completion_until(horizon);observation.drain_to_current_time();
    if(!completion)require(engine.current_time_ns()==horizon,
                           "thermal-gated request lost before horizon");
  }
  require(completion&&completion->request_id==401&&observer.terminals().size()==1,
          "thermal-gated MQSim completion was not delivered once");
}

void gate_contract(Profile profile) {
  {
    HbfCompletion baseline{};
    {
      MqsimOnlineEngine direct(profile);direct.submit(request(11));
      const auto completion=direct.run_next_completion();
      require(completion.has_value(),"direct MQSim request failed");baseline=*completion;
    }
    MqsimOnlineEngine bypassed(profile);
    MqsimSubmissionGateAdapter off(bypassed);
    const auto attempt=off.try_submit(request(11));
    const auto completion=bypassed.run_next_completion();
    require(attempt.submitted&&attempt.external_wait_ns==0&&completion&&
            baseline.modeled_completion_ns==completion->modeled_completion_ns&&
            baseline.modeled_ns==completion->modeled_ns,"disabled gate changed MQSim service");
  }
  const auto read=gated(profile,RuntimeMode::ReadOnly);
  const auto shadow=gated(profile,RuntimeMode::Shadow);
  require(read.completions.size()==2&&shadow.completions.size()==2&&
          read.events==shadow.events&&read.admitted_ns==200000000&&
          read.external_wait_ns==200000000,"gate modes changed service/event contract");
  for(std::size_t i=0;i<read.completions.size();++i)
    require(read.completions[i].request_id==shadow.completions[i].request_id&&
            read.completions[i].modeled_completion_ns==shadow.completions[i].modeled_completion_ns&&
            read.completions[i].modeled_ns==shadow.completions[i].modeled_ns,
            "shadow gate changed original MQSim completion");
  thermal_gate_closed_loop(profile);
  {
    MqsimOnlineEngine engine(profile);
    MqsimSubmissionGateAdapter blocked(engine,MqsimGateMode::Enabled,
      [](const HbfRequest&,std::uint64_t) {
        return MqsimGateDecision{MqsimGateDisposition::Blocked,std::nullopt,
                                 "fixture endpoint shutdown"};
      });
    require(!blocked.try_submit(request(21)).submitted&&engine.pending()==0,
            "blocked gate leaked a backend request");
  }
  {
    MqsimOnlineEngine engine(profile);unsigned decisions=0;
    MqsimSubmissionGateAdapter gate(engine,MqsimGateMode::Enabled,
      [&](const HbfRequest&,std::uint64_t) {
        ++decisions;return MqsimGateDecision{};
      });
    const auto future=gate.try_submit(request(31,5000));
    require(!future.submitted&&future.decision.disposition==MqsimGateDisposition::Defer&&
            future.decision.target_time_ns==5000&&decisions==0,
            "future request was evaluated before its target arrival");
    require(!engine.run_next_completion_until(5000)&&engine.current_time_ns()==5000,
            "existing horizon did not reach future arrival");
    require(gate.try_submit(request(31,5000)).submitted&&decisions==1,
            "future request was not admitted at arrival");
    require(engine.run_next_completion().has_value(),"future admitted request was lost");
  }
  const auto maintenance=MqsimSubmissionGateAdapter::capability(
    MqsimBackendOperation::DieLevelMaintenance);
  require(!maintenance.supported&&maintenance.status=="UNSUPPORTED_CAPABILITY",
          "unsupported die maintenance was promoted to a real backend operation");
}
}
int main(int argc,char** argv){
  require(argc==2,"supply existing nominal profile");auto p=load_profile(argv[1]);
  p.capacity_bytes=16ULL<<30;p.hbm_cache_bytes=64ULL<<20;p.queue_depth=2;
  p.aggregate_bandwidth_bytes_per_s=100000; // explicit fixture exercises delayed reported completion
  const auto off=run(p,RuntimeMode::Off),read=run(p,RuntimeMode::ReadOnly),shadow=run(p,RuntimeMode::Shadow);
  require(off.events.empty(),"off enabled observations");require(read.events==shadow.events,"shadow changed activity stream");
  for(std::size_t i=0;i<off.completions.size();++i)for(const auto* r:{&read,&shadow}){
    const auto& a=off.completions[i];const auto& b=r->completions[i];
    require(a.request_id==b.request_id&&a.modeled_completion_ns==b.modeled_completion_ns&&a.modeled_ns==b.modeled_ns&&a.service_ns==b.service_ns&&a.status==b.status,"mode changed actual MQSim service");
  }
  require(read.energy==shadow.energy,"shadow duplicated energy");
  require(shadow.advice_samples>0&&!off.advice_samples&&!read.advice_samples,"shadow advice not consumed");
  gate_contract(p);
  std::cout<<"PASS CPU_PATH_VERIFIED actual MQSim6requests; off/read_only/shadow; GPU_LIVE_NOT_TESTED\n"
    <<"PASS MQSIM_GATE default-off; nonblocking defer/recovery; maintenance=UNSUPPORTED_CAPABILITY\n"
    <<"wall_s off="<<off.wall<<" read_only="<<read.wall<<" shadow="<<shadow.wall
    <<" fixture_energy_j="<<shadow.energy<<" shadow_advice_samples="<<shadow.advice_samples<<"\n";
}
