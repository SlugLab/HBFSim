// Isolated scale probe for the existing P1 core; no runtime integration.
#include "hbfsim/eq3_thermal/thermal.hpp"
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <sys/resource.h>
using namespace hbfsim::eq3_thermal;
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  const auto count = std::stoul(argv[1]);
  if (count != 256 && count != 1024) return 2;
  constexpr std::size_t n = 32;
  ThermalModelConfig cfg;
  for (std::size_t i=0; i<n; ++i) {
    ThermalNode node;
    node.id="node"+std::to_string(i); node.group_id="closed";
    node.heat_capacity_j_per_k=1.; node.initial_temperature_k=300.;
    node.boundary_temperature_k=300.; cfg.nodes.push_back(node);
    if (i) cfg.edges.push_back({i-1,i,0.1,ConductanceKind::IntraComponent});
  }
  ThermalModel m(cfg);
  for (std::size_t i=0; i<count; ++i) {
    PhysicalActivity a;
    a.activity_id=i+1; a.request_id=std::to_string(i+1);
    a.start_time_s=i*.01;
    a.end_time_s=a.start_time_s+.001+(i%97)*.000001;
    a.completion_time_s=a.end_time_s+.0003;
    a.node_energy={{i%n,.01}};
    m.add_activity(a);
  }
  const auto start=std::chrono::steady_clock::now();
  const double end=count*.01;
  m.advance_to(end);
  const auto elapsed=std::chrono::duration<double>(
    std::chrono::steady_clock::now()-start).count();
  const auto completions=m.take_completions();
  const auto cp=m.checkpoint();
  double energy=0., stored=0.;
  for (std::size_t i=0;i<n;++i) {
    energy+=m.applied_energy_j()[i];
    stored+=m.temperatures_k()[i]-300.;
  }
  rusage ru{}; getrusage(RUSAGE_SELF,&ru);
  const bool pass=std::abs(energy-count*.01)<1e-8 &&
    std::abs(stored-energy)<1e-7 && completions.size()==count &&
    cp.pending_activities.empty() && cp.seen_activity_ids.size()==count;
  std::cout<<std::setprecision(17)
    <<"{\"classification\":\"SOFTWARE_SCALE_PROBE\",\"events\":"<<count
    <<",\"nodes\":"<<n<<",\"simulated_s\":"<<end
    <<",\"solver_wall_s\":"<<elapsed
    <<",\"wall_s_per_simulated_s\":"<<elapsed/end
    <<",\"factorization_count_and_distinct_dt\":"<<m.factorization_count()
    <<",\"factor_payload_bytes_excluding_map_allocator\":"<<m.factorization_count()*n*n*sizeof(double)
    <<",\"seen_id_count\":"<<cp.seen_activity_ids.size()
    <<",\"pending_after_run\":"<<cp.pending_activities.size()
    <<",\"peak_rss_kib\":"<<ru.ru_maxrss
    <<",\"applied_J\":"<<energy<<",\"stored_J\":"<<stored
    <<",\"completed\":"<<completions.size()
    <<",\"checks_pass\":"<<(pass?"true":"false")<<"}\n";
  return pass?0:1;
}
