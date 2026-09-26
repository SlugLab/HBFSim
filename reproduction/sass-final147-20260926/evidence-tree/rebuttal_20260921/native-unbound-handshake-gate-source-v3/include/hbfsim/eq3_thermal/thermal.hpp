#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace hbfsim::eq3_thermal {

enum class PhysicalType { Gpu, Gddr, Hbm, Hbf, Interposer, Cooling, Other };
enum class LogicalRole { Compute, FastMemory, CapacityMemory, Package, Cooling, Other };
enum class ActivityKind { Read, Program, Erase, Link, Relay, Refresh, ExternalHeat };
enum class ActivitySource { Demand, Prefetch, Refresh, External };
enum class SensorSource { Simulated, Measured, Replayed, Unavailable };
enum class RuntimeMode { Off, ReadOnly, Shadow, Active };
enum class ConductanceKind { IntraComponent, InterComponent, Cooling };

struct ThermalNode {
  std::string id;
  PhysicalType physical_type{PhysicalType::Other};
  LogicalRole logical_role{LogicalRole::Other};
  std::string group_id;
  std::optional<std::size_t> die_index;
  double heat_capacity_j_per_k{};
  double initial_temperature_k{};
  double static_power_w{};
  double boundary_conductance_w_per_k{};
  double boundary_temperature_k{};
};

struct ThermalEdge {
  std::size_t node_a{};
  std::size_t node_b{};
  double conductance_w_per_k{};
  ConductanceKind kind{ConductanceKind::InterComponent};
};

struct ThermalModelConfig {
  std::vector<ThermalNode> nodes;
  std::vector<ThermalEdge> edges;
  // False disables only edges explicitly classified InterComponent. It is a
  // direct-edge numerical toggle, not a claim of complete physical isolation:
  // shared dynamic sink/interposer states can still mediate heat indirectly.
  bool direct_intercomponent_edges_enabled{true};
};

struct NodeEnergy {
  std::size_t node_index{};
  double energy_j{};
};

struct PhysicalActivity {
  std::uint64_t activity_id{};
  std::string request_id;
  ActivityKind kind{ActivityKind::Read};
  ActivitySource source{ActivitySource::Demand};
  std::uint64_t bytes{};
  std::optional<std::size_t> stack_index;
  std::optional<std::size_t> die_index;
  std::optional<std::size_t> plane_index;
  double start_time_s{};
  double end_time_s{};
  double completion_time_s{};
  std::vector<NodeEnergy> node_energy;
};

struct ActivityCompletion {
  std::uint64_t activity_id{};
  std::string request_id;
  ActivitySource source{ActivitySource::Demand};
  double completion_time_s{};
};

struct ThermalCheckpoint {
  std::string model_identity;
  double time_s{};
  std::vector<double> temperature_k;
  std::vector<double> applied_energy_j;
  std::vector<PhysicalActivity> pending_activities;
  std::vector<std::uint64_t> seen_activity_ids;
  std::vector<ActivityCompletion> ready_completions;
};

struct SensorReading {
  std::string location;
  PhysicalType physical_type{PhysicalType::Other};
  LogicalRole logical_role{LogicalRole::Other};
  SensorSource source{SensorSource::Unavailable};
  bool valid{false};
  std::optional<double> value_k;
  std::optional<double> quantization_k;
  double latency_s{};
};

struct SensorSnapshot {
  double simulation_time_s{};
  std::vector<SensorReading> nodes;
};

struct GroupTemperature {
  std::string group_id;
  PhysicalType physical_type{PhysicalType::Other};
  LogicalRole logical_role{LogicalRole::Other};
  double hotspot_k{};
  double mean_k{};
  std::size_t node_count{};
};

class ThermalModel {
 public:
  explicit ThermalModel(ThermalModelConfig config);
  ~ThermalModel();
  ThermalModel(ThermalModel&&) noexcept;
  ThermalModel& operator=(ThermalModel&&) noexcept;
  ThermalModel(const ThermalModel&) = delete;
  ThermalModel& operator=(const ThermalModel&) = delete;

  const ThermalModelConfig& config() const noexcept;
  const std::string& model_identity() const noexcept;
  double time_s() const noexcept;
  const std::vector<double>& temperatures_k() const noexcept;
  const std::vector<double>& applied_energy_j() const noexcept;
  std::size_t factorization_count() const noexcept;

  void add_activity(PhysicalActivity activity);
  void advance_to(double target_time_s);
  std::vector<ActivityCompletion> take_completions();
  void reset();
  ThermalCheckpoint checkpoint() const;
  void restore(const ThermalCheckpoint& checkpoint);
  std::vector<GroupTemperature> grouped_temperatures() const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

std::string checkpoint_to_text(const ThermalCheckpoint& checkpoint);
ThermalCheckpoint checkpoint_from_text(std::string_view text);
void validate_model_config(const ThermalModelConfig& config);
void validate_physical_activity(const PhysicalActivity& activity,
                                std::size_t node_count);
ThermalModelConfig model_config_from_text(std::string_view text);
std::vector<PhysicalActivity> activities_from_text(
    std::string_view text, const ThermalModelConfig& config);

class ITemperatureProvider {
 public:
  virtual ~ITemperatureProvider() = default;
  virtual SensorSnapshot snapshot() const = 0;
};

class SimulatedTemperatureProvider final : public ITemperatureProvider {
 public:
  explicit SimulatedTemperatureProvider(const ThermalModel& model);
  SensorSnapshot snapshot() const override;

 private:
  const ThermalModel& model_;
};

class UnavailableTemperatureProvider final : public ITemperatureProvider {
 public:
  explicit UnavailableTemperatureProvider(double simulation_time_s,
                                          std::vector<std::string> locations);
  SensorSnapshot snapshot() const override;

 private:
  double simulation_time_s_{};
  std::vector<std::string> locations_;
};

// Off and ReadOnly own no solver. Shadow constructs the numerical core.
// Active feedback is intentionally rejected as not implemented in P1.
class ThermalRuntime {
 public:
  ThermalRuntime(RuntimeMode mode, std::optional<ThermalModelConfig> config);
  bool solver_constructed() const noexcept;
  ThermalModel* model() noexcept;
  const ThermalModel* model() const noexcept;

 private:
  RuntimeMode mode_;
  std::unique_ptr<ThermalModel> model_;
};

}  // namespace hbfsim::eq3_thermal
