#include "hbfsim/eq3_thermal/thermal.hpp"

#include <algorithm>
#include <bit>
#include <charconv>
#include <cmath>
#include <iomanip>
#include <iterator>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <utility>

namespace hbfsim::eq3_thermal {
namespace {

bool finite(double value) { return std::isfinite(value); }

void require(bool condition, const std::string& message) {
  if (!condition) throw std::invalid_argument(message);
}

std::uint64_t parse_unsigned(const std::string& token, const std::string& label) {
  require(!token.empty() && token.front() != '-', label + " must be non-negative");
  std::uint64_t value{};
  const auto [end, error] = std::from_chars(token.data(), token.data() + token.size(), value);
  require(error == std::errc{} && end == token.data() + token.size(),
          "invalid " + label);
  return value;
}

void validate_config_impl(const ThermalModelConfig& config) {
  require(!config.nodes.empty(), "thermal model requires at least one node");
  std::set<std::string> ids;
  std::map<std::string, std::pair<PhysicalType, LogicalRole>> groups;
  for (const auto& node : config.nodes) {
    require(!node.id.empty(), "thermal node id must not be empty");
    require(ids.insert(node.id).second, "thermal node ids must be unique");
    require(finite(node.heat_capacity_j_per_k) && node.heat_capacity_j_per_k > 0.0,
            "heat capacity must be finite and positive J/K");
    require(finite(node.initial_temperature_k) && node.initial_temperature_k > 0.0,
            "initial temperature must be finite and positive K");
    require(finite(node.static_power_w) && node.static_power_w >= 0.0,
            "static power must be finite and non-negative W");
    require(finite(node.boundary_conductance_w_per_k) &&
                node.boundary_conductance_w_per_k >= 0.0,
            "boundary conductance must be finite and non-negative W/K");
    require(finite(node.boundary_temperature_k) && node.boundary_temperature_k > 0.0,
            "boundary temperature must be finite and positive K");
    if (!node.group_id.empty()) {
      auto [group, inserted] = groups.emplace(
          node.group_id, std::pair{node.physical_type, node.logical_role});
      require(inserted || group->second == std::pair{node.physical_type, node.logical_role},
              "sensor group must have one physical type and logical role");
    }
  }
  for (const auto& edge : config.edges) {
    require(edge.node_a < config.nodes.size() && edge.node_b < config.nodes.size(),
            "thermal edge endpoint is outside the node array");
    require(edge.node_a != edge.node_b, "thermal edge must connect distinct nodes");
    require(finite(edge.conductance_w_per_k) && edge.conductance_w_per_k > 0.0,
            "edge conductance must be finite and positive W/K");
  }
}

void validate_activity_impl(const PhysicalActivity& activity, std::size_t node_count) {
  require(activity.activity_id != 0, "activity id zero is reserved");
  require(!activity.request_id.empty(), "activity request id must not be empty");
  require(static_cast<int>(activity.kind) >= 0 &&
              static_cast<int>(activity.kind) <= static_cast<int>(ActivityKind::ExternalHeat),
          "invalid activity kind");
  require(static_cast<int>(activity.source) >= 0 &&
              static_cast<int>(activity.source) <= static_cast<int>(ActivitySource::External),
          "invalid activity source");
  require(finite(activity.start_time_s) && finite(activity.end_time_s) &&
              finite(activity.completion_time_s),
          "activity times must be finite seconds");
  require(activity.start_time_s >= 0.0,
          "activity start time must be non-negative seconds");
  require(activity.end_time_s > activity.start_time_s,
          "activity end must be after start");
  require(activity.completion_time_s >= activity.end_time_s,
          "completion time must not precede physical activity end");
  require(!activity.node_energy.empty(), "activity must assign energy to a node");
  for (const auto& energy : activity.node_energy) {
    require(energy.node_index < node_count,
            "activity energy node is outside the node array");
    require(finite(energy.energy_j) && energy.energy_j >= 0.0,
            "activity energy must be finite and non-negative J");
  }
}

std::string canonical_config(const ThermalModelConfig& config) {
  std::ostringstream out;
  out << std::setprecision(17) << "eq3-rc-v1 "
      << config.direct_intercomponent_edges_enabled << '\n';
  for (const auto& n : config.nodes) {
    out << n.id.size() << ':' << n.id << ' ' << static_cast<int>(n.physical_type) << ' '
        << static_cast<int>(n.logical_role) << ' ' << n.group_id.size() << ':' << n.group_id
        << ' ' << (n.die_index ? static_cast<long long>(*n.die_index) : -1LL) << ' '
        << n.heat_capacity_j_per_k << ' ' << n.initial_temperature_k << ' '
        << n.static_power_w << ' ' << n.boundary_conductance_w_per_k << ' '
        << n.boundary_temperature_k << '\n';
  }
  for (const auto& e : config.edges)
    out << e.node_a << ' ' << e.node_b << ' ' << e.conductance_w_per_k << ' '
        << static_cast<int>(e.kind) << '\n';
  return out.str();
}

std::string identity_for(const ThermalModelConfig& config) {
  // Stable non-cryptographic identity for accidental checkpoint/config mismatch.
  std::uint64_t hash = 1469598103934665603ULL;
  for (unsigned char byte : canonical_config(config)) {
    hash ^= byte;
    hash *= 1099511628211ULL;
  }
  std::ostringstream out;
  out << "eq3-thermal-rc-v1-fnv1a64-" << std::hex << std::setfill('0') << std::setw(16)
      << hash;
  return out.str();
}

std::vector<double> cholesky(const std::vector<double>& matrix, std::size_t n) {
  std::vector<double> lower(n * n, 0.0);
  for (std::size_t i = 0; i < n; ++i) {
    for (std::size_t j = 0; j <= i; ++j) {
      double sum = matrix[i * n + j];
      for (std::size_t k = 0; k < j; ++k) sum -= lower[i * n + k] * lower[j * n + k];
      if (i == j) {
        if (!(sum > 0.0) || !finite(sum))
          throw std::invalid_argument("thermal system matrix is not positive definite");
        lower[i * n + j] = std::sqrt(sum);
      } else {
        lower[i * n + j] = sum / lower[j * n + j];
      }
    }
  }
  return lower;
}

std::vector<double> solve_cholesky(const std::vector<double>& lower,
                                   const std::vector<double>& rhs) {
  const std::size_t n = rhs.size();
  std::vector<double> y(n), x(n);
  for (std::size_t i = 0; i < n; ++i) {
    double sum = rhs[i];
    for (std::size_t j = 0; j < i; ++j) sum -= lower[i * n + j] * y[j];
    y[i] = sum / lower[i * n + i];
  }
  for (std::size_t i = n; i-- > 0;) {
    double sum = y[i];
    for (std::size_t j = i + 1; j < n; ++j) sum -= lower[j * n + i] * x[j];
    x[i] = sum / lower[i * n + i];
  }
  return x;
}

}  // namespace

struct ThermalModel::Impl {
  explicit Impl(ThermalModelConfig value)
      : config(std::move(value)), identity(identity_for(config)) {
    temperature.reserve(config.nodes.size());
    for (const auto& node : config.nodes) temperature.push_back(node.initial_temperature_k);
    applied_energy.assign(config.nodes.size(), 0.0);
  }

  const std::vector<double>& factor(double dt) {
    auto found = factors.find(dt);
    if (found != factors.end()) return found->second;
    const std::size_t n = config.nodes.size();
    std::vector<double> matrix(n * n, 0.0);
    for (std::size_t i = 0; i < n; ++i) {
      matrix[i * n + i] = config.nodes[i].heat_capacity_j_per_k / dt +
                          config.nodes[i].boundary_conductance_w_per_k;
    }
    for (const auto& edge : config.edges) {
      if (!config.direct_intercomponent_edges_enabled &&
          edge.kind == ConductanceKind::InterComponent) {
        continue;
      }
      matrix[edge.node_a * n + edge.node_a] += edge.conductance_w_per_k;
      matrix[edge.node_b * n + edge.node_b] += edge.conductance_w_per_k;
      matrix[edge.node_a * n + edge.node_b] -= edge.conductance_w_per_k;
      matrix[edge.node_b * n + edge.node_a] -= edge.conductance_w_per_k;
    }
    auto inserted = factors.emplace(dt, cholesky(matrix, n));
    return inserted.first->second;
  }

  ThermalModelConfig config;
  std::string identity;
  double time{};
  std::vector<double> temperature;
  std::vector<double> applied_energy;
  std::vector<PhysicalActivity> activities;
  std::set<std::uint64_t> seen_ids;
  std::vector<ActivityCompletion> ready_completions;
  std::map<double, std::vector<double>> factors;
};

ThermalModel::ThermalModel(ThermalModelConfig config) {
  validate_config_impl(config);
  impl_ = std::make_unique<Impl>(std::move(config));
}

void validate_model_config(const ThermalModelConfig& config) {
  validate_config_impl(config);
}
void validate_physical_activity(const PhysicalActivity& activity,
                                std::size_t node_count) {
  validate_activity_impl(activity, node_count);
}
ThermalModel::~ThermalModel() = default;
ThermalModel::ThermalModel(ThermalModel&&) noexcept = default;
ThermalModel& ThermalModel::operator=(ThermalModel&&) noexcept = default;
const ThermalModelConfig& ThermalModel::config() const noexcept { return impl_->config; }
const std::string& ThermalModel::model_identity() const noexcept { return impl_->identity; }
double ThermalModel::time_s() const noexcept { return impl_->time; }
const std::vector<double>& ThermalModel::temperatures_k() const noexcept {
  return impl_->temperature;
}
const std::vector<double>& ThermalModel::applied_energy_j() const noexcept {
  return impl_->applied_energy;
}
std::size_t ThermalModel::factorization_count() const noexcept { return impl_->factors.size(); }

void ThermalModel::add_activity(PhysicalActivity activity) {
  validate_physical_activity(activity, impl_->config.nodes.size());
  require(!impl_->seen_ids.contains(activity.activity_id),
          "duplicate activity id would double-count energy");
  require(activity.start_time_s >= impl_->time,
          "activity cannot begin before the current simulation time");
  impl_->seen_ids.insert(activity.activity_id);
  impl_->activities.push_back(std::move(activity));
}

void ThermalModel::advance_to(double target_time_s) {
  require(finite(target_time_s), "target time must be finite seconds");
  require(target_time_s > impl_->time, "thermal clock must advance monotonically");
  const double previous_time = impl_->time;
  const std::size_t n = impl_->config.nodes.size();
  std::vector<double> working_temperature = impl_->temperature;
  std::vector<double> added_energy(n, 0.0);
  // Preserve event time structure even when the caller requests a wide window.
  // Every segment has constant activity power; its exact energy is integrated.
  std::set<double> boundaries{previous_time, target_time_s};
  for (const auto& activity : impl_->activities) {
    if (activity.start_time_s > previous_time && activity.start_time_s < target_time_s)
      boundaries.insert(activity.start_time_s);
    if (activity.end_time_s > previous_time && activity.end_time_s < target_time_s)
      boundaries.insert(activity.end_time_s);
  }
  auto segment_start = boundaries.begin();
  for (auto segment_end = std::next(segment_start); segment_end != boundaries.end();
       ++segment_start, ++segment_end) {
    const double start = *segment_start;
    const double end = *segment_end;
    const double dt = end - start;
    std::vector<double> energy(n, 0.0);
    for (const auto& activity : impl_->activities) {
      const double overlap_start = std::max(start, activity.start_time_s);
      const double overlap_end = std::min(end, activity.end_time_s);
      if (overlap_end <= overlap_start) continue;
      const double fraction = (overlap_end - overlap_start) /
                              (activity.end_time_s - activity.start_time_s);
      for (const auto& assignment : activity.node_energy)
        energy[assignment.node_index] += assignment.energy_j * fraction;
    }
    std::vector<double> rhs(n);
    for (std::size_t i = 0; i < n; ++i) {
      const auto& node = impl_->config.nodes[i];
      rhs[i] = node.heat_capacity_j_per_k / dt * working_temperature[i] +
               node.static_power_w + energy[i] / dt +
               node.boundary_conductance_w_per_k * node.boundary_temperature_k;
      require(finite(rhs[i]), "thermal RHS overflowed or became nonfinite");
    }
    auto candidate = solve_cholesky(impl_->factor(dt), rhs);
    for (double temperature : candidate)
      require(finite(temperature) && temperature > 0.0,
              "thermal solve produced a nonfinite or nonpositive temperature");
    working_temperature = std::move(candidate);
    for (std::size_t i = 0; i < n; ++i) {
      added_energy[i] += energy[i];
      require(finite(added_energy[i]) &&
                  finite(impl_->applied_energy[i] + added_energy[i]),
              "applied thermal energy overflowed");
    }
  }
  impl_->temperature = std::move(working_temperature);
  for (std::size_t i = 0; i < n; ++i) impl_->applied_energy[i] += added_energy[i];
  impl_->time = target_time_s;
  for (const auto& activity : impl_->activities) {
    if (activity.completion_time_s > previous_time &&
        activity.completion_time_s <= target_time_s) {
      impl_->ready_completions.push_back({activity.activity_id, activity.request_id,
                                          activity.source, activity.completion_time_s});
    }
  }
  impl_->activities.erase(
      std::remove_if(impl_->activities.begin(), impl_->activities.end(),
                     [&](const PhysicalActivity& value) {
                       return value.end_time_s <= impl_->time &&
                              value.completion_time_s <= impl_->time;
                     }),
      impl_->activities.end());
}

std::vector<ActivityCompletion> ThermalModel::take_completions() {
  auto result = std::move(impl_->ready_completions);
  impl_->ready_completions.clear();
  return result;
}

void ThermalModel::reset() {
  impl_->time = 0.0;
  impl_->activities.clear();
  impl_->seen_ids.clear();
  impl_->ready_completions.clear();
  impl_->factors.clear();
  std::fill(impl_->applied_energy.begin(), impl_->applied_energy.end(), 0.0);
  for (std::size_t i = 0; i < impl_->config.nodes.size(); ++i)
    impl_->temperature[i] = impl_->config.nodes[i].initial_temperature_k;
}

ThermalCheckpoint ThermalModel::checkpoint() const {
  return {impl_->identity, impl_->time, impl_->temperature, impl_->applied_energy,
          impl_->activities,
          std::vector<std::uint64_t>(impl_->seen_ids.begin(), impl_->seen_ids.end()),
          impl_->ready_completions};
}

void ThermalModel::restore(const ThermalCheckpoint& value) {
  require(value.model_identity == impl_->identity,
          "checkpoint model identity does not match thermal configuration");
  require(finite(value.time_s) && value.time_s >= 0.0, "invalid checkpoint time");
  require(value.temperature_k.size() == impl_->config.nodes.size(),
          "checkpoint temperature count does not match model");
  require(value.applied_energy_j.size() == impl_->config.nodes.size(),
          "checkpoint energy count does not match model");
  for (double t : value.temperature_k)
    require(finite(t) && t > 0.0, "checkpoint contains invalid temperature");
  for (double e : value.applied_energy_j)
    require(finite(e) && e >= 0.0, "checkpoint contains invalid energy");
  std::set<std::uint64_t> validated_seen;
  for (auto id : value.seen_activity_ids) {
    require(id != 0, "checkpoint contains reserved activity id zero");
    require(validated_seen.insert(id).second, "checkpoint contains duplicate seen id");
  }
  std::set<std::uint64_t> pending_ids;
  for (const auto& activity : value.pending_activities) {
    validate_physical_activity(activity, impl_->config.nodes.size());
    require(validated_seen.contains(activity.activity_id),
            "checkpoint pending activity is absent from seen ids");
    require(pending_ids.insert(activity.activity_id).second,
            "checkpoint contains duplicate pending activity");
    require(activity.end_time_s > value.time_s ||
                activity.completion_time_s > value.time_s,
            "checkpoint retained an activity already fully completed");
  }
  std::set<std::uint64_t> completion_ids;
  for (const auto& completion : value.ready_completions) {
    require(completion.activity_id != 0 &&
                validated_seen.contains(completion.activity_id),
            "checkpoint completion has unknown activity id");
    require(!completion.request_id.empty(),
            "checkpoint completion request id must not be empty");
    require(static_cast<int>(completion.source) >= 0 &&
                static_cast<int>(completion.source) <=
                    static_cast<int>(ActivitySource::External),
            "checkpoint completion has invalid source");
    require(finite(completion.completion_time_s) &&
                completion.completion_time_s >= 0.0 &&
                completion.completion_time_s <= value.time_s,
            "checkpoint completion time is invalid or in the future");
    require(completion_ids.insert(completion.activity_id).second &&
                !pending_ids.contains(completion.activity_id),
            "checkpoint duplicates a ready completion");
  }
  // All validation precedes mutation so a rejected checkpoint is atomic.
  impl_->time = value.time_s;
  impl_->temperature = value.temperature_k;
  impl_->applied_energy = value.applied_energy_j;
  impl_->activities = value.pending_activities;
  impl_->seen_ids = std::move(validated_seen);
  impl_->ready_completions = value.ready_completions;
  impl_->factors.clear();
}

std::vector<GroupTemperature> ThermalModel::grouped_temperatures() const {
  struct Aggregate {
    PhysicalType type;
    LogicalRole role;
    double maximum;
    double sum;
    std::size_t count;
  };
  std::map<std::string, Aggregate> groups;
  for (std::size_t i = 0; i < impl_->config.nodes.size(); ++i) {
    const auto& node = impl_->config.nodes[i];
    if (node.group_id.empty()) continue;
    auto [it, inserted] = groups.emplace(
        node.group_id,
        Aggregate{node.physical_type, node.logical_role, impl_->temperature[i], 0.0, 0});
    require(inserted || (it->second.type == node.physical_type &&
                         it->second.role == node.logical_role),
            "a sensor group cannot mix physical types or logical roles");
    it->second.maximum = std::max(it->second.maximum, impl_->temperature[i]);
    it->second.sum += impl_->temperature[i];
    ++it->second.count;
  }
  std::vector<GroupTemperature> result;
  for (const auto& [id, value] : groups)
    result.push_back(
        {id, value.type, value.role, value.maximum, value.sum / value.count, value.count});
  return result;
}

std::string checkpoint_to_text(const ThermalCheckpoint& value) {
  std::ostringstream out;
  out << std::setprecision(17) << "HBFSIM_EQ3_THERMAL_CHECKPOINT 1\n"
      << std::quoted(value.model_identity) << '\n' << value.time_s << '\n';
  out << value.temperature_k.size();
  for (double v : value.temperature_k) out << ' ' << v;
  out << '\n' << value.applied_energy_j.size();
  for (double v : value.applied_energy_j) out << ' ' << v;
  out << '\n' << value.seen_activity_ids.size();
  for (auto id : value.seen_activity_ids) out << ' ' << id;
  out << '\n' << value.pending_activities.size() << '\n';
  for (const auto& a : value.pending_activities) {
    out << a.activity_id << ' ' << std::quoted(a.request_id) << ' ' << static_cast<int>(a.kind)
        << ' ' << static_cast<int>(a.source) << ' ' << a.bytes << ' '
        << (a.stack_index ? static_cast<long long>(*a.stack_index) : -1LL) << ' '
        << (a.die_index ? static_cast<long long>(*a.die_index) : -1LL) << ' '
        << (a.plane_index ? static_cast<long long>(*a.plane_index) : -1LL) << ' '
        << a.start_time_s << ' ' << a.end_time_s << ' ' << a.completion_time_s << ' '
        << a.node_energy.size();
    for (const auto& e : a.node_energy) out << ' ' << e.node_index << ' ' << e.energy_j;
    out << '\n';
  }
  out << value.ready_completions.size() << '\n';
  for (const auto& completion : value.ready_completions)
    out << completion.activity_id << ' ' << std::quoted(completion.request_id) << ' '
        << static_cast<int>(completion.source) << ' ' << completion.completion_time_s << '\n';
  return out.str();
}

ThermalCheckpoint checkpoint_from_text(std::string_view text) {
  std::istringstream in{std::string(text)};
  std::string magic;
  int version{};
  ThermalCheckpoint value;
  if (!(in >> magic >> version) || magic != "HBFSIM_EQ3_THERMAL_CHECKPOINT" || version != 1)
    throw std::invalid_argument("unsupported thermal checkpoint header");
  if (!(in >> std::quoted(value.model_identity) >> value.time_s))
    throw std::invalid_argument("malformed thermal checkpoint identity/time");
  auto read_doubles = [&](std::vector<double>& values) {
    std::size_t count{};
    if (!(in >> count) || count > 1000000) throw std::invalid_argument("invalid checkpoint vector");
    values.resize(count);
    for (double& item : values) if (!(in >> item)) throw std::invalid_argument("truncated checkpoint vector");
  };
  read_doubles(value.temperature_k);
  read_doubles(value.applied_energy_j);
  std::size_t seen_count{};
  if (!(in >> seen_count) || seen_count > 1000000) throw std::invalid_argument("invalid seen-id count");
  value.seen_activity_ids.resize(seen_count);
  for (auto& id : value.seen_activity_ids) {
    std::string token;
    if (!(in >> token)) throw std::invalid_argument("truncated seen ids");
    id = parse_unsigned(token, "checkpoint seen id");
  }
  std::size_t activity_count{};
  if (!(in >> activity_count) || activity_count > 1000000) throw std::invalid_argument("invalid activity count");
  value.pending_activities.resize(activity_count);
  for (auto& a : value.pending_activities) {
    int kind{}, source{};
    long long stack{}, die{}, plane{};
    std::size_t energy_count{};
    std::string id_token, bytes_token;
    if (!(in >> id_token >> std::quoted(a.request_id) >> kind >> source >> bytes_token >>
          stack >> die >> plane >> a.start_time_s >> a.end_time_s >> a.completion_time_s >>
          energy_count) || energy_count > 1000000)
      throw std::invalid_argument("malformed checkpoint activity");
    a.activity_id = parse_unsigned(id_token, "checkpoint activity id");
    a.bytes = parse_unsigned(bytes_token, "checkpoint activity bytes");
    if (kind < 0 || kind > static_cast<int>(ActivityKind::ExternalHeat) || source < 0 ||
        source > static_cast<int>(ActivitySource::External))
      throw std::invalid_argument("invalid checkpoint activity enum");
    a.kind = static_cast<ActivityKind>(kind);
    a.source = static_cast<ActivitySource>(source);
    if (stack >= 0) a.stack_index = static_cast<std::size_t>(stack);
    if (die >= 0) a.die_index = static_cast<std::size_t>(die);
    if (plane >= 0) a.plane_index = static_cast<std::size_t>(plane);
    a.node_energy.resize(energy_count);
    for (auto& e : a.node_energy)
      if (!(in >> e.node_index >> e.energy_j)) throw std::invalid_argument("truncated activity energy");
  }
  std::size_t completion_count{};
  if (!(in >> completion_count) || completion_count > 1000000)
    throw std::invalid_argument("invalid completion count");
  value.ready_completions.resize(completion_count);
  for (auto& completion : value.ready_completions) {
    int source{};
    std::string id_token;
    if (!(in >> id_token >> std::quoted(completion.request_id) >> source >>
          completion.completion_time_s) || source < 0 ||
        source > static_cast<int>(ActivitySource::External))
      throw std::invalid_argument("malformed checkpoint completion");
    completion.activity_id = parse_unsigned(id_token, "checkpoint completion id");
    completion.source = static_cast<ActivitySource>(source);
  }
  in >> std::ws;
  if (!in.eof()) throw std::invalid_argument("unexpected trailing checkpoint data");
  return value;
}

SimulatedTemperatureProvider::SimulatedTemperatureProvider(const ThermalModel& model)
    : model_(model) {}
SensorSnapshot SimulatedTemperatureProvider::snapshot() const {
  SensorSnapshot result{model_.time_s(), {}};
  const auto& nodes = model_.config().nodes;
  const auto& temperatures = model_.temperatures_k();
  for (std::size_t i = 0; i < nodes.size(); ++i)
    result.nodes.push_back({nodes[i].id, nodes[i].physical_type, nodes[i].logical_role,
                            SensorSource::Simulated, true, temperatures[i], std::nullopt, 0.0});
  return result;
}
UnavailableTemperatureProvider::UnavailableTemperatureProvider(
    double simulation_time_s, std::vector<std::string> locations)
    : simulation_time_s_(simulation_time_s), locations_(std::move(locations)) {
  require(finite(simulation_time_s_) && simulation_time_s_ >= 0.0,
          "sensor snapshot time must be finite and non-negative");
}
SensorSnapshot UnavailableTemperatureProvider::snapshot() const {
  SensorSnapshot result{simulation_time_s_, {}};
  for (const auto& location : locations_)
    result.nodes.push_back({location, PhysicalType::Other, LogicalRole::Other,
                            SensorSource::Unavailable, false, std::nullopt, std::nullopt, 0.0});
  return result;
}

ThermalRuntime::ThermalRuntime(RuntimeMode mode, std::optional<ThermalModelConfig> config)
    : mode_(mode) {
  if (mode_ == RuntimeMode::Off || mode_ == RuntimeMode::ReadOnly) {
    require(!config.has_value(), "Off/ReadOnly mode must not construct a thermal solver");
  } else if (mode_ == RuntimeMode::Shadow) {
    require(config.has_value(), "Shadow thermal runtime requires a model configuration");
    model_ = std::make_unique<ThermalModel>(std::move(*config));
  } else {
    throw std::invalid_argument("Active thermal feedback is NOT_IMPLEMENTED in P1");
  }
}
bool ThermalRuntime::solver_constructed() const noexcept { return model_ != nullptr; }
ThermalModel* ThermalRuntime::model() noexcept { return model_.get(); }
const ThermalModel* ThermalRuntime::model() const noexcept { return model_.get(); }

}  // namespace hbfsim::eq3_thermal
