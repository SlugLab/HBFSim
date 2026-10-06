#include <hbfsim/ucie/multistack_profile.hpp>

#include <json.hpp>

#include <iostream>
#include <stdexcept>

int main(int argc,char** argv)
{
    if (argc!=2) return 2;
    try {
        const auto p=hbfsim::ucie::load_multistack_profile(argv[1]);
        const auto& link=p.device.link;
        const nlohmann::json result={
            {"schema","hbfsim.ucie.multistack.resolved.v1"},
            {"name",p.name},
            {"top_profile_path",p.top_profile_path.string()},
            {"link_profile_path",p.link_profile_path.string()},
            {"media_profile_path",p.media_profile_path.string()},
            {"stack_count",p.stack_count},
            {"modules_per_stack",p.modules_per_stack},
            {"total_capacity_bytes",p.stack_capacity_bytes*p.stack_count},
            {"stack_capacity_bytes",p.stack_capacity_bytes},
            {"module_capacity_bytes",p.stack_capacity_bytes/
                                     p.modules_per_stack},
            {"total_service_bandwidth_bytes_per_s",
             p.total_service_bandwidth_bytes_per_s},
            {"per_stack_service_bandwidth_bytes_per_s",
             p.per_stack_service_bandwidth_bytes_per_s},
            {"service_cap_source","SCENARIO_ASSUMPTION"},
            {"service_cap_applied_once_per_stack",true},
            {"upstream_topology",p.shared_upstream?"shared":"independent"},
            {"ocp_host_channels",p.device.layout.host_channels},
            {"ocp_ncdus",p.device.layout.core_dies_per_channel},
            {"banks_per_die",p.device.layout.banks_per_die},
            {"buffers_per_bank",2},
            {"reduced_topology_scenario_assumption",
             p.device.layout.scenario_assumption},
            {"page_bytes",p.device.media.page_bytes},
            {"ucie_lanes",link.lanes},
            {"ucie_gt_per_second",link.gt_per_second},
            {"aou_profile_id",link.aou_hbf_profile_id},
            {"aou_hbf_profile_under_development",true},
            {"link_initial_ar_granules",link.initial_ar_granules},
            {"link_initial_r_granules",link.initial_r_granules},
            {"link_propagation_ns",link.propagation_ns},
            {"software_limits",{{"max_parent_requests",
                 p.software.max_parent_requests},
                {"max_child_records",p.software.max_child_records},
                {"max_read_bytes",p.software.max_read_bytes},
                {"max_backing_ranges",p.software.max_backing_ranges},
                {"host_reassembly_capacity_bytes",
                 p.software.host_reassembly_capacity_bytes}}}
        };
        std::cout << result.dump() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
