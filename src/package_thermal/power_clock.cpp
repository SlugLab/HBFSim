#include <hbfsim/package_thermal.hpp>

#include <dlfcn.h>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <limits>
#include <sstream>

namespace hbfsim::package_thermal {
namespace {

void require_power(double value, const std::string& field)
{
    if (!std::isfinite(value) || value < 0.0) {
        throw ThermalError(field + " must be finite and non-negative");
    }
}

const OperationEnergy& energy_for(const NandEnergyModel& model,
                                  NandOperation operation)
{
    switch (operation) {
    case NandOperation::Read: return model.read;
    case NandOperation::Program: return model.program;
    case NandOperation::Erase: return model.erase;
    }
    throw ThermalError("unknown NAND operation");
}

class SampledPowerProvider final : public PowerProvider {
public:
    SampledPowerProvider(std::vector<PowerSample> samples,
                         Interpolation interpolation,
                         std::string identity)
        : samples_(std::move(samples)),
          interpolation_(interpolation),
          identity_(std::move(identity))
    {
        if (samples_.empty()) {
            throw ThermalError("sampled power provider has no samples");
        }
        std::uint64_t previous = 0;
        bool first = true;
        for (const auto& sample : samples_) {
            require_power(sample.watts, "power sample");
            if (!first && sample.relative_time_ns <= previous) {
                throw ThermalError(
                    "power sample timestamps must be strictly increasing");
            }
            first = false;
            previous = sample.relative_time_ns;
        }
    }

    double sample_w(std::uint64_t relative_time_ns) override
    {
        const auto upper = std::upper_bound(
            samples_.begin(), samples_.end(), relative_time_ns,
            [](std::uint64_t time, const PowerSample& sample) {
                return time < sample.relative_time_ns;
            });
        if (upper == samples_.begin()) {
            return samples_.front().watts;
        }
        if (upper == samples_.end() || interpolation_ == Interpolation::Hold) {
            return std::prev(upper)->watts;
        }
        const auto& right = *upper;
        const auto& left = *std::prev(upper);
        const long double fraction =
            static_cast<long double>(relative_time_ns - left.relative_time_ns) /
            static_cast<long double>(right.relative_time_ns -
                                     left.relative_time_ns);
        const auto result = static_cast<double>(
            static_cast<long double>(left.watts) +
            fraction * static_cast<long double>(right.watts - left.watts));
        require_power(result, "interpolated power");
        return result;
    }

    std::string identity() const override { return identity_; }

private:
    std::vector<PowerSample> samples_;
    Interpolation interpolation_;
    std::string identity_;
};

std::vector<PowerSample> load_trace(const std::filesystem::path& path)
{
    std::ifstream stream(path);
    if (!stream) {
        throw ThermalError("failed to open power trace: " + path.string());
    }
    std::vector<PowerSample> result;
    std::string line;
    std::size_t line_number = 0;
    while (std::getline(stream, line)) {
        ++line_number;
        if (line.empty() || line[0] == '#') continue;
        const auto comma = line.find(',');
        if (comma == std::string::npos || line.find(',', comma + 1) !=
                                                   std::string::npos) {
            throw ThermalError("invalid power trace line " +
                               std::to_string(line_number));
        }
        const auto time_text = line.substr(0, comma);
        const auto power_text = line.substr(comma + 1);
        if (line_number == 1 && time_text == "relative_time_ns" &&
            power_text == "watts") {
            continue;
        }
        std::uint64_t time = 0;
        const auto time_parse = std::from_chars(
            time_text.data(), time_text.data() + time_text.size(), time);
        if (time_parse.ec != std::errc{} ||
            time_parse.ptr != time_text.data() + time_text.size()) {
            throw ThermalError("invalid power trace timestamp at line " +
                               std::to_string(line_number));
        }
        std::size_t consumed = 0;
        double watts = 0.0;
        try {
            watts = std::stod(power_text, &consumed);
        } catch (...) {
            throw ThermalError("invalid power trace watts at line " +
                               std::to_string(line_number));
        }
        if (consumed != power_text.size()) {
            throw ThermalError("invalid power trace watts at line " +
                               std::to_string(line_number));
        }
        require_power(watts, "power trace watts");
        result.push_back(PowerSample{time, watts});
    }
    return result;
}

class NvmlPowerProvider final : public PowerProvider {
public:
    explicit NvmlPowerProvider(const PowerProviderConfig& config)
        : library_path_(config.nvml_library.empty()
                            ? "libnvidia-ml.so.1"
                            : config.nvml_library.string()),
          device_index_(config.device_index)
    {
        library_ = ::dlopen(library_path_.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (library_ == nullptr) {
            throw ThermalError("failed to load selected NVML library: " +
                               library_path_);
        }
        try {
            init_ = symbol<Init>("nvmlInit_v2");
            shutdown_ = symbol<Shutdown>("nvmlShutdown");
            get_device_ = symbol<GetDevice>("nvmlDeviceGetHandleByIndex_v2");
            get_power_ = symbol<GetPower>("nvmlDeviceGetPowerUsage");
            if (init_() != 0) {
                throw ThermalError("selected NVML provider failed to initialize");
            }
            initialized_ = true;
            if (get_device_(device_index_, &device_) != 0 || device_ == nullptr) {
                throw ThermalError("selected NVML device index is unavailable");
            }
        } catch (...) {
            close();
            throw;
        }
    }

    ~NvmlPowerProvider() override { close(); }

    double sample_w(std::uint64_t) override
    {
        unsigned int milliwatts = 0;
        if (get_power_(device_, &milliwatts) != 0) {
            throw ThermalError("NVML power sample failed");
        }
        const double watts = static_cast<double>(milliwatts) / 1000.0;
        require_power(watts, "NVML power sample");
        return watts;
    }

    std::string identity() const override
    {
        return "nvml:" + library_path_ + ":device=" +
               std::to_string(device_index_);
    }

private:
    using Device = void*;
    using Init = int (*)();
    using Shutdown = int (*)();
    using GetDevice = int (*)(unsigned int, Device*);
    using GetPower = int (*)(Device, unsigned int*);

    template <typename Function>
    Function symbol(const char* name)
    {
        ::dlerror();
        auto* address = ::dlsym(library_, name);
        if (address == nullptr || ::dlerror() != nullptr) {
            throw ThermalError(std::string("selected NVML library lacks ") +
                               name);
        }
        return reinterpret_cast<Function>(address);
    }

    void close() noexcept
    {
        if (initialized_ && shutdown_ != nullptr) {
            (void)shutdown_();
        }
        initialized_ = false;
        if (library_ != nullptr) {
            (void)::dlclose(library_);
            library_ = nullptr;
        }
    }

    std::string library_path_;
    std::uint32_t device_index_;
    void* library_{nullptr};
    Device device_{nullptr};
    Init init_{nullptr};
    Shutdown shutdown_{nullptr};
    GetDevice get_device_{nullptr};
    GetPower get_power_{nullptr};
    bool initialized_{false};
};

}  // namespace

PowerAccumulator::PowerAccumulator(std::uint64_t bin_width_ns,
                                   PackageTopology topology,
                                   NandEnergyModel nand,
                                   BaseDiePowerModel base,
                                   double relative_tolerance)
    : bin_width_ns_(bin_width_ns),
      topology_(std::move(topology)),
      nand_(std::move(nand)),
      base_(std::move(base)),
      relative_tolerance_(relative_tolerance),
      base_node_index_(std::numeric_limits<std::size_t>::max())
{
    if (bin_width_ns_ == 0) {
        throw ThermalError("power bin width must be non-zero");
    }
    if (!std::isfinite(relative_tolerance_) || relative_tolerance_ < 0.0) {
        throw ThermalError("power energy tolerance must be finite and non-negative");
    }
    const auto found = std::find(topology_.node_names().begin(),
                                 topology_.node_names().end(),
                                 base_.thermal_node);
    if (found == topology_.node_names().end()) {
        throw ThermalError("base die thermal node is not in topology");
    }
    base_node_index_ = static_cast<std::size_t>(
        std::distance(topology_.node_names().begin(), found));
    for (const auto value : {base_.idle_w.value, base_.command_j.value,
                             base_.joules_per_byte.value}) {
        require_power(value, "base die power coefficient");
    }
}

void PowerAccumulator::add(const NandMediaActivity& activity)
{
    if (finished_) {
        throw ThermalError("cannot add media activity after power finish");
    }
    if (activity.end_time_ns < activity.start_time_ns) {
        throw ThermalError("media activity ends before it starts");
    }
    const auto location = topology_.locate(activity.coordinate);
    const auto& model = energy_for(nand_, activity.operation);
    require_power(model.command_j.value, "NAND command energy");
    require_power(model.joules_per_byte.value, "NAND byte energy");
    const long double event_energy =
        static_cast<long double>(model.command_j.value) +
        static_cast<long double>(model.joules_per_byte.value) * activity.bytes;
    const long double base_energy =
        static_cast<long double>(base_.command_j.value) +
        static_cast<long double>(base_.joules_per_byte.value) * activity.bytes;
    if (!std::isfinite(static_cast<double>(event_energy)) ||
        !std::isfinite(static_cast<double>(base_energy))) {
        throw ThermalError("media activity energy overflows");
    }
    input_nand_energy_j_ += static_cast<double>(event_energy);
    maximum_event_time_ns_ =
        std::max(maximum_event_time_ns_, activity.end_time_ns);

    const auto ensure_bin = [&](std::size_t index) {
        while (bins_.size() <= index) {
            bins_.push_back(BinEnergy{
                .node_j = std::vector<double>(topology_.node_names().size(), 0.0)});
        }
    };
    const auto add_counter = [](std::uint64_t& target,
                                std::uint64_t increment,
                                const char* field) {
        if (target > std::numeric_limits<std::uint64_t>::max() - increment) {
            throw ThermalError(std::string(field) + " overflows");
        }
        target += increment;
    };
    const auto record_activity = [&](std::size_t index) {
        ensure_bin(index);
        auto& bin = bins_[index];
        add_counter(bin.media_event_count, 1, "media event count");
        switch (activity.operation) {
        case NandOperation::Read:
            add_counter(bin.read_bytes, activity.bytes, "media read bytes");
            break;
        case NandOperation::Program:
            add_counter(bin.program_bytes, activity.bytes,
                        "media program bytes");
            break;
        case NandOperation::Erase:
            add_counter(bin.erase_count, 1, "media erase count");
            break;
        }
    };
    if (activity.start_time_ns == activity.end_time_ns) {
        const auto index = static_cast<std::size_t>(
            activity.start_time_ns / bin_width_ns_);
        if (index < next_output_bin_) {
            throw ThermalError("media activity arrived after its power bin was emitted");
        }
        record_activity(index);
        bins_[index].node_j[location.thermal_node_index] +=
            static_cast<double>(event_energy);
        bins_[index].node_j[base_node_index_] +=
            static_cast<double>(base_energy);
        bins_[index].nand_j += static_cast<double>(event_energy);
        bins_[index].base_dynamic_j += static_cast<double>(base_energy);
        assigned_nand_energy_j_ += static_cast<double>(event_energy);
        return;
    }

    const auto duration = activity.end_time_ns - activity.start_time_ns;
    const auto first = activity.start_time_ns / bin_width_ns_;
    const auto last = (activity.end_time_ns - 1) / bin_width_ns_;
    if (first < next_output_bin_) {
        throw ThermalError("media activity arrived after its power bin was emitted");
    }
    if (first > std::numeric_limits<std::size_t>::max()) {
        throw ThermalError("power bin index overflows size_t");
    }
    record_activity(static_cast<std::size_t>(first));
    for (std::uint64_t index = first; index <= last; ++index) {
        if (index > std::numeric_limits<std::size_t>::max()) {
            throw ThermalError("power bin index overflows size_t");
        }
        ensure_bin(static_cast<std::size_t>(index));
        const auto bin_start = index * bin_width_ns_;
        const auto bin_end = bin_start >
                                     std::numeric_limits<std::uint64_t>::max() -
                                         bin_width_ns_
                                 ? std::numeric_limits<std::uint64_t>::max()
                                 : bin_start + bin_width_ns_;
        const auto overlap_start = std::max(activity.start_time_ns, bin_start);
        const auto overlap_end = std::min(activity.end_time_ns, bin_end);
        const auto overlap = overlap_end - overlap_start;
        const long double fraction = static_cast<long double>(overlap) / duration;
        const auto nand_share = static_cast<double>(event_energy * fraction);
        const auto base_share = static_cast<double>(base_energy * fraction);
        auto& bin = bins_[static_cast<std::size_t>(index)];
        bin.node_j[location.thermal_node_index] += nand_share;
        bin.node_j[base_node_index_] += base_share;
        bin.nand_j += nand_share;
        bin.base_dynamic_j += base_share;
        assigned_nand_energy_j_ += nand_share;
    }
}

PackagePowerBin PowerAccumulator::emit_bin(std::size_t index,
                                           std::uint64_t end_time_ns)
{
    if (index > std::numeric_limits<std::uint64_t>::max() / bin_width_ns_) {
        throw ThermalError("power bin start time overflows");
    }
    const auto start = static_cast<std::uint64_t>(index) * bin_width_ns_;
    const auto nominal_end =
        start > std::numeric_limits<std::uint64_t>::max() - bin_width_ns_
            ? std::numeric_limits<std::uint64_t>::max()
            : start + bin_width_ns_;
    const auto end = std::min(end_time_ns, nominal_end);
    if (end <= start) {
        throw ThermalError("power bin has non-positive duration");
    }
    const long double seconds =
        static_cast<long double>(end - start) / 1.0e9L;
    const auto idle_energy =
        static_cast<double>(seconds * base_.idle_w.value);
    auto& bin = bins_[index];
    bin.node_j[base_node_index_] += idle_energy;
    std::vector<double> watts;
    watts.reserve(bin.node_j.size());
    for (const auto joules : bin.node_j) {
        const auto power = static_cast<double>(joules / seconds);
        require_power(power, "binned node power");
        watts.push_back(power);
    }
    return PackagePowerBin{
        .start_time_ns = start,
        .end_time_ns = end,
        .node_power_w = std::move(watts),
        .nand_energy_j = bin.nand_j,
        .base_die_energy_j = bin.base_dynamic_j + idle_energy,
        .media_event_count = bin.media_event_count,
        .read_bytes = bin.read_bytes,
        .program_bytes = bin.program_bytes,
        .erase_count = bin.erase_count,
    };
}

std::vector<PackagePowerBin> PowerAccumulator::drain_complete(
    std::uint64_t through_time_ns)
{
    if (finished_) {
        throw ThermalError("cannot drain power after finish");
    }
    const auto count_u64 = through_time_ns / bin_width_ns_;
    if (count_u64 > std::numeric_limits<std::size_t>::max()) {
        throw ThermalError("power bin count overflows size_t");
    }
    const auto count = static_cast<std::size_t>(count_u64);
    while (bins_.size() < count) {
        bins_.push_back(BinEnergy{
            .node_j = std::vector<double>(topology_.node_names().size(), 0.0)});
    }
    std::vector<PackagePowerBin> result;
    result.reserve(count > next_output_bin_ ? count - next_output_bin_ : 0);
    while (next_output_bin_ < count) {
        result.push_back(emit_bin(next_output_bin_, through_time_ns));
        ++next_output_bin_;
    }
    return result;
}

std::vector<PackagePowerBin> PowerAccumulator::finish(
    std::uint64_t end_time_ns)
{
    if (finished_) {
        throw ThermalError("power accumulation was already finished");
    }
    if (end_time_ns == 0 || end_time_ns < maximum_event_time_ns_) {
        throw ThermalError("power accumulation end time excludes media activity");
    }
    const auto bin_count_u64 =
        end_time_ns / bin_width_ns_ + (end_time_ns % bin_width_ns_ != 0);
    if (bin_count_u64 > std::numeric_limits<std::size_t>::max()) {
        throw ThermalError("power bin count overflows size_t");
    }
    const auto bin_count = static_cast<std::size_t>(bin_count_u64);
    while (bins_.size() < bin_count) {
        bins_.push_back(BinEnergy{
            .node_j = std::vector<double>(topology_.node_names().size(), 0.0)});
    }

    const auto error = std::abs(input_nand_energy_j_ - assigned_nand_energy_j_);
    const auto scale = std::max(std::abs(input_nand_energy_j_), 1.0e-30);
    if (error > relative_tolerance_ * scale) {
        throw ThermalError("NAND event energy was not conserved across bins");
    }

    std::vector<PackagePowerBin> result;
    result.reserve(bin_count > next_output_bin_
                       ? bin_count - next_output_bin_
                       : 0);
    while (next_output_bin_ < bin_count) {
        result.push_back(emit_bin(next_output_bin_, end_time_ns));
        ++next_output_bin_;
    }
    finished_ = true;
    return result;
}

double PowerAccumulator::input_nand_energy_j() const noexcept
{
    return input_nand_energy_j_;
}

double PowerAccumulator::assigned_nand_energy_j() const noexcept
{
    return assigned_nand_energy_j_;
}

std::unique_ptr<PowerProvider> make_power_provider(
    const PowerProviderConfig& config)
{
    switch (config.kind) {
    case PowerProviderKind::Synthetic:
        return std::make_unique<SampledPowerProvider>(
            config.samples, config.interpolation, "synthetic");
    case PowerProviderKind::Trace:
        return std::make_unique<SampledPowerProvider>(
            load_trace(config.trace_path), config.interpolation,
            "trace:" + config.trace_path.string());
    case PowerProviderKind::Nvml:
        return std::make_unique<NvmlPowerProvider>(config);
    }
    throw ThermalError("unknown power provider kind");
}

ComposedPower compose_gpu_hbm_power(double raw_gpu_w,
                                    double hbm_w,
                                    GpuPowerSemantics semantics,
                                    double hbm_in_board_total_w)
{
    require_power(raw_gpu_w, "raw GPU power");
    require_power(hbm_w, "HBM power");
    require_power(hbm_in_board_total_w, "HBM-in-board allocation");
    double compute = raw_gpu_w;
    if (semantics == GpuPowerSemantics::BoardTotal) {
        if (hbm_in_board_total_w > raw_gpu_w) {
            throw ThermalError("HBM-in-board power exceeds board-total power");
        }
        compute -= hbm_in_board_total_w;
    } else if (hbm_in_board_total_w != 0.0) {
        throw ThermalError(
            "compute_only GPU semantics cannot subtract board HBM power");
    }
    const double total = compute + hbm_w;
    require_power(total, "composed GPU/HBM power");
    return ComposedPower{raw_gpu_w, compute, hbm_w, total};
}

ThermalClock::ThermalClock(ClockMode mode, std::uint64_t source_origin_ns)
    : mode_(mode), origin_ns_(source_origin_ns)
{
}

void ThermalClock::synchronize_model_to_live(
    std::int64_t model_to_live_offset_ns)
{
    if (mode_ != ClockMode::LiveMonotonic) {
        throw ThermalError("model/live synchronization is valid only in live mode");
    }
    model_to_live_offset_ns_ = model_to_live_offset_ns;
}

std::uint64_t ThermalClock::relative_ns(ClockSource source,
                                        std::uint64_t timestamp_ns)
{
    if (mode_ == ClockMode::ModelTimeReplay &&
        source != ClockSource::ModelTime) {
        throw ThermalError("live clock source used in model-time replay");
    }
    if (mode_ == ClockMode::LiveMonotonic && source == ClockSource::ModelTime) {
        if (!model_to_live_offset_ns_.has_value()) {
            throw ThermalError("model time has no explicit live synchronization");
        }
        const auto adjusted = static_cast<__int128>(timestamp_ns) +
                              *model_to_live_offset_ns_;
        if (adjusted < 0 ||
            adjusted > std::numeric_limits<std::uint64_t>::max()) {
            throw ThermalError("model/live synchronized timestamp overflows");
        }
        timestamp_ns = static_cast<std::uint64_t>(adjusted);
    }

    std::optional<std::uint64_t>* last = nullptr;
    switch (source) {
    case ClockSource::ModelTime: last = &last_model_; break;
    case ClockSource::HostMonotonic: last = &last_host_; break;
    case ClockSource::NvmlMonotonic: last = &last_nvml_; break;
    }
    if (last->has_value() && timestamp_ns < **last) {
        throw ThermalError("thermal clock source moved backwards");
    }
    *last = timestamp_ns;
    if (timestamp_ns < origin_ns_) {
        throw ThermalError("thermal timestamp precedes its source-relative origin");
    }
    return timestamp_ns - origin_ns_;
}

std::uint64_t ThermalClock::live_now_ns() const
{
    if (mode_ != ClockMode::LiveMonotonic) {
        throw ThermalError("live_now_ns requires live_monotonic mode");
    }
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count());
}

}  // namespace hbfsim::package_thermal
