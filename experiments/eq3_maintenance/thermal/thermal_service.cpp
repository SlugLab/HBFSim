// Persistent, experiment-only sparse thermal service for EQ3 maintenance studies.
// It intentionally does not alter the public thermal ABI or any default target.
#include "hbfsim/eq3_thermal/thermal.hpp"

#include <Eigen/Sparse>
#include <Eigen/SparseCholesky>
#include <json.hpp>
#include <openssl/evp.h>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Json = nlohmann::json;
using SparseMatrix = Eigen::SparseMatrix<double>;
using SparseSolver = Eigen::SimplicialLDLT<SparseMatrix, Eigen::Lower,
                                           Eigen::AMDOrdering<int>>;
using ThermalModelConfig = hbfsim::eq3_thermal::ThermalModelConfig;
using ConductanceKind = hbfsim::eq3_thermal::ConductanceKind;
using Tick = std::uint64_t;

[[noreturn]] void fail(const std::string& message) {
  throw std::invalid_argument(message);
}
void require(bool condition, const std::string& message) {
  if (!condition) fail(message);
}
double seconds(Clock::duration duration) {
  return std::chrono::duration<double>(duration).count();
}
std::string read_file(const std::string& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot open input: " + path);
  std::ostringstream output;
  output << input.rdbuf();
  if (input.bad()) throw std::runtime_error("failed reading input: " + path);
  return output.str();
}
std::string sha256(const std::string& bytes) {
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(
      EVP_MD_CTX_new(), EVP_MD_CTX_free);
  require(context != nullptr, "cannot allocate SHA-256 context");
  require(EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) == 1 &&
              EVP_DigestUpdate(context.get(), bytes.data(), bytes.size()) == 1,
          "SHA-256 calculation failed");
  unsigned char digest[EVP_MAX_MD_SIZE];
  unsigned int size = 0;
  require(EVP_DigestFinal_ex(context.get(), digest, &size) == 1 && size == 32,
          "SHA-256 finalization failed");
  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (unsigned int index = 0; index < size; ++index)
    output << std::setw(2) << static_cast<unsigned int>(digest[index]);
  return output.str();
}
Tick unsigned_value(const std::string& token, const std::string& label) {
  Tick result{};
  const auto [end, error] =
      std::from_chars(token.data(), token.data() + token.size(), result);
  require(error == std::errc{} && end == token.data() + token.size(),
          "invalid " + label);
  return result;
}
double finite_nonnegative(const std::string& token, const std::string& label) {
  std::size_t used = 0;
  double result{};
  try {
    result = std::stod(token, &used);
  } catch (const std::exception&) {
    fail("invalid " + label);
  }
  require(used == token.size() && std::isfinite(result) && result >= 0,
          label + " must be finite and non-negative");
  return result;
}

struct Options {
  std::string model_path, grid_path, sensors_path;
  std::string model_sha256, grid_sha256, sensors_sha256;
  Tick step_ns{};
  double minimum_k{}, maximum_k{};
};

Options parse_options(int argc, char** argv) {
  std::map<std::string, std::string> values;
  for (int index = 1; index < argc; index += 2) {
    require(index + 1 < argc, "every option requires a value");
    const std::string key = argv[index];
    require(key.starts_with("--") && values.emplace(key, argv[index + 1]).second,
            "unknown or duplicate option: " + key);
  }
  for (const char* key : {"--model", "--grid", "--sensors", "--model-sha256",
                          "--grid-sha256", "--sensors-sha256", "--step-ns",
                          "--min-k", "--max-k"})
    require(values.contains(key), std::string("missing option: ") + key);
  require(values.size() == 9, "unknown CLI option");
  Options result;
  result.model_path = values["--model"];
  result.grid_path = values["--grid"];
  result.sensors_path = values["--sensors"];
  result.model_sha256 = values["--model-sha256"];
  result.grid_sha256 = values["--grid-sha256"];
  result.sensors_sha256 = values["--sensors-sha256"];
  result.step_ns = unsigned_value(values["--step-ns"], "step-ns");
  result.minimum_k = finite_nonnegative(values["--min-k"], "min-k");
  result.maximum_k = finite_nonnegative(values["--max-k"], "max-k");
  require(result.step_ns > 0, "step-ns must be positive");
  require(result.minimum_k > 0 && result.minimum_k < result.maximum_k,
          "temperature domain must be positive and increasing");
  return result;
}

SparseMatrix system_matrix(const ThermalModelConfig& config, double step_s) {
  require(config.nodes.size() <= static_cast<std::size_t>(std::numeric_limits<int>::max()),
          "node count exceeds Eigen int range");
  const int count = static_cast<int>(config.nodes.size());
  std::vector<Eigen::Triplet<double>> entries;
  entries.reserve(config.nodes.size() + 4 * config.edges.size());
  for (int index = 0; index < count; ++index) {
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    entries.emplace_back(index, index, node.heat_capacity_j_per_k / step_s +
                                            node.boundary_conductance_w_per_k);
  }
  for (const auto& edge : config.edges) {
    if (!config.direct_intercomponent_edges_enabled &&
        edge.kind == ConductanceKind::InterComponent)
      continue;
    const int left = static_cast<int>(edge.node_a);
    const int right = static_cast<int>(edge.node_b);
    entries.emplace_back(left, left, edge.conductance_w_per_k);
    entries.emplace_back(right, right, edge.conductance_w_per_k);
    entries.emplace_back(left, right, -edge.conductance_w_per_k);
    entries.emplace_back(right, left, -edge.conductance_w_per_k);
  }
  SparseMatrix result(count, count);
  result.setFromTriplets(entries.begin(), entries.end());
  result.makeCompressed();
  return result;
}

struct Component {
  std::string id;
  std::vector<std::size_t> cells;
  std::vector<double> volume_weights;
};
struct Sensor {
  std::string id;
  bool maximum{};
  std::vector<std::size_t> cells;
  std::vector<double> weights;
};
struct Interval {
  Tick start_step{}, end_step{};
  std::size_t component{};
  double energy_j{};
};

class Service {
 public:
  Service(Options options, std::string model_text, std::string grid_text,
          std::string sensors_text)
      : options_(std::move(options)), model_text_(std::move(model_text)),
        grid_text_(std::move(grid_text)), sensors_text_(std::move(sensors_text)),
        config_(hbfsim::eq3_thermal::model_config_from_text(model_text_)),
        step_s_(static_cast<double>(options_.step_ns) * 1e-9),
        matrix_(system_matrix(config_, step_s_)) {
    verify_hash(model_text_, options_.model_sha256, "model");
    verify_hash(grid_text_, options_.grid_sha256, "grid");
    verify_hash(sensors_text_, options_.sensors_sha256, "sensors");
    parse_grid(Json::parse(grid_text_));
    parse_sensors(Json::parse(sensors_text_));
    const Eigen::Index count = static_cast<Eigen::Index>(config_.nodes.size());
    temperature_.resize(count);
    theta_.resize(count);
    capacity_over_dt_.resize(count);
    static_and_boundary_.resize(count);
    applied_node_energy_ = Eigen::VectorXd::Zero(count);
    origin_k_ = config_.nodes.front().initial_temperature_k;
    for (Eigen::Index index = 0; index < count; ++index) {
      const auto& node = config_.nodes[static_cast<std::size_t>(index)];
      require(node.initial_temperature_k >= options_.minimum_k &&
                  node.initial_temperature_k <= options_.maximum_k,
              "initial temperature outside declared domain: " + node.id);
      temperature_[index] = node.initial_temperature_k;
      theta_[index] = node.initial_temperature_k - origin_k_;
      capacity_over_dt_[index] = node.heat_capacity_j_per_k / step_s_;
      static_and_boundary_[index] =
          node.static_power_w + node.boundary_conductance_w_per_k *
                                    (node.boundary_temperature_k - origin_k_);
      static_power_w_ += node.static_power_w;
    }
    const auto begin = Clock::now();
    solver_.compute(matrix_);
    factor_seconds_ = seconds(Clock::now() - begin);
    require(solver_.info() == Eigen::Success, "sparse LDLT factorization failed");
  }

  Json ready() const {
    return {{"type", "READY"},
            {"schema_version", "eq3-maintenance-persistent-thermal-v1"},
            {"backend", "Eigen::SimplicialLDLT_AMD"},
            {"state_variable", "theta=T-origin"},
            {"temperature_clamping", false},
            {"model_sha256", options_.model_sha256},
            {"grid_sha256", options_.grid_sha256},
            {"sensors_sha256", options_.sensors_sha256},
            {"step_ns", options_.step_ns},
            {"domain_k", {options_.minimum_k, options_.maximum_k}},
            {"nodes", config_.nodes.size()},
            {"entities", components_.size()},
            {"sensors", sensors_.size()},
            {"matrix_structural_nnz", matrix_.nonZeros()},
            {"factor_L_nnz", solver_.matrixL().nestedExpression().nonZeros()},
            {"factorization_count", 1},
            {"timing", {{"factor_seconds", factor_seconds_}}}};
  }

  Json energy(Tick start_ns, Tick end_ns, const std::string& id, double energy_j) {
    require(start_ns < end_ns, "ENERGY interval must be positive");
    require(start_ns % options_.step_ns == 0 && end_ns % options_.step_ns == 0,
            "ENERGY interval must align with the fixed thermal step");
    const Tick start = start_ns / options_.step_ns;
    const Tick end = end_ns / options_.step_ns;
    require(start >= current_step_, "ENERGY cannot enter committed thermal time");
    const auto component = component_index_.find(id);
    require(component != component_index_.end(), "unknown or unpowered component: " + id);
    const auto identity = std::tuple{start, end, component->second};
    require(interval_identities_.insert(identity).second,
            "duplicate component ENERGY interval would double count");
    intervals_.push_back({start, end, component->second, energy_j});
    accepted_activity_j_ += energy_j;
    return {{"type", "ENERGY_ACK"}, {"start_ns", start_ns}, {"end_ns", end_ns},
            {"component", id}, {"energy_j", energy_j},
            {"accepted_activity_energy_j", static_cast<double>(accepted_activity_j_)},
            {"pending_intervals", intervals_.size()}};
  }

  Json advance(Tick target_ns) {
    require(target_ns % options_.step_ns == 0,
            "ADVANCE target must align with the fixed thermal step");
    const Tick target_step = target_ns / options_.step_ns;
    require(target_step > current_step_, "ADVANCE must move forward");
    const Tick initial_step = current_step_;
    const long double initial_activity = applied_activity_j_;
    const long double initial_node_activity = applied_node_energy_.sum();
    const long double initial_static = static_input_j_;
    const long double initial_boundary = boundary_loss_j_;
    const long double initial_stored = stored_energy();
    const auto advance_start = Clock::now();
    for (; current_step_ < target_step;) advance_one();
    const double advance_seconds = seconds(Clock::now() - advance_start);
    intervals_.erase(std::remove_if(intervals_.begin(), intervals_.end(),
                                    [&](const Interval& value) {
                                      return value.end_step <= current_step_;
                                    }), intervals_.end());
    const auto observation_start = Clock::now();
    Json entities = entity_temperatures();
    Json sensors = sensor_temperatures();
    const double observation_seconds = seconds(Clock::now() - observation_start);
    const long double stored = stored_energy();
    Json window_energy = energy_json(applied_activity_j_ - initial_activity,
                                     static_input_j_ - initial_static,
                                     stored - initial_stored,
                                     boundary_loss_j_ - initial_boundary);
    const long double mapped_window = applied_node_energy_.sum() - initial_node_activity;
    window_energy["node_mapped_activity_input_j"] = static_cast<double>(mapped_window);
    window_energy["component_mapping_error_j"] =
        static_cast<double>(mapped_window - (applied_activity_j_ - initial_activity));
    Json cumulative_energy = energy_json(applied_activity_j_, static_input_j_, stored,
                                         boundary_loss_j_);
    cumulative_energy["node_mapped_activity_input_j"] = applied_node_energy_.sum();
    cumulative_energy["component_mapping_error_j"] =
        static_cast<double>(applied_node_energy_.sum() - applied_activity_j_);
    return {{"type", "ADVANCE"},
            {"from_ns", initial_step * options_.step_ns},
            {"time_ns", current_step_ * options_.step_ns},
            {"steps", current_step_ - initial_step},
            {"entity_temperatures_k", std::move(entities)},
            {"sensor_temperatures_k", std::move(sensors)},
            {"temperature_range_k", {temperature_.minCoeff(), temperature_.maxCoeff()}},
            {"energy_j",
             {{"window", std::move(window_energy)},
              {"cumulative", std::move(cumulative_energy)},
              {"accepted_activity", static_cast<double>(accepted_activity_j_)},
              {"accepted_not_yet_applied_activity",
               static_cast<double>(accepted_activity_j_ - applied_activity_j_)}}},
            {"timing", {{"advance_seconds", advance_seconds},
                         {"observation_seconds", observation_seconds}}},
            {"factorization_count", 1}};
  }

  Json quit() const {
    require(intervals_.empty() &&
                std::abs(static_cast<double>(accepted_activity_j_ - applied_activity_j_)) <=
                    1e-12 * std::max(1.0, std::abs(static_cast<double>(accepted_activity_j_))),
            "QUIT would discard accepted but unapplied ENERGY");
    Json energy = energy_json(applied_activity_j_, static_input_j_,
                              stored_energy(), boundary_loss_j_);
    energy["node_mapped_activity_input_j"] = applied_node_energy_.sum();
    energy["component_mapping_error_j"] =
        static_cast<double>(applied_node_energy_.sum() - applied_activity_j_);
    return {{"type", "BYE"}, {"time_ns", current_step_ * options_.step_ns},
            {"energy_j", std::move(energy)}};
  }

 private:
  void verify_hash(const std::string& text, const std::string& expected,
                   const std::string& label) {
    require(expected.size() == 64 &&
                std::all_of(expected.begin(), expected.end(), [](unsigned char value) {
                  return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
                }), label + " SHA-256 must be lowercase hexadecimal");
    require(sha256(text) == expected, label + " SHA-256 mismatch");
  }

  void parse_grid(const Json& grid) {
    const auto& cells = grid.at("cells");
    require(cells.is_array() && cells.size() == config_.nodes.size(),
            "grid/model node count mismatch");
    cell_volumes_.resize(cells.size());
    for (std::size_t index = 0; index < cells.size(); ++index) {
      const auto& cell = cells.at(index);
      require(cell.at("index").get<std::size_t>() == index &&
                  cell.at("id").get<std::string>() == config_.nodes[index].id,
              "grid/model node ordering mismatch");
      const double volume = cell.at("volume_m3").get<double>();
      const double capacity = cell.at("capacity_j_k").get<double>();
      require(std::isfinite(volume) && volume > 0, "invalid grid cell volume");
      require(std::isfinite(capacity) &&
                  std::abs(capacity - config_.nodes[index].heat_capacity_j_per_k) <=
                      1e-12 * std::max(1.0, std::abs(capacity)),
              "grid/model capacity mismatch");
      cell_volumes_[index] = volume;
    }
    std::vector<unsigned> ownership(cells.size());
    for (const auto& [id, indices] : grid.at("component_cells").items()) {
      if (id == "__background__") continue;
      Component component;
      component.id = id;
      double volume = 0;
      for (const auto& item : indices) {
        const std::size_t index = item.get<std::size_t>();
        require(index < cells.size(), "component cell index outside grid");
        require(cells.at(index).at("component") == id,
                "component ownership disagrees with grid cell");
        require(++ownership[index] == 1, "grid cell has duplicate component ownership");
        component.cells.push_back(index);
        volume += cell_volumes_[index];
      }
      require(!component.cells.empty() && std::isfinite(volume) && volume > 0,
              "empty or invalid component mapping");
      for (const auto index : component.cells)
        component.volume_weights.push_back(cell_volumes_[index] / volume);
      component_index_.emplace(id, components_.size());
      components_.push_back(std::move(component));
    }
    require(!components_.empty(), "grid contains no observable entities");
  }

  void parse_sensors(const Json& definitions) {
    require(definitions.is_array(), "sensor mapping must be an array");
    std::set<std::string> ids;
    for (const auto& definition : definitions) {
      Sensor sensor;
      sensor.id = definition.at("id").get<std::string>();
      require(!sensor.id.empty() && ids.insert(sensor.id).second,
              "duplicate or empty sensor id");
      const std::string reduction = definition.at("reduction").get<std::string>();
      if (reduction == "max") {
        sensor.maximum = true;
        for (const auto& item : definition.at("cell_indices"))
          sensor.cells.push_back(item.get<std::size_t>());
      } else if (reduction == "weighted_mean") {
        double total = 0;
        for (const auto& item : definition.at("cell_weights")) {
          require(item.is_array() && item.size() == 2,
                  "sensor cell weight must be [index,weight]");
          sensor.cells.push_back(item.at(0).get<std::size_t>());
          const double weight = item.at(1).get<double>();
          require(std::isfinite(weight) && weight >= 0, "invalid sensor weight");
          sensor.weights.push_back(weight);
          total += weight;
        }
        require(std::abs(total - 1.0) <= 1e-9, "sensor weights do not sum to one");
      } else {
        fail("unsupported sensor reduction: " + reduction);
      }
      require(!sensor.cells.empty(), "empty sensor mapping");
      for (const auto index : sensor.cells)
        require(index < config_.nodes.size(), "sensor cell outside model");
      sensors_.push_back(std::move(sensor));
    }
  }

  void advance_one() {
    const Tick trial_step = current_step_ + 1;
    Eigen::VectorXd power = Eigen::VectorXd::Zero(theta_.size());
    long double trial_activity = 0;
    for (const auto& interval : intervals_) {
      if (interval.start_step > current_step_ || interval.end_step <= current_step_) continue;
      const double duration_s =
          static_cast<double>(interval.end_step - interval.start_step) * step_s_;
      const double component_power = interval.energy_j / duration_s;
      const auto& component = components_[interval.component];
      for (std::size_t position = 0; position < component.cells.size(); ++position)
        power[static_cast<Eigen::Index>(component.cells[position])] +=
            component_power * component.volume_weights[position];
      trial_activity += component_power * step_s_;
    }
    const Eigen::VectorXd rhs =
        capacity_over_dt_.cwiseProduct(theta_) + static_and_boundary_ + power;
    const Eigen::VectorXd trial_theta = solver_.solve(rhs);
    if (solver_.info() != Eigen::Success || !trial_theta.allFinite())
      throw_trial("NUMERICAL_FAILURE", "sparse LDLT solve failed", nullptr, power,
                  trial_activity, trial_step);
    const Eigen::VectorXd trial_temperature = trial_theta.array() + origin_k_;
    if (!trial_temperature.allFinite() || trial_temperature.minCoeff() < options_.minimum_k ||
        trial_temperature.maxCoeff() > options_.maximum_k)
      throw_trial("DOMAIN_FAILURE", "temperature left required domain",
                  &trial_temperature, power, trial_activity, trial_step);
    theta_ = trial_theta;
    temperature_ = trial_temperature;
    applied_node_energy_ += power * step_s_;
    applied_activity_j_ += trial_activity;
    static_input_j_ += static_power_w_ * step_s_;
    for (Eigen::Index index = 0; index < theta_.size(); ++index) {
      const auto& node = config_.nodes[static_cast<std::size_t>(index)];
      boundary_loss_j_ += static_cast<long double>(step_s_) *
                          node.boundary_conductance_w_per_k *
                          (static_cast<long double>(theta_[index]) +
                           (origin_k_ - node.boundary_temperature_k));
    }
    current_step_ = trial_step;
  }

  [[noreturn]] void throw_trial(const std::string& status, const std::string& reason,
                                const Eigen::VectorXd* trial,
                                const Eigen::VectorXd& power,
                                long double trial_activity, Tick trial_step) const {
    Json completed_energy = energy_json(applied_activity_j_, static_input_j_,
                                        stored_energy(), boundary_loss_j_);
    completed_energy["node_mapped_activity_input_j"] = applied_node_energy_.sum();
    completed_energy["component_mapping_error_j"] =
        static_cast<double>(applied_node_energy_.sum() - applied_activity_j_);
    Json evidence = {{"type", "ERROR"}, {"status", status}, {"reason", reason},
                     {"failure_returned_to_caller", true},
                     {"temperature_clamping", false},
                     {"last_valid_time_ns", current_step_ * options_.step_ns},
                     {"trial_target_time_ns", trial_step * options_.step_ns},
                     {"last_valid_temperature_range_k",
                      {temperature_.minCoeff(), temperature_.maxCoeff()}},
                     {"completed_energy_j", std::move(completed_energy)},
                     {"failed_trial_step_energy",
                      {{"status", "UNKNOWN_NOT_INTEGRATED"},
                       {"activity_input_j", nullptr}, {"static_input_j", nullptr},
                       {"stored_energy_change_j", nullptr}, {"boundary_loss_j", nullptr},
                       {"energy_residual_j", nullptr}}},
                     {"trial_declared_activity_energy_j", static_cast<double>(trial_activity)},
                     {"trial_activity_power_w", power.sum()},
                     {"domain_k", {options_.minimum_k, options_.maximum_k}}};
    if (trial) {
      evidence["trial_temperature_range_k"] = {trial->minCoeff(), trial->maxCoeff()};
      evidence["offending_nodes"] = Json::array();
      for (Eigen::Index index = 0; index < trial->size(); ++index) {
        const double value = (*trial)[index];
        if (!std::isfinite(value) || value < options_.minimum_k || value > options_.maximum_k)
          evidence["offending_nodes"].push_back(
              {{"index", index},
               {"id", config_.nodes[static_cast<std::size_t>(index)].id},
               {"temperature_k", std::isfinite(value) ? Json(value) : Json(nullptr)}});
      }
    } else {
      evidence["trial_temperature_range_k"] = nullptr;
      evidence["offending_nodes"] = nullptr;
    }
    throw TrialFailure(std::move(evidence));
  }

  Json entity_temperatures() const {
    Json result = Json::object();
    for (const auto& component : components_) {
      double mean = 0;
      double hotspot = -std::numeric_limits<double>::infinity();
      for (std::size_t position = 0; position < component.cells.size(); ++position) {
        const double value = temperature_[static_cast<Eigen::Index>(component.cells[position])];
        mean += value * component.volume_weights[position];
        hotspot = std::max(hotspot, value);
      }
      result[component.id] = {{"mean_k", mean}, {"hotspot_k", hotspot}};
    }
    return result;
  }

  Json sensor_temperatures() const {
    Json result = Json::object();
    for (const auto& sensor : sensors_) {
      double value = sensor.maximum ? -std::numeric_limits<double>::infinity() : 0;
      for (std::size_t position = 0; position < sensor.cells.size(); ++position) {
        const double temperature =
            temperature_[static_cast<Eigen::Index>(sensor.cells[position])];
        if (sensor.maximum) value = std::max(value, temperature);
        else value += temperature * sensor.weights[position];
      }
      result[sensor.id] = value;
    }
    return result;
  }

  long double stored_energy() const {
    long double result = 0;
    for (Eigen::Index index = 0; index < theta_.size(); ++index) {
      const auto& node = config_.nodes[static_cast<std::size_t>(index)];
      result += node.heat_capacity_j_per_k *
                (static_cast<long double>(theta_[index]) -
                 (node.initial_temperature_k - origin_k_));
    }
    return result;
  }

  Json energy_json(long double activity, long double static_input,
                   long double stored, long double boundary) const {
    return {{"activity_input_j", static_cast<double>(activity)},
            {"static_input_j", static_cast<double>(static_input)},
            {"total_input_j", static_cast<double>(activity + static_input)},
            {"stored_energy_change_j", static_cast<double>(stored)},
            {"boundary_loss_j", static_cast<double>(boundary)},
            {"energy_residual_j",
             static_cast<double>(activity + static_input - stored - boundary)}};
  }

 public:
  class TrialFailure : public std::exception {
   public:
    explicit TrialFailure(Json evidence) : evidence_(std::move(evidence)) {}
    const char* what() const noexcept override { return "thermal trial failed"; }
    const Json& evidence() const noexcept { return evidence_; }
   private:
    Json evidence_;
  };

 private:
  Options options_;
  std::string model_text_, grid_text_, sensors_text_;
  ThermalModelConfig config_;
  double step_s_{}, origin_k_{}, static_power_w_{}, factor_seconds_{};
  SparseMatrix matrix_;
  SparseSolver solver_;
  Eigen::VectorXd temperature_, theta_, capacity_over_dt_, static_and_boundary_;
  Eigen::VectorXd applied_node_energy_;
  std::vector<double> cell_volumes_;
  std::vector<Component> components_;
  std::map<std::string, std::size_t> component_index_;
  std::vector<Sensor> sensors_;
  std::vector<Interval> intervals_;
  std::set<std::tuple<Tick, Tick, std::size_t>> interval_identities_;
  Tick current_step_{};
  long double accepted_activity_j_{}, applied_activity_j_{}, static_input_j_{};
  long double boundary_loss_j_{};
};

double write_response(Json response, double previous_write_seconds,
                      double input_parse_seconds = 0) {
  response["timing"]["input_parse_seconds"] = input_parse_seconds;
  response["timing"]["previous_stdout_write_seconds"] = previous_write_seconds;
  const auto serialization_start = Clock::now();
  (void)response.dump();
  response["timing"]["serialization_probe_seconds"] =
      seconds(Clock::now() - serialization_start);
  const std::string line = response.dump();
  const auto write_start = Clock::now();
  std::cout << line << '\n' << std::flush;
  if (!std::cout) throw std::runtime_error("stdout write failed");
  return seconds(Clock::now() - write_start);
}

}  // namespace

int main(int argc, char** argv) try {
  const Options options = parse_options(argc, argv);
  Service service(options, read_file(options.model_path), read_file(options.grid_path),
                  read_file(options.sensors_path));
  double previous_write = write_response(service.ready(), 0);
  for (std::string line; std::getline(std::cin, line);) {
    if (line.empty()) continue;
    const auto parse_start = Clock::now();
    std::istringstream input(line);
    std::string command;
    input >> command;
    Json response;
    bool done = false;
    double parse_seconds = 0;
    if (command == "ENERGY") {
      std::string start, end, component, energy, extra;
      require(static_cast<bool>(input >> start >> end >> component >> energy) && !(input >> extra),
              "ENERGY syntax: ENERGY START_NS END_NS COMPONENT ENERGY_J");
      parse_seconds = seconds(Clock::now() - parse_start);
      response = service.energy(unsigned_value(start, "ENERGY start"),
                                unsigned_value(end, "ENERGY end"), component,
                                finite_nonnegative(energy, "ENERGY joules"));
    } else if (command == "ADVANCE") {
      std::string target, extra;
      require(static_cast<bool>(input >> target) && !(input >> extra),
              "ADVANCE syntax: ADVANCE TARGET_NS");
      parse_seconds = seconds(Clock::now() - parse_start);
      response = service.advance(unsigned_value(target, "ADVANCE target"));
    } else if (command == "QUIT") {
      std::string extra;
      require(!(input >> extra), "QUIT takes no arguments");
      parse_seconds = seconds(Clock::now() - parse_start);
      response = service.quit();
      done = true;
    } else {
      fail("unknown protocol command: " + command);
    }
    previous_write = write_response(std::move(response), previous_write, parse_seconds);
    if (done) return 0;
  }
  fail("stdin ended without QUIT");
} catch (const Service::TrialFailure& failure) {
  try {
    (void)write_response(failure.evidence(), 0);
  } catch (...) {
  }
  return 2;
} catch (const std::exception& error) {
  try {
    (void)write_response({{"type", "ERROR"}, {"status", "INPUT_OR_PROTOCOL_FAILURE"},
                          {"reason", error.what()}, {"state_commit", "NO_TRIAL_COMMIT"}}, 0);
  } catch (...) {
  }
  std::cerr << "eq3_maintenance_thermal_service: " << error.what() << '\n';
  return 2;
}
