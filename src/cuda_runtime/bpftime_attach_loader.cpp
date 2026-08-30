#include <bpf/libbpf.h>

#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

int main(int argc, char** argv)
{
    if (argc != 3) {
        std::fprintf(stderr, "usage: %s probe.bpf.o ready-file\n", argv[0]);
        return 64;
    }
    sigset_t termination_signals;
    sigemptyset(&termination_signals);
    sigaddset(&termination_signals, SIGINT);
    sigaddset(&termination_signals, SIGTERM);
    if (pthread_sigmask(SIG_BLOCK, &termination_signals, nullptr) != 0) {
        std::fprintf(stderr, "hbfsim attach loader: cannot block signals\n");
        return 70;
    }

    bpf_object* object = bpf_object__open_file(argv[1], nullptr);
    if (libbpf_get_error(object) != 0 || object == nullptr) {
        std::fprintf(stderr, "hbfsim attach loader: cannot open BPF probe\n");
        return 65;
    }
    if (bpf_object__load(object) != 0) {
        std::fprintf(stderr, "hbfsim attach loader: cannot load BPF probe\n");
        bpf_object__close(object);
        return 66;
    }

    std::vector<bpf_link*> links;
    bpf_program* program = nullptr;
    bpf_object__for_each_program(program, object)
    {
        bpf_link* link = bpf_program__attach(program);
        if (libbpf_get_error(link) != 0 || link == nullptr) {
            std::fprintf(stderr, "hbfsim attach loader: CUDA attach failed\n");
            for (auto* current : links) {
                bpf_link__destroy(current);
            }
            bpf_object__close(object);
            return 67;
        }
        links.push_back(link);
    }
    if (links.empty()) {
        std::fprintf(stderr, "hbfsim attach loader: no CUDA attach entries\n");
        bpf_object__close(object);
        return 68;
    }

    std::ofstream ready(argv[2], std::ios::trunc);
    ready << "HBFSIM_BPFTIME_ATTACH_READY v1\n"
          << "shm=bpftime\n"
          << "attach_type=8\n"
          << "entries=" << links.size() << '\n';
    ready.close();
    if (!ready) {
        std::fprintf(stderr,
                     "hbfsim attach loader: cannot publish readiness\n");
        for (auto* link : links) {
            bpf_link__destroy(link);
        }
        bpf_object__close(object);
        return 69;
    }

    int received_signal = 0;
    int wait_status = 0;
    do {
        wait_status = sigwait(&termination_signals, &received_signal);
    } while (wait_status == EINTR);
    for (auto* link : links) {
        bpf_link__destroy(link);
    }
    bpf_object__close(object);
    if (wait_status != 0) {
        std::fprintf(stderr, "hbfsim attach loader: sigwait failed: %s\n",
                     std::strerror(wait_status));
        return 71;
    }
    return 0;
}
