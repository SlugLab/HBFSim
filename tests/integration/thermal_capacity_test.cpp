#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/refresh_scheduler.hpp"

#include <hbfsim/exact_artifact.hpp>
#include <hbfsim/profile.hpp>

#include <chrono>
#include <filesystem>
#include <span>
#include <unistd.h>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

int main()
{
    using namespace std::chrono_literals;
    auto profile = hbfsim::load_profile(
        "configs/profiles/thermal-validation.json");
    auto thermal = *profile.thermal_reliability;
    thermal.refresh_damage_threshold = 0.0001L;
    const auto block_bytes = static_cast<std::uint64_t>(profile.page_bytes) *
                             profile.pages_per_block;
    const auto path = std::filesystem::temp_directory_path() /
        ("hbfsim-thermal-capacity-" + std::to_string(::getpid()));
    std::filesystem::remove(path);
    {
        auto store = hbfsim::host_service::BackingStore::create_deterministic(
            path, block_bytes, 0x5eed);
        const auto before = store.read_page(0, block_bytes);
        const auto before_hash = hbfsim::sha256_hex(std::span(before));

        hbfsim::host_service::RefreshScheduler scheduler(profile, thermal);
        scheduler.register_range(0, block_bytes, true);
        scheduler.age(85'000, 1s, 1);
        const auto plan = scheduler.plan(1, {});
        std::vector<std::byte> page;
        std::uint64_t refresh_bytes = 0;
        for (const auto& action : plan) {
            if (action.kind ==
                hbfsim::host_service::RefreshActionKind::Read) {
                page = store.read_page(action.page, action.bytes);
            } else {
                store.write_page(action.page, action.bytes, page);
            }
            refresh_bytes += action.bytes;
            CHECK(scheduler.complete(action, true));
        }
        store.flush();
        const auto after = store.read_page(0, block_bytes);
        CHECK(hbfsim::sha256_hex(std::span(after)) == before_hash);
        CHECK(scheduler.completed_blocks() == 1 &&
              scheduler.maximum_pec() == 1);
        CHECK(refresh_bytes == block_bytes * 2);

        hbfsim::host_service::RefreshScheduler programmed(profile, thermal);
        programmed.register_range(0, block_bytes, false);
        programmed.record_program(0, block_bytes);
        CHECK(programmed.maximum_pec() == 1);
        programmed.age(85'000, 1s, 1);
        const auto second = programmed.plan(1, {});
        for (const auto& action : second) {
            CHECK(programmed.complete(action, true));
        }
        CHECK(programmed.maximum_pec() == 2);
    }
    std::filesystem::remove(path);
    return 0;
}
