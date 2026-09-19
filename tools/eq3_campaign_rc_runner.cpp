// Isolated sparse implicit-Euler runner for the authorized EQ3 P2 campaign.
// It reuses only the public text parser/config types; no simulator-core change.
#include "hbfsim/eq3_thermal/thermal.hpp"

#include <Eigen/Sparse>
#include <Eigen/SparseCholesky>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using hbfsim::eq3_thermal::ConductanceKind;
using hbfsim::eq3_thermal::PhysicalActivity;
using hbfsim::eq3_thermal::ThermalModelConfig;
using SparseMatrix = Eigen::SparseMatrix<double>;
using SparseSolver = Eigen::SimplicialLDLT<SparseMatrix, Eigen::Lower,
                                           Eigen::AMDOrdering<int>>;

struct Options {
  std::string model_path, events_path;
  double step_s{}, slot_s{}, end_s{}, sample_s{}, min_k{}, max_k{};
  bool inspect_only{}, run{};
};

struct Schedule {
  std::uint64_t steps_per_slot{}, slot_count{}, total_steps{};
  std::uint64_t steps_per_sample{}, sample_count{};
  double max_target_grid_error_s{}, max_dt_label_error_s{};
};

[[noreturn]] void fail(const std::string& message) {
  throw std::invalid_argument(message);
}
void require(bool condition, const std::string& message) {
  if (!condition) fail(message);
}

std::string read_file(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path);
  std::ostringstream result;
  result << input.rdbuf();
  if (!input.good() && !input.eof()) throw std::runtime_error("failed reading " + path);
  return result.str();
}

double number(const std::string& text, const std::string& option) {
  std::size_t used{};
  double value{};
  try {
    value = std::stod(text, &used);
  } catch (const std::exception&) {
    fail(option + " requires a finite number");
  }
  require(used == text.size() && std::isfinite(value), option + " requires a finite number");
  return value;
}

Options options_from(int argc, char** argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string argument = argv[index];
    if (argument == "--inspect-only") {
      require(!options.inspect_only, "duplicate --inspect-only");
      options.inspect_only = true;
      continue;
    }
    if (argument == "--run") {
      require(!options.run, "duplicate --run");
      options.run = true;
      continue;
    }
    const bool takes_value =
        argument == "--model" || argument == "--events" || argument == "--step-s" ||
        argument == "--slot-s" || argument == "--end-s" || argument == "--sample-s" ||
        argument == "--min-k" || argument == "--max-k";
    require(takes_value, "unknown argument " + argument);
    require(index + 1 < argc, "missing value after " + argument);
    const std::string value = argv[++index];
    if (argument == "--model") options.model_path = value;
    else if (argument == "--events") options.events_path = value;
    else if (argument == "--step-s") options.step_s = number(value, argument);
    else if (argument == "--slot-s") options.slot_s = number(value, argument);
    else if (argument == "--end-s") options.end_s = number(value, argument);
    else if (argument == "--sample-s") options.sample_s = number(value, argument);
    else if (argument == "--min-k") options.min_k = number(value, argument);
    else if (argument == "--max-k") options.max_k = number(value, argument);
  }
  require(options.inspect_only != options.run,
          "exactly one of --inspect-only or --run is required; execution is default-off");
  require(!options.model_path.empty() && !options.events_path.empty(),
          "--model and --events are required");
  require(options.step_s > 0 && options.slot_s > 0 && options.end_s > 0 &&
              options.sample_s > 0,
          "step, slot, end, and sample seconds must be positive");
  require(options.min_k > 0 && options.max_k > options.min_k,
          "--min-k and --max-k must satisfy 0 < min < max");
  return options;
}

std::uint64_t ratio(double whole, double part, const std::string& label) {
  const double raw = whole / part;
  require(std::isfinite(raw) && raw >= 1.0 &&
              raw <= static_cast<double>(std::numeric_limits<std::uint64_t>::max()),
          label + " ratio is outside uint64 range");
  const double rounded = std::round(raw);
  require(std::abs(raw - rounded) <= 1e-12 * std::max(1.0, std::abs(raw)),
          label + " must divide exactly within floating-input tolerance");
  return static_cast<std::uint64_t>(rounded);
}

Schedule schedule_for(const Options& options) {
  Schedule schedule;
  schedule.steps_per_slot = ratio(options.slot_s, options.step_s, "step into slot");
  schedule.slot_count = ratio(options.end_s, options.slot_s, "slot into end");
  schedule.steps_per_sample = ratio(options.sample_s, options.step_s, "step into sample");
  schedule.sample_count = ratio(options.end_s, options.sample_s, "sample into end");
  require(schedule.slot_count <= std::numeric_limits<std::uint64_t>::max() /
                                     schedule.steps_per_slot,
          "total steps overflow uint64");
  schedule.total_steps = schedule.slot_count * schedule.steps_per_slot;
  double previous = 0.0;
  std::uint64_t global = 0;
  for (std::uint64_t slot = 0; slot < schedule.slot_count; ++slot) {
    for (std::uint64_t local = 1; local <= schedule.steps_per_slot; ++local) {
      ++global;
      const double target = local == schedule.steps_per_slot
                                ? static_cast<double>(slot + 1) * options.slot_s
                                : static_cast<double>(slot) * options.slot_s +
                                      static_cast<double>(local) * options.step_s;
      require(target > previous, "constructed schedule is not monotonic");
      schedule.max_target_grid_error_s =
          std::max(schedule.max_target_grid_error_s,
                   std::abs(target - static_cast<double>(global) * options.step_s));
      schedule.max_dt_label_error_s =
          std::max(schedule.max_dt_label_error_s, std::abs(target - previous - options.step_s));
      previous = target;
    }
  }
  return schedule;
}

double slot_error(double time, double slot) {
  return std::abs(time - std::round(time / slot) * slot);
}

double validate_activities(const std::vector<PhysicalActivity>& activities,
                           const Options& options) {
  const double tolerance = 1e-12 * std::max(1.0, options.end_s);
  double maximum = 0.0;
  std::set<std::uint64_t> activity_ids;
  for (const auto& activity : activities) {
    require(activity_ids.insert(activity.activity_id).second,
            "duplicate activity id would double-count energy: " +
                std::to_string(activity.activity_id));
    const double start_error = slot_error(activity.start_time_s, options.slot_s);
    const double end_error = slot_error(activity.end_time_s, options.slot_s);
    maximum = std::max({maximum, start_error, end_error});
    require(start_error <= tolerance && end_error <= tolerance,
            "activity start/end must align with power slots: " +
                std::to_string(activity.activity_id));
    require(activity.start_time_s >= 0 && activity.end_time_s <= options.end_s + tolerance,
            "activity lies outside [0,end]: " + std::to_string(activity.activity_id));
    require(activity.completion_time_s <= options.end_s + tolerance,
            "activity completion lies after end: " + std::to_string(activity.activity_id));
  }
  return maximum;
}

std::size_t structural_nnz(const ThermalModelConfig& config) {
  std::set<std::pair<std::size_t, std::size_t>> connections;
  for (const auto& edge : config.edges) {
    if (!config.direct_intercomponent_edges_enabled &&
        edge.kind == ConductanceKind::InterComponent)
      continue;
    connections.emplace(std::min(edge.node_a, edge.node_b),
                        std::max(edge.node_a, edge.node_b));
  }
  return config.nodes.size() + 2 * connections.size();
}

long double declared_activity_energy(const std::vector<PhysicalActivity>& activities) {
  long double result = 0;
  for (const auto& activity : activities)
    for (const auto& assignment : activity.node_energy) result += assignment.energy_j;
  return result;
}

long double static_energy(const ThermalModelConfig& config, double end_s) {
  long double result = 0;
  for (const auto& node : config.nodes) result += node.static_power_w * end_s;
  return result;
}

void validate_initial_domain(const ThermalModelConfig& config, const Options& options) {
  for (const auto& node : config.nodes)
    require(node.initial_temperature_k >= options.min_k &&
                node.initial_temperature_k <= options.max_k,
            "initial node temperature is outside domain: " + node.id);
}

void inspect(const ThermalModelConfig& config,
             const std::vector<PhysicalActivity>& activities,
             const Options& options, const Schedule& schedule,
             double activity_alignment_error_s) {
  const std::size_t nnz = structural_nnz(config);
  const long double compressed_matrix_bytes =
      static_cast<long double>(nnz) * (sizeof(double) + sizeof(int)) +
      static_cast<long double>(config.nodes.size() + 1) * sizeof(int);
  const long double rows = static_cast<long double>(config.nodes.size()) *
                           static_cast<long double>(schedule.sample_count + 1);
  const long double activity_energy = declared_activity_energy(activities);
  const long double static_input = static_energy(config, options.end_s);
  std::cout << std::setprecision(17)
            << "{\n"
            << "  \"mode\": \"inspect_only\",\n"
            << "  \"solver_constructed\": false,\n"
            << "  \"backend\": \"Eigen::SimplicialLDLT_AMD\",\n"
            << "  \"factor_cache_limit\": 1,\n"
            << "  \"nodes\": " << config.nodes.size() << ",\n"
            << "  \"edges\": " << config.edges.size() << ",\n"
            << "  \"matrix_structural_nnz\": " << nnz << ",\n"
            << "  \"compressed_matrix_bytes_excluding_factor\": "
            << compressed_matrix_bytes << ",\n"
            << "  \"factor_fill_memory_unmeasured_until_pilot\": true,\n"
            << "  \"activities\": " << activities.size() << ",\n"
            << "  \"total_steps\": " << schedule.total_steps << ",\n"
            << "  \"estimated_csv_rows_including_t0\": " << rows << ",\n"
            << "  \"activity_declared_energy_j\": " << activity_energy << ",\n"
            << "  \"static_input_energy_j\": " << static_input << ",\n"
            << "  \"total_declared_input_energy_j\": " << activity_energy + static_input << ",\n"
            << "  \"max_event_slot_alignment_error_s\": "
            << activity_alignment_error_s << ",\n"
            << "  \"max_target_grid_error_s\": " << schedule.max_target_grid_error_s << ",\n"
            << "  \"max_dt_label_error_s\": " << schedule.max_dt_label_error_s << "\n"
            << "}\n";
}

SparseMatrix system_matrix(const ThermalModelConfig& config, double step_s) {
  require(config.nodes.size() <= static_cast<std::size_t>(std::numeric_limits<int>::max()),
          "node count exceeds Eigen int index range");
  const int n = static_cast<int>(config.nodes.size());
  std::vector<Eigen::Triplet<double>> entries;
  entries.reserve(config.nodes.size() + 4 * config.edges.size());
  for (int index = 0; index < n; ++index) {
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    entries.emplace_back(index, index,
                         node.heat_capacity_j_per_k / step_s +
                             node.boundary_conductance_w_per_k);
  }
  for (const auto& edge : config.edges) {
    if (!config.direct_intercomponent_edges_enabled &&
        edge.kind == ConductanceKind::InterComponent)
      continue;
    const int a = static_cast<int>(edge.node_a);
    const int b = static_cast<int>(edge.node_b);
    const double conductance = edge.conductance_w_per_k;
    entries.emplace_back(a, a, conductance);
    entries.emplace_back(b, b, conductance);
    entries.emplace_back(a, b, -conductance);
    entries.emplace_back(b, a, -conductance);
  }
  SparseMatrix matrix(n, n);
  matrix.setFromTriplets(entries.begin(), entries.end());
  matrix.makeCompressed();
  return matrix;
}

std::vector<Eigen::VectorXd> slot_powers(
    const ThermalModelConfig& config,
    const std::vector<PhysicalActivity>& activities,
    const Options& options, const Schedule& schedule) {
  std::vector<Eigen::VectorXd> powers(
      static_cast<std::size_t>(schedule.slot_count),
      Eigen::VectorXd::Zero(static_cast<Eigen::Index>(config.nodes.size())));
  const double tolerance = 1e-12 * std::max(1.0, options.end_s);
  for (const auto& activity : activities) {
    const auto start = static_cast<std::uint64_t>(std::llround(activity.start_time_s /
                                                               options.slot_s));
    const auto end = static_cast<std::uint64_t>(std::llround(activity.end_time_s /
                                                             options.slot_s));
    require(start < end && end <= schedule.slot_count,
            "invalid canonical activity slot range");
    const double duration = static_cast<double>(end - start) * options.slot_s;
    require(std::abs(duration - (activity.end_time_s - activity.start_time_s)) <= tolerance,
            "canonical activity duration mismatch");
    for (std::uint64_t slot = start; slot < end; ++slot)
      for (const auto& assignment : activity.node_energy)
        powers[static_cast<std::size_t>(slot)]
              [static_cast<Eigen::Index>(assignment.node_index)] +=
            assignment.energy_j / duration;
  }
  return powers;
}

void emit(double time_s, const ThermalModelConfig& config,
          const Eigen::VectorXd& temperature) {
  for (std::size_t index = 0; index < config.nodes.size(); ++index)
    std::cout << time_s << ',' << config.nodes[index].id << ','
              << temperature[static_cast<Eigen::Index>(index)] << '\n';
}

void receipt(const Options& options, const Schedule& schedule,
             const ThermalModelConfig& config, std::size_t matrix_nnz,
             double alignment_error_s, double min_observed_k,
             double max_observed_k, long double declared_energy_j,
             long double applied_energy_j, long double stored_energy_j,
             long double boundary_loss_j) {
  const std::filesystem::path final{"rc_energy_receipt.json"};
  const std::filesystem::path temporary{"rc_energy_receipt.json.tmp"};
  require(!std::filesystem::exists(final) && !std::filesystem::exists(temporary),
          "refusing to overwrite RC energy receipt");
  const long double static_input = static_energy(config, options.end_s);
  const long double total_input = applied_energy_j + static_input;
  const long double residual = total_input - stored_energy_j - boundary_loss_j;
  std::ofstream output(temporary, std::ios::out | std::ios::trunc);
  if (!output) throw std::runtime_error("cannot create energy receipt");
  output << std::setprecision(17)
         << "{\n"
         << "  \"schema_version\": \"eq3-campaign-sparse-rc-energy-v1\",\n"
         << "  \"backend\": \"Eigen::SimplicialLDLT_AMD\",\n"
         << "  \"factorization_count\": 1,\n"
         << "  \"factor_cache_limit\": 1,\n"
         << "  \"matrix_structural_nnz\": " << matrix_nnz << ",\n"
         << "  \"nodes\": " << config.nodes.size() << ",\n"
         << "  \"total_steps\": " << schedule.total_steps << ",\n"
         << "  \"sample_count_excluding_t0\": " << schedule.sample_count << ",\n"
         << "  \"t0_emitted\": true,\n"
         << "  \"step_s\": " << options.step_s << ",\n"
         << "  \"slot_s\": " << options.slot_s << ",\n"
         << "  \"sample_s\": " << options.sample_s << ",\n"
         << "  \"end_s\": " << options.end_s << ",\n"
         << "  \"min_k\": " << options.min_k << ",\n"
         << "  \"max_k\": " << options.max_k << ",\n"
         << "  \"min_observed_k\": " << min_observed_k << ",\n"
         << "  \"max_observed_k\": " << max_observed_k << ",\n"
         << "  \"max_event_slot_alignment_error_s\": " << alignment_error_s << ",\n"
         << "  \"max_target_grid_error_s\": " << schedule.max_target_grid_error_s << ",\n"
         << "  \"max_dt_label_error_s\": " << schedule.max_dt_label_error_s << ",\n"
         << "  \"activity_declared_energy_j\": " << declared_energy_j << ",\n"
         << "  \"activity_applied_energy_j\": " << applied_energy_j << ",\n"
         << "  \"activity_energy_difference_j\": "
         << applied_energy_j - declared_energy_j << ",\n"
         << "  \"static_input_energy_j\": " << static_input << ",\n"
         << "  \"total_input_energy_j\": " << total_input << ",\n"
         << "  \"stored_energy_change_j\": " << stored_energy_j << ",\n"
         << "  \"boundary_loss_j\": " << boundary_loss_j << ",\n"
         << "  \"energy_residual_j\": " << residual << "\n"
         << "}\n";
  output.close();
  if (!output) throw std::runtime_error("failed writing energy receipt");
  std::filesystem::rename(temporary, final);
}

void run(const ThermalModelConfig& config,
         const std::vector<PhysicalActivity>& activities,
         const Options& options, const Schedule& schedule,
         double alignment_error_s) {
  require(!std::filesystem::exists("rc_energy_receipt.json") &&
              !std::filesystem::exists("rc_energy_receipt.json.tmp"),
          "refusing to overwrite RC energy receipt");
  const SparseMatrix matrix = system_matrix(config, options.step_s);
  SparseSolver solver;
  solver.compute(matrix);
  require(solver.info() == Eigen::Success, "sparse LDLT factorization failed");
  const auto powers = slot_powers(config, activities, options, schedule);
  const Eigen::Index n = static_cast<Eigen::Index>(config.nodes.size());
  Eigen::VectorXd temperature(n), capacity_over_dt(n), static_and_boundary(n);
  Eigen::VectorXd applied = Eigen::VectorXd::Zero(n);
  for (Eigen::Index index = 0; index < n; ++index) {
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    temperature[index] = node.initial_temperature_k;
    capacity_over_dt[index] = node.heat_capacity_j_per_k / options.step_s;
    static_and_boundary[index] =
        node.static_power_w + node.boundary_conductance_w_per_k *
                                  node.boundary_temperature_k;
  }
  double minimum = temperature.minCoeff(), maximum = temperature.maxCoeff();
  const auto check_domain = [&] {
    require(temperature.allFinite(), "sparse solve produced nonfinite temperature");
    minimum = std::min(minimum, temperature.minCoeff());
    maximum = std::max(maximum, temperature.maxCoeff());
    require(temperature.minCoeff() >= options.min_k &&
                temperature.maxCoeff() <= options.max_k,
            "temperature left required domain");
  };
  std::cout << "time_s,node_id,temperature_k\n" << std::setprecision(17);
  emit(0.0, config, temperature);
  long double boundary_loss = 0;
  std::uint64_t global = 0;
  for (std::uint64_t slot = 0; slot < schedule.slot_count; ++slot) {
    const auto& power = powers[static_cast<std::size_t>(slot)];
    for (std::uint64_t local = 1; local <= schedule.steps_per_slot; ++local) {
      ++global;
      const Eigen::VectorXd rhs =
          capacity_over_dt.cwiseProduct(temperature) + static_and_boundary + power;
      temperature = solver.solve(rhs);
      require(solver.info() == Eigen::Success, "sparse LDLT solve failed");
      check_domain();
      for (Eigen::Index index = 0; index < n; ++index) {
        const auto& node = config.nodes[static_cast<std::size_t>(index)];
        boundary_loss += static_cast<long double>(options.step_s) *
                         node.boundary_conductance_w_per_k *
                         (temperature[index] - node.boundary_temperature_k);
      }
      applied += power * options.step_s;
      if (global % schedule.steps_per_sample == 0) {
        const double label = local == schedule.steps_per_slot
                                 ? static_cast<double>(slot + 1) * options.slot_s
                                 : static_cast<double>(slot) * options.slot_s +
                                       static_cast<double>(local) * options.step_s;
        emit(label, config, temperature);
      }
    }
  }
  long double applied_energy = 0, stored = 0;
  for (Eigen::Index index = 0; index < n; ++index) {
    applied_energy += applied[index];
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    stored += node.heat_capacity_j_per_k *
              (temperature[index] - node.initial_temperature_k);
  }
  receipt(options, schedule, config, static_cast<std::size_t>(matrix.nonZeros()),
          alignment_error_s, minimum, maximum,
          declared_activity_energy(activities), applied_energy, stored,
          boundary_loss);
}
}  // namespace

int main(int argc, char** argv) try {
  const Options options = options_from(argc, argv);
  const auto config = hbfsim::eq3_thermal::model_config_from_text(read_file(options.model_path));
  const auto activities =
      hbfsim::eq3_thermal::activities_from_text(read_file(options.events_path), config);
  validate_initial_domain(config, options);
  const Schedule schedule = schedule_for(options);
  const double alignment_error = validate_activities(activities, options);
  if (options.inspect_only) {
    inspect(config, activities, options, schedule, alignment_error);
    return 0;
  }
  run(config, activities, options, schedule, alignment_error);
  return 0;
} catch (const std::exception& error) {
  std::cerr << "eq3_campaign_rc_runner: " << error.what() << '\n';
  return 2;
}
