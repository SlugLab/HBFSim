// Isolated sparse implicit-Euler runner for the authorized EQ3 P2 campaign.
// It reuses only the public text parser/config types; no simulator-core change.
#include "hbfsim/eq3_thermal/thermal.hpp"

#include <Eigen/Sparse>
#include <Eigen/SparseCholesky>

#include <algorithm>
#include <cmath>
#include <chrono>
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

std::string json_string(const std::string& value);

struct Options {
  std::string model_path, events_path;
  std::string model_sha256{"UNKNOWN_NOT_SUPPLIED"};
  std::string events_sha256{"UNKNOWN_NOT_SUPPLIED"};
  std::string runner_source_sha256{"UNKNOWN_NOT_SUPPLIED"};
  std::string domain_version{"eq3-runner-cli-temperature-domain-v1"};
  double step_s{}, slot_s{}, end_s{}, sample_s{}, min_k{}, max_k{};
  double envelope_limit_k{};
  bool inspect_only{}, run{}, equilibrium_diagnostic{}, steady_envelope{};
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
    if (argument == "--equilibrium-diagnostic") {
      require(!options.equilibrium_diagnostic, "duplicate diagnostic flag");
      options.equilibrium_diagnostic = true;
      continue;
    }
    if (argument == "--steady-envelope") {
      require(!options.steady_envelope, "duplicate --steady-envelope");
      options.steady_envelope = true;
      continue;
    }
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
        argument == "--min-k" || argument == "--max-k" ||
        argument == "--envelope-limit-k" ||
        argument == "--model-sha256" || argument == "--events-sha256" ||
        argument == "--runner-source-sha256" || argument == "--domain-version";
    require(takes_value, "unknown argument " + argument);
    require(index + 1 < argc, "missing value after " + argument);
    const std::string value = argv[++index];
    if (argument == "--model") options.model_path = value;
    else if (argument == "--events") options.events_path = value;
    else if (argument == "--model-sha256") options.model_sha256 = value;
    else if (argument == "--events-sha256") options.events_sha256 = value;
    else if (argument == "--runner-source-sha256") options.runner_source_sha256 = value;
    else if (argument == "--domain-version") options.domain_version = value;
    else if (argument == "--step-s") options.step_s = number(value, argument);
    else if (argument == "--slot-s") options.slot_s = number(value, argument);
    else if (argument == "--end-s") options.end_s = number(value, argument);
    else if (argument == "--sample-s") options.sample_s = number(value, argument);
    else if (argument == "--min-k") options.min_k = number(value, argument);
    else if (argument == "--max-k") options.max_k = number(value, argument);
    else if (argument == "--envelope-limit-k")
      options.envelope_limit_k = number(value, argument);
  }
  require(int(options.inspect_only) + int(options.run) +
              int(options.equilibrium_diagnostic) + int(options.steady_envelope) == 1,
          "exactly one of --inspect-only, --equilibrium-diagnostic, --steady-envelope or --run is required");
  require(!options.model_path.empty() && !options.events_path.empty(),
          "--model and --events are required");
  require(options.step_s > 0 && options.slot_s > 0 && options.end_s > 0 &&
              options.sample_s > 0,
          "step, slot, end, and sample seconds must be positive");
  require(options.min_k > 0 && options.max_k > options.min_k,
          "--min-k and --max-k must satisfy 0 < min < max");
  require(!options.steady_envelope || options.envelope_limit_k > 0,
          "--steady-envelope requires positive --envelope-limit-k");
  require(!options.model_sha256.empty() && !options.events_sha256.empty() &&
              !options.runner_source_sha256.empty() && !options.domain_version.empty(),
          "diagnostic identity values must not be empty");
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

SparseMatrix steady_matrix(const ThermalModelConfig& config) {
  require(config.nodes.size() <= static_cast<std::size_t>(std::numeric_limits<int>::max()),
          "node count exceeds Eigen int index range");
  const int n = static_cast<int>(config.nodes.size());
  std::vector<Eigen::Triplet<double>> entries;
  entries.reserve(config.nodes.size() + 4 * config.edges.size());
  double boundary_total = 0;
  for (int index = 0; index < n; ++index) {
    const double boundary =
        config.nodes[static_cast<std::size_t>(index)].boundary_conductance_w_per_k;
    entries.emplace_back(index, index, boundary);
    boundary_total += boundary;
  }
  require(boundary_total > 0, "steady envelope requires at least one heat-rejection boundary");
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

void steady_envelope(const ThermalModelConfig& config,
                     const std::vector<PhysicalActivity>& activities,
                     const Options& options) {
  const Eigen::Index n = static_cast<Eigen::Index>(config.nodes.size());
  Eigen::VectorXd fixed_rhs(n), cap_power = Eigen::VectorXd::Zero(n);
  for (Eigen::Index index = 0; index < n; ++index) {
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    fixed_rhs[index] = node.static_power_w +
                       node.boundary_conductance_w_per_k * node.boundary_temperature_k;
  }
  double cap_total_w = 0;
  for (const auto& activity : activities) {
    const double duration = activity.end_time_s - activity.start_time_s;
    require(duration > 0, "steady cap activity must have positive duration");
    for (const auto& assignment : activity.node_energy) {
      const double power = assignment.energy_j / duration;
      require(std::isfinite(power) && power >= 0,
              "steady cap activity power must be finite and nonnegative");
      cap_power[static_cast<Eigen::Index>(assignment.node_index)] += power;
      cap_total_w += power;
    }
  }
  require(cap_total_w > 0, "steady envelope requires positive all-source cap power");
  const SparseMatrix matrix = steady_matrix(config);
  SparseSolver solver;
  const auto factor_start = std::chrono::steady_clock::now();
  solver.compute(matrix);
  const double factor_seconds = std::chrono::duration<double>(
      std::chrono::steady_clock::now() - factor_start).count();
  require(solver.info() == Eigen::Success, "steady L factorization failed");
  const Eigen::VectorXd fixed = solver.solve(fixed_rhs);
  require(solver.info() == Eigen::Success && fixed.allFinite(),
          "steady fixed-source solve failed");
  const Eigen::VectorXd cap_rise = solver.solve(cap_power);
  require(solver.info() == Eigen::Success && cap_rise.allFinite(),
          "steady cap solve failed");
  require(cap_rise.minCoeff() >= -1e-10,
          "steady cap response violates positive-network monotonicity");
  const double fixed_residual =
      (matrix * fixed - fixed_rhs).lpNorm<Eigen::Infinity>();
  const double cap_residual =
      (matrix * cap_rise - cap_power).lpNorm<Eigen::Infinity>();
  const std::vector<double> alphas{1.0, 0.75, 0.5, 0.25};
  double selected = -1;
  std::ostringstream candidates;
  candidates << std::setprecision(17) << '[';
  for (std::size_t number = 0; number < alphas.size(); ++number) {
    const double alpha = alphas[number];
    const Eigen::VectorXd envelope = fixed + alpha * cap_rise;
    bool initial_covered = true;
    for (Eigen::Index index = 0; index < n; ++index)
      initial_covered &= config.nodes[static_cast<std::size_t>(index)].initial_temperature_k <=
                         envelope[index] + 1e-10;
    const bool within = envelope.maxCoeff() <= options.envelope_limit_k;
    if (selected < 0 && within && initial_covered) selected = alpha;
    Eigen::Index maximum_index{};
    const double maximum = envelope.maxCoeff(&maximum_index);
    if (number) candidates << ',';
    candidates << "{\"alpha\":" << alpha << ",\"max_k\":" << maximum
               << ",\"max_node\":"
               << json_string(config.nodes[static_cast<std::size_t>(maximum_index)].id)
               << ",\"within_limit\":" << (within ? "true" : "false")
               << ",\"initial_covered\":" << (initial_covered ? "true" : "false")
               << '}';
  }
  candidates << ']';
  std::cout << std::setprecision(17)
            << "{\"schema_version\":\"eq3-steady-envelope-v1\","
            << "\"status\":" << json_string(selected > 0 ? "PREDICTED_ENVELOPE"
                                                        : "DOMAIN_REDESIGN_REQUIRED")
            << ",\"workload_executed\":false,\"reference_qualified\":false,"
            << "\"model_sha256\":" << json_string(options.model_sha256)
            << ",\"events_sha256\":" << json_string(options.events_sha256)
            << ",\"runner_source_sha256\":" << json_string(options.runner_source_sha256)
            << ",\"domain_version\":" << json_string(options.domain_version)
            << ",\"declared_temperature_domain_k\":[" << options.min_k << ','
            << options.max_k << "],"
            << "\"cap_total_w\":" << cap_total_w
            << ",\"envelope_limit_k\":" << options.envelope_limit_k
            << ",\"selected_alpha\":";
  if (selected > 0) std::cout << selected;
  else std::cout << "null";
  std::cout << ",\"matrix_nnz\":" << matrix.nonZeros()
            << ",\"factor_L_nnz\":" << solver.matrixL().nestedExpression().nonZeros()
            << ",\"factor_seconds\":" << factor_seconds
            << ",\"fixed_residual_inf\":" << fixed_residual
            << ",\"cap_residual_inf\":" << cap_residual
            << ",\"candidates\":" << candidates.str() << "}\n";
}

// Fixed zero-source equilibrium diagnostic, never the supplied workload.
void equilibrium_diagnostic(const ThermalModelConfig& config, double step_s) {
  const double reference = config.nodes.front().initial_temperature_k;
  const SparseMatrix matrix = system_matrix(config, step_s);
  SparseSolver solver;
  solver.compute(matrix);
  require(solver.info() == Eigen::Success, "diagnostic factorization failed");
  Eigen::VectorXd rhs(config.nodes.size());
  for (std::size_t i = 0; i < config.nodes.size(); ++i) {
    const auto& node = config.nodes[i];
    require(node.initial_temperature_k == reference && node.static_power_w == 0 &&
                (node.boundary_conductance_w_per_k == 0 || node.boundary_temperature_k == reference),
            "diagnostic requires zero-static-power isothermal equilibrium");
    rhs[i] = node.heat_capacity_j_per_k / step_s * reference +
             node.boundary_conductance_w_per_k * node.boundary_temperature_k;
  }
  const Eigen::VectorXd absolute = solver.solve(rhs);
  require(solver.info() == Eigen::Success, "absolute diagnostic solve failed");
  const Eigen::VectorXd theta = solver.solve(Eigen::VectorXd::Zero(rhs.size()));
  require(solver.info() == Eigen::Success, "theta diagnostic solve failed");
  std::cout << std::setprecision(17)
            << "{\"mode\":\"zero_source_equilibrium_diagnostic\",\"workload_executed\":false,"
            << "\"reference_k\":" << reference
            << ",\"absolute_min_k\":" << absolute.minCoeff()
            << ",\"absolute_max_k\":" << absolute.maxCoeff()
            << ",\"absolute_max_equilibrium_error_k\":" << (absolute.array()-reference).abs().maxCoeff()
            << ",\"absolute_residual_inf\":" << (matrix*absolute-rhs).lpNorm<Eigen::Infinity>()
            << ",\"theta_max_abs_k\":" << theta.lpNorm<Eigen::Infinity>()
            << ",\"factor_L_nnz\":" << solver.matrixL().nestedExpression().nonZeros()
            << "}\n";
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

std::string json_string(const std::string& value) {
  std::ostringstream out;
  out << '"';
  for (const unsigned char character : value) {
    switch (character) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\b': out << "\\b"; break;
      case '\f': out << "\\f"; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (character < 0x20)
          out << "\\u" << std::hex << std::setfill('0') << std::setw(4)
              << static_cast<unsigned>(character) << std::dec << std::setfill(' ');
        else
          out << character;
    }
  }
  return out.str() + '"';
}

std::string csv_string(const std::string& value) {
  std::string result{"\""};
  for (const char character : value) {
    if (character == '"') result += '"';
    result += character;
  }
  return result + '"';
}

std::string json_number_or_null(double value) {
  if (!std::isfinite(value)) return "null";
  std::ostringstream output;
  output << std::setprecision(17) << value;
  return output.str();
}

void write_state(const std::filesystem::path& final,
                 const ThermalModelConfig& config,
                 const Eigen::VectorXd& temperature) {
  const std::filesystem::path temporary{final.string() + ".tmp"};
  require(!std::filesystem::exists(final) && !std::filesystem::exists(temporary),
          "refusing to overwrite failure state " + final.string());
  std::ofstream output(temporary, std::ios::out | std::ios::trunc);
  if (!output) throw std::runtime_error("cannot create failure state " + final.string());
  output << "node_index,node_id,group_id,die_index,temperature_k\n"
         << std::setprecision(17);
  for (std::size_t index = 0; index < config.nodes.size(); ++index) {
    const auto& node = config.nodes[index];
    output << index << ',' << csv_string(node.id) << ',' << csv_string(node.group_id) << ',';
    if (node.die_index) output << *node.die_index;
    else output << "UNKNOWN";
    output << ',' << temperature[static_cast<Eigen::Index>(index)] << '\n';
  }
  output.close();
  if (!output) throw std::runtime_error("failed writing failure state " + final.string());
  std::filesystem::rename(temporary, final);
}

long double stored_energy(const ThermalModelConfig& config,
                          const Eigen::VectorXd& theta, double origin) {
  long double result = 0;
  for (Eigen::Index index = 0; index < theta.size(); ++index) {
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    result += node.heat_capacity_j_per_k *
              (static_cast<long double>(theta[index]) -
               (node.initial_temperature_k - origin));
  }
  return result;
}

void unknown_trial_diagnostic(const Options& options,
                              const ThermalModelConfig& config,
                              const Eigen::VectorXd& last_valid_temperature,
                              const Eigen::VectorXd& last_valid_theta,
                              const Eigen::VectorXd& applied,
                              const Eigen::VectorXd& power,
                              std::uint64_t completed_steps,
                              std::uint64_t trial_step, std::uint64_t slot,
                              double last_valid_time_s, double trial_time_s,
                              long double boundary_loss_j, double origin_k,
                              const std::string& reason) {
  const std::filesystem::path final{"rc_failure_diagnostic.json"};
  const std::filesystem::path temporary{"rc_failure_diagnostic.json.tmp"};
  require(!std::filesystem::exists(final) && !std::filesystem::exists(temporary),
          "refusing to overwrite RC failure diagnostic");
  write_state("rc_failure_last_valid.csv", config, last_valid_temperature);
  long double activity_completed = 0;
  for (Eigen::Index index = 0; index < applied.size(); ++index)
    activity_completed += applied[index];
  const long double static_completed = static_energy(config, last_valid_time_s);
  const long double stored_completed = stored_energy(config, last_valid_theta, origin_k);
  const long double residual_completed = activity_completed + static_completed -
                                         stored_completed - boundary_loss_j;
  std::ofstream output(temporary, std::ios::out | std::ios::trunc);
  if (!output) throw std::runtime_error("cannot create RC failure diagnostic");
  output << std::setprecision(17)
         << "{\n"
         << "  \"schema_version\": \"eq3-campaign-sparse-rc-failure-v1\",\n"
         << "  \"status\": \"NUMERICAL_FAILURE\",\n"
         << "  \"exit_reason\": " << json_string(reason) << ",\n"
         << "  \"failure_returned_to_caller\": true,\n"
         << "  \"last_valid_state_file\": \"rc_failure_last_valid.csv\",\n"
         << "  \"trial_state_file\": null,\n"
         << "  \"trial_state_status\": \"UNKNOWN_SOLVER_FAILURE\",\n"
         << "  \"last_valid_time_s\": " << last_valid_time_s << ",\n"
         << "  \"trial_target_time_s\": " << trial_time_s << ",\n"
         << "  \"completed_steps\": " << completed_steps << ",\n"
         << "  \"trial_step_1_based\": " << trial_step << ",\n"
         << "  \"step_s\": " << options.step_s << ",\n"
         << "  \"input_slot_index_0_based\": " << slot << ",\n"
         << "  \"input_interval_start_s\": " << static_cast<double>(slot) * options.slot_s << ",\n"
         << "  \"input_interval_end_s\": " << static_cast<double>(slot + 1) * options.slot_s << ",\n"
         << "  \"trial_activity_power_w\": " << json_number_or_null(power.sum()) << ",\n"
         << "  \"domain_version\": " << json_string(options.domain_version) << ",\n"
         << "  \"domain_min_k\": " << options.min_k << ",\n"
         << "  \"domain_max_k\": " << options.max_k << ",\n"
         << "  \"model_sha256\": " << json_string(options.model_sha256) << ",\n"
         << "  \"events_sha256\": " << json_string(options.events_sha256) << ",\n"
         << "  \"runner_source_sha256\": " << json_string(options.runner_source_sha256) << ",\n"
         << "  \"completed_interval_energy\": {\"activity_input_j\":" << activity_completed
         << ",\"static_input_j\":" << static_completed
         << ",\"stored_energy_change_j\":" << stored_completed
         << ",\"boundary_loss_j\":" << boundary_loss_j
         << ",\"energy_residual_j\":" << residual_completed << "},\n"
         << "  \"failed_trial_step_energy\": {\"status\":\"UNKNOWN_NOT_INTEGRATED\","
            "\"activity_input_j\":null,\"static_input_j\":null,"
            "\"stored_energy_change_j\":null,\"boundary_loss_j\":null,"
            "\"energy_residual_j\":null}\n"
         << "}\n";
  output.close();
  if (!output) throw std::runtime_error("failed writing RC failure diagnostic");
  std::filesystem::rename(temporary, final);
}

void failure_diagnostic(const Options& options, const ThermalModelConfig& config,
                        const Eigen::VectorXd& last_valid_temperature,
                        const Eigen::VectorXd& trial_temperature,
                        const Eigen::VectorXd& last_valid_theta,
                        const Eigen::VectorXd& applied,
                        const Eigen::VectorXd& power,
                        std::uint64_t completed_steps, std::uint64_t trial_step,
                        std::uint64_t slot, double last_valid_time_s,
                        double trial_time_s, long double boundary_loss_j,
                        double origin_k, const std::string& reason) {
  const std::filesystem::path final{"rc_failure_diagnostic.json"};
  const std::filesystem::path temporary{"rc_failure_diagnostic.json.tmp"};
  require(!std::filesystem::exists(final) && !std::filesystem::exists(temporary),
          "refusing to overwrite RC failure diagnostic");
  write_state("rc_failure_last_valid.csv", config, last_valid_temperature);
  write_state("rc_failure_trial.csv", config, trial_temperature);

  std::vector<std::size_t> offending;
  std::size_t nonfinite_count = 0;
  for (Eigen::Index index = 0; index < trial_temperature.size(); ++index) {
    const double value = trial_temperature[index];
    if (!std::isfinite(value)) ++nonfinite_count;
    if (!std::isfinite(value) || value < options.min_k || value > options.max_k)
      offending.push_back(static_cast<std::size_t>(index));
  }
  long double activity_completed = 0;
  for (Eigen::Index index = 0; index < applied.size(); ++index)
    activity_completed += applied[index];
  const long double static_completed = static_energy(config, last_valid_time_s);
  const long double stored_completed = stored_energy(config, last_valid_theta, origin_k);
  const long double residual_completed = activity_completed + static_completed -
                                         stored_completed - boundary_loss_j;
  const bool finite_trial = trial_temperature.allFinite();

  std::ofstream output(temporary, std::ios::out | std::ios::trunc);
  if (!output) throw std::runtime_error("cannot create RC failure diagnostic");
  output << std::setprecision(17)
         << "{\n"
         << "  \"schema_version\": \"eq3-campaign-sparse-rc-failure-v1\",\n"
         << "  \"status\": "
         << json_string(nonfinite_count ? "NUMERICAL_FAILURE" : "DOMAIN_FAILURE") << ",\n"
         << "  \"exit_reason\": " << json_string(reason) << ",\n"
         << "  \"failure_returned_to_caller\": true,\n"
         << "  \"temperature_clamping\": false,\n"
         << "  \"last_valid_state_file\": \"rc_failure_last_valid.csv\",\n"
         << "  \"trial_state_file\": \"rc_failure_trial.csv\",\n"
         << "  \"last_valid_time_s\": " << last_valid_time_s << ",\n"
         << "  \"trial_target_time_s\": " << trial_time_s << ",\n"
         << "  \"completed_steps\": " << completed_steps << ",\n"
         << "  \"trial_step_1_based\": " << trial_step << ",\n"
         << "  \"step_s\": " << options.step_s << ",\n"
         << "  \"input_slot_index_0_based\": " << slot << ",\n"
         << "  \"input_interval_start_s\": " << static_cast<double>(slot) * options.slot_s << ",\n"
         << "  \"input_interval_end_s\": " << static_cast<double>(slot + 1) * options.slot_s << ",\n"
         << "  \"trial_activity_power_w\": " << json_number_or_null(power.sum()) << ",\n"
         << "  \"domain_version\": " << json_string(options.domain_version) << ",\n"
         << "  \"domain_min_k\": " << options.min_k << ",\n"
         << "  \"domain_max_k\": " << options.max_k << ",\n"
         << "  \"model_path\": " << json_string(options.model_path) << ",\n"
         << "  \"events_path\": " << json_string(options.events_path) << ",\n"
         << "  \"model_sha256\": " << json_string(options.model_sha256) << ",\n"
         << "  \"events_sha256\": " << json_string(options.events_sha256) << ",\n"
         << "  \"runner_source_sha256\": " << json_string(options.runner_source_sha256) << ",\n"
         << "  \"coordinate_status\": \"UNKNOWN_NOT_EXPOSED_BY_MODEL_TEXT_API\",\n"
         << "  \"nonfinite_trial_nodes\": " << nonfinite_count << ",\n"
         << "  \"trial_extrema_available\": " << (finite_trial ? "true" : "false") << ",\n";
  if (finite_trial)
    output << "  \"trial_min_k\": " << trial_temperature.minCoeff() << ",\n"
           << "  \"trial_max_k\": " << trial_temperature.maxCoeff() << ",\n";
  else
    output << "  \"trial_min_k\": null,\n  \"trial_max_k\": null,\n";
  output << "  \"offending_nodes\": [";
  for (std::size_t position = 0; position < offending.size(); ++position) {
    if (position) output << ',';
    const std::size_t index = offending[position];
    const auto& node = config.nodes[index];
    output << "{\"index\":" << index << ",\"id\":" << json_string(node.id)
           << ",\"group_id\":" << json_string(node.group_id) << ",\"die_index\":";
    if (node.die_index) output << *node.die_index;
    else output << "null";
    output << ",\"temperature_k\":";
    const double value = trial_temperature[static_cast<Eigen::Index>(index)];
    if (std::isfinite(value)) output << value;
    else output << json_string(std::isnan(value) ? "NaN" : value > 0 ? "+Infinity" : "-Infinity");
    output << '}';
  }
  output << "],\n"
         << "  \"completed_interval_energy\": {\n"
         << "    \"activity_input_j\": " << activity_completed << ",\n"
         << "    \"static_input_j\": " << static_completed << ",\n"
         << "    \"stored_energy_change_j\": " << stored_completed << ",\n"
         << "    \"boundary_loss_j\": " << boundary_loss_j << ",\n"
         << "    \"energy_residual_j\": " << residual_completed << "\n"
         << "  },\n"
         << "  \"failed_trial_step_energy\": {\"status\":\"UNKNOWN_NOT_INTEGRATED\","
            "\"activity_input_j\":null,\"static_input_j\":null,"
            "\"stored_energy_change_j\":null,\"boundary_loss_j\":null,"
            "\"energy_residual_j\":null}\n"
         << "}\n";
  output.close();
  if (!output) throw std::runtime_error("failed writing RC failure diagnostic");
  std::filesystem::rename(temporary, final);
}

void receipt(const Options& options, const Schedule& schedule,
             const ThermalModelConfig& config, std::size_t matrix_nnz,
             double alignment_error_s, double min_observed_k,
             double max_observed_k, long double declared_energy_j,
             long double applied_energy_j, long double stored_energy_j,
             long double boundary_loss_j, double origin_k,
             std::size_t factor_nnz, double factor_seconds) {
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
         << "  \"state_variable\": \"theta=T-origin\",\n"
         << "  \"temperature_origin_k\": " << origin_k << ",\n"
         << "  \"temperature_clamping\": false,\n"
         << "  \"factor_L_nnz\": " << factor_nnz << ",\n"
         << "  \"factor_seconds\": " << factor_seconds << ",\n"
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
  for (const char* path : {"rc_energy_receipt.json", "rc_energy_receipt.json.tmp",
                           "rc_failure_diagnostic.json", "rc_failure_diagnostic.json.tmp",
                           "rc_failure_last_valid.csv", "rc_failure_last_valid.csv.tmp",
                           "rc_failure_trial.csv", "rc_failure_trial.csv.tmp"})
    require(!std::filesystem::exists(path),
            std::string("refusing to overwrite RC output ") + path);
  const SparseMatrix matrix = system_matrix(config, options.step_s);
  SparseSolver solver;
  const auto factor_start = std::chrono::steady_clock::now();
  solver.compute(matrix);
  const double factor_seconds = std::chrono::duration<double>(
      std::chrono::steady_clock::now() - factor_start).count();
  require(solver.info() == Eigen::Success, "sparse LDLT factorization failed");
  const auto powers = slot_powers(config, activities, options, schedule);
  const Eigen::Index n = static_cast<Eigen::Index>(config.nodes.size());
  const double origin = config.nodes.front().initial_temperature_k;
  Eigen::VectorXd temperature(n), theta(n), capacity_over_dt(n), static_and_boundary(n);
  Eigen::VectorXd applied = Eigen::VectorXd::Zero(n);
  for (Eigen::Index index = 0; index < n; ++index) {
    const auto& node = config.nodes[static_cast<std::size_t>(index)];
    temperature[index] = node.initial_temperature_k;
    theta[index] = node.initial_temperature_k - origin;
    capacity_over_dt[index] = node.heat_capacity_j_per_k / options.step_s;
    static_and_boundary[index] =
        node.static_power_w + node.boundary_conductance_w_per_k *
                                  (node.boundary_temperature_k - origin);
  }
  double minimum = temperature.minCoeff(), maximum = temperature.maxCoeff();
  std::cout << "time_s,node_id,temperature_k\n" << std::setprecision(17);
  emit(0.0, config, temperature);
  long double boundary_loss = 0;
  std::uint64_t global = 0;
  for (std::uint64_t slot = 0; slot < schedule.slot_count; ++slot) {
    const auto& power = powers[static_cast<std::size_t>(slot)];
    for (std::uint64_t local = 1; local <= schedule.steps_per_slot; ++local) {
      ++global;
      const Eigen::VectorXd rhs =
          capacity_over_dt.cwiseProduct(theta) + static_and_boundary + power;
      const Eigen::VectorXd trial_theta = solver.solve(rhs);
      const Eigen::VectorXd trial_temperature = trial_theta.array() + origin;
      const double trial_time = local == schedule.steps_per_slot
                                    ? static_cast<double>(slot + 1) * options.slot_s
                                    : static_cast<double>(slot) * options.slot_s +
                                          static_cast<double>(local) * options.step_s;
      const double last_valid_time = local == 1
                                         ? static_cast<double>(slot) * options.slot_s
                                         : static_cast<double>(slot) * options.slot_s +
                                               static_cast<double>(local - 1) * options.step_s;
      if (solver.info() != Eigen::Success) {
        unknown_trial_diagnostic(options, config, temperature, theta, applied, power,
                                 global - 1, global, slot, last_valid_time,
                                 trial_time, boundary_loss, origin,
                                 "sparse LDLT solve failed");
        fail("sparse LDLT solve failed");
      }
      const bool finite = trial_temperature.allFinite();
      const bool in_domain = finite && trial_temperature.minCoeff() >= options.min_k &&
                             trial_temperature.maxCoeff() <= options.max_k;
      if (!in_domain) {
        failure_diagnostic(options, config, temperature, trial_temperature, theta,
                           applied, power, global - 1, global, slot,
                           last_valid_time, trial_time, boundary_loss,
                           origin, finite ? "temperature left required domain"
                                          : "sparse solve produced nonfinite temperature");
        fail(finite ? "temperature left required domain"
                    : "sparse solve produced nonfinite temperature");
      }
      theta = trial_theta;
      temperature = trial_temperature;
      minimum = std::min(minimum, temperature.minCoeff());
      maximum = std::max(maximum, temperature.maxCoeff());
      for (Eigen::Index index = 0; index < n; ++index) {
        const auto& node = config.nodes[static_cast<std::size_t>(index)];
        boundary_loss += static_cast<long double>(options.step_s) *
                         node.boundary_conductance_w_per_k *
                         (static_cast<long double>(theta[index]) +
                          (origin - node.boundary_temperature_k));
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
  long double applied_energy = 0;
  for (Eigen::Index index = 0; index < n; ++index) {
    applied_energy += applied[index];
  }
  const long double stored = stored_energy(config, theta, origin);
  receipt(options, schedule, config, static_cast<std::size_t>(matrix.nonZeros()),
          alignment_error_s, minimum, maximum,
          declared_activity_energy(activities), applied_energy, stored,
          boundary_loss, origin,
          static_cast<std::size_t>(solver.matrixL().nestedExpression().nonZeros()),
          factor_seconds);
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
  if (options.equilibrium_diagnostic) {
    equilibrium_diagnostic(config, options.step_s);
    return 0;
  }
  if (options.steady_envelope) {
    steady_envelope(config, activities, options);
    return 0;
  }
  run(config, activities, options, schedule, alignment_error);
  return 0;
} catch (const std::exception& error) {
  std::cerr << "eq3_campaign_rc_runner: " << error.what() << '\n';
  return 2;
}
