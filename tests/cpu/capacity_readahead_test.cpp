// Readahead in capacity mode: on a demand miss for page N, bring N+1 and the
// pages after it into the HBM page cache so a later demand for them hits.
//
// This is a system-side prefetcher, not an application-side one. The
// application does not ask for it and does not know it happened; the only
// thing the policy has to go on is which pages have already been demanded.
//
// Two safety properties matter more than the speedup and are asserted below.
// Readahead never evicts, so it can never push out a page a demand still
// needs, and it is off unless switched on, so no existing measurement moves.
//
// Written before src/host_service/capacity_page_service.cpp had any readahead.

#include "../../src/cuda_runtime/hbm_cache.hpp"
#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/capacity_page_service.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <span>
#include <unordered_map>
#include <vector>

#include <unistd.h>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity readahead CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

constexpr std::size_t kPageBytes = 4096;

}  // namespace

int main()
{
    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-capacity-readahead-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);
    auto backing = hbfsim::host_service::BackingStore::create_deterministic(
        path, 8 * kPageBytes, 0x2468);

    std::unordered_map<std::uint64_t, std::vector<std::byte>> frames;
    std::vector<std::uint64_t> frame_addresses;
    for (std::uint64_t index = 0; index < 4; ++index) {
        const auto address = 0x1000 * (index + 1);
        frames.emplace(address, std::vector<std::byte>(kPageBytes));
        frame_addresses.push_back(address);
    }

    const hbfsim::host_service::CapacityFrameIo frame_io{
        .host_to_frame = [&](std::uint64_t frame,
                             std::span<const std::byte> data) {
            std::ranges::copy(data, frames.at(frame).begin());
            return true;
        },
        .frame_to_host = [&](std::uint64_t frame, std::span<std::byte> data) {
            std::ranges::copy(frames.at(frame), data.begin());
            return true;
        },
    };

    // 1. Readahead is off by default. A demand miss brings in exactly the page
    // demanded and nothing else, so no existing measurement changes.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        const auto demand = service.resolve(0, 0);
        CHECK(demand.status == hbfsim::RequestStatus::Ready);
        CHECK(!service.run_one_readahead());
        CHECK(cache.resolve(0).has_value());
        CHECK(!cache.resolve(1).has_value());
        CHECK(service.readahead_pages_fetched() == 0);
    }

    // 2. With readahead on, a demand miss for page 0 makes pages 1 and 2
    // resident once the worker has drained the queue, and a later demand for
    // page 1 is a hit that costs no media action.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        service.set_readahead_pages(2);

        const auto demand = service.resolve(0, 0);
        CHECK(demand.status == hbfsim::RequestStatus::Ready);
        CHECK(demand.media.flags == hbfsim::host_service::CapacityMediaRead);
        // Nothing has run the queue yet, so the demand itself did not drag the
        // readahead onto its own critical path.
        CHECK(!cache.resolve(1).has_value());

        CHECK(service.run_one_readahead());
        CHECK(service.run_one_readahead());
        CHECK(!service.run_one_readahead());
        CHECK(cache.resolve(1).has_value());
        CHECK(cache.resolve(2).has_value());
        CHECK(service.readahead_pages_fetched() == 2);

        const auto hit = service.resolve(1, 0);
        CHECK(hit.status == hbfsim::RequestStatus::Ready);
        CHECK(hit.media.flags == hbfsim::host_service::CapacityMediaNone);
        // The page the readahead brought in has to hold the right bytes, or
        // the hit is worse than the miss it replaced.
        CHECK(frames.at(hit.frame_address) ==
              backing.read_page(1, kPageBytes));
    }

    // 3. Readahead never forces a writeback. A readahead may take a clean
    // victim once the cache is full -- refusing to would make it do nothing at
    // all in the only regime capacity mode exists for -- but a dirty page
    // costs a program on the media, which is dearer than the read the
    // readahead would save, so a dirty victim is put back and the readahead
    // gives up instead.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        service.set_readahead_pages(1);

        // Fill every frame with a page written by the GPU, so each is dirty.
        for (std::uint64_t page = 0; page < 4; ++page) {
            CHECK(service.resolve(page, 1).status ==
                  hbfsim::RequestStatus::Ready);
        }
        CHECK(cache.dirty_pages() == 4);

        const auto fetched_before = service.readahead_pages_fetched();
        while (service.run_one_readahead()) {
        }
        // Nothing was fetched, because every victim on offer was dirty.
        CHECK(service.readahead_pages_fetched() == fetched_before);
        CHECK(cache.dirty_pages() == 4);
        for (std::uint64_t page = 0; page < 4; ++page) {
            CHECK(cache.resolve(page).has_value());
        }
    }

    // 3b. A readahead page enters unreferenced, so a page nothing ever asked
    // for is evicted before a page a demand brought in. Without this the
    // readahead would push out the very pages it is meant to help.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        service.set_readahead_pages(3);
        // One clean demand, then let the readahead fill the rest.
        CHECK(service.resolve(0, 0).status == hbfsim::RequestStatus::Ready);
        while (service.run_one_readahead()) {
        }
        CHECK(service.readahead_pages_fetched() > 0);
        // The demanded page survives the readahead that followed it.
        CHECK(cache.resolve(0).has_value());
    }

    // 4. A readahead that runs past the end of the backing store is skipped,
    // not reported as an error.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        service.set_readahead_pages(4);
        CHECK(service.resolve(7, 0).status == hbfsim::RequestStatus::Ready);
        while (service.run_one_readahead()) {
        }
        CHECK(service.readahead_pages_skipped() > 0);
    }

    // 4b. A readahead whose speculative read fails must not have cost a
    // resident page. With every frame taken by a clean demanded page, a
    // readahead for an address past the end of the backing store has to leave
    // all of them in place. The earlier version evicted a victim first and
    // read second, so this case silently destroyed a page a demand had brought
    // in; case 4 above does not catch it because free frames were still
    // available there.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        service.set_readahead_pages(2);
        // Four clean demanded pages fill all four frames. Page 7 is the last
        // page of the store, so its readahead runs off the end.
        for (const std::uint64_t page : {4ULL, 5ULL, 6ULL, 7ULL}) {
            CHECK(service.resolve(page, 0).status ==
                  hbfsim::RequestStatus::Ready);
        }
        for (const std::uint64_t page : {4ULL, 5ULL, 6ULL, 7ULL}) {
            CHECK(cache.resolve(page).has_value());
        }
        const auto fetched_before = service.readahead_pages_fetched();

        while (service.run_one_readahead()) {
        }

        // Nothing was fetched, because every queued page is past the end.
        CHECK(service.readahead_pages_fetched() == fetched_before);
        // And every demanded page survived the failed speculation.
        for (const std::uint64_t page : {4ULL, 5ULL, 6ULL, 7ULL}) {
            CHECK(cache.resolve(page).has_value());
        }
    }

    // 5. A queued page that a demand brought in first is skipped, not fetched
    // a second time. The page becomes resident between being queued and being
    // drained, which is the race the fill path has to survive.
    {
        hbfsim::runtime::HbmCache cache(frame_addresses);
        hbfsim::host_service::CapacityPageService service(backing, cache,
                                                          kPageBytes,
                                                          frame_io);
        service.set_readahead_pages(1);
        // Queues page 1.
        CHECK(service.resolve(0, 0).status == hbfsim::RequestStatus::Ready);
        // A demand fills page 1 before the queue is drained, and queues page 2.
        CHECK(service.resolve(1, 0).status == hbfsim::RequestStatus::Ready);

        const auto fetched_before = service.readahead_pages_fetched();
        const auto skipped_before = service.readahead_pages_skipped();
        while (service.run_one_readahead()) {
        }
        // Page 1 was skipped as already resident; only page 2 was fetched.
        CHECK(service.readahead_pages_fetched() == fetched_before + 1);
        CHECK(service.readahead_pages_skipped() == skipped_before + 1);
    }

    std::filesystem::remove(path);
    std::printf("capacity readahead: all checks passed\n");
    return 0;
}
