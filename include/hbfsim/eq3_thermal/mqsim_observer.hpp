#pragma once
#include <hbfsim/eq3_thermal/observer.hpp>
#include <hbfsim/mqsim_online.hpp>
#include <algorithm>
#include <functional>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>

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

// Optional pre-submit composition boundary. The decision callback is pure with
// respect to MQSim: it must not submit requests, advance the engine, or drain
// observations. In particular, this adapter owns no event loop and buffers no
// completions. A caller handles DEFER by advancing through the existing
// run_next_completion_until() API, draining observations, and trying again.
enum class MqsimGateMode { Off, Enabled };
enum class MqsimGateDisposition { Allow, Defer, Blocked, Unsupported };

struct MqsimGateDecision {
  MqsimGateDisposition disposition{MqsimGateDisposition::Allow};
  std::optional<std::uint64_t> target_time_ns;
  std::string reason;
};

struct MqsimGateAttempt {
  MqsimGateDecision decision;
  bool submitted{};
  std::uint64_t original_arrival_ns{};
  std::uint64_t evaluated_ns{};
  std::optional<std::uint64_t> backend_arrival_ns;
  std::optional<std::uint64_t> external_wait_ns;
};

enum class MqsimBackendOperation { Demand, DieLevelMaintenance };
struct MqsimBackendCapability {
  bool supported{};
  std::string status;
  std::string detail;
};

using MqsimAdmissionGate =
  std::function<MqsimGateDecision(const HbfRequest&,std::uint64_t)>;

class MqsimSubmissionGateAdapter {
 public:
  MqsimSubmissionGateAdapter(MqsimOnlineEngine& engine,
                             MqsimGateMode mode=MqsimGateMode::Off,
                             MqsimAdmissionGate gate={})
    :engine_(engine),mode_(mode),gate_(std::move(gate)) {
    if(mode_==MqsimGateMode::Enabled&&!gate_)
      throw std::invalid_argument("enabled MQSim submission gate needs a decision callback");
  }

  MqsimGateAttempt try_submit(const HbfRequest& request) {
    const auto now=engine_.current_time_ns();
    if(mode_==MqsimGateMode::Off) {
      engine_.submit(request);
      return {{MqsimGateDisposition::Allow,std::nullopt,"gate disabled"},true,
              request.arrival_ns,now,request.arrival_ns,0};
    }
    if(submitted_.contains(request.request_id))
      throw std::logic_error("MQSim gate request was already submitted");
    // Do not make a control decision before this request exists in target
    // simulation time: the controlled state may change before its arrival.
    if(request.arrival_ns>now)
      return {{MqsimGateDisposition::Defer,request.arrival_ns,
               "request has not reached its target arrival time"},false,
              request.arrival_ns,now,std::nullopt,std::nullopt};

    auto decision=gate_(request,now);
    if(decision.disposition==MqsimGateDisposition::Allow) {
      if(decision.target_time_ns)
        throw std::invalid_argument("ALLOW decision cannot carry a target time");
      auto backend=request;
      // MQSim rejects arrivals before its current clock. Preserve that external
      // wait in the attempt ledger and leave the backend completion untouched.
      backend.arrival_ns=std::max(request.arrival_ns,now);
      engine_.submit(backend);
      submitted_.insert(request.request_id);
      return {std::move(decision),true,request.arrival_ns,now,
              backend.arrival_ns,backend.arrival_ns-request.arrival_ns};
    }
    if(decision.reason.empty())
      throw std::invalid_argument("non-ALLOW MQSim gate decision needs a reason");
    if(decision.disposition==MqsimGateDisposition::Defer) {
      if(!decision.target_time_ns||*decision.target_time_ns<=now)
        throw std::invalid_argument("DEFER target must follow current target time");
    } else if(decision.target_time_ns) {
      throw std::invalid_argument("BLOCKED/UNSUPPORTED decision cannot carry a target time");
    }
    return {std::move(decision),false,request.arrival_ns,now,std::nullopt,std::nullopt};
  }

  static MqsimBackendCapability capability(MqsimBackendOperation operation) {
    if(operation==MqsimBackendOperation::Demand)
      return {true,"SUPPORTED","optional pre-submit demand gate"};
    return {false,"UNSUPPORTED_CAPABILITY",
            "MQSimOnlineEngine exposes no die-level maintenance operation or completion"};
  }

 private:
  MqsimOnlineEngine& engine_;
  MqsimGateMode mode_;
  MqsimAdmissionGate gate_;
  std::set<std::uint64_t> submitted_;
};
} // namespace hbfsim::eq3_thermal
