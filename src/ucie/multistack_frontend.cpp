#include "private_observer_gate.hpp"
#include "parallel_component_accounting_v1.hpp"
#include <hbfsim/ucie/multistack_frontend.hpp>
#include "advance_gather_diagnostics_v1.hpp"

#include <hbfsim/ucie/bank_layout.hpp>

#include <algorithm>
#include <array>
#include <bit>
#include <cstdlib>
#include <limits>
#include <iostream>
#include <optional>
#include <set>
#include <stdexcept>
#include <utility>

namespace hbfsim::ucie {
namespace {
constexpr std::size_t max_unique_intervals=4'194'304; // host accounting bound
bool releasable_child_index_requested()
{
    const auto* value=std::getenv("HBFSIM_UCIE_RELEASABLE_CHILD_INDEX_V1");
    if (!value || std::string(value)=="0") return false;
    if (std::string(value)!="1") throw std::invalid_argument("releasable child index flag must be0 or1");
    return true;
}
bool deferred_initial_hello_requested()
{
    const auto* value=std::getenv("HBFSIM_UCIE_DEFERRED_INITIAL_HELLO_V1");
    if (!value || std::string(value)=="0") return false;
    if (std::string(value)!="1")
        throw std::invalid_argument("deferred initial HELLO flag must be0 or1");
    return true;
}
bool compact_wave_requested()
{
    const auto* value=std::getenv("HBFSIM_UCIE_COMPACT_CONSUME_WAVE_V1");
    if(!value || std::string(value)=="0") return false;
    if(std::string(value)!="1") throw std::invalid_argument("compact wave flag must be0 or1");
    return true;
}
std::optional<std::uint32_t> selected_local_stack(std::uint32_t stack_count)
{
    const char* raw=std::getenv("HBFSIM_UCIE_LOCAL_STACK_ID");
    if (!raw) return std::nullopt;
    const std::string value(raw);
    if (value.empty() || !std::all_of(value.begin(),value.end(),
            [](char c) { return c>='0' && c<='9'; }))
        throw std::invalid_argument("invalid local stack ID");
    const auto parsed=std::stoull(value);
    if (parsed>=stack_count)
        throw std::out_of_range("local stack ID outside topology");
    return static_cast<std::uint32_t>(parsed);
}
bool same_ready(const WorkerReady& a,const WorkerReady& b)
{
    return a.request_id==b.request_id && a.module_id==b.module_id &&
           a.result==b.result && a.ar_delivered_ns==b.ar_delivered_ns &&
           a.media_ready_ns==b.media_ready_ns &&
           a.response_delivered_ns==b.response_delivered_ns;
}
}

MultistackFrontend::MultistackFrontend(ContiguousStackMap map,
        const std::filesystem::path& worker_executable,
        const std::filesystem::path& stack_profile,bool shared_upstream,
        std::uint64_t max_reassembly_bytes,MultistackAdvanceMode advance_mode)
    : map_(map),registry_(map),profile_(load_device_profile(stack_profile)),
      shared_(shared_upstream),advance_mode_(advance_mode),
      max_reassembly_bytes_(max_reassembly_bytes)
{
    releasable_child_index_=releasable_child_index_requested();
    compact_wave_requested_=compact_wave_requested();
    if (max_reassembly_bytes_<64)
        throw std::invalid_argument("host reassembly capacity below one AXI beat");
    if (profile_.media.capacity_bytes!=map_.stack_bytes() ||
        profile_.layout.host_channels!=map_.modules_per_stack() ||
        profile_.link.local_capacity_bytes!=map_.module_bytes())
        throw std::invalid_argument("worker profile does not match stack placement");
    if (shared_) {
        auto upstream_profile=profile_.link;
        upstream_profile.max_accepted=4096; // bounded parent software queue
        upstream_=std::make_unique<StreamingLink>(upstream_profile);
    }
    const auto local_stack=selected_local_stack(map_.stack_count());
    workers_.reserve(map_.stack_count());
    // Preserve local0 initialization in its original first position. Other local
    // selections retain the old synchronous compatibility path.
    const bool defer_hello=deferred_initial_hello_requested() &&
        (!local_stack || *local_stack==0);
    if (!defer_hello) {
        for (std::uint32_t stack=0;stack<map_.stack_count();++stack)
            workers_.push_back(std::make_unique<StackWorkerClient>(
                worker_executable,stack_profile,stack,false,
                local_stack && *local_stack==stack));
    } else {
        std::uint64_t published=0,validated=0;
        try {
            for (std::uint32_t stack=0;stack<map_.stack_count();++stack) {
                const bool local=local_stack && *local_stack==stack;
                // Private constructor remains on this owner thread, never std::async.
                auto client=std::unique_ptr<StackWorkerClient>(new StackWorkerClient(
                    worker_executable,stack_profile,stack,false,local,!local));
                if (client->initial_hello_pending()) ++published;
                workers_.push_back(std::move(client));
            }
            for (auto& client:workers_) {
                if (!client->initial_hello_pending()) continue;
                client->finish_initial_hello();++validated;
            }
            std::cerr << "STARTUP_DEFERRED_HELLO_V1 enabled=1 published=" << published
                << " validated=" << validated << " pending_unknown=0 peak_pending="
                << published << '\n';
        } catch (...) {
            // Preserve the first original-order exception. Later pending starts
            // are unknown; poison before clear so STOP cannot read their HELLO.
            std::uint64_t pending_unknown=0;
            for (auto& client:workers_) {
                if (client->initial_hello_pending()) ++pending_unknown;
                client->abandon_initial_hello();
            }
            std::cerr << "STARTUP_DEFERRED_HELLO_V1 enabled=1 published=" << published
                << " validated=" << validated << " pending_unknown=" << pending_unknown
                << " peak_pending=" << published << '\n';
            workers_.clear();
            throw;
        }
    }
    idle_workers_.resize(workers_.size());
    short_qkv_observer::frontend_local = [this] {
        return short_qkv_observer::FrontendLocal{parents_.size(),child_to_parent_.size(),
            retiring_.size(),poisoned_,quiescent_at_now_};
    };
    short_qkv_observer::progress_tail = [this] {
        const auto next=next_event_ns();
        if(next){if(*next<now_)throw std::logic_error("diagnostic tail moved backwards");advance_until(*next);}
    };
}

MultistackFrontend::~MultistackFrontend()
{
    if(compact_wave_requested_) {
        const auto& c=compact_wave_counters_;
        std::cerr << "COMPACT_CONSUME_WAVE_V1 enabled=1 attempts=" << c.attempts
            << " published=" << c.published << " validated=" << c.validated
            << " confirmed=" << c.confirmed << " unknown_pending=" << c.unknown_pending
            << " width1=" << c.published_width[1] << " width2=" << c.published_width[2]
            << " width3=" << c.published_width[3] << '\n';
    }
}

bool MultistackFrontend::idle_mode() const noexcept
{
    return advance_mode_==MultistackAdvanceMode::EventDrivenIdleDeferred &&
           !external_worker_access_;
}

bool MultistackFrontend::no_parent_child_on(std::uint32_t stack) const
{
    for (const auto& [id,parent]:parents_) {
        (void)id;
        for (const auto& child:parent.children)
            if (child.route.location.stack_id==stack) return false;
    }
    return true;
}

void MultistackFrontend::check_idle_liveness(std::uint32_t stack)
{
    auto& idle=idle_workers_.at(stack);
    if (!idle.certified) return;
    if (!workers_.at(stack)->transport_alive())
        throw std::runtime_error("deferred idle worker transport died");
    const auto wall=std::chrono::steady_clock::now();
    if (wall-idle.heartbeat<std::chrono::seconds(30)) return;
    const auto stats=workers_.at(stack)->stats();
    if (stats.physical_outstanding || stats.current_time_ns!=idle.physical_now)
        throw std::logic_error("deferred idle worker changed without parent input");
    idle.heartbeat=wall;
}

void MultistackFrontend::catch_up_worker(std::uint32_t stack)
{
    if (!idle_mode()) return;
    auto& idle=idle_workers_.at(stack);
    if (idle.physical_now==now_) return;
    if (!idle.certified || idle.physical_now>now_ ||
        last_closed_global_<idle.physical_now || last_closed_global_>=now_)
        throw std::logic_error("invalid deferred worker clock frontier");
    check_idle_liveness(stack);
    const auto step=[&](std::uint64_t target) {
        if (target==idle.physical_now) return;
        if (!workers_.at(stack)->close_and_advance(idle.physical_now,target).empty())
            throw std::logic_error("idle worker produced unexpected ready result");
        idle.physical_now=target;
    };
    // Recreate the baseline's most recent closed horizon, then leave now open.
    step(last_closed_global_);
    step(now_);
}

void MultistackFrontend::touch_worker(std::uint32_t stack)
{
    if (!idle_mode()) return;
    catch_up_worker(stack);
    idle_workers_.at(stack).certified=false;
}

void MultistackFrontend::flush_idle_workers()
{
    if (!idle_mode()) return;
    for (std::uint32_t stack=0;stack<workers_.size();++stack)
        catch_up_worker(stack);
}

void MultistackFrontend::certify_idle_workers()
{
    // With no parent there is no cross-stack event traffic to amortize a probe.
    if (!idle_mode() || parents_.empty()) return;
    for (std::uint32_t stack=0;stack<workers_.size();++stack) {
        auto& idle=idle_workers_[stack];
        if (idle.certified || idle.physical_now!=now_ ||
            !no_parent_child_on(stack)) continue;
        const auto stats=workers_[stack]->stats();
        if (stats.current_time_ns!=now_ || stats.physical_outstanding) continue;
        const auto peek=workers_[stack]->next_event(now_);
        if (!peek.supported || peek.next_ns) continue;
        idle.certified=true;
        idle.heartbeat=std::chrono::steady_clock::now();
    }
}

StackWorkerClient& MultistackFrontend::worker(std::uint32_t stack)
{
    if (poisoned_) throw std::logic_error("multistack worker failure");
    try { flush_idle_workers(); }
    catch (...) { abort_workers(); throw; }
    external_worker_access_=true;
    for (auto& idle:idle_workers_) idle.certified=false;
    invalidate_event_cache();
    return *workers_.at(stack);
}

MultistackAccounting MultistackFrontend::accounting()
{
    if (poisoned_) throw std::logic_error("multistack worker failure");
    MultistackAccounting result;
    result.host=counters_;
    if (upstream_) {
        result.upstream_ar_wire_bytes=upstream_->counters(
            Direction::Request).wire_bytes;
        result.upstream_r_wire_bytes=upstream_->counters(
            Direction::Return).wire_bytes;
    }
    try {
        flush_idle_workers();
        for (auto& worker:workers_) {
            auto stats=worker->stats();
            result.media_submit_bytes+=stats.media_submit_bytes;
            result.native_commands+=stats.native_commands;
            for (const auto& link:stats.module_links) {
                result.worker_ar_wire_bytes+=link.ar_wire_bytes;
                result.worker_r_wire_bytes+=link.r_wire_bytes;
            }
            result.stacks.push_back(std::move(stats));
        }
    } catch (...) { abort_workers(); throw; }
    return result;
}

void MultistackFrontend::record_unique(const Parent& parent)
{
    for (const auto& child:parent.children) {
        if (!counters_.unique_canonical_exact) {
            ++counters_.unique_tracking_dropped_spans;
            continue;
        }
        const auto start=child.route.original_canonical_address;
        const auto end=start+child.route.original_bytes;
        auto& intervals=unique_intervals_[{parent.canonical_id,
            parent.generation,parent.read.endpoint_id}];
        auto it=intervals.lower_bound(start);
        if (it!=intervals.begin()) {
            auto before=std::prev(it);
            if (before->second>=start) it=before;
        }
        auto merged_start=start,merged_end=end;
        std::uint64_t old_bytes=0;
        std::size_t erased=0;
        while (it!=intervals.end() && it->first<=merged_end) {
            merged_start=std::min(merged_start,it->first);
            merged_end=std::max(merged_end,it->second);
            old_bytes+=it->second-it->first;
            it=intervals.erase(it);
            ++erased;
        }
        if (!erased && unique_interval_count_==max_unique_intervals) {
            counters_.unique_canonical_exact=false;
            ++counters_.unique_tracking_dropped_spans;
            continue;
        }
        intervals.emplace(merged_start,merged_end);
        unique_interval_count_=unique_interval_count_-erased+1;
        const auto increment=merged_end-merged_start-old_bytes;
        if (increment>std::numeric_limits<std::uint64_t>::max()-
                          counters_.unique_canonical_bytes) {
            counters_.unique_canonical_exact=false;
            ++counters_.unique_tracking_dropped_spans;
        } else counters_.unique_canonical_bytes+=increment;
    }
}

MultistackFrontend::MultistackFrontend(
        const std::filesystem::path& top_profile,
        const std::filesystem::path& worker_executable,
        MultistackAdvanceMode advance_mode)
    : MultistackFrontend(load_multistack_profile(top_profile),
                         worker_executable,top_profile,advance_mode)
{}

MultistackFrontend::MultistackFrontend(MultistackProfile top,
        const std::filesystem::path& worker_executable,
        const std::filesystem::path& top_profile,
        MultistackAdvanceMode advance_mode)
    : map_(top.stack_capacity_bytes*top.stack_count,top.stack_count,
           top.modules_per_stack),
      registry_(map_,top.software.max_backing_ranges,
                top.software.max_read_bytes),
      profile_(std::move(top.device)),shared_(top.shared_upstream),
      advance_mode_(advance_mode),
      max_reassembly_bytes_(top.software.host_reassembly_capacity_bytes),
      max_parent_requests_(top.software.max_parent_requests),
      max_child_records_(top.software.max_child_records)
{
    releasable_child_index_=releasable_child_index_requested();
    compact_wave_requested_=compact_wave_requested();
    if (top.stack_count>1024)
        throw std::invalid_argument("worker-process software bound exceeded");
    if (shared_) {
        auto upstream_profile=profile_.link;
        upstream_profile.max_accepted=max_child_records_;
        upstream_=std::make_unique<StreamingLink>(upstream_profile);
    }
    const auto local_stack=selected_local_stack(top.stack_count);
    workers_.reserve(top.stack_count);
    // Preserve local0 initialization in its original first position. Other local
    // selections retain the old synchronous compatibility path.
    const bool defer_hello=deferred_initial_hello_requested() &&
        (!local_stack || *local_stack==0);
    if (!defer_hello) {
        for (std::uint32_t stack=0;stack<top.stack_count;++stack)
            workers_.push_back(std::make_unique<StackWorkerClient>(
                worker_executable,top_profile,stack,true,
                local_stack && *local_stack==stack));
    } else {
        std::uint64_t published=0,validated=0;
        try {
            for (std::uint32_t stack=0;stack<top.stack_count;++stack) {
                const bool local=local_stack && *local_stack==stack;
                // Private constructor remains on this owner thread, never std::async.
                auto client=std::unique_ptr<StackWorkerClient>(new StackWorkerClient(
                    worker_executable,top_profile,stack,true,local,!local));
                if (client->initial_hello_pending()) ++published;
                workers_.push_back(std::move(client));
            }
            for (auto& client:workers_) {
                if (!client->initial_hello_pending()) continue;
                client->finish_initial_hello();++validated;
            }
            std::cerr << "STARTUP_DEFERRED_HELLO_V1 enabled=1 published=" << published
                << " validated=" << validated << " pending_unknown=0 peak_pending="
                << published << '\n';
        } catch (...) {
            // Preserve the first original-order exception. Later pending starts
            // are unknown; poison before clear so STOP cannot read their HELLO.
            std::uint64_t pending_unknown=0;
            for (auto& client:workers_) {
                if (client->initial_hello_pending()) ++pending_unknown;
                client->abandon_initial_hello();
            }
            std::cerr << "STARTUP_DEFERRED_HELLO_V1 enabled=1 published=" << published
                << " validated=" << validated << " pending_unknown=" << pending_unknown
                << " peak_pending=" << published << '\n';
            workers_.clear();
            throw;
        }
    }
    idle_workers_.resize(workers_.size());
    short_qkv_observer::frontend_local = [this] {
        return short_qkv_observer::FrontendLocal{parents_.size(),child_to_parent_.size(),
            retiring_.size(),poisoned_,quiescent_at_now_};
    };
    short_qkv_observer::progress_tail = [this] {
        const auto next=next_event_ns();
        if(next){if(*next<now_)throw std::logic_error("diagnostic tail moved backwards");advance_until(*next);}
    };
}

const StreamingLink& MultistackFrontend::shared_upstream_link() const
{
    if (!upstream_) throw std::logic_error("independent upstream has no shared link");
    return *upstream_;
}

void MultistackFrontend::abort_workers() noexcept
{
    if (poisoned_) return;
    invalidate_event_cache();
    poisoned_=true;
    workers_.clear(); // owned exact PIDs are boundedly stopped/reaped now
    for (auto it=parents_.begin();it!=parents_.end();) {
        auto& parent=it->second;
        if (!parent.admitted || parent.caller_consumed) {
            it=parents_.erase(it);
    parallel_component_accounting_v1::pending(parents_.size(),child_to_parent_.size());
            continue;
        }
        parent.result=DeviceResult::BackendError;
        parent.ready_ns=now_;
        parent.presented=true;
        ++it;
    }
    child_to_parent_.clear();
    parent_order_.clear();
    ready_.clear();
    installed_.clear();
    retiring_.clear();
}

void MultistackFrontend::add_backing(const GlobalBackingRange& range)
{
    invalidate_event_cache();
    if (poisoned_)
        throw std::logic_error("backing registration on failed frontend");
    const auto segments=registry_.add(range);
    try {
        for (const auto& segment:segments) {
            touch_worker(segment.stack_id);
            workers_.at(segment.stack_id)->add_backing(segment);
            installed_[{range.canonical_id,range.generation,range.endpoint_id}]
                .insert({segment.stack_id,segment.module_id});
        }
    } catch (...) {
        abort_workers();
        throw;
    }
}

void MultistackFrontend::add_page_striped_backing(const PageStripeBacking& range)
{
    invalidate_event_cache();
    if (poisoned_)
        throw std::logic_error("striped registration on failed frontend");
    try {
        const auto segments = registry_.add_page_striped(range);
        for (const auto& segment : segments) {
            touch_worker(segment.stack_id);
            workers_.at(segment.stack_id)->add_backing(segment);
            installed_[{range.parent.canonical_id, range.parent.generation,
                        range.parent.endpoint_id}]
                .insert({segment.stack_id, segment.module_id});
        }
    } catch (...) {
        abort_workers();
        throw;
    }
}

void MultistackFrontend::retire_backing_generation(
    std::uint64_t canonical_id,std::uint64_t generation,
    std::uint64_t endpoint_id)
{
    invalidate_event_cache();
    if (poisoned_) throw std::logic_error("retirement on failed frontend");
    registry_.disable_generation(canonical_id,generation,endpoint_id);
    retiring_.insert({canonical_id,generation,endpoint_id});
    try { sweep_retired(); }
    catch (...) { abort_workers(); throw; }
}

void MultistackFrontend::sweep_retired()
{
    for (auto it=retiring_.begin();it!=retiring_.end();) {
        const auto [canonical,generation,endpoint]=*it;
        bool pending=false;
        for (const auto& [id,parent]:parents_) {
            (void)id;
            if (parent.canonical_id!=canonical ||
                parent.generation!=generation ||
                parent.read.endpoint_id!=endpoint) continue;
            for (const auto& child:parent.children)
                if (!child.dispatched &&
                    parent.result==DeviceResult::Ready)
                    pending=true;
        }
        if (pending) { ++it; continue; }
        const auto installed=installed_.find(*it);
        if (installed!=installed_.end()) {
            for (const auto [stack,module]:installed->second) {
                touch_worker(stack);
                workers_.at(stack)->retire_backing_generation(
                    canonical,generation,module,endpoint);
            }
            installed_.erase(installed);
        }
        registry_.retire_generation(canonical,generation,endpoint);
        it=retiring_.erase(it);
    }
}

bool MultistackFrontend::dispatch_child(std::uint64_t child_id,
                                        std::uint64_t arrival)
{
    const auto owner=child_to_parent_.at(child_id);
    auto& parent=parents_.at(owner);
    const auto child_it=std::find_if(parent.children.begin(),parent.children.end(),
        [child_id](const Child& candidate) {
            return candidate.route.child_id==child_id;
        });
    auto& child=*child_it;
    if (child.dispatched || parent.result!=DeviceResult::Ready)
        return child.dispatched;
    if (parent.undispatched_children==0)
        throw std::logic_error("undispatched child count underflow");
    const auto& location=child.route.location;
    const auto q=location.module_local_address/4096;
    DeviceRead read{.request_id=child_id,.arrival_ns=arrival,
        .deadline_ns=parent.read.deadline_ns,
        .local_address=location.module_local_address,
        .axi_id=parent.read.axi_id,.stack_id=location.stack_id,
        .module_id=location.module_id,.endpoint_id=parent.read.endpoint_id,
        .bytes=64,.operation=0,
        .expected_media_page=hbf_media_lpa(q,location.module_id,
                                             profile_.layout)*4096,
        .expected_generation=parent.generation};
    auto& worker=*workers_.at(location.stack_id);
    touch_worker(location.stack_id);
    std::vector<std::uint32_t> counts(map_.modules_per_stack());
    counts.at(location.module_id)=1;
    // All original parent/child/AR/blocked/horizon gates have already passed.
    // External calls use this same owned client; no external-access flag is
    // bypassed or changed for the existing idle/event scheduler.
    if (worker.capacity_proves_full(child_id,arrival,counts)) return false;
    const PacketBackingProof proof{
        child.route.original_canonical_address%map_.module_bytes(),
        child.route.original_bytes,parent.canonical_id,parent.generation};
    if (!shared_ && worker.reserve_submit_single_remote()) {
        if (!worker.reserve_submit_single(child_id,arrival,counts,read,proof))
            return false;
    } else {
        if (!worker.reserve(child_id,arrival,counts))
            return false;
        const auto accepted=worker.try_submit(read,proof);
        if (!accepted) {
            worker.release_reservation(child_id);
            return false;
        }
    }
    child.dispatched=true;
    --parent.undispatched_children;
    if (shared_) {
        upstream_->consume(Direction::Request,child_id,arrival);
        child.upstream_ar_consumed=true;
    }
    refresh_releasable_child(parent,static_cast<std::size_t>(child_it-parent.children.begin()));
    return true;
}

bool MultistackFrontend::dispatch_pending(std::uint64_t horizon)
{
    bool changed=false;
    std::set<std::pair<std::uint32_t,std::uint32_t>> blocked;
    for (const auto id:parent_order_) {
        auto& parent=parents_.at(id);
        if (parent.result!=DeviceResult::Ready ||
            parent.read.arrival_ns>horizon || parent.undispatched_children==0)
            continue;
        for (std::size_t index=0;index<parent.children.size();++index) {
            auto& child=parent.children[index];
            if (child.dispatched || (shared_ && !child.upstream_ar_delivered)) continue;
            const auto key=std::pair{child.route.location.stack_id,child.route.location.module_id};
            if (blocked.contains(key)) continue;
            auto& worker=*workers_.at(key.first);
            std::size_t run=1;
            if (!shared_ && !external_worker_access_ && worker.ordered_admission_remote()) {
                // Same parent, immediate original traversal only. Never scan across skipped actions.
                while (run<ordered_admission_v1::max_count && index+run<parent.children.size()) {
                    const auto& next=parent.children[index+run];
                    if (next.dispatched || next.route.location.stack_id!=key.first ||
                        next.route.location.module_id!=key.second) break;
                    ++run;
                }
            }
            if (run==1) {
                if (dispatch_child(child.route.child_id,horizon)) changed=true;
                else blocked.insert(key);
                continue;
            }
            if (parent.undispatched_children==0)
                throw std::logic_error("undispatched child count underflow");
            ordered_admission_v1::Request request;
            request.horizon=horizon;request.module=key.second;
            request.count=static_cast<std::uint32_t>(run);
            for (std::uint32_t i=0;i<request.count;++i) {
                const auto& current=parent.children[index+i];
                const auto& location=current.route.location;
                const auto q=location.module_local_address/4096;
                auto& record=request.records[i];record.token=current.route.child_id;
                record.read=DeviceRead{.request_id=current.route.child_id,.arrival_ns=horizon,
                    .deadline_ns=parent.read.deadline_ns,.local_address=location.module_local_address,
                    .axi_id=parent.read.axi_id,.stack_id=location.stack_id,.module_id=location.module_id,
                    .endpoint_id=parent.read.endpoint_id,.bytes=64,.operation=0,
                    .expected_media_page=hbf_media_lpa(q,location.module_id,profile_.layout)*4096,
                    .expected_generation=parent.generation};
                record.proof=PacketBackingProof{
                    current.route.original_canonical_address%map_.module_bytes(),
                    current.route.original_bytes,parent.canonical_id,parent.generation};
            }
            // Keep first touch/cached-full eligibility; suffix gains no capacity proof.
            touch_worker(key.first);
            std::vector<std::uint32_t> counts(map_.modules_per_stack());counts.at(key.second)=1;
            if (worker.capacity_proves_full(child.route.child_id,horizon,counts)) {
                blocked.insert(key);continue;
            }
            const auto result=worker.reserve_submit_ordered(request);
            // The client validated the ENTIRE reply, including terminal prefix, before this commit.
            for (std::uint32_t i=0;i<result.completed;++i) {
                if (!result.items[i].accepted) break;
                if (parent.undispatched_children==0)
                    throw std::logic_error("undispatched child count underflow");
                parent.children[index+i].dispatched=true;
                --parent.undispatched_children;changed=true;
                refresh_releasable_child(parent,index+i);
            }
            if (result.stop==ordered_admission_v1::Stop::SemanticError)
                throw std::runtime_error("ordered admission terminal error; current worker item unknown");
            if (result.stop!=ordered_admission_v1::Stop::End) blocked.insert(key);
            // Every remaining item in this same-module run is blocked after first denial.
            index+=run-1;
        }
    }
    return changed;
}
bool MultistackFrontend::try_submit(const GlobalRead& read)
{
    parallel_component_accounting_v1::BackendScope backend_scope;
    invalidate_event_cache();
    if (poisoned_) throw std::logic_error("multistack worker failure");
    if (!read.request_id || !read.bytes || read.operation!=0 ||
        read.axi_id>=(1U<<14) || read.arrival_ns<now_ ||
        read.arrival_ns<last_arrival_ns_ ||
        (read.deadline_ns && read.deadline_ns<read.arrival_ns) ||
        parents_.contains(read.request_id))
        throw std::invalid_argument("invalid or duplicate global read");
    if (parents_.size()>=max_parent_requests_) {
        ++counters_.rejected_backpressure;
        return false;
    }
    const auto routes=registry_.split_read(read.address,read.bytes,
        read.endpoint_id,next_child_id_);
    const auto backing=registry_.resolve_span(read.address,read.bytes,
                                               read.endpoint_id);
    if (routes.size()>max_child_records_ ||
        child_to_parent_.size()>max_child_records_-routes.size()) {
        ++counters_.rejected_backpressure;
        return false;
    }
    if (routes.size()>max_reassembly_bytes_/64)
        throw std::invalid_argument("one read exceeds host reassembly capacity");
    if (reassembly_reserved_bytes_>
            max_reassembly_bytes_-routes.size()*64) {
        ++counters_.rejected_backpressure;
        ++counters_.rejected_reassembly;
        return false;
    }
    if (routes.empty() ||
        next_child_id_>std::numeric_limits<std::uint64_t>::max()-routes.size())
        throw std::overflow_error("global child ID exhausted");
    Parent parent{.read=read,.canonical_id=backing.canonical_id,
                  .generation=backing.generation,
                  .unassembled_children=routes.size(),
                  .undispatched_children=routes.size()};
    parent.children.reserve(routes.size());
    if(!routes.empty()) {
        parent.compact_wave_single_domain=true;
        parent.compact_wave_stack=routes.front().location.stack_id;
        parent.compact_wave_module=routes.front().location.module_id;
    }
    for (const auto& route:routes) {
        parent.children.push_back(Child{.route=route});
        if(route.location.stack_id!=parent.compact_wave_stack ||
           route.location.module_id!=parent.compact_wave_module)
            parent.compact_wave_single_domain=false;
    }
    if (releasable_child_index_)
        parent.releasable_child_words.assign((parent.children.size()+63)/64,0);
    parents_.emplace(read.request_id,std::move(parent));
    parent_order_.push_back(read.request_id);
    for (const auto& route:routes)
        child_to_parent_.emplace(route.child_id,read.request_id);
    try {
        if (shared_) {
            for (auto& child:parents_.at(read.request_id).children) {
                upstream_->enqueue(Direction::Request,child.route.child_id,
                                   read.arrival_ns);
                child.upstream_ar_queued=true;
            }
        }
    } catch (...) {
        abort_workers();
        throw;
    }
    reassembly_reserved_bytes_+=routes.size()*64;
    counters_.reassembly_reserved_bytes=reassembly_reserved_bytes_;
    counters_.peak_reassembly_reserved_bytes=std::max(
        counters_.peak_reassembly_reserved_bytes,reassembly_reserved_bytes_);
    next_child_id_+=routes.size();
    last_arrival_ns_=read.arrival_ns;
    ++counters_.accepted;
    ++counters_.caller_outstanding;
    actual_parent_inflight_peak_ = std::max(actual_parent_inflight_peak_,
                                          counters_.caller_outstanding);
    parallel_component_accounting_v1::pending(parents_.size(),child_to_parent_.size());
    parents_.at(read.request_id).admitted=true;
    counters_.original_bytes+=read.bytes;
    counters_.accepted_axi_payload_bytes+=routes.size()*64;
    record_unique(parents_.at(read.request_id));
    opened_=true;
    if (read.arrival_ns==now_) {
        try { fixed_point(now_); }
        catch (...) { abort_workers(); throw; }
    }
    return true;
}

bool MultistackFrontend::observe_workers(
    std::uint64_t horizon,
    const std::vector<std::vector<WorkerReady>>* first_snapshots)
{
    const auto* flag=std::getenv("HBFSIM_UCIE_ADVANCE_GATHER_V1");
    if (flag && !((flag[0]=='0' || flag[0]=='1') && flag[1]=='\0'))
        throw std::invalid_argument("Advance gather flag must be 0 or 1");
    const bool requested=flag && flag[0]=='1';
    if (requested && first_snapshots)
        ++advance_gather_diagnostics_v1::counts.first_snapshots_bypass;
    bool gather=requested && !first_snapshots && !shared_ &&
        workers_.size()>1 && workers_[0]->advance_gather_local();
    if (gather)
        for (std::uint32_t stack=1;stack<workers_.size();++stack)
            if (!workers_[stack]->advance_gather_remote()) { gather=false; break; }
    std::vector<std::vector<WorkerReady>> snapshots;
    if (gather) {
        // Storage and the exact original active set are ready before any publish.
        snapshots.resize(workers_.size());
        std::vector<unsigned char> active(workers_.size(),1);
        std::uint64_t active_count=0;
        for (std::uint32_t stack=0;stack<workers_.size();++stack) {
            if (idle_mode() && idle_workers_[stack].certified) {
                check_idle_liveness(stack);
                active[stack]=0;
            } else ++active_count;
        }
        advance_gather_diagnostics_v1::record(active_count);
        try {
            {
                parallel_component_accounting_v1::PhaseScope component_publish{
                    parallel_component_accounting_v1::Context::Publish};
                for (std::uint32_t stack=1;stack<workers_.size();++stack)
                    if (active[stack]) workers_[stack]->begin_advance_gather(horizon);
            }
            {
                parallel_component_accounting_v1::PhaseScope component_local{
                    parallel_component_accounting_v1::Context::Local};
                if (active[0]) snapshots[0]=workers_[0]->advance_until(horizon);
            }
            {
                parallel_component_accounting_v1::PhaseScope component_join{
                    parallel_component_accounting_v1::Context::Join};
                for (std::uint32_t stack=1;stack<workers_.size();++stack)
                    if (active[stack]) snapshots[stack]=workers_[stack]->finish_advance_gather();
            }
        } catch (...) {
            // Readers unwind first. Poison every pending kind before frontend abort.
            for (auto& worker:workers_) worker->abandon_advance_gather();
            throw;
        }
        if (idle_mode())
            for (std::uint32_t stack=0;stack<workers_.size();++stack)
                if (active[stack]) idle_workers_[stack].physical_now=horizon;
    }
    bool changed=false;
    for (std::uint32_t stack=0;stack<workers_.size();++stack) {
        std::vector<WorkerReady> ready;
        if (first_snapshots) ready=(*first_snapshots)[stack];
        else if (gather) ready=std::move(snapshots[stack]);
        else if (idle_mode() && idle_workers_[stack].certified)
            check_idle_liveness(stack);
        else {
            ready=workers_[stack]->advance_until(horizon);
            if (idle_mode()) idle_workers_[stack].physical_now=horizon;
        }
        for (const auto& item:ready) {
            const auto owner=child_to_parent_.find(item.request_id);
            if (owner==child_to_parent_.end())
                throw std::logic_error("worker reported unknown child");
            auto& parent=parents_.at(owner->second);
            auto child=std::find_if(parent.children.begin(),parent.children.end(),
                [&](const Child& value) {
                    return value.route.child_id==item.request_id;
                });
            if (child==parent.children.end() ||
                child->route.location.stack_id!=stack ||
                child->route.location.module_id!=item.module_id)
                throw std::logic_error("worker reported wrong child ownership");
            if (!child->reported || !same_ready(child->latest,item)) {
                child->reported=true;
                child->latest=item;
                if (item.result!=DeviceResult::Ready)
                    parent.nonready_report_seen=true;
                changed=true;
                if (!shared_ && item.response_delivered_ns &&
                    item.result==DeviceResult::Ready &&
                    !child->r_received) {
                    child->r_received=true;
                    set_assembled(parent,*child,
                        parent.result==DeviceResult::Ready &&
                        !parent.caller_consumed);
                    counters_.axi_payload_bytes+=64;
                }
            }
            if (shared_ && item.result==DeviceResult::Ready &&
                !child->upstream_r_queued) {
                upstream_->enqueue(Direction::Return,item.request_id,
                                   horizon);
                child->upstream_r_queued=true;
                changed=true;
            }
            // Refresh after all ready/report/receive/queued writes, including repeated ready.
            refresh_releasable_child(parent,static_cast<std::size_t>(child-parent.children.begin()));
        }
    }
    return changed;
}

bool MultistackFrontend::process_upstream(std::uint64_t horizon)
{
    if (!shared_) return false;
    bool changed=false;
    while (const auto next=upstream_->next_event_ns()) {
        if (*next>horizon) break;
        for (const auto& delivery:upstream_->step()) {
            auto& parent=parents_.at(child_to_parent_.at(delivery.request_id));
            auto child=std::find_if(parent.children.begin(),parent.children.end(),
                [&](const Child& value) {
                    return value.route.child_id==delivery.request_id;
                });
            if (delivery.direction==Direction::Request) {
                child->upstream_ar_delivered=true;
                if (parent.result!=DeviceResult::Ready) {
                    upstream_->consume(Direction::Request,delivery.request_id,
                                       horizon);
                    child->upstream_ar_consumed=true;
                }
            } else {
                child->upstream_r_delivered=true;
                child->r_received=true;
                set_assembled(parent,*child,
                    parent.result==DeviceResult::Ready &&
                    !parent.caller_consumed);
                counters_.axi_payload_bytes+=64;
                refresh_releasable_child(parent,static_cast<std::size_t>(child-parent.children.begin()));
            }
            changed=true;
        }
    }
    return changed;
}

void MultistackFrontend::present(std::uint64_t id,std::uint64_t horizon)
{
    auto& parent=parents_.at(id);
    if (!parent.presented) {
        parent.presented=true;
        ready_.push_back(id);
    }
    parent.ready_ns=horizon;
}

void MultistackFrontend::set_assembled(Parent& parent,Child& child,
                                       bool assembled)
{
    if (child.assembled==assembled) return;
    if (assembled) {
        if (parent.unassembled_children==0)
            throw std::logic_error("assembled child count underflow");
        --parent.unassembled_children;
    } else {
        if (parent.unassembled_children>=parent.children.size())
            throw std::logic_error("assembled child count overflow");
        ++parent.unassembled_children;
    }
    child.assembled=assembled;
}

bool MultistackFrontend::update_parent(std::uint64_t id,
                                       std::uint64_t horizon)
{
    auto& parent=parents_.at(id);
    bool changed=false;
    if (!parent.caller_consumed && parent.read.deadline_ns &&
        horizon>=parent.read.deadline_ns &&
        parent.result==DeviceResult::Ready) {
        parent.result=DeviceResult::TimedOut;
        present(id,horizon);
        changed=true;
    }
    if (parent.result==DeviceResult::Ready &&
        !parent.nonready_report_seen) {
        if (parent.unassembled_children==0 && !parent.presented) {
            present(id,horizon);
            changed=true;
        }
        return changed;
    }
    bool all_ready=true;
    for (auto& child:parent.children) {
        if (!child.assembled) all_ready=false;
        if (child.reported && child.latest.result!=DeviceResult::Ready &&
                 parent.result==DeviceResult::Ready) {
            parent.result=child.latest.result;
            present(id,horizon);
            changed=true;
        }
        if (parent.result!=DeviceResult::Ready && child.dispatched &&
            !child.reported && !child.cancel_sent) {
            touch_worker(child.route.location.stack_id);
            workers_[child.route.location.stack_id]->cancel(
                child.route.child_id,horizon);
            child.cancel_sent=true;
            changed=true;
        }
        if (shared_ && child.upstream_ar_delivered &&
                   !child.upstream_ar_consumed &&
                   parent.result!=DeviceResult::Ready) {
            upstream_->consume(Direction::Request,child.route.child_id,horizon);
            child.upstream_ar_consumed=true;
            changed=true;
        }
    }
    if (all_ready && parent.result==DeviceResult::Ready &&
        !parent.presented) {
        present(id,horizon);
        changed=true;
    }
    return changed;
}

bool MultistackFrontend::child_release_eligible(const Child& child) const noexcept
{
    return child.dispatched && child.reported && !child.worker_consumed &&
        (child.latest.result!=DeviceResult::Ready || child.r_received) &&
        (!shared_ || !child.upstream_r_queued || child.upstream_r_delivered);
}

void MultistackFrontend::refresh_releasable_child(Parent& parent,std::size_t index)
{
    if (!releasable_child_index_) return;
    const auto word=index/64;
    const auto mask=std::uint64_t(1)<<(index%64);
    auto& bits=parent.releasable_child_words.at(word);
    const bool before=(bits&mask)!=0;
    const bool after=child_release_eligible(parent.children.at(index));
    if (before==after) return;
    if (after) { bits|=mask; ++parent.eligible_children; }
    else {
        if (!parent.eligible_children) throw std::logic_error("eligible child count underflow");
        bits&=~mask; --parent.eligible_children;
    }
}

#ifdef HBFSIM_RELEASABLE_CHILD_INDEX_TEST_ORACLE
void MultistackFrontend::verify_releasable_child_index(const Parent& parent) const
{
    if (!releasable_child_index_) return;
    ++index_oracle_calls_;
    std::vector<std::size_t> original_sequence,index_sequence;
    for (std::size_t index=0;index<parent.children.size();++index) {
        const auto& child=parent.children[index];
        // Original full-scan guard, independent of the production predicate helper.
        if (!child.dispatched || !child.reported || child.worker_consumed) continue;
        if (child.latest.result==DeviceResult::Ready && !child.r_received) continue;
        if (shared_ && child.upstream_r_queued && !child.upstream_r_delivered) continue;
        original_sequence.push_back(index);
    }
    for (std::size_t word=0;word<parent.releasable_child_words.size();++word) {
        auto bits=parent.releasable_child_words[word];
        while (bits) {
            const auto index=word*64+std::countr_zero(bits);
            if (index>=parent.children.size()) throw std::logic_error("eligible bitmap padding set");
            index_sequence.push_back(index); bits&=bits-1;
        }
    }
    if (original_sequence!=index_sequence || parent.eligible_children!=index_sequence.size())
        throw std::logic_error("eligible original/index sequence or count differs");
}
#endif

bool MultistackFrontend::release_children(std::uint64_t id)
{
    auto& parent=parents_.at(id);
    bool changed=false;
    if (releasable_child_index_) {
#ifdef HBFSIM_RELEASABLE_CHILD_INDEX_TEST_ORACLE
        ++index_release_entries_;
        verify_releasable_child_index(parent); // test-only, never compiled into performance
#endif
        if (!parent.eligible_children) return false;
        bool compact_enabled=false;
        if(!shared_ && !external_worker_access_)
            for(const auto& worker:workers_) if(worker->compact_consume_remote()) { compact_enabled=true; break; }
        if(compact_enabled) {
            std::array<std::size_t,compact_consume_v1::max_count> selected{};
            compact_consume_v1::Request request;
            std::uint32_t selected_stack{};
            const auto flush=[&] {
                if(!request.count) return;
                auto& worker=*workers_.at(selected_stack);
                // Preserve each original touch in ordinal order before the synchronous command.
                for(std::uint32_t i=0;i<request.count;++i) touch_worker(selected_stack);
                std::uint32_t confirmed{}; bool terminal=false;
                if(request.count==1 || !worker.compact_consume_remote()) {
                    // Normally one; no batch or invented fallback after any send.
                    for(std::uint32_t i=0;i<request.count;++i) {
                        (void)worker.consume_completion(request.ids[i],now_);
                        auto& child=parent.children.at(selected[i]); child.worker_consumed=true;
                        refresh_releasable_child(parent,selected[i]); changed=true;
                    }
                    request.count=0; return;
                }
                const auto ack=worker.consume_compact_ordered(request); // whole ACK validated inside client
                confirmed=ack.confirmed; terminal=ack.stop==compact_consume_v1::Stop::SemanticError;
                for(std::uint32_t i=0;i<confirmed;++i) {
                    auto& child=parent.children.at(selected[i]); child.worker_consumed=true;
                    refresh_releasable_child(parent,selected[i]); changed=true;
                }
                request.count=0;
                if(terminal) throw std::runtime_error("compact Consume semantic failure after confirmed prefix; current UNKNOWN");
            };
            for(std::size_t word=0;word<parent.releasable_child_words.size();++word) {
                auto bits=parent.releasable_child_words[word];
                while(bits) {
                    const auto index=word*64+std::countr_zero(bits); bits&=bits-1;
                    auto& child=parent.children.at(index);
                    if(!child_release_eligible(child)) continue; // Original guards at the original release phase.
                    const auto stack=child.route.location.stack_id, module=child.route.location.module_id;
                    const bool compact=workers_.at(stack)->compact_consume_remote();
                    if(request.count && (stack!=selected_stack || module!=request.module || !compact || request.count==compact_consume_v1::max_count)) flush();
                    if(!compact) {
                        touch_worker(stack); (void)workers_.at(stack)->consume_completion(child.route.child_id,now_);
                        child.worker_consumed=true; refresh_releasable_child(parent,index); changed=true; continue;
                    }
                    if(!request.count) { selected_stack=stack; request.module=module; request.horizon=now_; }
                    selected[request.count]=index; request.ids[request.count]=child.route.child_id; ++request.count;
                }
            }
            flush(); return changed;
        }
        for (std::size_t word=0;word<parent.releasable_child_words.size();++word) {
            auto bits=parent.releasable_child_words[word];
            while (bits) {
                const std::size_t index=word*64+std::countr_zero(bits);
                bits&=bits-1;
                auto& child=parent.children.at(index);
                if (!child.dispatched || !child.reported || child.worker_consumed)
                    continue;
                if (child.latest.result==DeviceResult::Ready && !child.r_received)
                    continue;
                if (shared_ && child.upstream_r_queued &&
                    !child.upstream_r_delivered)
                    continue;
                touch_worker(child.route.location.stack_id);
                (void)workers_[child.route.location.stack_id]->consume_completion(
                    child.route.child_id,now_);
                child.worker_consumed=true;
                changed=true;
                if (shared_ && child.upstream_r_delivered &&
                    !child.upstream_r_consumed) {
                    upstream_->consume(Direction::Return,child.route.child_id,now_);
                    child.upstream_r_consumed=true;
                }
                refresh_releasable_child(parent,index);
            }
        }
        return changed;
    }
#ifdef HBFSIM_RELEASABLE_CHILD_INDEX_TEST_ORACLE
    ++legacy_release_entries_;
#endif
    for (auto& child:parent.children) {
        if (!child.dispatched || !child.reported || child.worker_consumed)
            continue;
        if (child.latest.result==DeviceResult::Ready && !child.r_received)
            continue;
        if (shared_ && child.upstream_r_queued &&
            !child.upstream_r_delivered)
            continue;
        touch_worker(child.route.location.stack_id);
        (void)workers_[child.route.location.stack_id]->consume_completion(
            child.route.child_id,now_);
        child.worker_consumed=true;
        changed=true;
        if (shared_ && child.upstream_r_delivered &&
            !child.upstream_r_consumed) {
            upstream_->consume(Direction::Return,child.route.child_id,now_);
            child.upstream_r_consumed=true;
        }
    }
    return changed;
}

std::optional<MultistackFrontend::CompactWaveHead>
MultistackFrontend::prepare_compact_wave_head(std::uint64_t id)
{
    if(!compact_wave_requested_ || !releasable_child_index_ || shared_ ||
       external_worker_access_) return std::nullopt;
    const auto it=parents_.find(id);
    if(it==parents_.end()) return std::nullopt;
    const auto& parent=it->second;
    if(!parent.compact_wave_single_domain || parent.eligible_children<2)
        return std::nullopt;
    const auto stack=parent.compact_wave_stack;
    if(stack>=workers_.size() || stack>=4 ||
       !workers_[stack]->compact_consume_begin_available()) return std::nullopt;
    // In other advance modes touch_worker is exactly a no-op. In deferred mode
    // even the certified=false store must already be idempotent; no catch-up RPC.
    if(idle_mode() && (idle_workers_[stack].physical_now!=now_ ||
                      idle_workers_[stack].certified)) return std::nullopt;
    CompactWaveHead head;
    head.parent_id=id; head.stack=stack;
    head.request.horizon=now_; head.request.module=parent.compact_wave_module;
    for(std::size_t word=0;word<parent.releasable_child_words.size();++word) {
        auto bits=parent.releasable_child_words[word];
        while(bits) {
            const auto index=word*64+std::countr_zero(bits); bits&=bits-1;
            const auto& child=parent.children.at(index);
            if(!child_release_eligible(child)) continue; // original eligibility, no observation
            if(child.route.location.stack_id!=stack ||
               child.route.location.module_id!=head.request.module)
                throw std::logic_error("immutable compact parent domain certificate changed");
            head.selected[head.request.count]=index;
            head.request.ids[head.request.count]=child.route.child_id;
            if(++head.request.count==compact_consume_v1::max_count) return head;
        }
    }
    if(head.request.count<2) return std::nullopt;
    return head;
}

bool MultistackFrontend::commit_compact_wave_head(const CompactWaveHead& head,
    const compact_consume_v1::Reply& reply)
{
    auto& parent=parents_.at(head.parent_id);
    // Client already validated complete sequence/horizon/module/count/ACK.
    for(std::uint32_t i=0;i<reply.confirmed;++i) {
        auto& child=parent.children.at(head.selected[i]);
        if(child.worker_consumed || child.route.child_id!=head.request.ids[i] ||
           !child_release_eligible(child))
            throw std::logic_error("compact wave saved original-index commit changed");
    }
    for(std::uint32_t i=0;i<reply.confirmed;++i) {
        auto& child=parent.children.at(head.selected[i]);
        child.worker_consumed=true;
        refresh_releasable_child(parent,head.selected[i]);
    }
    compact_wave_counters_.confirmed+=reply.confirmed;
    if(reply.stop==compact_consume_v1::Stop::SemanticError)
        throw std::runtime_error("compact wave semantic failure after validated prefix; later outcomes UNKNOWN");
    return reply.confirmed!=0;
}

void MultistackFrontend::release_fixed_point_parents(
    const std::vector<std::uint64_t>& ids,bool& changed,bool allow_compact_wave)
{
    if(!allow_compact_wave || !compact_wave_requested_ || !releasable_child_index_ ||
       shared_ || external_worker_access_) {
        for(const auto id:ids) { changed|=release_children(id); maybe_erase(id); }
        return;
    }
    for(std::size_t position=0;position<ids.size();) {
        std::array<CompactWaveHead,3> heads;
        std::size_t width{};
        for(std::size_t next=position;next<ids.size() && width<heads.size();++next) {
            const auto head=prepare_compact_wave_head(ids[next]);
            if(!head) break; // actual consecutive traversal; never cross a barrier
            bool repeated=false;
            for(std::size_t i=0;i<width;++i) repeated|=heads[i].stack==head->stack;
            if(repeated) break;
            heads[width++]=*head;
        }
        if(width<2) {
            changed|=release_children(ids[position]); maybe_erase(ids[position]);
            ++position; continue; // width1 retains original synchronous path
        }
        ++compact_wave_counters_.attempts;
        std::size_t published{};
        try {
            for(std::size_t i=0;i<width;++i) {
                const auto& head=heads[i];
                // Every touch already proved no-op, with no cross-parent side effect.
                for(std::uint32_t child=0;child<head.request.count;++child) touch_worker(head.stack);
                workers_[head.stack]->begin_compact_consume(head.request);
                ++published; ++compact_wave_counters_.published;
            }
            ++compact_wave_counters_.published_width[published];
            for(std::size_t i=0;i<width;++i) {
                const auto& head=heads[i];
                const auto reply=workers_[head.stack]->finish_compact_consume();
                ++compact_wave_counters_.validated;
                changed|=commit_compact_wave_head(head,reply);
                // Finish all remaining original A groups and its physical query
                // exactly before any B host commit; entire domain is A worker only.
                changed|=release_children(head.parent_id);
                maybe_erase(head.parent_id);
            }
        } catch(...) {
            // Record only known completed sends; an in-flight failed send is UNKNOWN.
            if(published<width && published)
                ++compact_wave_counters_.published_width[published];
            for(std::size_t i=0;i<width;++i) {
                auto& worker=*workers_[heads[i].stack];
                if(worker.compact_consume_pending()) {
                    ++compact_wave_counters_.unknown_pending;
                    worker.abandon_compact_consume();
                }
            }
            throw; // original outer abort preserves already committed public result
        }
        position+=width;
    }
}

bool MultistackFrontend::all_physical_done(const Parent& parent)
{
    for (const auto& child:parent.children) {
        if (shared_ && child.upstream_ar_queued &&
            (!child.upstream_ar_consumed ||
             upstream_->message_active(Direction::Request,
                                       child.route.child_id))) return false;
        if (shared_ && child.upstream_r_queued &&
            (!child.upstream_r_consumed ||
             upstream_->message_active(Direction::Return,
                                       child.route.child_id))) return false;
        if (child.dispatched) {
            if (!child.worker_consumed) return false;
            touch_worker(child.route.location.stack_id);
            if (!external_worker_access_ &&
                workers_[child.route.location.stack_id]->capacity_proves_inactive(
                    child.route.location.module_id,now_))
                continue;
            if (workers_[child.route.location.stack_id]->is_active(
                    child.route.child_id)) return false;
        }
    }
    return true;
}

void MultistackFrontend::maybe_erase(std::uint64_t id)
{
    const auto it=parents_.find(id);
    if (it==parents_.end() || !it->second.caller_consumed ||
        !all_physical_done(it->second))
        return;
    for (const auto& child:it->second.children)
        child_to_parent_.erase(child.route.child_id);
    parent_order_.erase(std::find(parent_order_.begin(),parent_order_.end(),id));
    parents_.erase(it);
    parallel_component_accounting_v1::pending(parents_.size(),child_to_parent_.size());
}

void MultistackFrontend::fixed_point(
    std::uint64_t horizon,
    const std::vector<std::vector<WorkerReady>>* first_snapshots,
    bool allow_compact_wave)
{
    parallel_component_accounting_v1::FixedPointScope component_fixed_point;
    for (std::size_t rounds=0;;++rounds) {
        if (rounds>100000)
            throw std::logic_error("same-horizon worker feedback did not quiesce");
        bool changed=observe_workers(horizon,rounds==0 ? first_snapshots : nullptr);
        changed|=process_upstream(horizon);
        std::vector<std::uint64_t> ids;
        ids.reserve(parents_.size());
        for (const auto& [id,parent]:parents_) {
            (void)parent;
            ids.push_back(id);
        }
        for (const auto id:ids) changed|=update_parent(id,horizon);
        changed|=dispatch_pending(horizon);
        const auto retiring_before=retiring_.size();
        sweep_retired();
        changed|=retiring_.size()!=retiring_before;
        release_fixed_point_parents(ids,changed,allow_compact_wave);
        if (shared_ && upstream_->next_event_ns() &&
            *upstream_->next_event_ns()<=horizon)
            changed=true;
        if (!changed) break;
    }
    parallel_component_accounting_v1::pending(parents_.size(),child_to_parent_.size());
    certify_idle_workers();
    quiescent_at_now_=true;
}

std::optional<std::uint64_t> MultistackFrontend::next_event_ns()
{
    parallel_component_accounting_v1::BackendScope backend_scope;
    if (poisoned_) throw std::logic_error("multistack worker failure");
    try {
        opened_=true;
        const bool cache_enabled=
            (advance_mode_==MultistackAdvanceMode::EventDriven ||
             advance_mode_==MultistackAdvanceMode::EventDrivenIdleDeferred ||
             advance_mode_==MultistackAdvanceMode::EventDrivenCachedSeparate) &&
            !external_worker_access_;
        if (cache_enabled && cached_next_event_)
            return *cached_next_event_;
        for (unsigned attempts=0;attempts<3;++attempts) {
            if (!cache_enabled || !quiescent_at_now_)
                fixed_point(now_);
            if (parents_.empty() &&
                advance_mode_!=MultistackAdvanceMode::NanosecondReference) {
                if (cache_enabled)
                    cached_next_event_=std::optional<std::uint64_t>{};
                return std::nullopt;
            }
            std::optional<std::uint64_t> next;
            bool supported=advance_mode_!=MultistackAdvanceMode::NanosecondReference;
            if (supported) {
                const auto consider=[&](std::uint64_t event) {
                    if (!next || event<*next) next=event;
                };
                for (std::uint32_t stack=0;stack<workers_.size();++stack) {
                    if (idle_mode() && idle_workers_[stack].certified) {
                        check_idle_liveness(stack);
                        continue;
                    }
                    const auto peek=workers_[stack]->next_event(now_);
                    if (!peek.supported) { supported=false; break; }
                    if (peek.next_ns) consider(*peek.next_ns);
                }
                if (shared_)
                    if (const auto event=upstream_->next_event_ns())
                        consider(*event);
                for (const auto& [id,parent]:parents_) {
                    (void)id;
                    if (!parent.caller_consumed &&
                        parent.result==DeviceResult::Ready) {
                        // A future parent is accepted before any child has a
                        // worker/link event. Its arrival is an input boundary.
                        if (parent.read.arrival_ns>now_)
                            consider(parent.read.arrival_ns);
                        if (parent.read.deadline_ns)
                            consider(parent.read.deadline_ns);
                    }
                }
            }
            if (!supported) {
                if (now_==std::numeric_limits<std::uint64_t>::max())
                    throw std::overflow_error("reference clock exhausted");
                if (cache_enabled) cached_next_event_=now_+1;
                return now_+1;
            }
            if (!next || *next>now_) {
                if (cache_enabled) cached_next_event_=next;
                return next;
            }
            quiescent_at_now_=false;
        }
        throw std::logic_error("event peek did not quiesce at open horizon");
    } catch (...) {
        abort_workers();
        throw;
    }
}

void MultistackFrontend::advance_until(std::uint64_t horizon)
{
    parallel_component_accounting_v1::BackendScope backend_scope;
    if (poisoned_) throw std::logic_error("multistack worker failure");
    if (horizon<now_)
        throw std::invalid_argument("global time cannot move backwards");
    try {
        opened_=true;
        if (horizon==now_) {
            if ((advance_mode_!=MultistackAdvanceMode::EventDriven &&
                 advance_mode_!=MultistackAdvanceMode::EventDrivenIdleDeferred &&
                 advance_mode_!=MultistackAdvanceMode::EventDrivenCachedSeparate) ||
                external_worker_access_ || !quiescent_at_now_)
                fixed_point(now_);
            return;
        }
        if ((advance_mode_!=MultistackAdvanceMode::EventDriven &&
             advance_mode_!=MultistackAdvanceMode::EventDrivenIdleDeferred &&
             advance_mode_!=MultistackAdvanceMode::EventDrivenCachedSeparate) ||
            external_worker_access_)
            fixed_point(now_);
        const auto* gather_flag=std::getenv("HBFSIM_UCIE_CLOSE_ADVANCE_GATHER_V1");
        bool gather=gather_flag && std::string(gather_flag)=="1" && !shared_ &&
            workers_.size()>1 && workers_[0]->close_gather_local();
        if (gather)
            for (std::uint32_t stack=1;stack<workers_.size();++stack)
                if (!workers_[stack]->close_gather_remote()) { gather=false; break; }
        while (now_<horizon) {
            const auto event=next_event_ns();
            const auto next=event ? std::min(horizon,*event) : horizon;
            invalidate_event_cache();
            if (advance_mode_==MultistackAdvanceMode::EventDriven ||
                advance_mode_==MultistackAdvanceMode::EventDrivenIdleDeferred) {
                std::vector<std::vector<WorkerReady>> snapshots;
                if (gather) {
                    // Allocate and certify the exact original active set before publication.
                    snapshots.resize(workers_.size());
                    std::vector<unsigned char> active(workers_.size(),1);
                    for (std::uint32_t stack=0;stack<workers_.size();++stack) {
                        if (idle_mode() && idle_workers_[stack].certified) {
                            check_idle_liveness(stack);
                            active[stack]=0; // retain deferred physical clock, no dummy RPC
                        }
                    }
                    try {
                        {
                            parallel_component_accounting_v1::PhaseScope component_publish{
                                parallel_component_accounting_v1::Context::Publish};
                            for (std::uint32_t stack=1;stack<workers_.size();++stack)
                                if (active[stack]) workers_[stack]->begin_close_gather(now_,next);
                        }
                        {
                            parallel_component_accounting_v1::PhaseScope component_local{
                                parallel_component_accounting_v1::Context::Local};
                            if (active[0]) snapshots[0]=workers_[0]->close_and_advance(now_,next);
                        }
                        {
                            parallel_component_accounting_v1::PhaseScope component_join{
                                parallel_component_accounting_v1::Context::Join};
                            for (std::uint32_t stack=1;stack<workers_.size();++stack)
                                if (active[stack]) snapshots[stack]=workers_[stack]->finish_close_gather();
                        }
                    } catch (...) {
                        // Every function-local Reader has unwound before clearing its Impl.
                        // Poison unread responses first; abort_workers must not mix STOP/reply.
                        for (auto& worker:workers_) worker->abandon_close_gather();
                        throw;
                    }
                    // No scheduler feedback, idle clock or global horizon commits early.
                    if (idle_mode())
                        for (std::uint32_t stack=0;stack<workers_.size();++stack)
                            if (active[stack]) idle_workers_[stack].physical_now=next;
                } else {
                    snapshots.reserve(workers_.size());
                    for (std::uint32_t stack=0;stack<workers_.size();++stack) {
                        if (idle_mode() && idle_workers_[stack].certified) {
                            check_idle_liveness(stack);
                            snapshots.emplace_back();
                        } else {
                            snapshots.push_back(workers_[stack]->close_and_advance(now_,next));
                            if (idle_mode()) idle_workers_[stack].physical_now=next;
                        }
                    }
                }
                last_closed_global_=now_;
                now_=next;
                fixed_point(now_,&snapshots);
            } else {
                for (auto& worker:workers_) worker->close_horizon(now_);
                now_=next;
                fixed_point(now_);
            }
        }
    } catch (...) {
        abort_workers();
        throw;
    }
}

std::vector<std::uint64_t> MultistackFrontend::ready_ids() const
{
    if (!poisoned_) return {ready_.begin(),ready_.end()};
    std::vector<std::uint64_t> result;
    for (const auto& [id,parent]:parents_)
        if (parent.admitted && !parent.caller_consumed)
            result.push_back(id);
    return result;
}

std::optional<GlobalCompletion> MultistackFrontend::peek_completion(
    std::uint64_t id) const
{
    const auto it=parents_.find(id);
    if (it==parents_.end() || !it->second.presented ||
        it->second.caller_consumed)
        return std::nullopt;
    const auto& parent=it->second;
    std::uint32_t delivered=0;
    for (const auto& child:parent.children)
        if (child.r_received)
            delivered+=64;
    return GlobalCompletion{parent.read,parent.result,parent.ready_ns,0,
        parent.read.bytes,delivered,
        static_cast<std::uint32_t>(parent.children.size())};
}

GlobalCompletion MultistackFrontend::consume_completion(std::uint64_t id)
{
    parallel_component_accounting_v1::BackendScope backend_scope;
    invalidate_event_cache();
    if (poisoned_) {
        const auto value=peek_completion(id);
        if (!value) throw std::invalid_argument("global completion is not ready");
        const auto bytes=parents_.at(id).children.size()*64;
        reassembly_reserved_bytes_-=bytes;
        counters_.reassembly_reserved_bytes=reassembly_reserved_bytes_;
        --counters_.caller_outstanding;
        ++counters_.failed;
        auto result=*value;
        result.consumed_ns=now_;
        parents_.erase(id);
        return result;
    }
    const auto value=peek_completion(id);
    if (!value) throw std::invalid_argument("global completion is not ready");
    auto it=std::find(ready_.begin(),ready_.end(),id);
    if (it==ready_.end()) throw std::logic_error("ready parent missing queue entry");
    ready_.erase(it);
    auto& parent=parents_.at(id);
    parent.caller_consumed=true;
    reassembly_reserved_bytes_-=parent.children.size()*64;
    counters_.reassembly_reserved_bytes=reassembly_reserved_bytes_;
    --counters_.caller_outstanding;
    if (parent.result==DeviceResult::Ready) ++counters_.succeeded;
    else if (parent.result==DeviceResult::Cancelled) ++counters_.cancelled;
    else ++counters_.failed;
    auto result=*value;
    result.consumed_ns=now_;
    try {
        (void)release_children(id);
        maybe_erase(id);
        fixed_point(now_,nullptr,false); // public consume remains single-parent synchronous
    } catch (...) {
        // The caller result and host bytes were already committed. An IPC
        // cleanup failure poisons the remaining session, but cannot retract
        // this one stable completion or leave the caller with no result.
        abort_workers();
        return result;
    }
    return result;
}

void MultistackFrontend::cancel(std::uint64_t id)
{
    invalidate_event_cache();
    if (poisoned_) throw std::logic_error("multistack worker failure");
    auto it=parents_.find(id);
    if (it==parents_.end() || it->second.caller_consumed)
        throw std::invalid_argument("unknown or consumed global read");
    if (it->second.result!=DeviceResult::Ready) return;
    it->second.result=DeviceResult::Cancelled;
    present(id,now_);
    try { fixed_point(now_); }
    catch (...) { abort_workers(); throw; }
}
} // namespace hbfsim::ucie
