#pragma once
#include <cstring>
namespace hbfsim_samepage_variant {
struct Pair { bool valid; bool on; const char *sha[2]; };
inline Pair select(const char *selector, const char *arm, bool combined) {
    const char *off[2] = {"41ccb7e8d850f2c6d0db8245919e60613ed51543c14f5d5cc11fcca965a3c699", "3e28d6b15bb5b7ac4ad093226da3aeb4d480270f2c3f5de4ccc2fec784a2309a"};
    const char *on[2] = {"d6ac9e9070798816c64a4f268e4ec51ab21a9d20f9b3f872bf51908a12cb187e", "79f84540413cbb30441c8e8f925d0698834a428363d6763f9c786810a4b6186c"};
    bool enabled = false;
    if (selector) {
        if (!std::strcmp(selector,"ON")) enabled = true;
        else if (std::strcmp(selector,"OFF")) return {false,false,{"",""}};
        if (!combined || !arm || std::strcmp(arm,enabled ? "SAME_NATIVE_4K_ON" : "LEGACY_OFF"))
            return {false,false,{"",""}};
    } else if (arm) return {false,false,{"",""}};
    return {true,enabled,{enabled?on[0]:off[0], enabled?on[1]:off[1]}};
}
inline bool matches(const Pair &p, const char *a, const char *b) {
    return p.valid && a && b && !std::strcmp(a,p.sha[0]) && !std::strcmp(b,p.sha[1]);
}
}
