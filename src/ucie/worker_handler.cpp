#include "worker_handler.hpp"
#include <hbfsim/ucie/device_profile.hpp>
#include <hbfsim/ucie/multistack_profile.hpp>
#include <hbfsim/ucie/stack_frontend.hpp>
#include "typed_rpc.hpp"
#include "reserve_submit_single_v1.hpp"
#include "ordered_admission_v1.hpp"
#include "compact_consume_v1.hpp"
#include "next_event_snapshot_v1.hpp"
#include "shm_rpc.hpp"
#include <json.hpp>
#include <sys/resource.h>
#include <unistd.h>
#include <cstdint>
#include <chrono>
#include <limits>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
#include <utility>

namespace {
using nlohmann::json;
using namespace hbfsim::ucie;
template <typename T>
T number(const json& value,const char* key)
{
    const auto& item=value.at(key);
    if (!item.is_number_unsigned())
        throw std::invalid_argument(std::string("non-unsigned IPC field: ")+key);
    const auto raw=item.get<std::uint64_t>();
    if (raw>std::numeric_limits<T>::max())
        throw std::out_of_range(std::string("IPC field out of range: ")+key);
    return static_cast<T>(raw);
}

// Parse each worker profile once; top is file format, shared_upstream is topology.
std::pair<DeviceProfile,bool> worker_profile_settings(const std::filesystem::path& path,bool top)
{
    if (!top) return {load_device_profile(path),true};
    auto parsed=load_multistack_profile(path);
    const bool independent=!parsed.shared_upstream;
    return {std::move(parsed.device),independent};
}
json ready_snapshot(const StackFrontend& front,std::size_t offset,
                    std::size_t count)
{
    json entries=json::array();
    const auto ids=front.ready_ids();
    for (auto i=offset;i<ids.size() && i-offset<count;++i) {
        const auto id=ids[i];
        const auto completion=front.peek_completion(id);
        if (!completion) throw std::logic_error("ready ID has no state");
        entries.push_back({{"request_id",id},
            {"module_id",completion->request.module_id},
            {"result",static_cast<int>(completion->result)},
            {"ar_delivered_ns",completion->ar_delivered_ns},
            {"media_ready_ns",completion->media_ready_ns},
            {"response_delivered_ns",completion->response_delivered_ns}});
    }
    return entries;
}

std::size_t typed_ready_snapshot(const StackFrontend& front,std::size_t offset,
                                 typed_rpc::Writer& reply)
{
    const auto ids=front.ready_ids();
    if (ids.size()>std::numeric_limits<std::uint32_t>::max() ||
        offset>ids.size())
        throw std::overflow_error("typed ready snapshot size");
    const auto count=std::min<std::size_t>(16,ids.size()-offset);
    reply.u32(static_cast<std::uint32_t>(ids.size()));
    reply.u32(static_cast<std::uint32_t>(count));
    for (auto i=offset;i<offset+count;++i) {
        const auto completion=front.peek_completion(ids[i]);
        if (!completion) throw std::logic_error("ready ID has no state");
        reply.u64(ids[i]);
        reply.u32(completion->request.module_id);
        reply.u32(static_cast<std::uint32_t>(completion->result));
        reply.u64(completion->ar_delivered_ns);
        reply.u64(completion->media_ready_ns);
        reply.u64(completion->response_delivered_ns);
    }
    return count;
}
}

namespace hbfsim::ucie {
struct WorkerCommandState::Impl {
    DeviceProfile profile;
    StackFrontend front;
    std::uint32_t stack_id;
    bool shm_available;
    bool local_transport;
    bool independent_profile;
    std::optional<std::uint64_t> closed;
    bool dirty{};
    std::uint64_t ipc_commands{};
    std::uint64_t advance_commands{};
    std::uint64_t peek_commands{};
    std::uint64_t close_commands{};
    std::uint64_t close_advance_commands{};
    std::uint64_t reserve_commands{};
    std::uint64_t submit_commands{};
    std::uint64_t consume_commands{};
    std::uint64_t is_active_commands{};
    struct Reservation {
        std::uint64_t token{};
        std::vector<std::uint32_t> remaining;
    };
    std::optional<Reservation> reservation;
    std::optional<std::size_t> snapshot_next;
    std::size_t snapshot_total{};
    std::uint64_t expected_sequence{};
    bool typed_active{};
    bool capacity_active{};
    bool next_event_active{};
    bool shm_active{};
    bool reserve_submit_active{};
    bool compact_active{};
    std::uint64_t compact_commands{},compact_offered{},compact_confirmed{},compact_terminal{};
    std::array<std::uint64_t,17> compact_histogram{};
    std::array<std::uint64_t,4> compact_results{};
    bool ordered_active{};
    std::uint64_t ordered_commands{},ordered_reserved{},ordered_denied{},
        ordered_accepted{},ordered_released{},ordered_terminal{};
    std::uint64_t fusion_commands{},fusion_reserved{},fusion_denied{},
        fusion_accepted{},fusion_released{};
    bool stopped{};
    int exit_code{};
    std::chrono::milliseconds reply_timeout{std::chrono::seconds(30)};

    Impl(const std::filesystem::path& path,bool top,std::uint32_t stack,
         bool has_shm,bool local)
        : Impl(worker_profile_settings(path,top),stack,has_shm,local) {}

    Impl(std::pair<DeviceProfile,bool> settings,std::uint32_t stack,bool has_shm,bool local)
        : profile(std::move(settings.first)),
          front(std::vector<LinkProfile>(profile.layout.host_channels,profile.link),
                profile.media,profile.layout,stack,profile.coalescing,
                profile.buffer_cache),
          stack_id(stack),shm_available(has_shm),local_transport(local),
          independent_profile(settings.second) {}

    void append_capacity(typed_rpc::Writer& reply) const {
        if (!capacity_active) return;
        reply.u32(1); // negotiated capacity subprotocol, not typed version
        reply.u64(front.current_time_ns());
        reply.u32(profile.layout.host_channels);
        for (std::uint32_t m=0;m<profile.layout.host_channels;++m) {
            const auto [occupied,limit]=front.module_capacity(m);
            reply.u32(occupied);
            reply.u32(limit);
        }
    }

    void append_next_event(typed_rpc::Writer& reply) {
        if (!next_event_active) return;
        // This Impl constructs only the proven RealStackMediaPort. Successful
        // advance has initialized MQSim and drained staging before quiescence.
        // No generic DeviceMediaPort capability is extended by this handshake.
        if (closed && front.current_time_ns()<=*closed)
            throw std::logic_error("next-event snapshot at closed horizon");
        next_event_snapshot_v1::encode(reply,
            {front.current_time_ns(),front.next_event()});
    }

    bool reserve_stage(std::uint64_t token,std::uint64_t horizon,
                       std::vector<std::uint32_t> counts)
    {
        if (counts.size()!=profile.layout.host_channels)
            throw std::invalid_argument("invalid worker reservation count");
        if (!token || (closed && horizon<=*closed) || horizon<front.current_time_ns())
            throw std::invalid_argument("invalid worker reservation");
        const bool reserved=front.can_reserve(counts);
        if (reserved)
            reservation=Reservation{token,std::move(counts)};
        return reserved;
    }

    bool submit_stage(const DeviceRead& read,
                      const std::optional<PacketBackingProof>& proof)
    {
        if (closed && read.arrival_ns<=*closed)
            throw std::invalid_argument("input at closed horizon");
        if (reservation && (read.module_id>=reservation->remaining.size() ||
                            reservation->remaining[read.module_id]==0))
            throw std::invalid_argument("child outside reserved module count");
        const bool accepted=proof ? front.try_submit_packet(read,*proof) :
                                   front.try_submit(read);
        if (accepted) {
            dirty=true;
            if (reservation) {
                --reservation->remaining[read.module_id];
                bool done=true;
                for (const auto count:reservation->remaining)
                    if (count)
                        done=false;
                if (done)
                    reservation.reset();
            }
        }
        return accepted;
    }

    void release_stage(std::uint64_t token)
    {
        if (!reservation || reservation->token!=token)
            throw std::invalid_argument("unknown reservation release");
        reservation.reset();
    }

    std::string handle(std::string frame) {
        if (stopped) throw std::runtime_error("worker command after stop");
        reply_timeout=std::chrono::seconds(30);
            if (typed_active && !frame.empty() &&
                frame[0]==typed_rpc::typed_tag) {
                const auto reply_sequence=expected_sequence+1;
                try {
                    typed_rpc::Reader command(std::move(frame));
                    if (command.sequence()!=reply_sequence ||
                        command.stack()!=stack_id)
                        throw std::invalid_argument("typed worker sequence/stack mismatch");
                    expected_sequence=reply_sequence;
                    const auto op=command.op();
                    ++ipc_commands;
                    if (op==typed_rpc::Op::Reserve) ++reserve_commands;
                    else if (op==typed_rpc::Op::Submit) ++submit_commands;
                    else if (op==typed_rpc::Op::Consume) ++consume_commands;
                    else if (op==typed_rpc::Op::IsActive) ++is_active_commands;
                    if (snapshot_next && op!=typed_rpc::Op::ReadyPage)
                        throw std::invalid_argument("unfinished ready snapshot page sequence");
                    if (reservation && op!=typed_rpc::Op::Submit)
                        throw std::invalid_argument("unfinished worker admission reservation");
                    typed_rpc::Writer reply(op,reply_sequence,stack_id);
                    switch (op) {
                    case typed_rpc::Op::Reserve: {
                        const auto token=command.u64();
                        const auto horizon=command.u64();
                        const auto length=command.u32();
                        if (length!=profile.layout.host_channels)
                            throw std::invalid_argument("invalid worker reservation count");
                        std::vector<std::uint32_t> counts;
                        counts.reserve(length);
                        for (std::uint32_t i=0;i<length;++i)
                            counts.push_back(command.u32());
                        command.done();
                        reply.boolean(reserve_stage(token,horizon,std::move(counts)));
                        break;
                    }
                    case typed_rpc::Op::Submit: {
                        const auto fields=reserve_submit_single_v1::decode_read(command,stack_id);
                        command.done();
                        reply.boolean(submit_stage(fields.read,fields.proof));
                        break;
                    }
                    case typed_rpc::Op::ReserveSubmitSingle: {
                        if (!reserve_submit_active || !shm_active || local_transport)
                            throw std::invalid_argument("unnegotiated single-child admission");
                        if (command.u32()!=reserve_submit_single_v1::version || command.u32()!=1)
                            throw std::invalid_argument("single-child admission version/count");
                        const auto token=command.u64(),horizon=command.u64();
                        const auto length=command.u32();
                        if (length!=profile.layout.host_channels ||
                            length>reserve_submit_single_v1::max_modules)
                            throw std::invalid_argument("single-child admission module length");
                        std::vector<std::uint32_t> counts;
                        counts.reserve(length);
                        std::uint64_t sum=0;
                        for (std::uint32_t m=0;m<length;++m) {
                            const auto value=command.u32();
                            counts.push_back(value);
                            sum+=value;
                        }
                        const auto fields=reserve_submit_single_v1::decode_read(command,stack_id);
                        command.done(); // Entire bounded frame before any mutation.
                        if (sum!=1)
                            throw std::invalid_argument("single-child admission must be one-hot");
                        ++fusion_commands;
                        ++reserve_commands;
                        const bool reserved=reserve_stage(token,horizon,std::move(counts));
                        bool accepted=false;
                        if (reserved) {
                            ++fusion_reserved;
                            // Denial deliberately does not inspect Submit semantics.
                            if (fields.read.request_id!=token || fields.read.arrival_ns!=horizon)
                                throw std::invalid_argument("single-child admission read/token/horizon mismatch");
                            ++submit_commands;
                            accepted=submit_stage(fields.read,fields.proof);
                            if (accepted)
                                ++fusion_accepted;
                            else {
                                release_stage(token);
                                ++fusion_released;
                            }
                        } else
                            ++fusion_denied;
                        reserve_submit_single_v1::encode_reply(reply,token,horizon,
                            fields.read.request_id,{reserved,accepted});
                        break;
                    }
                    case typed_rpc::Op::ReserveSubmitOrdered: {
                        if (!ordered_active || !shm_active || local_transport ||
                            !independent_profile || profile.layout.host_channels!=ordered_admission_v1::modules)
                            throw std::invalid_argument("unnegotiated ordered admission");
                        const auto request=ordered_admission_v1::decode_request(command,stack_id);
                        ordered_admission_v1::Reply result;
                        ++ordered_commands;
                        for (std::uint32_t i=0;i<request.count;++i) {
                            const auto& record=request.records[i];
                            try {
                                std::vector<std::uint32_t> counts(profile.layout.host_channels);
                                counts.at(request.module)=1;
                                ++reserve_commands;
                                const bool reserved=reserve_stage(record.token,request.horizon,std::move(counts));
                                if (!reserved) {
                                    ++ordered_denied;
                                    result.items[result.completed++]={record.token,record.read.request_id,false,false};
                                    result.stop=ordered_admission_v1::Stop::ReserveDenied;
                                    break;
                                }
                                ++ordered_reserved;
                                // Denied and unattempted suffix reads are not semantically inspected.
                                if (record.read.request_id!=record.token || record.read.arrival_ns!=request.horizon)
                                    throw std::invalid_argument("ordered admission read/token/horizon mismatch");
                                ++submit_commands;
                                const bool accepted=submit_stage(record.read,record.proof);
                                if (!accepted) {
                                    release_stage(record.token);++ordered_released;
                                    result.items[result.completed++]={record.token,record.read.request_id,true,false};
                                    result.stop=ordered_admission_v1::Stop::SubmitFalse;
                                    break;
                                }
                                // Original one-hot Submit exhaustion must clear its reservation.
                                if (reservation) throw std::logic_error("ordered admission reservation not exhausted");
                                ++ordered_accepted;
                                result.items[result.completed++]={record.token,record.read.request_id,true,true};
                            } catch (const std::exception& error) {
                                // The current item may already have side effects; report ONLY completed prefix.
                                result.stop=ordered_admission_v1::Stop::SemanticError;
                                result.failing_index=i;result.error_code=1;
                                const auto* diagnostic=error.what();
                                while (diagnostic && result.diagnostic_size<ordered_admission_v1::max_diagnostic &&
                                       diagnostic[result.diagnostic_size]) {
                                    result.diagnostic[result.diagnostic_size]=diagnostic[result.diagnostic_size];
                                    ++result.diagnostic_size;
                                }
                                ++ordered_terminal;stopped=true;exit_code=1;
                                reply_timeout=std::chrono::seconds(1);
                                break;
                            }
                        }
                        ordered_admission_v1::encode_reply(reply,request,result);
                        // Serialization failure escapes to fail-closed handling; no guessed prefix.
                        break;
                    }
                    case typed_rpc::Op::Advance: {
                        const auto horizon=command.u64();
                        command.done();
                        if (closed && horizon<=*closed)
                            throw std::invalid_argument("advance at closed horizon");
                        front.advance_until(horizon);
                        ++advance_commands;
                        snapshot_total=front.ready_ids().size();
                        reply.u64(front.current_time_ns());
                        (void)typed_ready_snapshot(front,0,reply);
                        append_capacity(reply);
                        append_next_event(reply);
                        if (snapshot_total>16) snapshot_next=16;
                        dirty=false;
                        break;
                    }
                    case typed_rpc::Op::CloseAdvance: {
                        const auto from=command.u64();
                        const auto to=command.u64();
                        command.done();
                        if (dirty || front.current_time_ns()!=from ||
                            (closed && from<=*closed) || to<=from)
                            throw std::invalid_argument("invalid close-and-advance horizon");
                        closed=from;
                        ++close_advance_commands;
                        front.advance_until(to);
                        snapshot_total=front.ready_ids().size();
                        reply.u64(front.current_time_ns());
                        (void)typed_ready_snapshot(front,0,reply);
                        append_capacity(reply);
                        append_next_event(reply);
                        if (snapshot_total>16) snapshot_next=16;
                        dirty=false;
                        break;
                    }
                    case typed_rpc::Op::ReadyPage: {
                        const auto horizon=command.u64();
                        const auto offset=command.u32();
                        command.done();
                        if (!snapshot_next || offset!=*snapshot_next ||
                            horizon!=front.current_time_ns() ||
                            front.ready_ids().size()!=snapshot_total)
                            throw std::invalid_argument("ready snapshot page changed");
                        const auto count=typed_ready_snapshot(front,offset,reply);
                        snapshot_next=offset+count;
                        if (*snapshot_next>=snapshot_total) snapshot_next.reset();
                        break;
                    }
                    case typed_rpc::Op::PeekEvent: {
                        const auto horizon=command.u64();
                        command.done();
                        if (dirty || horizon!=front.current_time_ns() ||
                            (closed && horizon<=*closed))
                            throw std::invalid_argument("event peek outside quiescent open horizon");
                        const auto peek=front.next_event();
                        reply.boolean(peek.supported);
                        reply.boolean(peek.next_ns.has_value());
                        if (peek.next_ns) reply.u64(*peek.next_ns);
                        ++peek_commands;
                        break;
                    }
                    case typed_rpc::Op::ConsumeCompactOrdered: {
                        if(!compact_active || !shm_active || local_transport || !independent_profile || profile.layout.host_channels!=16)
                            throw std::invalid_argument("unnegotiated compact Consume handler");
                        const auto request=compact_consume_v1::decode_request(command);
                        if((closed && request.horizon<=*closed) || request.horizon!=front.current_time_ns())
                            throw std::invalid_argument("consume outside open horizon");
                        compact_consume_v1::Reply result;
                        ++compact_commands; compact_offered+=request.count; ++compact_histogram[request.count];
                        for(std::uint32_t i=0;i<request.count;++i) {
                            try {
                                ++consume_commands; // One actual core attempt, not one compact packet.
                                const auto value=front.consume_completion(request.ids[i]);
                                dirty=true;
                                const auto kind=static_cast<std::uint32_t>(value.result);
                                if(value.request.module_id!=request.module || kind>=4)
                                    throw std::logic_error("compact Consume actual module/result mismatch");
                                ++result.results[kind]; ++compact_results[kind];
                                ++result.confirmed; ++compact_confirmed;
                            } catch(const std::exception& error) {
                                // Current core may already mutate: UNKNOWN, no successful suffix claimed.
                                result.stop=compact_consume_v1::Stop::SemanticError;
                                result.failing_index=i; result.error_code=1;
                                const char* text=error.what();
                                while(text && result.diagnostic_size<compact_consume_v1::max_diagnostic && text[result.diagnostic_size]) {
                                    result.diagnostic[result.diagnostic_size]=text[result.diagnostic_size]; ++result.diagnostic_size;
                                }
                                ++compact_terminal; stopped=true; exit_code=1;
                                reply_timeout=std::chrono::seconds(1); break;
                            }
                        }
                        compact_consume_v1::encode_reply(reply,request,result); break;
                    }
                    case typed_rpc::Op::Consume: {
                        const auto id=command.u64();
                        const auto horizon=command.u64();
                        command.done();
                        if ((closed && horizon<=*closed) ||
                            horizon!=front.current_time_ns())
                            throw std::invalid_argument("consume outside open horizon");
                        const auto result=front.consume_completion(id);
                        dirty=true;
                        reply.u32(result.request.module_id);
                        reply.u32(static_cast<std::uint32_t>(result.result));
                        reply.u64(result.ar_delivered_ns);
                        reply.u64(result.media_ready_ns);
                        reply.u64(result.response_delivered_ns);
                        reply.u64(result.consumed_ns);
                        reply.u32(result.axi_payload_bytes);
                        break;
                    }
                    case typed_rpc::Op::IsActive: {
                        const auto id=command.u64();
                        command.done();
                        reply.boolean(front.is_active(id));
                        break;
                    }
                    }
                    return std::move(reply).finish();
                } catch (const std::exception& error) {
                    const json failure={{"sequence",reply_sequence},
                        {"stack_id",stack_id},{"ok",false},{"error",error.what()}};
                    stopped=true;
                    exit_code=1;
                    reply_timeout=std::chrono::seconds(1);
                    return typed_rpc::tagged_json(failure.dump());
                }
            }
            if (typed_active) {
                if (frame.empty() || frame[0]!=typed_rpc::json_tag)
                    throw std::invalid_argument("tagged worker command expected");
                frame.erase(0,1);
            }
            const auto command=json::parse(frame);
            const auto seq=number<std::uint64_t>(command,"sequence");
            if (seq!=++expected_sequence)
                throw std::invalid_argument("worker IPC repeated/out-of-order sequence");
            if (number<std::uint32_t>(command,"stack_id")!=stack_id)
                throw std::invalid_argument("worker stack identity mismatch");
            const auto type=command.at("type").get<std::string>();
            ++ipc_commands;
            if (type=="RESERVE") ++reserve_commands;
            else if (type=="SUBMIT") ++submit_commands;
            else if (type=="CONSUME") ++consume_commands;
            else if (type=="IS_ACTIVE") ++is_active_commands;
            if (snapshot_next && type!="READY_PAGE")
                throw std::invalid_argument("unfinished ready snapshot page sequence");
            if (reservation && type!="SUBMIT" && type!="RELEASE")
                throw std::invalid_argument("unfinished worker admission reservation");
            json reply={{"sequence",seq},{"stack_id",stack_id},{"ok",true}};
            try {
                if (type=="HELLO") {
                    reply["protocol"]=1;
                    reply["modules"]=profile.layout.host_channels;
                    reply["stack_capacity_bytes"]=profile.media.capacity_bytes;
                    if(command.contains("compact_consume_version") || command.contains("compact_consume_max_count")) {
                        if(!command.contains("compact_consume_version") || !command.contains("compact_consume_max_count") ||
                           number<std::uint32_t>(command,"compact_consume_version")!=compact_consume_v1::version ||
                           number<std::uint32_t>(command,"compact_consume_max_count")!=compact_consume_v1::max_count ||
                           local_transport || !shm_available || !independent_profile ||
                           !command.contains("typed_rpc_version") || !command.contains("shm_rpc_version"))
                            throw std::invalid_argument("unsupported compact Consume capability");
                        if(profile.layout.host_channels==16) {
                            reply["compact_consume_version"]=compact_consume_v1::version;
                            reply["compact_consume_max_count"]=compact_consume_v1::max_count;
                        }
                    }
                    if (command.contains("ordered_admission_version") ||
                        command.contains("ordered_admission_max_count")) {
                        if (!command.contains("ordered_admission_version") ||
                            !command.contains("ordered_admission_max_count") ||
                            number<std::uint32_t>(command,"ordered_admission_version")!=ordered_admission_v1::version ||
                            number<std::uint32_t>(command,"ordered_admission_max_count")!=ordered_admission_v1::max_count ||
                            local_transport || !shm_available || !independent_profile ||
                            !command.contains("typed_rpc_version") || !command.contains("shm_rpc_version"))
                            throw std::invalid_argument("unsupported ordered admission capability");
                        if (profile.layout.host_channels==ordered_admission_v1::modules) {
                            reply["ordered_admission_version"]=ordered_admission_v1::version;
                            reply["ordered_admission_max_count"]=ordered_admission_v1::max_count;
                        }
                    }
                    if (command.contains("reserve_submit_single_version")) {
                        if (number<std::uint32_t>(command,"reserve_submit_single_version")!=
                                reserve_submit_single_v1::version || local_transport ||
                            !shm_available || !command.contains("typed_rpc_version") ||
                            !command.contains("shm_rpc_version"))
                            throw std::invalid_argument("unsupported single-child admission capability");
                        reply["reserve_submit_single_version"]=reserve_submit_single_v1::version;
                    }
                    if (command.contains("next_event_snapshot_version")) {
                        if (number<std::uint32_t>(command,"next_event_snapshot_version")!=
                                next_event_snapshot_v1::version ||
                            !command.contains("typed_rpc_version"))
                            throw std::invalid_argument("unsupported next-event snapshot version");
                        reply["next_event_snapshot_version"]=next_event_snapshot_v1::version;
                    }
                    if (command.contains("capacity_snapshot_version")) {
                        if (number<std::uint32_t>(command,"capacity_snapshot_version")!=1 ||
                            !command.contains("typed_rpc_version"))
                            throw std::invalid_argument("unsupported capacity snapshot version");
                        // Other profiles stay on the original full RPC path.
                        if (profile.layout.host_channels==16) {
                            reply["capacity_snapshot_version"]=std::uint32_t(1);
                            reply["capacity_limits"]=json::array();
                            for (std::uint32_t m=0;m<profile.layout.host_channels;++m)
                                reply["capacity_limits"].push_back(front.module_capacity(m).second);
                        }
                    }
                    if (command.contains("typed_rpc_version")) {
                        if (number<std::uint32_t>(command,"typed_rpc_version")!=
                            typed_rpc::version)
                            throw std::invalid_argument("unsupported typed worker version");
                        reply["typed_rpc_version"]=typed_rpc::version;
                    }
                if (command.contains("shm_rpc_version")) {
                    if (!shm_available || number<std::uint32_t>(command,
                            "shm_rpc_version")!=shm_rpc::version ||
                        !command.contains("typed_rpc_version"))
                        throw std::invalid_argument("unsupported worker mailbox version");
                    reply["shm_rpc_version"]=shm_rpc::version;
                }
                } else if (type=="BACKING") {
                    const auto module=number<std::uint32_t>(command,"module_id");
                    front.add_backing({number<std::uint64_t>(command,"region_id"),
                        number<std::uint64_t>(command,"address"),
                        number<std::uint64_t>(command,"bytes"),
                        number<std::uint64_t>(command,"canonical_id"),
                        number<std::uint64_t>(command,"canonical_offset"),
                        number<std::uint64_t>(command,"generation"),
                        stack_id,module,
                        number<std::uint64_t>(command,"endpoint_id"),
                        command.at("readable").get<bool>()});
                } else if (type=="RETIRE") {
                    front.retire_backing_generation(
                        number<std::uint64_t>(command,"canonical_id"),
                        number<std::uint64_t>(command,"generation"),
                        number<std::uint32_t>(command,"module_id"),
                        number<std::uint64_t>(command,"endpoint_id"));
                } else if (type=="RESERVE") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    const auto token=number<std::uint64_t>(command,"token");
                    if (!token || (closed && horizon<=*closed) ||
                        horizon<front.current_time_ns() ||
                        !command.at("counts").is_array() ||
                        command.at("counts").size()!=profile.layout.host_channels)
                        throw std::invalid_argument("invalid worker reservation");
                    std::vector<std::uint32_t> counts;
                    for (const auto& item:command.at("counts")) {
                        if (!item.is_number_unsigned() ||
                            item.get<std::uint64_t>()>
                                std::numeric_limits<std::uint32_t>::max())
                            throw std::invalid_argument("invalid reservation count");
                        counts.push_back(item.get<std::uint32_t>());
                    }
                    reply["reserved"]=front.can_reserve(counts);
                    if (reply["reserved"].get<bool>())
                        reservation=Reservation{token,std::move(counts)};
                } else if (type=="RELEASE") {
                    release_stage(number<std::uint64_t>(command,"token"));
                } else if (type=="SUBMIT") {
                    const auto arrival=number<std::uint64_t>(command,"arrival_ns");
                    if (closed && arrival<=*closed)
                        throw std::invalid_argument("input at closed horizon");
                    DeviceRead read{.request_id=number<std::uint64_t>(command,"request_id"),
                        .arrival_ns=arrival,
                        .deadline_ns=number<std::uint64_t>(command,"deadline_ns"),
                        .local_address=number<std::uint64_t>(command,"local_address"),
                        .axi_id=number<std::uint32_t>(command,"axi_id"),
                        .stack_id=stack_id,
                        .module_id=number<std::uint32_t>(command,"module_id"),
                        .endpoint_id=number<std::uint64_t>(command,"endpoint_id"),
                        .bytes=number<std::uint32_t>(command,"bytes"),
                        .operation=number<std::uint32_t>(command,"operation")};
                    if (command.contains("expected_media_page"))
                        read.expected_media_page=number<std::uint64_t>(
                            command,"expected_media_page");
                    if (command.contains("expected_generation"))
                        read.expected_generation=number<std::uint64_t>(
                            command,"expected_generation");
                    if (reservation &&
                        (read.module_id>=reservation->remaining.size() ||
                         reservation->remaining[read.module_id]==0))
                        throw std::invalid_argument("child outside reserved module count");
                    if (command.contains("original_local_address")) {
                        const PacketBackingProof proof{
                            number<std::uint64_t>(command,"original_local_address"),
                            number<std::uint32_t>(command,"original_bytes"),
                            number<std::uint64_t>(command,"expected_canonical_id"),
                            number<std::uint64_t>(command,"packet_generation")};
                        reply["accepted"]=front.try_submit_packet(read,proof);
                    } else reply["accepted"]=front.try_submit(read);
                    if (reply["accepted"].get<bool>()) {
                        dirty=true;
                        if (reservation) {
                            --reservation->remaining[read.module_id];
                            bool done=true;
                            for (const auto count:reservation->remaining)
                                if (count) done=false;
                            if (done) reservation.reset();
                        }
                    }
                } else if (type=="CANCEL") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    if ((closed && horizon<=*closed) ||
                        horizon!=front.current_time_ns())
                        throw std::invalid_argument("cancel outside open horizon");
                    front.cancel(number<std::uint64_t>(command,"request_id"));
                    dirty=true;
                } else if (type=="ADVANCE") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    if (closed && horizon<=*closed)
                        throw std::invalid_argument("advance at closed horizon");
                    front.advance_until(horizon);
                    ++advance_commands;
                    snapshot_total=front.ready_ids().size();
                    reply["ready"]=ready_snapshot(front,0,16);
                    reply["ready_total"]=snapshot_total;
                    if (snapshot_total>16) snapshot_next=16;
                    reply["current_time_ns"]=front.current_time_ns();
                    dirty=false;
                } else if (type=="CLOSE_ADVANCE") {
                    const auto from=number<std::uint64_t>(command,"from_horizon");
                    const auto to=number<std::uint64_t>(command,"to_horizon");
                    if (dirty || front.current_time_ns()!=from ||
                        (closed && from<=*closed) || to<=from)
                        throw std::invalid_argument("invalid close-and-advance horizon");
                    closed=from;
                    ++close_advance_commands;
                    front.advance_until(to);
                    snapshot_total=front.ready_ids().size();
                    reply["ready"]=ready_snapshot(front,0,16);
                    reply["ready_total"]=snapshot_total;
                    if (snapshot_total>16) snapshot_next=16;
                    reply["current_time_ns"]=front.current_time_ns();
                    dirty=false;
                } else if (type=="PEEK_EVENT") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    if (dirty || horizon!=front.current_time_ns() ||
                        (closed && horizon<=*closed))
                        throw std::invalid_argument("event peek outside quiescent open horizon");
                    const auto peek=front.next_event();
                    reply["supported"]=peek.supported;
                    reply["next_event_ns"]=peek.next_ns ?
                        json(*peek.next_ns) : json(nullptr);
                    ++peek_commands;
                } else if (type=="READY_PAGE") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    const auto offset=number<std::size_t>(command,"offset");
                    if (!snapshot_next || offset!=*snapshot_next ||
                        horizon!=front.current_time_ns() ||
                        front.ready_ids().size()!=snapshot_total)
                        throw std::invalid_argument("ready snapshot page changed");
                    reply["ready"]=ready_snapshot(front,offset,16);
                    reply["ready_total"]=snapshot_total;
                    snapshot_next=offset+reply["ready"].size();
                    if (*snapshot_next>=snapshot_total) snapshot_next.reset();
                } else if (type=="CONSUME") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    if ((closed && horizon<=*closed) ||
                        horizon!=front.current_time_ns())
                        throw std::invalid_argument("consume outside open horizon");
                    const auto result=front.consume_completion(
                        number<std::uint64_t>(command,"request_id"));
                    dirty=true;
                    reply["module_id"]=result.request.module_id;
                    reply["result"]=static_cast<int>(result.result);
                    reply["ar_delivered_ns"]=result.ar_delivered_ns;
                    reply["media_ready_ns"]=result.media_ready_ns;
                    reply["response_delivered_ns"]=result.response_delivered_ns;
                    reply["consumed_ns"]=result.consumed_ns;
                    reply["axi_payload_bytes"]=result.axi_payload_bytes;
                } else if (type=="IS_ACTIVE") {
                    reply["active"]=front.is_active(
                        number<std::uint64_t>(command,"request_id"));
                } else if (type=="CLOSE") {
                    const auto horizon=number<std::uint64_t>(command,"horizon");
                    if (dirty || front.current_time_ns()!=horizon ||
                        (closed && horizon<=*closed))
                        throw std::invalid_argument("non-quiescent or repeated close");
                    closed=horizon;
                    ++close_commands;
                } else if (type=="STATS") {
                    const auto& c=front.counters();
                    rusage usage{};
                    (void)::getrusage(RUSAGE_SELF,&usage);
                    reply["accepted"]=c.accepted;
                    reply["process_id"]=local_transport ? 0 :
                        static_cast<std::uint64_t>(::getpid());
                    reply["transport"]=local_transport ? "local" : "process";
                    reply["physical_ipc_send_count"]=local_transport ? 0 : ipc_commands;
                    reply["physical_ipc_receive_count"]=local_transport ? 0 : ipc_commands;
                    reply["succeeded"]=c.succeeded;
                    reply["cancelled"]=c.cancelled;
                    reply["failed"]=c.failed;
                    reply["native_commands"]=c.native_commands;
                    reply["media_submit_bytes"]=c.media_submit_bytes;
                    reply["max_rss_kib"]=usage.ru_maxrss;
                    reply["physical_outstanding"]=front.outstanding();
                    reply["current_time_ns"]=front.current_time_ns();
                    reply["ipc_commands"]=ipc_commands;
                    reply["advance_commands"]=advance_commands;
                    reply["peek_commands"]=peek_commands;
                    reply["close_commands"]=close_commands;
                    reply["close_advance_commands"]=close_advance_commands;
                    reply["reserve_commands"]=reserve_commands;
                    reply["submit_commands"]=submit_commands;
                    reply["consume_commands"]=consume_commands;
                    reply["is_active_commands"]=is_active_commands;
                    reply["module_links"]=json::array();
                    for (std::uint32_t m=0;m<profile.layout.host_channels;++m)
                        reply["module_links"].push_back({
                            {"module_id",m},
                            {"ar_wire_bytes",front.link(m).counters(
                                Direction::Request).wire_bytes},
                            {"r_wire_bytes",front.link(m).counters(
                                Direction::Return).wire_bytes},
                            {"ar_credit_granules",front.link(m).credits(
                                Direction::Request)},
                            {"r_credit_granules",front.link(m).credits(
                                Direction::Return)}});
                } else if (type=="EVIDENCE") {
                    const auto offset=number<std::size_t>(command,"offset");
                    const auto count=number<std::size_t>(command,"count");
                    if (count==0 || count>16)
                        throw std::invalid_argument("worker evidence page size must be 1..16");
                    reply["records"]=json::array();
                    if (command.at("kind")=="native") {
                        const auto& records=front.native_proofs();
                        reply["total"]=records.size();
                        for (auto i=offset;i<records.size() && i-offset<count;++i) {
                            const auto& r=records[i];
                            reply["records"].push_back({
                                {"stack_id",r.stack_id},{"module_id",r.module_id},
                                {"group_token",r.group_token},
                                {"host_channel",r.bank.host_channel},
                                {"core_die",r.bank.core_die},{"hbf_bank",r.bank.bank},
                                {"native_channel",r.bank.native_channel},
                                {"native_chip",r.bank.native_chip},
                                {"native_die",r.bank.native_die},
                                {"native_plane",r.bank.native_plane},
                                {"expected_lpa",r.proof.expected_logical_page},
                                {"observed_lpa",r.proof.observed_logical_page},
                                {"media_bytes",r.proof.observed_media_bytes},
                                {"commands",r.proof.issued_commands},
                                {"phase_events",r.proof.phase_events},
                                {"first_issued_ns",r.proof.first_issued_ns},
                                {"last_issued_ns",r.proof.last_issued_ns},
                                {"media_begin_ns",r.proof.first_media_begin_ns},
                                {"media_end_ns",r.proof.last_media_end_ns},
                                {"lpa_match",r.proof.logical_page_match},
                                {"bank_match",r.proof.bank_match},
                                {"observer_failed",r.proof.observer_failed}});
                        }
                    } else if (command.at("kind")=="request") {
                        const auto& records=front.request_records();
                        reply["total"]=records.size();
                        for (auto i=offset;i<records.size() && i-offset<count;++i) {
                            const auto& r=records[i];
                            reply["records"].push_back({
                                {"request_id",r.request_id},
                                {"transport_id",r.transport_id},
                                {"group_token",r.group_token},
                                {"canonical_id",r.canonical_id},
                                {"generation",r.generation},
                                {"stack_id",r.stack_id},{"module_id",r.module_id},
                                {"endpoint_id",r.endpoint_id},
                                {"canonical_page",r.canonical_page},
                                {"media_lpa",r.media_lpa},
                                {"accepted_ns",r.accepted_ns},
                                {"ar_delivered_ns",r.ar_delivered_ns},
                                {"media_ready_ns",r.media_ready_ns},
                                {"r_delivered_ns",r.r_delivered_ns},
                                {"consumed_ns",r.consumed_ns},
                                {"result",static_cast<int>(r.result)}});
                        }
                    } else throw std::invalid_argument("unknown evidence kind");
                } else if (type=="STOP") {
                    if(compact_active) {
                        std::cerr << "COMPACT_CONSUME_HANDLER stack=" << stack_id << " negotiated=" << compact_active
                            << " commands=" << compact_commands << " offered=" << compact_offered
                            << " confirmed=" << compact_confirmed << " terminal=" << compact_terminal;
                        for(std::size_t i=0;i<compact_histogram.size();++i) std::cerr << " k" << i << '=' << compact_histogram[i];
                        for(std::size_t i=0;i<4;++i) std::cerr << " result" << i << '=' << compact_results[i];
                        std::cerr << '\n';
                    }
                    if (ordered_active)
                        std::cerr << "ORDERED_ADMISSION_HANDLER stack=" << stack_id
                            << " negotiated=" << ordered_active << " commands=" << ordered_commands
                            << " reserved=" << ordered_reserved << " denied=" << ordered_denied
                            << " accepted=" << ordered_accepted << " released=" << ordered_released
                            << " terminal=" << ordered_terminal << '\n';
                    if (reserve_submit_active)
                        std::cerr << "RESERVE_SUBMIT_SINGLE_HANDLER stack=" << stack_id
                            << " negotiated=" << reserve_submit_active
                            << " commands=" << fusion_commands << " reserved=" << fusion_reserved
                            << " denied=" << fusion_denied << " accepted=" << fusion_accepted
                            << " released=" << fusion_released << '\n';
                    std::cerr << "WORKER_RPC_COUNTS stack=" << stack_id
                        << " typed=" << typed_active
                        << " ipc=" << ipc_commands
                        << " advance=" << advance_commands
                        << " peek=" << peek_commands
                        << " close=" << close_commands
                        << " close_advance=" << close_advance_commands
                        << " reserve=" << reserve_commands
                        << " submit=" << submit_commands
                        << " consume=" << consume_commands
                        << " is_active=" << is_active_commands << '\n';
                    stopped=true;
                    exit_code=0;
                    reply_timeout=std::chrono::seconds(1);
                    return typed_active ?
                        typed_rpc::tagged_json(reply.dump()) : reply.dump();
                } else {
                    throw std::invalid_argument("unknown worker IPC command");
                }
            } catch (const std::exception& error) {
                reply["ok"]=false;
                reply["error"]=error.what();
                stopped=true;
                exit_code=1;
                reply_timeout=std::chrono::seconds(1);
                return typed_active ?
                    typed_rpc::tagged_json(reply.dump()) : reply.dump();
            }
            const auto response=typed_active ?
                typed_rpc::tagged_json(reply.dump()) : reply.dump();
            if(type=="HELLO" && reply.contains("compact_consume_version")) compact_active=true;
            if (type=="HELLO" && reply.contains("ordered_admission_version"))
                ordered_active=true;
            if (type=="HELLO" && reply.contains("reserve_submit_single_version"))
                reserve_submit_active=true;
            if (type=="HELLO" && reply.contains("next_event_snapshot_version"))
                next_event_active=true;
            if (type=="HELLO" && reply.contains("capacity_snapshot_version"))
                capacity_active=true;
            if (type=="HELLO" && command.contains("typed_rpc_version"))
                typed_active=true;
            if (type=="HELLO" && command.contains("shm_rpc_version"))
                shm_active=true;
            return response;
    }
};

WorkerCommandState::WorkerCommandState(const std::filesystem::path& path,
    bool top,std::uint32_t stack,bool shm_available,bool local_transport)
    : impl_(std::make_unique<Impl>(path,top,stack,shm_available,local_transport)) {}
WorkerCommandState::~WorkerCommandState()=default;
std::string WorkerCommandState::handle(std::string frame)
{
    try { return impl_->handle(std::move(frame)); }
    catch (...) {
        // The process endpoint exits on an uncaught protocol error. The
        // in-daemon endpoint must reject every subsequent command as well.
        impl_->stopped=true;
        impl_->exit_code=1;
        impl_->reply_timeout=std::chrono::seconds(1);
        throw;
    }
}
bool WorkerCommandState::typed_active() const noexcept
{ return impl_->typed_active; }
bool WorkerCommandState::shm_active() const noexcept
{ return impl_->shm_active; }
bool WorkerCommandState::stopped() const noexcept
{ return impl_->stopped; }
int WorkerCommandState::exit_code() const noexcept
{ return impl_->exit_code; }
std::chrono::milliseconds WorkerCommandState::reply_timeout() const noexcept
{ return impl_->reply_timeout; }
} // namespace hbfsim::ucie
