#include "transform.hpp"

#include <json.hpp>

#include <exception>
#include <iostream>
#include <iterator>
#include <string>

namespace {

nlohmann::json coverage_json(const hbfsim::ptx::CoverageManifest& coverage)
{
    return {
        {"rewritten_instructions", coverage.rewritten_instructions},
        {"unsupported_instructions", coverage.unsupported_instructions},
        {"excluded_functions", coverage.excluded_functions},
        {"unsupported_opcodes", coverage.unsupported_opcodes},
    };
}

}  // namespace

int main()
{
    try {
        const std::string text{std::istreambuf_iterator<char>(std::cin), {}};
        const auto root = nlohmann::json::parse(text);
        const auto& input = root.contains("input") ? root.at("input") : root;
        hbfsim::ptx::TransformRequest request{
            .full_ptx = input.at("full_ptx").get<std::string>(),
            .to_patch_kernel = input.value("to_patch_kernel", ""),
            .global_ebpf_map_info_symbol =
                input.value("global_ebpf_map_info_symbol", "map_info"),
            .ebpf_communication_data_symbol =
                input.value("ebpf_communication_data_symbol", "constData"),
        };
        const auto transformed = hbfsim::ptx::transform_ptx(request);
        const nlohmann::json response{
            {"output_ptx", transformed.output_ptx},
            {"modified", transformed.modified},
            {"coverage", coverage_json(transformed.coverage)},
        };
        std::cout << response.dump();
        return 0;
    } catch (const nlohmann::json::exception& error) {
        std::cerr << "ptxpass_hbf configuration error: " << error.what() << '\n';
        return 64;
    } catch (const std::exception& error) {
        std::cerr << "ptxpass_hbf error: " << error.what() << '\n';
        return 70;
    }
}
