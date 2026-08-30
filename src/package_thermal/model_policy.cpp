#include <hbfsim/package_thermal.hpp>

#include <dlfcn.h>
#include <openssl/evp.h>

#include <json.hpp>

#include <algorithm>
#include <bit>
#include <cmath>
#include <complex>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <set>
#include <sstream>

namespace hbfsim::package_thermal {
namespace {

using Json = nlohmann::json;

std::string sha256(const std::string& value)
{
    auto* context = EVP_MD_CTX_new();
    if (context == nullptr) {
        throw ThermalError("failed to allocate SHA-256 context");
    }
    unsigned char digest[EVP_MAX_MD_SIZE]{};
    unsigned int size = 0;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
                    EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
                    EVP_DigestFinal_ex(context, digest, &size) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || size != 32) {
        throw ThermalError("failed to compute SHA-256");
    }
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (unsigned int index = 0; index < size; ++index) {
        output << std::setw(2) << static_cast<unsigned int>(digest[index]);
    }
    return output.str();
}

void append_u64_be(std::string& output, std::uint64_t value)
{
    for (int shift = 56; shift >= 0; shift -= 8) {
        output.push_back(static_cast<char>((value >> shift) & 0xffU));
    }
}

void append_hash_string(std::string& output, const std::string& value)
{
    append_u64_be(output, value.size());
    output.append(value);
}

void append_hash_strings(std::string& output, const Json& values)
{
    append_u64_be(output, values.size());
    for (const auto& value : values) {
        append_hash_string(output, value.get<std::string>());
    }
}

void append_hash_doubles(std::string& output, const Json& values)
{
    static_assert(sizeof(double) == sizeof(std::uint64_t));
    append_u64_be(output, values.size());
    for (const auto& value : values) {
        const auto number = value.get<double>();
        append_u64_be(output, std::bit_cast<std::uint64_t>(number));
    }
}

std::string rom_payload_sha256_v2(const Json& payload)
{
    std::string encoded{"HBFSimRomPayloadV2\0", 19};
    append_hash_string(encoded, payload.at("model_id").get<std::string>());
    append_hash_string(encoded,
                       payload.at("evidence_label").get<std::string>());
    append_u64_be(encoded,
                  payload.at("sample_period_ns").get<std::uint64_t>());
    append_hash_strings(encoded, payload.at("input_names"));
    append_hash_strings(encoded, payload.at("output_names"));
    append_u64_be(encoded, payload.at("state_count").get<std::uint64_t>());
    for (const auto* field : {"a", "b", "bias", "c", "d", "offset"}) {
        append_hash_doubles(encoded, payload.at(field));
    }
    for (const auto* field : {"solver_identity", "geometry_sha256",
                              "training_split", "held_out_split"}) {
        append_hash_string(encoded, payload.at(field).get<std::string>());
    }
    append_u64_be(encoded, 2);
    append_u64_be(encoded, std::bit_cast<std::uint64_t>(
                               payload.at("held_out_max_error_c").get<double>()));
    append_u64_be(encoded, std::bit_cast<std::uint64_t>(
                               payload.at("held_out_p95_error_c").get<double>()));
    return sha256(encoded);
}

std::vector<double> doubles(const Json& value, const std::string& field)
{
    if (!value.is_array()) {
        throw ThermalError(field + " must be an array");
    }
    std::vector<double> result;
    result.reserve(value.size());
    for (const auto& item : value) {
        const auto number = item.get<double>();
        if (!std::isfinite(number)) {
            throw ThermalError(field + " contains a non-finite value");
        }
        result.push_back(number);
    }
    return result;
}

void require_size(std::span<const double> value,
                  std::size_t expected,
                  const std::string& field)
{
    if (value.size() != expected) {
        throw ThermalError(field + " has the wrong dimensions");
    }
}

void validate_names(std::span<const std::string> names,
                    const std::string& field)
{
    if (names.empty()) {
        throw ThermalError(field + " must not be empty");
    }
    std::set<std::string> unique;
    for (const auto& name : names) {
        if (name.empty() || !unique.emplace(name).second) {
            throw ThermalError(field + " must contain unique non-empty names");
        }
    }
}

std::vector<std::string> split_csv(const char* text, const std::string& field)
{
    if (text == nullptr || *text == '\0') {
        throw ThermalError(field + " is empty");
    }
    std::vector<std::string> result;
    std::string value{text};
    std::size_t begin = 0;
    while (begin <= value.size()) {
        const auto end = value.find(',', begin);
        auto name = value.substr(begin, end == std::string::npos
                                            ? std::string::npos
                                            : end - begin);
        if (name.empty()) {
            throw ThermalError(field + " contains an empty name");
        }
        result.push_back(std::move(name));
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    validate_names(result, field);
    return result;
}

void validate_expected(std::span<const std::string> actual,
                       std::span<const std::string> expected,
                       const std::string& field)
{
    if (!expected.empty() && !std::equal(actual.begin(), actual.end(),
                                         expected.begin(), expected.end())) {
        throw ThermalError(field + " do not match the configured topology");
    }
}

class RomThermalModel final : public ThermalModel {
public:
    explicit RomThermalModel(RomArtifact artifact)
        : artifact_(std::move(artifact)), state_(artifact_.state_count, 0.0)
    {
        validate_rom_artifact(artifact_);
    }

    void reset(double initial_temperature_c) override
    {
        if (!std::isfinite(initial_temperature_c) ||
            initial_temperature_c < -273.15 || initial_temperature_c > 1000.0) {
            throw ThermalError("ROM reset temperature is outside sanity bounds");
        }
        std::fill(state_.begin(), state_.end(), initial_temperature_c);
    }

    std::vector<double> step(std::span<const double> input_power_w) override
    {
        const auto n = artifact_.state_count;
        const auto m = artifact_.input_names.size();
        const auto o = artifact_.output_names.size();
        if (input_power_w.size() != m) {
            throw ThermalError("ROM input power vector has the wrong dimension");
        }
        for (const auto power : input_power_w) {
            if (!std::isfinite(power) || power < 0.0) {
                throw ThermalError("ROM input power must be finite and non-negative");
            }
        }

        std::vector<double> output(o, 0.0);
        for (std::size_t row = 0; row < o; ++row) {
            long double value = artifact_.offset[row];
            for (std::size_t column = 0; column < n; ++column) {
                value += static_cast<long double>(artifact_.c[row * n + column]) *
                         state_[column];
            }
            for (std::size_t column = 0; column < m; ++column) {
                value += static_cast<long double>(artifact_.d[row * m + column]) *
                         input_power_w[column];
            }
            output[row] = static_cast<double>(value);
            if (!std::isfinite(output[row]) || output[row] < -273.15 ||
                output[row] > 1000.0) {
                throw ThermalError("ROM produced an invalid temperature");
            }
        }

        std::vector<double> next(n, 0.0);
        for (std::size_t row = 0; row < n; ++row) {
            long double value = artifact_.bias[row];
            for (std::size_t column = 0; column < n; ++column) {
                value += static_cast<long double>(artifact_.a[row * n + column]) *
                         state_[column];
            }
            for (std::size_t column = 0; column < m; ++column) {
                value += static_cast<long double>(artifact_.b[row * m + column]) *
                         input_power_w[column];
            }
            next[row] = static_cast<double>(value);
            if (!std::isfinite(next[row])) {
                throw ThermalError("ROM state became non-finite");
            }
        }
        state_ = std::move(next);
        return output;
    }

    std::span<const std::string> input_names() const override
    {
        return artifact_.input_names;
    }

    std::span<const std::string> output_names() const override
    {
        return artifact_.output_names;
    }

    std::uint64_t sample_period_ns() const noexcept override
    {
        return artifact_.sample_period_ns;
    }

    std::string identity() const override
    {
        return "rom:" + artifact_.model_id + ":" + artifact_.payload_sha256;
    }

private:
    RomArtifact artifact_;
    std::vector<double> state_;
};

class PluginThermalModel final : public ThermalModel {
public:
    PluginThermalModel(const std::filesystem::path& path,
                       std::span<const std::string> expected_inputs,
                       std::span<const std::string> expected_outputs)
    {
        library_ = ::dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (library_ == nullptr) {
            throw ThermalError("failed to load selected thermal plugin: " +
                               path.string());
        }
        try {
            ::dlerror();
            auto* query_address =
                ::dlsym(library_, HBFSIM_PACKAGE_THERMAL_PLUGIN_SYMBOL);
            if (query_address == nullptr || ::dlerror() != nullptr) {
                throw ThermalError("thermal plugin lacks the v1 query symbol");
            }
            auto query = reinterpret_cast<
                hbfsim_package_thermal_plugin_query_v1>(query_address);
            api_ = query();
            if (api_ == nullptr ||
                api_->abi_version !=
                    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION ||
                api_->struct_size != sizeof(*api_) || api_->metadata == nullptr ||
                api_->create == nullptr || api_->destroy == nullptr ||
                api_->reset == nullptr || api_->step == nullptr ||
                api_->last_error == nullptr) {
                throw ThermalError("thermal plugin ABI table is invalid");
            }
            const auto* metadata = api_->metadata();
            if (metadata == nullptr ||
                metadata->abi_version !=
                    HBFSIM_PACKAGE_THERMAL_PLUGIN_ABI_VERSION ||
                metadata->struct_size != sizeof(*metadata) ||
                metadata->state_count == 0 || metadata->input_count == 0 ||
                metadata->output_count == 0 || metadata->sample_period_ns == 0 ||
                metadata->model_id == nullptr || *metadata->model_id == '\0') {
                throw ThermalError("thermal plugin metadata is invalid");
            }
            identity_ = std::string{"plugin:"} + metadata->model_id;
            input_names_ = split_csv(metadata->input_names_csv,
                                     "thermal plugin input names");
            output_names_ = split_csv(metadata->output_names_csv,
                                      "thermal plugin output names");
            if (input_names_.size() != metadata->input_count ||
                output_names_.size() != metadata->output_count) {
                throw ThermalError("thermal plugin name dimensions are invalid");
            }
            validate_expected(input_names_, expected_inputs,
                              "thermal plugin input names");
            validate_expected(output_names_, expected_outputs,
                              "thermal plugin output names");
            sample_period_ns_ = metadata->sample_period_ns;
            instance_ = api_->create();
            if (instance_ == nullptr) {
                throw ThermalError("thermal plugin failed to create a model");
            }
        } catch (...) {
            close();
            throw;
        }
    }

    ~PluginThermalModel() override { close(); }

    void reset(double initial_temperature_c) override
    {
        if (!std::isfinite(initial_temperature_c) ||
            api_->reset(instance_, initial_temperature_c) != 0) {
            throw ThermalError(plugin_error("thermal plugin reset failed"));
        }
    }

    std::vector<double> step(std::span<const double> input_power_w) override
    {
        if (input_power_w.size() != input_names_.size()) {
            throw ThermalError("thermal plugin input dimension mismatch");
        }
        for (const auto power : input_power_w) {
            if (!std::isfinite(power) || power < 0.0) {
                throw ThermalError(
                    "thermal plugin input power must be finite and non-negative");
            }
        }
        std::vector<double> output(output_names_.size(), 0.0);
        if (api_->step(instance_, input_power_w.data(), input_power_w.size(),
                       output.data(), output.size()) != 0) {
            throw ThermalError(plugin_error("thermal plugin step failed"));
        }
        for (const auto temperature : output) {
            if (!std::isfinite(temperature) || temperature < -273.15 ||
                temperature > 1000.0) {
                throw ThermalError("thermal plugin produced an invalid temperature");
            }
        }
        return output;
    }

    std::span<const std::string> input_names() const override
    {
        return input_names_;
    }

    std::span<const std::string> output_names() const override
    {
        return output_names_;
    }

    std::uint64_t sample_period_ns() const noexcept override
    {
        return sample_period_ns_;
    }

    std::string identity() const override { return identity_; }

private:
    std::string plugin_error(const char* fallback) const
    {
        const auto* error = api_->last_error(instance_);
        return error != nullptr && *error != '\0' ? error : fallback;
    }

    void close() noexcept
    {
        if (instance_ != nullptr && api_ != nullptr && api_->destroy != nullptr) {
            api_->destroy(instance_);
            instance_ = nullptr;
        }
        if (library_ != nullptr) {
            (void)::dlclose(library_);
            library_ = nullptr;
        }
    }

    void* library_{nullptr};
    const hbfsim_package_thermal_plugin_api_v1* api_{nullptr};
    void* instance_{nullptr};
    std::vector<std::string> input_names_;
    std::vector<std::string> output_names_;
    std::uint64_t sample_period_ns_{0};
    std::string identity_;
};

}  // namespace

double spectral_radius(std::span<const double> square_matrix,
                       std::size_t dimension)
{
    if (dimension == 0 || dimension > kMaximumRomStateCount ||
        square_matrix.size() != dimension * dimension) {
        throw ThermalError("spectral-radius matrix dimensions are invalid");
    }
    std::vector<long double> matrix;
    matrix.reserve(square_matrix.size());
    for (const auto value : square_matrix) {
        if (!std::isfinite(value)) {
            throw ThermalError("spectral-radius matrix is non-finite");
        }
        matrix.emplace_back(value);
    }

    const auto at = [dimension](std::size_t row, std::size_t column) {
        return row * dimension + column;
    };
    if (dimension == 1) return static_cast<double>(std::abs(matrix[0]));

    if (dimension == 2) {
        using Complex = std::complex<long double>;
        const auto trace = matrix[0] + matrix[3];
        const auto determinant = matrix[0] * matrix[3] - matrix[1] * matrix[2];
        const auto discriminant = std::sqrt(
            Complex{trace * trace - 4.0L * determinant, 0.0L});
        const auto root1 = (Complex{trace, 0.0L} + discriminant) / 2.0L;
        const auto root2 = (Complex{trace, 0.0L} - discriminant) / 2.0L;
        return static_cast<double>(std::max(std::abs(root1), std::abs(root2)));
    }

    bool diagonal = true;
    long double diagonal_radius = 0.0L;
    for (std::size_t row = 0; row < dimension; ++row) {
        diagonal_radius = std::max(diagonal_radius,
                                   std::abs(matrix[at(row, row)]));
        for (std::size_t column = 0; column < dimension; ++column) {
            if (row != column && matrix[at(row, column)] != 0.0L) {
                diagonal = false;
            }
        }
    }
    if (diagonal) return static_cast<double>(diagonal_radius);

    // For larger matrices return a fail-closed upper bound rather than a
    // numerically fragile eigenvalue estimate.  Gelfand's formula gives
    // rho(A) <= ||A^k||_inf^(1/k). Repeated normalized squaring tightens the
    // bound without overflowing. A stable model is accepted only when this
    // certified bound is strictly below one.
    long double log_scale = 0.0L;
    std::size_t exponent = 1;
    long double best_bound = std::numeric_limits<long double>::infinity();
    for (std::size_t iteration = 0; iteration < 12; ++iteration) {
        long double norm = 0.0L;
        for (std::size_t row = 0; row < dimension; ++row) {
            long double row_sum = 0.0L;
            for (std::size_t column = 0; column < dimension; ++column) {
                row_sum += std::abs(matrix[at(row, column)]);
            }
            norm = std::max(norm, row_sum);
        }
        if (norm == 0.0L) return 0.0;
        if (!std::isfinite(norm)) {
            throw ThermalError("spectral-radius bound overflowed");
        }

        log_scale += std::log(norm);
        for (auto& value : matrix) value /= norm;
        const auto bound = std::exp(log_scale /
                                    static_cast<long double>(exponent));
        best_bound = std::min(best_bound, bound);

        std::vector<long double> squared(dimension * dimension, 0.0L);
        for (std::size_t row = 0; row < dimension; ++row) {
            for (std::size_t column = 0; column < dimension; ++column) {
                long double value = 0.0L;
                for (std::size_t inner = 0; inner < dimension; ++inner) {
                    value += matrix[at(row, inner)] *
                             matrix[at(inner, column)];
                }
                squared[at(row, column)] = value;
            }
        }
        matrix = std::move(squared);
        log_scale *= 2.0L;
        exponent *= 2;
    }
    if (!std::isfinite(best_bound)) {
        throw ThermalError("spectral-radius bound is non-finite");
    }
    constexpr auto roundoff_guard =
        64.0L * std::numeric_limits<long double>::epsilon();
    return static_cast<double>(best_bound * (1.0L + roundoff_guard));
}

RomArtifact load_rom_artifact(const std::filesystem::path& path)
{
    std::ifstream stream(path);
    if (!stream) {
        throw ThermalError("failed to open thermal ROM: " + path.string());
    }
    Json document;
    try {
        stream >> document;
    } catch (const std::exception& error) {
        throw ThermalError("failed to parse thermal ROM: " +
                           std::string(error.what()));
    }
    if (!document.is_object() || !document.contains("schema_version") ||
        !document.contains("payload") || !document.contains("payload_sha256") ||
        document.size() != 3) {
        throw ThermalError("thermal ROM envelope is invalid");
    }
    const auto schema = document.at("schema_version").get<std::uint32_t>();
    const auto expected_hash = document.at("payload_sha256").get<std::string>();
    const auto& payload = document.at("payload");
    std::string actual_hash;
    if (schema == kLegacyRomSchemaVersion) {
        actual_hash = sha256(payload.dump());
    } else if (schema == kRomSchemaVersion) {
        actual_hash = rom_payload_sha256_v2(payload);
    } else {
        throw ThermalError("unsupported thermal ROM schema");
    }
    if (expected_hash.size() != 64 || expected_hash != actual_hash) {
        throw ThermalError("thermal ROM payload checksum mismatch");
    }
    const std::set<std::string> required{
        "model_id", "evidence_label", "sample_period_ns", "input_names",
        "output_names", "state_count", "a", "b", "bias", "c", "d",
        "offset", "solver_identity", "geometry_sha256", "training_split",
        "held_out_split", "held_out_max_error_c", "held_out_p95_error_c"};
    if (!payload.is_object() || payload.size() != required.size()) {
        throw ThermalError("thermal ROM payload fields are invalid");
    }
    for (const auto& name : required) {
        if (!payload.contains(name)) {
            throw ThermalError("thermal ROM payload is missing " + name);
        }
    }
    RomArtifact result{
        .schema_version = schema,
        .model_id = payload.at("model_id").get<std::string>(),
        .evidence_label = payload.at("evidence_label").get<std::string>(),
        .sample_period_ns =
            payload.at("sample_period_ns").get<std::uint64_t>(),
        .input_names =
            payload.at("input_names").get<std::vector<std::string>>(),
        .output_names =
            payload.at("output_names").get<std::vector<std::string>>(),
        .state_count = payload.at("state_count").get<std::size_t>(),
        .a = doubles(payload.at("a"), "thermal ROM A"),
        .b = doubles(payload.at("b"), "thermal ROM B"),
        .bias = doubles(payload.at("bias"), "thermal ROM bias"),
        .c = doubles(payload.at("c"), "thermal ROM C"),
        .d = doubles(payload.at("d"), "thermal ROM D"),
        .offset = doubles(payload.at("offset"), "thermal ROM offset"),
        .payload_sha256 = expected_hash,
        .solver_identity = payload.at("solver_identity").get<std::string>(),
        .geometry_sha256 = payload.at("geometry_sha256").get<std::string>(),
        .training_split = payload.at("training_split").get<std::string>(),
        .held_out_split = payload.at("held_out_split").get<std::string>(),
        .held_out_max_error_c =
            payload.at("held_out_max_error_c").get<double>(),
        .held_out_p95_error_c =
            payload.at("held_out_p95_error_c").get<double>(),
    };
    validate_rom_artifact(result);
    return result;
}

void validate_rom_artifact(const RomArtifact& artifact,
                           std::span<const std::string> expected_inputs,
                           std::span<const std::string> expected_outputs)
{
    if (artifact.schema_version != kLegacyRomSchemaVersion &&
        artifact.schema_version != kRomSchemaVersion) {
        throw ThermalError("unsupported thermal ROM schema");
    }
    if (artifact.model_id.empty() || artifact.sample_period_ns == 0 ||
        artifact.state_count == 0 ||
        artifact.state_count > kMaximumRomStateCount) {
        throw ThermalError("thermal ROM metadata is invalid");
    }
    if (artifact.evidence_label != "synthetic_fixture" &&
        artifact.evidence_label != "literature_parameterized" &&
        artifact.evidence_label != "calibrated_external_solver" &&
        artifact.evidence_label != "measured") {
        throw ThermalError("thermal ROM evidence label is invalid");
    }
    validate_names(artifact.input_names, "thermal ROM input names");
    validate_names(artifact.output_names, "thermal ROM output names");
    validate_expected(artifact.input_names, expected_inputs,
                      "thermal ROM input names");
    validate_expected(artifact.output_names, expected_outputs,
                      "thermal ROM output names");
    const auto n = artifact.state_count;
    const auto m = artifact.input_names.size();
    const auto o = artifact.output_names.size();
    require_size(artifact.a, n * n, "thermal ROM A");
    require_size(artifact.b, n * m, "thermal ROM B");
    require_size(artifact.bias, n, "thermal ROM bias");
    require_size(artifact.c, o * n, "thermal ROM C");
    require_size(artifact.d, o * m, "thermal ROM D");
    require_size(artifact.offset, o, "thermal ROM offset");
    for (const auto* matrix : {&artifact.a, &artifact.b, &artifact.bias,
                               &artifact.c, &artifact.d, &artifact.offset}) {
        for (const auto value : *matrix) {
            if (!std::isfinite(value)) {
                throw ThermalError("thermal ROM contains a non-finite coefficient");
            }
        }
    }
    const auto radius = spectral_radius(artifact.a, n);
    if (!std::isfinite(radius) || radius >= 1.0 - 1.0e-9) {
        throw ThermalError("thermal ROM is not strictly stable");
    }
    if (artifact.payload_sha256.size() != 64 ||
        artifact.solver_identity.empty() || artifact.geometry_sha256.size() != 64 ||
        artifact.training_split.empty() || artifact.held_out_split.empty() ||
        artifact.training_split == artifact.held_out_split ||
        !std::isfinite(artifact.held_out_max_error_c) ||
        !std::isfinite(artifact.held_out_p95_error_c) ||
        artifact.held_out_max_error_c < 0.0 ||
        artifact.held_out_p95_error_c < 0.0 ||
        artifact.held_out_p95_error_c > artifact.held_out_max_error_c) {
        throw ThermalError("thermal ROM provenance/validation metadata is invalid");
    }
}

std::unique_ptr<ThermalModel> make_rom_model(RomArtifact artifact)
{
    return std::make_unique<RomThermalModel>(std::move(artifact));
}

std::unique_ptr<ThermalModel> load_thermal_plugin(
    const std::filesystem::path& library_path,
    std::span<const std::string> expected_inputs,
    std::span<const std::string> expected_outputs)
{
    if (library_path.empty()) {
        throw ThermalError("selected thermal plugin path is empty");
    }
    return std::make_unique<PluginThermalModel>(
        library_path, expected_inputs, expected_outputs);
}

ThermalPolicy::ThermalPolicy(ThermalStage stage, PolicyConfig config)
    : stage_(stage), config_(std::move(config))
{
    if (config_.debounce_samples == 0 ||
        !(config_.light_scale.value > 0.0 &&
          config_.light_scale.value <= 1.0) ||
        !(config_.light_off_c.value < config_.light_on_c.value &&
          config_.light_on_c.value < config_.severe_on_c.value &&
          config_.severe_off_c.value < config_.severe_on_c.value &&
          config_.severe_on_c.value < config_.shutdown_on_c.value &&
          config_.shutdown_off_c.value < config_.shutdown_on_c.value)) {
        throw ThermalError("thermal policy configuration is invalid");
    }
}

ThermalMode ThermalPolicy::desired(double temperature) const
{
    if (!std::isfinite(temperature)) {
        throw ThermalError("thermal policy temperature is non-finite");
    }
    if (temperature >= config_.shutdown_on_c.value) {
        return ThermalMode::Shutdown;
    }
    switch (mode_) {
    case ThermalMode::Normal:
        if (temperature >= config_.severe_on_c.value) return ThermalMode::Severe;
        if (temperature >= config_.light_on_c.value) return ThermalMode::Light;
        return ThermalMode::Normal;
    case ThermalMode::Light:
        if (temperature >= config_.severe_on_c.value) return ThermalMode::Severe;
        if (temperature <= config_.light_off_c.value) return ThermalMode::Normal;
        return ThermalMode::Light;
    case ThermalMode::Severe:
        if (temperature <= config_.severe_off_c.value) {
            return temperature <= config_.light_off_c.value
                       ? ThermalMode::Normal
                       : ThermalMode::Light;
        }
        return ThermalMode::Severe;
    case ThermalMode::Shutdown:
        if (temperature <= config_.shutdown_off_c.value) {
            if (temperature >= config_.severe_on_c.value)
                return ThermalMode::Severe;
            if (temperature >= config_.light_on_c.value)
                return ThermalMode::Light;
            return ThermalMode::Normal;
        }
        return ThermalMode::Shutdown;
    }
    throw ThermalError("unknown thermal policy mode");
}

PolicyDecision ThermalPolicy::observe(double maximum_temperature_c)
{
    if (stage_ == ThermalStage::Off || stage_ == ThermalStage::ReadOnly) {
        if (!std::isfinite(maximum_temperature_c)) {
            throw ThermalError("thermal policy temperature is non-finite");
        }
        return PolicyDecision{
            ThermalMode::Normal, ThermalMode::Normal, 1.0, true,
            generation_, false, 0, 0};
    }
    ++dwell_count_;
    const auto requested = desired(maximum_temperature_c);
    bool changed = false;
    if (requested == mode_) {
        candidate_ = mode_;
        candidate_count_ = 0;
    } else if (dwell_count_ >= config_.minimum_dwell_samples) {
        if (requested != candidate_) {
            candidate_ = requested;
            candidate_count_ = 1;
        } else {
            ++candidate_count_;
        }
        if (candidate_count_ >= config_.debounce_samples) {
            mode_ = candidate_;
            candidate_count_ = 0;
            dwell_count_ = 0;
            ++generation_;
            changed = true;
        }
    }

    auto effective = mode_;
    if (stage_ != ThermalStage::Active) {
        effective = ThermalMode::Normal;
    }
    double scale = 1.0;
    bool admission_open = true;
    if (effective == ThermalMode::Light) {
        scale = config_.light_scale.value;
    } else if (effective == ThermalMode::Severe) {
        admission_open = false;
    } else if (effective == ThermalMode::Shutdown) {
        scale = 0.0;
        admission_open = false;
    }
    return PolicyDecision{mode_, effective, scale, admission_open,
                          generation_, changed, candidate_count_,
                          dwell_count_};
}

ThermalMode ThermalPolicy::mode() const noexcept { return mode_; }

}  // namespace hbfsim::package_thermal
