#include <hbfsim/ucie/multistack_frontend.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using hbfsim::ucie::DeviceResult;
using hbfsim::ucie::GlobalRead;
using hbfsim::ucie::MultistackFrontend;

std::uint64_t elapsed_us(Clock::time_point begin, Clock::time_point end)
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(end-begin).count());
}

struct Ipc {
    std::uint64_t total{};
    std::uint64_t advance{};
    std::uint64_t peek{};
    std::uint64_t close{};
};

Ipc ipc(MultistackFrontend& front)
{
    Ipc result;
    for (const auto& stats:front.accounting().stacks) {
        result.total+=stats.ipc_commands;
        result.advance+=stats.advance_commands;
        result.peek+=stats.peek_commands;
        result.close+=stats.close_commands;
    }
    return result;
}

Ipc difference(Ipc later,Ipc earlier)
{
    return {later.total-earlier.total,later.advance-earlier.advance,
            later.peek-earlier.peek,later.close-earlier.close};
}

void service(MultistackFrontend& front,std::uint64_t id)
{
    constexpr std::uint64_t page_bytes=16'384;
    const auto arrival=front.current_time_ns();
    if (!front.try_submit(GlobalRead{
            .request_id=id,.arrival_ns=arrival,
            .deadline_ns=arrival+10'000'000,
            .address=(id-1)*page_bytes,.bytes=page_bytes,
            .endpoint_id=7,.axi_id=static_cast<std::uint32_t>(id)})) {
        throw std::runtime_error("16KiB request rejected");
    }
    for (std::uint64_t step=0;step<1'000'000;++step) {
        if (auto ready=front.peek_completion(id)) {
            const auto completion=front.consume_completion(id);
            if (completion.result!=DeviceResult::Ready ||
                completion.original_bytes!=page_bytes ||
                completion.child_count!=256) {
                throw std::runtime_error("16KiB request completion mismatch");
            }
            return;
        }
        const auto next=front.next_event_ns();
        if (!next || *next<front.current_time_ns())
            throw std::runtime_error("no causal next event for accepted request");
        front.advance_until(*next);
    }
    throw std::runtime_error("event step limit exceeded");
}

void service_batch(MultistackFrontend& front,std::uint64_t first,
                   std::uint64_t count)
{
    constexpr std::uint64_t page_bytes=16'384;
    const auto arrival=front.current_time_ns();
    for (std::uint64_t id=first;id<first+count;++id) {
        if (!front.try_submit(GlobalRead{
                .request_id=id,.arrival_ns=arrival,
                .deadline_ns=arrival+10'000'000,
                .address=(id-1)*page_bytes,.bytes=page_bytes,
                .endpoint_id=7,.axi_id=static_cast<std::uint32_t>(id)})) {
            throw std::runtime_error("parallel 16KiB request rejected");
        }
    }
    std::uint64_t completed=0;
    for (std::uint64_t step=0;step<1'000'000;++step) {
        const auto ready=front.ready_ids();
        for (const auto id:ready) {
            if (id<first || id>=first+count)
                throw std::runtime_error("unexpected ready request");
            const auto completion=front.consume_completion(id);
            if (completion.result!=DeviceResult::Ready ||
                completion.original_bytes!=page_bytes ||
                completion.child_count!=256) {
                throw std::runtime_error("parallel 16KiB completion mismatch");
            }
            ++completed;
        }
        if (completed==count) return;
        const auto next=front.next_event_ns();
        if (!next || *next<front.current_time_ns())
            throw std::runtime_error("no causal next event for batch");
        front.advance_until(*next);
    }
    throw std::runtime_error("batch event step limit exceeded");
}
} // namespace

int main(int argc,char** argv)
{
    if (argc!=4 && argc!=5) {
        std::cerr << "usage: ucie_16k_sustained WORKER TOP_PROFILE COUNT [WINDOW]\n";
        return 2;
    }
    try {
        const auto count=std::stoull(argv[3]);
        if (count<2 || count>16) throw std::invalid_argument("count must be 2..16");
        const auto window=argc==5?std::stoull(argv[4]):1;
        if (window!=1 && window!=4)
            throw std::invalid_argument("window must be 1 or 4");
        const auto start=Clock::now();
        MultistackFrontend front(argv[2],argv[1]);
        const auto initialized=Clock::now();
        front.add_backing({1,0,(count+1)*16'384,1,0,1,7,true});
        const auto registered=Clock::now();
        const auto base_ipc=ipc(front);
        std::vector<std::uint64_t> request_us;
        request_us.reserve((count+window-1)/window);
        for (std::uint64_t id=1;id<=count;id+=window) {
            const auto begin=Clock::now();
            if (window==1) service(front,id);
            else service_batch(front,id,std::min(window,count-id+1));
            const auto end=Clock::now();
            request_us.push_back(elapsed_us(begin,end));
        }
        const auto final_ipc=ipc(front);
        const auto accounting=front.accounting();
        if (accounting.host.succeeded!=count ||
            accounting.host.caller_outstanding!=0 ||
            accounting.host.original_bytes!=count*16'384 ||
            accounting.host.axi_payload_bytes!=count*16'384 ||
            accounting.native_commands!=count*4 ||
            accounting.media_submit_bytes!=count*16'384) {
            throw std::runtime_error("sustained 16KiB accounting mismatch");
        }
        const auto calls=difference(final_ipc,base_ipc);
        std::cout << "setup_us=" << elapsed_us(start,initialized)
                  << " registration_us=" << elapsed_us(initialized,registered)
                  << " count=" << count << " window=" << window
                  << " group_us=";
        for (std::size_t i=0;i<request_us.size();++i)
            std::cout << (i?",":"") << request_us[i];
        std::cout << " first_us=" << request_us.front()
                  << " subsequent_sum_us=";
        std::uint64_t subsequent=0;
        for (std::size_t i=1;i<request_us.size();++i) subsequent+=request_us[i];
        std::cout << subsequent
                  << " ipc_total=" << calls.total
                  << " ipc_advance=" << calls.advance
                  << " ipc_peek=" << calls.peek
                  << " ipc_close=" << calls.close
                  << " native=" << accounting.native_commands
                  << " media_bytes=" << accounting.media_submit_bytes
                  << " final_sim_ns=" << front.current_time_ns() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
