#pragma once

#include <hbfsim/package_thermal_plugin.h>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace hbfsim::package_thermal {

inline constexpr std::uint32_t kProfileSchemaVersion = 1;
inline constexpr std::uint32_t kLegacyRomSchemaVersion = 1;
inline constexpr std::uint32_t kRomSchemaVersion = 2;
inline constexpr std::size_t kMaximumRomStateCount = 256;

class ThermalError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

enum class EvidenceClass : std::uint8_t {
    Specification,
    Literature,
    CalibrationOrSensitivity,
    Measurement,
};

struct Provenance {
    EvidenceClass evidence;
    std::string source;
    std::string locator;
    std::string note;
    std::string dataset_sha256;
    std::string calibration_sha256;
};

struct SourcedScalar {
    double value;
    std::string unit;
    Provenance provenance;
};

enum class ThermalStage : std::uint8_t {
    Off,
    ReadOnly,
    Shadow,
    Active,
};

enum class ClockMode : std::uint8_t {
    ModelTimeReplay,
    LiveMonotonic,
};

enum class ClockSource : std::uint8_t {
    ModelTime,
    HostMonotonic,
    NvmlMonotonic,
};

enum class GpuPowerSemantics : std::uint8_t {
    BoardTotal,
    ComputeOnly,
};

enum class NearMemoryKind : std::uint8_t {
    None,
    Gddr7,
    Hbm2,
    Hbm2e,
    Hbm3,
    Hbm3e,
    Hbm4,
    Synthetic,
};

enum class NearMemoryPlacement : std::uint8_t {
    Board,
    Interposer,
    Stacked,
    External,
};

enum class PackageArchitecture : std::uint8_t {
    GddrGpuHbf,
    HbmGpuHbf,
    Synthetic,
};

enum class AcceleratorPowerSemantics : std::uint8_t {
    BoardTotalLumped,
    GpuComputeOnly,
    GpuPlusGddrLumped,
    GpuComputePlusExplicitHbm,
};

enum class PowerModelEvidenceLevel : std::uint8_t {
    SensitivityOnly,
    LiteratureBounded,
    Calibrated,
    Measured,
};

enum class PowerProviderKind : std::uint8_t {
    Synthetic,
    Trace,
    Nvml,
};

enum class Interpolation : std::uint8_t {
    Hold,
    Linear,
};

struct PowerSample {
    std::uint64_t relative_time_ns;
    double watts;
};

struct PowerProviderConfig {
    PowerProviderKind kind;
    Provenance provenance;
    Interpolation interpolation{Interpolation::Hold};
    std::vector<PowerSample> samples;
    std::filesystem::path trace_path;
    std::filesystem::path nvml_library;
    std::uint32_t device_index{0};
};

struct NearMemoryPowerSource {
    std::string thermal_node;
    PowerProviderConfig provider;
};

struct NearMemoryConfig {
    NearMemoryKind kind;
    NearMemoryPlacement placement;
    Provenance provenance;
    std::vector<NearMemoryPowerSource> power_sources;
};

struct PhysicalGeometry {
    std::uint32_t channels;
    std::uint32_t chips_per_channel;
    std::uint32_t dies_per_chip;
    std::uint32_t planes_per_die;
};

struct DieMapping {
    std::uint32_t channel;
    std::uint32_t chip;
    std::uint32_t die;
    std::uint32_t package_stack;
    std::uint32_t vertical_layer;
    std::string thermal_node;
};

struct PhysicalCoordinate {
    std::uint32_t channel;
    std::uint32_t chip;
    std::uint32_t die;
    std::uint32_t plane;
};

struct ThermalLocation {
    std::uint32_t package_stack;
    std::uint32_t vertical_layer;
    std::size_t thermal_node_index;
};

class PackageTopology {
public:
    PackageTopology(PhysicalGeometry geometry,
                    std::uint32_t stack_height,
                    std::vector<std::string> node_names,
                    std::vector<DieMapping> die_mappings,
                    Provenance provenance);

    [[nodiscard]] const PhysicalGeometry& geometry() const noexcept;
    [[nodiscard]] std::uint32_t stack_height() const noexcept;
    [[nodiscard]] std::uint32_t package_stack_count() const noexcept;
    [[nodiscard]] const std::vector<std::string>& node_names() const noexcept;
    [[nodiscard]] const Provenance& provenance() const noexcept;
    [[nodiscard]] ThermalLocation locate(
        const PhysicalCoordinate& coordinate) const;

private:
    PhysicalGeometry geometry_{};
    std::uint32_t stack_height_{};
    std::uint32_t package_stack_count_{};
    std::vector<std::string> node_names_;
    std::vector<DieMapping> mappings_;
    Provenance provenance_{};
    std::unordered_map<std::uint64_t, ThermalLocation> lookup_;
};

enum class NandOperation : std::uint8_t {
    Read,
    Program,
    Erase,
};

struct NandMediaActivity {
    NandOperation operation;
    std::uint64_t start_time_ns;
    std::uint64_t end_time_ns;
    PhysicalCoordinate coordinate;
    std::uint64_t block;
    std::uint64_t page;
    std::uint64_t bytes;
};

struct OperationEnergy {
    SourcedScalar command_j;
    SourcedScalar joules_per_byte;
};

struct NandEnergyModel {
    OperationEnergy read;
    OperationEnergy program;
    OperationEnergy erase;
};

struct BaseDiePowerModel {
    SourcedScalar idle_w;
    SourcedScalar command_j;
    SourcedScalar joules_per_byte;
    std::string thermal_node;
};

struct PackagePowerBin {
    std::uint64_t start_time_ns;
    std::uint64_t end_time_ns;
    std::vector<double> node_power_w;
    double nand_energy_j;
    double base_die_energy_j;
    std::uint64_t media_event_count;
    std::uint64_t read_bytes;
    std::uint64_t program_bytes;
    std::uint64_t erase_count;
};

class PowerAccumulator {
public:
    PowerAccumulator(std::uint64_t bin_width_ns,
                     PackageTopology topology,
                     NandEnergyModel nand,
                     BaseDiePowerModel base,
                     double relative_tolerance = 1.0e-12);

    void add(const NandMediaActivity& activity);
    [[nodiscard]] std::vector<PackagePowerBin> drain_complete(
        std::uint64_t through_time_ns);
    [[nodiscard]] std::vector<PackagePowerBin> finish(
        std::uint64_t end_time_ns);
    [[nodiscard]] double input_nand_energy_j() const noexcept;
    [[nodiscard]] double assigned_nand_energy_j() const noexcept;

private:
    struct BinEnergy {
        std::vector<double> node_j;
        double nand_j{0.0};
        double base_dynamic_j{0.0};
        std::uint64_t media_event_count{0};
        std::uint64_t read_bytes{0};
        std::uint64_t program_bytes{0};
        std::uint64_t erase_count{0};
    };
    [[nodiscard]] PackagePowerBin emit_bin(std::size_t index,
                                           std::uint64_t end_time_ns);
    std::uint64_t bin_width_ns_;
    PackageTopology topology_;
    NandEnergyModel nand_;
    BaseDiePowerModel base_;
    double relative_tolerance_;
    std::size_t base_node_index_;
    std::vector<BinEnergy> bins_;
    double input_nand_energy_j_{0.0};
    double assigned_nand_energy_j_{0.0};
    std::uint64_t maximum_event_time_ns_{0};
    std::size_t next_output_bin_{0};
    bool finished_{false};
};

class PowerProvider {
public:
    virtual ~PowerProvider() = default;
    [[nodiscard]] virtual double sample_w(
        std::uint64_t relative_time_ns) = 0;
    [[nodiscard]] virtual std::string identity() const = 0;
};

std::unique_ptr<PowerProvider> make_power_provider(
    const PowerProviderConfig& config);

struct ComposedPower {
    double raw_gpu_w;
    double allocated_gpu_compute_w;
    double hbm_w;
    double total_w;
};

ComposedPower compose_gpu_hbm_power(double raw_gpu_w,
                                    double hbm_w,
                                    GpuPowerSemantics semantics,
                                    double hbm_in_board_total_w = 0.0);

class ThermalClock {
public:
    ThermalClock(ClockMode mode, std::uint64_t source_origin_ns);
    void synchronize_model_to_live(std::int64_t model_to_live_offset_ns);
    [[nodiscard]] std::uint64_t relative_ns(ClockSource source,
                                            std::uint64_t timestamp_ns);
    [[nodiscard]] std::uint64_t live_now_ns() const;

private:
    ClockMode mode_;
    std::uint64_t origin_ns_;
    std::optional<std::int64_t> model_to_live_offset_ns_;
    std::optional<std::uint64_t> last_model_;
    std::optional<std::uint64_t> last_host_;
    std::optional<std::uint64_t> last_nvml_;
};

struct PolicyConfig {
    SourcedScalar light_on_c;
    SourcedScalar light_off_c;
    SourcedScalar severe_on_c;
    SourcedScalar severe_off_c;
    SourcedScalar shutdown_on_c;
    SourcedScalar shutdown_off_c;
    SourcedScalar light_scale;
    std::uint32_t debounce_samples;
    std::uint32_t minimum_dwell_samples;
    Provenance timing_provenance;
};

enum class ThermalMode : std::uint8_t {
    Normal,
    Light,
    Severe,
    Shutdown,
};

struct PolicyDecision {
    ThermalMode raw_mode;
    ThermalMode effective_mode;
    double service_scale;
    bool admission_open;
    std::uint64_t generation;
    bool changed;
    std::uint32_t debounce_counter;
    std::uint32_t dwell_counter;
};

class ThermalPolicy {
public:
    ThermalPolicy(ThermalStage stage, PolicyConfig config);
    [[nodiscard]] PolicyDecision observe(double maximum_temperature_c);
    [[nodiscard]] ThermalMode mode() const noexcept;

private:
    [[nodiscard]] ThermalMode desired(double maximum_temperature_c) const;
    ThermalStage stage_;
    PolicyConfig config_;
    ThermalMode mode_{ThermalMode::Normal};
    ThermalMode candidate_{ThermalMode::Normal};
    std::uint32_t candidate_count_{0};
    std::uint32_t dwell_count_{0};
    std::uint64_t generation_{0};
};

struct ThermalObservation {
    std::uint64_t start_time_ns;
    std::uint64_t end_time_ns;
    std::uint64_t host_sample_time_ns;
    double accelerator_power_w;
    std::vector<double> near_memory_power_w;
    std::vector<double> input_power_w;
    std::vector<double> temperatures_c;
    double hbf_hotspot_c;
    std::string hbf_hotspot_node;
    PolicyDecision policy;
    std::uint64_t media_event_count;
    std::uint64_t media_read_bytes;
    std::uint64_t media_program_bytes;
    std::uint64_t media_erase_count;
};

struct ThermalRuntimeStats {
    double maximum_hbf_temperature_c;
    std::uint64_t maximum_time_ns;
    std::string maximum_node;
    std::uint64_t time_normal_ns;
    std::uint64_t time_light_ns;
    std::uint64_t time_severe_ns;
    std::uint64_t time_shutdown_ns;
    std::uint64_t light_transitions;
    std::uint64_t severe_transitions;
    std::uint64_t shutdown_transitions;
    std::uint64_t thermal_steps;
    std::uint64_t media_observer_cost_ns;
    std::uint64_t power_aggregation_cost_ns;
    std::uint64_t telemetry_sampling_cost_ns;
    std::uint64_t model_advance_cost_ns;
    std::uint64_t policy_cost_ns;
};

struct TimelineConfig {
    bool enabled{false};
};

struct PackageThermalProfile {
    std::uint32_t schema_version;
    std::string name;
    ThermalStage stage;
    ClockMode clock_mode;
    SourcedScalar ambient_c;
    SourcedScalar bin_width_ns;
    GpuPowerSemantics gpu_power_semantics;
    Provenance gpu_power_semantics_provenance;
    PowerProviderConfig gpu_provider;
    PowerProviderConfig hbm_provider;
    bool legacy_power_schema;
    PackageArchitecture package_architecture;
    NearMemoryConfig near_memory;
    AcceleratorPowerSemantics accelerator_power_semantics;
    Provenance accelerator_power_semantics_provenance;
    PowerModelEvidenceLevel power_model_evidence_level;
    Provenance power_model_evidence_provenance;
    PackageTopology topology;
    NandEnergyModel nand_energy;
    BaseDiePowerModel base_die;
    PolicyConfig policy;
    TimelineConfig timeline;
    std::string evidence_label;
};

PackageThermalProfile load_package_thermal_profile(
    const std::filesystem::path& path);
void validate_package_thermal_profile(const PackageThermalProfile& profile);

struct RomArtifact {
    std::uint32_t schema_version;
    std::string model_id;
    std::string evidence_label;
    std::uint64_t sample_period_ns;
    std::vector<std::string> input_names;
    std::vector<std::string> output_names;
    std::size_t state_count;
    std::vector<double> a;
    std::vector<double> b;
    std::vector<double> bias;
    std::vector<double> c;
    std::vector<double> d;
    std::vector<double> offset;
    std::string payload_sha256;
    std::string solver_identity;
    std::string geometry_sha256;
    std::string training_split;
    std::string held_out_split;
    double held_out_max_error_c;
    double held_out_p95_error_c;
};

RomArtifact load_rom_artifact(const std::filesystem::path& path);
void validate_rom_artifact(const RomArtifact& artifact,
                           std::span<const std::string> expected_inputs = {},
                           std::span<const std::string> expected_outputs = {});
double spectral_radius(std::span<const double> square_matrix,
                       std::size_t dimension);

class ThermalModel {
public:
    virtual ~ThermalModel() = default;
    virtual void reset(double initial_temperature_c) = 0;
    [[nodiscard]] virtual std::vector<double> step(
        std::span<const double> input_power_w) = 0;
    [[nodiscard]] virtual std::span<const std::string> input_names() const = 0;
    [[nodiscard]] virtual std::span<const std::string> output_names() const = 0;
    [[nodiscard]] virtual std::uint64_t sample_period_ns() const noexcept = 0;
    [[nodiscard]] virtual std::string identity() const = 0;
};

std::unique_ptr<ThermalModel> make_rom_model(RomArtifact artifact);
std::unique_ptr<ThermalModel> load_thermal_plugin(
    const std::filesystem::path& library_path,
    std::span<const std::string> expected_inputs,
    std::span<const std::string> expected_outputs);

class PackageThermalRuntime {
public:
    PackageThermalRuntime(PackageThermalProfile profile,
                          std::unique_ptr<ThermalModel> model);
    void record(const NandMediaActivity& activity);
    [[nodiscard]] std::vector<ThermalObservation> advance_to(
        std::uint64_t relative_time_ns);
    [[nodiscard]] std::vector<ThermalObservation> finish(
        std::uint64_t relative_time_ns);
    [[nodiscard]] const ThermalRuntimeStats& stats() const noexcept;
    [[nodiscard]] const PolicyDecision& decision() const noexcept;
    [[nodiscard]] const std::optional<ThermalObservation>& latest() const noexcept;
    [[nodiscard]] const PackageThermalProfile& profile() const noexcept;
    [[nodiscard]] std::string model_identity() const;

private:
    [[nodiscard]] std::vector<ThermalObservation> process(
        std::vector<PackagePowerBin> bins);
    PackageThermalProfile profile_;
    std::unique_ptr<ThermalModel> model_;
    PowerAccumulator accumulator_;
    std::unique_ptr<PowerProvider> gpu_provider_;
    std::vector<std::unique_ptr<PowerProvider>> near_memory_providers_;
    ThermalPolicy policy_;
    std::size_t gpu_node_{};
    std::vector<std::size_t> near_memory_nodes_;
    std::vector<std::size_t> hbf_outputs_;
    ThermalRuntimeStats stats_{};
    PolicyDecision decision_{};
    std::optional<ThermalObservation> latest_;
    std::optional<std::uint64_t> last_activity_start_ns_;
    std::optional<std::uint64_t> last_advance_ns_;
    bool finished_{false};
};

struct ThermalReportMetadata {
    std::filesystem::path package_profile_path;
    std::filesystem::path model_path;
    std::string model_kind;
};

struct ThermalServiceSnapshot {
    std::uint64_t submitted_requests{0};
    std::uint64_t admitted_requests{0};
    std::uint64_t completed_requests{0};
    std::uint64_t queue_depth{0};
    std::uint64_t thermal_blocked_requests{0};
};

struct ThermalServiceMetrics {
    std::uint64_t thermal_blocked_requests{0};
    std::uint64_t block_episode_count{0};
    std::uint64_t gate_closed_ns{0};
    std::optional<std::uint64_t> total_admission_wait_ns;
    std::uint64_t requests_delayed{0};
    std::optional<std::uint64_t> admission_retry_count;
    std::uint64_t queue_depth_peak{0};
    std::uint64_t hypothetical_block_time_ns{0};
};

class PackageThermalTimelineWriter {
public:
    PackageThermalTimelineWriter(const std::filesystem::path& path,
                                 const PackageThermalProfile& profile);
    ~PackageThermalTimelineWriter();
    PackageThermalTimelineWriter(const PackageThermalTimelineWriter&) = delete;
    PackageThermalTimelineWriter& operator=(
        const PackageThermalTimelineWriter&) = delete;
    void append(const ThermalObservation& observation,
                const ThermalServiceSnapshot& service);
    void finish();
    [[nodiscard]] const ThermalServiceMetrics& metrics() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

void write_package_thermal_report(
    const std::filesystem::path& path,
    const PackageThermalRuntime& runtime,
    const ThermalReportMetadata& metadata,
    const ThermalServiceMetrics& service_metrics);
std::string sha256_file(const std::filesystem::path& path);

std::string to_string(EvidenceClass value);
std::string to_string(ThermalStage value);
std::string to_string(ThermalMode value);
std::string to_string(NearMemoryKind value);
std::string to_string(NearMemoryPlacement value);
std::string to_string(PackageArchitecture value);
std::string to_string(AcceleratorPowerSemantics value);
std::string to_string(PowerModelEvidenceLevel value);

}  // namespace hbfsim::package_thermal
