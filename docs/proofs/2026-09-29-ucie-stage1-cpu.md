# UCIe Stage 1 CPU integration checkpoint

Date: 2026-09-29. Source: `feature/ucie-interface-20260929` at base/HEAD `70b1f93998ff142a8ab21f1a1cb75b7db84b8b90`, with the uncommitted UCIe files listed in this checkpoint. At verification, `origin/main` was also `70b1f93998ff142a8ab21f1a1cb75b7db84b8b90`. Nothing was committed or pushed. Host identity was `threadripper` on giga; GCC 15.2.0, CMake 4.2.3, Ninja 1.13.2, OpenSSL 3.5.5, and Python 3.14.4. Pinned submodules: bpftime `ec26daecc8e787fb80fd95dd596a576404a5e36e`; MQSim `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`. The original MQSim build-copy patches `0001`, `0002`, `0003` applied cleanly. No GPU build or run was performed and the unrelated vLLM was left alone.

This checkpoint establishes a default-off CPU UCIe/AoU structural interface with a real patched MQSim consumer. [The interface contract](../ucie/STAGE1_CPU_INTERFACE.md) describes the modeled layers and exclusions. AoU v0.8 labels its Appendix D HBF profile **under development**; this implementation models that draft profile. The UCIe profile's initial credits (AR 12/R 56 granules) and zero propagation are labeled **SCENARIO_ASSUMPTION**; the physical x64/16 GT/s grade and AoU message/flit dimensions come from the cited specifications. This does not establish link silicon latency, hardware bandwidth accuracy, bit-level AoU compliance, a full HBF page-buffer model, or any production GPU service.

## Rebuild and tests

The final full build was created in the independent ignored directory `build-ucie-clean2/` inside the source checkout. This location satisfies the repository's existing service-client path contract. Exact commands:

```bash
git submodule update --init third_party/bpftime third_party/mqsim
cmake -S . -B build-ucie-clean2 -G Ninja \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON \
  -DHBFSIM_ENABLE_UCIE=ON -DHBFSIM_ENABLE_EVAL_TOOLS=ON \
  -DBUILD_TESTING=ON -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=TRUE
cmake --build build-ucie-clean2 -j4
ctest --test-dir build-ucie-clean2 --output-on-failure -j4
./scripts/reproduce_ucie_stage1_cpu.sh build-ucie-clean2 focused
```

Results: the full CPU build succeeded (the Ninja plan listed 203 actions), and full CPU CTest **56/56 passed** in 16.98 wall-clock seconds; focused reproduction script found no build work and passed `mqsim_online`, `mqsim_horizon`, `ucie_link`, `ucie_mqsim` (4/4). The script then ran four reads against real MQSim. The fixed independent oracle covered 16 ReadReq messages/one flit, 17/two flits, four ReadData512 messages/two flits with a cross-flit fragment, 3/14-granule legal credit-return decomposition, slow consumer backpressure, and strict profile rejection. A deterministic fake media port forced reverse same-ID media completion and backend error; the separate production test required real MQSim arrival, admission and callback observations plus native NAND READ command observations.

The real integration test's raw stdout was:

```text
native_mqsim arrivals=3 admissions=3 callbacks=3
native_nand_read_commands=3
native_callback_ns=10312 existing_cap_ready_ns=40960002 ucie_return_ns=40960004
PASS fake fault/order and real MQSim/NAND CPU integration
```

The low-cap case deliberately set the existing MQSim aggregate service cap to 100,000 B/s; its raw native callback preceded modeled readiness. The 2 ns difference from modeled readiness to return is the selected structural UCIe serialization for that single response, not measured transport latency. The ordinary four-request benchmark reported accepted/consumed/MQSim arrivals/admissions/callbacks of 4 each, 256 application bytes, 16,384 submitted media bytes, 1024 AR wire bytes and 1024 R wire bytes after draining the final credit grants. Its source is [the real MQSim benchmark](../../benchmarks/mqsim/ucie_mqsim_bench.cpp), with the exact [profile](../../configs/profiles/ucie/hbf-grade2-single-module.json). The [raw benchmark JSONL](artifacts/2026-09-29-ucie-stage1-bench4.jsonl) is retained in this checkout; all command logs remain in `/root/hbfsim-exp/ucie-interface-20260929/` and are collected in the local evidence package.

The old aggregate cap is a separate readiness bound; it is not charged as another UCIe serialization cost. CPU modeled device/link time was observed as above. Host-service CPU time and emulator overhead were **not measured**. The full CTest wall-clock 16.98 seconds is build/test wait time, not modeled device latency. No hardware speedup or accuracy factor follows from these numbers.

## Retained failures and boundaries

The initial CPU configure failed at an unrelated existing CMake `.cu` probe target when installed libbpf was discovered despite CUDA being disabled. The final CPU command disables optional PkgConfig discovery within that build; no global dependency changed. The first new UCIe compiler attempt used `<nlohmann/json.hpp>`, while this repository's pinned header is `<json.hpp>`; the UCIe target was corrected to use the existing bpftime include path. A test helper named `read` collided with POSIX `read`; only that test name was changed. An initial full-suite build outside the checkout passed 55/56 tests; the sole existing `mqsim_service_client` requires its binary and artifacts inside the experiment checkout. The independent `build-ucie-clean2/` reproduced that constraint and passed 56/56 without altering the service test. All original configure/build/CTest logs remain under `/root/hbfsim-exp/ucie-interface-20260929/`.

The no-MQSim negative configure was checked: `HBFSIM_ENABLE_UCIE=ON` with `HBFSIM_ENABLE_MQSIM=OFF` fails with `HBFSIM_ENABLE_UCIE requires the real MQSim backend`. The implementation leaves the original `protocol.hpp` ABI, PTX/device helpers, MQSim patches and backend algorithms unchanged. Its optional build target is not linked into `hbfsimd` or the GPU path. The added source, tests, profile, schema, reproduction script, and checkpoint remain uncommitted for review.
