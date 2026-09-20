#include <hbfsim/eq3_thermal/cpu_service.hpp>
#include <hbfsim/eq3_thermal/thermal.hpp>
#include <json.hpp>
#include <cmath>
#include <iostream>
#include <functional>
#include <stdexcept>
#include <sstream>
#ifdef EQ3_TEST_DISPATCHER
#include "../../src/host_service/request_dispatcher.hpp"
#include <cstdlib>
#endif
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
void force_control(CpuService& service,const std::string& stack,const std::string& applied,
                   std::uint64_t next_sample=1000000000ULL) {
  auto snapshot=J::parse(service.checkpoint());
  snapshot["state"]["control"][stack]["applied"]=applied;
  snapshot["state"]["control"][stack]["suggested"]=applied;
  snapshot["state"]["control"][stack]["pending"]=nullptr;
  snapshot["state"]["next_sample"]=next_sample;
  service.restore(snapshot.dump());
}
void path_endpoint_admission() {
  // A relay traverses the paired HBM base control domain. Shutdown there must
  // prevent start without reserving any resource or creating forwarding heat.
  CpuService shutdown(fixture("relay").dump());force_control(shutdown,"hbm0","Shutdown");
  shutdown.submit(request("blocked-relay","hbf0","relay").dump());shutdown.advance_to(10000000);
  auto r=J::parse(shutdown.report());
  check(r["active"].empty()&&r["done"].empty()&&r["queue"].size()==1,"relay bypassed paired HBM Shutdown");
  check(r["resources"].empty(),"blocked relay leaked a partial reservation");
  check(r["admission_blocks"].size()==1&&r["admission_blocks"][0]["blocked_endpoints"].size()==1&&
        r["admission_blocks"][0]["blocked_endpoints"][0]["stack"]=="hbm0"&&
        r["admission_blocks"][0]["blocked_endpoints"][0]["reason"]=="SHUTDOWN"&&
        r["admission_blocks"][0]["blocked_endpoints"][0]["retry_at_ns"].is_null(),"Shutdown blocker diagnostics missing");
  near(r["energy_j"]["hbf0_die0"],0);near(r["energy_j"]["hbf0_base"],0);near(r["energy_j"]["hbm0_base"],0);

  // DASH direct does not traverse the paired HBM endpoint and remains legal.
  CpuService dash(fixture("dash").dump());force_control(dash,"hbm0","Shutdown");
  dash.submit(request("blocked-dash-relay","hbf0","relay").dump());
  dash.submit(request("legal-dash-direct","hbf0","direct", "read",1).dump());dash.advance_to(100000000);
  r=J::parse(dash.report());
  check(r["done"].size()==1&&r["done"][0]["id"]=="legal-dash-direct"&&r["queue"].size()==1,
        "unrelated paired endpoint blocked DASH direct or relay escaped");
  near(r["energy_j"]["hbm0_base"],0);

  // A successful foreground relay consumes the Light quota of each unique
  // controlled endpoint, including its paired HBM base domain.
  CpuService light(fixture("relay").dump());force_control(light,"hbf0","Light");force_control(light,"hbm0","Light");
  light.submit(request("light-relay","hbf0","relay").dump());light.advance_to(1);
  r=J::parse(light.report());
  check(r["active"].size()==1,"two-endpoint Light relay was not admitted initially");
  check(r["control"]["hbf0"]["next_admit"]==250000000&&r["control"]["hbm0"]["next_admit"]==250000000,
        "successful relay did not update every endpoint Light quota");
  auto hbm=request("light-hbm","hbm0");hbm["arrival_ns"]=1;light.submit(hbm.dump());light.advance_to(100000000);
  r=J::parse(light.report());check(r["done"].size()==1&&r["queue"].size()==1,"paired Light quota was bypassed");
  light.advance_to(350000000);r=J::parse(light.report());check(r["done"].size()==2&&r["queue"].empty(),"Light retry did not complete uniquely");

  // Joint admission honors the latest quota among traversed endpoints.
  CpuService staggered(fixture("relay").dump());
  auto staggered_snapshot=J::parse(staggered.checkpoint());
  for(const auto* endpoint:{"hbf0","hbm0"}) {
    staggered_snapshot["state"]["control"][endpoint]["applied"]="Light";
    staggered_snapshot["state"]["control"][endpoint]["suggested"]="Light";
  }
  staggered_snapshot["state"]["control"]["hbf0"]["next_admit"]=50000000;
  staggered_snapshot["state"]["control"]["hbm0"]["next_admit"]=150000000;
  staggered_snapshot["state"]["next_sample"]=1000000000ULL;staggered.restore(staggered_snapshot.dump());
  staggered.submit(request("staggered-light","hbf0","relay").dump());staggered.advance_to(100000000);
  r=J::parse(staggered.report());check(r["active"].empty()&&r["queue"].size()==1,"relay ignored the later endpoint Light quota");
  staggered.advance_to(250000000);r=J::parse(staggered.report());
  check(r["done"].size()==1&&r["done"][0]["start_ns"]==150000000,"relay did not retry at the joint Light boundary");

  // The direct and relay DASH routes share the HBF upstream control endpoint.
  CpuService shared(fixture("dash").dump());force_control(shared,"hbf0","Light");
  shared.submit(request("shared-direct","hbf0","direct").dump());shared.submit(request("shared-relay","hbf0","relay", "read",1).dump());
  shared.advance_to(200000000);r=J::parse(shared.report());
  check(r["done"].size()==1&&r["done"][0]["id"]=="shared-direct"&&r["queue"].size()==1,
        "DASH routes bypassed their shared HBF Light quota");
  shared.advance_to(350000000);r=J::parse(shared.report());
  check(r["done"].size()==2&&r["done"][1]["start_ns"]==250000000,"shared endpoint retry was not unique/deterministic");

  // Recovery becomes effective before admission at the same timestamp. A
  // checkpoint taken while blocked must preserve the retry and completion.
  auto recovering_config=fixture("relay");recovering_config["policy"]="hysteresis";
  CpuService recovering(recovering_config.dump());force_control(recovering,"hbm0","Shutdown",0);
  recovering.submit(request("recovering-relay","hbf0","relay").dump());recovering.advance_to(20000000);
  r=J::parse(recovering.report());check(r["queue"].size()==1&&r["active"].empty(),"relay started before endpoint recovery");
  CpuService resumed(recovering_config.dump());resumed.restore(recovering.checkpoint());
  recovering.advance_to(200000000);resumed.advance_to(200000000);check(recovering.report()==resumed.report(),"blocked endpoint checkpoint diverged");
  r=J::parse(recovering.report());check(r["done"].size()==1&&r["done"][0]["start_ns"]==30000000,
        "same-timestamp recovery/admission ordering changed");

  // Control changes never revoke in-flight work; they only gate later starts.
  CpuService draining(fixture("relay").dump());draining.submit(request("inflight","hbf0","relay").dump());draining.advance_to(10000000);
  force_control(draining,"hbm0","Shutdown");auto later=request("later","hbf0","relay");later["arrival_ns"]=10000000;draining.submit(later.dump());
  draining.advance_to(200000000);r=J::parse(draining.report());
  check(r["done"].size()==1&&r["done"][0]["id"]=="inflight"&&r["queue"].size()==1&&r["resources"].empty(),
        "endpoint gate changed drain semantics or leaked resources");

  // Existing maintenance policy permits maintenance through Severe (but not
  // Shutdown); adding endpoint checks must preserve that explicit distinction.
  CpuService maintenance(fixture("relay").dump());
  auto snapshot=J::parse(maintenance.checkpoint());snapshot["state"]["cohorts"]["hbf0:0"]["initial_age_ns"]=10000000000ULL;
  snapshot["state"]["control"]["hbm0"]["applied"]="Severe";snapshot["state"]["control"]["hbm0"]["suggested"]="Severe";
  snapshot["state"]["next_sample"]=1000000000ULL;maintenance.restore(snapshot.dump());maintenance.advance_to(1);
  r=J::parse(maintenance.report());bool found=false;for(const auto& job:r["active"])
    found|=job["maintenance"].get<bool>()&&job["stack"]=="hbf0"&&job["die"]==0;
  check(found,"paired Severe endpoint changed maintenance exemption");
  CpuService light_maintenance(fixture("relay").dump());snapshot=J::parse(light_maintenance.checkpoint());
  snapshot["state"]["cohorts"]["hbf0:0"]["initial_age_ns"]=10000000000ULL;
  snapshot["state"]["control"]["hbm0"]["applied"]="Light";snapshot["state"]["control"]["hbm0"]["suggested"]="Light";
  snapshot["state"]["control"]["hbm0"]["next_admit"]=700000000ULL;snapshot["state"]["next_sample"]=1000000000ULL;
  light_maintenance.restore(snapshot.dump());light_maintenance.advance_to(1);r=J::parse(light_maintenance.report());
  found=false;for(const auto& job:r["active"])found|=job["maintenance"].get<bool>()&&job["stack"]=="hbf0"&&job["die"]==0;
  check(found&&r["control"]["hbm0"]["next_admit"]==700000000ULL,"paired Light endpoint changed maintenance exemption/quota");
  CpuService stopped_maintenance(fixture("relay").dump());snapshot=J::parse(stopped_maintenance.checkpoint());
  snapshot["state"]["cohorts"]["hbf0:0"]["initial_age_ns"]=10000000000ULL;
  snapshot["state"]["control"]["hbm0"]["applied"]="Shutdown";snapshot["state"]["control"]["hbm0"]["suggested"]="Shutdown";
  snapshot["state"]["next_sample"]=1000000000ULL;stopped_maintenance.restore(snapshot.dump());stopped_maintenance.advance_to(1);
  r=J::parse(stopped_maintenance.report());
  found=false;for(const auto& job:r["queue"])found|=job["maintenance"].get<bool>()&&job["stack"]=="hbf0"&&job["die"]==0;
  check(found&&r["active"].empty()&&r["resources"].empty(),"paired Shutdown endpoint admitted maintenance or leaked resources");
}
void control_pending_contract() {
  auto c=fixture();c["policy"]="hysteresis";
  CpuService cancel(c.dump());auto snapshot=J::parse(cancel.checkpoint());
  snapshot["state"]["control"]["hbf0"]["pending"]={{"state","Light"},{"at_ns",20000000}};
  cancel.restore(snapshot.dump());cancel.advance_to(1);auto r=J::parse(cancel.report());
  check(r["control"]["hbf0"]["applied"]=="Normal"&&r["control"]["hbf0"]["pending"].is_null(),
        "desired==applied did not cancel a stale pending action");

  auto hot=fixture();hot["policy"]="hysteresis";
  hot["control"]["hbf0"]["light_k"]=299.0;hot["control"]["hbf0"]["severe_k"]=299.1;hot["control"]["hbf0"]["shutdown_k"]=299.2;
  CpuService escalate(hot.dump());snapshot=J::parse(escalate.checkpoint());
  snapshot["state"]["control"]["hbf0"]["pending"]={{"state","Light"},{"at_ns",20000000}};
  escalate.restore(snapshot.dump());escalate.advance_to(1);r=J::parse(escalate.report());
  check(r["control"]["hbf0"]["pending"]["state"]=="Shutdown"&&r["control"]["hbf0"]["pending"]["at_ns"]==30000000,
        "more severe sample did not replace stale pending action");
  escalate.advance_to(30000001);r=J::parse(escalate.report());unsigned transitions=0;
  for(const auto& row:r["log"])if(row["kind"]=="control"&&row["stack"]=="hbf0") {
    ++transitions;check(row["from"]=="Normal"&&row["to"]=="Shutdown"&&row["time_ns"]==30000000,
                        "sample/action timestamp ordering changed");
  }
  check(transitions==1,"stale pending action executed before escalation");
}
void escalation_priority_v2_contract() {
  auto hot=[](const std::string& policy) {
    auto c=fixture();c["policy"]=policy;c["control"]["hbf0"]["light_k"]=299.0;
    c["control"]["hbf0"]["severe_k"]=299.1;c["control"]["hbf0"]["shutdown_k"]=299.2;
    c["control"]["hbf0"]["action_delay_ns"]=20000000;c["control"]["hbf0"]["min_dwell_ns"]=100000000;
    return c;
  };
  auto force_applied=[](CpuService& service,const std::string& applied,J pending=nullptr) {
    auto snapshot=J::parse(service.checkpoint());auto& state=snapshot["state"]["control"]["hbf0"];
    state["applied"]=applied;state["suggested"]=applied;state["last_change"]=0;state["pending"]=pending;
    snapshot["state"]["next_sample"]=0;service.restore(snapshot.dump());
  };

  CpuService legacy(hot("hysteresis").dump());force_applied(legacy,"Light");legacy.advance_to(1);
  auto r=J::parse(legacy.report());
  check(r["control"]["hbf0"]["pending"]["state"]=="Shutdown"&&r["control"]["hbf0"]["pending"]["at_ns"]==100000000,
        "legacy hysteresis dwell contract changed");

  const auto v2_config=hot("hysteresis_escalation_priority_v2");CpuService priority(v2_config.dump());
  force_applied(priority,"Light");priority.advance_to(10000001);r=J::parse(priority.report());
  check(r["control"]["hbf0"]["pending"]["state"]=="Shutdown"&&r["control"]["hbf0"]["pending"]["at_ns"]==20000000,
        "v2 escalation was delayed by recovery dwell or same-target resampling");
  CpuService resumed(v2_config.dump());resumed.restore(priority.checkpoint());
  priority.advance_to(20000001);resumed.advance_to(20000001);check(priority.report()==resumed.report(),"v2 pending checkpoint diverged");
  r=J::parse(priority.report());check(r["control"]["hbf0"]["applied"]=="Shutdown","v2 escalation omitted action delay boundary");

  auto cool=fixture();cool["policy"]="hysteresis_escalation_priority_v2";
  cool["control"]["hbf0"]["action_delay_ns"]=20000000;cool["control"]["hbf0"]["min_dwell_ns"]=100000000;
  CpuService recovery(cool.dump());force_applied(recovery,"Shutdown");recovery.advance_to(1);r=J::parse(recovery.report());
  check(r["control"]["hbf0"]["pending"]["state"]=="Normal"&&r["control"]["hbf0"]["pending"]["at_ns"]==100000000,
        "v2 recovery bypassed hysteresis dwell");

  auto held=cool;held["control"]["hbf0"]["light_k"]=299.0;held["control"]["hbf0"]["severe_k"]=299.1;
  held["control"]["hbf0"]["shutdown_k"]=300.02;held["control"]["hbf0"]["hysteresis_k"]=.05;
  CpuService hysteresis(held.dump());force_applied(hysteresis,"Shutdown");hysteresis.advance_to(1);r=J::parse(hysteresis.report());
  check(r["control"]["hbf0"]["applied"]=="Shutdown"&&r["control"]["hbf0"]["pending"].is_null(),"v2 recovery ignored hysteresis");

  CpuService replace(v2_config.dump());
  force_applied(replace,"Severe",J{{"state","Normal"},{"at_ns",100000000}});replace.advance_to(1);r=J::parse(replace.report());
  check(r["control"]["hbf0"]["pending"]["state"]=="Shutdown"&&r["control"]["hbf0"]["pending"]["at_ns"]==20000000,
        "v2 severe recommendation did not replace stale recovery");

  auto light=fixture();light["policy"]="hysteresis_escalation_priority_v2";light["control"]["hbf0"]["light_k"]=299.0;
  light["control"]["hbf0"]["severe_k"]=310.0;light["control"]["hbf0"]["shutdown_k"]=320.0;
  CpuService retarget(light.dump());force_applied(retarget,"Normal",J{{"state","Shutdown"},{"at_ns",20000000}});
  retarget.advance_to(1);r=J::parse(retarget.report());
  check(r["control"]["hbf0"]["pending"]["state"]=="Light","v2 retained an obsolete stronger pending target");

  CpuService ordered(v2_config.dump());force_applied(ordered,"Light");auto q=request("v2-boundary");q["arrival_ns"]=20000000;
  ordered.submit(q.dump());ordered.advance_to(20000001);r=J::parse(ordered.report());
  check(r["control"]["hbf0"]["applied"]=="Shutdown"&&r["queue"].size()==1&&r["active"].empty()&&
        r["admission_blocks"][0]["blocked_endpoints"][0]["reason"]=="SHUTDOWN",
        "v2 same-timestamp sample/apply/admission ordering changed");
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
    check(r["balance"]["relative_residual"].get<double>()<.001,"discrete energy conservation");
    near(r["balance"]["max_component_mapping_error_j"],0);
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
  CpuService erase(fixture().dump());erase.submit(request("erase","hbf0","direct","erase").dump());erase.advance_to(400000000);
  auto er=J::parse(erase.report());check(er["cohorts"]["hbf0:0"]["erase_attempts"]==1&&er["cohorts"]["hbf0:0"]["successful_erases"]==1,"explicit erase accounting");
  near(er["energy_j"]["hbf0_die0"],1.5);
  CpuService relay(fixture("relay").dump());relay.submit(request("relay","hbf0","relay").dump());relay.submit(request("hbm","hbm0").dump());relay.advance_to(300000000);
  auto rr=J::parse(relay.report());check(rr["done"][1]["start_ns"]==100000000,"HBM direct failed to share relay base/link");
  near(rr["energy_j"]["hbm0_die0"],.2);
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
  unsigned inflight_programs=0;for(const auto& job:r["active"])if(job["stack"]=="hbf0"&&job["die"]==0&&job["op"]=="program")++inflight_programs;
  check(cohort["program_attempts"].get<unsigned>()==cohort["successful_programs"].get<unsigned>()+1+inflight_programs,"actual program accounting including inflight");
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
  bool light=false,severe=false,shutdown=false;std::uint64_t previous=0;
  for(const auto& row:r["log"])if(row["kind"]=="control") {
    const auto at=row["time_ns"].get<std::uint64_t>();check(at>=20000000,"action delay ignored");
    if(previous)check(at-previous>=30000000,"minimum dwell ignored");previous=at;
    light|=row["to"]=="Light";severe|=row["to"]=="Severe";shutdown|=row["to"]=="Shutdown";
  }
  check(light&&severe&&shutdown,"not all heating states reached");
  check(r["done"][0]["end_ns"]==100000000,"inflight was lost or retroactively delayed");
  check(r["control"]["hbf1"]["applied"]=="Normal","control not per stack");
  const auto hot=r["temperature_k"]["hbf0_die0"].get<double>();s.advance_to(10000000000ULL);r=J::parse(s.report());
  check(r["done"].size()==3&&r["queue"].empty(),"idle recovery failed");check(r["temperature_k"]["hbf0_die0"].get<double>()<hot,"idle cooling absent");
  check(r["control"]["hbf0"]["applied"]=="Normal","hysteresis recovery failed");
  c["policy"]="none";CpuService no(c.dump());for(unsigned i=0;i<3;++i)no.submit(request("q"+std::to_string(i)).dump());no.advance_to(500000000);
  check(J::parse(no.report())["done"].size()==3,"no-action control blocked work");
}
void actual_dispatcher() {
#ifdef EQ3_TEST_DISPATCHER
  using namespace hbfsim::host_service;
  void* storage=nullptr;const auto bytes=control_region_bytes(8);check(posix_memalign(&storage,64,bytes)==0,"control allocation");
  std::unique_ptr<void,decltype(&std::free)> owned(storage,&std::free);ControlView view(storage,bytes);check(view.initialize(8),"control initialization");
  CpuService service(fixture().dump());hbfsim::HbfRequest original{};original.request_id=1001;original.bytes=64;
  original.operation=static_cast<std::uint32_t>(hbfsim::RequestOperation::Read);original.page_generation=9;
  std::uint64_t ticket;check(view.try_push_request(original,ticket),"dispatcher enqueue");
  hbfsim::HbfRequest submitted{};
  RequestDispatcher dispatcher(view,{
    .prepare=[](const hbfsim::HbfRequest& r){PreparedDispatch p;p.completion.request_id=r.request_id;p.completion.page_generation=r.page_generation;
      p.completion.status=static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready);p.media_actions[0]=r;p.media_action_count=1;return p;},
    .submit=[&](const hbfsim::HbfRequest& r){submitted=r;auto q=request(std::to_string(r.request_id));q["arrival_ns"]=r.arrival_ns;service.submit(q.dump());},
    .run_next_completion=[&]()->std::optional<hbfsim::HbfCompletion>{
      service.advance_to(100000000);const auto r=J::parse(service.report());check(r["done"].size()==1,"dispatcher not consuming actual service");
      hbfsim::HbfCompletion c{};c.request_id=submitted.request_id;c.page_generation=submitted.page_generation;
      c.modeled_completion_ns=r["done"][0]["end_ns"];c.modeled_ns=c.modeled_completion_ns;c.status=static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready);return c;}});
  check(dispatcher.poll_once(),"actual dispatcher no progress");hbfsim::HbfCompletion completion{};
  check(view.try_consume_completion(ticket,completion),"actual shared completion not published");
  check(completion.request_id==1001&&completion.modeled_ns==100000000,"dispatcher ID/time semantics");
  std::cout<<"PASS actual RequestDispatcher Engine and shared completion consumer (CPU fixture, not live GPU)\n";
#endif
}
int main(){try{path_endpoint_admission();control_pending_contract();escalation_priority_v2_contract();resource_energy_topologies();maintenance_failures_restart();control_cooling();actual_dispatcher();std::cout<<"PASS path endpoint admission/Light quotas; legacy/v2 pending control contracts; four topology resources/energy; maintenance partial failure/commit/wear; checkpoint; per-stack control/drain/cooling\n";return 0;}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
