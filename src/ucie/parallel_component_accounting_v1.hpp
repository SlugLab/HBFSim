#pragma once
// Private leader-only diagnostic. No public class/header or wire ABI changes.
// One inline state has external ODR linkage across the client/frontend TUs.
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <exception>
#include <fstream>
#include <thread>
#include <json.hpp>
namespace hbfsim::ucie::parallel_component_accounting_v1 {
enum class Method : unsigned {
    TransportAlive,Backing,Retire,Reserve,Release,Submit,Cancel,Advance,
    CloseAdvance,Peek,Consume,IsActive,Close,Stats,NativeEvidence,
    RequestEvidence,Other,CapacityProvesFull,GatherBegin,GatherFinish,ReserveSubmit,ReserveSubmitOrdered,Count
};
enum class Context : unsigned { Other,FixedPoint,Publish,Local,Join,Count };
constexpr unsigned methods=static_cast<unsigned>(Method::Count);
constexpr unsigned contexts=static_cast<unsigned>(Context::Count);
constexpr std::array<const char*,methods> method_names={
    "TransportAlive","Backing","Retire","Reserve","Release","Submit","Cancel",
    "Advance","CloseAdvance","Peek","Consume","IsActive","Close","Stats",
    "NativeEvidence","RequestEvidence","Other","CapacityProvesFull","GatherBegin","GatherFinish","ReserveSubmit","ReserveSubmitOrdered"};
constexpr std::array<const char*,contexts> context_names={"other","fixed_point","publish","local","join"};
struct Counter {
    std::uint64_t calls{},successful_exits{},exceptional_exits{},nested_calls{},
        phase_suppressed_outer_calls{},timed_outer_calls{},elapsed_ns{},max_ns{},false_results{},boolean_results{};
};
struct Aggregate { std::uint64_t calls{},successful_exits{},exceptional_exits{},elapsed_ns{},max_ns{}; };
struct State {
    std::array<std::array<std::array<std::array<Counter,methods>,2>,4>,contexts> rows{};
    std::array<Aggregate,3> phases{};
    Aggregate fixed_point{};
    std::atomic<bool> configured{false},gate_open{false},valid{true};
    std::thread::id owner{};
    bool enabled{},closed{},unsupported_nesting{},unsupported_stack{};
    unsigned rpc_depth{},phase_depth{},fixed_depth{};
    Context context{Context::Other};
    std::uint64_t gate_begin_ns{},gate_end_ns{},marker_clock_reads{},rpc_clock_reads{},
        phase_clock_reads{},fixed_clock_reads{},fixed_phase_ns{};
};
inline State state{};
inline std::uint64_t now_ns() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline bool owner() noexcept {
    if(std::this_thread::get_id()==state.owner)return true;
    state.valid.store(false,std::memory_order_relaxed);return false;
}
inline bool running() noexcept {
    // Acquire the gate publication before reading the stable owner/enabled state.
    if(!state.gate_open.load(std::memory_order_acquire))return false;
    if(!owner())return false;
    return state.enabled;
}
inline void invalid_nesting() noexcept {
    state.unsupported_nesting=true;state.valid.store(false,std::memory_order_relaxed);
}
struct RpcScope {
    bool active{},timed{};unsigned stack{},transport{},method{},context{};
    int exceptions{};std::uint64_t started{};
    RpcScope(unsigned s,bool local,Method m) noexcept {
        if(!running())return;
        if(s>=4){state.unsupported_stack=true;state.valid.store(false,std::memory_order_relaxed);return;}
        active=true;stack=s;transport=local?0:1;method=static_cast<unsigned>(m);
        context=static_cast<unsigned>(state.context);exceptions=std::uncaught_exceptions();
        auto& c=state.rows[context][stack][transport][method];++c.calls;
        const bool outer=state.rpc_depth++==0;
        if(!outer)++c.nested_calls;
        else if(state.phase_depth)++c.phase_suppressed_outer_calls;
        else {timed=true;++c.timed_outer_calls;started=now_ns();++state.rpc_clock_reads;}
    }
    ~RpcScope() noexcept {
        if(!active)return;
        auto& c=state.rows[context][stack][transport][method];
        const bool error=std::uncaught_exceptions()>exceptions;
        if(error){++c.exceptional_exits;state.valid.store(false,std::memory_order_relaxed);}
        else ++c.successful_exits;
        if(timed){const auto elapsed=now_ns()-started;++state.rpc_clock_reads;
            c.elapsed_ns+=elapsed;c.max_ns=std::max(c.max_ns,elapsed);}
        --state.rpc_depth;
    }
    bool boolean_result(bool value) noexcept {
        if(active){auto& row=state.rows[context][stack][transport][method];++row.boolean_results;if(!value)++row.false_results;}
        return value;
    }
};
struct PhaseScope {
    bool active{};unsigned phase{};Context previous{};int exceptions{};std::uint64_t started{};
    explicit PhaseScope(Context c) noexcept {
        if(!running())return;
        if(c<Context::Publish || c>Context::Join || state.phase_depth || state.rpc_depth){invalid_nesting();return;}
        active=true;phase=static_cast<unsigned>(c)-static_cast<unsigned>(Context::Publish);
        previous=state.context;state.context=c;++state.phase_depth;
        ++state.phases[phase].calls;exceptions=std::uncaught_exceptions();
        started=now_ns();++state.phase_clock_reads;
    }
    ~PhaseScope() noexcept {
        if(!active)return;
        const auto elapsed=now_ns()-started;++state.phase_clock_reads;
        if(state.fixed_depth)state.fixed_phase_ns+=elapsed;
        auto& c=state.phases[phase];c.elapsed_ns+=elapsed;c.max_ns=std::max(c.max_ns,elapsed);
        if(std::uncaught_exceptions()>exceptions){++c.exceptional_exits;state.valid.store(false,std::memory_order_relaxed);}
        else ++c.successful_exits;
        --state.phase_depth;state.context=previous;
    }
};
struct FixedPointScope {
    bool active{};Context previous{};int exceptions{};std::uint64_t started{};
    FixedPointScope() noexcept {
        if(!running())return;
        if(state.phase_depth||state.fixed_depth||state.rpc_depth){invalid_nesting();return;}
        active=true;previous=state.context;state.context=Context::FixedPoint;++state.fixed_depth;
        ++state.fixed_point.calls;exceptions=std::uncaught_exceptions();started=now_ns();++state.fixed_clock_reads;
    }
    ~FixedPointScope() noexcept {
        if(!active)return;
        const auto elapsed=now_ns()-started;++state.fixed_clock_reads;
        auto& c=state.fixed_point;c.elapsed_ns+=elapsed;c.max_ns=std::max(c.max_ns,elapsed);
        if(std::uncaught_exceptions()>exceptions){++c.exceptional_exits;state.valid.store(false,std::memory_order_relaxed);}
        else ++c.successful_exits;
        --state.fixed_depth;state.context=previous;
    }
};

struct Envelopes {
    unsigned request_depth{},backend_depth{};
    std::uint64_t clock_reads{},request_calls{},backend_calls{},request_errors{},backend_errors{};
    std::uint64_t adapter_pending{},adapter_order{},adapter_staged{},adapter_pending_max{},adapter_order_max{},adapter_staged_max{};
    std::array<std::array<std::uint64_t,4>,4> consume_results{};
    std::uint64_t request_ns{},backend_ns{},parent_current{},parent_max{},child_current{},child_max{};
    std::array<std::uint64_t,4> physical_send_attempts{},physical_receive_attempts{},physical_send_success{},physical_receive_success{};
};
inline Envelopes envelopes{};
struct RequestScope {
    bool entered{},timed{}; std::uint64_t start{};int exceptions{};
    RequestScope() noexcept {
        if(!running())return;
        entered=true;timed=envelopes.request_depth++==0;exceptions=std::uncaught_exceptions();
        if(timed){++envelopes.request_calls;start=now_ns();++envelopes.clock_reads;}
    }
    ~RequestScope() noexcept {
        if(!entered)return;
        if(timed){envelopes.request_ns+=now_ns()-start;++envelopes.clock_reads;
            if(std::uncaught_exceptions()>exceptions){++envelopes.request_errors;state.valid.store(false);}}
        --envelopes.request_depth;
    }
};
struct BackendScope {
    bool entered{},timed{};std::uint64_t start{};int exceptions{};
    BackendScope() noexcept {
        if(!running())return;
        entered=true;timed=envelopes.backend_depth++==0;exceptions=std::uncaught_exceptions();
        if(timed){++envelopes.backend_calls;start=now_ns();++envelopes.clock_reads;}
    }
    ~BackendScope() noexcept {
        if(!entered)return;
        if(timed){envelopes.backend_ns+=now_ns()-start;++envelopes.clock_reads;
            if(std::uncaught_exceptions()>exceptions){++envelopes.backend_errors;state.valid.store(false);}}
        --envelopes.backend_depth;
    }
};
inline void adapter_state(std::uint64_t pending,std::uint64_t order,std::uint64_t staged) noexcept {
    if(!running())return;
    envelopes.adapter_pending=pending;envelopes.adapter_order=order;envelopes.adapter_staged=staged;
    envelopes.adapter_pending_max=std::max(envelopes.adapter_pending_max,pending);
    envelopes.adapter_order_max=std::max(envelopes.adapter_order_max,order);
    envelopes.adapter_staged_max=std::max(envelopes.adapter_staged_max,staged);
}
inline void pending(std::uint64_t parents,std::uint64_t children) noexcept {
    if(!running())return;
    envelopes.parent_current=parents;envelopes.child_current=children;
    envelopes.parent_max=std::max(envelopes.parent_max,parents);
    envelopes.child_max=std::max(envelopes.child_max,children);
}
struct PhysicalScope {
    bool active{},send{};unsigned stack{};int exceptions{};
    PhysicalScope(unsigned s,bool physical,bool sending) noexcept {
        if(!physical||!running())return;
        if(s>=4){state.valid.store(false);return;}
        active=true;send=sending;stack=s;exceptions=std::uncaught_exceptions();
        ++(send?envelopes.physical_send_attempts:envelopes.physical_receive_attempts)[stack];
    }
    ~PhysicalScope() noexcept {if(active){
        if(std::uncaught_exceptions()==exceptions)
            ++(send?envelopes.physical_send_success:envelopes.physical_receive_success)[stack];
        else state.valid.store(false);}}
};
inline int begin(int enabled) noexcept {
    // Lifecycle is single-owner, with no concurrent begin/end or in-flight caller.
    if(enabled!=0&&enabled!=1)return 1;
    if(state.configured.load(std::memory_order_acquire)&&!owner())return 1;
    if(state.gate_open.load(std::memory_order_acquire)||state.rpc_depth||state.phase_depth||state.fixed_depth||envelopes.request_depth||envelopes.backend_depth)return 1;
    envelopes={};state.rows={};state.phases={};state.fixed_point={};
    state.valid.store(true,std::memory_order_relaxed);state.unsupported_nesting=false;state.unsupported_stack=false;
    state.owner=std::this_thread::get_id();state.enabled=enabled!=0;state.closed=false;
    state.context=Context::Other;state.gate_end_ns=0;state.marker_clock_reads=1;
    state.fixed_phase_ns=0;state.rpc_clock_reads=state.phase_clock_reads=state.fixed_clock_reads=0;
    state.gate_begin_ns=now_ns();state.configured.store(true,std::memory_order_release);
    state.gate_open.store(true,std::memory_order_release);return 0;
}
inline int end() noexcept {
    if(!state.configured.load(std::memory_order_acquire)||!owner())return 1;
    if(!state.gate_open.load(std::memory_order_acquire))return 1;
    if(state.rpc_depth||state.phase_depth||state.fixed_depth||envelopes.request_depth||envelopes.backend_depth){state.valid.store(false,std::memory_order_relaxed);return 1;}
    state.gate_end_ns=now_ns();++state.marker_clock_reads;state.closed=true;
    state.gate_open.store(false,std::memory_order_release);return 0;
}
inline int write_report(const char* path) noexcept {
    if(!state.configured.load(std::memory_order_acquire)||!owner())return 1;
    if(!path||state.gate_open.load(std::memory_order_acquire)||!state.closed)return 1;
    try {
        bool valid=state.valid.load(std::memory_order_relaxed)&&!state.unsupported_nesting&&!state.unsupported_stack;
        using Wide=unsigned __int128;
        nlohmann::json rows=nlohmann::json::array();Wide rpc_total=0,fixed_rpc=0;std::uint64_t timed=0;
        std::uint64_t consume_success=0,consume_exceptional=0,is_active_calls=0;
        for(unsigned ctx=0;ctx<contexts;++ctx)for(unsigned s=0;s<4;++s)for(unsigned t=0;t<2;++t)for(unsigned m=0;m<methods;++m){
            const auto& c=state.rows[ctx][s][t][m];
            valid=valid&&c.calls==c.successful_exits+c.exceptional_exits&&
                c.calls==c.nested_calls+c.phase_suppressed_outer_calls+c.timed_outer_calls&&c.false_results<=c.boolean_results&&c.boolean_results<=c.successful_exits;
            if(m==static_cast<unsigned>(Method::IsActive))valid=valid&&c.boolean_results==c.successful_exits;
            if(ctx>=static_cast<unsigned>(Context::Publish))valid=valid&&c.elapsed_ns==0&&c.timed_outer_calls==0;
            rpc_total+=c.elapsed_ns;timed+=c.timed_outer_calls;
            if(ctx==static_cast<unsigned>(Context::FixedPoint))fixed_rpc+=c.elapsed_ns;
            if(m==static_cast<unsigned>(Method::Consume)){consume_success+=c.successful_exits;consume_exceptional+=c.exceptional_exits;}
            if(m==static_cast<unsigned>(Method::IsActive))is_active_calls+=c.calls;
            rows.push_back({{"context",context_names[ctx]},{"stack",s},{"transport",t==0?"local":"remote"},
                {"method",method_names[m]},{"calls",c.calls},{"successful_exits",c.successful_exits},
                {"exceptional_exits",c.exceptional_exits},{"nested_calls",c.nested_calls},
                {"phase_suppressed_outer_calls",c.phase_suppressed_outer_calls},{"timed_outer_calls",c.timed_outer_calls},
                {"elapsed_ns",c.elapsed_ns},{"max_ns",c.max_ns},{"false_results",c.false_results},{"boolean_results_observed",c.boolean_results},{"true_results_observed",c.boolean_results-c.false_results}});
        }
        Wide phases=0;std::uint64_t phase_calls=0;nlohmann::json phase_rows=nlohmann::json::array();
        for(unsigned i=0;i<3;++i){const auto& c=state.phases[i];phases+=c.elapsed_ns;phase_calls+=c.calls;
            valid=valid&&c.calls==c.successful_exits+c.exceptional_exits;
            phase_rows.push_back({{"phase",context_names[i+2]},{"calls",c.calls},{"successful_exits",c.successful_exits},
                {"exceptional_exits",c.exceptional_exits},{"elapsed_ns",c.elapsed_ns},{"max_ns",c.max_ns}});}
        valid=valid&&state.gate_end_ns>=state.gate_begin_ns&&state.marker_clock_reads==2&&
            state.rpc_clock_reads==2*timed&&state.phase_clock_reads==2*phase_calls&&
            state.fixed_clock_reads==2*state.fixed_point.calls;
        const auto gate=state.gate_end_ns-state.gate_begin_ns;
        const Wide fp=state.fixed_point.elapsed_ns;
        valid=valid&&state.fixed_point.calls==state.fixed_point.successful_exits+state.fixed_point.exceptional_exits;
        valid=valid&&fp>=fixed_rpc+state.fixed_phase_ns&&rpc_total>=fixed_rpc&&fp+rpc_total-fixed_rpc+phases-state.fixed_phase_ns<=gate;
        nlohmann::json additive=nullptr;
        if(state.enabled&&valid){
            const auto fp_remaining=static_cast<std::uint64_t>(fp-fixed_rpc-state.fixed_phase_ns);
            const auto other_remaining=static_cast<std::uint64_t>(Wide(gate)-phases-rpc_total-fp_remaining);
            additive={{"gather_publish_ns",state.phases[0].elapsed_ns},{"gather_local_ns",state.phases[1].elapsed_ns},
                {"gather_join_ns",state.phases[2].elapsed_ns},{"outside_gather_outer_rpc_ns",static_cast<std::uint64_t>(rpc_total)},
                {"fixed_point_non_rpc_remainder_ns",fp_remaining},{"other_leader_remainder_ns",other_remaining},
                {"sum_leaves_ns",gate},{"exact_integer_closure",true}};
        }
        if(!state.enabled)valid=valid&&rpc_total==0&&phases==0&&fp==0&&timed==0&&consume_success==0&&is_active_calls==0;
        valid=valid&&envelopes.backend_ns<=envelopes.request_ns&&envelopes.request_ns<=gate;
        nlohmann::json envelope_table=nullptr;
        if(state.enabled&&valid) envelope_table={{"selected_four_frontend_calls_ns",envelopes.backend_ns},
            {"poll_remainder_including_uninstrumented_frontend_ns",envelopes.request_ns-envelopes.backend_ns},
            {"idle_no_poll_remainder_ns",gate-envelopes.request_ns},{"sum_ns",gate}};
        Wide consume_returned_sum=0;
        for(const auto& stack:envelopes.consume_results)
            for(const auto returned:stack)consume_returned_sum+=returned;
        valid=valid&&consume_returned_sum==consume_success;
        valid=valid&&envelopes.clock_reads==2*(envelopes.request_calls+envelopes.backend_calls)&&rows.size()==840;
        if(!valid){additive=nullptr;envelope_table=nullptr;}
        nlohmann::json report={{"envelope_clock_reads",envelopes.clock_reads},
            {"request_calls",envelopes.request_calls},{"backend_calls",envelopes.backend_calls},
            {"request_exceptional_exits",envelopes.request_errors},{"backend_exceptional_exits",envelopes.backend_errors},
            {"fixed_phase_ns",state.fixed_phase_ns},{"row_count",rows.size()},
            {"adapter_pending_current",envelopes.adapter_pending},{"adapter_pending_observed_max",envelopes.adapter_pending_max},
            {"adapter_order_current",envelopes.adapter_order},{"adapter_order_observed_max",envelopes.adapter_order_max},
            {"adapter_staged_current",envelopes.adapter_staged},{"adapter_staged_observed_max",envelopes.adapter_staged_max},
            {"consume_result_enum_counts",envelopes.consume_results},{"consume_result_enum_labels",{"Ready","BackendError","Cancelled","TimedOut"}},
            {"repeated_same_id_false", "NOT_COLLECTED_NO_EXISTING_BOUNDED_SEEN_STATE"},
            {"backend_envelope_labels",{"try_submit","advance_until","next_event_ns","consume_completion"}},
            {"request_envelopes",envelope_table},
            {"parent_pending_current",envelopes.parent_current},{"parent_pending_observed_max",envelopes.parent_max},
            {"child_pending_current",envelopes.child_current},{"child_pending_observed_max",envelopes.child_max},
            {"physical_send_attempts",envelopes.physical_send_attempts},
            {"physical_receive_attempts",envelopes.physical_receive_attempts},
            {"physical_send_success",envelopes.physical_send_success},
            {"physical_receive_success",envelopes.physical_receive_success},{"schema","hbfsim.ucie.parallel_component_accounting.v1"},
            {"status",valid?(state.enabled?"VALID_SINGLE_OWNER_COMPONENT_REPORT":"DISABLED_BY_PLAN"):"INVALID_OBSERVER_REPORT"},
            {"enabled",state.enabled},{"gate_begin_ns",state.gate_begin_ns},{"gate_end_ns",state.gate_end_ns},{"gate_elapsed_ns",gate},
            {"marker_clock_reads",state.marker_clock_reads},{"rpc_clock_reads",state.rpc_clock_reads},
            {"phase_clock_reads",state.phase_clock_reads},{"fixed_point_clock_reads",state.fixed_clock_reads},
            {"rows",rows},{"phases",phase_rows},{"fixed_point_inclusive_ns",state.fixed_point.elapsed_ns},
            {"fixed_point_invocations",state.fixed_point.calls},{"fixed_point_successful_exits",state.fixed_point.successful_exits},
            {"fixed_point_exceptional_exits",state.fixed_point.exceptional_exits},{"fixed_point_view_overlaps_rpc_leaves",true},
            {"additive_table",additive},{"successful_individual_Consume_commands",consume_success},
            {"successful_Consume_returned_enum_sum",static_cast<std::uint64_t>(consume_returned_sum)},
            {"exceptional_Consume_exits",consume_exceptional},
            {"Consume_returned_enum_closure",consume_returned_sum==consume_success},
            {"Consume_result_scope","successful returned Ready/BackendError/Cancelled/TimedOut enum; exceptions separate; not physical retirement proof"},{"IsActive_calls",is_active_calls},
            {"unsupported_nesting",state.unsupported_nesting},{"unsupported_stack",state.unsupported_stack},
            {"scope","leader elapsed; request plus physical-tail gate; gather phase RPC counts only; fixed-point inclusive drilldown; remainders include timing bookkeeping and caller work, not pure CPU"},
            {"clock","std::chrono::steady_clock; fixture verifies CLOCK_MONOTONIC bracket"},
            {"batching","not adopted; ordinary Consume only; no Op9 or batch bucket"}};
        std::ofstream out(path);out<<report.dump(2)<<'\n';out.flush();if(!out)return 1;out.close();return out&&valid?0:1;
    }catch(...){return 1;}
}
}
