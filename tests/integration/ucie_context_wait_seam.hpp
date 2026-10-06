#pragma once
// Publication-only CPU Context -> normal UCIe daemon seam. This uses existing
// test hooks; the mapped generation and range gate are fixtures, not CUDA proof.
#include "../../src/cuda_runtime/context.hpp"
#include <algorithm>
#include <sstream>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <iterator>
#include <memory>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>

namespace hbfsim::publication_test {
using json = nlohmann::json;
using namespace hbfsim::host_service;
inline void seam_need(bool good,const char* why) {
    if (!good) throw std::runtime_error(why);
}
inline json seam_read(const std::filesystem::path& p) {
    std::ifstream in(p);seam_need(bool(in),"seam input missing");return json::parse(in);
}
inline void seam_write(const std::filesystem::path& p,const json& j) {
    std::ofstream out(p);seam_need(bool(out),"seam output open failed");
    out << j.dump(2) << '\n';out.flush();seam_need(bool(out),"seam output write failed");
}
struct SeamMap {
    void* p{MAP_FAILED};std::size_t bytes{};
    explicit SeamMap(int fd) {
        struct stat st{};seam_need(::fstat(fd,&st)==0 && st.st_size>0,"seam control fstat failed");
        bytes=static_cast<std::size_t>(st.st_size);
        p=::mmap(nullptr,bytes,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
        seam_need(p!=MAP_FAILED,"seam control mmap failed");
    }
    ~SeamMap() {if(p!=MAP_FAILED) (void)::munmap(p,bytes);}
};
struct SeamSpawn {bool initialized{};};
inline void seam_generation(int fd,void* opaque) noexcept {
    auto& state=*static_cast<SeamSpawn*>(opaque);
    struct stat st{};if(::fstat(fd,&st)!=0 || st.st_size<=0) return;
    void* p=::mmap(nullptr,static_cast<std::size_t>(st.st_size),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if(p==MAP_FAILED) return;
    ControlView v(p,static_cast<std::size_t>(st.st_size));
    if(v.valid()) {atomic_store(v.header()->control_generation,1,std::memory_order_release);state.initialized=true;}
    (void)::munmap(p,static_cast<std::size_t>(st.st_size));
}
inline int seam_gate(void*,std::uintptr_t alias,std::uintptr_t begin,std::uintptr_t end,
                     runtime::PublishRange publish,void* publication,void* opaque) noexcept {
    auto& count=*static_cast<unsigned*>(opaque);
    if(alias!=0xfeed0000 || begin!=0x100000 || end!=0x104000 || !publish) return HBFSIM_INVALID_ARGUMENT;
    publish(publication);++count;return HBFSIM_OK;
}
inline std::vector<std::string> seam_argv(pid_t pid) {
    std::ifstream in("/proc/"+std::to_string(pid)+"/cmdline",std::ios::binary);
    seam_need(bool(in),"seam actual daemon argv missing");
    std::vector<std::string> result;std::string s;
    while(std::getline(in,s,'\0')) result.push_back(s);
    return result;
}
inline std::string seam_option(const std::vector<std::string>& args,const std::string& key) {
    const auto i=std::find(args.begin(),args.end(),key);
    seam_need(i!=args.end() && std::next(i)!=args.end(),"seam daemon option absent");
    seam_need(std::find(std::next(i),args.end(),key)==args.end(),"seam duplicate daemon option");
    return *std::next(i);
}
inline void seam_case(const std::filesystem::path& daemon,const std::filesystem::path& worker,
                      const std::filesystem::path& top,const std::filesystem::path& profile,
                      const std::filesystem::path& root,const std::string& mode) {
    const auto folder=root/mode;seam_need(std::filesystem::create_directory(folder),"seam output already exists");
    const auto placement=folder/"placement.json";
    seam_write(placement,{{"schema","hbfsim.ucie.host_placement.v1"},{"placements",json::array({{
        {"range_id",1},{"file_offset",0},{"length",16384},{"page_bytes",16384},
        {"registered_address",0x100000},{"canonical_physical_address",0},{"endpoint_id",7},{"generation",1}}})}});
    for(const auto& item:std::vector<std::pair<const char*,std::string>>{
        {"HBFSIM_UCIE_TOP_PROFILE",top.string()},{"HBFSIM_UCIE_WORKER",worker.string()},
        {"HBFSIM_UCIE_PLACEMENT_MANIFEST",placement.string()},{"HBFSIM_UCIE_WAIT_MODE",mode}})
        seam_need(::setenv(item.first,item.second.c_str(),1)==0,"seam setenv failed");
    const auto report_dir=folder/"report";
    const std::string profile_s=profile.string(),report_s=report_dir.string(),daemon_s=daemon.string();
    const hbfsim_options opts{profile_s.c_str(),report_s.c_str(),HBFSIM_MODEL_REFERENCE,8,5'000'000'000ULL};
    hbfsim_context* raw=nullptr;SeamSpawn spawn{};
    const int create=runtime::create_cpu_test_context_with_spawn_hook(&opts,daemon_s.c_str(),seam_generation,&spawn,&raw);
    std::unique_ptr<hbfsim_context,decltype(&hbfsim_context_destroy)> context(raw,&hbfsim_context_destroy);
    seam_need(create==HBFSIM_OK && raw && spawn.initialized,"seam real Context/daemon startup failed");
    const pid_t pid=runtime::daemon_pid_for_test(raw);seam_need(pid>0,"seam daemon PID absent");
    std::ifstream proc_stat("/proc/"+std::to_string(pid)+"/stat");std::string stat_line;std::getline(proc_stat,stat_line);
    const auto right=stat_line.rfind(')');seam_need(right!=std::string::npos,"seam daemon stat missing");
    std::istringstream stat_fields(stat_line.substr(right+2));std::string field;
    for(unsigned i=0;i<=19;++i) seam_need(bool(stat_fields>>field),"seam daemon start ticks missing");
    const auto start_ticks=std::stoull(field);
    std::ifstream boot_input("/proc/sys/kernel/random/boot_id");std::string boot;std::getline(boot_input,boot);
    seam_need(!boot.empty(),"seam boot identity missing");
    SeamMap mapping(runtime::control_fd_for_test(raw));ControlView control(mapping.p,mapping.bytes);
    seam_need(control.valid() && control.header()->control_generation==1,"seam generation changed");
    const auto flags=atomic_load(control.header()->reserved0,std::memory_order_acquire);
    const bool zero=mode!="nominal";
    seam_need(((flags&kControlZeroInjectedWait)!=0)==zero,"seam real control wait flag differs");
    seam_need((flags&kControlCapabilityUcieBackend)!=0,"seam daemon UCIe capability missing");
    const auto args=seam_argv(pid);
    seam_need(seam_option(args,"--backend")=="ucie" &&
              seam_option(args,"--ucie-wait-mode")== (zero ? "zero" : "nominal") &&
              seam_option(args,"--ucie-top-profile")==top.string() &&
              seam_option(args,"--ucie-worker")==worker.string() &&
              seam_option(args,"--ucie-placement-manifest")==placement.string(),"seam actual canonical daemon argv differs");
    unsigned gate_count=0;
    const hbfsim_range_options range{HBFSIM_RANGE_MODE_TIMING,HBFSIM_RANGE_READ,HBFSIM_CACHE_POLICY_NONE,0};
    seam_need(runtime::register_device_with_gate_for_test(raw,reinterpret_cast<void*>(0x100000),16384,&range,
        0xfeed0000,seam_gate,&gate_count)==HBFSIM_OK && gate_count==1 && runtime::range_count_for_test(raw)==1,
        "seam actual range publication failed");
    const auto record=control.ranges()[0];
    seam_need(record.base==0x100000 && record.length==16384 && record.file_offset==0 &&
              record.range_id==1 && record.page_bytes==16384,"seam registered range differs from placement");
    const HbfRequest request{.request_id=11,.sequence=0,.arrival_ns=1'000'000'000'000ULL,
        .logical_address=record.file_offset,.deadline_ns=1'000'100'000'000ULL,
        .bytes=16384,.range_id=record.range_id,.stream_id=0,
        .operation=static_cast<std::uint32_t>(RequestOperation::Read),.page_generation=1,.flags=0};
    HbfCompletion completion{};
    seam_need(runtime::submit_for_test(raw,request,&completion)==HBFSIM_OK && completion.request_id==11 &&
        completion.page_generation==1 && completion.status==static_cast<std::uint32_t>(RequestStatus::Ready),
        "seam real registered ring read did not complete");
    seam_need(completion.service_ns>0 && (zero ? (completion.modeled_ns==0 &&
        completion.modeled_completion_ns==request.arrival_ns) : (completion.modeled_ns>0 &&
        completion.modeled_completion_ns==request.arrival_ns+completion.modeled_ns)),
        "seam nominal/zero injection service contract failed");
    // Request graceful normal shutdown through the existing control protocol.
    // WNOWAIT proves natural status before the Context performs its owned reap.
    atomic_store(control.header()->shutdown,1,std::memory_order_release);
    const auto stop_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(15);
    siginfo_t info{};
    for(;;) {
        const int r=::waitid(P_PID,static_cast<id_t>(pid),&info,WEXITED|WNOWAIT|WNOHANG);
        if(r<0 && errno==EINTR) continue;
        seam_need(r==0,"seam natural daemon wait failed");
        if(info.si_pid==pid) break;
        seam_need(std::chrono::steady_clock::now()<stop_deadline,"seam graceful daemon exit timed out");
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    seam_need(info.si_code==CLD_EXITED && info.si_status==0,"seam daemon did not naturally exit zero");
    context.reset();int status=0;errno=0;
    seam_need(::waitpid(pid,&status,WNOHANG)==-1 && errno==ECHILD &&
              !std::filesystem::exists("/proc/"+std::to_string(pid)),"seam Context did not own/reap daemon");
    const auto report=seam_read(report_dir/"ucie-backend-report.json");
    seam_need(report.at("wait_mode")== (zero ? "zero_injected" : "nominal") &&
        report.at("control_generation")==1 && report.at("published")==1 && report.at("failed")==0 &&
        report.at("unsupported")==0 && report.at("caller_outstanding")==0 &&
        report.at("original_hbf_page_bytes")==16384 && report.at("original_hbf_request_bytes_submitted")==16384 &&
        report.at("original_frontend_bytes")==16384 && report.at("axi_payload_bytes")==16384 &&
        report.at("axi_child_transactions")==256 && report.at("media_submit_bytes")==16384 &&
        report.at("native_commands")==4 && report.at("max_sim_ready_ns")>0,"seam report service closure differs");
    std::uint64_t native=0,media=0;bool module_report=false;
    for(const auto& stack:report.at("stacks")) {
        seam_need(stack.at("physical_outstanding")==0 && stack.at("failed")==0,"seam physical tail incomplete");
        native+=stack.at("native_commands").get<std::uint64_t>();
        media+=stack.at("media_submit_bytes").get<std::uint64_t>();
        module_report=module_report || !stack.at("module_links").empty();
    }
    seam_need(native==4 && media==16384 && module_report,"seam stack/media/module report did not close");
    const json proof{{"schema","hbfsim.publication.context-wait-seam.v1"},{"status","PASS"},
        {"requested_mode",mode},{"actual_daemon_argv",args},{"actual_daemon_pid",pid},{"actual_daemon_start_ticks",start_ticks},{"boot",boot},
        {"control_flags",flags},{"test_only_generation",1},{"range_gate_calls",gate_count},
        {"registered_range_id",record.range_id},{"registered_file_offset",record.file_offset},
        {"completion",{{"request_id",completion.request_id},{"status",completion.status},
            {"modeled_ns",completion.modeled_ns},{"modeled_completion_ns",completion.modeled_completion_ns},
            {"service_ns",completion.service_ns}}},{"daemon_natural_exit",info.si_status},
        {"context_owned_reap",true},{"cudaHostRegister_proof",false},{"adapter_report",report}};
    seam_write(folder/"SEAM_RESULT.json",proof);std::cout << proof.dump() << '\n';
}
inline int context_wait_seam(const char* daemon_arg,const char* worker_arg,const char* top_arg) {
    const auto daemon=std::filesystem::absolute(daemon_arg),worker=std::filesystem::absolute(worker_arg),top=std::filesystem::absolute(top_arg);
    const auto source=top.parent_path().parent_path().parent_path().parent_path();
    const auto profile=source/"configs/profiles/nominal.json";
    const auto root=daemon.parent_path()/"ucie-context-wait-seam";
    seam_need(std::filesystem::create_directory(root),"seam output root already exists");
    const auto p=seam_read(profile);
    seam_need(p.at("page_bytes")==16384 && p.at("read_latency_ns")==10000 &&
              p.at("program_latency_ns")==100000 && p.at("time_scale")==100,
              "seam original nominal science profile changed");
    for(const auto& mode:std::vector<std::string>{"nominal","zero","zero_injected"})
        seam_case(daemon,worker,top,profile,root,mode);
    seam_need(::setenv("HBFSIM_UCIE_WAIT_MODE","invalid",1)==0,"seam invalid env failed");
    const std::string ps=profile.string(),rs=(root/"invalid").string(),ds=daemon.string();
    const hbfsim_options opts{ps.c_str(),rs.c_str(),HBFSIM_MODEL_REFERENCE,8,5'000'000'000ULL};
    hbfsim_context* bad=nullptr;
    const auto rc=runtime::create_cpu_test_context(&opts,ds.c_str(),&bad);
    std::unique_ptr<hbfsim_context,decltype(&hbfsim_context_destroy)> cleanup(bad,&hbfsim_context_destroy);
    seam_need(rc==HBFSIM_INVALID_ARGUMENT && bad==nullptr,"seam invalid wait accepted");
    seam_write(root/"INVALID_RESULT.json",{{"status","PASS"},{"requested_mode","invalid"},
        {"create_rc",rc},{"context_is_null",true},{"scope","Rejected before daemon spawn"}});
    std::cout << "ucie_context_wait_seam nominal/zero/zero_injected + invalid PASS\n";return 0;
}
} // namespace hbfsim::publication_test
