# UCIe Stage 2 CPU device checkpoint

Date: 2026-09-29. Source is the uncommitted `feature/ucie-interface-20260929` worktree at `70b1f93998ff142a8ab21f1a1cb75b7db84b8b90`; `origin/main` resolved to the same commit at final verification. Nothing was committed or pushed. Host `threadripper` on giga has GPU UUID `GPU-45044e90-a553-930c-b950-ba660acb37fc`; this CPU-only run did not use it or stop the existing vLLM. GCC 15.2.0, CMake 4.2.3, Ninja 1.13.2, Python 3.14.4, OpenSSL 3.5.5. Pinned bpftime `ec26daecc8e787fb80fd95dd596a576404a5e36e` and MQSim `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`; existing MQSim patches 0001–0003 applied in the private build copy.

Stage 2 adds the separate [CPU device frontend](../ucie/STAGE2_DEVICE_COALESCING.md), complete-span backing registry, in-flight page coalescer, bounded two-slot bank buffer, and strict native MQSim proof. It leaves Stage 1, daemon/GPU integration, PTX/helpers, public ABI, and MQSim core scheduling unchanged. The 512 GiB/stack configuration is parsed and its 16-module bank geometry validated, but a 512 GiB high-address real-engine run and four-stack service are Stage 3 work. AoU v0.8 Appendix D labels the HBF profile under development. A native MQSim chip per HBF bank is a service-resource abstraction, not physical die equivalence.

## Exact build and verification

From `/root/hbfsim-exp/ucie-interface-20260929/worktree` on giga:

```bash
HBFSIM_BUILD_JOBS=4 bash scripts/reproduce_ucie_stage2_cpu.sh build-stage2-clean-a full \
  > /root/hbfsim-exp/ucie-interface-20260929/build-stage2-clean-a.full.log 2>&1
HBFSIM_BUILD_JOBS=4 bash scripts/reproduce_ucie_stage2_cpu.sh build-stage2-clean-a full \
  > /root/hbfsim-exp/ucie-interface-20260929/build-stage2-clean-a.final.log 2>&1
python3 build-stage2-clean-a/validate_schema.py
git diff --check
```

The script configured a **new** build directory inside the checkout with CUDA OFF, MQSim and UCIe ON, evaluation tools and testing ON, and the optional PkgConfig probe OFF. It built all default CPU targets (221 Ninja actions), ran **61/61 CTest PASS**, and ran the real MQSim ON/OFF eight-read benchmark. After correcting the duplicate-active-ID test to submit at the current time, the second invocation rebuilt that test and again passed **61/61** in 16.78 s CTest wall time. The three profile JSON files passed Draft 2020-12 schema validation. `git diff --check` produced no diagnostics. The full raw build logs are retained in the local Stage 2 review package, and the raw benchmark JSONL is [ON](artifacts/2026-09-29-ucie-stage2-on8.jsonl) and [OFF](artifacts/2026-09-29-ucie-stage2-off8.jsonl).

The real ON benchmark recorded 8 distinct successful 64 B AR/R transactions, 512 B application and AXI payload, 7 in-flight joins, **1** native NAND read command and **4096 B** media submission. OFF recorded the same 8 transactions and payload, **8** commands and **32768 B** media submission. ON used 1536 B AR wire and 1280 B R wire; OFF used 8448 B and 2560 B because completions change legal packet packing. Native per-group proof checks the actual LPA, channel/chip/die/plane, command count, and `MEDIA_BEGIN/END` and `DATA_OUT_BEGIN/END` observations independently of the bounded diagnostic trace. The integration fixture observed overlapping sense intervals for different native chips and ordered, non-overlapping intervals for the same chip. The test also covers chip ID 255 for NCDU16 and the 512 GiB geometry arithmetic without allocating 512 GiB of MQSim metadata.

Deterministic tests independently cover 2-slot pinning and third-page backpressure, finite cache reuse, one of eight waiter cancel/timeout with seven successes, last waiting waiter cancel, old generation callback versus new generation, same-nanosecond completion-before-AR, forged backing fields, slow caller consumption, AR/R propagation deadlines, same-ID reverse media completion/order, trace overflow without proof loss, and mixed Stage 1/2 single-engine exclusion. Errors do not create a successful 64 B R packet. Unconsumed responses retain resources until physical drain and caller consumption as documented.

The four time domains remain separate: native sense `MEDIA_BEGIN/END`, MQSim raw callback and modeled completion, AoU link serialization/propagation, and caller poll time. The reported 16.78 s is test harness wall-clock time, not hardware service time. Host CPU overhead and GPU timings were not measured. The ON/OFF command reduction is an engineering behavior result; it is not a measured speedup or hardware accuracy result.

## Retained failures and limits

During implementation, a profile compile first used the unavailable `<nlohmann/json.hpp>` path and was corrected to the pinned `<json.hpp>`; an early frontend horizon-drain assertion and a fake profile page-size fixture failed before correction. Those failed diagnostics were recorded in the execution conversation but were not captured as separate log files; the final raw successful logs are preserved in the review package. The Stage 1 unrelated PkgConfig CPU probe issue remains handled by this build's explicit CMake option, not a global tool change. This Stage 2 path does not simulate hardware cancellation packets, ECC/CRC/retry, writes, burst reads, physical page-buffer circuitry, the full HBF internal bus, bit-level AoU encoding, PHY behavior, or production GPU/daemon requests. Software deadline is not OCP readiness timeout.
