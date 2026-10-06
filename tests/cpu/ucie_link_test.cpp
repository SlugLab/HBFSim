#include <hbfsim/ucie/link.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {
void require(bool yes, const char* reason)
{
    if (!yes) throw std::runtime_error(reason);
}
hbfsim::ucie::LinkProfile profile()
{
    hbfsim::ucie::LinkProfile p;
    p.name="oracle";
    p.local_capacity_bytes=16ULL<<30;
    p.initial_ar_granules=128;
    p.initial_r_granules=128;
    p.max_accepted=128;
    return p;
}
std::uint64_t drain(hbfsim::ucie::StreamingLink& link)
{
    std::uint64_t delivered=0;
    for (unsigned steps=0; steps<1000 && link.next_event_ns(); ++steps) {
        for (const auto& event: link.step()) {
            ++delivered;
            link.consume(event.direction,event.request_id,event.delivered_ns);
        }
    }
    require(!link.next_event_ns(), "link did not quiesce");
    return delivered;
}
}

int main()
{
    using namespace hbfsim::ucie;
    auto p=profile();
    require(flit_serialization_ns(p)==2, "x64 Grade2 wire serialization");
    for (const auto [grade, ns] : {std::pair{8U,4ULL}, {16U,2ULL}, {32U,1ULL}}) {
        p.gt_per_second=grade;
        require(flit_serialization_ns(p)==ns, "specified grade timing");
    }
    p.gt_per_second=16;
    {
        StreamingLink link(p);
        for (std::uint64_t id=1; id<=16; ++id) link.enqueue(Direction::Request,id,0);
        drain(link);
        const auto& c=link.counters(Direction::Request);
        // Oracle: 16 * 15 B = one 240 B AoU message area.
        require(c.flits==1 && c.wire_bytes==256 && c.aou_message_bytes==240 &&
                c.unused_message_bytes==0 && c.aou_header_bytes==10 &&
                c.ucie_header_bytes==2 && c.crc_position_bytes==4,
                "16 AR messages do not fill exactly one Format 6 flit");
    }
    {
        StreamingLink link(p);
        for (std::uint64_t id=1; id<=17; ++id) link.enqueue(Direction::Request,id,0);
        drain(link);
        const auto& c=link.counters(Direction::Request);
        // Oracle: 255 message + 225 unused + 20 AoU + 12 UCIe = 512 B.
        require(c.flits==2 && c.wire_bytes==512 && c.aou_message_bytes==255 &&
                c.unused_message_bytes==225 && c.aou_header_bytes==20 &&
                c.ucie_header_bytes+c.crc_position_bytes==12,
                "17 AR message byte accounting");
    }
    {
        StreamingLink link(p);
        for (std::uint64_t id=1; id<=4; ++id) link.enqueue(Direction::Return,id,0);
        drain(link);
        const auto& c=link.counters(Direction::Return);
        // Oracle: 4 * 70 B = 280 B across two 240 B message areas.
        require(c.flits==2 && c.wire_bytes==512 && c.aou_message_bytes==280 &&
                c.unused_message_bytes==200, "R message cross-flit accounting");
        require(link.flits().at(0).fragments.back().request_id==4 &&
                !link.flits().at(0).fragments.back().msg_end &&
                link.flits().at(1).fragments.front().request_id==4 &&
                link.flits().at(1).fragments.front().message_granule==6 &&
                link.flits().at(1).fragments.front().msg_end,
                "cross-flit MsgStart/fragment offsets were not preserved");
    }
    {
        p.initial_ar_granules=3;
        p.initial_r_granules=14;
        StreamingLink link(p);
        link.enqueue(Direction::Request,1,0);
        drain(link);
        const auto& reverse=link.counters(Direction::Return);
        // Released 3 credits cannot be encoded as a single 3-bit grant.
        require(reverse.credit_only_flits==3 && reverse.wire_bytes==768 &&
                reverse.aou_message_bytes==0 && reverse.unused_message_bytes==720 &&
                link.credits(Direction::Request)==3,
                "3-granule credit return must use three legal unit grants");
        for (const auto& f: link.flits())
            require(f.grant_granules==0 || f.grant_granules==1 ||
                    f.grant_granules==4 ||
                    f.grant_granules==8 || f.grant_granules==16 ||
                    f.grant_granules==32 || f.grant_granules==64 ||
                    f.grant_granules==128, "illegal MsgCredit grant");
    }
    {
        p.max_accepted=2;
        StreamingLink link(p);
        link.enqueue(Direction::Return,1,0);
        link.enqueue(Direction::Return,2,0);
        require(link.step().empty(),"return data delivered before serialization");
        const auto first=link.step();
        require(first.size()==1 && first[0].request_id==1 &&
                first[0].delivered_ns==2,"first return message timing");
        require(!link.next_event_ns(),"unconsumed R generated phantom credit");
        bool duplicate=false;
        try { link.enqueue(Direction::Return,1,2); }
        catch (const std::invalid_argument&) { duplicate=true; }
        require(duplicate,"duplicate active message ID accepted");
        bool bounded=false;
        try { link.enqueue(Direction::Return,3,2); }
        catch (const std::overflow_error&) { bounded=true; }
        require(bounded,"in-flight direction capacity was not enforced");
        link.consume(Direction::Return,1,100); // deliberately slow caller
        std::uint64_t second_delivery=0, granted=0;
        for (unsigned i=0;i<20 && link.next_event_ns();++i) {
            for (const auto& value:link.step()) {
                if (value.request_id==2) second_delivery=value.delivered_ns;
            }
        }
        for (const auto& f:link.flits())
            if (f.direction==Direction::Request) granted+=f.grant_granules;
        require(granted==14 &&
                link.counters(Direction::Request).credit_only_flits==4 &&
                second_delivery>=110,
                "14-granule credit was overgranted or returned before slow consume");
        link.consume(Direction::Return,2,second_delivery);
        link.step(); // process actual consume event; only now does capacity free
        link.enqueue(Direction::Return,3,link.current_time_ns());
    }
    {
        p.gt_per_second=64;
        bool rejected=false;
        try { validate_link_profile(p); }
        catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"unsupported rate was accepted");
    }
    {
        auto valid=std::string(R"({"name":"test","version":1,"ucie_format":6,"aou_hbf_profile_id":1,"aou_hbf_revision":0,"aou_hbf_option":0,"lanes":64,"gt_per_second":16,"axi_ports":1,"local_capacity_bytes":17179869184,"scenario_assumption":{"initial_ar_granules":3,"initial_r_granules":14,"max_accepted":2,"propagation_ns":0}})");
        const auto path=std::filesystem::temp_directory_path()/
            ("hbfsim-ucie-profile-"+std::to_string(getpid())+".json");
        const auto check=[&](std::string value, bool should_pass) {
            { std::ofstream out(path);out<<value; }
            bool passed=true;
            try { (void)load_link_profile(path); }
            catch (const std::exception&) { passed=false; }
            require(passed==should_pass,"strict profile parser accepted invalid JSON value");
        };
        check(valid,true);
        for (const auto& bad: {std::string("16.5"),std::string("-1"),
                               std::string("4294967312")}) {
            auto changed=valid;
            changed.replace(changed.find("\"gt_per_second\":16"),18,
                            "\"gt_per_second\":"+bad);
            check(changed,false);
        }
        auto unknown=valid;
        unknown.insert(unknown.find("\"version\""),"\"unknown\":1,");
        check(unknown,false);
        std::filesystem::remove(path);
    }
    {
        p=profile();
        StreamingLink link(p);
        link.enqueue(Direction::Request,1,std::numeric_limits<std::uint64_t>::max());
        bool overflow=false;
        try { (void)link.step(); }
        catch (const std::overflow_error&) { overflow=true; }
        require(overflow,"flit event time silently overflowed");
    }
    std::cout << "PASS fixed Format6/AoU oracle, grade timing, legal grant encoding\n";
}
