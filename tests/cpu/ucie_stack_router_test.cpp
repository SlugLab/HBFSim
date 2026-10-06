#include <hbfsim/ucie/stack_router.hpp>

#include <limits>
#include <stdexcept>

namespace {
void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }
template <typename F> void rejects(F&& f)
{
    bool threw=false;
    try { f(); } catch (const std::exception&) { threw=true; }
    require(threw,"invalid global span was accepted");
}
}

int main()
{
    using namespace hbfsim::ucie;
    constexpr std::uint64_t gib=1ULL<<30;
    ContiguousStackMap map(2ULL<<40,4,16);
    require(map.stack_bytes()==512*gib && map.module_bytes()==32*gib,
            "2 TiB default geometry did not form 32 GiB modules");
    require(map.locate(0).stack_id==0 &&
            map.locate(32*gib).module_id==1 &&
            map.locate(512*gib).stack_id==1 &&
            map.locate(map.total_bytes()-1).stack_id==3 &&
            map.locate(map.total_bytes()-1).module_id==15,
            "continuous first/last/module/stack address routing failed");
    rejects([] { ContiguousStackMap invalid(512*gib,4,1); });
    rejects([] { ContiguousStackMap invalid(2ULL<<40,1,16); });
    rejects([&] { (void)map.locate(map.total_bytes()); });

    GlobalSpanRegistry registry(map);
    const auto segments=registry.add({1,0,map.total_bytes(),1,0,1,7,true});
    require(segments.size()==64 && segments[0].stack_id==0 &&
            segments[0].module_id==0 && segments[63].stack_id==3 &&
            segments[63].module_id==15 &&
            segments[63].canonical_offset==0,
            "global backing did not expand into 64 real worker modules");
    const auto small=registry.split_read(3,5,7,1);
    require(small.size()==1 && small[0].original_bytes==5 &&
            small[0].aligned_canonical_address==0,
            "small original access lost 64 B AXI padding distinction");
    const auto boundary=registry.split_read(32*gib-16,32,7,10);
    require(boundary.size()==2 && boundary[0].original_bytes==16 &&
            boundary[1].original_bytes==16 &&
            boundary[0].location.module_id==0 &&
            boundary[1].location.module_id==1,
            "module-crossing original span failed split");
    const auto cross_stack=registry.split_read(512*gib-16,32,7,20);
    require(cross_stack.size()==2 && cross_stack[0].location.stack_id==0 &&
            cross_stack[1].location.stack_id==1,
            "stack boundary span failed split");
    const auto last=registry.split_read(map.total_bytes()-1,1,7,30);
    require(last.size()==1 && last[0].original_bytes==1 &&
            last[0].location.stack_id==3 && last[0].location.module_id==15,
            "global final byte failed padding/route");
    rejects([&] { (void)registry.split_read(0,1,8,40); });
    rejects([&] { (void)registry.split_read(map.total_bytes()-1,2,7,40); });
    rejects([&] { (void)registry.split_read(0,65537,7,40); });
    rejects([&] { (void)registry.split_read(
        std::numeric_limits<std::uint64_t>::max(),2,7,40); });

    GlobalSpanRegistry aliases(map);
    (void)aliases.add({1,4096,4096,99,8192,1,7,true});
    (void)aliases.add({2,16384,4096,99,8192,1,7,true});
    const auto first=aliases.split_read(4096,64,7,1);
    const auto second=aliases.split_read(16384,64,7,2);
    require(first[0].aligned_canonical_address==
            second[0].aligned_canonical_address,
            "explicit aliases did not converge on canonical media");
    constexpr std::uint64_t high_virtual=1ULL<<60;
    const auto new_physical=aliases.add({3,high_virtual+3,8,99,
                                         512*gib+8195,1,7,true});
    require(new_physical.size()==1 &&
            new_physical[0].stack_id==1 &&
            new_physical[0].address==8195 &&
            new_physical[0].bytes==8,
            "8 B high virtual alias did not retain exact worker permissions");
    const auto padded=aliases.split_read(high_virtual+3,8,7,3);
    require(padded.size()==1 &&
            padded[0].location.stack_id==1 &&
            padded[0].aligned_canonical_address==512*gib+8192 &&
            padded[0].original_bytes==8,
            "original 8 B read should route across virtual placement");
    rejects([&] { (void)aliases.split_read(high_virtual+3,9,7,4); });
    rejects([&] { (void)aliases.add({4,high_virtual+3,8,99,
                                    512*gib+8195,1,7,true}); });
    const auto same_physical=aliases.add({5,high_virtual+4096,8,99,
                                          512*gib+8195,1,7,true});
    require(same_physical.empty(),
            "physical alias installed duplicate worker registration");
    const auto neighbor=aliases.add({6,high_virtual+8192,8,100,
                                     512*gib+8212,1,7,true});
    require(neighbor.size()==1 && neighbor[0].address==8212 &&
            aliases.split_read(high_virtual+8192,8,7,5)[0]
                .aligned_canonical_address==512*gib+8192,
            "independent short objects sharing one 64 B packet were rejected");
    rejects([&] { (void)aliases.add({9,high_virtual+12288,8,101,
                                    512*gib+8195,1,7,true}); });
    aliases.retire_generation(99,1,7);
    rejects([&] { (void)aliases.add({7,4096,4096,99,8192,1,7,true}); });
    (void)aliases.add({8,4096,4096,99,8192,2,7,true});
    ContiguousStackMap many(32ULL*4096,32,1);
    require(many.stack_count()==32 && many.locate(31ULL*4096).stack_id==31,
            "stack count incorrectly inherits 16-module OCP bound");
    GlobalSpanRegistry bounded(map,4,8);
    (void)bounded.add({1,high_virtual,9,1,4096,1,7,true});
    rejects([&] { (void)bounded.split_read(high_virtual,9,7,1); });
    GlobalSpanRegistry top(map);
    (void)top.add({1,std::numeric_limits<std::uint64_t>::max(),1,
                   901,0,1,7,true});
    require(top.split_read(std::numeric_limits<std::uint64_t>::max(),
                           1,7,1).size()==1,
            "last legal virtual byte was incorrectly treated as overflow");
}
