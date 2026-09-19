#include <hbfsim/eq3_thermal/cpu_service.hpp>
#include <hbfsim/eq3_thermal/thermal.hpp>
#include <json.hpp>
#include <cmath>
#include <iostream>
#include <functional>
#include <stdexcept>
#include <sstream>
using namespace hbfsim::eq3_thermal;
using J=nlohmann::json;
void check(bool b,const char* s){if(!b)throw std::runtime_error(s);}
void near(double a,double b){check(std::abs(a-b)<1e-7,"energy arithmetic");}
void rejects(const std::function<void()>& f){bool hit=false;try{f();}catch(const std::exception&){hit=true;}check(hit,"expected rejection");}
J fixture(std::string topology="mixed_direct",unsigned hbms=4) {
  ThermalModelConfig c;c.nodes={{"gpu",PhysicalType::Gpu,LogicalRole::Compute,"gpu",std::nullopt,1,300,0,1,300}};
  J j={{"evidence","ENGINEERING_FIXTURE"},{"topology",topology},{"policy","none"},
    {"thermal_step_ns",10000000},{"sample_ns",10000000},{"hbf_maintenance_period_ns",10000000000ULL},
    {"hbm_refresh_period_ns",20000000000ULL},{"page_bytes",4096},{"initial_age_ns",0},
    {"external_fast_memory","GDDR"},{"custom_base_die_relay",true},
    {"duration_ns",{{"read",100000000},{"program",200000000},{"erase",300000000},{"write",100000000},{"dram_refresh",50000000}}},
    {"power_w",{{"read_array",2},{"program_array",4},{"erase_array",5},{"write_array",3},{"refresh_array",1},
       {"base",1},{"relay_base",3},{"gpu_phy",.5},{"gddr",7},{"gpu_external",0}}},
    {"stacks",J::array()},{"control",J::object()}};
  for(unsigned i=0;i<8;++i) {
    const bool hbm=i<hbms;const auto index=hbm?i:i-hbms;const std::string id=(hbm?"hbm":"hbf")+std::to_string(index);
    J s={{"id",id},{"physical_kind",hbm?"HBM4":"HBF"},{"die_count",2}};
    if(!hbm)s["pair"]="hbm"+std::to_string(index);j["stacks"].push_back(s);
    const auto physical=hbm?PhysicalType::Hbm:PhysicalType::Hbf;const auto role=hbm?LogicalRole::FastMemory:LogicalRole::CapacityMemory;
    c.nodes.push_back({id+"_base",physical,role,id,std::nullopt,1,300,0,1,300});
    for(unsigned d=0;d<2;++d)c.nodes.push_back({id+"_die"+std::to_string(d),physical,role,id,d,1,300,0,1,300});
    j["control"][id]={{"light_k",310},{"severe_k",320},{"shutdown_k",330},{"hysteresis_k",.05},
      {"action_delay_ns",20000000},{"min_dwell_ns",30000000},{"light_gap_ns",250000000}};
  }
  std::ostringstream model;model<<"HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n";
  for(const auto& n:c.nodes)model<<"node "<<n.id<<' '<<(n.physical_type==PhysicalType::Gpu?"gpu":n.physical_type==PhysicalType::Hbm?"hbm":"hbf")<<' '
    <<(n.logical_role==LogicalRole::Compute?"compute":n.logical_role==LogicalRole::FastMemory?"fast_memory":"capacity_memory")<<' '<<n.group_id<<' '
    <<(n.die_index?std::to_string(*n.die_index):"-1")<<" 1 300 0 1 300\n";
  j["thermal_model_text"]=model.str();return j;
}
J request(std::string id,std::string stack="hbf0",std::string route="direct",std::string op="read",unsigned die=0) {
  return {{"id",id},{"stack",stack},{"route",route},{"op",op},{"die",die},{"arrival_ns",0},
    {"logical_bytes",64},{"physical_bytes",128},{"link_bytes",256},{"fail_fraction",0}};
}
void resource_energy_topologies() {
  for(const auto& topology:{"mixed_direct","relay","dash","all_hbf_direct"}) {
    auto config=fixture(topology,std::string(topology)=="all_hbf_direct"?0:4);CpuService s(config.dump());
    const auto path=std::string(topology)=="relay"?"relay":"direct";
    auto a=request("a","hbf0",path);check(s.submit(a.dump())&&!s.submit(a.dump()),"dedup");
    auto b=request("b","hbf0",std::string(topology)=="dash"?"relay":path,"read",1);s.submit(b.dump());
    s.advance_to(250000000);auto r=J::parse(s.report());check(r["done"].size()==2,"two actual completions");
    check(r["done"][1]["start_ns"]==100000000,"shared upstream must serialize DASH and direct");
    near(r["energy_j"]["hbf0_die0"],.2);near(r["energy_j"]["hbf0_die1"],.2);near(r["energy_j"]["hbf0_base"],.2);
    near(r["energy_j"]["gpu"],.1);
    if(std::string(topology)=="relay"||std::string(topology)=="dash") {
      near(r["energy_j"]["hbm0_base"],std::string(topology)=="relay"?.6:.3);
      near(r["energy_j"]["hbm0_die0"],0); // forwarding is NOT an HBM array access
    }
    if(std::string(topology)=="all_hbf_direct") {
      auto g=request("g","gddr");g["arrival_ns"]=250000000;s.submit(g.dump());s.advance_to(400000000);
      r=J::parse(s.report());near(r["external_energy_j"],.7);check(!r["temperature_k"].contains("gddr"),"GDDR must remain external");
    }
    check(r["cohorts"]["hbf0:0"]["program_attempts"]==0,"read counted as P/E");
  }
  CpuService configurable(fixture("mixed_direct",2).dump());configurable.submit(request("2plus6").dump());configurable.advance_to(200000000);
  check(J::parse(configurable.report())["done"].size()==1,"non4+4 unsupported");
  auto bad=fixture();bad["topology"]="unknown";rejects([&]{CpuService s(bad.dump());});
  CpuService s(fixture().dump());rejects([&]{s.submit(request("bad","hbf0","relay").dump());});
}
void maintenance_failures_restart() {
  auto c=fixture();c["hbf_maintenance_period_ns"]=100000000;c["hbm_refresh_period_ns"]=500000000;
  c["fail_maintenance_once"]={{"cohort","hbf0:0"},{"op","program"},{"fraction",.5}};
  CpuService s(c.dump());s.submit(request("front").dump());s.advance_to(150000000);
  auto r=J::parse(s.report());check(r["cohorts"]["hbf0:0"]["commits"]==0,"enqueue or read reset age");
  check(r["cohorts"]["hbf0:0"]["age_ns"]==150000000,"age not advancing");
  const auto snapshot=s.checkpoint();CpuService restored(c.dump());restored.restore(snapshot);
  auto invalid=J::parse(snapshot);invalid["state"]["resources"]=J::object();rejects([&]{restored.restore(invalid.dump());});
  s.advance_to(2000000000);restored.advance_to(2000000000);check(s.report()==restored.report(),"checkpoint continuation differs");
  r=J::parse(s.report());const auto& cohort=r["cohorts"]["hbf0:0"];
  check(cohort["maintenance_failures"]==1&&cohort["commits"].get<unsigned>()>0,"failure/commit accounting");
  check(cohort["program_attempts"].get<unsigned>()==cohort["successful_programs"].get<unsigned>()+1,"actual program accounting");
  check(cohort["erase_attempts"]==0,"refresh intent counted as erase");
  for(const auto& job:r["done"])if(job["status"]=="FAILED")check(job["end_ns"].get<unsigned long long>()-job["start_ns"].get<unsigned long long>()==100000000,"partial failure duration");
  check(r["energy_j"]["hbf0_die0"].get<double>()>0,"maintenance no energy");
  // Explicit failed foreground keeps work and consumes exactly its elapsed energy.
  CpuService f(fixture().dump());auto q=request("failed");q["fail_fraction"]=.25;f.submit(q.dump());f.advance_to(200000000);
  auto fr=J::parse(f.report());near(fr["energy_j"]["hbf0_die0"],.05);check(fr["done"][0]["status"]=="FAILED","failure disappeared");
}
void control_cooling() {
  auto c=fixture();c["policy"]="hysteresis";c["control"]["hbf0"]["light_k"]=300.02;
  c["control"]["hbf0"]["severe_k"]=300.08;c["control"]["hbf0"]["shutdown_k"]=300.14;
  c["control"]["hbf0"]["hysteresis_k"]=.01;
  CpuService s(c.dump());for(unsigned i=0;i<3;++i)s.submit(request("q"+std::to_string(i)).dump());
  s.advance_to(500000000);auto r=J::parse(s.report());check(r["done"].size()==1&&r["queue"].size()==2,"control did not gate or drain");
  bool shutdown=false;for(const auto& row:r["log"])if(row["kind"]=="control"&&row["to"]=="Shutdown")shutdown=true;
  check(shutdown,"shutdown not reached");check(r["control"]["hbf1"]["applied"]=="Normal","control not per stack");
  const auto hot=r["temperature_k"]["hbf0_die0"].get<double>();s.advance_to(10000000000ULL);r=J::parse(s.report());
  check(r["done"].size()==3&&r["queue"].empty(),"idle recovery failed");check(r["temperature_k"]["hbf0_die0"].get<double>()<hot,"idle cooling absent");
  check(r["control"]["hbf0"]["applied"]=="Normal","hysteresis recovery failed");
  c["policy"]="none";CpuService no(c.dump());for(unsigned i=0;i<3;++i)no.submit(request("q"+std::to_string(i)).dump());no.advance_to(500000000);
  check(J::parse(no.report())["done"].size()==3,"no-action control blocked work");
}
int main(){try{resource_energy_topologies();maintenance_failures_restart();control_cooling();std::cout<<"PASS four topology resources/energy; maintenance partial failure/commit/wear; checkpoint; per-stack control/drain/cooling\n";return 0;}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
