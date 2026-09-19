#include <hbfsim/eq3_thermal/observer.hpp>
#include <cmath>
#include <functional>
#include <iostream>
#include <stdexcept>
using namespace hbfsim::eq3_thermal;
namespace {
void require(bool v,const char* m){if(!v)throw std::runtime_error(m);}
void near(double a,double b){require(std::abs(a-b)<1e-8,"numeric invariant failed");}
void rejects(const std::function<void()>& f){bool caught=false;try{f();}catch(const std::exception&){caught=true;}require(caught,"expected rejection");}
ThermalModelConfig config(){
  ThermalModelConfig c;
  c.nodes={{"gpu",PhysicalType::Gpu,LogicalRole::Compute,"gpu",std::nullopt,2,300,0,0,300},
    {"hbf_base",PhysicalType::Hbf,LogicalRole::CapacityMemory,"hbf0",std::nullopt,2,300,0,0,300},
    {"hbf_die0",PhysicalType::Hbf,LogicalRole::CapacityMemory,"hbf0",0,1,300,0,0,300},
    {"hbf_die1",PhysicalType::Hbf,LogicalRole::CapacityMemory,"hbf0",1,1,300,0,0,300},
    {"hbm_base",PhysicalType::Hbm,LogicalRole::FastMemory,"hbm0",std::nullopt,2,300,0,0,300}};
  return c;
}
ObservedEvent event(Phase phase,std::uint64_t t,const std::string& id="x"){
  ObservedEvent e;e.event_id=id+":"+std::to_string(static_cast<int>(phase));
  e.operation_id=id;e.request_id=id;e.phase=phase;e.time_ns=t;e.operation="read";
  e.source="demand";e.physical_type="HBF";e.stack_id="hbf0";e.die=0;
  e.logical_bytes=64;e.physical_bytes=128;e.link_bytes=64;e.evidence="ENGINEERING_FIXTURE";
  e.component_power_w={{"hbf_base",2},{"hbf_die0",1},{"hbf_die1",3}};return e;
}
void lifecycle(ActivityObserver& o){
  o.submit(event(Phase::Complete,1500000000)); // queued, not completed early
  o.submit(event(Phase::End,1000000000));
  o.submit(event(Phase::Start,0));
}
void mode_energy_time(){
  ActivityObserver off(RuntimeMode::Off,config()),read(RuntimeMode::ReadOnly,config()),shadow(RuntimeMode::Shadow,config());
  require(!off.solver_constructed()&&!read.solver_constructed()&&shadow.solver_constructed(),"mode solver ownership");
  for(auto* o:{&off,&read,&shadow}){lifecycle(*o);o->advance_to(400000000);}
  require(off.journal().empty()&&off.energy_j().empty(),"off was not bypassed");
  require(read.terminals().empty()&&shadow.terminals().empty(),"future completion counted early");
  near(read.energy_j().at("hbf_base"),.8);near(shadow.energy_j().at("hbf_die1"),1.2);
  near(shadow.model()->temperatures_k()[1],300.4); // independent E/C hand check
  for(auto* o:{&read,&shadow}){o->advance_to(1000000000);o->advance_to(1500000000);}
  require(read.journal()==shadow.journal()&&read.terminals()==shadow.terminals(),"shadow changed events");
  require(read.terminals().size()==1,"completion lost");near(shadow.energy_j().at("hbf_die1"),3);
  near(shadow.energy_j().at("hbf_die0"),1);near(shadow.energy_j().at("hbf_base"),2);
  for(const auto& s:shadow.sensors().nodes)require(s.source==SensorSource::Simulated&&s.valid,"false sensor source");
  for(const auto& s:read.sensors().nodes)require(s.source==SensorSource::Unavailable&&!s.valid,"readonly ran model");
}
void duplicates_failures(){
  ActivityObserver o(RuntimeMode::Shadow,config());auto start=event(Phase::Start,0);
  require(o.submit(start)&&!o.submit(start),"duplicate replay not idempotent");
  auto bad=start;bad.component_power_w["hbf_base"]=9;rejects([&]{o.submit(bad);});
  o.submit(event(Phase::Fail,250000000));o.advance_to(1000000000);
  near(o.energy_j().at("hbf_base"),.5);require(o.terminals().size()==1,"failed work lost");
  rejects([&]{o.submit(event(Phase::Arrival,1,"late"));});
  auto missing=event(Phase::Start,1000000000,"bad");missing.component_power_w={{"missing",1}};
  rejects([&]{o.submit(missing);});missing.component_power_w={{"hbf_base",-1}};rejects([&]{o.submit(missing);});
  o.reset();require(o.time_ns()==0&&o.journal().empty(),"reset state");near(o.energy_j().at("hbf_base"),0);
  o.submit(event(Phase::Start,0));o.submit(event(Phase::Cancel,100000000));o.advance_to(500000000);
  near(o.energy_j().at("hbf_base"),.2);
}
void checkpoint_and_idle(){
  auto c=config();c.edges={{1,2,1,ConductanceKind::IntraComponent},{2,0,.5,ConductanceKind::InterComponent},{0,4,.2,ConductanceKind::InterComponent}};
  c.nodes[0].boundary_conductance_w_per_k=1;
  ActivityObserver continuous(RuntimeMode::Shadow,c),resumed(RuntimeMode::Shadow,c);
  auto e=event(Phase::Start,0);e.component_power_w={{"hbf_base",2}};
  continuous.submit(e);continuous.submit(event(Phase::End,500000000));
  continuous.submit(event(Phase::Complete,500000000));
  for(std::uint64_t i=1;i<=4;++i)continuous.advance_to(i*100000000);
  const auto state=continuous.checkpoint();resumed.restore(state);
  for(std::uint64_t i=5;i<=20;++i){continuous.advance_to(i*100000000);resumed.advance_to(i*100000000);}
  require(continuous.journal()==resumed.journal(),"restart duplicated events");
  for(std::size_t i=0;i<c.nodes.size();++i)near(continuous.model()->temperatures_k()[i],resumed.model()->temperatures_k()[i]);
  near(continuous.energy_j().at("hbf_base"),1);
  require(continuous.model()->temperatures_k()[2]>300&&continuous.model()->temperatures_k()[0]>300&&continuous.model()->temperatures_k()[4]>300,"base/GPU/HBM coupling absent");
  const double hot=continuous.model()->temperatures_k()[1];continuous.advance_to(10000000000ULL);
  require(continuous.model()->temperatures_k()[1]<hot,"idle cooling stopped");
  auto invalid=state;invalid.time_ns++;rejects([&]{resumed.restore(invalid);});
}
void external_domain(){
  ActivityObserver o(RuntimeMode::Shadow,config());auto e=event(Phase::Start,0);
  e.physical_type="GDDR";e.stack_id="external-gddr";e.component_power_w.clear();e.external_power_w=5;
  o.submit(e);auto end=e;end.phase=Phase::End;end.time_ns=1000000000;end.event_id="gddr-end";
  o.submit(end);o.advance_to(1000000000);
  near(o.external_energy_j(),5);for(double t:o.model()->temperatures_k())near(t,300);
}
}
int main(){mode_energy_time();duplicates_failures();checkpoint_and_idle();external_domain();
  std::cout<<"PASS observer modes, phase IDs, windows, failure/cancel, idle, base/die, restart, external domain\n";
}
