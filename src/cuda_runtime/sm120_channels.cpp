#include <hbfsim/sm120_channels.hpp>

#include "../host_service/control_layout.hpp"

#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace hbfsim {
namespace {

constexpr std::uint64_t mix(std::uint64_t value) noexcept
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

constexpr std::uint64_t add(std::uint64_t left,
                            std::uint64_t right) noexcept
{
    return right > std::numeric_limits<std::uint64_t>::max() - left
               ? std::numeric_limits<std::uint64_t>::max()
               : left + right;
}

std::uint64_t routing_key(const RoutingInput& input) noexcept
{
    auto key = mix(input.smid);
    key = mix(key ^ input.warpid);
    key = mix(key ^ (std::uint64_t{input.cta_x} << 32) ^ input.cta_y);
    key = mix(key ^ (std::uint64_t{input.cta_z} << 32) ^
              input.resident_warps);
    key = mix(key ^ (std::uint64_t{input.cluster_ctarank} << 32) ^
              static_cast<std::uint32_t>(input.operation));
    return key;
}

bool valid_operation(Sm120Operation operation) noexcept
{
    return static_cast<std::uint32_t>(operation) < kSm120OperationClasses;
}

bool uses_gnic(Sm120Operation operation) noexcept
{
    return operation == Sm120Operation::OrdinaryLoad ||
           operation == Sm120Operation::TmaLoad ||
           operation == Sm120Operation::TmaUnicast ||
           operation == Sm120Operation::TmaMulticast ||
           operation == Sm120Operation::MixedHbmHbf;
}

bool uses_gpc(Sm120Operation operation) noexcept
{
    return operation == Sm120Operation::OrdinaryStore ||
           operation == Sm120Operation::TmaStore ||
           operation == Sm120Operation::MixedHbmHbf;
}

template <typename Queue>
void purge(Queue& queue, std::uint64_t arrival) noexcept
{
    while (!queue.completions.empty() &&
           queue.completions.front() <= arrival) {
        queue.completions.pop_front();
    }
}

template <std::size_t N>
bool copy_services(const std::vector<std::uint64_t>& source,
                   std::array<std::uint64_t, N>& target) noexcept
{
    if (source.size() != N) return false;
    std::copy(source.begin(), source.end(), target.begin());
    return true;
}

QueueArbitration arbitration(std::string_view value)
{
    if (value == "fifo") return QueueArbitration::Fifo;
    if (value == "round_robin") return QueueArbitration::RoundRobin;
    throw std::invalid_argument("unsupported SM120 arbitration");
}

bool decode_sha256(std::string_view text, std::byte* output) noexcept
{
    if (text.size() != 64 || output == nullptr) return false;
    const auto digit = [](char value) -> int {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        return -1;
    };
    for (std::size_t index = 0; index < 32; ++index) {
        const auto high = digit(text[index * 2]);
        const auto low = digit(text[index * 2 + 1]);
        if (high < 0 || low < 0) return false;
        output[index] = static_cast<std::byte>((high << 4) | low);
    }
    return true;
}

}  // namespace

std::string validate_sm120_channel_config(
    const Sm120ChannelConfig& config)
{
    if (config.gnic.count != kSm120GnicChannels)
        return "gnic_count_not_four";
    if (config.gpc.count != kSm120GpcChannels)
        return "gpc_count_not_two";
    if (config.gnic.depth == 0) return "gnic_depth_zero";
    if (config.gpc.depth == 0) return "gpc_depth_zero";
    if (config.routing.version == 0) return "routing_version_zero";
    if (config.routing.smsp_proxy_lut.empty()) return "smsp_lut_empty";
    if (config.routing.gnic_lut.empty()) return "gnic_lut_empty";
    if (config.routing.gpc_lut.empty()) return "gpc_lut_empty";
    if (std::any_of(config.routing.gnic_lut.begin(),
                    config.routing.gnic_lut.end(), [](auto value) {
                        return value >= kSm120GnicChannels;
                    })) return "gnic_lut_out_of_range";
    if (std::any_of(config.routing.gpc_lut.begin(),
                    config.routing.gpc_lut.end(), [](auto value) {
                        return value >= kSm120GpcChannels;
                    })) return "gpc_lut_out_of_range";
    for (const auto service : config.gnic.service_ns_by_class)
        if (service == 0) return "gnic_service_zero";
    for (const auto service : config.gpc.service_ns_by_class)
        if (service == 0) return "gpc_service_zero";
    return {};
}

Sm120ChannelConfig make_sm120_channel_config(
    const ExactCalibration& calibration)
{
    Sm120ChannelConfig result{
        .gnic = {.count = calibration.gnic.count,
                 .depth = calibration.gnic.depth,
                 .arbitration = arbitration(calibration.gnic.arbitration)},
        .gpc = {.count = calibration.gpc.count,
                .depth = calibration.gpc.depth,
                .arbitration = arbitration(calibration.gpc.arbitration)},
        .routing = {.version = calibration.routing.version,
                    .smsp_proxy_lut = calibration.routing.smsp_proxy_lut,
                    .gnic_lut = calibration.routing.gnic_lut,
                    .gpc_lut = calibration.routing.gpc_lut},
    };
    if (!copy_services(calibration.gnic.service_ns_by_class,
                       result.gnic.service_ns_by_class) ||
        !copy_services(calibration.gpc.service_ns_by_class,
                       result.gpc.service_ns_by_class)) {
        throw std::invalid_argument(
            "SM120 service arrays must cover exactly seven classes");
    }
    const auto error = validate_sm120_channel_config(result);
    if (!error.empty()) throw std::invalid_argument(error);
    return result;
}

ChannelSelection route_sm120(const Sm120ChannelConfig& config,
                             const RoutingInput& input)
{
    if (!validate_sm120_channel_config(config).empty() ||
        !valid_operation(input.operation) || input.cta_x == 0 ||
        input.cta_y == 0 || input.cta_z == 0 ||
        input.resident_warps == 0) return {};
    const auto key = routing_key(input);
    const auto proxy = config.routing.smsp_proxy_lut[
        key % config.routing.smsp_proxy_lut.size()];
    const auto operation = static_cast<std::uint32_t>(input.operation);
    const auto gnic_index = mix(key ^ proxy ^ (std::uint64_t{operation} << 32)) %
                            config.routing.gnic_lut.size();
    const auto gpc_index = mix(key ^ (std::uint64_t{proxy} << 32) ^ operation) %
                           config.routing.gpc_lut.size();
    return {.smsp_proxy = proxy,
            .gnic = config.routing.gnic_lut[gnic_index],
            .gpc = config.routing.gpc_lut[gpc_index],
            .valid = true};
}

bool publish_sm120_channel_config(
    void* control_address, std::size_t control_bytes,
    const ExactCalibration& calibration, std::uint32_t sm_count) noexcept
{
    try {
        const auto config = make_sm120_channel_config(calibration);
        if (config.routing.smsp_proxy_lut.size() >
                host_service::kSm120RoutingLutCapacity ||
            config.routing.gnic_lut.size() >
                host_service::kSm120RoutingLutCapacity ||
            config.routing.gpc_lut.size() >
                host_service::kSm120RoutingLutCapacity ||
            sm_count == 0 || sm_count > host_service::kSm120StateCapacity) {
            return false;
        }
        host_service::ControlView control(control_address, control_bytes);
        if (!control.valid()) return false;
        auto* shared = control.sm120_channel_config();
        std::memset(shared, 0, sizeof(*shared));
        shared->magic = host_service::kSm120ChannelConfigMagic;
        shared->routing_version = config.routing.version;
        shared->gnic_count = config.gnic.count;
        shared->gpc_count = config.gpc.count;
        shared->gnic_depth = config.gnic.depth;
        shared->gpc_depth = config.gpc.depth;
        shared->gnic_arbitration =
            static_cast<std::uint32_t>(config.gnic.arbitration);
        shared->gpc_arbitration =
            static_cast<std::uint32_t>(config.gpc.arbitration);
        shared->smsp_proxy_lut_count =
            static_cast<std::uint32_t>(config.routing.smsp_proxy_lut.size());
        shared->gnic_lut_count =
            static_cast<std::uint32_t>(config.routing.gnic_lut.size());
        shared->gpc_lut_count =
            static_cast<std::uint32_t>(config.routing.gpc_lut.size());
        if (!decode_sha256(calibration.routing.program_sha256,
                           shared->routing_program_sha256)) return false;
        std::copy(config.gnic.service_ns_by_class.begin(),
                  config.gnic.service_ns_by_class.end(),
                  shared->gnic_service_ns_by_class);
        std::copy(config.gpc.service_ns_by_class.begin(),
                  config.gpc.service_ns_by_class.end(),
                  shared->gpc_service_ns_by_class);
        std::copy(config.routing.smsp_proxy_lut.begin(),
                  config.routing.smsp_proxy_lut.end(),
                  shared->smsp_proxy_lut);
        std::copy(config.routing.gnic_lut.begin(),
                  config.routing.gnic_lut.end(), shared->gnic_lut);
        std::copy(config.routing.gpc_lut.begin(),
                  config.routing.gpc_lut.end(), shared->gpc_lut);
        auto* header = control.header();
        header->sm120_channel_state_count = sm_count;
        shared->enabled = 1;
        host_service::atomic_store(
            header->sm120_channel_profile_generation, 1,
            std::memory_order_release);
        return true;
    } catch (...) {
        return false;
    }
}

Sm120ChannelModel::Sm120ChannelModel(Sm120ChannelConfig config,
                                     std::uint32_t sm_count)
    : config_(std::move(config)), states_(sm_count)
{
    const auto error = validate_sm120_channel_config(config_);
    if (!error.empty()) throw std::invalid_argument(error);
    if (sm_count == 0) throw std::invalid_argument("sm_count_zero");
}

ChannelReservation Sm120ChannelModel::reserve(
    const RoutingInput& input,
    const ReservationConditions& conditions) noexcept
{
    const auto selection = route_sm120(config_, input);
    if (!selection.valid || input.smid >= states_.size() ||
        conditions.bytes == 0) return {};
    auto& state = states_[input.smid];
    auto gnic = selection.gnic;
    auto gpc = selection.gpc;
    if (config_.gnic.arbitration == QueueArbitration::RoundRobin)
        gnic = static_cast<std::uint32_t>(
            (gnic + state.gnic_round_robin++) % config_.gnic.count);
    if (config_.gpc.arbitration == QueueArbitration::RoundRobin)
        gpc = static_cast<std::uint32_t>(
            (gpc + state.gpc_round_robin++) % config_.gpc.count);
    const bool take_gnic = uses_gnic(input.operation);
    const bool take_gpc = uses_gpc(input.operation);
    auto& gnic_queue = state.gnic[gnic];
    auto& gpc_queue = state.gpc[gpc];
    if (take_gnic) purge(gnic_queue, conditions.arrival_ns);
    if (take_gpc) purge(gpc_queue, conditions.arrival_ns);
    if ((take_gnic && gnic_queue.completions.size() >= config_.gnic.depth) ||
        (take_gpc && gpc_queue.completions.size() >= config_.gpc.depth)) {
        ++state.counters.saturated_requests;
        return {.ready_ns = std::numeric_limits<std::uint64_t>::max(),
                .gnic = gnic, .gpc = gpc, .uses_gnic = take_gnic,
                .uses_gpc = take_gpc, .saturated = true};
    }
    const auto operation = static_cast<std::uint32_t>(input.operation);
    const auto gnic_service = take_gnic
        ? config_.gnic.service_ns_by_class[operation] : 0;
    const auto gpc_service = take_gpc
        ? config_.gpc.service_ns_by_class[operation] : 0;
    auto ready = std::max({conditions.arrival_ns, conditions.base_ready_ns,
                           conditions.media_ready_ns,
                           conditions.capacity_ready_ns,
                           conditions.native_ready_ns});
    if (take_gnic) {
        const auto begin = std::max(conditions.arrival_ns, gnic_queue.tail_ns);
        gnic_queue.tail_ns = add(begin, gnic_service);
        gnic_queue.completions.push_back(gnic_queue.tail_ns);
        ready = std::max(ready, gnic_queue.tail_ns);
        state.counters.gnic_bytes[gnic] += conditions.bytes;
        state.counters.gnic_service_ns[gnic] += gnic_service;
        ++state.counters.gnic_requests[gnic];
    }
    if (take_gpc) {
        const auto begin = std::max(conditions.arrival_ns, gpc_queue.tail_ns);
        gpc_queue.tail_ns = add(begin, gpc_service);
        gpc_queue.completions.push_back(gpc_queue.tail_ns);
        ready = std::max(ready, gpc_queue.tail_ns);
        state.counters.gpc_bytes[gpc] += conditions.bytes;
        state.counters.gpc_service_ns[gpc] += gpc_service;
        ++state.counters.gpc_requests[gpc];
    }
    return {.ready_ns = ready, .gnic = gnic, .gpc = gpc,
            .gnic_service_ns = gnic_service,
            .gpc_service_ns = gpc_service,
            .uses_gnic = take_gnic, .uses_gpc = take_gpc,
            .accepted = true};
}

std::optional<Sm120ChannelCounters> Sm120ChannelModel::counters(
    std::uint32_t smid) const noexcept
{
    if (smid >= states_.size()) return std::nullopt;
    return states_[smid].counters;
}

void Sm120ChannelModel::reset() noexcept
{
    states_.assign(states_.size(), {});
}

}  // namespace hbfsim
