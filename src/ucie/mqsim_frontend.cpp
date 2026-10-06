#include <hbfsim/ucie/mqsim_frontend.hpp>
#include <hbfsim/ucie/engine_guard.hpp>

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace hbfsim::ucie {
namespace {
class RealMqsimPort final : public MediaPort {
public:
    explicit RealMqsimPort(const Profile& profile) : engine_(profile)
    {
        engine_.enable_observations();
    }
    void submit(const HbfRequest& r) override { engine_.submit(r); }
    std::optional<HbfCompletion> run_until(std::uint64_t t) override
    {
        return engine_.run_next_completion_until(t);
    }
    std::uint64_t current_time_ns() const override { return engine_.current_time_ns(); }
    std::vector<MqsimObservation> take_observations() override
    {
        return engine_.take_observations();
    }
private:
    MqsimOnlineEngine engine_;
};
} // namespace

struct UcieMqsimFrontend::EngineGuard {
    UcieEngineGuard lease;
};

UcieMqsimFrontend::UcieMqsimFrontend(LinkProfile lp, const Profile& mp)
    : guard_(std::make_unique<EngineGuard>()),
      media_(std::make_unique<RealMqsimPort>(mp)),
      link_(lp), profile_(std::move(lp))
{
    validate_link_profile(profile_);
    if (mp.page_bytes != 4096 || mp.capacity_bytes != profile_.local_capacity_bytes)
        throw std::invalid_argument("UCIe CPU frontend requires matching 4KiB MQSim geometry");
}

UcieMqsimFrontend::UcieMqsimFrontend(LinkProfile lp, const Profile& mp,
                                     std::unique_ptr<MediaPort> test_media)
    : media_(std::move(test_media)), link_(lp), profile_(std::move(lp))
{
    validate_link_profile(profile_);
    if (!media_ || mp.page_bytes != 4096 ||
        mp.capacity_bytes != profile_.local_capacity_bytes)
        throw std::invalid_argument("test media or UCIe/MQSim geometry is invalid");
}

UcieMqsimFrontend::~UcieMqsimFrontend() = default;

void UcieMqsimFrontend::record(std::uint64_t id, std::uint64_t t, const char* phase)
{
    constexpr std::size_t kTraceLimit = 4096;
    if (trace_.size() < kTraceLimit) trace_.push_back({id, t, phase});
    else ++counters_.trace_dropped;
}

bool UcieMqsimFrontend::try_submit(const AxiRead& r)
{
    const auto now = current_time_ns();
    if (r.request_id == 0 || r.axi_id >= (1U << 14) || r.bytes != 64 ||
        r.operation != 0 || r.local_address % 64 != 0 ||
        r.local_address > profile_.local_capacity_bytes - 64 || r.arrival_ns < now ||
        r.arrival_ns < last_accepted_arrival_ns_)
        throw std::invalid_argument("unsupported or invalid HBF AXI regular read");
    if (active_.contains(r.request_id))
        throw std::invalid_argument("duplicate active UCIe request_id");
    if (active_.size() >= profile_.max_accepted) return false;
    active_.emplace(r.request_id, RequestState{.axi=r});
    same_id_[r.axi_id].push_back(r.request_id);
    link_.enqueue(Direction::Request, r.request_id, r.arrival_ns);
    ++sequence_;
    last_accepted_arrival_ns_=r.arrival_ns;
    ++counters_.accepted;
    counters_.application_bytes += 64;
    record(r.request_id, r.arrival_ns, "accepted");
    return true;
}

void UcieMqsimFrontend::drain_observations()
{
    auto fresh = media_->take_observations();
    constexpr std::size_t kObservationLimit = 4096;
    for (auto& value : fresh) {
        if (observations_.size() < kObservationLimit)
            observations_.push_back(std::move(value));
        else ++counters_.observations_dropped;
    }
}

std::vector<MqsimObservation> UcieMqsimFrontend::take_media_observations()
{
    drain_observations();
    std::vector<MqsimObservation> out;
    out.swap(observations_);
    return out;
}

void UcieMqsimFrontend::enqueue_ready_response(std::uint32_t id)
{
    auto& ordered = same_id_.at(id);
    while (!ordered.empty()) {
        auto& head = active_.at(ordered.front());
        if (!head.media || head.response_enqueued) break;
        head.response_enqueued = true;
        if (head.media->status != static_cast<std::uint32_t>(RequestStatus::Ready)) {
            // Native backend failure is returned exactly once. AoU ErrorMsg
            // byte serialization is outside Stage 1; no ReadData is invented.
            // An earlier same-ID ReadData must be delivered first.
            if (!same_id_return_order_[id].empty()) {
                head.response_enqueued=false;
                break;
            }
            const auto t = std::max({head.media->modeled_completion_ns,
                link_.current_time_ns(), media_->current_time_ns()});
            delivered_.push_back(FrontendCompletion{.request=head.axi,
                .media=*head.media, .request_delivered_ns=head.request_delivered_ns,
                .media_ready_ns=head.media->modeled_completion_ns,
                .response_delivered_ns=t, .interface_bytes=0,
                .media_submit_bytes=4096});
            ++counters_.delivered;
            record(head.axi.request_id, t, "backend_error");
            ordered.pop_front();
        } else {
            link_.enqueue(Direction::Return, head.axi.request_id,
                          std::max({head.media->modeled_completion_ns,
                              link_.current_time_ns(), media_->current_time_ns()}));
            same_id_return_order_[id].push_back(head.axi.request_id);
            ordered.pop_front();
        }
    }
}

void UcieMqsimFrontend::handle_media(HbfCompletion completion)
{
    auto it = active_.find(completion.request_id);
    if (it == active_.end() || it->second.media)
        throw std::logic_error("duplicate or unknown MQSim completion");
    it->second.media = completion;
    ++counters_.media_completions;
    record(completion.request_id, completion.modeled_completion_ns, "media_ready");
    enqueue_ready_response(it->second.axi.axi_id);
}

std::optional<FrontendCompletion> UcieMqsimFrontend::run_next_completion_until(
    std::uint64_t horizon)
{
    if (horizon < current_time_ns())
        throw std::invalid_argument("UCIe horizon precedes current simulation time");
    auto pop_delivered = [&]() -> std::optional<FrontendCompletion> {
        if (delivered_.empty()) return std::nullopt;
        auto out = delivered_.front();
        delivered_.pop_front();
        const auto consumed_at = current_time_ns();
        if (out.interface_bytes != 0)
            link_.consume(Direction::Return, out.request.request_id, consumed_at);
        active_.erase(out.request.request_id); // accepted slot lasts through consumer poll
        ++counters_.consumed;
        record(out.request.request_id, consumed_at, "consumed");
        return out;
    };
    if (auto out = pop_delivered()) return out;

    while (true) {
        const auto event = link_.next_event_ns();
        const auto target = event ? std::min(horizon, *event) : horizon;
        if (auto media_completion = media_->run_until(target)) {
            drain_observations();
            handle_media(*media_completion);
            if (auto out = pop_delivered()) return out;
            continue;
        }
        drain_observations();
        if (event && *event <= horizon) {
            const auto delivered = link_.step();
            for (const auto& item : delivered) {
                auto& state = active_.at(item.request_id);
                if (item.direction == Direction::Request) {
                    state.request_delivered_ns = item.delivered_ns;
                    const auto page = (state.axi.local_address / 4096) * 4096;
                    media_->submit(HbfRequest{.request_id=state.axi.request_id,
                        .sequence=state.axi.request_id, .arrival_ns=item.delivered_ns,
                        .logical_address=page, .bytes=4096,
                        .operation=static_cast<std::uint32_t>(RequestOperation::Read)});
                    counters_.media_submit_bytes += 4096;
                    record(item.request_id, item.delivered_ns, "mqsim_submit");
                    link_.consume(Direction::Request, item.request_id,
                                  item.delivered_ns);
                } else {
                    if (!state.media || state.media->status !=
                        static_cast<std::uint32_t>(RequestStatus::Ready))
                        throw std::logic_error("UCIe returned data before ready media");
                    delivered_.push_back(FrontendCompletion{.request=state.axi,
                        .media=*state.media, .request_delivered_ns=state.request_delivered_ns,
                        .media_ready_ns=state.media->modeled_completion_ns,
                        .response_delivered_ns=item.delivered_ns});
                    ++counters_.delivered;
                    record(item.request_id, item.delivered_ns, "response_delivered");
                    auto& same = same_id_return_order_.at(state.axi.axi_id);
                    if (same.empty() || same.front() != item.request_id)
                        throw std::logic_error("AXI same-ID response order violated");
                    same.pop_front();
                    enqueue_ready_response(state.axi.axi_id);
                }
            }
            if (auto out = pop_delivered()) return out;
            if (current_time_ns() == horizon &&
                (!link_.next_event_ns() || *link_.next_event_ns() > horizon))
                return std::nullopt;
            continue;
        }
        if (!event && !active_.empty() &&
            counters_.media_completions == counters_.accepted)
            throw std::logic_error("UCIe pending work has no causal link event");
        return std::nullopt;
    }
}

std::uint64_t UcieMqsimFrontend::current_time_ns() const
{
    return std::max(media_->current_time_ns(), link_.current_time_ns());
}

std::size_t UcieMqsimFrontend::outstanding() const noexcept
{
    return active_.size();
}

} // namespace hbfsim::ucie
