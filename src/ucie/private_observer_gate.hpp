#pragma once
#include "parallel_component_accounting_v1.hpp"
#include "../host_service/control_layout.hpp"
#include <functional>
#include <filesystem>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <sstream>
#include <cstdlib>
namespace hbfsim::ucie::short_qkv_observer {
struct FrontendLocal {std::uint64_t parents,children,retiring;bool poisoned,quiescent;};
struct AdapterLocal {std::uint64_t pending,order,staged;bool poisoned;};
inline std::function<FrontendLocal()> frontend_local;
inline std::function<AdapterLocal()> adapter_local;
inline std::function<void()> progress_tail;
constexpr std::uint64_t magic=0x4851465647415432ULL;
struct alignas(8) Mailbox {
    std::uint64_t magic,version,bytes,target_pid,start_ticks,control_generation,nonce,enabled;
    std::uint64_t command,ack,producer_finished,error,daemon_pid,daemon_start,device,inode;
};
static_assert(sizeof(Mailbox)==128 && offsetof(Mailbox,command)==64 && offsetof(Mailbox,ack)==72);
static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
inline auto atomic(std::uint64_t& value){return std::atomic_ref<std::uint64_t>(value);}
inline std::uint64_t ticks(std::uint64_t pid) {
    std::ifstream in("/proc/"+std::to_string(pid)+"/stat");std::string line;std::getline(in,line);
    const auto pos=line.rfind(')');if(!in||pos==std::string::npos)throw std::runtime_error("observer PID identity unavailable");
    std::istringstream fields(line.substr(pos+2));std::string token;
    for(unsigned i=0;i<=19;++i)if(!(fields>>token))throw std::runtime_error("observer PID stat truncated");
    return std::stoull(token);
}
class MappedGate {
    int fd_{-1};Mailbox* box_{};struct stat stat_{};unsigned phase_{};bool report_written_{};
    std::uint64_t validated_pid_{},validated_start_{},validated_generation_{},validated_nonce_{};
    std::filesystem::path report_;
    void fail() noexcept {
        if(!box_)return;
        parallel_component_accounting_v1::state.valid.store(false);
        (void)parallel_component_accounting_v1::end();
        try {
            if(!report_.empty()&&!std::filesystem::exists(report_)&&
               parallel_component_accounting_v1::state.closed)
                (void)parallel_component_accounting_v1::write_report(report_.c_str());
        }catch(...){} // Primary exception remains in original daemon stderr.
        atomic(box_->error).store(1,std::memory_order_release);
        atomic(box_->ack).store(4,std::memory_order_release);
    }
    void identity(const host_service::SharedControlHeader* h) {
        if(box_->magic!=magic||box_->version!=2||box_->bytes!=128||!box_->nonce||box_->enabled>1||
           box_->device!=static_cast<std::uint64_t>(stat_.st_dev)||box_->inode!=stat_.st_ino||
           box_->control_generation!=host_service::atomic_load(h->control_generation,std::memory_order_acquire))
            throw std::runtime_error("observer mailbox identity mismatch");
        if(phase_<=1){
            if(ticks(box_->target_pid)!=box_->start_ticks)throw std::runtime_error("observer target PID changed");
            validated_pid_=box_->target_pid;validated_start_=box_->start_ticks;
            validated_generation_=box_->control_generation;validated_nonce_=box_->nonce;
        }else if(box_->target_pid!=validated_pid_||box_->start_ticks!=validated_start_||
                 box_->control_generation!=validated_generation_||box_->nonce!=validated_nonce_)
            throw std::runtime_error("stable observer identity rewritten");
    }
    bool drained(const host_service::SharedControlHeader* h) {
        if(!frontend_local||!adapter_local)return false;
        const auto front=frontend_local();const auto adapter=adapter_local();
        if(front.poisoned||adapter.poisoned||host_service::atomic_load(h->fault,std::memory_order_acquire))
            throw std::runtime_error("observer drain detected original backend fault");
        if(adapter.pending||adapter.order||adapter.staged||front.parents||front.children||front.retiring)return false;
        // Removal follows original all_physical_done/IsActive false, released
        // reservations and consumed split replies; no accounting()/Stats here.
        const auto& e=parallel_component_accounting_v1::envelopes;
        for(unsigned s=0;s<4;++s)if(e.physical_send_success[s]!=e.physical_receive_success[s])return false;
        // GPU-exclusive host producer/consumer copies can be stale. Read the
        // actual shared slots after producer-finished, never those stale fields.
        const auto consumed=host_service::atomic_load(h->request_consumer,std::memory_order_acquire);
        auto* base=reinterpret_cast<const std::byte*>(h);
        auto* requests=reinterpret_cast<const host_service::SharedRequestSlot*>(base+h->request_offset);
        auto* completions=reinterpret_cast<const host_service::SharedCompletionSlot*>(base+h->completion_offset);
        for(std::uint64_t index=0;index<h->ring_capacity;++index){
            const auto expected=consumed<=index ? index :
                index+((consumed-1-index)/h->ring_capacity+1)*h->ring_capacity;
            if(host_service::atomic_load(requests[index].sequence,std::memory_order_acquire)!=expected||
               host_service::atomic_load(completions[index].sequence,std::memory_order_acquire)!=expected)return false;
        }
        return true;
    }
public:
    bool exported() const noexcept {return report_written_;}
    MappedGate() {
        const char* path=std::getenv("HBFSIM_SHORT_OBSERVER_MAILBOX");
        const char* report=std::getenv("HBFSIM_SHORT_OBSERVER_REPORT");
        if(!path&&!report)return;
        if(!path||!report)throw std::runtime_error("incomplete private observer config");
        try {
        fd_=::open(path,O_RDWR|O_CLOEXEC|O_NOFOLLOW);
        if(fd_<0||::fstat(fd_,&stat_)||!S_ISREG(stat_.st_mode)||stat_.st_size!=128||
           stat_.st_uid!=::geteuid()||stat_.st_nlink!=1||(stat_.st_mode&0777)!=0600)
            throw std::runtime_error("exclusive observer mailbox file identity");
        void* mapping=::mmap(nullptr,128,PROT_READ|PROT_WRITE,MAP_SHARED,fd_,0);
        if(mapping==MAP_FAILED)throw std::runtime_error("observer mmap failed");
        box_=static_cast<Mailbox*>(mapping);report_=report;
        if(std::filesystem::exists(report_))throw std::runtime_error("observer report must be absent");
        } catch (...) {
            if(box_){::munmap(box_,128);box_=nullptr;}
            if(fd_>=0){::close(fd_);fd_=-1;}
            throw;
        }
    }
    ~MappedGate(){if(box_){if(phase_==2)fail();::munmap(box_,128);}if(fd_>=0)::close(fd_);}
    template<class Export> void tick(const host_service::SharedControlHeader* h,Export export_closed) {
        if(!box_)return;
        try {
            const auto command=atomic(box_->command).load(std::memory_order_acquire);
            if(!command||command==phase_)return;
            identity(h);
            if(command==4){fail();phase_=4;return;}
            if(command==1&&phase_==0){
                if(!frontend_local||!adapter_local)return;
                box_->daemon_pid=::getpid();box_->daemon_start=ticks(::getpid());
                phase_=1;atomic(box_->ack).store(1,std::memory_order_release);return;
            }
            if(command==2&&phase_==1){
                if(!drained(h))return;
                if(parallel_component_accounting_v1::begin(box_->enabled))throw std::runtime_error("observer begin");
                phase_=2;atomic(box_->ack).store(2,std::memory_order_release);return;
            }
            if(command==3&&phase_==2){
                if(!atomic(box_->producer_finished).load(std::memory_order_acquire))throw std::runtime_error("STOP before producer finished");
                if(!drained(h)){
                    const auto front=frontend_local();const auto adapter=adapter_local();
                    if(!adapter.pending&&!adapter.order&&(front.parents||front.retiring)){
                        parallel_component_accounting_v1::RequestScope request;
                        progress_tail(); // Original next_event/advance only, no arbitrary horizon.
                    }
                    return;
                }
                if(parallel_component_accounting_v1::end())throw std::runtime_error("observer END depth");
                if(report_written_)throw std::runtime_error("duplicate observer export");
                if(parallel_component_accounting_v1::write_report(report_.c_str()))throw std::runtime_error("invalid observer export");
                export_closed(); // Existing accounting/report once, after gate.
                int report_fd=::open(report_.c_str(),O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
                if(report_fd<0)throw std::runtime_error("observer report fd");
                const auto sync_rc=::fsync(report_fd);::close(report_fd);
                if(sync_rc)throw std::runtime_error("observer report fsync");
                report_written_=true;phase_=3;atomic(box_->ack).store(3,std::memory_order_release);return;
            }
            throw std::runtime_error("observer gate transition");
        }catch(...){fail();throw;}
    }
};
}
