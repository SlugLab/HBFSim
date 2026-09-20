#pragma once
#include <hbfsim/eq3_thermal/observer.hpp>
#include <hbfsim/mqsim_online.hpp>
#include <stdexcept>

namespace hbfsim::eq3_thermal {
// Existing MQSim API exposes request occupancy, NOT NAND command start/die/plane.
// Mapping/power is an explicit caller-supplied proxy or fixture; never inferred
// by hashing addresses or presented as measured command-level activity.
class MqsimObserverAdapter {
 public:
  MqsimObserverAdapter(MqsimOnlineEngine& engine,ActivityObserver& observer)
    :engine_(engine),observer_(observer) {
    if(observer.mode()!=RuntimeMode::Off)engine_.enable_observations();
  }
  void bind(std::uint64_t request_id,ObservedEvent metadata) {
    if(observer_.mode()==RuntimeMode::Off)return;
    if(metadata.evidence!="ENGINEERING_FIXTURE_REQUEST_OCCUPANCY")
      throw std::invalid_argument("MQSim request occupancy energy is not NAND command measurement");
    metadata.request_id=std::to_string(request_id);
    metadata.operation_id="mqsim:"+metadata.request_id;
    if(!bindings_.emplace(request_id,std::move(metadata)).second)
      throw std::invalid_argument("duplicate MQSim request binding");
  }
  void drain_to_current_time() {
    if(observer_.mode()!=RuntimeMode::Off)for(const auto& event:engine_.take_observations()) {
      const auto it=bindings_.find(event.request_id);
      if(it==bindings_.end())throw std::invalid_argument("missing explicit MQSim activity mapping");
      auto value=it->second;value.logical_bytes=event.bytes;
      auto emit=[&](Phase phase,std::uint64_t time,const char* tag) {
        value.phase=phase;value.time_ns=time;value.event_id=value.operation_id+":"+tag;
        observer_.submit(value);
      };
      if(event.kind==MqsimEventKind::Arrival)emit(Phase::Arrival,event.time_ns,"arrival");
      else if(event.kind==MqsimEventKind::Admission) {
        emit(Phase::Issue,event.time_ns,"admission");
        emit(Phase::Start,event.time_ns,"occupancy-start-not-nand");
      } else {
        emit(Phase::End,event.time_ns,"media-end");
        value.outcome="success";
        emit(Phase::Complete,event.modeled_completion_ns,"reported-complete");
      }
    }
    observer_.advance_to(engine_.current_time_ns());
  }
 private:
  MqsimOnlineEngine& engine_;
  ActivityObserver& observer_;
  std::map<std::uint64_t,ObservedEvent> bindings_;
};
} // namespace hbfsim::eq3_thermal
