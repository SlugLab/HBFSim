#include "hbfsim/eq3_thermal/thermal.hpp"

#include <cctype>
#include <charconv>
#include <cmath>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

namespace hbfsim::eq3_thermal {
namespace {

std::string next_data_line(std::istringstream& input, std::size_t& line_number) {
  std::string line;
  while (std::getline(input, line)) {
    ++line_number;
    const auto first = line.find_first_not_of(" \t\r");
    if (first == std::string::npos || line[first] == '#') continue;
    return line.substr(first);
  }
  return {};
}

[[noreturn]] void fail(std::size_t line, const std::string& message) {
  throw std::invalid_argument("line " + std::to_string(line) + ": " + message);
}

void require_end(std::istringstream& line, std::size_t number) {
  std::string extra;
  if (line >> extra) fail(number, "unexpected trailing field '" + extra + "'");
}

PhysicalType physical(std::string value, std::size_t line) {
  if (value == "gpu") return PhysicalType::Gpu;
  if (value == "gddr") return PhysicalType::Gddr;
  if (value == "hbm") return PhysicalType::Hbm;
  if (value == "hbf") return PhysicalType::Hbf;
  if (value == "interposer") return PhysicalType::Interposer;
  if (value == "cooling") return PhysicalType::Cooling;
  if (value == "other") return PhysicalType::Other;
  fail(line, "unknown physical type '" + value + "'");
}
LogicalRole logical(std::string value, std::size_t line) {
  if (value == "compute") return LogicalRole::Compute;
  if (value == "fast_memory") return LogicalRole::FastMemory;
  if (value == "capacity_memory") return LogicalRole::CapacityMemory;
  if (value == "package") return LogicalRole::Package;
  if (value == "cooling") return LogicalRole::Cooling;
  if (value == "other") return LogicalRole::Other;
  fail(line, "unknown logical role '" + value + "'");
}
ConductanceKind conductance_kind(std::string value, std::size_t line) {
  if (value == "intra") return ConductanceKind::IntraComponent;
  if (value == "component") return ConductanceKind::InterComponent;
  if (value == "cooling") return ConductanceKind::Cooling;
  fail(line, "edge kind must be intra, component, or cooling");
}
ActivityKind kind(std::string value, std::size_t line) {
  if (value == "read") return ActivityKind::Read;
  if (value == "program") return ActivityKind::Program;
  if (value == "erase") return ActivityKind::Erase;
  if (value == "link") return ActivityKind::Link;
  if (value == "relay") return ActivityKind::Relay;
  if (value == "refresh") return ActivityKind::Refresh;
  if (value == "external_heat") return ActivityKind::ExternalHeat;
  fail(line, "unknown activity kind '" + value + "'");
}
ActivitySource source(std::string value, std::size_t line) {
  if (value == "demand") return ActivitySource::Demand;
  if (value == "prefetch") return ActivitySource::Prefetch;
  if (value == "refresh") return ActivitySource::Refresh;
  if (value == "external") return ActivitySource::External;
  fail(line, "unknown activity source '" + value + "'");
}
std::optional<std::size_t> optional_index(long long value, std::size_t line) {
  if (value == -1) return std::nullopt;
  if (value < 0) fail(line, "optional index must be -1 or non-negative");
  return static_cast<std::size_t>(value);
}
std::uint64_t unsigned_integer(const std::string& token, std::size_t line,
                               const std::string& label) {
  if (token.empty() || token.front() == '-') fail(line, label + " must be non-negative");
  std::uint64_t value{};
  const auto [end, error] = std::from_chars(token.data(), token.data() + token.size(), value);
  if (error != std::errc{} || end != token.data() + token.size())
    fail(line, "invalid " + label + " '" + token + "'");
  return value;
}

}  // namespace

ThermalModelConfig model_config_from_text(std::string_view text) {
  std::istringstream input{std::string(text)};
  std::size_t number = 0;
  std::string first = next_data_line(input, number);
  std::istringstream header(first);
  std::string magic;
  int version{};
  if (!(header >> magic >> version) || magic != "HBFSIM_EQ3_THERMAL_MODEL" || version != 1)
    fail(number, "expected HBFSIM_EQ3_THERMAL_MODEL 1");
  require_end(header, number);

  ThermalModelConfig config;
  struct EdgeNames {
    std::string a;
    std::string b;
    double conductance;
    std::optional<ConductanceKind> kind;
    std::size_t line;
  };
  std::vector<EdgeNames> edges;
  std::unordered_map<std::string, std::size_t> node_indices;
  for (std::string raw; !(raw = next_data_line(input, number)).empty();) {
    std::istringstream line(raw);
    std::string record;
    line >> record;
    if (record == "coupling") {
      std::string enabled;
      if (!(line >> enabled) || (enabled != "on" && enabled != "off"))
        fail(number, "coupling must be on or off");
      config.direct_intercomponent_edges_enabled = enabled == "on";
      require_end(line, number);
    } else if (record == "node") {
      ThermalNode node;
      std::string physical_name, logical_name;
      long long die{};
      if (!(line >> node.id >> physical_name >> logical_name >> node.group_id >> die >>
            node.heat_capacity_j_per_k >> node.initial_temperature_k >> node.static_power_w >>
            node.boundary_conductance_w_per_k >> node.boundary_temperature_k))
        fail(number, "malformed node record");
      node.physical_type = physical(physical_name, number);
      node.logical_role = logical(logical_name, number);
      node.die_index = optional_index(die, number);
      if (!node_indices.emplace(node.id, config.nodes.size()).second)
        fail(number, "duplicate node id '" + node.id + "'");
      config.nodes.push_back(std::move(node));
      require_end(line, number);
    } else if (record == "edge") {
      EdgeNames edge;
      edge.line = number;
      if (!(line >> edge.a >> edge.b >> edge.conductance)) fail(number, "malformed edge record");
      std::string kind_name;
      if (line >> kind_name) edge.kind = conductance_kind(kind_name, number);
      edges.push_back(std::move(edge));
      require_end(line, number);
    } else {
      fail(number, "unknown model record '" + record + "'");
    }
  }
  for (const auto& edge : edges) {
    const auto a = node_indices.find(edge.a);
    const auto b = node_indices.find(edge.b);
    if (a == node_indices.end() || b == node_indices.end())
      fail(edge.line, "edge references unknown node");
    ConductanceKind kind = ConductanceKind::InterComponent;
    if (edge.kind) {
      kind = *edge.kind;
    } else if (config.nodes[a->second].physical_type == PhysicalType::Cooling ||
               config.nodes[b->second].physical_type == PhysicalType::Cooling) {
      kind = ConductanceKind::Cooling;
    } else if (!config.nodes[a->second].group_id.empty() &&
               config.nodes[a->second].group_id == config.nodes[b->second].group_id) {
      kind = ConductanceKind::IntraComponent;
    }
    config.edges.push_back({a->second, b->second, edge.conductance, kind});
  }
  validate_model_config(config);
  return config;
}

std::vector<PhysicalActivity> activities_from_text(
    std::string_view text, const ThermalModelConfig& config) {
  std::istringstream input{std::string(text)};
  std::size_t number = 0;
  std::string first = next_data_line(input, number);
  std::istringstream header(first);
  std::string magic;
  int version{};
  if (!(header >> magic >> version) || magic != "HBFSIM_EQ3_THERMAL_EVENTS" || version != 1)
    fail(number, "expected HBFSIM_EQ3_THERMAL_EVENTS 1");
  require_end(header, number);
  std::unordered_map<std::string, std::size_t> nodes;
  for (std::size_t i = 0; i < config.nodes.size(); ++i) nodes.emplace(config.nodes[i].id, i);
  std::vector<PhysicalActivity> result;
  for (std::string raw; !(raw = next_data_line(input, number)).empty();) {
    std::istringstream line(raw);
    std::string record, id_token, bytes_token, kind_name, source_name;
    long long stack{}, die{}, plane{};
    PhysicalActivity activity;
    if (!(line >> record) || record != "activity") fail(number, "expected activity record");
    if (!(line >> id_token >> activity.request_id >> kind_name >> source_name >>
          bytes_token >> stack >> die >> plane >> activity.start_time_s >>
          activity.end_time_s >> activity.completion_time_s))
      fail(number, "malformed activity record");
    activity.activity_id = unsigned_integer(id_token, number, "activity id");
    activity.bytes = unsigned_integer(bytes_token, number, "activity bytes");
    activity.kind = kind(kind_name, number);
    activity.source = source(source_name, number);
    activity.stack_index = optional_index(stack, number);
    activity.die_index = optional_index(die, number);
    activity.plane_index = optional_index(plane, number);
    std::string node_id;
    double energy{};
    while (line >> node_id) {
      if (!(line >> energy)) fail(number, "node energy requires NODE_ID ENERGY_J pair");
      const auto found = nodes.find(node_id);
      if (found == nodes.end()) fail(number, "activity references unknown node '" + node_id + "'");
      activity.node_energy.push_back({found->second, energy});
    }
    if (activity.node_energy.empty()) fail(number, "activity requires at least one node energy pair");
    validate_physical_activity(activity, config.nodes.size());
    result.push_back(std::move(activity));
  }
  return result;
}

}  // namespace hbfsim::eq3_thermal
