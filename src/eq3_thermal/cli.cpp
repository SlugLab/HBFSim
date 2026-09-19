#include "hbfsim/eq3_thermal/thermal.hpp"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {
std::string read_file(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path);
  std::ostringstream contents;
  contents << input.rdbuf();
  return contents.str();
}
std::string physical_name(hbfsim::eq3_thermal::PhysicalType type) {
  using hbfsim::eq3_thermal::PhysicalType;
  switch (type) {
    case PhysicalType::Gpu: return "gpu";
    case PhysicalType::Gddr: return "gddr";
    case PhysicalType::Hbm: return "hbm";
    case PhysicalType::Hbf: return "hbf";
    case PhysicalType::Interposer: return "interposer";
    case PhysicalType::Cooling: return "cooling";
    case PhysicalType::Other: return "other";
  }
  return "other";
}
std::string logical_name(hbfsim::eq3_thermal::LogicalRole role) {
  using hbfsim::eq3_thermal::LogicalRole;
  switch (role) {
    case LogicalRole::Compute: return "compute";
    case LogicalRole::FastMemory: return "fast_memory";
    case LogicalRole::CapacityMemory: return "capacity_memory";
    case LogicalRole::Package: return "package";
    case LogicalRole::Cooling: return "cooling";
    case LogicalRole::Other: return "other";
  }
  return "other";
}
}  // namespace

int main(int argc, char** argv) try {
  std::string model_path, events_path;
  std::string mode = "off";
  double step_s = 0.0, end_s = 0.0;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if ((arg == "--mode" || arg == "--model" || arg == "--events" || arg == "--step-s" ||
         arg == "--end-s") && i + 1 >= argc)
      throw std::invalid_argument("missing value after " + arg);
    if (arg == "--mode") mode = argv[++i];
    else if (arg == "--model") model_path = argv[++i];
    else if (arg == "--events") events_path = argv[++i];
    else if (arg == "--step-s") step_s = std::stod(argv[++i]);
    else if (arg == "--end-s") end_s = std::stod(argv[++i]);
    else throw std::invalid_argument("unknown argument " + arg);
  }
  const char* csv_header =
      "time_s,record_type,location,physical_type,logical_role,source,valid,value_k,hotspot_k,mean_k,node_count\n";
  using namespace hbfsim::eq3_thermal;
  if (mode == "off") {
    ThermalRuntime runtime(RuntimeMode::Off, std::nullopt);
    std::cout << csv_header;
    return runtime.solver_constructed() ? 3 : 0;
  }
  if (mode == "active")
    throw std::invalid_argument("--mode active is NOT_IMPLEMENTED in P1");
  if (mode != "read_only" && mode != "shadow")
    throw std::invalid_argument("mode must be off, read_only, shadow, or active");
  if (model_path.empty() || events_path.empty())
    throw std::invalid_argument("read_only/shadow modes require --model and --events");
  if (mode == "shadow" && (!std::isfinite(step_s) || step_s <= 0.0 ||
                           !std::isfinite(end_s) || end_s <= 0.0))
    throw std::invalid_argument(
        "usage: hbfsim_eq3_thermal_cli --mode shadow --model FILE --events FILE --step-s DT --end-s END");

  auto config = model_config_from_text(read_file(model_path));
  auto activities = activities_from_text(read_file(events_path), config);
  if (mode == "read_only") {
    ThermalRuntime runtime(RuntimeMode::ReadOnly, std::nullopt);
    std::cout << csv_header;
    return runtime.solver_constructed() ? 3 : 0;
  }
  ThermalModel model(config);
  for (auto& activity : activities) model.add_activity(std::move(activity));

  std::cout << csv_header << std::setprecision(17);
  SimulatedTemperatureProvider provider(model);
  while (model.time_s() < end_s) {
    model.advance_to(std::min(end_s, model.time_s() + step_s));
    for (const auto& reading : provider.snapshot().nodes)
      std::cout << model.time_s() << ",node," << reading.location << ','
                << physical_name(reading.physical_type) << ','
                << logical_name(reading.logical_role) << ",SIMULATED,1,"
                << *reading.value_k << ",,,\n";
    for (const auto& group : model.grouped_temperatures())
      std::cout << model.time_s() << ",group," << group.group_id << ','
                << physical_name(group.physical_type) << ',' << logical_name(group.logical_role)
                << ",SIMULATED,1,," << group.hotspot_k
                << ',' << group.mean_k << ',' << group.node_count << '\n';
  }
  return 0;
} catch (const std::exception& error) {
  std::cerr << "hbfsim_eq3_thermal_cli: " << error.what() << '\n';
  return 2;
}
