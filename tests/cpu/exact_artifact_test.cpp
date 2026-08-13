#include <hbfsim/exact_artifact.hpp>
#include <hbfsim/module_identity.hpp>

#include <json.hpp>

#include <array>
#include <barrier>
#include <cstddef>
#include <future>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

void require(bool condition, std::string_view message)
{
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

#define CHECK(expression) require(static_cast<bool>(expression), #expression)

std::string artifact(const std::vector<std::byte>& cubin,
                     std::string module_hash = std::string(64, '1'))
{
    return nlohmann::json{
        {"schema_version", 1},
        {"module_id", "ptx:sha256:" + module_hash},
        {"ptx_target", "sm_120"},
        {"toolchain",
         {{"cuda_release", "13.0"},
          {"ptxas_version", "ptxas release 13.0, V13.0.88"},
          {"nvdisasm_version", "nvdisasm release 13.0, V13.0.85"},
          {"cuobjdump_version", "cuobjdump release 13.0, V13.0.85"}}},
        {"hashes",
         {{"original_ptx_sha256", std::string(64, '2')},
          {"transformed_ptx_sha256", std::string(64, '3')},
          {"cubin_sha256", hbfsim::sha256_hex(cubin)},
          {"sass_sha256", std::string(64, '5')}}},
        {"kernels",
         {{{"name", "kernel"},
           {"registers", 48},
           {"spill_store_bytes", 16},
           {"spill_load_bytes", 8},
           {"static_shared_bytes", 1024},
           {"max_dynamic_shared_bytes", 49152},
           {"block_threads", 256},
           {"occupancy_blocks_per_sm", 2}}}}}
        .dump();
}

}  // namespace

int main()
{
    std::vector<std::byte> cubin{
        std::byte{0x7f}, std::byte{'E'}, std::byte{'L'}, std::byte{'F'},
        std::byte{0x12}, std::byte{0x34}};
    hbfsim::AotLoadTransactionStore transactions;
    const auto token = transactions.begin(cubin, artifact(cubin));
    CHECK(token != 0);
    const auto evidence = transactions.take_for_image(cubin.data());
    CHECK(evidence.has_value());
    CHECK(evidence->module_id == "ptx:sha256:" + std::string(64, '1'));
    CHECK(evidence->cubin_sha256 == hbfsim::sha256_hex(cubin));
    CHECK(evidence->sass_sha256 == std::string(64, '5'));
    CHECK(evidence->identity.front() == 0x11);
    CHECK(evidence->kernels.at(0).block_threads == 256);
    CHECK(evidence->aot_verified);
    CHECK(!transactions.take_for_image(cubin.data()).has_value());

    const auto wrong_pointer = transactions.begin(cubin, artifact(cubin));
    CHECK(wrong_pointer != 0);
    auto copy = cubin;
    CHECK(!transactions.take_for_image(copy.data()).has_value());

    const auto tampered = transactions.begin(cubin, artifact(cubin));
    CHECK(tampered != 0);
    cubin.back() = std::byte{0x55};
    CHECK(!transactions.take_for_image(cubin.data()).has_value());
    cubin.back() = std::byte{0x34};

    auto bad_hash = nlohmann::json::parse(artifact(cubin));
    bad_hash["hashes"]["cubin_sha256"] = std::string(64, '0');
    CHECK(transactions.begin(cubin, bad_hash.dump()) == 0);
    CHECK(transactions.begin({}, artifact(cubin)) == 0);

    auto missing_kernel = nlohmann::json::parse(artifact(cubin));
    missing_kernel["kernels"] = nlohmann::json::array();
    CHECK(transactions.begin(cubin, missing_kernel.dump()) == 0);

    auto malformed_id = nlohmann::json::parse(artifact(cubin));
    malformed_id["module_id"] = "ptx:sha256:invalid";
    CHECK(transactions.begin(cubin, malformed_id.dump()) == 0);

    const auto nested = transactions.begin(cubin, artifact(cubin));
    CHECK(nested != 0);
    CHECK(transactions.begin(cubin, artifact(cubin)) == 0);
    CHECK(!transactions.take_for_image(cubin.data()).has_value());
    transactions.end(nested);

    std::barrier start(3);
    auto worker = [&](char digit) {
        auto local = cubin;
        auto document = artifact(local, std::string(64, digit));
        start.arrive_and_wait();
        const auto local_token = transactions.begin(local, document);
        const auto local_evidence = transactions.take_for_image(local.data());
        return local_token != 0 && local_evidence.has_value() &&
               local_evidence->module_id ==
                   "ptx:sha256:" + std::string(64, digit);
    };
    auto first = std::async(std::launch::async, worker, '6');
    auto second = std::async(std::launch::async, worker, '7');
    start.arrive_and_wait();
    CHECK(first.get());
    CHECK(second.get());

    hbfsim::ModuleIdentityRegistry registry;
    CHECK(registry.associate(0x1234, *evidence));
    CHECK(registry.lookup(0x1234) == evidence->identity);
    const auto stored = registry.lookup_evidence(0x1234);
    CHECK(stored.has_value());
    CHECK(stored->aot_verified);
    CHECK(stored->cubin_sha256 == evidence->cubin_sha256);
    CHECK(!registry.associate(0x1234, *evidence));
    registry.erase(0x1234);
    CHECK(!registry.lookup_evidence(0x1234).has_value());

    return 0;
}
