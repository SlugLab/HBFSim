#include <hbfsim/ucie/stack_frontend.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hbfsim::ucie {
namespace {
class RealStackMediaPort final : public DeviceMediaPort {
public:
    RealStackMediaPort(const Profile& profile,const HbfBankLayout& layout)
        : engine_(profile,layout) {}
    void submit(const HbfRequest& request) override { engine_.submit(request); }
    std::optional<HbfCompletion> run_until(std::uint64_t horizon) override
    { return engine_.run_next_completion_until(horizon); }
    std::uint64_t current_time_ns() const override
    { return engine_.current_time_ns(); }
    EventPeek next_event() override
    { return {true,engine_.next_event_ns()}; }
    HbfReadBank inspect(std::uint64_t page) override
    { return engine_.inspect_read_bank(page); }
    void expect(std::uint64_t token,std::uint64_t page,
                const HbfReadBank& bank) override
    { engine_.expect_native_read(token,page,bank); }
    NativeReadProof finish(std::uint64_t token) override
    { return engine_.finish_native_read(token); }
private:
    MqsimOnlineEngine engine_;
};

std::size_t validate_stack(const std::vector<LinkProfile>& modules,
                           const Profile& media,const HbfBankLayout& layout)
{
    (void)hbf_mqsim_geometry(media,layout);
    if (modules.size()!=layout.host_channels ||
        media.capacity_bytes%layout.host_channels)
        throw std::invalid_argument("stack requires one link per host channel");
    const auto per_module=media.capacity_bytes/layout.host_channels;
    if (per_module==0 || per_module>(1ULL<<36))
        throw std::invalid_argument("module exceeds 36-bit AXI address capacity");
    std::size_t total=0;
    for (const auto& link:modules) {
        validate_link_profile(link);
        if (link.axi_ports!=1 || link.local_capacity_bytes!=per_module)
            throw std::invalid_argument("stack module link geometry mismatch");
        if (total>std::numeric_limits<std::size_t>::max()-link.max_accepted)
            throw std::overflow_error("stack accepted limit overflow");
        total+=link.max_accepted;
    }
    return total;
}
bool same_bank(const HbfReadBank& a,const HbfReadBank& b)
{
    return a.native_channel==b.native_channel &&
           a.native_chip==b.native_chip && a.native_die==b.native_die &&
           a.native_plane==b.native_plane;
}
}

StackFrontend::Module::Module(LinkProfile value)
    : profile(value),registry(value.local_capacity_bytes),link(value) {}

StackFrontend::StackFrontend(std::vector<LinkProfile> profiles,
        const Profile& stack,HbfBankLayout layout,std::uint32_t stack_id,
        bool coalescing,bool buffer_cache)
    : guard_(std::make_unique<UcieEngineGuard>()),
      media_([&]() -> std::unique_ptr<DeviceMediaPort> {
          (void)validate_stack(profiles,stack,layout);
          return std::make_unique<RealStackMediaPort>(stack,layout);
      }()),
      stack_profile_(stack),layout_(layout),stack_id_(stack_id),
      coalescer_(validate_stack(profiles,stack,layout),
                 validate_stack(profiles,stack,layout),buffer_cache,
                 layout.host_channels,
                 layout.core_dies_per_channel*layout.banks_per_die,coalescing)
{
    modules_.reserve(profiles.size());
    for (auto& value:profiles) modules_.emplace_back(std::move(value));
}

StackFrontend::StackFrontend(std::vector<LinkProfile> profiles,
        const Profile& stack,HbfBankLayout layout,std::uint32_t stack_id,
        std::unique_ptr<DeviceMediaPort> test_media,
        bool coalescing,bool buffer_cache)
    : media_(std::move(test_media)),stack_profile_(stack),layout_(layout),
      stack_id_(stack_id),
      coalescer_(validate_stack(profiles,stack,layout),
                 validate_stack(profiles,stack,layout),buffer_cache,
                 layout.host_channels,
                 layout.core_dies_per_channel*layout.banks_per_die,coalescing)
{
    if (!media_) throw std::invalid_argument("missing deterministic test media");
    modules_.reserve(profiles.size());
    for (auto& value:profiles) modules_.emplace_back(std::move(value));
}
StackFrontend::~StackFrontend()=default;

const StreamingLink& StackFrontend::link(std::uint32_t module) const
{
    return modules_.at(module).link;
}

std::pair<std::uint32_t,std::uint32_t> StackFrontend::module_capacity(
    std::uint32_t id) const
{
    const auto& module=modules_.at(id);
    if (module.outstanding>module.profile.max_accepted)
        throw std::logic_error("module capacity invariant");
    return {static_cast<std::uint32_t>(module.outstanding),
            module.profile.max_accepted};
}

bool StackFrontend::can_reserve(
    const std::vector<std::uint32_t>& per_module) const
{
    if (per_module.size()!=modules_.size())
        throw std::invalid_argument("reservation module count mismatch");
    for (std::size_t i=0;i<modules_.size();++i) {
        const auto& module=modules_[i];
        if (module.outstanding>module.profile.max_accepted ||
            per_module[i]>module.profile.max_accepted-module.outstanding)
            return false;
    }
    return true;
}

void StackFrontend::add_backing(const BackingRange& range)
{
    if (range.stack_id!=stack_id_ || range.module_id>=modules_.size())
        throw std::invalid_argument("backing belongs to another stack/module");
    modules_[range.module_id].registry.add(range);
}

void StackFrontend::retire_backing_generation(std::uint64_t canonical_id,
        std::uint64_t generation,std::uint32_t module,std::uint64_t endpoint)
{
    modules_.at(module).registry.retire_generation(canonical_id,generation,
        stack_id_,module,endpoint);
}

bool StackFrontend::try_submit(const DeviceRead& read)
{ return try_submit_impl(read,std::nullopt); }

bool StackFrontend::try_submit_packet(const DeviceRead& read,
                                      const PacketBackingProof& proof)
{ return try_submit_impl(read,proof); }

bool StackFrontend::try_submit_impl(
    const DeviceRead& read,const std::optional<PacketBackingProof>& proof)
{
    if (read.module_id>=modules_.size())
        throw std::invalid_argument("invalid host module");
    auto& module=modules_[read.module_id];
    const auto now=current_time_ns();
    if (!read.request_id || read.bytes!=64 || read.operation!=0 ||
        read.axi_id>=(1U<<14) || read.stack_id!=stack_id_ ||
        read.local_address%64 || read.arrival_ns<now ||
        read.arrival_ns<module.last_arrival_ns ||
        (read.deadline_ns && read.deadline_ns<read.arrival_ns))
        throw std::invalid_argument("unsupported or invalid stack regular read");
    if (active_.contains(read.request_id))
        throw std::invalid_argument("duplicate active child request ID");
    if (read.local_address>
        std::numeric_limits<std::uint64_t>::max()-64)
        throw std::invalid_argument("AXI packet address overflows");
    if (proof && (!proof->original_bytes ||
        proof->original_local_address<read.local_address ||
        proof->original_local_address>=read.local_address+64 ||
        proof->original_bytes>
            read.local_address+64-proof->original_local_address))
        throw std::invalid_argument("invalid original-byte packet proof");
    const auto resolved=module.registry.resolve_span(
        proof?proof->original_local_address:read.local_address,
        proof?proof->original_bytes:64,
        stack_id_,read.module_id,read.endpoint_id);
    if (!resolved || (proof &&
        (resolved->canonical_id!=proof->canonical_id ||
         resolved->generation!=proof->generation ||
         resolved->canonical_offset!=proof->original_local_address)) ||
        (!proof && resolved->canonical_offset%64) ||
        (proof && resolved->canonical_offset/4096!=
                  read.local_address/4096))
        throw std::invalid_argument("unregistered stack child span");
    const auto q=(proof?read.local_address:
                  resolved->canonical_offset)/4096;
    const auto lpa=hbf_media_lpa(q,read.module_id,layout_);
    if (lpa>=stack_profile_.capacity_bytes/4096 ||
        lpa>std::numeric_limits<std::uint64_t>::max()/4096)
        throw std::out_of_range("stack media page exceeds capacity");
    const auto media_page=lpa*4096;
    if ((read.expected_media_page && *read.expected_media_page!=media_page) ||
        (read.expected_generation &&
         *read.expected_generation!=resolved->generation))
        throw std::invalid_argument("forged stack media page or generation");
    if (module.outstanding>=module.profile.max_accepted) {
        ++counters_.rejected_backpressure;
        return false;
    }
    module.link.enqueue(Direction::Request,read.request_id,read.arrival_ns);
    State state{.read=read,.backing=*resolved,.media_page=media_page};
    constexpr std::size_t record_limit=4096;
    if (request_records_.size()<record_limit) {
        state.record_index=request_records_.size();
        request_records_.push_back(DeviceRequestRecord{
            .request_id=read.request_id,.transport_id=read.request_id,
            .canonical_id=resolved->canonical_id,
            .generation=resolved->generation,.stack_id=stack_id_,
            .module_id=read.module_id,.endpoint_id=read.endpoint_id,
            .canonical_page=q,.media_lpa=lpa,
            .bank=hbf_cold_read_bank(lpa,layout_),
            .accepted_ns=read.arrival_ns});
    } else ++counters_.request_records_dropped;
    active_.emplace(read.request_id,std::move(state));
    module.same_id[read.axi_id].push_back(read.request_id);
    if (read.deadline_ns) deadlines_.emplace(read.deadline_ns,read.request_id);
    module.last_arrival_ns=read.arrival_ns;
    ++module.outstanding;
    ++counters_.accepted;
    ++counters_.caller_outstanding;
    counters_.peak_transport_outstanding=std::max<std::uint64_t>(
        counters_.peak_transport_outstanding,active_.size());
    counters_.application_bytes+=64;
    return true;
}

void StackFrontend::start_media()
{
    for (const auto& start:coalescer_.take_submittable()) {
        const auto module=start.key.module_id;
        const auto lpa=hbf_media_lpa(start.key.canonical_page,module,layout_);
        const auto page=lpa*4096;
        const auto observed=media_->inspect(page);
        const auto predicted=hbf_cold_read_bank(lpa,layout_);
        if (!same_bank(observed,predicted) ||
            observed.native_channel!=start.key.native_channel ||
            observed.native_chip!=start.key.native_chip)
            throw std::logic_error("stack FTL bank query mismatch");
        media_->expect(start.token,page,observed);
        expected_banks_.emplace(start.token,observed);
        group_module_.emplace(start.token,module);
        media_->submit(HbfRequest{.request_id=start.token,.sequence=start.token,
            .arrival_ns=current_time_ns(),.logical_address=page,.bytes=4096,
            .operation=static_cast<std::uint32_t>(RequestOperation::Read)});
        ++counters_.media_submits;
        counters_.media_submit_bytes+=4096;
    }
}

void StackFrontend::handle_media(HbfCompletion completion)
{
    const auto proof=media_->finish(completion.request_id);
    const auto bank=expected_banks_.at(completion.request_id);
    const auto module=group_module_.at(completion.request_id);
    expected_banks_.erase(completion.request_id);
    group_module_.erase(completion.request_id);
    constexpr std::size_t proof_limit=4096;
    if (native_proofs_.size()<proof_limit)
        native_proofs_.push_back({stack_id_,module,completion.request_id,
                                  bank,proof});
    else ++counters_.native_proofs_dropped;
    counters_.native_commands+=proof.issued_commands;
    bool success=completion.status==static_cast<std::uint32_t>(RequestStatus::Ready);
    if (success && (proof.issued_commands!=1 || !proof.bank_match ||
                    !proof.logical_page_match || proof.observed_media_bytes!=4096 ||
                    proof.observer_failed || proof.phase_events[1]!=1 ||
                    proof.phase_events[2]!=1 || proof.phase_events[3]==0 ||
                    proof.phase_events[4]==0))
        throw std::logic_error("stack native NAND proof failed");
    (void)coalescer_.complete(completion.request_id,success);
    for (auto& [id,state]:active_) {
        if (!state.group || *state.group!=completion.request_id) continue;
        state.media_done=true;
        state.media_ready_ns=completion.modeled_completion_ns;
        if (state.record_index)
            request_records_.at(*state.record_index).media_ready_ns=
                completion.modeled_completion_ns;
        if (!success && state.result==DeviceResult::Ready)
            state.result=DeviceResult::BackendError;
        release_same_id(state.read.module_id,state.read.axi_id);
    }
    sweep_retired();
    start_media();
}

void StackFrontend::present(std::uint64_t id)
{
    auto& state=active_.at(id);
    if (state.caller_delivered) return;
    state.caller_delivered=true;
    ready_.push_back(id);
}

void StackFrontend::release_same_id(std::uint32_t m,std::uint32_t axi_id)
{
    auto& module=modules_.at(m);
    auto it=module.same_id.find(axi_id);
    if (it==module.same_id.end()) return;
    auto& queue=it->second;
    while (!queue.empty()) {
        auto& state=active_.at(queue.front());
        if (state.result==DeviceResult::Ready) {
            if (!state.ar_delivered || !state.media_done) break;
            module.link.enqueue(Direction::Return,state.read.request_id,
                                std::max(current_time_ns(),state.media_ready_ns));
            state.r_enqueued=true;
            module.r_order[axi_id].push_back(state.read.request_id);
            queue.pop_front();
        } else {
            if (!module.r_order[axi_id].empty()) break;
            present(state.read.request_id);
            queue.pop_front();
        }
    }
    if (queue.empty()) module.same_id.erase(it);
}

void StackFrontend::maybe_erase(std::uint64_t id)
{
    const auto it=active_.find(id);
    if (it==active_.end()) return;
    const auto& s=it->second;
    const auto& link=modules_[s.read.module_id].link;
    if (s.caller_consumed && s.ar_delivered &&
        (!s.group || s.media_done) && (!s.r_enqueued || s.r_consumed) &&
        !link.message_active(Direction::Request,id) &&
        !link.message_active(Direction::Return,id)) {
        --modules_[s.read.module_id].outstanding;
        consumed_pending_.erase(id);
        active_.erase(it);
    }
}

void StackFrontend::sweep_retired()
{
    for (auto it=consumed_pending_.begin();it!=consumed_pending_.end();) {
        const auto id=*it;
        ++it;
        maybe_erase(id);
    }
}

void StackFrontend::handle_delivery(std::uint32_t m,const Delivery& delivery)
{
    auto& state=active_.at(delivery.request_id);
    if (state.read.module_id!=m)
        throw std::logic_error("module delivered another module's transport ID");
    auto& module=modules_[m];
    if (delivery.direction==Direction::Request) {
        state.ar_delivered=true;
        state.ar_delivered_ns=delivery.delivered_ns;
        if (state.record_index)
            request_records_.at(*state.record_index).ar_delivered_ns=
                delivery.delivered_ns;
        module.link.consume(Direction::Request,delivery.request_id,
                            delivery.delivered_ns);
        if (state.result!=DeviceResult::Ready) {
            release_same_id(m,state.read.axi_id);
            maybe_erase(delivery.request_id);
            return;
        }
        const auto lpa=state.media_page/4096;
        const auto bank=hbf_cold_read_bank(lpa,layout_);
        DevicePageKey key{state.backing.canonical_id,state.backing.generation,
            stack_id_,m,state.backing.endpoint_id,
            state.backing.canonical_offset/4096,
            bank.native_channel,bank.native_chip};
        const auto arrival=coalescer_.arrive(delivery.request_id,key);
        if (!arrival) throw std::logic_error("stack accepted waiter bound exhausted");
        state.group=arrival->token;
        if (state.record_index)
            request_records_.at(*state.record_index).group_token=arrival->token;
        counters_.peak_media_groups=std::max<std::uint64_t>(
            counters_.peak_media_groups,coalescer_.group_count());
        if (arrival->buffer_hit) {
            state.media_done=true;
            state.media_ready_ns=delivery.delivered_ns;
            if (state.record_index)
                request_records_.at(*state.record_index).media_ready_ns=
                    delivery.delivered_ns;
            release_same_id(m,state.read.axi_id);
        } else start_media();
    } else {
        auto& order=module.r_order.at(state.read.axi_id);
        if (order.empty() || order.front()!=delivery.request_id)
            throw std::logic_error("same-module same-ID R order violated");
        order.pop_front();
        state.r_delivered=true;
        state.r_delivered_ns=delivery.delivered_ns;
        if (state.record_index)
            request_records_.at(*state.record_index).r_delivered_ns=
                delivery.delivered_ns;
        counters_.axi_payload_bytes+=64;
        if (state.result!=DeviceResult::Ready) {
            module.link.consume(Direction::Return,delivery.request_id,
                                delivery.delivered_ns);
            coalescer_.consume(delivery.request_id);
            state.r_consumed=true;
        }
        present(delivery.request_id);
        if (!order.empty()) {
            auto& next=active_.at(order.front());
            if (next.result!=DeviceResult::Ready)
                present(next.read.request_id);
        }
        release_same_id(m,state.read.axi_id);
        maybe_erase(delivery.request_id);
    }
}

void StackFrontend::mark_terminal(std::uint64_t id,DeviceResult reason)
{
    auto& state=active_.at(id);
    if (state.caller_consumed || state.result!=DeviceResult::Ready) return;
    state.result=reason;
    if (state.record_index)
        request_records_.at(*state.record_index).result=reason;
    if (state.group && !state.r_enqueued && !state.detached) {
        const bool pending=coalescer_.detach(id);
        state.detached=true;
        if (!pending) state.media_done=true;
        start_media();
    }
    auto& module=modules_[state.read.module_id];
    if (state.r_delivered && !state.r_consumed) {
        module.link.consume(Direction::Return,id,current_time_ns());
        coalescer_.consume(id);
        state.r_consumed=true;
    }
    if (!state.r_enqueued)
        release_same_id(state.read.module_id,state.read.axi_id);
    else if (state.r_delivered ||
             (!module.r_order[state.read.axi_id].empty() &&
              module.r_order[state.read.axi_id].front()==id))
        present(id);
}

void StackFrontend::cancel(std::uint64_t id)
{
    if (!active_.contains(id))
        throw std::invalid_argument("unknown or fully consumed child ID");
    mark_terminal(id,DeviceResult::Cancelled);
}

void StackFrontend::process_deadline(std::uint64_t id)
{
    const auto it=active_.find(id);
    if (it!=active_.end() && !it->second.caller_consumed)
        mark_terminal(id,DeviceResult::TimedOut);
}

DeviceCompletion StackFrontend::completion_of(const State& state) const
{
    return DeviceCompletion{state.read,state.result,state.ar_delivered_ns,
        state.media_ready_ns,state.r_delivered_ns,0,
        state.r_delivered ? 64U : 0U};
}

std::vector<std::uint64_t> StackFrontend::ready_ids() const
{
    return {ready_.begin(),ready_.end()};
}

std::optional<DeviceCompletion> StackFrontend::peek_completion(
    std::uint64_t id) const
{
    const auto it=active_.find(id);
    if (it==active_.end() || !it->second.caller_delivered ||
        it->second.caller_consumed)
        return std::nullopt;
    return completion_of(it->second);
}

DeviceCompletion StackFrontend::consume_completion(std::uint64_t id)
{
    const auto it=std::find(ready_.begin(),ready_.end(),id);
    if (it==ready_.end())
        throw std::invalid_argument("child response not ready for consumption");
    ready_.erase(it);
    auto& state=active_.at(id);
    auto& module=modules_[state.read.module_id];
    if (!state.r_consumed && state.r_delivered) {
        module.link.consume(Direction::Return,id,current_time_ns());
        coalescer_.consume(id);
        state.r_consumed=true;
        start_media();
    }
    state.caller_consumed=true;
    if (state.record_index) {
        auto& entry=request_records_.at(*state.record_index);
        entry.result=state.result;
        entry.consumed_ns=current_time_ns();
    }
    --counters_.caller_outstanding;
    if (state.result==DeviceResult::Ready) ++counters_.succeeded;
    else if (state.result==DeviceResult::Cancelled) ++counters_.cancelled;
    else ++counters_.failed;
    for (auto deadline=deadlines_.begin();deadline!=deadlines_.end();) {
        if (deadline->second==id) deadline=deadlines_.erase(deadline);
        else ++deadline;
    }
    auto out=completion_of(state);
    out.consumed_ns=current_time_ns();
    maybe_erase(id);
    if (active_.contains(id)) consumed_pending_.insert(id);
    return out;
}

std::uint64_t StackFrontend::current_time_ns() const
{
    auto now=media_->current_time_ns();
    for (const auto& module:modules_)
        now=std::max(now,module.link.current_time_ns());
    return now;
}

DeviceMediaPort::EventPeek StackFrontend::next_event()
{
    auto result=media_->next_event();
    if (!result.supported) return result;
    const auto consider=[&](std::uint64_t value) {
        if (!result.next_ns || value<*result.next_ns) result.next_ns=value;
    };
    for (const auto& module:modules_)
        if (const auto next=module.link.next_event_ns()) consider(*next);
    if (!deadlines_.empty()) consider(deadlines_.begin()->first);
    return result;
}

void StackFrontend::advance_until(std::uint64_t horizon)
{
    if (horizon<current_time_ns())
        throw std::invalid_argument("stack horizon precedes current time");
    while (true) {
        auto target=horizon;
        for (const auto& module:modules_)
            if (const auto next=module.link.next_event_ns())
                target=std::min(target,*next);
        if (!deadlines_.empty()) target=std::min(target,deadlines_.begin()->first);
        if (auto completion=media_->run_until(target)) {
            handle_media(*completion);
            const auto completed_at=media_->current_time_ns();
            while (auto same_time=media_->run_until(completed_at))
                handle_media(*same_time);
            continue;
        }
        bool processed=false;
        while (!deadlines_.empty() && deadlines_.begin()->first<=target) {
            const auto id=deadlines_.begin()->second;
            deadlines_.erase(deadlines_.begin());
            process_deadline(id);
            processed=true;
        }
        for (std::uint32_t m=0;m<modules_.size();++m) {
            const auto next=modules_[m].link.next_event_ns();
            if (!next || *next!=target) continue;
            for (const auto& delivery:modules_[m].link.step())
                handle_delivery(m,delivery);
            processed=true;
        }
        sweep_retired();
        if (!processed) return;
        // Recheck media even when a just-delivered AR was the last known
        // event at this horizon. A zero-delay fake port can complete at the
        // same timestamp; a single null marker before AR cannot close it.
    }
}

void StackFrontend::drain_until_idle(std::uint64_t max_horizon)
{
    if (!ready_.empty())
        throw std::logic_error("caller must consume ready stack responses");
    advance_until(max_horizon);
    if (!active_.empty() || coalescer_.group_count() ||
        !deadlines_.empty() || !ready_.empty())
        throw std::runtime_error("stack did not drain by horizon");
    for (const auto& module:modules_) {
        if (module.link.next_event_ns() || module.outstanding ||
            module.link.credits(Direction::Request)!=
                module.profile.initial_ar_granules ||
            module.link.credits(Direction::Return)!=
                module.profile.initial_r_granules)
            throw std::logic_error("stack link did not restore credits");
    }
    if (counters_.caller_outstanding ||
        counters_.accepted!=counters_.succeeded+counters_.failed+
                            counters_.cancelled)
        throw std::logic_error("stack completion accounting mismatch");
}

} // namespace hbfsim::ucie
