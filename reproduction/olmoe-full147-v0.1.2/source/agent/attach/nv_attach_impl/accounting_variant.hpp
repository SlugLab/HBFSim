#pragma once
#include "same_page_variant.hpp"
// Generated build pin header is intentionally absent until real StageA stages exist.
// Empty pair refuses explicit accounting ON; it is not a fabricated payload identity.
#ifndef HBFSIM_ACCOUNTING_STAGE_PAIR_READY
#define HBFSIM_ACCOUNTING_STAGE_PAIR_READY 0
#define HBFSIM_ACCOUNTING_LI6_SHA ""
#define HBFSIM_ACCOUNTING_LI7_SHA ""
#endif
namespace hbfsim_accounting_variant {
inline hbfsim_samepage_variant::Pair select(const char *mode,const char *arm,
 const char *old_selector,const char *old_arm,bool combined) {
 if (!mode) {
  if (arm) return {false,false,{"",""}};
  return hbfsim_samepage_variant::select(old_selector,old_arm,combined);
 }
 if (std::strcmp(mode,"HOST_UNIFORM_ACCOUNTING_ONLY_V1") || !combined || !arm)
  return {false,false,{"",""}};
 // Coexistence is explicit: only the complete old OFF/LEGACY_OFF pair is allowed.
 if (old_selector || old_arm) {
  if (!old_selector || !old_arm || std::strcmp(old_selector,"OFF") ||
      std::strcmp(old_arm,"LEGACY_OFF")) return {false,false,{"",""}};
 }
 if (!std::strcmp(arm,"LEGACY_OFF"))
  return hbfsim_samepage_variant::select("OFF","LEGACY_OFF",true);
 if (std::strcmp(arm,"UNIFORM_ACCOUNTING_ON") || !HBFSIM_ACCOUNTING_STAGE_PAIR_READY)
  return {false,false,{"",""}};
 return {true,true,{HBFSIM_ACCOUNTING_LI6_SHA,HBFSIM_ACCOUNTING_LI7_SHA}};
}
}
