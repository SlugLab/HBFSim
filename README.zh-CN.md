# HBFSim

**一个在真实 GPU 上运行真实 LLM 负载的快速 HBF 模拟器。**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml/badge.svg)](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml)
[![arXiv:2609.09800](https://img.shields.io/badge/arXiv-2609.09800-b31b1b.svg)](https://arxiv.org/abs/2609.09800)

[English](README.md) | **简体中文**

HBFSim 是一个全面评估 High-Bandwidth Flash（**HBF**）的平台，HBF 是第一个把 NAND flash 堆叠在加速器封装内的内存层，有着媲美 HBM 的带宽和 SSD 的容量。首批 HBF 器件预计在 2027 年初送样。HBFSim 以极低的模拟代价在真实 GPU 上正常执行完整 LLM（如 Qwen3-30B），并在执行过程中对这个应用施加 HBF 的延时、容量与热效应。

📖 [HBF 快速介绍](#hbf-intro) \
📦 [主要特性](#features) \
🚀 [快速开始](#quickstart) \
⚙️ [工作原理](#mechanisms) \
📄 [我们的论文](https://arxiv.org/abs/2609.09800) \
📝 [TODO.md 里还没做完的工作](TODO.md)

<a id="question"></a>
## 为什么是 HBFSim？设计目标是什么？

内存墙问题在 LLM 推理阶段越来越严重。LLM 服务受限制于 HBM 有限的容量和高昂的价格。因此工业界寄希望于 HBF。系统设计者需要评估 HBM 与 HBF 之间的组织架构，决定把哪些数据放进 HBF。传统的模拟器方法给不出答案：

| 现有方法 | 执行真实负载 | 跑在真实硬件上 | 建模介质 | 建模温度 | 为什么不够 |
|---|:---:|:---:|:---:|:---:|---|
| 固定 trace 回放 | ✗ | ✗ | ✓ | ✗ | 换一种 HBF 配置可能改变 KV 分配、数据迁移和 kernel 调用，原配置下的 trace 不包含这些变化，重新采集要花大量时间。 |
| 全 GPU 周期级模拟 | ✓ | ✗ | ✓ | ✗ | 模拟开销不可接受：Accel-Sim 单线程模拟 lavaMD 要[超过 5 天](#methodology)。 |
| CPU / NVMe 拦截 | ✓ | ✓ | ✓ | ✗ | 拦截点不在 GPU 封装内访存路径上。 |
| 厂商参数表 | ✗ | ✗ | ✗ | ✗ | HBF 的时序参数尚未公布，规范把它们都留给了各厂商的产品数据表。 |
| HBFSim | ✓ | ✓ | ✓ | ✓ | — |

HBFSim 的设计目标有下面四条：

**1. 只模拟 HBF，计算交给真实 GPU**

- [我们的论文](https://arxiv.org/abs/2609.09800)的核心判断是，评估 HBF 不需要模拟 GPU，只需要建模 HBF 对程序可见的影响。
- HBFSim 只把注册为 HBF 的地址区间内的访问接入 HBF 模型；计算仍由真实 GPU 执行，普通显存访问保留原生路径。

**2. 模拟速度快**

- HBFSim 不在软件里重新模拟 GPU，因此不需要像周期级模拟器那样逐条模拟 warp 指令。
- GPU 本地快速路径还避免了逐请求的主机往返。
- HBFSim 的模拟速度约是基于 GPGPU-Sim 的 HBF 模拟的 **4,650 倍**。

**3. 改配置就能比较不同 HBF 架构，不用重录 trace**

- 每次实验都在真实 GPU 上重新运行负载本身，不回放旧的访问序列。
- HBF 参数写在 profile（`configs/profiles/` 下的 JSON 配置文件）里，换一组参数只需要换一份 profile。
- 同一套地址注册既可以用 Timing-only 模式只研究延迟，也可以用 Capacity 模式研究超出物理显存的容量。

**4. 回答具体的设计问题**

HBFSim 用来在真实 LLM 负载上评估各种问题，例如：

- **HBM 与 HBF 的容量划分**：HBF 和 HBM 的组织架构目前并无定论。目前至少有 4 种架构：
  - HBM 作 HBF cache 的菊花链架构
  - HBF 和 HBM 并排放置占用 GPU 引脚的架构
  - 全 HBF 架构
  - HBF 和 HBM 交织架构

  如何衡量不同架构、不同 HBF 和 HBM 容量划分比例，是我们模拟器努力的方向之一。
- **数据放置**：权重、不同生命周期的 KV cache，哪些放到 HBM 上，哪些放到 HBF 上。
- **HBF 物理参数如何反过来影响架构设置**：HBF 高于 HBM 两个数量级的读写延迟极大地考验 LLM KV 的预取算法。此外，一个 GPU 连接的 HBF device 数、UCIe 模拟以及 page 条带化也是我们的目标之一。
- **温度带来的降速**：芯片内部晶体管所在处的温度，比封装表面的温度高。
  - NAND 温度越高，数据保持期限越短，需要更频繁地刷新，而刷新也要占用带宽。
  - 模拟器如果不建模温度，报出的就是一个不随时间变化的速率，会高估 HBF 在长时间运行中的可持续服务速率。
  - 温度怎样让 HBF 变慢，见 [HBF 简介](#hbf-intro)。

<a id="features"></a>
## 主要特性

- **自动改写 PTX，无需手工修改 kernel**：HBFSim 使用 eBPF 拦截 GPU 执行路径，自动改写受支持的 PTX global load/store，不需要逐个手工修改 CUDA kernel。
- **闭源 kernel 的 SASS→PTX 恢复（可选）**：让缺少 PTX 的闭源预编译 kernel 也能进入同一套插桩流程。
- **按地址注册，只给 HBF 地址计时**：应用通过 [C API](include/hbfsim/api.h) 注册 HBF 地址范围；未注册的地址保持原生访存路径，不注入 HBF 延迟。
- **Timing-only 与 Capacity 两种模式**：Timing-only 模式下数据留在 GPU 显存中，只对已注册访问注入模拟延迟。Capacity 模式用文件承载逻辑数据，GPU 显存中的有界 page cache 保存常驻页面，让超出物理显存的逻辑容量也能运行；已在显存为 98 GB 的 GPU 上验证 110 GiB 逻辑地址范围的稀疏访问。
- **两种时序后端**：`reference` 把请求交给在线 MQSim，使用详细介质服务模型；`fast` 根据所选 profile 在 GPU 本地计算时序，避免逐请求的主机往返；另有结合两者的 `hybrid` 模式。
- **一个 GPU 连接多个 HBF device（UCIe，可选）**：建模 AXI 读传输、跨 HBF device 的 page 条带化与请求合并，每个 HBF device 对应一个 MQSim worker；通过 `HBFSIM_ENABLE_UCIE=ON` 启用；当前为只读接口，已完成 CPU 侧验证。
- **热学与可靠性（可选）**：把结温、服务策略、数据保持期限与刷新连接起来。
- **启动前的覆盖检查**：coverage gate 在每次 kernel 启动前检查该次执行是否处于可建模范围；Capacity 地址可能进入无法证明安全的代码时，拒绝 kernel 启动。
- **建模时间与墙上时间分开报告**：介质基准与 CUDA 微基准分别输出建模的器件时间和墙上时间，不把模拟器自身的开销算成 HBF 延迟。
- **接入真实推理框架**：提供 vLLM 与 llama.cpp 适配器。

<a id="quickstart"></a>
## 快速开始

研究中记录下来的环境配置见 [RTX 5090 构建环境与完整构建命令](docs/build/rtx-5090-environment.md)。遇到构建或运行问题时，请使用[排错指南与问题报告表](docs/troubleshooting.md)。

HBFSim 需要原生 Linux、CMake 3.25 或更新版本、Ninja、支持 C++20 的编译器、OpenSSL 和 Python 3。CUDA 组件需要 CUDA 12.8 或更新版本。介质模拟器和它的基准程序不需要 CUDA 就能构建，下面的命令用的就是这种配置。

```bash
git clone https://github.com/SlugLab/HBFSim.git
cd HBFSim

HBFSIM_ENABLE_CUDA=OFF HBFSIM_ENABLE_MQSIM=ON ./scripts/bootstrap.sh
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

### 示例运行

介质基准不需要 GPU 就能运行。介质基准用确定性的顺序请求驱动在线 MQSim 参考模型，并输出一份 JSON 文档：

```console
$ ./build/hbf_mqsim_bench --profile configs/profiles/nominal.json \
    --requests 1024 --bytes 16384 --operation read --arrival-gap-ns 0
{
  "effective_profile": {
    "aggregate_bandwidth_bytes_per_s": 512000000000,
    "blocks_per_plane": 16,
    "capacity_bytes": 68719476736,
    "channels": 32,
    "hbm_cache_bytes": 67108864,
    "nand_technology": "SLC",
    "plane_allocation_scheme": "CWDP",
    "program_latency_ns": 100000,
    "read_latency_ns": 10000
  },
  "engine": "mqsim-hbf-media-only",
  "latency_ns": { "average": 170115, "p50": 164960, "p99": 329920 },
  "modeled_bandwidth_bytes_per_s": 50852376333.65665,
  "requests": { "completed": 1024, "submitted": 1024 },
  "simulator_requests_per_s": 6557.51045857279,
  "timing_ns": { "modeled": 329920, "wall": 156156823 }
}
```

`timing_ns.modeled` 是 HBFSim 算出的器件时间；`timing_ns.wall` 是这次计算本身花了多长时间。HBFSim 在所有地方都把这两个时间分开报告，因为模拟器可能功能正确，自身开销却超过所建模的延迟。

测试结果、CI 跳过的测试、CPU 构建报错与介质基准的有效容量，见 [CPU 构建与测试说明](docs/build/cpu-build-and-tests.md)。

<a id="hbf-intro"></a>
## HBF 简介

HBF 把 NAND flash 堆叠在加速器封装内，位于 HBM 之下、SSD 之上。按 [v0.7.0 规范](docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf)第 3 章，HBF 的容量是 HBM 的 8 到 16 倍，读带宽与 HBM 相同；规范第 4.1 节与表 4 给出的单个 HBF device 的容量示例是 **512 GiB**（一个 HBF device 内堆叠 16 层 NAND die，即 16-Hi），最高速度档单个 HBF device 的用户带宽是 **3.072 TB/s**。HBF 目前还买不到：SanDisk 计划在 2027 年送出首批 HBF 推理产品的样品（据 [StorageReview 对 SanDisk 2026 年 8 月投资者日的报道](https://www.storagereview.com/news/sandisk-tapes-out-its-first-hbf-memory-die-targets-2027-for-inference-product-samples)），SK hynix 在 FMS 2026 上展示的时间线则把 HBF 样品放在 2028 年初。

因为 NAND 对温度敏感，在 20 到 70 °C 之间，每升温 10 °C 就会让数据保存周期缩短到原来的约 1/4 到 1/3（这是我们按 [HeatWatch](https://www.cs.cmu.edu/~yixinluo/index_files/heatwatch_hpca18.pdf) 在 3D NAND 上测得的激活能 1.04 eV、用 Arrhenius 公式推算的结果）。发热会让 HBF 无法持续达到峰值速度，产生严重的热节流问题：规范定义了 thermal throttling，结温达到阈值后，器件降低功耗，读写变慢，严重时暂停操作。规范还规定，通电时在 85 °C 下保证数据保持 24 小时，一般每 24 到 48 小时要刷新一次（第 9 章与第 11.5 节）。HBF device 与 GPU、HBM 同在一个封装里；一个 GPU 连接多个 HBF device 时，GPU、相邻的 HBM 与相邻的 HBF device 发出的热都会抬高每个 HBF device 的温度。GPU 自身的热状态也会改变性能：我们在同一块 GPU 上实测单独运行的同一个 BF16 8192x8192 矩阵乘，GPU 冷态时为 379.117 TFLOP/s，GPU 热态时为 348.427 TFLOP/s，低 8.10%；这个降幅来自 GPU 计算芯片自身降频，不是 HBF 的速率（[实验记录](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md)）。

业界对 HBF 的评估结论并不一致，而且各方的结论都来自自己的测试或模型：SanDisk 2025 年 7 月的 HBF 事实页标明其数字来自内部测试与模拟；SK hynix 研究者的 [H³ 论文](https://doi.org/10.1109/LCA.2026.3660969)使用内部模拟器；三位 Micron 研究者在 HotInfra '26 研讨会上的[论文](https://hotinfra.org/2026/papers/hotinfra26-final83.pdf)使用 roofline 解析模型，作者声明该论文不一定代表 Micron 的官方立场；Oxmiq Labs 在 Hot Chips 2026 上使用机架级模拟（[ServeTheHome 报道](https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/)）。HBM 与 HBF 各配多少容量、哪些张量放进 HBF，这类设计决策等不到器件上市。

<a id="architecture"></a>
## 总体架构

<div align="center">
  <img src="docs/assets/hbfsim-architecture.png" alt="HBFSim 架构：主机侧注册与 PTX 改写；真实 GPU 上的访问按是否落在已注册的 HBF 地址范围分成两支，已注册的在 Capacity 模式下由 GPU 显存里的 Frame cache 供给、未命中时由主机容量服务从 HBF 数据文件读页，未注册的照原路径访问；下方是在线 HBF 时序与热学模型" width="900">
</div>

**主机侧确定哪些访问需要建模，真实 GPU 执行计算，HBF 模型提供介质服务时间与容量行为。** 图中的实线框表示默认运行时组件，虚线框表示可选或实验组件；具体启用条件见[默认配置](#configuration)。图中 registered 一支连到 Frame cache，对应 Capacity 模式；Timing-only 模式下，已注册访问的数据同样留在 GPU 显存原处，只由 Device helpers 的等待加上模拟延迟。

<a id="methodology"></a>
## 为什么不能沿用传统评估方法

**评估 HBF，不能只给现有 GPU 模型换一组内存参数，也不能拿一条固定 trace 代替改变容量配置后的真实执行。** 我们要回答的是：换一种 HBM–HBF 配置后，同一个推理系统会怎样运行，而不只是同一串请求在另一个介质模型里需要多久。

### 固定 trace 回放，无法自行产生新配置下的执行行为

对于 HBF 目前的架构，与 HBM 的组织关系等都并无定论，这些参数可能改变 LLM 的 KV 分配、数据迁移和 kernel 调用。trace 模拟器无法灵活地模拟不同 HBF 架构。这是因为原配置下录制的 trace 不会自动出现新配置需要执行的指令，也不会自动删除不再需要的迁移。**把同一条 trace 换参数后重新回放，无法等价于重新运行一次采用新容量配置的系统。** trace 模拟器必须花大量时间来重新采集、扩展 trace 或增加反馈机制，同时 trace 回放本身也有很大的开销。这让 trace 模拟器无法快速模拟 HBF 下不同架构选择。

### 全 GPU 周期级模拟，把计算也变成了评估瓶颈

完整 GPU 微架构模拟带来的开销不可接受。以 [GPGPU-Sim](https://doi.org/10.1109/ISPASS.2009.4919648) 与 [Accel-Sim](https://doi.org/10.1109/ISCA45697.2020.00047) 这两个周期级 GPU 模拟器为例，下表列出相关论文报告的模拟速度，数据取自[我们的论文](https://arxiv.org/abs/2609.09800)动机一节的表格：

| 模拟器与设置 | 指标 | 数值 |
|---|---|---:|
| Accel-Sim 模拟 lavaMD，单线程 | 墙上时间 | 超过 5 天 |
| Accel-Sim 模拟 lavaMD，单线程 | 比真实硬件慢的倍数 | 10,748,031 倍 |
| Accel-Sim 模拟 lavaMD，16 线程 | 比真实硬件慢的倍数 | 766,423 倍 |
| Accel-Sim，trace 模式 | 每秒模拟的 warp 指令数 | 12,500 |
| Accel-Sim，execution 模式 | 每秒模拟的 warp 指令数 | 6,000 |
| GPGPU-Sim 3.x | 每秒模拟的 warp 指令数 | 3,000 |
| Accel-Sim 2.0 | 每秒模拟的 warp 指令数 | 27,500 |

lavaMD 是一个 GPU 基准程序；lavaMD 的三行数据出自 Huerta 与 González 的论文 [Parallelizing a Modern GPU Simulator](https://arxiv.org/abs/2502.14691)。按每秒模拟的 warp 指令数，表中最快的是 Accel-Sim 2.0，也只有 27,500 条。

周期级模拟的执行成本限制了生产规模模型、长时间运行和多维参数扫描；抽取一个 Transformer block 再外推，也不能直接替代容量变化后的端到端执行。[Accel-Sim 2.0](https://arxiv.org/abs/2608.22602)

### CPU 或 NVMe 拦截点，不在 HBF 的访问路径上

主机页表、CPU 性能计数器和 NVMe 队列，是相应系统中有效的拦截位置，但 **GPU 发出的封装内 HBF load/store 不经过这些位置**。不能把主机侧一次 I/O 的等待时间直接搬到 GPU 访存上，就认为已经模拟了 HBF。HBFSim 将受支持的访问接入点放在 GPU kernel 内部。[PTX 插桩设计](docs/skills/02-ptx-instrumentation.md)

<div align="center">
  <img src="docs/assets/hbfsim-comparison.png" alt="三种评估路线对比：固定 trace 回放只为旧访问序列算出新时序；全 GPU 周期级模拟每次实验都要重新模拟整颗 GPU；HBFSim 在真实 GPU 上运行负载，只对实际发生的访问在线建模 HBF" width="900">
</div>

HBFSim 的选择是：**保留真实计算和正在发生的访问，只建模 HBF 对程序可见的影响。** 这不是用一个更简单的公式替代整个系统，而是把必须模拟的部分限制在 HBF，避免重新实现 GPU 执行。

<a id="mechanisms"></a>
## 核心机制

### 1. 注册与插桩：让 GPU 访问进入 HBF 模型

应用或适配器通过显式地址注册，指定哪些地址属于 HBF 地址段。未注册的地址保持原生访存语义，不注入 HBF 延迟；注册必须在相关 kernel 首次启动前完成。

HBFSim 使用派生自 **bpftime / eGPU** 的拦截路径，自动改写受支持的 PTX global load/store，无需逐个手工修改 CUDA kernel。PTX 是 NVIDIA 编译器生成的中间代码，之后仍由编译器后端生成 GPU 机器指令。对于缺少 PTX 的预编译 kernel，可选的 **SASS→PTX 恢复路径** 将选定 kernel 交回同一套插桩流程。

每次 kernel 启动前，**coverage gate** 检查该次执行是否处于可建模范围。它不仅记录“地址已经注册”，还区分访问是否真正经过了插桩路径。

| 访问与代码情况 | 处理方式 |
|---|---|
| 未注册的普通显存地址 | 保持原生访存路径 |
| 已注册地址，访问可被安全改写 | 进入相应的 HBF 建模路径 |
| Capacity 地址可能进入无法证明安全的代码 | 拒绝 kernel 启动 |
| Timing 地址进入无法证明安全的代码 | 默认允许执行，但记为 `opaque_unmodeled_timing`，不计入建模覆盖；`strict` 策略下拒绝启动 |

受支持的基础访问包括标量与向量 global load/store；atomic、`cp.async`、texture 等未覆盖访问会被报告。详见 [PTX 插桩](docs/skills/02-ptx-instrumentation.md)、[CUDA 内存语义](docs/skills/03-cuda-memory-semantics.md)和 [SASS 恢复](tools/sass_runtime/README.md)。

### 2. GPU 执行与等待：不能把可隐藏的延迟全部变成停顿

真实 GPU 发出 load 后，可以继续执行不依赖该结果的指令，直到消费者需要数据时才等待。若在 load 发射点同步等完整段模拟延迟，就会阻止这些独立指令提前执行，改变原本的计算与访存重叠。

HBFSim 的**依赖点等待实验路径**将发射与消费分开：发射时记录 device future，遇到第一条使用结果的指令时，再等待尚未完成的模拟服务。

**默认构建仍使用发射点等待；依赖点等待是需要显式启用的实验路径。** 依赖点等待需要 `HBFSIM_ENABLE_TIMING_FUTURES=ON`、CUDA 13 与 `sm_120`，目前覆盖 Timing 区间中的 load 和 `fast` 标量模型。RTX 5090 上已通过 6 个等待语义用例；配套 profile 的准入条件和现有配置限制见[实现与验证说明](docs/52-main-gpu-thermal-integration/README.md)及[缺陷复核](docs/51-defect-review-and-fixes.md)。

### 3. 显存与容量服务：分开研究“更慢的内存”和“更大的内存”

| 模式 | 数据实际放在哪里 | 适合研究什么 |
|---|---|---|
| **Timing-only** | 数据留在真实 GPU 显存中，对已注册访问注入模拟延迟 | 延迟、带宽与争用敏感性 |
| **Capacity** | 文件承载逻辑数据，GPU 显存中的有界 page cache 保存常驻页面 | 超出物理显存的逻辑容量、分页与 cache 行为 |

两种模式共用地址注册、覆盖规则与 profile。Capacity 模式下，同一上下文中的文件映射共享有界 page cache：命中访问常驻 frame；未命中由主机容量服务从承载文件读取准确的页面字节，并计入模拟介质读；脏页淘汰涉及承载文件写回与模拟介质编程。

> **Capacity 淘汰路径仍有已知正确性问题：** frame 可能在 GPU 消费完成前被复用，脏页原地重写也不符合 HBF 的顺序编程约束。涉及淘汰或写入的实验应先核对[缺陷说明](docs/51-defect-review-and-fixes.md)与[修复计划](TODO.md#correctness-and-fidelity)。已公开的 110 GiB 稀疏访问验证未经过淘汰路径。

运行时预取与当前默认路径分开：仓库已有离线预取敏感性模型，通过 `HBFSIM_ENABLE_EVAL_TOOLS` 构建；论文描述的运行时预取尚未进入 `main`。容量转换细节见 [Capacity 设计](docs/skills/07-capacity-address-translation.md)。

### 4. 时序后端：详细参考路径与 GPU 本地快速路径

HBFSim 将 **MQSim 用作 flash 介质模型，而不是 SSD 主机软件栈模型**，并提供三种时序模式。

| 时序模式 | 工作方式 |
|---|---|
| `reference` | 请求交给在线 MQSim，使用详细介质服务模型 |
| `fast` | 根据所选 profile，在 GPU 本地计算时序，避免逐请求的主机往返 |
| `hybrid` | 结合快速路径与参考路径，在可配置预热后将部分请求送入 MQSim |

**Capacity 模式始终走参考路径。** `fast` 的参数由 profile 决定：合成 profile 用于假设扫描，`cd8p-vmem-p50` 则来自实测软件路径的标定曲线，二者不能混同。[后端实现与实验](docs/proofs/2026-08-11-hybrid-complete.md)

可选的 **UCIe 多 HBF device 后端**（代码与配置中称 multistack）模拟一个 GPU 连接多个 HBF device 的情形，进一步建模 AXI 读传输、跨 HBF device 的 page 条带化、请求合并与独立服务上限，每个 HBF device 对应一个 MQSim worker。该路径通过 `HBFSIM_ENABLE_UCIE=ON` 启用，当前为只读接口，已完成 CPU 侧验证。[UCIe 文档](docs/ucie/README.md) · [v0.1.1 发布说明](docs/releases/v0.1.1.md)

### 5. 热学与可靠性：让温度反馈到介质服务

可选的 **EQ3 模块**将结温、服务策略、数据保持期限与刷新连接起来。温度不只是报告末尾的一列：它会影响可持续服务速率，也会缩短数据保持期限；为保持数据而执行的刷新，又会增加读写流量与维护开销。

EQ3 模拟一个 GPU 连接多个存储器件时封装内的发热：[`configs/eq3_thermal/topologies/`](configs/eq3_thermal/topologies/) 下的四个拓扑配置都在一个 GPU 周围排布 8 个存储器件（HBM 或 HBF device），对应[设计目标第 4 条](#question)列出的四种架构，其中 HBF device 为 8-Hi（一个 HBF device 内堆叠 8 层 NAND die）或 16-Hi。这四个拓扑的参数都是假设值，热阻与热容是未经标定的测试取值。

EQ3 记录服务与维护相关统计，并在成功擦除时累加磨损计数；当前尚无用于输出 MTBF 的寿命模型。模块设计、启用方式与验证记录见 [EQ3 热学文档](docs/eq3_thermal/README.md)和[系统热学实验](experiments/eq3_system_thermal/README.md)。

### 6. 覆盖与报告：分清器件行为和模拟器开销

解释结果时，应区分**建模器件时间、主机服务时间、墙上时间和 HBFSim 自身开销**。建模器件时间描述所设 HBF 模型的服务；墙上时间还可能包含插桩、请求往返和主机处理，不能直接当成未来 HBF 硬件上的推理延迟。

介质基准与 CUDA 微基准已分别输出建模时间和墙上时间，覆盖日志记录哪些启动实际经过了建模。当前尚无统一拆分四类时间的报告，因此性能比较应连同所用路径、profile 和原始记录一起解读。[实验记录](docs/proofs/) · [评估方法](docs/eval/)

<a id="configuration"></a>
## 默认配置

默认构建提供地址注册、受支持 PTX 访存改写与覆盖检查、Timing / Capacity 两种模式，以及 MQSim 参考路径和 GPU 本地快速路径。以下选项用于控制额外组件；功能是否经过 GPU 验证，应结合对应模块文档判断。

| 选项 | 默认值 | 用途 |
|---|---|---|
| `HBFSIM_ENABLE_CUDA` | `ON` | CUDA 插桩与运行时组件 |
| `HBFSIM_ENABLE_MQSIM` | `ON` | MQSim 主机服务 |
| `HBFSIM_ENABLE_UCIE` | `OFF` | UCIe 多 HBF device 只读后端，当前已验证 CPU 路径 |
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | llama.cpp 与 vLLM 集成测试 |
| `HBFSIM_ENABLE_EVAL_TOOLS` | `OFF` | 离线评估、预取敏感性模型与回放工具 |
| `HBFSIM_ENABLE_TIMING_FUTURES` | `OFF` | 依赖点等待实验路径，要求 CUDA 13 与 `sm_120` |

选项的传递方式、SASS 恢复与 EQ3 的启用、profile、4 KiB 研究基准与当前实现范围，见[配置细节](docs/reference/configuration-details.md)。

<a id="integration"></a>
## 接入应用

需要 Native Linux、CMake ≥ 3.25、Ninja、支持 C++20 的编译器、OpenSSL、Python 3 和 Git。GPU 组件需要 CUDA ≥ 12.8；特定实验路径的额外要求见[默认配置](#configuration)。完整环境配置见 [RTX 5090 构建文档](docs/build/rtx-5090-environment.md)。

### 构建 GPU 组件并接入应用

以下命令使用独立构建目录；将 CUDA 路径换为本机实际安装位置。

```bash
HBFSIM_BUILD_DIR="$PWD/build-gpu" \
HBFSIM_ENABLE_CUDA=ON \
HBFSIM_CUDA_ROOT=/usr/local/cuda-12.8 \
  ./scripts/bootstrap.sh

cmake --build build-gpu -j"$(nproc)"
```

应用通过 [C API](include/hbfsim/api.h)注册需要建模的地址，例如将一段已分配显存注册为 Timing 区间：

```cpp
const hbfsim_range_options range{
    .mode = HBFSIM_RANGE_MODE_TIMING,
    .permissions = HBFSIM_RANGE_READ_WRITE,
    .cache_policy = HBFSIM_CACHE_POLICY_NONE,
    .stream_id = 0,
};
if (hbfsim_register_device(context, data, options.bytes,
                           &range) != HBFSIM_OK) {
    fail("hbfsim_register_device failed");
}
```

该片段取自 [CUDA 微基准](benchmarks/cuda/hbf_microbench.cu)。**注册地址和改写 kernel 是两步：只调用 C API 不会自动完成插桩。** 应通过 `scripts/run_with_bpftime.sh -- command [args ...]` 启动已完成注册的应用，并让 `HBFSIM_BUILD_DIR` 指向所用构建目录。

已有推理框架可以从对应适配器开始：[vLLM Timing](adapters/vllm/README.md)、[llama.cpp](adapters/llama_cpp/)；[vLLM Capacity](adapters/vllm_capacity/) 是离线容量 trace 回放工具，不加载 vLLM。模型与 SASS 研究路径的环境要求和命令保留在各自文档中，避免与基础构建混用。遇到问题参见[排错说明](docs/troubleshooting.md)。

<a id="examples"></a>
## 示例与验证

HBFSim 的公开验证覆盖真实推理框架接入、权重注册与消费者绑定、超显存逻辑容量、时序标定、快速路径和热行为。**下面列出代表性实验，而不是项目的全部测试或全部使用场景。** 更多运行记录见 [proofs](docs/proofs/)、[OLMoE 验证](docs/hbfsim-supported-weights/README.md)与[复现包](reproduction/)。

| 场景 | 代表性结果 | 记录 |
|---|---|---|
| **真实 vLLM 推理** | vLLM 0.15.1 服务 Qwen3-30B-A3B，注册区域内注入建模延迟，输出 token ID 与基线一致 | [vLLM 实验](docs/proofs/2026-08-11-vllm-exact-live-delay.md) |
| **OLMoE 权重接入** | 原生 PTX 路径注册 98 个受支持 storage；结合 SASS 恢复后注册全部 147 个去重权重 storage，并完成消费者绑定与固定请求验证 | [原生 PTX](docs/hbfsim-supported-weights/RESULTS.md) · [SASS 复现包](reproduction/sass-final147-20260926/README.md) |
| **超显存逻辑容量** | 110 GiB 逻辑地址范围、2 GiB GPU page cache，128 次稀疏访问全部完成，checksum 与基线一致 | [容量实验](docs/proofs/2026-08-11-hybrid-complete.md) |
| **时序标定** | 1、4、16、64、256、512 个连续 4 KiB page 的六个标定点精确匹配；属于拟合点一致性检查 | [标定实验](docs/proofs/2026-08-11-cd8p-vmem-tuning.md) |
| **模拟器快速路径** | 同一确定性 Qwen3-30B 用例，参考路径 44.469 s，快速路径 2.014352 s，墙上时间约缩短至 1/22，输出一致 | [路径对比](docs/proofs/2026-08-11-hybrid-complete.md) |
| **温度敏感性** | 同一 BF16 矩阵乘在冷机与热机状态下为 379.117 / 348.427 TFLOP/s，实测 GPU 计算吞吐下降 8.10% | [热行为实验](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md) |

结果应按对应实验范围使用：**快速路径加速比是两次模拟运行的墙上时间之比；110 GiB 是稀疏逻辑容量验证；温度实验测量的是 GPU 自身降频。** 三者均不是 HBF 器件的实测性能。

<details>
<summary>展开实验配置、覆盖范围与复现说明</summary>

**实验硬件。** 除 OLMoE 权重实验在 RTX 5090 上运行外，上表其余 GPU 结果来自 NVIDIA RTX PRO 6000 Blackwell Server Edition，driver 595.84。该卡的 GDDR7 在实验中承担所建模系统的 HBM 层角色，并非真实 HBM。[环境记录](docs/eval/capacity_qwen3_30b_a3b.md)

**vLLM。** 所列 Qwen3-30B 实验注册 `model.layers.0.mlp.experts.w13_weight` 的前 16,384 字节。2,304 次 `fused_moe_kernel` 启动中有 24 次建模启动，绑定了 4 个 Triton kernel 变体。这一实验验证指定区域的在线注入与输出一致性，不等同于全模型 HBF 时序覆盖。[记录](docs/proofs/2026-08-11-vllm-exact-live-delay.md)

**OLMoE。** 原生 PTX 路径注册 98 个 storage，共 13,091,213,312 字节；其余 49 个 storage 保持原生路径。SASS 路径注册全部 147 个去重 storage，共 13,838,323,712 字节，两次固定短请求均返回 `[[70, 13]]`。完整注册不表示短请求访问了每个字节或每个 expert，也不代表任意模型和 kernel 的通用覆盖。98-storage 完整实验尚未用重建组件全量复跑，重建组件已在 8-storage 代表用例中验证；final147 复现包尚未完成干净重建与跨机器 GPU 复现。[源码重建记录](docs/hbfsim-supported-weights/REBUILT_SOURCE_VALIDATION.md) · [final147 报告](reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/final147-20260926-v1/REPORT.md)

**容量。** 110 GiB 逻辑范围运行在报告显存为 97,887 MiB 的 GPU 上；128 次访问的 checksum 为 `14245581564465502923`，不安全启动为 0。该实验没有物理读取整个 110 GiB，也没有经过淘汰路径。[记录](docs/proofs/2026-08-11-hybrid-complete.md)

**标定。** `cd8p-vmem-p50` 来自 Dell CD8P NVMe SSD 的 vmem 完整软件路径，包括 page fault 和驱动等开销；它不是 HBF 实测，也不是裸 NAND 延迟。六个断点全部参与拟合，留出点验证与同输入下的 fast/reference 一致性对照仍在[验证计划](TODO.md#validation)中。

**热行为。** 上表 -8.10% 来自冷热独占矩阵乘对照；另一组矩阵乘与 fio 并发实验得到 -3.72%，热学配置采用后者。两个数对应不同运行条件，不应相互替代。[相关记录](docs/proofs/2026-08-11-hybrid-complete.md)

**端到端性能。** 当前公开 `main` 的评估记录尚未核实完整 prefill/decode 时延与 TPOT，不能将上表运行耗时直接解读为 HBF 上的端到端推理预测。[评估范围](docs/eval/capacity_qwen3_30b_a3b.md)

</details>

<a id="roadmap"></a>
## 路线图

这里只保留影响使用和研究能力的功能里程碑；详细验收条件见 [TODO.md](TODO.md)。版本发布、文档整理和仓库维护记录见[发布说明](docs/releases/v0.1.1.md)。

### 已完成

- [x] **GPU 访存接入与容量层：** 显式地址注册、PTX 自动改写、启动覆盖检查，以及 Timing / Capacity 两种模式。
- [x] **在线介质建模：** MQSim 参考路径、GPU 本地快速路径和混合模式，以及可选 EQ3 热学与维护模块。
- [x] **推理框架与权重接入：** vLLM、llama.cpp 适配；OLMoE 原生 PTX 的 98-storage 验证与可选 SASS 路径的 147-storage 固定请求验证。
- [x] **多 HBF device 扩展：** UCIe 只读接口、独立 MQSim worker、条带化与请求合并，完成 CPU 侧验证。
- [x] **等待与兼容性改进：** 依赖点等待实验实现及 RTX 5090 语义检查，修复 PTX target 兼容与多组时序、插桩缺陷。

### 正在推进

- [ ] **修复写入与淘汰语义：** 按 4 KiB 编程单元建模写入，遵守顺序编程约束，修复 frame 生命周期竞争。
- [ ] **完善介质数据复用：** 引入 page buffer 与跨 warp 页面驻留过滤，避免将重复访问重复计为介质读取。
- [ ] **扩展多 HBF device 能力：** 增加写路径、GPU 验证及不同 HBF device 数量下的容量与带宽扫描。

<a id="documentation"></a>
## 文档与代码导航

| 目标 | 入口 |
|---|---|
| 构建、运行和排错 | [构建环境](docs/build/rtx-5090-environment.md) · [排错](docs/troubleshooting.md) |
| 理解内部实现 | [文档索引](docs/README.md) · [子系统阅读指南](docs/skills/) |
| 运行与复现实验 | [实验记录](docs/proofs/) · [评估 runbook](docs/eval/) · [复现包](reproduction/) |
| 使用扩展模块 | [UCIe / 多 HBF device](docs/ucie/README.md) · [EQ3](docs/eq3_thermal/README.md) · [SASS 恢复](tools/sass_runtime/README.md) |
| 查看计划和版本 | [TODO](TODO.md) · [v0.1.1](docs/releases/v0.1.1.md) |

历史文档、日志与输入示例使用路径归一化的公开副本，填写路径和校验副本的方法见 [PUBLIC_COPY_NOTICE.md](PUBLIC_COPY_NOTICE.md)。

<details>
<summary>仓库结构</summary>

```text
HBFSim/
├── src/             # CUDA 运行时、PTX pass、容量服务、时序与热学模型
├── include/hbfsim/  # 公共 C API 与主机 / 设备接口
├── adapters/        # vLLM 与 llama.cpp 适配器、离线容量 trace 回放
├── benchmarks/      # CUDA、MQSim、UCIe 与离线评估基准
├── configs/         # Profile、热学配置、扫描参数与 JSON Schema
├── scripts/         # 构建、运行入口、评估与标定工具
├── tests/           # CPU / GPU 测试与 PTX fixture
├── tools/           # SASS 恢复、原生 PTX 复现与热学工具
├── experiments/     # EQ3 系统热学、速率与维护实验
├── reproduction/    # 固定实验复现包与验收记录
├── third_party/     # 固定版本的 bpftime 与 MQSim
├── patches/         # 外部依赖补丁
├── cmake/           # 依赖构建、版本固定与 PTX 嵌入
└── docs/            # 设计、实验、规范、构建与排错文档
```

`paper/` 与构建、测试无关；bootstrap 仅初始化 bpftime 与 MQSim 两个构建依赖。

</details>

<a id="citation"></a>
## 论文与引用

[HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution](https://arxiv.org/abs/2609.09800)

```bibtex
@article{hu2026hbfsim,
  title   = {HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution},
  author  = {Hu, Yanpeng and Yang, Yiwei and Zhu, Yuanwu and Zheng, Yusheng and Quinn, Andi and Zhang, Wei},
  journal = {arXiv preprint arXiv:2609.09800},
  year    = {2026}
}
```

仓库同时提供 [CITATION.cff](CITATION.cff)，可通过 GitHub 的 “Cite this repository” 获取引用。

<a id="contributing"></a>
## 参与贡献

贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交性能或正确性结果时，请同时提供可复现命令、环境、profile、覆盖记录和原始输出，并明确数字对应建模时间还是墙上时间。

使用问题可在 [Q&A](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a)讨论；缺陷报告使用 [Issue 模板](https://github.com/SlugLab/HBFSim/issues/new?template=bug_report.yml)，代码贡献使用 [PR 模板](.github/pull_request_template.md)。参与者请遵守[行为准则](CODE_OF_CONDUCT.md)。

<a id="acknowledgements"></a>
## 许可证与致谢

HBFSim 采用 [Apache License 2.0](LICENSE)。

感谢 **bpftime / eGPU** 提供 GPU 拦截与 PTX 插桩的基础，感谢 **ETH Zurich SAFARI Research Group 的 MQSim** 提供 flash 介质模型。bpftime 与 MQSim 作为固定版本依赖保留，分别遵循其上游许可证。

感谢 [hetGPU](https://github.com/vickiegpt/Concordia) 的贡献者提供 SASS→PTX 恢复管线，使选定的预编译 NVIDIA kernel 能够进入 HBFSim 的访存插桩流程。hetGPU 派生自 ZLUDA，采用 Apache-2.0 或 MIT 许可证。
