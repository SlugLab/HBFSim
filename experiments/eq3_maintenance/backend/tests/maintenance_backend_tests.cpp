#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
hbfsim::HbfRequest make_read(std::uint64_t id, std::uint64_t page, std::uint32_t bytes) {
  return {.request_id=id,.sequence=id,.arrival_ns=0,.logical_address=page*bytes,
    .bytes=bytes,.operation=static_cast<std::uint32_t>(hbfsim::RequestOperation::Read)};
}
hbfsim::HbfRequest make_write(std::uint64_t id,std::uint64_t page,std::uint32_t bytes,
    std::uint64_t arrival) {
  return {.request_id=id,.sequence=id,.arrival_ns=arrival,.logical_address=page*bytes,
    .bytes=bytes,.operation=static_cast<std::uint32_t>(hbfsim::RequestOperation::Write)};
}
void submit_read(hbfsim::MqsimOnlineEngine& engine,std::uint64_t id,
    std::uint64_t page,std::uint32_t bytes) {
  auto request=make_read(id,page,bytes);request.arrival_ns=engine.current_time_ns();
  engine.submit(request);
}
hbfsim::MqsimMaintenanceCompletion maintain(hbfsim::MqsimOnlineEngine& engine,
    std::uint64_t id, std::uint64_t page, hbfsim::MqsimMaintenanceFailurePoint failure,
    bool reclaim=false) {
  engine.submit_maintenance({.request_id=id,.parent_id=9000+id,
    .due_ns=engine.current_time_ns(),.deadline_ns=engine.current_time_ns()+1000000000ULL,
    .logical_page=page,.channel=0,.chip=0,.die=0,.plane=0,.plane_is_exact=true,
    .reclaim_invalid_source_block=reclaim,.failure_point=failure});
  std::vector<hbfsim::MqsimMaintenanceCompletion> completions;
  while(engine.pending_maintenance()) {
    (void)engine.run_next_completion_until(engine.current_time_ns()+1000000000ULL);
    auto batch=engine.take_maintenance_completions();
    completions.insert(completions.end(),batch.begin(),batch.end());
  }
  require(completions.size()==1,"maintenance did not complete exactly once");
  return completions.front();
}
}
int main(int argc,char** argv) {
  require(argc==2,"profile path required");
  auto profile=hbfsim::load_profile(argv[1]);
  profile.capacity_bytes=1ULL<<30;profile.channels=1;profile.dies_per_channel=1;
  profile.planes_per_die=1;profile.pages_per_block=1;profile.queue_depth=4;
  profile.aggregate_bandwidth_bytes_per_s=1ULL<<40;profile.hbm_cache_bytes=0;
  hbfsim::validate_profile(profile);
  hbfsim::HbfCompletion baseline{};
  { hbfsim::MqsimOnlineEngine engine(profile);engine.submit(make_read(1,0,profile.page_bytes));
    baseline=*engine.run_next_completion(); }
  { hbfsim::MqsimOnlineEngine engine(profile);engine.submit(make_read(1,0,profile.page_bytes));
    auto value=*engine.run_next_completion();
    require(value.modeled_completion_ns==baseline.modeled_completion_ns,"disabled maintenance changed demand timing");
    // Move the one-page data frontier away from page 0's source block so its
    // now-invalid source can be reclaimed without erasing an active frontier.
    submit_read(engine,3,1,profile.page_bytes);
    require(engine.run_next_completion().has_value(),"second mapped page setup failed");
    auto committed=maintain(engine,101,0,hbfsim::MqsimMaintenanceFailurePoint::None,true);
    auto events=engine.take_maintenance_events();
    require(committed.status==hbfsim::MqsimMaintenanceStatus::Committed&&
      committed.mapping_committed&&committed.source_retired&&committed.erase_completed,
      "one-page maintenance did not read/program/commit/reclaim");
    require(committed.source_version!=committed.committed_version&&
      committed.transaction_ids.size()==3,"committed version/command identity facts incomplete");
    require(!events.empty()&&events.front().state==hbfsim::MqsimMaintenanceState::Due&&
      events.back().state==hbfsim::MqsimMaintenanceState::Done,"maintenance lifecycle facts incomplete");
    submit_read(engine,2,0,profile.page_bytes);
    require(engine.run_next_completion().has_value(),"relocated LPA is unreadable");
    auto failed=maintain(engine,102,1,hbfsim::MqsimMaintenanceFailurePoint::Program);
    require(failed.status==hbfsim::MqsimMaintenanceStatus::FailedProgram&&
      !failed.mapping_committed&&!failed.source_retired,"failed program changed mapping");
    submit_read(engine,4,1,profile.page_bytes);require(engine.run_next_completion().has_value(),"source lost after failed program");

    submit_read(engine,5,2,profile.page_bytes);require(engine.run_next_completion().has_value(),"read-failure setup failed");
    auto read_failed=maintain(engine,103,2,hbfsim::MqsimMaintenanceFailurePoint::Read);
    require(read_failed.status==hbfsim::MqsimMaintenanceStatus::FailedRead&&
      !read_failed.mapping_committed&&read_failed.transaction_ids.size()==1,
      "failed maintenance read was not retained as a real command");
    submit_read(engine,6,2,profile.page_bytes);require(engine.run_next_completion().has_value(),"source lost after failed read");

    submit_read(engine,7,3,profile.page_bytes);require(engine.run_next_completion().has_value(),"stale-CAS setup failed");
    auto stale=maintain(engine,104,3,hbfsim::MqsimMaintenanceFailurePoint::StaleCommit);
    require(stale.status==hbfsim::MqsimMaintenanceStatus::FailedStaleVersion&&
      !stale.mapping_committed&&!stale.source_retired,
      "stale CAS changed the authoritative mapping");
    submit_read(engine,8,3,profile.page_bytes);require(engine.run_next_completion().has_value(),"source lost after stale CAS");

    submit_read(engine,9,4,profile.page_bytes);require(engine.run_next_completion().has_value(),"erase-failure setup failed");
    submit_read(engine,10,5,profile.page_bytes);require(engine.run_next_completion().has_value(),"erase-failure frontier setup failed");
    auto erase_failed=maintain(engine,105,4,hbfsim::MqsimMaintenanceFailurePoint::Erase,true);
    require(erase_failed.status==hbfsim::MqsimMaintenanceStatus::FailedAfterCommitNeedsReconcile&&
      erase_failed.mapping_committed&&erase_failed.source_retired&&!erase_failed.erase_completed,
      "erase failure did not preserve committed destination and reconciliation fact");
    submit_read(engine,11,4,profile.page_bytes);require(engine.run_next_completion().has_value(),"destination lost after erase failure");

    // A real foreground write arrives while maintenance is reading the old
    // source.  It executes through MQSim and advances the mapping generation;
    // the later maintenance program must be discarded by the generation CAS.
    submit_read(engine,12,6,profile.page_bytes);
    require(engine.run_next_completion().has_value(),"concurrent-write setup failed");
    const auto overlap_start=engine.current_time_ns();
    engine.submit_maintenance({.request_id=106,.parent_id=9106,
      .due_ns=overlap_start,.deadline_ns=overlap_start+1000000000ULL,
      .logical_page=6,.channel=0,.chip=0,.die=0,.plane=0,.plane_is_exact=true});
    engine.submit(make_write(13,6,profile.page_bytes,overlap_start+1));
    std::vector<hbfsim::HbfCompletion> foreground;
    while(engine.pending_maintenance()) {
      auto value=engine.run_next_completion_until(engine.current_time_ns()+1000000000ULL);
      if(value)foreground.push_back(*value);
    }
    while(engine.pending()) {
      auto value=engine.run_next_completion();if(value)foreground.push_back(*value);
    }
    auto overlap=engine.take_maintenance_completions();
    require(overlap.size()==1&&
      overlap.front().status==hbfsim::MqsimMaintenanceStatus::FailedStaleVersion&&
      !overlap.front().mapping_committed&&foreground.size()==1&&
      foreground.front().request_id==13,
      "real concurrent foreground write did not win generation CAS exactly once");
    submit_read(engine,14,6,profile.page_bytes);
    require(engine.run_next_completion().has_value(),"new foreground mapping lost after stale maintenance");
    auto after_write=maintain(engine,107,6,hbfsim::MqsimMaintenanceFailurePoint::None);
    require(after_write.status==hbfsim::MqsimMaintenanceStatus::Committed&&
      after_write.source_version==overlap.front().source_version+1&&
      after_write.committed_version==after_write.source_version+1,
      "mapping generation was not monotonic across foreground write and maintenance commit");
  }

  // The campaign fixture uses one channel per HBF stack and all 16 declared
  // dies.  Exercise die 15 with the finite 1 GiB engineering workspace.
  { auto p16=profile;p16.capacity_bytes=1ULL<<30;p16.channels=1;
    p16.dies_per_channel=16;p16.planes_per_die=1;p16.pages_per_block=256;
    hbfsim::validate_profile(p16);hbfsim::MqsimOnlineEngine engine(p16);
    submit_read(engine,201,15,p16.page_bytes);require(engine.run_next_completion().has_value(),"16-die setup failed");
    engine.submit_maintenance({.request_id=202,.parent_id=9202,
      .due_ns=engine.current_time_ns(),.deadline_ns=engine.current_time_ns()+1000000000ULL,
      .logical_page=15,.channel=0,.chip=0,.die=15,.plane=0,.plane_is_exact=true});
    while(engine.pending_maintenance())(void)engine.run_next_completion_until(engine.current_time_ns()+1000000000ULL);
    auto completion=engine.take_maintenance_completions();
    require(completion.size()==1&&completion.front().status==hbfsim::MqsimMaintenanceStatus::Committed,
      "16-die finite-workspace maintenance failed");
  }
  std::cout<<"PASS METADATA_VERSION_VALIDITY shared_TSU_PHY maintenance lifecycle\n";
}
