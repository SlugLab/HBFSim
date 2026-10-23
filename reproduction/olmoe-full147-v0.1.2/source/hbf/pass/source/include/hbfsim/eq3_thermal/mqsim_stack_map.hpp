#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstdint>
#include <cstddef>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace hbfsim::eq3_thermal {

struct MqsimStackChannelGroup {
  std::string stack_id;
  std::vector<std::uint32_t> channels;
  std::uint32_t declared_dies{};
};

struct MqsimStackPlacement {
  HbfRequest backend_request{};
  std::uint64_t external_page{};
  std::uint64_t backend_page{};
  std::optional<std::string> stack_id;
  std::optional<std::uint32_t> expected_channel;
};

// Optional one-page address-layout composition for same-kind HBF stacks. The
// transform is a persistent bijection, not dynamic polling or rebalancing. It
// does not submit, retry, reserve, or own work. Enabled mappings must be used
// exclusively for the lifetime of their MQSim engine because mapped and
// unmapped LPAs are distinct backend namespaces.
class MqsimStackMapAdapter {
 public:
  MqsimStackMapAdapter() = default;

  MqsimStackMapAdapter(const Profile& profile,
                       std::vector<MqsimStackChannelGroup> groups)
      : page_bytes_(profile.page_bytes), capacity_bytes_(profile.capacity_bytes),
        channels_(profile.channels),
        dies_per_channel_(profile.dies_per_channel), groups_(std::move(groups)) {
    if(profile.plane_allocation_scheme!=PlaneAllocationScheme::Cwdp)
      throw std::invalid_argument("MQSim stack map requires CWDP allocation");
    if(!page_bytes_||!channels_||groups_.size()<2||channels_%groups_.size())
      throw std::invalid_argument("invalid MQSim stack-map geometry");
    channels_per_stack_=channels_/groups_.size();
    channel_to_stack_.resize(channels_);
    for(std::size_t s=0;s<groups_.size();++s) {
      auto& group=groups_[s];
      if(group.stack_id.empty()||group.channels.size()!=channels_per_stack_||
         group.declared_dies!=channels_per_stack_*dies_per_channel_)
        throw std::invalid_argument("invalid MQSim HBF stack declaration");
      for(std::size_t prior=0;prior<s;++prior)
        if(groups_[prior].stack_id==group.stack_id)
          throw std::invalid_argument("duplicate MQSim HBF stack identity");
      for(std::size_t k=0;k<group.channels.size();++k) {
        const auto channel=group.channels[k];
        if(channel>=channels_||channel_to_stack_[channel])
          throw std::invalid_argument("overlapping or invalid MQSim channel group");
        channel_to_stack_[channel]=s;
      }
    }
    for(const auto& owner:channel_to_stack_)if(!owner)
      throw std::invalid_argument("MQSim stack map must cover every channel");
    enabled_=true;
  }

  [[nodiscard]] bool enabled()const noexcept{return enabled_;}

  [[nodiscard]] MqsimStackPlacement map(
      const HbfRequest& request,
      std::optional<std::string_view> requested_stack=std::nullopt)const {
    if(!enabled_)
      return {request,request.logical_address/page_bytes_,
              request.logical_address/page_bytes_,std::nullopt,std::nullopt};
    if(request.bytes!=page_bytes_||request.logical_address%page_bytes_)
      throw std::invalid_argument("MQSim stack map supports one aligned profile page");
    const auto page=request.logical_address/page_bytes_;
    if(page>=capacity_bytes_/page_bytes_)
      throw std::out_of_range("MQSim stack-map request exceeds capacity");
    const auto s=static_cast<std::size_t>(page%groups_.size());
    const auto q=page/groups_.size();
    const auto k=static_cast<std::size_t>(q%channels_per_stack_);
    const auto r=q/channels_per_stack_;
    const auto channel=groups_[s].channels[k];
    if(requested_stack&&*requested_stack!=groups_[s].stack_id)
      throw std::invalid_argument("requested HBF stack conflicts with configured address stripe");
    if(r>(std::numeric_limits<std::uint64_t>::max()-channel)/channels_)
      throw std::overflow_error("MQSim backend page mapping overflows");
    const auto backend_page=r*channels_+channel;
    if(backend_page>std::numeric_limits<std::uint64_t>::max()/page_bytes_)
      throw std::overflow_error("MQSim backend byte address overflows");
    auto backend=request;
    backend.logical_address=backend_page*page_bytes_;
    return {backend,page,backend_page,groups_[s].stack_id,channel};
  }

  // Convert a persistent page ordinal inside one explicitly named HBF stack
  // to the global striped namespace, then apply the same bijection as map().
  [[nodiscard]] MqsimStackPlacement map_stack_page(
      const HbfRequest& request,std::string_view requested_stack,
      std::uint64_t stack_local_page)const {
    if(!enabled_)throw std::logic_error("MQSim stack-local placement requires enabled mapping");
    std::size_t stack_index=groups_.size();
    for(std::size_t s=0;s<groups_.size();++s)
      if(groups_[s].stack_id==requested_stack) {stack_index=s;break;}
    if(stack_index==groups_.size())
      throw std::invalid_argument("unknown requested HBF stack identity");
    if(stack_local_page>(std::numeric_limits<std::uint64_t>::max()-stack_index)/groups_.size())
      throw std::overflow_error("MQSim external page mapping overflows");
    const auto external_page=stack_local_page*groups_.size()+stack_index;
    if(external_page>std::numeric_limits<std::uint64_t>::max()/page_bytes_)
      throw std::overflow_error("MQSim external byte address overflows");
    auto external=request;
    external.logical_address=external_page*page_bytes_;
    return map(external,requested_stack);
  }

  [[nodiscard]] std::optional<std::string> stack_for_channel(
      std::uint32_t channel)const {
    if(!enabled_||channel>=channel_to_stack_.size()||!channel_to_stack_[channel])
      return std::nullopt;
    return groups_[*channel_to_stack_[channel]].stack_id;
  }

 private:
  bool enabled_{};
  std::uint64_t page_bytes_{1};
  std::uint64_t capacity_bytes_{};
  std::size_t channels_{};
  std::size_t dies_per_channel_{};
  std::size_t channels_per_stack_{};
  std::vector<MqsimStackChannelGroup> groups_;
  std::vector<std::optional<std::size_t>> channel_to_stack_;
};

} // namespace hbfsim::eq3_thermal
