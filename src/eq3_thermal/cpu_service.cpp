#include <hbfsim/eq3_thermal/cpu_service.hpp>
#include <hbfsim/eq3_thermal/observer.hpp>
#include <json.hpp>
#include <algorithm>
#include <cmath>
#include <limits>
#include <set>
#include <stdexcept>

namespace hbfsim::eq3_thermal {
namespace {
using J=nlohmann::json;
using Tick=std::uint64_t;
void need(bool b,const char* s){if(!b)throw std::invalid_argument(s);}
Tick tick(const J& j){need(j.is_number_integer()&&!j.is_boolean(),"clock must be integer ns");need(j>=0,"negative clock");return j.get<Tick>();}
double number(const J& j){need(j.is_number()&&!j.is_boolean(),"numeric parameter required");double x=j.get<double>();need(std::isfinite(x)&&x>=0,"invalid finite nonnegative parameter");return x;}
int severity(const std::string& s){if(s=="Normal")return 0;if(s=="Light")return 1;if(s=="Severe")return 2;if(s=="Shutdown")return 3;throw std::invalid_argument("unknown control state");}
std::string level(int n){return std::vector<std::string>{"Normal","Light","Severe","Shutdown"}.at(n);}
std::string cohort_id(const std::string& s,std::size_t d){return s+":"+std::to_string(d);}
J event_json(const ObservedEvent& e){return J{{"event_id",e.event_id},{"operation_id",e.operation_id},{"request_id",e.request_id},{"phase",static_cast<int>(e.phase)},
 {"time_ns",e.time_ns},{"operation",e.operation},{"source",e.source},{"physical_type",e.physical_type},{"stack_id",e.stack_id},
 {"die",e.die?J(*e.die):J(nullptr)},{"plane",e.plane?J(*e.plane):J(nullptr)},
 {"logical_bytes",e.logical_bytes},{"physical_bytes",e.physical_bytes?J(*e.physical_bytes):J(nullptr)},
 {"link_bytes",e.link_bytes?J(*e.link_bytes):J(nullptr)},{"resources",e.resources},{"power",e.component_power_w},
 {"external_power_w",e.external_power_w},{"evidence",e.evidence},{"outcome",e.outcome}};}
ObservedEvent json_event(const J& j){ObservedEvent e;e.event_id=j.at("event_id");e.operation_id=j.at("operation_id");e.request_id=j.at("request_id");e.phase=static_cast<Phase>(j.at("phase").get<int>());
 e.time_ns=tick(j.at("time_ns"));e.operation=j.at("operation");e.source=j.at("source");e.physical_type=j.at("physical_type");e.stack_id=j.at("stack_id");
 if(!j.at("die").is_null())e.die=j.at("die").get<std::size_t>();
 if(!j.at("plane").is_null())e.plane=j.at("plane").get<std::size_t>();
 e.logical_bytes=tick(j.at("logical_bytes"));if(!j.at("physical_bytes").is_null())e.physical_bytes=tick(j.at("physical_bytes"));if(!j.at("link_bytes").is_null())e.link_bytes=tick(j.at("link_bytes"));
 e.resources=j.at("resources").get<std::vector<std::string>>();e.component_power_w=j.at("power").get<std::map<std::string,double>>();e.external_power_w=number(j.at("external_power_w"));e.evidence=j.at("evidence");e.outcome=j.at("outcome");return e;}
J observer_json(const ObserverCheckpoint& s){J j={{"mode",static_cast<int>(s.mode)},{"time_ns",s.time_ns},{"next_energy_id",s.next_energy_id},{"energy_j",s.energy_j},{"external_energy_j",s.external_energy_j}};
 for(const auto& [name,list]:std::vector<std::pair<std::string,const std::vector<ObservedEvent>*>>{{"pending",&s.pending},{"journal",&s.journal},{"terminals",&s.terminals}}){j[name]=J::array();for(const auto& e:*list)j[name].push_back(event_json(e));}
 for(const auto& [name,map]:std::vector<std::pair<std::string,const std::map<std::string,ObservedEvent>*>>{{"seen",&s.seen},{"active",&s.active}}){j[name]=J::object();for(const auto& [id,e]:*map)j[name][id]=event_json(e);}
 j["thermal"]=s.thermal?J(checkpoint_to_text(*s.thermal)):J(nullptr);return j;}
ObserverCheckpoint json_observer(const J& j){ObserverCheckpoint s;s.mode=static_cast<RuntimeMode>(j.at("mode").get<int>());s.time_ns=tick(j.at("time_ns"));s.next_energy_id=tick(j.at("next_energy_id"));s.energy_j=j.at("energy_j").get<std::map<std::string,double>>();s.external_energy_j=number(j.at("external_energy_j"));
 for(const auto& e:j.at("pending"))s.pending.push_back(json_event(e));
 for(const auto& e:j.at("journal"))s.journal.push_back(json_event(e));
 for(const auto& e:j.at("terminals"))s.terminals.push_back(json_event(e));
 for(const auto& [id,e]:j.at("seen").items())s.seen[id]=json_event(e);
 for(const auto& [id,e]:j.at("active").items())s.active[id]=json_event(e);
 if(!j.at("thermal").is_null())s.thermal=checkpoint_from_text(j.at("thermal").get<std::string>());
 return s;}
}

struct CpuService::Impl {
  J config,state;
  ThermalModelConfig thermal;
  ActivityObserver observer;
  std::map<std::string,J> stacks;
  std::map<std::string,std::size_t> nodes;
  explicit Impl(const std::string& text):config(J::parse(text)),
    thermal(model_config_from_text(config.at("thermal_model_text").get<std::string>())),
    observer(RuntimeMode::Shadow,thermal) {
    need(config.at("evidence")=="ENGINEERING_FIXTURE","physical service parameters not authorized");
    const std::string layout=config.at("topology");need(layout=="mixed_direct"||layout=="relay"||layout=="dash"||layout=="all_hbf_direct","unknown topology");
    need(config.at("policy")=="none"||config.at("policy")=="hysteresis","unsupported policy");
    need(tick(config.at("thermal_step_ns"))>0&&tick(config.at("sample_ns"))>0,"positive clocks required");
    for(const auto& [key,value]:config.at("duration_ns").items())need(tick(value)>0,"zero service duration");
    for(const auto& [key,value]:config.at("power_w").items())(void)number(value);
    for(const auto* op:{"read","program","erase","write","dram_refresh"})(void)tick(config.at("duration_ns").at(op));
    for(const auto* key:{"read_array","program_array","erase_array","write_array","refresh_array","base","relay_base","gpu_phy","gddr","gpu_external"})(void)number(config.at("power_w").at(key));
    need(tick(config.at("hbf_maintenance_period_ns"))>0&&tick(config.at("hbm_refresh_period_ns"))>0,"explicit distinct maintenance periods required");
    need(tick(config.at("page_bytes"))>0,"maintenance page bytes required");
    for(std::size_t i=0;i<thermal.nodes.size();++i)nodes[thermal.nodes[i].id]=i;
    need(nodes.contains("gpu"),"GPU component missing");
    state={{"now",0},{"next_sample",0},{"sequence",1},{"maintenance_sequence",1},
      {"queue",J::array()},{"active",J::array()},{"done",J::array()},{"log",J::array()},
      {"samples",J::array()},{"requests",J::object()},{"resources",J::object()},
      {"cohorts",J::object()},{"control",J::object()}};
    std::size_t hbms=0,hbfs=0;
    for(const auto& stack:config.at("stacks")) {
      const std::string id=stack.at("id"),kind=stack.at("physical_kind");
      need(!id.empty()&&stacks.emplace(id,stack).second,"duplicate stack identity");
      need(kind=="HBM4"||kind=="HBF","stack physical type invalid");
      if(kind=="HBM4")++hbms;else ++hbfs;
      const auto dies=tick(stack.at("die_count"));need(dies>0,"stack without array dies");
      need(nodes.contains(id+"_base"),"base missing from actual thermal model");
      const auto& ctl=config.at("control").at(id);
      const double light=number(ctl.at("light_k")),severe=number(ctl.at("severe_k")),shutdown=number(ctl.at("shutdown_k"));
      need(light<severe&&severe<shutdown&&number(ctl.at("hysteresis_k"))>0,"invalid explicit fixture thresholds");
      (void)tick(ctl.at("action_delay_ns"));(void)tick(ctl.at("min_dwell_ns"));need(tick(ctl.at("light_gap_ns"))>0,"light must affect admission");
      state["control"][id]={{"applied","Normal"},{"suggested","Normal"},{"last_change",0},{"next_admit",0},{"pending",nullptr}};
      for(std::size_t d=0;d<dies;++d) {
        need(nodes.contains(id+"_die"+std::to_string(d)),"array die mapping missing");
        state["cohorts"][cohort_id(id,d)]={{"stack",id},{"die",d},{"initial_age_ns",tick(config.at("initial_age_ns"))},{"last_commit_ns",0},{"pending",false},{"old_data_valid",true},
          {"commits",0},{"program_attempts",0},{"successful_programs",0},{"erase_attempts",0},{"successful_erases",0},{"maintenance_failures",0}};
      }
    }
    need(hbms+hbfs==8,"exactly eight stack slots required");
    if(layout=="all_hbf_direct")need(hbfs==8&&config.at("external_fast_memory")=="GDDR"&&!nodes.contains("gddr"),"external physical GDDR must stay outside package");
    else need(hbms>0&&hbfs>0,"mixed family needs both device types");
    if(layout=="relay"||layout=="dash") {
      need(hbms==4&&hbfs==4&&config.at("custom_base_die_relay")==true,"relay requires explicit four-pair custom base fixture");
      std::set<std::string> paired;
      for(const auto& [id,stack]:stacks)if(stack.at("physical_kind")=="HBF") {
        const std::string pair=stack.at("pair");need(stacks.contains(pair)&&stacks.at(pair).at("physical_kind")=="HBM4"&&paired.insert(pair).second,"invalid unique relay pairing");
      }
    }
    ObservedEvent external;external.event_id="gpu-external-start";external.operation_id="gpu-external";external.request_id="gpu-external";
    external.phase=Phase::Start;external.operation="external_heat";external.source="external";external.physical_type="GPU";external.stack_id="gpu";
    external.component_power_w={{"gpu",number(config.at("power_w").at("gpu_external"))}};external.evidence="ENGINEERING_FIXTURE";observer.submit(external);
  }
  Tick now()const{return tick(state.at("now"));}
  std::string kind(const std::string& id)const{return id=="gddr"?"GDDR":stacks.at(id).at("physical_kind").get<std::string>();}
  J route(const J& job)const {
    const std::string stack=job.at("stack"),op=job.at("op"),path=job.at("route");
    const std::string physical=kind(stack);const auto die=tick(job.at("die"));
    J result={{"resources",J::array()},{"power",J::object()},{"external_power_w",0.0},{"link_hops",1}};
    auto add=[&](const std::string& r){result["resources"].push_back(r);};
    const auto& power=config.at("power_w");
    if(physical=="GDDR") {
      need(config.at("topology")=="all_hbf_direct"&&path=="direct"&&(op=="read"||op=="write"),"invalid external GDDR operation");
      add("gddr:service");add("gddr:link");result["external_power_w"]=power.at("gddr");return result;
    }
    need(die<tick(stacks.at(stack).at("die_count")),"invalid explicit die mapping");
    need(op=="read"||op=="write"||op=="program"||op=="erase"||op=="dram_refresh","unknown operation");
    need(physical=="HBF"?(op=="read"||op=="program"||op=="erase"):(op=="read"||op=="write"||op=="dram_refresh"),"NAND and DRAM maintenance operations conflated");
    add(stack+":die:"+std::to_string(die));add(stack+":base");
    const std::string key=op=="dram_refresh"?"refresh_array":op+"_array";
    result["power"][stack+"_die"+std::to_string(die)]=power.at(key);result["power"][stack+"_base"]=power.at("base");
    result["power"]["gpu"]=power.at("gpu_phy");
    if(physical=="HBM4") {need(path=="direct","HBM endpoints use direct path");add(stack+":gpu-link");}
    else {
      add(stack+":upstream"); // ONE upstream NAND/TSV supply for both DASH paths
      const std::string topology=config.at("topology");
      if(path=="direct") {need(topology!="relay","relay has no direct HBF path");add(stack+":gpu-link");}
      else if(path=="relay") {
        need(topology=="relay"||topology=="dash","no relay path declared");
        const std::string pair=stacks.at(stack).at("pair");
        add(stack+":relay-link");add(pair+":base");add(pair+":gpu-link");
        result["power"][pair+"_base"]=power.at("relay_base");result["link_hops"]=2;
      } else throw std::invalid_argument("unknown route; no fallback");
    }
    return result;
  }
  void log(const std::string& type,const J& details){J row=details;row["time_ns"]=now();row["kind"]=type;state["log"].push_back(row);}
  ObservedEvent activity(const J& job,Phase phase)const {
    ObservedEvent e;e.operation_id=job.at("id");e.request_id=job.at("request_id");e.event_id=e.operation_id+":"+std::to_string(static_cast<int>(phase));
    e.time_ns=now();e.phase=phase;e.operation=job.at("op");e.source=job.at("maintenance").get<bool>()?"maintenance":"demand";
    e.stack_id=job.at("stack");e.physical_type=kind(e.stack_id);e.die=tick(job.at("die"));
    e.logical_bytes=tick(job.at("logical_bytes"));e.physical_bytes=tick(job.at("physical_bytes"));
    const auto mapping=route(job);e.link_bytes=tick(job.at("link_bytes"))*tick(mapping.at("link_hops"));
    e.resources=mapping.at("resources").get<std::vector<std::string>>();e.component_power_w=mapping.at("power").get<std::map<std::string,double>>();
    e.external_power_w=number(mapping.at("external_power_w"));e.evidence="ENGINEERING_FIXTURE";return e;
  }
  void enqueue(J job) {
    job["sequence"]=state["sequence"];state["sequence"]=tick(state["sequence"])+1;
    (void)route(job);state["queue"].push_back(job);
    log("enqueue",{{"id",job.at("id")},{"maintenance",job.at("maintenance")},{"arrival_ns",job.at("arrival_ns")}});
  }
  bool submit(const J& request) {
    const std::string id=request.at("id");need(!id.empty(),"missing request id");
    if(state["requests"].contains(id)){need(state["requests"][id]==request,"conflicting request replay");return false;}
    need(tick(request.at("arrival_ns"))>=now(),"request submitted in committed past");
    const std::string stack=request.at("stack");need(stack=="gddr"||stacks.contains(stack),"unknown stack");
    J job=request;job["request_id"]=id;job["maintenance"]=false;job["cohort"]=nullptr;
    (void)tick(job.at("logical_bytes"));(void)tick(job.at("physical_bytes"));(void)tick(job.at("link_bytes"));
    const double fraction=number(job.at("fail_fraction"));need(fraction<=1,"invalid failure injection fraction");
    (void)route(job);state["requests"][id]=request;enqueue(job);
    auto arrival=activity(job,Phase::Arrival);arrival.time_ns=tick(job.at("arrival_ns"));observer.submit(arrival);return true;
  }
  Tick age(const J& c)const{return now()-tick(c.at("last_commit_ns"))+(tick(c.at("commits"))?0:tick(c.at("initial_age_ns")));}
  Tick period(const J& c)const{return tick(config.at(kind(c.at("stack"))=="HBF"?"hbf_maintenance_period_ns":"hbm_refresh_period_ns"));}
  void maintenance_job(const std::string& key,const std::string& op) {
    auto& c=state["cohorts"][key];const std::string stack=c.at("stack");
    const auto serial=tick(state["maintenance_sequence"]);state["maintenance_sequence"]=serial+1;
    const std::string id="maintenance:"+std::to_string(serial);
    const auto bytes=tick(config.at("page_bytes"));
    J job={{"id",id},{"request_id",id},{"stack",stack},{"die",c.at("die")},{"op",op},
      {"route",kind(stack)=="HBF"&&config.at("topology")=="relay"?"relay":"direct"},
      {"arrival_ns",now()},{"logical_bytes",bytes},{"physical_bytes",bytes},{"link_bytes",bytes},
      {"fail_fraction",0.0},{"maintenance",true},{"cohort",key}};
    if(config.contains("fail_maintenance_once")&&!state.value("failure_injected",false)) {
      const auto& f=config.at("fail_maintenance_once");
      if(f.at("cohort")==key&&f.at("op")==op) {
        const auto fraction=number(f.at("fraction"));need(fraction>0&&fraction<=1,"invalid maintenance failure fraction");
        job["fail_fraction"]=fraction;state["failure_injected"]=true;
      }
    }
    c["pending"]=true;enqueue(job);observer.submit(activity(job,Phase::Arrival));
  }
  void complete_due() {
    J remaining=J::array();
    for(auto job:state["active"]) {
      if(tick(job.at("end_ns"))!=now()){remaining.push_back(job);continue;}
      const bool failed=number(job.at("fail_fraction"))>0;
      for(const auto& r:job.at("resources"))state["resources"].erase(r.get<std::string>());
      if(failed){auto e=activity(job,Phase::Fail);e.outcome="INJECTED_PARTIAL_FAILURE";observer.submit(e);}
      else {observer.submit(activity(job,Phase::End));observer.submit(activity(job,Phase::Complete));}
      job["status"]=failed?"FAILED":"COMPLETE";state["done"].push_back(job);
      log(failed?"failure":"complete",{{"id",job.at("id")},{"start_ns",job.at("start_ns")},{"end_ns",now()}});
      const std::string stack=job.at("stack"),op=job.at("op");
      if(stack!="gddr") {
        auto& c=state["cohorts"][cohort_id(stack,tick(job.at("die")))];
        if(!failed&&(op=="program"||op=="erase")) {const auto k=op=="program"?"successful_programs":"successful_erases";c[k]=tick(c.at(k))+1;}
        if(job.at("maintenance").get<bool>()) {
          const std::string key=job.at("cohort");
          if(failed){c["pending"]=false;c["maintenance_failures"]=tick(c.at("maintenance_failures"))+1;c["retry_after_ns"]=now()+tick(config.at("sample_ns"));}
          else if(op=="read")maintenance_job(key,"program");
          else {c["pending"]=false;c["last_commit_ns"]=now();c["commits"]=tick(c.at("commits"))+1;log("maintenance_commit",{{"cohort",key}});}
        }
      }
    }
    state["active"]=std::move(remaining);
  }
  void maintenance_due() {
    for(auto& [key,c]:state["cohorts"].items())
      if(!c.at("pending").get<bool>()&&age(c)>=period(c)&&now()>=c.value("retry_after_ns",Tick{0}))
        maintenance_job(key,kind(c.at("stack"))=="HBF"?"read":"dram_refresh");
  }
  double stack_temperature(const std::string& stack)const {
    double t=0;for(const auto& [id,index]:nodes)if(id.starts_with(stack+"_"))t=std::max(t,observer.model()->temperatures_k()[index]);return t;
  }
  void control_sample() {
    if(now()!=tick(state.at("next_sample")))return;
    for(auto& [id,c]:state["control"].items()) {
      const auto& p=config.at("control").at(id);const double t=stack_temperature(id);
      const int applied=severity(c.at("applied"));int desired=0;
      for(const auto* key:{"light_k","severe_k","shutdown_k"})if(t>=number(p.at(key)))++desired;
      if(desired<applied) {
        const auto key=std::vector<std::string>{"","light_k","severe_k","shutdown_k"}.at(applied);
        if(t>=number(p.at(key))-number(p.at("hysteresis_k")))desired=applied;
      }
      c["suggested"]=level(desired);
      state["samples"].push_back({{"time_ns",now()},{"stack",id},{"temperature_k",t},{"source","SIMULATED"},{"suggested",level(desired)},{"applied",c.at("applied")}});
      if(config.at("policy")=="none")continue;
      if(desired==applied)c["pending"]=nullptr;
      else if(c.at("pending").is_null()||c.at("pending").at("state")!=level(desired)) {
        c["pending"]={{"state",level(desired)},{"at_ns",std::max(now()+tick(p.at("action_delay_ns")),tick(c.at("last_change"))+tick(p.at("min_dwell_ns")))}};
      }
    }
    state["next_sample"]=now()+tick(config.at("sample_ns"));
  }
  void apply_controls() {
    for(auto& [id,c]:state["control"].items())if(!c.at("pending").is_null()&&tick(c.at("pending").at("at_ns"))<=now()) {
      log("control",{{"stack",id},{"from",c.at("applied")},{"to",c.at("pending").at("state")},{"reason","SIMULATED_STACK_HOTSPOT_HYSTERESIS"}});
      c["applied"]=c.at("pending").at("state");c["last_change"]=now();c["pending"]=nullptr;
    }
  }
  void admit() {
    J waiting=J::array();
    for(auto job:state["queue"]) {
      bool blocked=tick(job.at("arrival_ns"))>now();const std::string stack=job.at("stack");
      if(stack!="gddr") {
        const auto& c=state.at("control").at(stack);const int s=severity(c.at("applied"));
        blocked|=s==3||(!job.at("maintenance").get<bool>()&&(s>=2||(s==1&&now()<tick(c.at("next_admit")))));
      }
      const auto mapping=route(job);
      for(const auto& r:mapping.at("resources"))blocked|=state["resources"].contains(r.get<std::string>());
      if(blocked){waiting.push_back(job);continue;}
      const auto duration=tick(config.at("duration_ns").at(job.at("op").get<std::string>()));
      const auto fraction=number(job.at("fail_fraction"));
      const auto actual=fraction>0?std::max(Tick{1},static_cast<Tick>(std::ceil(duration*fraction))):duration;
      need(now()<=std::numeric_limits<Tick>::max()-actual,"service clock overflow");
      job["start_ns"]=now();job["end_ns"]=now()+actual;job["resources"]=mapping.at("resources");
      for(const auto& r:job.at("resources"))state["resources"][r.get<std::string>()]=job.at("id");
      if(stack!="gddr") {
        const std::string op=job.at("op");auto& cohort=state["cohorts"][cohort_id(stack,tick(job.at("die")))];
        if(op=="program"||op=="erase"){const auto key=op=="program"?"program_attempts":"erase_attempts";cohort[key]=tick(cohort.at(key))+1;}
        auto& c=state["control"][stack];if(!job.at("maintenance").get<bool>()&&c.at("applied")=="Light")c["next_admit"]=now()+tick(config.at("control").at(stack).at("light_gap_ns"));
      }
      observer.submit(activity(job,Phase::Issue));observer.submit(activity(job,Phase::Start));state["active"].push_back(job);
      log("start",{{"id",job.at("id")},{"resources",job.at("resources")},{"end_ns",job.at("end_ns")}});
    }
    state["queue"]=std::move(waiting);
  }
  void advance(Tick target) {
    need(target>=now(),"service clock backwards");
    while(now()<target) {
      complete_due();control_sample();apply_controls();maintenance_due();admit();observer.advance_to(now());
      Tick next=std::min(target,now()+tick(config.at("thermal_step_ns")));
      auto consider=[&](Tick t){if(t>now())next=std::min(next,t);};
      consider(tick(state.at("next_sample")));
      for(const auto& j:state["active"])consider(tick(j.at("end_ns")));
      for(const auto& j:state["queue"])consider(tick(j.at("arrival_ns")));
      for(const auto& [id,c]:state["control"].items()){if(!c.at("pending").is_null())consider(tick(c.at("pending").at("at_ns")));consider(tick(c.at("next_admit")));}
      for(const auto& [id,c]:state["cohorts"].items())if(!c.at("pending").get<bool>()) {
        if(age(c)<period(c))consider(now()+period(c)-age(c));else consider(c.value("retry_after_ns",Tick{0}));
      }
      observer.advance_to(next);state["now"]=next;
    }
    complete_due();observer.advance_to(now()); // complete boundaries, no new admission beyond horizon
  }
  J report()const {
    J result=state;result["evidence"]="ENGINEERING_FIXTURE";result["topology"]=config.at("topology");result["policy"]=config.at("policy");
    result["temperature_source"]="SIMULATED";result["token_throughput"]="UNAVAILABLE";
    result["energy_j"]=observer.energy_j();result["external_energy_j"]=observer.external_energy_j();
    result["external_gddr_temperature"]="UNAVAILABLE_OUTSIDE_PACKAGE";
    result["temperature_k"]=J::object();for(const auto& [id,i]:nodes)result["temperature_k"][id]=observer.model()->temperatures_k()[i];
    for(auto& [id,c]:result["cohorts"].items())c["age_ns"]=age(c);
    return result;
  }
};

CpuService::CpuService(const std::string& text):impl_(std::make_unique<Impl>(text)){}
CpuService::~CpuService()=default;
CpuService::CpuService(CpuService&&) noexcept=default;
CpuService& CpuService::operator=(CpuService&&) noexcept=default;
bool CpuService::submit(const std::string& text){return impl_->submit(J::parse(text));}
void CpuService::advance_to(std::uint64_t t){impl_->advance(t);}
std::string CpuService::report()const{return impl_->report().dump(2);}
std::string CpuService::checkpoint()const{return J{{"version",1},{"config",impl_->config},{"state",impl_->state},{"observer",observer_json(impl_->observer.checkpoint())}}.dump();}
void CpuService::restore(const std::string& text) {
  const auto j=J::parse(text);need(j.at("version")==1&&j.at("config")==impl_->config,"checkpoint configuration mismatch");
  auto restored=std::make_unique<Impl>(impl_->config.dump());restored->state=j.at("state");restored->observer.restore(json_observer(j.at("observer")));
  need(restored->now()==restored->observer.time_ns(),"checkpoint clock mismatch");
  J resources=J::object();std::set<std::string> ids;
  for(const auto& job:restored->state.at("active")) {
    need(ids.insert(job.at("id")).second&&tick(job.at("start_ns"))<=restored->now()&&tick(job.at("end_ns"))>restored->now(),"invalid active checkpoint");
    need(job.at("resources")==restored->route(job).at("resources"),"checkpoint resource mapping changed");
    for(const auto& r:job.at("resources")){need(!resources.contains(r.get<std::string>()),"checkpoint resource collision");resources[r.get<std::string>()]=job.at("id");}
  }
  need(resources==restored->state.at("resources"),"checkpoint reservations inconsistent");
  impl_=std::move(restored);
}
std::uint64_t CpuService::time_ns()const noexcept{return impl_->state.at("now").get<Tick>();}
} // namespace hbfsim::eq3_thermal
