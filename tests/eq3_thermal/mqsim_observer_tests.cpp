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
    engine.submit({.request_id=id,.sequence=id,.arrival_ns=id<5?0ULL:15000ULL,
      .logical_address=(id-1)*16384,.bytes=16384,.operation=0});
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
  std::cout<<"PASS CPU_PATH_VERIFIED actual MQSim6requests; off/read_only/shadow; GPU_LIVE_NOT_TESTED\n"
    <<"wall_s off="<<off.wall<<" read_only="<<read.wall<<" shadow="<<shadow.wall
    <<" fixture_energy_j="<<shadow.energy<<" shadow_advice_samples="<<shadow.advice_samples<<"\n";
}
