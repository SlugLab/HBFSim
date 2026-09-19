#include "hbfsim/eq3_thermal/thermal.hpp"

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
#include <string_view>
#include <vector>

namespace {

using hbfsim::eq3_thermal::PhysicalActivity;
using hbfsim::eq3_thermal::ThermalModelConfig;

struct Options {
  std::string model_path;
  std::string events_path;
  double step_s{};
  double slot_s{};
  double end_s{};
  double sample_s{};
  double min_k{};
  double max_k{};
  bool inspect_only{};
  bool run{};
};

struct Schedule {
  std::uint64_t steps_per_slot{};
  std::uint64_t slot_count{};
  std::uint64_t total_steps{};
  std::uint64_t steps_per_sample{};
  std::uint64_t sample_count{};
  std::set<double> dt_keys;
  double max_target_grid_error_s{};
  double max_dt_error_s{};
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
  std::ostringstream contents;
  contents << input.rdbuf();
  if (!input.good() && !input.eof()) throw std::runtime_error("failed reading " + path);
  return contents.str();
}

double parse_double(const std::string& text, const std::string& option) {
  std::size_t used{};
  double value{};
  try {
    value = std::stod(text, &used);
  } catch (const std::exception&) {
    fail(option + " requires a finite numeric value");
  }
  require(used == text.size() && std::isfinite(value),
          option + " requires a finite numeric value");
  return value;
}

Options parse_options(int argc, char** argv) {
  Options result;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    if (argument == "--inspect-only") {
      require(!result.inspect_only, "duplicate --inspect-only");
      result.inspect_only = true;
      continue;
    }
    if (argument == "--run") {
      require(!result.run, "duplicate --run");
      result.run = true;
      continue;
    }
    const bool takes_value =
        argument == "--model" || argument == "--events" || argument == "--step-s" ||
        argument == "--slot-s" || argument == "--end-s" || argument == "--sample-s" ||
        argument == "--min-k" || argument == "--max-k";
    require(takes_value, "unknown argument " + argument);
    require(i + 1 < argc, "missing value after " + argument);
    const std::string value = argv[++i];
    if (argument == "--model") result.model_path = value;
    else if (argument == "--events") result.events_path = value;
    else if (argument == "--step-s") result.step_s = parse_double(value, argument);
    else if (argument == "--slot-s") result.slot_s = parse_double(value, argument);
    else if (argument == "--end-s") result.end_s = parse_double(value, argument);
    else if (argument == "--sample-s") result.sample_s = parse_double(value, argument);
    else if (argument == "--min-k") result.min_k = parse_double(value, argument);
    else if (argument == "--max-k") result.max_k = parse_double(value, argument);
  }
  require(result.inspect_only != result.run,
          "exactly one of --inspect-only or --run is required; execution is default-off");
  require(!result.model_path.empty() && !result.events_path.empty(),
          "--model and --events are required");
  require(result.step_s > 0.0 && result.slot_s > 0.0 && result.end_s > 0.0 &&
              result.sample_s > 0.0,
          "--step-s, --slot-s, --end-s, and --sample-s must be positive");
  require(result.min_k > 0.0 && result.max_k > result.min_k,
          "--min-k and --max-k are required and must satisfy 0 < min < max");
  return result;
}

std::uint64_t exact_ratio(double whole, double quantum, const std::string& label) {
  const double ratio = whole / quantum;
  require(std::isfinite(ratio) && ratio >= 1.0 &&
              ratio <= static_cast<double>(std::numeric_limits<std::uint64_t>::max()),
          label + " ratio is outside the supported integer range");
  const double rounded = std::round(ratio);
  const double tolerance = 1e-12 * std::max(1.0, std::abs(ratio));
  require(std::abs(ratio - rounded) <= tolerance,
          label + " must divide exactly within floating-input tolerance");
  return static_cast<std::uint64_t>(rounded);
}

Schedule make_schedule(const Options& options) {
  Schedule schedule;
  schedule.steps_per_slot = exact_ratio(options.slot_s, options.step_s, "--step-s into --slot-s");
  schedule.slot_count = exact_ratio(options.end_s, options.slot_s, "--slot-s into --end-s");
  schedule.steps_per_sample = exact_ratio(options.sample_s, options.step_s, "--step-s into --sample-s");
  schedule.sample_count = exact_ratio(options.end_s, options.sample_s, "--sample-s into --end-s");
  require(schedule.slot_count <= std::numeric_limits<std::uint64_t>::max() /
                                     schedule.steps_per_slot,
          "total step count overflows uint64");
  schedule.total_steps = schedule.slot_count * schedule.steps_per_slot;

  double previous = 0.0;
  std::uint64_t global_step = 0;
  for (std::uint64_t slot = 0; slot < schedule.slot_count; ++slot) {
    for (std::uint64_t local = 1; local <= schedule.steps_per_slot; ++local) {
      ++global_step;
      const double target = local == schedule.steps_per_slot
                                ? static_cast<double>(slot + 1) * options.slot_s
                                : static_cast<double>(slot) * options.slot_s +
                                      static_cast<double>(local) * options.step_s;
      const double dt = target - previous;
      require(std::isfinite(target) && dt > 0.0, "constructed schedule is not monotonic");
      schedule.dt_keys.insert(dt);
      schedule.max_target_grid_error_s =
          std::max(schedule.max_target_grid_error_s,
                   std::abs(target - static_cast<double>(global_step) * options.step_s));
      schedule.max_dt_error_s =
          std::max(schedule.max_dt_error_s, std::abs(dt - options.step_s));
      previous = target;
    }
  }
  require(global_step == schedule.total_steps, "internal schedule step-count mismatch");
  return schedule;
}

double alignment_error(double value, double quantum) {
  return std::abs(value - std::round(value / quantum) * quantum);
}

double validate_and_canonicalize_activities(std::vector<PhysicalActivity>& activities,
                                            const Options& options) {
  double max_error = 0.0;
  const double tolerance = 1e-12 * std::max(1.0, options.end_s);
  for (auto& activity : activities) {
    const double start_error = alignment_error(activity.start_time_s, options.slot_s);
    const double end_error = alignment_error(activity.end_time_s, options.slot_s);
    max_error = std::max({max_error, start_error, end_error});
    require(start_error <= tolerance && end_error <= tolerance,
            "every activity start/end must align to --slot-s; activity " +
                std::to_string(activity.activity_id) + " is misaligned");
    require(activity.start_time_s >= 0.0 && activity.end_time_s <= options.end_s + tolerance,
            "every activity must lie inside [0, --end-s]; activity " +
                std::to_string(activity.activity_id) + " is outside");
    require(activity.completion_time_s <= options.end_s + tolerance,
            "activity completion lies after --end-s for activity " +
                std::to_string(activity.activity_id));
    // Parsing decimal text and multiplying a binary floating slot can differ by
    // a few ulps (for example 0.3 versus 3*0.1).  Canonicalize only after the
    // strict alignment check so activity boundaries exactly equal schedule slot
    // boundaries and cannot create undeclared factorization keys.
    activity.start_time_s = std::round(activity.start_time_s / options.slot_s) *
                            options.slot_s;
    activity.end_time_s = std::round(activity.end_time_s / options.slot_s) *
                          options.slot_s;
    require(activity.end_time_s > activity.start_time_s,
            "activity collapsed after slot canonicalization for activity " +
                std::to_string(activity.activity_id));
    if (std::abs(activity.completion_time_s - activity.end_time_s) <= tolerance)
      activity.completion_time_s = activity.end_time_s;
  }
  return max_error;
}

long double event_energy(const std::vector<PhysicalActivity>& activities) {
  long double total = 0.0L;
  for (const auto& activity : activities)
    for (const auto& assignment : activity.node_energy)
      total += static_cast<long double>(assignment.energy_j);
  return total;
}

long double static_energy(const ThermalModelConfig& config, double end_s) {
  long double total = 0.0L;
  for (const auto& node : config.nodes)
    total += static_cast<long double>(node.static_power_w) * end_s;
  return total;
}

long double cache_bytes(std::size_t nodes, std::size_t keys) {
  return static_cast<long double>(nodes) * static_cast<long double>(nodes) *
         static_cast<long double>(keys) * static_cast<long double>(sizeof(double));
}

void validate_initial_domain(const ThermalModelConfig& config, const Options& options) {
  for (const auto& node : config.nodes)
    require(node.initial_temperature_k >= options.min_k &&
                node.initial_temperature_k <= options.max_k,
            "initial temperature for node " + node.id + " is outside --min-k/--max-k");
}

void print_inspection(const ThermalModelConfig& config,
                      const std::vector<PhysicalActivity>& activities,
                      const Options& options, const Schedule& schedule,
                      double event_alignment_error_s) {
  const long double rows = static_cast<long double>(config.nodes.size()) *
                           static_cast<long double>(schedule.sample_count + 1);
  const long double declared_activity_energy = event_energy(activities);
  const long double declared_static_energy = static_energy(config, options.end_s);
  std::cout << std::setprecision(17)
            << "{\n"
            << "  \"mode\": \"inspect_only\",\n"
            << "  \"solver_constructed\": false,\n"
            << "  \"nodes\": " << config.nodes.size() << ",\n"
            << "  \"edges\": " << config.edges.size() << ",\n"
            << "  \"activities\": " << activities.size() << ",\n"
            << "  \"total_steps\": " << schedule.total_steps << ",\n"
            << "  \"sample_count_excluding_t0\": " << schedule.sample_count << ",\n"
            << "  \"t0_rows_declared\": " << config.nodes.size() << ",\n"
            << "  \"estimated_csv_rows_including_t0\": " << rows << ",\n"
            << "  \"static_dt_key_upper_bound\": " << schedule.dt_keys.size() << ",\n"
            << "  \"estimated_factor_cache_bytes\": "
            << cache_bytes(config.nodes.size(), schedule.dt_keys.size()) << ",\n"
            << "  \"activity_declared_energy_j\": " << declared_activity_energy << ",\n"
            << "  \"static_input_energy_j\": " << declared_static_energy << ",\n"
            << "  \"total_declared_input_energy_j\": "
            << declared_activity_energy + declared_static_energy << ",\n"
            << "  \"min_k\": " << options.min_k << ",\n"
            << "  \"max_k\": " << options.max_k << ",\n"
            << "  \"max_event_slot_alignment_error_s\": " << event_alignment_error_s << ",\n"
            << "  \"max_target_grid_error_s\": " << schedule.max_target_grid_error_s << ",\n"
            << "  \"max_dt_error_s\": " << schedule.max_dt_error_s << "\n"
            << "}\n";
}

void emit_nodes(double time_s, const ThermalModelConfig& config,
                const std::vector<double>& temperatures) {
  for (std::size_t index = 0; index < config.nodes.size(); ++index)
    std::cout << time_s << ',' << config.nodes[index].id << ',' << temperatures[index] << '\n';
}

void write_receipt(const Options& options, const Schedule& schedule,
                   const ThermalModelConfig& config, std::size_t factorization_count,
                   double event_alignment_error_s, double min_observed_k,
                   double max_observed_k, long double applied_energy_j,
                   long double declared_activity_energy_j,
                   long double static_energy_j, long double stored_energy_j,
                   long double boundary_loss_j) {
  const std::filesystem::path final_path{"rc_energy_receipt.json"};
  const std::filesystem::path temporary_path{"rc_energy_receipt.json.tmp"};
  require(!std::filesystem::exists(final_path) && !std::filesystem::exists(temporary_path),
          "refusing to overwrite rc_energy_receipt.json or its temporary file");
  const long double total_input = applied_energy_j + static_energy_j;
  const long double residual = total_input - stored_energy_j - boundary_loss_j;
  std::ofstream out(temporary_path, std::ios::out | std::ios::trunc);
  if (!out) throw std::runtime_error("cannot create " + temporary_path.string());
  out << std::setprecision(17)
      << "{\n"
      << "  \"schema_version\": \"eq3-layered-rc-energy-v1\",\n"
      << "  \"mode\": \"run\",\n"
      << "  \"t0_emitted\": true,\n"
      << "  \"nodes\": " << config.nodes.size() << ",\n"
      << "  \"total_steps\": " << schedule.total_steps << ",\n"
      << "  \"sample_count_excluding_t0\": " << schedule.sample_count << ",\n"
      << "  \"factorization_count\": " << factorization_count << ",\n"
      << "  \"static_dt_key_upper_bound\": " << schedule.dt_keys.size() << ",\n"
      << "  \"estimated_factor_cache_bytes\": "
      << cache_bytes(config.nodes.size(), schedule.dt_keys.size()) << ",\n"
      << "  \"step_s\": " << options.step_s << ",\n"
      << "  \"slot_s\": " << options.slot_s << ",\n"
      << "  \"sample_s\": " << options.sample_s << ",\n"
      << "  \"end_s\": " << options.end_s << ",\n"
      << "  \"min_k\": " << options.min_k << ",\n"
      << "  \"max_k\": " << options.max_k << ",\n"
      << "  \"min_observed_k\": " << min_observed_k << ",\n"
      << "  \"max_observed_k\": " << max_observed_k << ",\n"
      << "  \"max_event_slot_alignment_error_s\": " << event_alignment_error_s << ",\n"
      << "  \"max_target_grid_error_s\": " << schedule.max_target_grid_error_s << ",\n"
      << "  \"max_dt_error_s\": " << schedule.max_dt_error_s << ",\n"
      << "  \"activity_declared_energy_j\": " << declared_activity_energy_j << ",\n"
      << "  \"activity_applied_energy_j\": " << applied_energy_j << ",\n"
      << "  \"activity_energy_difference_j\": "
      << applied_energy_j - declared_activity_energy_j << ",\n"
      << "  \"static_input_energy_j\": " << static_energy_j << ",\n"
      << "  \"total_input_energy_j\": " << total_input << ",\n"
      << "  \"stored_energy_change_j\": " << stored_energy_j << ",\n"
      << "  \"boundary_loss_j\": " << boundary_loss_j << ",\n"
      << "  \"energy_residual_j\": " << residual << "\n"
      << "}\n";
  out.close();
  if (!out) throw std::runtime_error("failed writing " + temporary_path.string());
  std::filesystem::rename(temporary_path, final_path);
}

void run(const ThermalModelConfig& config, std::vector<PhysicalActivity> activities,
         const Options& options, const Schedule& schedule,
         double event_alignment_error_s) {
  require(!std::filesystem::exists("rc_energy_receipt.json") &&
              !std::filesystem::exists("rc_energy_receipt.json.tmp"),
          "refusing to overwrite rc_energy_receipt.json or its temporary file");
  const long double declared_activity_energy = event_energy(activities);
  hbfsim::eq3_thermal::ThermalModel model(config);
  for (auto& activity : activities) model.add_activity(std::move(activity));

  double min_observed = std::numeric_limits<double>::infinity();
  double max_observed = -std::numeric_limits<double>::infinity();
  const auto check_domain = [&](const std::vector<double>& temperatures) {
    for (double temperature : temperatures) {
      require(std::isfinite(temperature) && temperature >= options.min_k &&
                  temperature <= options.max_k,
              "temperature left the required --min-k/--max-k domain");
      min_observed = std::min(min_observed, temperature);
      max_observed = std::max(max_observed, temperature);
    }
  };

  std::cout << "time_s,node_id,temperature_k\n" << std::setprecision(17);
  check_domain(model.temperatures_k());
  emit_nodes(0.0, config, model.temperatures_k());

  long double boundary_loss = 0.0L;
  std::uint64_t global_step = 0;
  double previous = 0.0;
  for (std::uint64_t slot = 0; slot < schedule.slot_count; ++slot) {
    for (std::uint64_t local = 1; local <= schedule.steps_per_slot; ++local) {
      ++global_step;
      const double target = local == schedule.steps_per_slot
                                ? static_cast<double>(slot + 1) * options.slot_s
                                : static_cast<double>(slot) * options.slot_s +
                                      static_cast<double>(local) * options.step_s;
      const double dt = target - previous;
      model.advance_to(target);
      check_domain(model.temperatures_k());
      for (std::size_t index = 0; index < config.nodes.size(); ++index) {
        const auto& node = config.nodes[index];
        boundary_loss += static_cast<long double>(dt) *
                         static_cast<long double>(node.boundary_conductance_w_per_k) *
                         static_cast<long double>(model.temperatures_k()[index] -
                                                  node.boundary_temperature_k);
      }
      if (global_step % schedule.steps_per_sample == 0)
        emit_nodes(target, config, model.temperatures_k());
      previous = target;
    }
  }
  require(global_step == schedule.total_steps, "internal executed step-count mismatch");

  long double applied = 0.0L;
  for (double energy : model.applied_energy_j()) applied += static_cast<long double>(energy);
  long double static_energy = 0.0L;
  long double stored = 0.0L;
  for (std::size_t index = 0; index < config.nodes.size(); ++index) {
    const auto& node = config.nodes[index];
    static_energy += static_cast<long double>(node.static_power_w) * options.end_s;
    stored += static_cast<long double>(node.heat_capacity_j_per_k) *
              static_cast<long double>(model.temperatures_k()[index] -
                                       node.initial_temperature_k);
  }
  write_receipt(options, schedule, config, model.factorization_count(),
                event_alignment_error_s, min_observed, max_observed, applied,
                declared_activity_energy, static_energy, stored, boundary_loss);
}

}  // namespace

int main(int argc, char** argv) try {
  const Options options = parse_options(argc, argv);
  const auto config = hbfsim::eq3_thermal::model_config_from_text(read_file(options.model_path));
  auto activities =
      hbfsim::eq3_thermal::activities_from_text(read_file(options.events_path), config);
  validate_initial_domain(config, options);
  const Schedule schedule = make_schedule(options);
  const double event_alignment_error_s =
      validate_and_canonicalize_activities(activities, options);
  if (options.inspect_only) {
    print_inspection(config, activities, options, schedule, event_alignment_error_s);
    return 0;
  }
  run(config, std::move(activities), options, schedule, event_alignment_error_s);
  return 0;
} catch (const std::exception& error) {
  std::cerr << "eq3_layered_rc_runner: " << error.what() << '\n';
  return 2;
}
