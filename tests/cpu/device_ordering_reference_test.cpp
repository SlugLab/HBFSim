#include "../../src/host_service/control_layout.hpp"

#include <hbfsim/protocol.hpp>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <thread>
#include <vector>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

int main()
{
    constexpr std::uint32_t capacity = 2;
    constexpr std::uint64_t iterations = 1'000'000;
    std::vector<std::byte> storage(
        hbfsim::host_service::control_region_bytes(capacity));
    hbfsim::host_service::ControlView control(storage.data(), storage.size());
    CHECK(control.initialize(capacity));
    std::atomic<bool> failed{false};

    std::thread daemon([&] {
        for (std::uint64_t expected = 1; expected <= iterations; ++expected) {
            hbfsim::HbfRequest request{};
            while (!control.try_pop_request(request)) {
                std::this_thread::yield();
            }
            if (request.request_id != expected) {
                failed.store(true, std::memory_order_release);
            }
            const hbfsim::HbfCompletion completion{
                .request_id = request.request_id,
                .modeled_completion_ns = expected + 100,
                .modeled_ns = expected + 1,
                .service_ns = expected + 2,
                .cache_frame_address = expected + 3,
                .page_generation = static_cast<std::uint32_t>(expected),
                .status = static_cast<std::uint32_t>(
                    hbfsim::RequestStatus::Ready),
                .checksum = expected ^ 0xa5a5a5a5a5a5a5a5ULL,
                .reserved = expected + 4,
            };
            while (!control.try_publish_completion(request.sequence,
                                                    completion)) {
                std::this_thread::yield();
            }
        }
    });

    for (std::uint64_t request_id = 1; request_id <= iterations;
         ++request_id) {
        const hbfsim::HbfRequest request{
            .request_id = request_id,
            .sequence = 0,
            .arrival_ns = request_id,
            .logical_address = 0x1000 + request_id,
            .deadline_ns = request_id + 1000,
            .bytes = 4,
            .range_id = 1,
            .stream_id = 0,
            .operation = 0,
            .page_generation = static_cast<std::uint32_t>(request_id),
            .flags = 0,
        };
        std::uint64_t ticket = 0;
        while (!control.try_push_request(request, ticket)) {
            std::this_thread::yield();
        }
        hbfsim::HbfCompletion completion{};
        while (!control.try_consume_completion(ticket, completion)) {
            std::this_thread::yield();
        }
        if (completion.request_id != request_id ||
            completion.modeled_ns != request_id + 1 ||
            completion.status != static_cast<std::uint32_t>(
                                     hbfsim::RequestStatus::Ready) ||
            completion.checksum !=
                (request_id ^ 0xa5a5a5a5a5a5a5a5ULL)) {
            failed.store(true, std::memory_order_release);
        }
    }
    daemon.join();
    CHECK(!failed.load(std::memory_order_acquire));
    return 0;
}
