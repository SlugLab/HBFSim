#include "worker_handler.hpp"
#include "worker_io.hpp"
#include "shm_rpc.hpp"
#include <sys/poll.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>

// Cold activation records share one inherited stderr open-file description.
// A positive short write is an incomplete record and is never retried.
static void write_activation_record(const char* tag,std::uint32_t stack_id)
{
    const std::string line=std::string(tag)+" stack="+std::to_string(stack_id)+"\n";
    ssize_t written;
    do { written=::write(STDERR_FILENO,line.data(),line.size()); }
    while (written<0 && errno==EINTR);
    if (written<0 || static_cast<std::size_t>(written)!=line.size())
        throw std::runtime_error("worker activation record write failed or short");
}

int main(int argc,char** argv)
{
    if (argc!=5 || (std::string(argv[1])!="--profile" &&
                    std::string(argv[1])!="--top-profile") ||
        std::string(argv[3])!="--stack") {
        std::cerr << "usage: ucie_stack_worker --profile PATH --stack ID\n";
        return 2;
    }
    try {
        const auto parsed=std::stoull(argv[4]);
        if (parsed>std::numeric_limits<std::uint32_t>::max())
            throw std::out_of_range("worker stack ID out of range");
        const auto stack_id=static_cast<std::uint32_t>(parsed);
        std::unique_ptr<hbfsim::ucie::shm_rpc::Shared,
                        void(*)(hbfsim::ucie::shm_rpc::Shared*)> shm(
            nullptr,hbfsim::ucie::shm_rpc::unmap);
        if (const auto* shm_fd=std::getenv("HBFSIM_UCIE_SHM_FD")) {
            if (std::string(shm_fd)!="4")
                throw std::invalid_argument("worker mailbox fd mismatch");
            shm.reset(hbfsim::ucie::shm_rpc::map_fd(4,false));
            ::close(4);
        }
        hbfsim::ucie::WorkerCommandState handler(argv[2],
            std::string(argv[1])=="--top-profile",stack_id,shm!=nullptr,false);
        const auto sideband_alive=[] {
            pollfd state{3,static_cast<short>(POLLIN|POLLHUP|POLLERR),0};
            int result;
            do { result=::poll(&state,1,0); } while (result<0 && errno==EINTR);
            if (result<0) throw std::runtime_error("worker sideband poll failed");
            return result==0 || !(state.revents&(POLLHUP|POLLERR|POLLNVAL));
        };
        while (true) {
            auto frame=handler.shm_active() ?
                hbfsim::ucie::shm_rpc::receive(shm->request,
                    std::chrono::hours(24),sideband_alive) :
                hbfsim::ucie::worker_io::receive_frame(3,std::chrono::hours(24));
            const bool typed_before=handler.typed_active();
            const bool shm_before=handler.shm_active();
            auto reply=handler.handle(std::move(frame));
            if (shm_before)
                hbfsim::ucie::shm_rpc::send(shm->response,reply,
                    handler.reply_timeout(),sideband_alive);
            else hbfsim::ucie::worker_io::send_frame(3,reply,
                    handler.reply_timeout());
            if (!typed_before && handler.typed_active())
                write_activation_record("TYPED_RPC_V1_ACTIVE",stack_id);
            if (!shm_before && handler.shm_active())
                write_activation_record("SHM_RPC_V1_ACTIVE",stack_id);
            if (handler.stopped()) return handler.exit_code();
        }
    } catch (const std::exception& error) {
        std::cerr << "stack worker fatal: " << error.what() << '\n';
        return 1;
    }
}
