#include <hbfsim/package_thermal.hpp>

#include <json.hpp>

#include <cassert>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

namespace thermal = hbfsim::package_thermal;

namespace {

using Json = nlohmann::json;

thermal::Provenance model_provenance()
{
    return thermal::Provenance{
        .evidence = thermal::EvidenceClass::CalibrationOrSensitivity,
        .source = "synthetic unit test",
        .locator = "package_thermal_test",
        .note = "not scientific calibration",
    };
}

thermal::SourcedScalar scalar(double value, std::string unit)
{
    return thermal::SourcedScalar{value, std::move(unit), model_provenance()};
}

thermal::PackageTopology topology(std::uint32_t channels = 1,
                                  std::uint32_t stack_height = 8)
{
    std::vector<std::string> nodes{"gpu", "hbm", "base"};
    std::vector<thermal::DieMapping> mappings;
    for (std::uint32_t stack = 0; stack < channels; ++stack) {
        for (std::uint32_t layer = 0; layer < stack_height; ++layer) {
            nodes.push_back("nand.s" + std::to_string(stack) + ".l" +
                            std::to_string(layer));
        }
    }
    for (std::uint32_t channel = 0; channel < channels; ++channel) {
        const auto stack = channels - channel - 1;
        for (std::uint32_t die = 0; die < stack_height; ++die) {
            const auto layer = (die + 3) % stack_height;
            mappings.push_back(thermal::DieMapping{
                .channel = channel,
                .chip = 0,
                .die = die,
                .package_stack = stack,
                .vertical_layer = layer,
                .thermal_node = "nand.s" + std::to_string(stack) + ".l" +
                                std::to_string(layer),
            });
        }
    }
    return thermal::PackageTopology{
        thermal::PhysicalGeometry{channels, 1, stack_height, 2}, stack_height,
        std::move(nodes), std::move(mappings), model_provenance()};
}

thermal::NandEnergyModel energy_model()
{
    return thermal::NandEnergyModel{
        .read = {scalar(2.0, "J"), scalar(0.0, "J/byte")},
        .program = {scalar(3.0, "J"), scalar(0.0, "J/byte")},
        .erase = {scalar(4.0, "J"), scalar(0.0, "J/byte")},
    };
}

thermal::PolicyConfig policy_config()
{
    return thermal::PolicyConfig{
        .light_on_c = scalar(80.0, "C"),
        .light_off_c = scalar(75.0, "C"),
        .severe_on_c = scalar(90.0, "C"),
        .severe_off_c = scalar(85.0, "C"),
        .shutdown_on_c = scalar(100.0, "C"),
        .shutdown_off_c = scalar(92.0, "C"),
        .light_scale = scalar(0.5, "ratio"),
        .debounce_samples = 2,
        .minimum_dwell_samples = 1,
        .timing_provenance = model_provenance(),
    };
}

bool throws(const std::function<void()>& operation)
{
    try {
        operation();
    } catch (const thermal::ThermalError&) {
        return true;
    }
    return false;
}

Json read_json(const std::filesystem::path& path)
{
    std::ifstream input(path);
    assert(input.good());
    Json value;
    input >> value;
    assert(input.good());
    return value;
}

std::filesystem::path write_temporary_profile(const Json& value,
                                              const std::string& name)
{
    const auto path = std::filesystem::temp_directory_path() / name;
    std::ofstream output(path, std::ios::trunc);
    output << value.dump(2) << '\n';
    assert(output.good());
    return path;
}

Json phase2_provenance()
{
    return Json{{"class", "C"},
                {"source", "synthetic Phase-II semantics test"},
                {"locator", "package_thermal_test"},
                {"note", "semantic fixture, not physical calibration"}};
}

Json phase2_provider(double watts)
{
    return Json{{"kind", "synthetic"},
                {"provenance", phase2_provenance()},
                {"interpolation", "hold"},
                {"samples", Json::array({Json{{"relative_time_ns", 0},
                                               {"watts", watts}}})}};
}

Json phase2_base(const std::filesystem::path& root)
{
    auto value = read_json(
        root / "configs/package_thermal/synthetic-8hi.json");
    value.erase("gpu_power_semantics");
    value.erase("hbm_provider");
    value["power_model_evidence_level"] =
        Json{{"value", "sensitivity_only"},
             {"provenance", phase2_provenance()}};
    return value;
}

class SyntheticPackageModel final : public thermal::ThermalModel {
public:
    explicit SyntheticPackageModel(std::vector<std::string> names)
        : names_(std::move(names))
    {
    }

    void reset(double initial_temperature_c) override
    {
        initial_ = initial_temperature_c;
    }

    std::vector<double> step(std::span<const double> power) override
    {
        assert(power.size() == names_.size());
        std::vector<double> result(names_.size(), initial_);
        for (std::size_t index = 0; index < names_.size(); ++index) {
            if (names_[index].starts_with("hbf.")) result[index] = 95.0;
        }
        return result;
    }

    std::span<const std::string> input_names() const override { return names_; }
    std::span<const std::string> output_names() const override { return names_; }
    std::uint64_t sample_period_ns() const noexcept override { return 1000; }
    std::string identity() const override { return "synthetic-package-model"; }

private:
    std::vector<std::string> names_;
    double initial_{30.0};
};

void test_profile(const std::filesystem::path& root)
{
    auto profile = thermal::load_package_thermal_profile(
        root / "configs/package_thermal/synthetic-8hi.json");
    assert(profile.schema_version == 1);
    assert(profile.stage == thermal::ThermalStage::ReadOnly);
    assert(profile.topology.stack_height() == 8);
    assert(profile.topology.package_stack_count() == 2);
    assert(profile.evidence_label == "synthetic_fixture");
    assert(profile.legacy_power_schema);
    assert(!profile.timeline.enabled);
    const auto tall = thermal::load_package_thermal_profile(
        root / "configs/package_thermal/synthetic-16hi.json");
    assert(tall.topology.stack_height() == 16);
    assert(tall.topology.geometry().dies_per_chip == 16);
    assert(tall.topology.locate({0, 0, 15, 1}).vertical_layer == 4);

    std::ifstream source(root / "configs/package_thermal/synthetic-8hi.json");
    assert(source.good());
    std::string measured_without_dataset{
        std::istreambuf_iterator<char>(source), {}};
    const auto class_position = measured_without_dataset.find(
        "\"class\": \"C\"");
    assert(class_position != std::string::npos);
    measured_without_dataset.replace(class_position,
                                     std::string("\"class\": \"C\"").size(),
                                     "\"class\": \"M\"");
    const auto malformed_path = std::filesystem::temp_directory_path() /
                                "hbfsim-measured-without-dataset.json";
    {
        std::ofstream output(malformed_path, std::ios::trunc);
        output << measured_without_dataset;
        assert(output.good());
    }
    assert(throws([&] {
        (void)thermal::load_package_thermal_profile(malformed_path);
    }));
    std::filesystem::remove(malformed_path);

    profile.policy.light_scale.value = 1.1;
    assert(throws([&] { thermal::validate_package_thermal_profile(profile); }));

    auto gddr = phase2_base(root);
    gddr["name"] = "phase2-gddr7-semantics";
    gddr["package_architecture"] = "gddr_gpu_hbf";
    gddr["near_memory"] =
        Json{{"kind", "gddr7"},
             {"placement", "board"},
             {"provenance", phase2_provenance()},
             {"power_sources", Json::array()}};
    gddr["accelerator_power_semantics"] =
        Json{{"value", "gpu_plus_gddr_lumped"},
             {"provenance", phase2_provenance()}};
    gddr["timeline"] = Json{{"enabled", true}};
    const auto gddr_path = write_temporary_profile(
        gddr, "hbfsim-phase2-gddr7-profile.json");
    const auto gddr_profile =
        thermal::load_package_thermal_profile(gddr_path);
    assert(!gddr_profile.legacy_power_schema);
    assert(gddr_profile.package_architecture ==
           thermal::PackageArchitecture::GddrGpuHbf);
    assert(gddr_profile.near_memory.kind == thermal::NearMemoryKind::Gddr7);
    assert(gddr_profile.near_memory.power_sources.empty());
    assert(gddr_profile.accelerator_power_semantics ==
           thermal::AcceleratorPowerSemantics::GpuPlusGddrLumped);
    assert(gddr_profile.timeline.enabled);

    auto hbm = phase2_base(root);
    hbm["name"] = "phase2-hbm3e-semantics";
    hbm["package_architecture"] = "hbm_gpu_hbf";
    auto& node_names = hbm["topology"]["node_names"];
    for (auto& node : node_names) {
        if (node == "hbm") node = "hbm.s0";
    }
    node_names.push_back("hbm.s1");
    hbm["near_memory"] =
        Json{{"kind", "hbm3e"},
             {"placement", "interposer"},
             {"provenance", phase2_provenance()},
             {"power_sources",
              Json::array(
                  {Json{{"thermal_node", "hbm.s0"},
                        {"provider", phase2_provider(15.0)}},
                   Json{{"thermal_node", "hbm.s1"},
                        {"provider", phase2_provider(16.0)}}})}};
    hbm["accelerator_power_semantics"] =
        Json{{"value", "gpu_compute_plus_explicit_hbm"},
             {"provenance", phase2_provenance()}};
    const auto hbm_path = write_temporary_profile(
        hbm, "hbfsim-phase2-hbm3e-profile.json");
    auto hbm_profile = thermal::load_package_thermal_profile(hbm_path);
    assert(!hbm_profile.legacy_power_schema);
    assert(hbm_profile.package_architecture ==
           thermal::PackageArchitecture::HbmGpuHbf);
    assert(hbm_profile.near_memory.kind == thermal::NearMemoryKind::Hbm3e);
    assert(hbm_profile.near_memory.power_sources.size() == 2);
    auto hbm_model = std::make_unique<SyntheticPackageModel>(
        hbm_profile.topology.node_names());
    thermal::PackageThermalRuntime hbm_runtime(std::move(hbm_profile),
                                               std::move(hbm_model));
    assert(hbm_runtime.advance_to(1000).size() == 1);

    auto invalid = hbm;
    invalid["package_architecture"] = "gddr_gpu_hbf";
    const auto invalid_path = write_temporary_profile(
        invalid, "hbfsim-phase2-invalid-memory-profile.json");
    assert(throws([&] {
        (void)thermal::load_package_thermal_profile(invalid_path);
    }));

    invalid = gddr;
    invalid["accelerator_power_semantics"]["value"] =
        "gpu_compute_plus_explicit_hbm";
    const auto invalid_semantics_path = write_temporary_profile(
        invalid, "hbfsim-phase2-invalid-power-profile.json");
    assert(throws([&] {
        (void)thermal::load_package_thermal_profile(invalid_semantics_path);
    }));

    std::filesystem::remove(gddr_path);
    std::filesystem::remove(hbm_path);
    std::filesystem::remove(invalid_path);
    std::filesystem::remove(invalid_semantics_path);
}

void test_topology()
{
    const auto value = topology(2);
    const auto first = value.locate({0, 0, 0, 1});
    assert(first.package_stack == 1);
    assert(first.vertical_layer == 3);
    const auto second = value.locate({1, 0, 7, 0});
    assert(second.package_stack == 0);
    assert(second.vertical_layer == 2);
    assert(throws([&] { (void)value.locate({0, 0, 8, 0}); }));

    const auto tall = topology(2, 16);
    assert(tall.stack_height() == 16);
    assert(tall.node_names().size() == 35);
    const auto tall_top = tall.locate({0, 0, 15, 1});
    assert(tall_top.package_stack == 1);
    assert(tall_top.vertical_layer == 2);
    assert(tall.node_names()[tall_top.thermal_node_index] == "nand.s1.l2");
    assert(throws([&] { (void)tall.locate({1, 0, 16, 0}); }));

    std::vector<std::string> aggregated_nodes{"base"};
    std::vector<thermal::DieMapping> aggregated_mappings;
    for (std::uint32_t layer = 0; layer < 8; ++layer) {
        aggregated_nodes.push_back("aggregated.l" + std::to_string(layer));
    }
    for (std::uint32_t channel = 0; channel < 16; ++channel) {
        for (std::uint32_t die = 0; die < 8; ++die) {
            aggregated_mappings.push_back({
                channel, 0, die, 0, die,
                "aggregated.l" + std::to_string(die)});
        }
    }
    const thermal::PackageTopology aggregated(
        {16, 1, 8, 2}, 8, aggregated_nodes, aggregated_mappings,
        model_provenance());
    assert(aggregated.package_stack_count() == 1);
    const auto aggregated_first = aggregated.locate({0, 0, 3, 0});
    const auto aggregated_last = aggregated.locate({15, 0, 3, 1});
    assert(aggregated_first.package_stack == 0);
    assert(aggregated_first.vertical_layer == 3);
    assert(aggregated_first.thermal_node_index ==
           aggregated_last.thermal_node_index);
    aggregated_mappings.back().thermal_node = "aggregated.l0";
    assert(throws([&] {
        thermal::PackageTopology conflicting(
            {16, 1, 8, 2}, 8, aggregated_nodes, aggregated_mappings,
            model_provenance());
    }));

    auto mappings = std::vector<thermal::DieMapping>{};
    auto nodes = std::vector<std::string>{"base"};
    for (std::uint32_t die = 0; die < 8; ++die) {
        nodes.push_back("n" + std::to_string(die));
        mappings.push_back({0, 0, die, 0, die,
                            "n" + std::to_string(die)});
    }
    mappings.back().die = 0;
    assert(throws([&] {
        thermal::PackageTopology invalid({1, 1, 8, 1}, 8, nodes,
                                         mappings, model_provenance());
    }));
}

void test_power()
{
    thermal::PowerAccumulator accumulator(
        10, topology(), energy_model(),
        thermal::BaseDiePowerModel{
            scalar(1.0, "W"), scalar(1.0, "J"),
            scalar(0.0, "J/byte"), "base"});
    accumulator.add(thermal::NandMediaActivity{
        .operation = thermal::NandOperation::Read,
        .start_time_ns = 5,
        .end_time_ns = 15,
        .coordinate = {0, 0, 0, 0},
        .block = 1,
        .page = 2,
        .bytes = 4096,
    });
    auto bins = accumulator.drain_complete(10);
    assert(bins.size() == 1);
    auto tail = accumulator.finish(20);
    assert(tail.size() == 1);
    assert(std::abs(accumulator.input_nand_energy_j() - 2.0) < 1.0e-12);
    assert(std::abs(accumulator.assigned_nand_energy_j() - 2.0) < 1.0e-12);
    assert(std::abs(bins[0].nand_energy_j - 1.0) < 1.0e-12);
    assert(std::abs(tail[0].nand_energy_j - 1.0) < 1.0e-12);
    assert(std::abs(bins[0].base_die_energy_j - (0.5 + 1.0e-8)) <
           1.0e-12);
    assert(bins[0].media_event_count == 1);
    assert(bins[0].read_bytes == 4096);
    assert(bins[0].program_bytes == 0 && bins[0].erase_count == 0);
    assert(tail[0].media_event_count == 0);

    const auto board = thermal::compose_gpu_hbm_power(
        100.0, 20.0, thermal::GpuPowerSemantics::BoardTotal, 20.0);
    assert(board.allocated_gpu_compute_w == 80.0 && board.total_w == 100.0);
    const auto compute = thermal::compose_gpu_hbm_power(
        80.0, 20.0, thermal::GpuPowerSemantics::ComputeOnly);
    assert(compute.total_w == 100.0);
    assert(throws([] {
        (void)thermal::compose_gpu_hbm_power(
            10.0, 2.0, thermal::GpuPowerSemantics::BoardTotal, 11.0);
    }));
}

void test_runtime(const std::filesystem::path& root)
{
    auto profile = thermal::load_package_thermal_profile(
        root / "configs/package_thermal/synthetic-8hi.json");
    profile.stage = thermal::ThermalStage::Active;
    profile.timeline.enabled = true;
    const auto timeline_path = std::filesystem::temp_directory_path() /
                               "hbfsim-package-thermal-timeline.csv";
    thermal::PackageThermalTimelineWriter timeline(timeline_path, profile);
    auto model = std::make_unique<SyntheticPackageModel>(
        profile.topology.node_names());
    thermal::PackageThermalRuntime runtime(std::move(profile),
                                           std::move(model));
    runtime.record(thermal::NandMediaActivity{
        .operation = thermal::NandOperation::Read,
        .start_time_ns = 0,
        .end_time_ns = 1500,
        .coordinate = {0, 0, 0, 0},
        .block = 1,
        .page = 2,
        .bytes = 4096,
    });
    auto observations = runtime.advance_to(1000);
    assert(observations.size() == 1);
    assert(observations[0].policy.raw_mode == thermal::ThermalMode::Normal);
    assert(observations[0].media_event_count == 1);
    assert(observations[0].media_read_bytes == 4096);
    assert(observations[0].host_sample_time_ns != 0);
    timeline.append(observations[0], thermal::ThermalServiceSnapshot{
        .submitted_requests = 1,
        .admitted_requests = 1,
        .completed_requests = 0,
        .queue_depth = 0,
        .thermal_blocked_requests = 0,
    });
    observations = runtime.advance_to(2000);
    assert(observations.size() == 1);
    assert(observations[0].policy.raw_mode == thermal::ThermalMode::Severe);
    assert(observations[0].policy.effective_mode ==
           thermal::ThermalMode::Severe);
    assert(!observations[0].policy.admission_open);
    timeline.append(observations[0], thermal::ThermalServiceSnapshot{
        .submitted_requests = 2,
        .admitted_requests = 1,
        .completed_requests = 1,
        .queue_depth = 1,
        .thermal_blocked_requests = 1,
    });
    assert(runtime.stats().thermal_steps == 2);
    assert(runtime.stats().severe_transitions == 1);
    assert(throws([&] { (void)runtime.advance_to(1999); }));
    observations = runtime.finish(2500);
    assert(observations.size() == 1);
    timeline.append(observations[0], thermal::ThermalServiceSnapshot{
        .submitted_requests = 2,
        .admitted_requests = 2,
        .completed_requests = 2,
        .queue_depth = 0,
        .thermal_blocked_requests = 1,
    });
    timeline.finish();
    assert(timeline.metrics().block_episode_count == 1);
    assert(timeline.metrics().gate_closed_ns == 1500);
    assert(timeline.metrics().requests_delayed == 1);
    assert(timeline.metrics().queue_depth_peak == 1);
    std::ifstream timeline_input(timeline_path);
    std::string timeline_text{std::istreambuf_iterator<char>(timeline_input),
                              {}};
    assert(timeline_text.find("host_sample_time_ns") != std::string::npos);
    assert(timeline_text.find("would_have_policy") != std::string::npos);
    assert(timeline_text.find("MQSim_read_bytes") != std::string::npos);
    std::filesystem::remove(timeline_path);
    assert(throws([&] { (void)runtime.finish(3000); }));
}

void test_providers(const std::filesystem::path& root)
{
    thermal::PowerProviderConfig synthetic{
        .kind = thermal::PowerProviderKind::Synthetic,
        .provenance = model_provenance(),
        .interpolation = thermal::Interpolation::Linear,
        .samples = {{0, 10.0}, {100, 20.0}},
    };
    auto provider = thermal::make_power_provider(synthetic);
    assert(std::abs(provider->sample_w(50) - 15.0) < 1.0e-12);

    thermal::PowerProviderConfig trace{
        .kind = thermal::PowerProviderKind::Trace,
        .provenance = model_provenance(),
        .interpolation = thermal::Interpolation::Hold,
        .trace_path = root / "tests/fixtures/package_thermal/power.csv",
    };
    provider = thermal::make_power_provider(trace);
    assert(provider->sample_w(99) == 12.0);
    assert(provider->sample_w(100) == 18.0);

    thermal::PowerProviderConfig nvml{
        .kind = thermal::PowerProviderKind::Nvml,
        .provenance = model_provenance(),
        .nvml_library = root / "does-not-exist/libnvidia-ml.so",
    };
    assert(throws([&] { (void)thermal::make_power_provider(nvml); }));
}

void test_clock()
{
    thermal::ThermalClock replay(thermal::ClockMode::ModelTimeReplay, 100);
    assert(replay.relative_ns(thermal::ClockSource::ModelTime, 125) == 25);
    assert(throws([&] {
        (void)replay.relative_ns(thermal::ClockSource::HostMonotonic, 126);
    }));
    assert(throws([&] {
        (void)replay.relative_ns(thermal::ClockSource::ModelTime, 124);
    }));

    thermal::ThermalClock live(thermal::ClockMode::LiveMonotonic, 1000);
    assert(live.relative_ns(thermal::ClockSource::HostMonotonic, 1010) == 10);
    assert(throws([&] {
        (void)live.relative_ns(thermal::ClockSource::ModelTime, 20);
    }));
    live.synchronize_model_to_live(1000);
    assert(live.relative_ns(thermal::ClockSource::ModelTime, 20) == 20);
}

void test_rom(const std::filesystem::path& root)
{
    const std::vector<double> coupled{0.7, 0.1, 0.05, 0.8};
    const auto coupled_radius = thermal::spectral_radius(coupled, 2);
    assert(std::abs(coupled_radius - 0.8366025403784439) < 1.0e-8);
    const auto path = root / "configs/package_thermal/rom/synthetic-2node.json";
    auto artifact = thermal::load_rom_artifact(path);
    assert(artifact.evidence_label == "synthetic_fixture");
    assert(std::abs(thermal::spectral_radius(artifact.a,
                                            artifact.state_count) -
                    0.9) < 1.0e-8);
    const std::vector<std::string> inputs{"gpu", "hbf"};
    const std::vector<std::string> outputs{"gpu_temp", "hbf_temp"};
    thermal::validate_rom_artifact(artifact, inputs, outputs);
    auto model = thermal::make_rom_model(artifact);
    model->reset(25.0);
    const auto first = model->step(std::vector<double>{10.0, 5.0});
    assert(first.size() == 2);
    assert(std::abs(first[0] - 25.0) < 1.0e-12);
    artifact.a[0] = 1.01;
    assert(throws([&] { thermal::validate_rom_artifact(artifact); }));

    thermal::RomArtifact analytic{
        .schema_version = 1,
        .model_id = "one-node-analytic-rc",
        .evidence_label = "synthetic_fixture",
        .sample_period_ns = 1000,
        .input_names = {"node"},
        .output_names = {"node"},
        .state_count = 1,
        .a = {0.9},
        .b = {0.1},
        .bias = {3.0},
        .c = {1.0},
        .d = {0.0},
        .offset = {0.0},
        .payload_sha256 = std::string(64, '0'),
        .solver_identity = "analytic_fixture",
        .geometry_sha256 = std::string(64, '1'),
        .training_split = "analytic_train",
        .held_out_split = "analytic_holdout",
        .held_out_max_error_c = 0.0,
        .held_out_p95_error_c = 0.0,
    };
    thermal::validate_rom_artifact(analytic);
    auto analytic_model = thermal::make_rom_model(std::move(analytic));
    analytic_model->reset(30.0);
    auto temperature = analytic_model->step(std::vector<double>{10.0});
    assert(std::abs(temperature[0] - 30.0) < 1.0e-12);
    temperature = analytic_model->step(std::vector<double>{10.0});
    assert(std::abs(temperature[0] - 31.0) < 1.0e-12);
    for (std::size_t sample = 0; sample < 200; ++sample) {
        temperature = analytic_model->step(std::vector<double>{10.0});
    }
    assert(std::abs(temperature[0] - 40.0) < 1.0e-7);
    for (std::size_t sample = 0; sample < 200; ++sample) {
        temperature = analytic_model->step(std::vector<double>{0.0});
    }
    assert(std::abs(temperature[0] - 30.0) < 1.0e-7);
    assert(throws([&] {
        (void)analytic_model->step(std::vector<double>{-1.0});
    }));

    thermal::RomArtifact large = artifact;
    large.state_count = 224;
    large.a.assign(large.state_count * large.state_count, 0.0);
    large.b.assign(large.state_count * large.input_names.size(), 0.0);
    large.bias.assign(large.state_count, 0.0);
    large.c.assign(large.output_names.size() * large.state_count, 0.0);
    large.d.assign(large.output_names.size() * large.input_names.size(), 0.0);
    large.offset.assign(large.output_names.size(), 30.0);
    thermal::validate_rom_artifact(large);
    large.state_count = thermal::kMaximumRomStateCount + 1;
    assert(throws([&] { thermal::validate_rom_artifact(large); }));
}

void test_rom_file(const std::filesystem::path& path)
{
    const auto artifact = thermal::load_rom_artifact(path);
    thermal::validate_rom_artifact(artifact);
    auto model = thermal::make_rom_model(artifact);
    model->reset(30.0);
    std::vector<double> power(model->input_names().size(), 0.5);
    const auto output = model->step(power);
    assert(output.size() == model->output_names().size());
}

void test_plugin(const std::filesystem::path& plugin)
{
    const std::vector<std::string> inputs{"gpu", "hbf"};
    const std::vector<std::string> outputs{"gpu_temp", "hbf_temp"};
    auto model = thermal::load_thermal_plugin(plugin, inputs, outputs);
    assert(model->sample_period_ns() == 1000);
    model->reset(25.0);
    const auto temperatures = model->step(std::vector<double>{10.0, 5.0});
    assert(temperatures.size() == 2);
    assert(temperatures[1] > temperatures[0]);
    const std::vector<std::string> wrong{"wrong", "hbf"};
    assert(throws([&] {
        (void)thermal::load_thermal_plugin(plugin, wrong, outputs);
    }));
}

void test_policy()
{
    thermal::ThermalPolicy active(thermal::ThermalStage::Active,
                                  policy_config());
    assert(active.observe(81.0).raw_mode == thermal::ThermalMode::Normal);
    auto decision = active.observe(81.0);
    assert(decision.raw_mode == thermal::ThermalMode::Light);
    assert(decision.effective_mode == thermal::ThermalMode::Light);
    assert(decision.service_scale == 0.5 && decision.admission_open);
    assert(decision.debounce_counter == 0);
    assert(decision.dwell_counter == 0);
    (void)active.observe(91.0);
    decision = active.observe(91.0);
    assert(decision.effective_mode == thermal::ThermalMode::Severe);
    assert(!decision.admission_open);
    (void)active.observe(84.0);
    decision = active.observe(84.0);
    assert(decision.effective_mode == thermal::ThermalMode::Light);
    (void)active.observe(101.0);
    decision = active.observe(101.0);
    assert(decision.effective_mode == thermal::ThermalMode::Shutdown);
    assert(!decision.admission_open && decision.service_scale == 0.0);

    thermal::ThermalPolicy shadow(thermal::ThermalStage::Shadow,
                                  policy_config());
    (void)shadow.observe(95.0);
    decision = shadow.observe(95.0);
    assert(decision.raw_mode == thermal::ThermalMode::Severe);
    assert(decision.effective_mode == thermal::ThermalMode::Normal);
    assert(decision.admission_open && decision.service_scale == 1.0);

    thermal::ThermalPolicy read_only(thermal::ThermalStage::ReadOnly,
                                     policy_config());
    decision = read_only.observe(101.0);
    assert(decision.raw_mode == thermal::ThermalMode::Normal);
    assert(decision.effective_mode == thermal::ThermalMode::Normal);
    assert(decision.generation == 0 && decision.debounce_counter == 0 &&
           decision.dwell_counter == 0);
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 4) {
        std::cerr << "usage: package_thermal_test CASE ROOT PLUGIN\n";
        return 2;
    }
    const std::string test_case = argv[1];
    const std::filesystem::path root = argv[2];
    const std::filesystem::path plugin = argv[3];
    if (test_case == "profile") test_profile(root);
    else if (test_case == "topology") test_topology();
    else if (test_case == "power") test_power();
    else if (test_case == "providers") test_providers(root);
    else if (test_case == "clock") test_clock();
    else if (test_case == "rom") test_rom(root);
    else if (test_case == "plugin") test_plugin(plugin);
    else if (test_case == "policy") test_policy();
    else if (test_case == "runtime") test_runtime(root);
    else if (test_case == "rom_file") test_rom_file(plugin);
    else return 3;
    return 0;
}
