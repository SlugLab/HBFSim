#pragma once
#include <hbfsim/eq3_thermal/thermal.hpp>
#include <map>
#include <set>

namespace hbfsim::eq3_thermal {
// Target simulation nanoseconds only. Host/wall time never enters this clock.
enum class Phase { Arrival, Issue, Start, End, Fail, Cancel, Complete };
struct ObservedEvent {
  std::string event_id, operation_id, request_id;
  Phase phase{Phase::Arrival};
  std::uint64_t time_ns{};
  std::string operation, source, physical_type, stack_id;
  std::optional<std::size_t> die, plane;
  std::uint64_t logical_bytes{};
  std::optional<std::uint64_t> physical_bytes, link_bytes;
  std::vector<std::string> resources;
  std::map<std::string, double> component_power_w;
  double external_power_w{}; // explicitly outside the package thermal domain
  std::string evidence, outcome;
  bool operator==(const ObservedEvent&) const = default;
};
struct ObserverCheckpoint {
  RuntimeMode mode{};
  std::uint64_t time_ns{}, next_energy_id{};
  std::map<std::string, ObservedEvent> seen, active;
  std::vector<ObservedEvent> pending, journal, terminals;
  std::map<std::string,double> energy_j;
  double external_energy_j{};
  std::optional<ThermalCheckpoint> thermal;
};
// A consumer of actual service events, not a service scheduler. Off bypasses
// collection and solver construction; read-only validates/accounts but never solves.
class ActivityObserver {
 public:
  ActivityObserver(RuntimeMode mode, ThermalModelConfig config);
  bool submit(const ObservedEvent& event);
  void advance_to(std::uint64_t target_ns);
  RuntimeMode mode() const noexcept { return mode_; }
  bool solver_constructed() const noexcept { return runtime_.solver_constructed(); }
  std::uint64_t time_ns() const noexcept { return time_ns_; }
  const auto& journal() const noexcept { return journal_; }
  const auto& terminals() const noexcept { return terminals_; }
  const auto& energy_j() const noexcept { return energy_j_; }
  double external_energy_j() const noexcept { return external_energy_j_; }
  const ThermalModel* model() const noexcept { return runtime_.model(); }
  SensorSnapshot sensors() const;
  ObserverCheckpoint checkpoint() const;
  void restore(const ObserverCheckpoint& state);
  void reset();
 private:
  void integrate_to(std::uint64_t target_ns);
  void consume(const ObservedEvent& event);
  RuntimeMode mode_;
  ThermalModelConfig config_;
  ThermalRuntime runtime_;
  std::map<std::string,std::size_t> indices_;
  std::map<std::string,ObservedEvent> seen_, active_;
  std::vector<ObservedEvent> pending_, journal_, terminals_;
  std::map<std::string,double> energy_j_;
  double external_energy_j_{};
  std::uint64_t time_ns_{}, next_energy_id_{1};
};
} // namespace hbfsim::eq3_thermal
