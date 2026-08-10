#include <bpf/libbpf.h>

#include <csignal>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace {
volatile std::sig_atomic_t stopping = 0;
void stop(int) { stopping = 1; }
}  // namespace

int main(int argc, char** argv)
{
    if (argc != 3) {
        std::fprintf(stderr, "usage: %s probe.bpf.o ready-file\n", argv[0]);
        return 64;
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
        std::fprintf(stderr, "hbfsim attach loader: cannot publish readiness\n");
        for (auto* link : links) {
            bpf_link__destroy(link);
        }
        bpf_object__close(object);
        return 69;
    }

    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    while (!stopping) {
        pause();
    }
    for (auto* link : links) {
        bpf_link__destroy(link);
    }
    bpf_object__close(object);
    return 0;
}
