#include <hbfsim/eq3_thermal/observer.hpp>
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <tuple>

namespace hbfsim::eq3_thermal {
namespace {
void check(bool ok,const char* why) { if(!ok) throw std::invalid_argument(why); }
bool terminal(Phase p) { return p==Phase::Complete || p==Phase::Fail || p==Phase::Cancel; }
}
ActivityObserver::ActivityObserver(RuntimeMode mode,ThermalModelConfig config)
 : mode_(mode),config_(std::move(config)),runtime_(mode,mode==RuntimeMode::Shadow ?
       std::optional<ThermalModelConfig>(config_) : std::nullopt) {
  check(mode!=RuntimeMode::Active,"observer alone cannot implement active control");
  if(mode==RuntimeMode::Off) return;
  validate_model_config(config_);
  for(std::size_t i=0;i<config_.nodes.size();++i) {
    indices_.emplace(config_.nodes[i].id,i);energy_j_[config_.nodes[i].id]=0;
  }
}
bool ActivityObserver::submit(const ObservedEvent& e) {
  if(mode_==RuntimeMode::Off) return false;
  check(!e.event_id.empty()&&!e.operation_id.empty()&&!e.request_id.empty(),"missing event identity");
  check(e.phase>=Phase::Arrival&&e.phase<=Phase::Complete,"invalid event phase");
  if(auto it=seen_.find(e.event_id);it!=seen_.end()) {
    check(it->second==e,"conflicting duplicate event");return false;
  }
  check(e.time_ns>=time_ns_,"late event below committed watermark");
  check(!e.operation.empty()&&!e.source.empty()&&!e.physical_type.empty()&&
        !e.stack_id.empty()&&!e.evidence.empty(),"missing activity provenance; unknown fields must be explicit");
  check(std::isfinite(e.external_power_w)&&e.external_power_w>=0,"invalid external power");
  for(const auto& [id,power]:e.component_power_w) {
    check(indices_.contains(id),"unknown thermal component");
    check(std::isfinite(power)&&power>=0,"invalid component power");
  }
  if(e.phase==Phase::Start)
    check(!e.component_power_w.empty()||e.external_power_w>0,"start needs explicit power mapping, including zero source");
  seen_.emplace(e.event_id,e);pending_.push_back(e);return true;
}
void ActivityObserver::integrate_to(std::uint64_t end) {
  if(end==time_ns_)return;
  const double dt=static_cast<double>(end-time_ns_)*1e-9;
  std::vector<NodeEnergy> assignments;
  std::map<std::string,double> added;
  double external=0;
  for(const auto& [id,e]:active_) {
    (void)id; external+=e.external_power_w*dt;
    for(const auto& [component,power]:e.component_power_w) added[component]+=power*dt;
  }
  for(const auto& [id,energy]:added) {
    check(std::isfinite(energy)&&std::isfinite(energy_j_.at(id)+energy),"energy overflow");
    assignments.push_back({indices_.at(id),energy});
  }
  check(std::isfinite(external_energy_j_+external),"external energy overflow");
  if(auto* model=runtime_.model()) {
    if(!assignments.empty()) {
      check(next_energy_id_!=std::numeric_limits<std::uint64_t>::max(),"energy id exhausted");
      PhysicalActivity a;
      a.activity_id=next_energy_id_++;a.request_id="elapsed-observer-window";
      a.kind=ActivityKind::ExternalHeat;a.source=ActivitySource::External;
      a.start_time_s=static_cast<double>(time_ns_)*1e-9;
      a.end_time_s=static_cast<double>(end)*1e-9;a.completion_time_s=a.end_time_s;
      a.node_energy=std::move(assignments);model->add_activity(std::move(a));
    }
    model->advance_to(static_cast<double>(end)*1e-9);
    (void)model->take_completions(); // segment completions are not service completions
  }
  for(const auto& [id,energy]:added) energy_j_[id]+=energy;
  external_energy_j_+=external;time_ns_=end;
}
void ActivityObserver::consume(const ObservedEvent& e) {
  if(auto active=active_.find(e.operation_id);active!=active_.end()) {
    const auto& start=active->second;
    check(e.request_id==start.request_id&&e.operation==start.operation&&
          e.source==start.source&&e.physical_type==start.physical_type&&
          e.stack_id==start.stack_id&&e.die==start.die,"operation metadata changed during activity");
  }
  if(e.phase==Phase::Start) {
    check(!active_.contains(e.operation_id),"operation already active");
    check(std::none_of(journal_.begin(),journal_.end(),[&](const auto& x){return x.operation_id==e.operation_id&&x.phase==Phase::Start;}),"operation start already consumed");
    check(std::none_of(terminals_.begin(),terminals_.end(),[&](const auto& x){return x.operation_id==e.operation_id;}),"start after terminal event");
    active_.emplace(e.operation_id,e);
  } else if(e.phase==Phase::End) {
    check(active_.erase(e.operation_id)==1,"end without active operation");
  } else if(terminal(e.phase)) {
    check(std::none_of(terminals_.begin(),terminals_.end(),[&](const auto& x){return x.operation_id==e.operation_id;}),"duplicate terminal operation");
    if(e.phase==Phase::Complete) check(!active_.contains(e.operation_id),"completion before activity end");
    else active_.erase(e.operation_id); // failure/cancel retains already integrated energy
    terminals_.push_back(e);
  }
  journal_.push_back(e);
}
void ActivityObserver::advance_to(std::uint64_t target) {
  check(target>=time_ns_,"observer clock moved backwards");
  if(mode_==RuntimeMode::Off) {time_ns_=target;return;}
  std::stable_sort(pending_.begin(),pending_.end(),[](const auto& a,const auto& b){
    return std::tie(a.time_ns,a.phase,a.event_id)<std::tie(b.time_ns,b.phase,b.event_id);
  });
  // Validate a same-time event before committing it. Caller errors are explicit;
  // a failed batch is not silently retried or treated as accepted input.
  std::size_t done=0;
  while(done<pending_.size()&&pending_[done].time_ns<=target) {
    integrate_to(pending_[done].time_ns);consume(pending_[done]);++done;
  }
  pending_.erase(pending_.begin(),pending_.begin()+static_cast<std::ptrdiff_t>(done));
  integrate_to(target);
}
SensorSnapshot ActivityObserver::sensors() const {
  if(const auto* model=runtime_.model()) return SimulatedTemperatureProvider(*model).snapshot();
  std::vector<std::string> names;for(const auto& n:config_.nodes)names.push_back(n.id);
  return UnavailableTemperatureProvider(static_cast<double>(time_ns_)*1e-9,std::move(names)).snapshot();
}
ObserverCheckpoint ActivityObserver::checkpoint() const {
  ObserverCheckpoint s{mode_,time_ns_,next_energy_id_,seen_,active_,pending_,journal_,terminals_,energy_j_,external_energy_j_,std::nullopt};
  if(auto* m=runtime_.model())s.thermal=m->checkpoint();
  return s;
}
std::optional<unsigned> ActivityObserver::advise(const std::vector<std::string>& components,double light,double severe,double shutdown) const {
  if(mode_!=RuntimeMode::Shadow)return std::nullopt;
  check(!components.empty()&&std::isfinite(light)&&std::isfinite(severe)&&std::isfinite(shutdown)&&light>0&&light<severe&&severe<shutdown,"invalid explicit advisory policy");
  double hottest=0;
  for(const auto& id:components){check(indices_.contains(id),"unknown advisory component");hottest=std::max(hottest,runtime_.model()->temperatures_k()[indices_.at(id)]);}
  return static_cast<unsigned>(hottest>=light)+static_cast<unsigned>(hottest>=severe)+static_cast<unsigned>(hottest>=shutdown);
}
void ActivityObserver::restore(const ObserverCheckpoint& s) {
  check(s.mode==mode_,"observer checkpoint mode mismatch");
  check(s.thermal.has_value()==runtime_.solver_constructed(),"checkpoint solver mismatch");
  check(std::isfinite(s.external_energy_j)&&s.external_energy_j>=0,"invalid external checkpoint energy");
  check(s.next_energy_id>0,"invalid checkpoint energy identity");
  if(mode_!=RuntimeMode::Off) {
    check(s.energy_j.size()==indices_.size(),"checkpoint component mismatch");
    for(const auto& [id,v]:s.energy_j)check(indices_.contains(id)&&std::isfinite(v)&&v>=0,"invalid checkpoint energy");
    for(const auto& e:s.pending)check(e.time_ns>=s.time_ns&&s.seen.contains(e.event_id)&&s.seen.at(e.event_id)==e,"invalid pending checkpoint event");
    for(const auto& [id,e]:s.active)check(id==e.operation_id&&e.phase==Phase::Start&&e.time_ns<=s.time_ns&&s.seen.contains(e.event_id),"invalid active checkpoint event");
  }
  if(s.thermal) {
    check(std::abs(s.thermal->time_s-static_cast<double>(s.time_ns)*1e-9)<1e-12,"checkpoint clocks disagree");
    runtime_.model()->restore(*s.thermal);
  }
  time_ns_=s.time_ns;next_energy_id_=s.next_energy_id;seen_=s.seen;active_=s.active;
  pending_=s.pending;journal_=s.journal;terminals_=s.terminals;energy_j_=s.energy_j;
  external_energy_j_=s.external_energy_j;
}
void ActivityObserver::reset() {
  if(auto* m=runtime_.model())m->reset();
  time_ns_=0;next_energy_id_=1;
  seen_.clear();active_.clear();pending_.clear();journal_.clear();terminals_.clear();
  for(auto& [id,e]:energy_j_)e=0;
  external_energy_j_=0;
}
} // namespace hbfsim::eq3_thermal
