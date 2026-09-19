#include "hbfsim/eq3_thermal/thermal.hpp"

#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

using namespace hbfsim::eq3_thermal;

namespace {

void check(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}
void near(double actual, double expected, double tolerance, const std::string& message) {
  if (!std::isfinite(actual) || !std::isfinite(expected) ||
      !std::isfinite(tolerance) || tolerance < 0.0 ||
      std::abs(actual - expected) > tolerance)
    throw std::runtime_error(message + ": got " + std::to_string(actual) +
                             ", expected " + std::to_string(expected));
}
template <class Function>
void rejects(Function function, const std::string& message) {
  try { function(); } catch (const std::invalid_argument&) { return; }
  throw std::runtime_error(message);
}

ThermalNode node(std::string id, PhysicalType type = PhysicalType::Hbf,
                 std::string group = "hbf0", double capacity = 1.0,
                 double initial = 300.0, double boundary_g = 0.0,
                 double boundary_k = 295.0, double static_power = 0.0) {
  return {std::move(id), type,
          type == PhysicalType::Gpu ? LogicalRole::Compute : LogicalRole::CapacityMemory,
          std::move(group), std::nullopt, capacity, initial, static_power, boundary_g,
          boundary_k};
}
PhysicalActivity activity(std::uint64_t id, double start, double end, double completion,
                          double energy, std::size_t target = 0,
                          ActivitySource source = ActivitySource::Demand) {
  return {id, "request-" + std::to_string(id), ActivityKind::Read, source, 4096,
          0, 0, 0, start, end, completion, {{target, energy}}};
}

void test_zero_power_cooldown() {
  ThermalModel model({{node("hbf", PhysicalType::Hbf, "hbf0", 2.0, 330.0, 1.0, 300.0)}, {}});
  model.advance_to(1.0);
  check(model.temperatures_k()[0] < 330.0 && model.temperatures_k()[0] > 300.0,
        "zero-power node must cool toward its boundary");
}

void test_one_node_implicit_analytic_and_cache() {
  constexpr double kTolerance = 1e-12;  // Fixed before executing the fixture.
  ThermalModel model({{node("hbf", PhysicalType::Hbf, "hbf0", 2.0, 310.0, 0.5, 300.0,
                            4.0)}, {}});
  model.advance_to(0.25);
  const double expected = ((2.0 / 0.25) * 310.0 + 4.0 + 0.5 * 300.0) /
                          (2.0 / 0.25 + 0.5);
  near(model.temperatures_k()[0], expected, kTolerance,
       "implicit Euler result must match closed-form discrete equation");
  model.advance_to(0.5);
  check(model.factorization_count() == 1, "equal time steps must reuse Cholesky factorization");
}

void test_continuous_analytic_timestep_convergence() {
  auto fixture = node("hbf", PhysicalType::Hbf, "hbf0", 2.0, 320.0, 1.0, 300.0);
  ThermalModel full({{fixture}, {}}), half({{fixture}, {}});
  full.advance_to(1.0);
  half.advance_to(0.5);
  half.advance_to(1.0);
  const double exact = 300.0 + 20.0 * std::exp(-1.0 / 2.0);
  const double full_error = std::abs(full.temperatures_k()[0] - exact);
  const double half_error = std::abs(half.temperatures_k()[0] - exact);
  constexpr double kHalfStepMaxErrorK = 0.7;  // Frozen analytic tolerance.
  check(half_error < full_error && half_error < kHalfStepMaxErrorK,
        "halving implicit-Euler step must converge toward continuous analytic cooling");
}

void test_closed_system_conservation() {
  auto a = node("gpu", PhysicalType::Gpu, "gpu", 2.0, 320.0);
  auto b = node("hbf", PhysicalType::Hbf, "hbf0", 3.0, 290.0);
  ThermalModel model({{a, b}, {{0, 1, 4.0}}, true});
  const double initial = 2.0 * 320.0 + 3.0 * 290.0;
  model.advance_to(3.0);
  near(2.0 * model.temperatures_k()[0] + 3.0 * model.temperatures_k()[1], initial,
       1e-10, "internal conductance must conserve thermal energy");
}

void test_gpu_hbm_hbf_coupling_toggle() {
  ThermalModelConfig coupled{{node("gpu", PhysicalType::Gpu, "gpu", 5.0, 330.0),
                              node("hbm", PhysicalType::Hbm, "hbm0", 2.0, 300.0),
                              node("hbf", PhysicalType::Hbf, "hbf0", 2.0, 300.0)},
                             {{0, 1, 1.0}, {1, 2, 1.0}}, true};
  auto uncoupled = coupled;
  uncoupled.direct_intercomponent_edges_enabled = false;
  ThermalModel on(coupled), off(uncoupled);
  on.advance_to(1.0);
  off.advance_to(1.0);
  check(on.temperatures_k()[1] > off.temperatures_k()[1] &&
            on.temperatures_k()[2] > off.temperatures_k()[2],
        "GPU/HBM/HBF coupling must transfer heat when enabled");
  near(off.temperatures_k()[0], 330.0, 1e-12, "coupling-off must preserve self baseline");
}

void test_no_component_coupling_preserves_intra_and_cooling() {
  ThermalModelConfig config{{node("gpu", PhysicalType::Gpu, "gpu", 2.0, 330.0),
                             node("hbf_base", PhysicalType::Hbf, "hbf0", 1.0, 300.0),
                             node("hbf_die", PhysicalType::Hbf, "hbf0", 1.0, 300.0),
                             node("sink", PhysicalType::Cooling, "sink", 10.0, 290.0,
                                  1.0, 290.0)},
                            {{0, 1, 1.0, ConductanceKind::InterComponent},
                             {1, 2, 1.0, ConductanceKind::IntraComponent},
                             {2, 3, 1.0, ConductanceKind::Cooling}},
                            false};
  ThermalModel model(config);
  auto no_intra_config = config;
  no_intra_config.edges.erase(no_intra_config.edges.begin() + 1);
  ThermalModel no_intra(no_intra_config);
  model.add_activity(activity(88, 0.0, 1.0, 1.0, 10.0, 1));
  no_intra.add_activity(activity(88, 0.0, 1.0, 1.0, 10.0, 1));
  model.advance_to(1.0);
  no_intra.advance_to(1.0);
  near(model.temperatures_k()[0], 330.0, 1e-12,
       "inter-component GPU path must be disabled");
  check(model.temperatures_k()[2] > no_intra.temperatures_k()[2],
        "intra-stack path must remain enabled in no-component-coupling mode");
  check(model.temperatures_k()[3] > 290.0,
        "cooling path must remain enabled in no-component-coupling mode");
}

void test_invalid_config_and_clock() {
  auto invalid = node("bad");
  invalid.heat_capacity_j_per_k = -1.0;
  rejects([&] { ThermalModel model({{invalid}, {}}); }, "negative capacity must be rejected");
  invalid.heat_capacity_j_per_k = std::numeric_limits<double>::quiet_NaN();
  rejects([&] { ThermalModel model({{invalid}, {}}); }, "nonfinite capacity must be rejected");
  ThermalModel model({{node("ok")}, {}});
  rejects([&] { model.advance_to(0.0); }, "non-monotonic clock must be rejected");
  rejects([&] { model.add_activity(activity(1, -1.0, 1.0, 1.0, 1.0)); },
          "activity before current clock must be rejected");
  model.add_activity(activity(1, 0.0, 1.0, 1.0, 1.0));
}

void test_multiwindow_energy_completion_and_duplicate() {
  ThermalModel model({{node("hbf")}, {}});
  model.add_activity(activity(7, 0.0, 5.0, 7.0, 10.0, 0, ActivitySource::Prefetch));
  model.advance_to(1.0);
  near(model.applied_energy_j()[0], 2.0, 1e-12, "first overlap energy");
  check(model.take_completions().empty(), "future completion must not appear early");
  model.advance_to(3.0);
  near(model.applied_energy_j()[0], 6.0, 1e-12, "middle overlap energy");
  model.advance_to(5.0);
  near(model.applied_energy_j()[0], 10.0, 1e-12, "full activity energy exactly once");
  check(model.take_completions().empty(), "completion after physical work remains future");
  model.advance_to(7.0);
  auto completions = model.take_completions();
  check(completions.size() == 1 && completions[0].request_id == "request-7" &&
            completions[0].source == ActivitySource::Prefetch &&
            completions[0].completion_time_s == 7.0,
        "completion must retain request/source/actual time");
  rejects([&] { model.add_activity(activity(7, 7.0, 8.0, 8.0, 1.0)); },
          "duplicate activity id must be rejected");
}

void test_checkpoint_roundtrip_reproducibility() {
  ThermalModelConfig config{{node("hbf", PhysicalType::Hbf, "hbf0", 2.0, 300.0, 0.2,
                                  295.0)}, {}};
  ThermalModel original(config), restored(config);
  original.add_activity(activity(11, 0.0, 4.0, 5.0, 8.0));
  original.advance_to(2.0);
  const auto text = checkpoint_to_text(original.checkpoint());
  restored.restore(checkpoint_from_text(text));
  original.advance_to(5.0);
  restored.advance_to(5.0);
  near(restored.temperatures_k()[0], original.temperatures_k()[0], 1e-12,
       "restored trajectory must reproduce original");
  near(restored.applied_energy_j()[0], 8.0, 1e-12, "checkpoint must preserve pending energy");
  check(restored.take_completions().size() == 1, "checkpoint must preserve future completion");
  auto changed = config;
  changed.nodes[0].heat_capacity_j_per_k = 3.0;
  ThermalModel mismatched(changed);
  rejects([&] { mismatched.restore(checkpoint_from_text(text)); },
          "model identity mismatch must be rejected");
  auto corrupt = original.checkpoint();
  corrupt.pending_activities.push_back(activity(99, 5.0, 6.0, 6.0, 1.0, 999));
  corrupt.seen_activity_ids.push_back(99);
  const double old_time = restored.time_s();
  const double old_temperature = restored.temperatures_k()[0];
  rejects([&] { restored.restore(corrupt); },
          "untrusted checkpoint node index must be rejected");
  near(restored.time_s(), old_time, 0.0, "rejected restore must be atomic in time");
  near(restored.temperatures_k()[0], old_temperature, 0.0,
       "rejected restore must be atomic in state");
}

void test_pulse_timing_preserved_inside_wide_advance() {
  ThermalModelConfig config{{node("hbf", PhysicalType::Hbf, "hbf0", 1.0, 300.0,
                                  1.0, 300.0)}, {}};
  ThermalModel early(config), late(config);
  early.add_activity(activity(1, 0.0, 1.0, 1.0, 10.0));
  late.add_activity(activity(2, 9.0, 10.0, 10.0, 10.0));
  early.advance_to(10.0);
  late.advance_to(10.0);
  near(early.applied_energy_j()[0], 10.0, 1e-12, "early pulse energy conservation");
  near(late.applied_energy_j()[0], 10.0, 1e-12, "late pulse energy conservation");
  check(late.temperatures_k()[0] > early.temperatures_k()[0],
        "activity boundaries must preserve early-vs-late pulse timing");
}

void test_sensors_groups_and_missing() {
  auto base = node("hbf0-base", PhysicalType::Hbf, "hbf0", 1.0, 300.0);
  base.die_index = 0;
  auto die = node("hbf0-die1", PhysicalType::Hbf, "hbf0", 1.0, 320.0);
  die.die_index = 1;
  ThermalModel model({{base, die}, {}});
  auto groups = model.grouped_temperatures();
  check(groups.size() == 1 && groups[0].hotspot_k == 320.0 && groups[0].mean_k == 310.0,
        "hotspot and mean must remain separate");
  SimulatedTemperatureProvider simulated(model);
  check(simulated.snapshot().nodes[0].source == SensorSource::Simulated,
        "simulated provider must label its source");
  UnavailableTemperatureProvider unavailable(3.0, {"gpu", "hbf0"});
  for (const auto& reading : unavailable.snapshot().nodes)
    check(!reading.valid && !reading.value_k.has_value() &&
              reading.source == SensorSource::Unavailable,
          "missing sensor must be null, never fake zero");
}

void test_generic_die_and_stack_counts() {
  for (std::size_t dies : {std::size_t{8}, std::size_t{16}, std::size_t{3}}) {
    ThermalModelConfig config;
    for (std::size_t i = 0; i < dies; ++i) {
      auto current = node("die" + std::to_string(i), PhysicalType::Hbf, "hbf0");
      current.die_index = i;
      config.nodes.push_back(std::move(current));
      if (i) config.edges.push_back({i - 1, i, 0.1});
    }
    ThermalModel model(config);
    model.advance_to(0.1);
    check(model.temperatures_k().size() == dies,
          "model must support 8, 16, and nonstandard generic die counts");
  }
}

void test_text_interface_and_runtime_modes() {
  const std::string model_text = R"(HBFSIM_EQ3_THERMAL_MODEL 1
coupling on
node gpu gpu compute gpu -1 10 300 1 0.5 295
node gddr0 gddr fast_memory gddr0 0 2 300 0.2 0.2 295
node hbf0 hbf capacity_memory hbf0 0 2 300 0.2 0.2 295
edge gpu hbf0 0.1
)";
  const auto config = model_config_from_text(model_text);
  check(config.nodes[1].physical_type == PhysicalType::Gddr &&
            config.nodes[1].logical_role == LogicalRole::FastMemory,
        "GDDR physical type and logical role must remain distinct");
  const std::string events = R"(HBFSIM_EQ3_THERMAL_EVENTS 1
activity 1 reqA read demand 4096 0 0 0 0 1 1 hbf0 2 gpu 1
activity 2 refreshA refresh refresh 8192 0 1 0 1 2 3 hbf0 4
)";
  auto parsed = activities_from_text(events, config);
  check(parsed.size() == 2 && parsed[1].source == ActivitySource::Refresh &&
            parsed[0].node_energy.size() == 2,
        "line event format must preserve source and multi-node energy");
  rejects([&] {
    activities_from_text("HBFSIM_EQ3_THERMAL_EVENTS 1\n"
                         "activity -1 req read demand 1 0 0 0 0 1 1 hbf0 1\n",
                         config);
  }, "negative activity id must be rejected before unsigned conversion");
  rejects([&] {
    activities_from_text("HBFSIM_EQ3_THERMAL_EVENTS 1\n"
                         "activity 9 req read demand -1 0 0 0 0 1 1 hbf0 1\n",
                         config);
  }, "negative byte count must be rejected before unsigned conversion");
  rejects([&] {
    activities_from_text("HBFSIM_EQ3_THERMAL_EVENTS 1\n"
                         "activity 9 req read demand 1 0 0 0 0 1 1 hbf0 -1\n",
                         config);
  }, "read-only activity validation must reject negative energy");
  rejects([&] {
    activities_from_text("HBFSIM_EQ3_THERMAL_EVENTS 1\n"
                         "activity 9 req read demand 1 0 0 0 -2 -1 -1 hbf0 1\n",
                         config);
  }, "read-only activity validation must reject negative simulation times");
  ThermalRuntime off(RuntimeMode::Off, std::nullopt);
  ThermalRuntime read_only(RuntimeMode::ReadOnly, std::nullopt);
  ThermalRuntime shadow(RuntimeMode::Shadow, config);
  check(!off.solver_constructed() && !read_only.solver_constructed() &&
            shadow.solver_constructed(),
        "off/read_only must stay solver-free and shadow must construct solver");
  rejects([&] { ThermalRuntime active(RuntimeMode::Active, config); },
          "P1 active mode must be explicitly rejected");
}

}  // namespace

int main() {
  const std::vector<std::pair<std::string, std::function<void()>>> tests = {
      {"zero_power_cooldown", test_zero_power_cooldown},
      {"one_node_implicit_analytic_and_cache", test_one_node_implicit_analytic_and_cache},
      {"continuous_analytic_timestep_convergence",
       test_continuous_analytic_timestep_convergence},
      {"closed_system_conservation", test_closed_system_conservation},
      {"gpu_hbm_hbf_coupling_toggle", test_gpu_hbm_hbf_coupling_toggle},
      {"no_component_coupling_preserves_intra_and_cooling",
       test_no_component_coupling_preserves_intra_and_cooling},
      {"invalid_config_and_clock", test_invalid_config_and_clock},
      {"multiwindow_energy_completion_and_duplicate", test_multiwindow_energy_completion_and_duplicate},
      {"checkpoint_roundtrip_reproducibility", test_checkpoint_roundtrip_reproducibility},
      {"pulse_timing_preserved_inside_wide_advance",
       test_pulse_timing_preserved_inside_wide_advance},
      {"sensors_groups_and_missing", test_sensors_groups_and_missing},
      {"generic_die_and_stack_counts", test_generic_die_and_stack_counts},
      {"text_interface_and_runtime_modes", test_text_interface_and_runtime_modes},
  };
  std::size_t failures = 0;
  for (const auto& [name, test] : tests) {
    try {
      test();
      std::cout << "PASS " << name << '\n';
    } catch (const std::exception& error) {
      ++failures;
      std::cerr << "FAIL " << name << ": " << error.what() << '\n';
    }
  }
  std::cout << "SUMMARY passed=" << (tests.size() - failures) << " failed=" << failures
            << '\n';
  return failures == 0 ? 0 : 1;
}
