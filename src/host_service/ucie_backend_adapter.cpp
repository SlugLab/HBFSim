#include "../ucie/private_observer_gate.hpp"
#include "../ucie/parallel_component_accounting_v1.hpp"
#include "ucie_backend_adapter.hpp"

#include <hbfsim/api.h>
#include <hbfsim/ucie/multistack_frontend.hpp>
#include <hbfsim/ucie/multistack_profile.hpp>

#include <json.hpp>
#include <openssl/sha.h>

#include <algorithm>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace hbfsim::host_service {
namespace {
using json = nlohmann::json;

struct Chunk {
    std::uint64_t region_id{}, offset{}, extent{}, physical{};
};

struct Placement {
    std::uint32_t range_id{};
    std::uint64_t file_offset{};
    std::uint64_t registered_address{};
    std::uint64_t length{};
    std::uint64_t page_bytes{};
    std::uint64_t canonical_physical_address{};
    std::uint64_t endpoint_id{};
    std::uint64_t generation{};
    std::uint64_t extent{};
    bool chunked{};
    bool striped{};
    ucie::PageStripeBacking stripe;
    std::uint32_t stream_id{};
    std::vector<Chunk> chunks;
    std::vector<std::string> aliases;
    std::string storage_sha256;
};

void exact_keys(const json& object, const std::set<std::string>& expected)
{
    if (!object.is_object() || object.size() != expected.size())
        throw std::invalid_argument("placement object has missing or extra keys");
    for (const auto& [key, value] : object.items()) {
        (void)value;
        if (!expected.contains(key))
            throw std::invalid_argument("unknown placement key: " + key);
    }
}

std::uint64_t unsigned_field(const json& object, const char* key)
{
    const auto& field = object.at(key);
    if (!field.is_number_integer())
        throw std::invalid_argument(std::string("integer required: ") + key);
    if (field.is_number_unsigned()) return field.get<std::uint64_t>();
    const auto value = field.get<std::int64_t>();
    if (value < 0) throw std::invalid_argument(std::string("negative: ") + key);
    return static_cast<std::uint64_t>(value);
}

std::uint64_t checked_add(std::uint64_t a, std::uint64_t b)
{
    if (a > UINT64_MAX - b) throw std::overflow_error("UCIe address/time overflow");
    return a + b;
}

std::uint64_t extent(std::uint64_t length, std::uint64_t page_bytes)
{
    if (!page_bytes || !length || length > UINT64_MAX - (page_bytes - 1))
        throw std::invalid_argument("invalid page-rounded extent");
    return ((length + page_bytes - 1) / page_bytes) * page_bytes;
}

std::map<std::uint32_t, Placement> load_placements(
    const std::filesystem::path& path, std::uint64_t total_capacity)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open UCIe placement manifest");
    const auto root = json::parse(input);
    exact_keys(root, {"schema", "placements"});
    if (!root.at("schema").is_string() ||
        root.at("schema").get<std::string>() != "hbfsim.ucie.host_placement.v1" ||
        !root.at("placements").is_array() || root.at("placements").empty())
        throw std::invalid_argument("invalid UCIe placement manifest");
    std::map<std::uint32_t, Placement> result;
    std::vector<std::pair<std::uint64_t, std::uint64_t>> physical;
    std::vector<std::pair<std::uint64_t, std::uint64_t>> logical;
    for (const auto& value : root.at("placements")) {
        exact_keys(value, {"range_id", "file_offset", "length", "page_bytes",
                           "registered_address",
                           "canonical_physical_address", "endpoint_id",
                           "generation"});
        const auto id = unsigned_field(value, "range_id");
        if (!id || id > UINT32_MAX)
            throw std::invalid_argument("invalid placement range_id");
        Placement item{
            .range_id = static_cast<std::uint32_t>(id),
            .file_offset = unsigned_field(value, "file_offset"),
            .registered_address = unsigned_field(value, "registered_address"),
            .length = unsigned_field(value, "length"),
            .page_bytes = unsigned_field(value, "page_bytes"),
            .canonical_physical_address =
                unsigned_field(value, "canonical_physical_address"),
            .endpoint_id = unsigned_field(value, "endpoint_id"),
            .generation = unsigned_field(value, "generation"),
        };
        item.extent = extent(item.length, item.page_bytes);
        if (!item.registered_address || !item.endpoint_id || !item.generation ||
            item.file_offset % item.page_bytes ||
            item.canonical_physical_address % item.page_bytes)
            throw std::invalid_argument("invalid or unaligned placement");
        const auto logical_end = checked_add(item.file_offset, item.extent);
        const auto physical_end = checked_add(item.canonical_physical_address,
                                              item.extent);
        if (physical_end > total_capacity)
            throw std::invalid_argument("placement exceeds HBF capacity");
        if (!result.emplace(item.range_id, item).second)
            throw std::invalid_argument("duplicate placement range_id");
        logical.emplace_back(item.file_offset, logical_end);
        physical.emplace_back(item.canonical_physical_address, physical_end);
    }
    const auto disjoint = [](auto& spans) {
        std::sort(spans.begin(), spans.end());
        for (std::size_t i = 1; i < spans.size(); ++i)
            if (spans[i].first < spans[i-1].second)
                throw std::invalid_argument("overlapping UCIe placements");
    };
    disjoint(logical);
    disjoint(physical);
    return result;
}

#include "page_striped_parser_v3.inc"

// Existing v1/v2 parsing remains unchanged.
std::map<std::uint32_t, Placement> load_chunk_placements(
    const std::filesystem::path& path, const ucie::MultistackProfile& profile)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open UCIe placement manifest");
    const auto root = json::parse(input);
    if (root.at("schema") == "hbfsim.ucie.host_placement.page_striped.v3")
        return load_page_striped_placements(root, profile);
    if (root.at("schema") == "hbfsim.ucie.host_placement.v1")
        return load_placements(path, profile.stack_capacity_bytes*profile.stack_count);
    exact_keys(root, {"schema", "placements"});
    if (root.at("schema") != "hbfsim.ucie.host_placement.v2" ||
        !root.at("placements").is_array() || root.at("placements").empty() ||
        profile.stack_count != 4 || profile.modules_per_stack != 16 ||
        profile.stack_capacity_bytes != (512ULL<<30))
        throw std::invalid_argument("unsupported v2 placement contract");
    const auto module_bytes=profile.stack_capacity_bytes/profile.modules_per_stack;
    const auto capacity=profile.stack_capacity_bytes*profile.stack_count;
    std::map<std::uint32_t, Placement> result;
    std::set<std::uint64_t> regions;
    std::set<std::string> aliases;
    std::vector<std::pair<std::uint64_t,std::uint64_t>> physical, logical;
    std::size_t count=0;
    for (const auto& value:root.at("placements")) {
        exact_keys(value,{"range_id","file_offset","length","page_bytes",
            "registered_address","endpoint_id","generation","stream_id",
            "aliases","storage_sha256","chunks"});
        const auto id=unsigned_field(value,"range_id"), stream=unsigned_field(value,"stream_id");
        if (!id || id>UINT32_MAX || stream>UINT32_MAX)
            throw std::invalid_argument("invalid parent/stream ID");
        Placement item{.range_id=static_cast<std::uint32_t>(id),
            .file_offset=unsigned_field(value,"file_offset"),
            .registered_address=unsigned_field(value,"registered_address"),
            .length=unsigned_field(value,"length"),
            .page_bytes=unsigned_field(value,"page_bytes"),
            .endpoint_id=unsigned_field(value,"endpoint_id"),
            .generation=unsigned_field(value,"generation")};
        item.chunked=true; item.stream_id=static_cast<std::uint32_t>(stream);
        item.extent=extent(item.length,item.page_bytes);
        if (item.page_bytes!=16384 || !item.registered_address || !item.endpoint_id ||
            !item.generation || item.file_offset%item.page_bytes)
            throw std::invalid_argument("invalid v2 parent alignment/identity");
        checked_add(item.registered_address,item.length);
        logical.emplace_back(item.file_offset,checked_add(item.file_offset,item.extent));
        if (!value.at("aliases").is_array() || value.at("aliases").empty() ||
            !value.at("storage_sha256").is_string())
            throw std::invalid_argument("missing v2 storage identity");
        for (const auto& alias:value.at("aliases")) {
            if (!alias.is_string() || alias.get<std::string>().empty() ||
                !aliases.insert(alias.get<std::string>()).second)
                throw std::invalid_argument("duplicate/invalid parent alias");
            item.aliases.push_back(alias.get<std::string>());
        }
        item.storage_sha256=value.at("storage_sha256").get<std::string>();
        if (item.storage_sha256.size()!=64 || !std::all_of(item.storage_sha256.begin(),
            item.storage_sha256.end(),[](char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f');}))
            throw std::invalid_argument("invalid backing storage SHA256");
        const auto& chunks=value.at("chunks");
        if (!chunks.is_array() || chunks.empty())
            throw std::invalid_argument("empty v2 chunk union");
        std::uint64_t cursor=0;
        for (const auto& chunk:chunks) {
            exact_keys(chunk,{"region_id","offset","extent","canonical_physical_address"});
            Chunk piece{.region_id=unsigned_field(chunk,"region_id"),
                .offset=unsigned_field(chunk,"offset"),.extent=unsigned_field(chunk,"extent"),
                .physical=unsigned_field(chunk,"canonical_physical_address")};
            const auto end=checked_add(piece.physical,piece.extent);
            if (!piece.region_id || !piece.extent || piece.offset!=cursor ||
                piece.offset%item.page_bytes || piece.extent%item.page_bytes ||
                piece.physical%item.page_bytes || end>capacity ||
                piece.physical/module_bytes!=(end-1)/module_bytes ||
                !regions.insert(piece.region_id).second)
                throw std::invalid_argument("invalid/colliding v2 chunk");
            cursor=checked_add(cursor,piece.extent);
            if (cursor>item.extent) throw std::invalid_argument("v2 chunk exceeds parent");
            physical.emplace_back(piece.physical,end); item.chunks.push_back(piece);
            if (++count>profile.software.max_backing_ranges)
                throw std::invalid_argument("v2 internal range count exceeds unchanged bound");
        }
        if (cursor!=item.extent || !result.emplace(item.range_id,std::move(item)).second)
            throw std::invalid_argument("v2 union/parent ID mismatch");
    }
    for (const auto& [id,item]:result) {
        (void)item;
        if (regions.contains(id)) throw std::invalid_argument("parent/internal region ID collision");
    }
    for (auto* spans:{&logical,&physical}) {
        std::sort(spans->begin(),spans->end());
        for (std::size_t i=1;i<spans->size();++i)
            if ((*spans)[i].first<(*spans)[i-1].second)
                throw std::invalid_argument("overlapping v2 logical/physical chunks");
    }
    return result;
}

HbfCompletion completion_for(const HbfRequest& request, RequestStatus status,
                             std::uint64_t modeled_ns,
                             std::uint64_t service_ns)
{
    return HbfCompletion{
        .request_id = request.request_id,
        .modeled_completion_ns = checked_add(request.arrival_ns, modeled_ns),
        .modeled_ns = modeled_ns,
        .service_ns = service_ns,
        .cache_frame_address = 0,
        .page_generation = request.page_generation,
        .status = static_cast<std::uint32_t>(status),
        .checksum = 0,
        .reserved = 0,
    };
}
} // namespace

struct UcieBackendAdapter::Impl {
    struct Pending {
        HbfRequest original{};
        std::uint64_t sim_arrival{};
        bool submitted{};
        std::optional<HbfCompletion> terminal;
    };

    ControlView control;
    UcieBackendOptions options;
    std::unique_ptr<ucie::MultistackFrontend> frontend;
    std::map<std::uint32_t, Placement> placements;
    std::set<std::uint32_t> registered;
    std::map<std::uint64_t, Pending> pending;
    std::deque<std::uint64_t> order;
    std::uint64_t control_generation{};
    std::uint64_t raw_page_bytes{};
    std::uint64_t published{};
    std::uint64_t unsupported{};
    std::uint64_t failed{};
    std::uint64_t late_clamped{};
    std::uint64_t pre_epoch_arrivals{};
    std::uint64_t staged_admission_clamps{};
    std::uint64_t staged_admission_wait_sum_ns{};
    std::uint64_t first_gpu_arrival{};
    std::uint64_t last_sim_arrival{};
    std::uint64_t max_sim_ready{};
    std::uint64_t total_sim_service_ns{};
    std::uint64_t sim_service_over_gpu_budget{};
    std::uint64_t post_publish_cleanup_failures{};
    bool epoch_set{};
    bool fatal_partial_registration{};
    std::uint64_t internal_ranges_installed{};
    std::vector<json> timing_samples;

    Impl(ControlView view, UcieBackendOptions config)
        : control(view), options(std::move(config))
    {
        if (!control.valid() || options.top_profile.empty() ||
            options.worker_executable.empty() || options.placement_manifest.empty())
            throw std::invalid_argument("invalid UCIe backend options/control");
        control_generation = atomic_load(control.header()->control_generation,
                                         std::memory_order_acquire);
        if (!control_generation)
            throw std::invalid_argument("UCIe backend requires control generation");
        const auto profile = ucie::load_multistack_profile(options.top_profile);
        if (profile.stack_capacity_bytes >
            UINT64_MAX / profile.stack_count)
            throw std::overflow_error("top profile capacity overflow");
        placements = load_chunk_placements(options.placement_manifest, profile);
        frontend = std::make_unique<ucie::MultistackFrontend>(
            options.top_profile, options.worker_executable,
            ucie::MultistackAdvanceMode::EventDrivenIdleDeferred);
    }

    const SharedRangeRecord* range_for(std::uint32_t id) const
    {
        const auto count = atomic_load(control.header()->range_count,
                                       std::memory_order_acquire);
        if (count > kRangeCapacity)
            throw std::runtime_error("invalid shared range count");
        const SharedRangeRecord* found = nullptr;
        for (std::uint32_t index = 0; index < count; ++index) {
            if (control.ranges()[index].range_id != id) continue;
            if (found) throw std::runtime_error("duplicate shared range id");
            found = &control.ranges()[index];
        }
        return found;
    }

    RequestStatus validate(const HbfRequest& request,
                           const SharedRangeRecord*& record,
                           const Placement*& placement)
    {
        if (!request.request_id || !request.range_id || !request.bytes ||
            (request.deadline_ns &&
             request.deadline_ns < request.arrival_ns) || request.flags ||
            request.operation != static_cast<std::uint32_t>(RequestOperation::Read))
            return RequestStatus::Unsupported;
        record = range_for(request.range_id);
        const auto item = placements.find(request.range_id);
        if (!record || item == placements.end())
            throw std::runtime_error("missing registered UCIe placement");
        placement = &item->second;
        if (record->range_id != placement->range_id ||
            record->base != placement->registered_address ||
            record->file_offset != placement->file_offset ||
            record->length != placement->length ||
            record->page_bytes != placement->page_bytes ||
            ((placement->chunked || placement->striped) && record->stream_id != placement->stream_id))
            throw std::runtime_error("UCIe placement differs from registered range");
        if (record->mode != HBFSIM_RANGE_MODE_TIMING ||
            (record->permissions & HBFSIM_RANGE_READ) == 0 ||
            record->page_bytes != request.bytes ||
            record->stream_id != request.stream_id ||
            request.logical_address % request.bytes ||
            request.logical_address < record->file_offset ||
            request.logical_address > UINT64_MAX - request.bytes ||
            request.logical_address + request.bytes >
                record->file_offset + placement->extent)
            return RequestStatus::Unsupported;
        return RequestStatus::Ready;
    }

    void register_once(const SharedRangeRecord& record,
                       const Placement& placement)
    {
        if (fatal_partial_registration || !frontend)
            throw std::runtime_error("fatal partial v2 registration; retry forbidden");
        if (registered.contains(record.range_id)) return;
        if (placement.striped) {
            try {
                frontend->add_page_striped_backing(placement.stripe);
                internal_ranges_installed += placement.stripe.segments.size();
                registered.insert(record.range_id);
            } catch (...) {
                fatal_partial_registration = true;
                frontend.reset();
                throw;
            }
            return;
        }
        if (placement.chunked) {
            try {
                for (const auto& chunk:placement.chunks) {
                    frontend->add_backing({.region_id=chunk.region_id,
                        .address=checked_add(record.file_offset,chunk.offset),
                        .bytes=chunk.extent,.canonical_id=record.range_id,
                        .canonical_physical_address=chunk.physical,
                        .generation=placement.generation,.endpoint_id=placement.endpoint_id,
                        .readable=true});
                    ++internal_ranges_installed;
                }
                registered.insert(record.range_id);
            } catch (...) {
                // Earlier chunks may already exist: fail the whole owned
                // frontend and clean its workers, never retry this parent.
                fatal_partial_registration=true;
                frontend.reset();
                throw;
            }
            return;
        }
        frontend->add_backing({
            .region_id = record.range_id,
            .address = record.file_offset,
            .bytes = placement.extent,
            .canonical_id = record.range_id,
            .canonical_physical_address = placement.canonical_physical_address,
            .generation = placement.generation,
            .endpoint_id = placement.endpoint_id,
            .readable = true,
        });
        registered.insert(record.range_id);
    }

    std::uint64_t simulated_arrival(const HbfRequest& request)
    {
        if (!epoch_set)
            throw std::logic_error("GPU arrival epoch not established");
        // Ticket order is not GPU timestamp order: another lane can sample
        // globaltimer first but reserve its ring slot later.
        const bool predates_epoch = request.arrival_ns < first_gpu_arrival;
        if (predates_epoch) ++pre_epoch_arrivals;
        const auto relative = predates_epoch ? std::uint64_t{0}
            : request.arrival_ns - first_gpu_arrival;
        const auto assigned = std::max({relative, last_sim_arrival,
                                        frontend->current_time_ns()});
        if (assigned != relative) ++late_clamped;
        if (timing_samples.size() < 64)
            timing_samples.push_back({{"request_id",request.request_id},
                {"ticket",request.sequence},
                {"gpu_globaltimer_arrival_ns",request.arrival_ns},
                {"sim_arrival_ns",assigned},
                {"predates_first_ticket_epoch",predates_epoch},
                {"clamped",assigned != relative}});
        last_sim_arrival = assigned;
        return assigned;
    }

    std::uint64_t staged_count() const {
        return std::count_if(pending.begin(),pending.end(),[](const auto& item) {
            return !item.second.submitted && !item.second.terminal;
        });
    }
    void observe_pending() const {
        if(!ucie::parallel_component_accounting_v1::running())return;
        ucie::parallel_component_accounting_v1::adapter_state(pending.size(),order.size(),staged_count());
    }
    bool poll_once()
    {
        ucie::parallel_component_accounting_v1::RequestScope request_scope;
        if (fatal_partial_registration || !frontend)
            throw std::runtime_error("fatal partial v2 registration; polling forbidden");
        if (atomic_load(control.header()->control_generation,
                        std::memory_order_acquire) != control_generation)
            throw std::runtime_error("UCIe control generation changed");
        ucie::short_qkv_observer::adapter_local = [this] {
            return ucie::short_qkv_observer::AdapterLocal{pending.size(),order.size(),staged_count(),fatal_partial_registration};
        };
        observe_pending();
        bool progressed = false;
        const auto limit = control.header()->ring_capacity;
        std::vector<std::uint64_t> newly_popped;
        HbfRequest request{};
        while (pending.size() < limit && control.try_pop_request(request)) {
            progressed = true;
            if (pending.contains(request.sequence))
                throw std::runtime_error("duplicate HBF request ticket");
            const SharedRangeRecord* record = nullptr;
            const Placement* placement = nullptr;
            const auto status = validate(request, record, placement);
            Pending state{.original=request};
            if (status == RequestStatus::Ready) {
                register_once(*record, *placement);
            } else {
                state.terminal = completion_for(request,status,0,0);
                ++unsupported;
            }
            pending.emplace(request.sequence,std::move(state));
            order.push_back(request.sequence);
            newly_popped.push_back(request.sequence);
            observe_pending();
        }
        // GPU lanes can sample globaltimer before a peer yet reserve a later
        // ticket. The first concurrently drained batch establishes its epoch
        // from the minimum valid sample, independent of ticket order.
        if (!epoch_set) {
            for (const auto ticket : newly_popped) {
                const auto& state = pending.at(ticket);
                if (state.terminal) continue;
                first_gpu_arrival = epoch_set
                    ? std::min(first_gpu_arrival,state.original.arrival_ns)
                    : state.original.arrival_ns;
                epoch_set = true;
            }
        }
        for (const auto ticket : newly_popped) {
            auto& state = pending.at(ticket);
            if (!state.terminal)
                state.sim_arrival = simulated_arrival(state.original);
        }
        for (const auto ticket : order) {
            auto& state = pending.at(ticket);
            if (state.submitted || state.terminal) continue;
            const auto& request = state.original;
            const auto& placement = placements.at(request.range_id);
            // A bounded host staging queue may retain a request while the
            // frontend advances other media work. Keep the mapped original
            // arrival in state for end-to-end queueing time; the actual
            // frontend admission cannot be in its already-closed past.
            const auto admission_ns=std::max(state.sim_arrival,
                                             frontend->current_time_ns());
            const ucie::GlobalRead read{
                .request_id = request.request_id,
                .arrival_ns = admission_ns,
                .deadline_ns = 0, // GPU resolver liveness is not UCIe read deadline.
                .address = request.logical_address,
                .bytes = request.bytes,
                .endpoint_id = placement.endpoint_id,
                .axi_id = static_cast<std::uint32_t>(ticket % (1U << 14)),
                .operation = 0,
            };
            if (!frontend->try_submit(read)) break; // bounded staging, no loss
            if (admission_ns != state.sim_arrival) {
                ++staged_admission_clamps;
                staged_admission_wait_sum_ns=checked_add(
                    staged_admission_wait_sum_ns,
                    admission_ns-state.sim_arrival);
            }
            state.submitted = true;
            observe_pending();
            raw_page_bytes = checked_add(raw_page_bytes,request.bytes);
            progressed = true;
        }
        const auto drain_ready = [&]() {
            bool any = false;
            for (;;) {
                bool changed = false;
                for (const auto id : frontend->ready_ids()) {
                    auto found = std::find_if(order.begin(),order.end(),
                        [&](auto ticket) {
                            return pending.at(ticket).original.request_id == id;
                        });
                    if (found == order.end())
                        throw std::runtime_error("unknown UCIe parent completion");
                    auto& state = pending.at(*found);
                    if (state.terminal) continue;
                    const auto ready = frontend->peek_completion(id);
                    if (!ready || ready->ready_ns < state.sim_arrival)
                        throw std::runtime_error("invalid UCIe ready time");
                    const auto delta = ready->ready_ns - state.sim_arrival;
                    const auto status = ready->result == ucie::DeviceResult::Ready
                        ? RequestStatus::Ready
                        : ready->result == ucie::DeviceResult::TimedOut
                            ? RequestStatus::Timeout : RequestStatus::IoError;
                    const auto injected = options.wait_mode ==
                        UcieWaitMode::ZeroInjected ? std::uint64_t{0} : delta;
                    state.terminal = completion_for(state.original,status,
                                                    injected,delta);
                    max_sim_ready = std::max(max_sim_ready,ready->ready_ns);
                    total_sim_service_ns = checked_add(total_sim_service_ns,
                                                       delta);
                    if (state.original.deadline_ns &&
                        delta > state.original.deadline_ns -
                                state.original.arrival_ns)
                        ++sim_service_over_gpu_budget;
                    if (status != RequestStatus::Ready) ++failed;
                    changed = true;
                }
                for (auto item = order.begin(); item != order.end();) {
                    auto& state = pending.at(*item);
                    if (!state.terminal ||
                        !control.try_publish_completion(*item,*state.terminal)) {
                        ++item;
                        continue;
                    }
                    if (state.submitted)
                        try {
                            (void)frontend->consume_completion(
                                state.original.request_id);
                        } catch (...) {
                            // The old GPU ticket is already visible. Do not
                            // retract it or reuse its pinned worker resource.
                            ++post_publish_cleanup_failures;
                            throw std::runtime_error(
                                "UCIe worker consume failed after HBF ticket publication");
                        }
                    ++published;
                    pending.erase(*item);
                    item = order.erase(item);
            observe_pending();
                    changed = true;
                }
                any |= changed;
                if (!changed) return any;
                // consume_completion may create another same-time ready
                // parent through credit feedback. Drain that real result
                // before asking the event coordinator for a later horizon.
            }
        };
        progressed |= drain_ready();
        if (!order.empty() &&
            std::any_of(order.begin(),order.end(),[&](auto ticket) {
                return pending.at(ticket).submitted &&
                       !pending.at(ticket).terminal;
            })) {
            const auto now = frontend->current_time_ns();
            // The event coordinator performs a same-time fixed point first.
            // It may conservatively return now+1 only where the underlying
            // MQSim boundary is not yet peekable; this adapter never creates
            // an unconditional per-ns IPC loop of its own.
            const auto ready_before_next=frontend->ready_ids().size();
            const auto next = frontend->next_event_ns();
            const auto ready_after_next=frontend->ready_ids().size();
            // next_event_ns itself closes the current horizon. It can make a
            // parent ready even when no future event remains.
            if (drain_ready()) return true;
            if (!next) {
                const bool blocked_by_hbf_ring=std::any_of(
                    order.begin(),order.end(),[&](auto ticket) {
                        const auto& state=pending.at(ticket);
                        return state.submitted && state.terminal.has_value();
                    });
                if (blocked_by_hbf_ring)
                    return progressed; // caller must consume an old HBF slot
                const auto accounting=frontend->accounting();
                std::uint64_t physical_outstanding=0;
                for (const auto& stack:accounting.stacks)
                    physical_outstanding=checked_add(physical_outstanding,
                                                     stack.physical_outstanding);
                std::ostringstream details;
                details << "active UCIe request has no next event"
                        << " sim_now=" << frontend->current_time_ns()
                        << " ready_before_next=" << ready_before_next
                        << " ready_after_next=" << ready_after_next
                        << " pending=" << order.size()
                        << " caller_outstanding="
                        << accounting.host.caller_outstanding
                        << " physical_outstanding=" << physical_outstanding
                        << " native_commands=" << accounting.native_commands;
                for (const auto ticket:order) {
                    const auto& state=pending.at(ticket);
                    details << " [ticket=" << ticket
                            << ",request=" << state.original.request_id
                            << ",submitted=" << state.submitted
                            << ",terminal=" << state.terminal.has_value()
                            << ",sim_arrival=" << state.sim_arrival << ']';
                }
                throw std::runtime_error(details.str());
            }
            if (*next < now)
                throw std::runtime_error("UCIe next event moved backwards");
            auto target = *next;
            for (const auto ticket : order) {
                const auto& state = pending.at(ticket);
                if (state.terminal) continue;
                if (!state.submitted && state.sim_arrival > now)
                    target = std::min(target,state.sim_arrival);
                // Resolver liveness remains on the GPU clock. This translated
                // point is only a host scheduling boundary, not a UCIe read
                // deadline or a hardware cancel packet.
                if (state.original.deadline_ns &&
                    state.original.deadline_ns >= first_gpu_arrival) {
                    const auto boundary = state.original.deadline_ns -
                                          first_gpu_arrival;
                    if (boundary > now) target = std::min(target,boundary);
                }
            }
            frontend->advance_until(target);
            progressed = true;
        } else {
            // If all staged reads were rejected by a temporary bound before
            // their future arrival, there may be no worker event yet. Move
            // only to that explicit host-input boundary and retry admission.
            const auto now = frontend->current_time_ns();
            std::optional<std::uint64_t> next_arrival;
            for (const auto ticket : order) {
                const auto& state = pending.at(ticket);
                if (state.terminal || state.submitted ||
                    state.sim_arrival <= now) continue;
                next_arrival = next_arrival
                    ? std::min(*next_arrival,state.sim_arrival)
                    : state.sim_arrival;
            }
            if (next_arrival) {
                frontend->advance_until(*next_arrival);
                progressed = true;
            } else if (std::any_of(order.begin(),order.end(),
                        [&](auto ticket) {
                            const auto& state=pending.at(ticket);
                            return !state.terminal && !state.submitted;
                        })) {
                // Admission was attempted before drain_ready() released its
                // completed parents. Retry with the newly freed capacity on
                // the next poll before calling an empty frontend a deadlock.
                // A permanently invalid request gets only this one progress
                // turn: the next rejected poll has progressed == false.
                if (progressed) return true;
                // A staged request can be waiting on native resources from
                // an already consumed parent. Advance that actual worker
                // event, or wait for an unconsumed HBF completion slot. An
                // otherwise empty frontend cannot make this request valid by
                // repeatedly polling at the same simulation time.
                const auto next=frontend->next_event_ns();
                if (drain_ready()) return true;
                if (next) {
                    if (*next < now)
                        throw std::runtime_error(
                            "UCIe staged next event moved backwards");
                    frontend->advance_until(*next);
                    progressed=true;
                } else if (!std::any_of(order.begin(),order.end(),
                               [&](auto ticket) {
                                   const auto& state=pending.at(ticket);
                                   return state.submitted &&
                                          state.terminal.has_value();
                               })) {
                    throw std::runtime_error(
                        "staged UCIe read cannot enter empty frontend");
                }
            }
        }
        return progressed;
    }

    void write_report(const std::filesystem::path& path) const
    {
        if (fatal_partial_registration || !frontend) {
            std::ofstream output(path);
            if (!output) throw std::runtime_error("cannot open fatal v2 registration report");
            output << json({{"schema","hbfsim.ucie.host_backend_report.v2.failure"},
                {"status","FATAL_PARTIAL_CHUNK_REGISTRATION"},{"complete",false},
                {"registered_parent_count",registered.size()},
                {"internal_ranges_installed_before_cleanup",internal_ranges_installed},
                {"stripe_failed_parent_prefix_count","UNKNOWN; count includes only fully registered striped parents, peer logs identify partial acknowledgements"},
                {"cleanup","owned frontend destroyed; no successful closure claimed"}}).dump(2) << '\n';
            if (!output) throw std::runtime_error("cannot write fatal v2 registration report");
            return;
        }
        const auto accounting = frontend->accounting();
        json stacks=json::array();
        for (std::size_t index=0;index<accounting.stacks.size();++index) {
            const auto& worker=accounting.stacks[index];
            json module_links = json::array();
            for (const auto& module : worker.module_links)
            {
                module_links.push_back({
                    {"module_id", module.module_id},
                    {"ar_wire_bytes", module.ar_wire_bytes},
                    {"r_wire_bytes", module.r_wire_bytes},
                    {"ar_credit_granules", module.ar_credit_granules},
                    {"r_credit_granules", module.r_credit_granules},
                });
            }
            stacks.push_back({
                {"stack",index},
                {"module_links",std::move(module_links)},
                {"process_id",worker.process_id},
                {"transport",worker.transport},
                {"logical_ipc_commands",worker.ipc_commands},
                {"physical_ipc_send_count",worker.physical_ipc_send_count},
                {"physical_ipc_receive_count",worker.physical_ipc_receive_count},
                {"current_time_ns",worker.current_time_ns},
                {"accepted",worker.accepted},
                {"succeeded",worker.succeeded},
                {"cancelled",worker.cancelled},
                {"failed",worker.failed},
                {"native_commands",worker.native_commands},
                {"media_submit_bytes",worker.media_submit_bytes},
                {"physical_outstanding",worker.physical_outstanding},
                {"max_rss_kib",worker.max_rss_kib},
            });
        }
        json report{
            {"schema", "hbfsim.ucie.host_backend_report.v1"},
            {"wait_mode", options.wait_mode == UcieWaitMode::Nominal
                ? "nominal" : "zero_injected"},
            {"control_generation", control_generation},
            {"gpu_globaltimer_epoch_ns", epoch_set ? json(first_gpu_arrival) : json(nullptr)},
            {"last_sim_arrival_ns", last_sim_arrival},
            {"sim_now_ns", frontend->current_time_ns()},
            {"max_sim_ready_ns", max_sim_ready},
            {"sim_service_delta_sum_ns", total_sim_service_ns},
            {"sim_service_over_gpu_budget_diagnostic",
                sim_service_over_gpu_budget},
            {"post_publish_cleanup_failures",post_publish_cleanup_failures},
            {"clock_note", "gpu_globaltimer arrival/deadline and independent simulator ns; modeled wait is injected separately"},
            {"late_arrival_clamps", late_clamped},
            {"pre_epoch_arrivals", pre_epoch_arrivals},
            {"staged_admission_clamps", staged_admission_clamps},
            {"staged_admission_wait_sum_ns",
                staged_admission_wait_sum_ns},
            {"timing_mapping_samples_first_64", timing_samples},
            {"published", published},
            {"unsupported", unsupported},
            {"failed", failed},
            {"original_hbf_page_bytes", raw_page_bytes},
            {"original_hbf_request_bytes_submitted", raw_page_bytes},
            {"original_frontend_bytes", accounting.host.original_bytes},
            {"caller_outstanding", accounting.host.caller_outstanding},
            {"axi_payload_bytes", accounting.host.axi_payload_bytes},
            {"axi_child_transactions", accounting.host.axi_payload_bytes/64},
            {"worker_ar_wire_bytes", accounting.worker_ar_wire_bytes},
            {"worker_r_wire_bytes", accounting.worker_r_wire_bytes},
            {"upstream_ar_wire_bytes", accounting.upstream_ar_wire_bytes},
            {"upstream_r_wire_bytes", accounting.upstream_r_wire_bytes},
            {"media_submit_bytes", accounting.media_submit_bytes},
            {"native_commands", accounting.native_commands},
            {"stacks",std::move(stacks)},
        };
        if (std::any_of(placements.begin(),placements.end(),
            [](const auto& entry){return entry.second.chunked || entry.second.striped;})) {
            const bool striped = std::any_of(placements.begin(), placements.end(),
                [](const auto& entry) { return entry.second.striped; });
            report["placement_schema"] = striped ? "hbfsim.ucie.host_placement.page_striped.v3" :
                                                   "hbfsim.ucie.host_placement.v2";
            if (striped) {
                json identities = json::array();
                for (const auto& [id, placement] : placements) {
                    identities.push_back({{"range_id", id}, {"generation", placement.generation},
                        {"endpoint_id", placement.endpoint_id}, {"length", placement.length},
                        {"descriptor_sha256", placement.stripe.descriptor_sha256},
                        {"packed_segment_count", placement.stripe.segments.size()}});
                }
                report["stripe_descriptor_identities"] = std::move(identities);
                report["stripe_hash_validation_scope"] = "parser recomputes complete canonical descriptor SHA256; actual storage bytes require external byte identity verification";
            }
            report["registered_parent_count"]=registered.size();
            report["internal_ranges_installed"]=internal_ranges_installed;
            report["storage_hash_validation_scope"]="manifest format/identity metadata only; external backing-byte evidence required";
        }
        std::ofstream output(path);
        if (!output) throw std::runtime_error("cannot open UCIe backend report");
        output << report.dump(2) << '\n';
        if (!output) throw std::runtime_error("cannot write UCIe backend report");
    }
};

UcieBackendAdapter::UcieBackendAdapter(ControlView control,
                                       UcieBackendOptions options)
    : impl_(std::make_unique<Impl>(control,std::move(options))) {}
UcieBackendAdapter::~UcieBackendAdapter() = default;
bool UcieBackendAdapter::poll_once() { return impl_->poll_once(); }
void UcieBackendAdapter::write_report(const std::filesystem::path& path) const
{ impl_->write_report(path); }

} // namespace hbfsim::host_service
