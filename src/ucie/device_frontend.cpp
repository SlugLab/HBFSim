#include <hbfsim/ucie/device_frontend.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hbfsim::ucie {
namespace {
class RealDeviceMediaPort final : public DeviceMediaPort {
public:
    RealDeviceMediaPort(const Profile& profile,const HbfBankLayout& layout)
        : engine_(profile,layout) {}
    void submit(const HbfRequest& request) override { engine_.submit(request); }
    std::optional<HbfCompletion> run_until(std::uint64_t t) override
    { return engine_.run_next_completion_until(t); }
    std::uint64_t current_time_ns() const override
    { return engine_.current_time_ns(); }
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

void validate_args(const LinkProfile& link,const Profile& stack,
                   const HbfBankLayout& layout,std::uint32_t module)
{
    validate_link_profile(link);
    (void)hbf_mqsim_geometry(stack,layout);
    if (module>=layout.host_channels ||
        link.local_capacity_bytes>(1ULL<<36) ||
        link.local_capacity_bytes==0 ||
        stack.capacity_bytes/layout.host_channels!=link.local_capacity_bytes ||
        stack.capacity_bytes%layout.host_channels!=0)
        throw std::invalid_argument("module AXI capacity differs from stack media geometry");
}
bool same_bank(const HbfReadBank& a,const HbfReadBank& b)
{
    return a.native_channel==b.native_channel &&
           a.native_chip==b.native_chip &&
           a.native_die==b.native_die &&
           a.native_plane==b.native_plane;
}
}

UcieDeviceFrontend::UcieDeviceFrontend(LinkProfile link,const Profile& stack,
        HbfBankLayout layout,std::uint32_t stack_id,std::uint32_t module_id,
        bool coalescing,bool buffer_cache)
    : guard_(std::make_unique<UcieEngineGuard>()),
      media_([&]() -> std::unique_ptr<DeviceMediaPort> {
          validate_args(link,stack,layout,module_id);
          return std::make_unique<RealDeviceMediaPort>(stack,layout);
      }()),
      link_profile_(link),stack_profile_(stack),layout_(layout),
      stack_id_(stack_id),module_id_(module_id),
      registry_(link.local_capacity_bytes),
      coalescer_(link.max_accepted,link.max_accepted,buffer_cache,
                 layout.host_channels,
                 layout.core_dies_per_channel*layout.banks_per_die,
                 coalescing),link_(link)
{}

UcieDeviceFrontend::UcieDeviceFrontend(LinkProfile link,const Profile& stack,
        HbfBankLayout layout,std::uint32_t stack_id,std::uint32_t module_id,
        std::unique_ptr<DeviceMediaPort> test_media,
        bool coalescing,bool buffer_cache)
    : media_(std::move(test_media)),link_profile_(link),stack_profile_(stack),
      layout_(layout),stack_id_(stack_id),module_id_(module_id),
      registry_(link.local_capacity_bytes),
      coalescer_(link.max_accepted,link.max_accepted,buffer_cache,
                 layout.host_channels,
                 layout.core_dies_per_channel*layout.banks_per_die,
                 coalescing),link_(link)
{
    validate_args(link,stack,layout,module_id);
    if (!media_) throw std::invalid_argument("missing deterministic test media");
}

UcieDeviceFrontend::~UcieDeviceFrontend()=default;

void UcieDeviceFrontend::add_backing(const BackingRange& range)
{
    if (range.stack_id!=stack_id_ || range.module_id!=module_id_)
        throw std::invalid_argument("backing region belongs to another stack/module");
    registry_.add(range);
}

void UcieDeviceFrontend::retire_backing_generation(
    std::uint64_t canonical_id,std::uint64_t generation,
    std::uint64_t endpoint_id)
{
    registry_.retire_generation(canonical_id,generation,stack_id_,module_id_,endpoint_id);
}

void UcieDeviceFrontend::record(std::uint64_t id,std::uint64_t t,const char* phase)
{
    constexpr std::size_t limit=4096;
    if (trace_.size()<limit) trace_.push_back({id,t,phase});
    else ++counters_.trace_dropped;
}

bool UcieDeviceFrontend::try_submit(const DeviceRead& read)
{
    const auto now=current_time_ns();
    if (!read.request_id || read.bytes!=64 || read.operation!=0 ||
        read.axi_id>=(1U<<14) || read.stack_id!=stack_id_ ||
        read.module_id!=module_id_ || read.local_address%64 ||
        read.arrival_ns<now || read.arrival_ns<last_arrival_ns_ ||
        (read.deadline_ns && read.deadline_ns<read.arrival_ns))
        throw std::invalid_argument("unsupported or invalid HBF regular read");
    if (active_.contains(read.request_id))
        throw std::invalid_argument("duplicate active device request ID");
    const auto resolved=registry_.resolve_span(read.local_address,64,
        read.stack_id,read.module_id,read.endpoint_id);
    if (!resolved || resolved->canonical_offset%64)
        throw std::invalid_argument("unregistered or unaligned canonical read span");
    const auto canonical_page=resolved->canonical_offset/4096;
    const auto media_lpa=hbf_media_lpa(canonical_page,module_id_,layout_);
    if (media_lpa>=stack_profile_.capacity_bytes/4096 ||
        media_lpa>std::numeric_limits<std::uint64_t>::max()/4096)
        throw std::out_of_range("resolved media page exceeds HBF stack");
    const auto media_page=media_lpa*4096;
    if ((read.expected_media_page && *read.expected_media_page!=media_page) ||
        (read.expected_generation && *read.expected_generation!=resolved->generation))
        throw std::invalid_argument("forged media page or backing generation");
    if (active_.size()>=link_profile_.max_accepted) {
        ++counters_.rejected_backpressure;
        return false;
    }
    link_.enqueue(Direction::Request,read.request_id,read.arrival_ns);
    State state{.read=read,.backing=*resolved,.media_page=media_page};
    constexpr std::size_t record_limit=4096;
    if (request_records_.size()<record_limit) {
        state.record_index=request_records_.size();
        request_records_.push_back(DeviceRequestRecord{
            .request_id=read.request_id,.transport_id=read.request_id,
            .canonical_id=resolved->canonical_id,
            .generation=resolved->generation,.stack_id=stack_id_,
            .module_id=module_id_,.endpoint_id=read.endpoint_id,
            .canonical_page=canonical_page,.media_lpa=media_lpa,
            .bank=hbf_cold_read_bank(media_lpa,layout_),
            .accepted_ns=read.arrival_ns});
    } else ++counters_.request_records_dropped;
    active_.emplace(read.request_id,std::move(state));
    same_id_[read.axi_id].push_back(read.request_id);
    if (read.deadline_ns) deadlines_.emplace(read.deadline_ns,read.request_id);
    last_arrival_ns_=read.arrival_ns;
    ++counters_.accepted;
    ++counters_.caller_outstanding;
    counters_.peak_transport_outstanding=std::max<std::uint64_t>(
        counters_.peak_transport_outstanding,active_.size());
    counters_.application_bytes+=64;
    record(read.request_id,read.arrival_ns,"accepted");
    return true;
}

void UcieDeviceFrontend::start_media()
{
    for (const auto& start:coalescer_.take_submittable()) {
        const auto lpa=hbf_media_lpa(start.key.canonical_page,module_id_,layout_);
        const auto page=lpa*4096;
        const auto observed=media_->inspect(page);
        const auto predicted=hbf_cold_read_bank(lpa,layout_);
        if (!same_bank(observed,predicted) ||
            observed.native_channel!=start.key.native_channel ||
            observed.native_chip!=start.key.native_chip)
            throw std::logic_error("FTL bank query differs from HBF resource identity");
        media_->expect(start.token,page,observed);
        expected_banks_.emplace(start.token,observed);
        media_->submit(HbfRequest{.request_id=start.token,.sequence=start.token,
            .arrival_ns=current_time_ns(),.logical_address=page,.bytes=4096,
            .operation=static_cast<std::uint32_t>(RequestOperation::Read)});
        ++counters_.media_submits;
        counters_.media_submit_bytes+=4096;
        record(start.token,current_time_ns(),"native_submit");
    }
}

void UcieDeviceFrontend::handle_media(HbfCompletion completion)
{
    const auto proof=media_->finish(completion.request_id);
    const auto bank=expected_banks_.at(completion.request_id);
    expected_banks_.erase(completion.request_id);
    constexpr std::size_t proof_limit=4096;
    if (native_proofs_.size()<proof_limit)
        native_proofs_.push_back({stack_id_,module_id_,completion.request_id,
                                  bank,proof});
    else ++counters_.native_proofs_dropped;
    counters_.native_commands+=proof.issued_commands;
    bool success=completion.status==static_cast<std::uint32_t>(RequestStatus::Ready);
    if (success && (proof.issued_commands!=1 || !proof.bank_match ||
                    !proof.logical_page_match || proof.observed_media_bytes!=4096 ||
                    proof.observer_failed || proof.phase_events[1]!=1 ||
                    proof.phase_events[2]!=1 || proof.phase_events[3]==0 ||
                    proof.phase_events[4]==0))
        throw std::logic_error("native NAND command proof failed for ready media group");
    const auto ready=coalescer_.complete(completion.request_id,success);
    for (auto& [id,state]:active_) {
        if (!state.group || *state.group!=completion.request_id) continue;
        state.media_done=true;
        state.media_ready_ns=completion.modeled_completion_ns;
        if (state.record_index)
            request_records_.at(*state.record_index).media_ready_ns=
                completion.modeled_completion_ns;
        if (!success && state.result==DeviceResult::Ready)
            state.result=DeviceResult::BackendError;
        release_same_id(state.read.axi_id);
    }
    sweep_retired();
    (void)ready;
    start_media(); // a failed/fully detached group may release a bank slot
    record(completion.request_id,completion.modeled_completion_ns,"modeled_media_ready");
}

void UcieDeviceFrontend::present(std::uint64_t request_id)
{
    auto& state=active_.at(request_id);
    if (state.caller_delivered) return;
    state.caller_delivered=true;
    delivered_.push_back(request_id);
    record(request_id,current_time_ns(),"caller_result_available");
}

void UcieDeviceFrontend::release_same_id(std::uint32_t axi_id)
{
    auto it=same_id_.find(axi_id);
    if (it==same_id_.end()) return;
    auto& queue=it->second;
    while (!queue.empty()) {
        auto& state=active_.at(queue.front());
        if (state.result==DeviceResult::Ready) {
            if (!state.ar_delivered || !state.media_done) break;
            link_.enqueue(Direction::Return,state.read.request_id,
                          std::max(current_time_ns(),state.media_ready_ns));
            state.r_enqueued=true;
            r_order_[axi_id].push_back(state.read.request_id);
            queue.pop_front();
        } else {
            if (!r_order_[axi_id].empty()) break;
            present(state.read.request_id);
            queue.pop_front();
        }
    }
    if (queue.empty()) same_id_.erase(it);
}

void UcieDeviceFrontend::maybe_erase(std::uint64_t request_id)
{
    const auto it=active_.find(request_id);
    if (it==active_.end()) return;
    const auto& s=it->second;
    if (s.caller_consumed && s.ar_delivered &&
        (!s.group || s.media_done) && (!s.r_enqueued || s.r_consumed) &&
        !link_.message_active(Direction::Request,request_id) &&
        !link_.message_active(Direction::Return,request_id))
        active_.erase(it);
}

void UcieDeviceFrontend::sweep_retired()
{
    for (auto it=active_.begin();it!=active_.end();) {
        const auto id=it->first;
        ++it;
        maybe_erase(id);
    }
}

void UcieDeviceFrontend::handle_delivery(const Delivery& delivery)
{
    auto& state=active_.at(delivery.request_id);
    if (delivery.direction==Direction::Request) {
        state.ar_delivered=true;
        state.ar_delivered_ns=delivery.delivered_ns;
        if (state.record_index)
            request_records_.at(*state.record_index).ar_delivered_ns=
                delivery.delivered_ns;
        link_.consume(Direction::Request,delivery.request_id,delivery.delivered_ns);
        if (state.result!=DeviceResult::Ready) {
            release_same_id(state.read.axi_id);
            maybe_erase(delivery.request_id);
            return;
        }
        const auto lpa=state.media_page/4096;
        const auto bank=hbf_cold_read_bank(lpa,layout_);
        DevicePageKey key{state.backing.canonical_id,state.backing.generation,
            stack_id_,module_id_,state.backing.endpoint_id,
            state.backing.canonical_offset/4096,
            bank.native_channel,bank.native_chip};
        const auto arrival=coalescer_.arrive(delivery.request_id,key);
        if (!arrival) throw std::logic_error("accepted read exhausted device waiter bound");
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
            release_same_id(state.read.axi_id);
        } else start_media();
        record(delivery.request_id,delivery.delivered_ns,"ar_delivered");
    } else {
        auto& order=r_order_.at(state.read.axi_id);
        if (order.empty() || order.front()!=delivery.request_id)
            throw std::logic_error("same-ID R delivery order violated");
        order.pop_front();
        state.r_delivered=true;
        state.r_delivered_ns=delivery.delivered_ns;
        if (state.record_index)
            request_records_.at(*state.record_index).r_delivered_ns=
                delivery.delivered_ns;
        counters_.axi_payload_bytes+=64;
        if (state.result!=DeviceResult::Ready) {
            link_.consume(Direction::Return,delivery.request_id,delivery.delivered_ns);
            coalescer_.consume(delivery.request_id);
            state.r_consumed=true;
        }
        present(delivery.request_id);
        if (!order.empty()) {
            auto& next=active_.at(order.front());
            if (next.result!=DeviceResult::Ready)
                present(next.read.request_id);
        }
        release_same_id(state.read.axi_id);
        maybe_erase(delivery.request_id);
        record(delivery.request_id,delivery.delivered_ns,"r_delivered");
    }
}

void UcieDeviceFrontend::mark_terminal(std::uint64_t request_id,DeviceResult reason)
{
    auto& state=active_.at(request_id);
    if (state.caller_consumed || state.result!=DeviceResult::Ready) return;
    state.result=reason;
    if (state.record_index)
        request_records_.at(*state.record_index).result=reason;
    if (state.group && !state.r_enqueued && !state.detached) {
        const bool native_callback_pending=coalescer_.detach(request_id);
        state.detached=true;
        if (!native_callback_pending) state.media_done=true;
        start_media();
    }
    if (state.r_delivered && !state.r_consumed) {
        link_.consume(Direction::Return,request_id,current_time_ns());
        coalescer_.consume(request_id);
        state.r_consumed=true;
    }
    if (!state.r_enqueued) release_same_id(state.read.axi_id);
    else if (state.r_delivered ||
             (!r_order_[state.read.axi_id].empty() &&
              r_order_[state.read.axi_id].front()==request_id))
        present(request_id);
    record(request_id,current_time_ns(),
           reason==DeviceResult::Cancelled ? "cancelled" : "timed_out");
}

void UcieDeviceFrontend::cancel(std::uint64_t request_id)
{
    if (!active_.contains(request_id))
        throw std::invalid_argument("unknown or fully consumed request ID");
    mark_terminal(request_id,DeviceResult::Cancelled);
}

void UcieDeviceFrontend::process_deadline(std::uint64_t request_id)
{
    const auto it=active_.find(request_id);
    if (it!=active_.end() && !it->second.caller_consumed)
        mark_terminal(request_id,DeviceResult::TimedOut);
}

std::optional<DeviceCompletion> UcieDeviceFrontend::pop_delivered()
{
    if (delivered_.empty()) return std::nullopt;
    const auto id=delivered_.front();
    delivered_.pop_front();
    auto& state=active_.at(id);
    if (!state.r_consumed && state.r_delivered) {
        link_.consume(Direction::Return,id,current_time_ns());
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
    for (auto it=deadlines_.begin();it!=deadlines_.end();) {
        if (it->second==id) it=deadlines_.erase(it);
        else ++it;
    }
    DeviceCompletion out{state.read,state.result,state.ar_delivered_ns,
        state.media_ready_ns,state.r_delivered_ns,current_time_ns(),
        state.r_delivered ? 64U : 0U};
    maybe_erase(id);
    record(id,current_time_ns(),"caller_consumed");
    return out;
}

void UcieDeviceFrontend::advance_impl(std::uint64_t horizon,
                                       bool stop_on_delivery)
{
    if (horizon<current_time_ns())
        throw std::invalid_argument("device horizon precedes current time");
    if (stop_on_delivery && !delivered_.empty()) return;
    while (true) {
        const auto link_event=link_.next_event_ns();
        const auto deadline=deadlines_.empty()
            ? std::optional<std::uint64_t>{}
            : std::optional<std::uint64_t>{deadlines_.begin()->first};
        auto target=horizon;
        if (link_event) target=std::min(target,*link_event);
        if (deadline) target=std::min(target,*deadline);
        // A completion may enqueue a new R earlier than the old target.
        // Recompute the horizon after draining only its exact timestamp.
        if (auto completion=media_->run_until(target)) {
            handle_media(*completion);
            const auto completed_at=media_->current_time_ns();
            while (auto same_time=media_->run_until(completed_at))
                handle_media(*same_time);
            if (stop_on_delivery && !delivered_.empty()) return;
            continue;
        }
        bool processed=false;
        while (!deadlines_.empty() && deadlines_.begin()->first<=target) {
            const auto id=deadlines_.begin()->second;
            deadlines_.erase(deadlines_.begin());
            process_deadline(id);
            processed=true;
        }
        if (link_event && *link_event==target) {
            for (const auto& delivery:link_.step()) handle_delivery(delivery);
            processed=true;
        }
        sweep_retired();
        if (stop_on_delivery && !delivered_.empty()) return;
        if (!processed || (target==horizon &&
            (!link_.next_event_ns() || *link_.next_event_ns()>horizon) &&
            (deadlines_.empty() || deadlines_.begin()->first>horizon)))
            return;
    }
}

void UcieDeviceFrontend::advance_until(std::uint64_t horizon)
{
    advance_impl(horizon,false);
}

std::optional<DeviceCompletion> UcieDeviceFrontend::poll_completion()
{
    return pop_delivered();
}

std::optional<DeviceCompletion> UcieDeviceFrontend::run_next_completion_until(
    std::uint64_t horizon)
{
    if (horizon<current_time_ns())
        throw std::invalid_argument("device horizon precedes current time");
    if (auto result=pop_delivered()) return result;
    advance_impl(horizon,true);
    return pop_delivered();
}

void UcieDeviceFrontend::drain_until_idle(std::uint64_t max_horizon)
{
    while (true) {
        if (active_.empty() && coalescer_.group_count()==0 &&
            !link_.next_event_ns() && deadlines_.empty()) break;
        const auto next=link_.next_event_ns();
        (void)run_next_completion_until(
            next ? std::min(max_horizon,*next) : max_horizon);
        if (active_.empty() && coalescer_.group_count()==0 &&
            !link_.next_event_ns() && deadlines_.empty()) break;
        if (current_time_ns()>=max_horizon)
            throw std::runtime_error("device frontend did not drain by horizon");
    }
    if (link_.credits(Direction::Request)!=link_profile_.initial_ar_granules ||
        link_.credits(Direction::Return)!=link_profile_.initial_r_granules ||
        counters_.caller_outstanding!=0 ||
        counters_.accepted!=counters_.succeeded+counters_.failed+counters_.cancelled)
        throw std::logic_error("device frontend drain/accounting mismatch");
}

std::uint64_t UcieDeviceFrontend::current_time_ns() const
{
    return std::max(media_->current_time_ns(),link_.current_time_ns());
}
} // namespace hbfsim::ucie
