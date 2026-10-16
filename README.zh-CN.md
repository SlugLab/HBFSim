# HBFSim：让负载跑在真实 GPU 上，同时模拟 High-Bandwidth Flash

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml/badge.svg)](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml)
[![arXiv:2609.09800](https://img.shields.io/badge/arXiv-2609.09800-b31b1b.svg)](https://arxiv.org/abs/2609.09800)

[English](README.md) | **中文**

HBFSim 是一个开源的 High-Bandwidth Flash（**HBF**）模拟器，用来在 HBF 器件还买不到的时候评估 HBF。HBF 把 NAND flash 堆叠起来，和 GPU 这类计算芯片放进同一个封装，作为 High-Bandwidth Memory（**HBM**）下面的一个内存层。

HBFSim 让应用照常在真实 GPU 上执行，并在执行过程中对注册为 HBF 的那部分数据施加 HBF 的时序与容量效应，不需要先把访问序列录下来、事后回放。HBFSim 的设计还把温度纳入模型；温度建模在 `main` 分支上目前是默认关闭的 CPU 实验（仓库里名为 EQ3 的热学与可靠性实验代码），还没有接进真实 GPU 运行时的时序路径。

HBFSim 已经在真实 GPU 上跑通两个例子。vLLM 服务 Qwen3-30B 时注入建模延迟，生成的 token 标识符与基线一致；110 GiB 的逻辑地址范围在一块 97,887 MiB 的 GPU 上完成了全部 128 次访问。细节与限定见 [HBFSim 已经量到什么](#hbfsim-已经量到什么)。

HBFSim 适合系统设计者与研究者，他们要决定买多少 HBF 容量、把哪些张量放进 HBF。想先跑起来，从[快速开始](#快速开始)读；想知道为什么需要这样一个模拟器，从 [HBF 为什么重要](#hbf-为什么重要)读起。

[HBF 为什么重要](#hbf-为什么重要) · [现有办法为什么定不下来](#现有办法为什么定不下来) · [HBFSim 怎么做](#hbfsim-怎么做) · [功能一览](#功能一览) · [快速开始](#快速开始) · [HBFSim 已经量到什么](#hbfsim-已经量到什么) · [路线图与 TODO](#路线图与-todo) · [arXiv 上的论文](https://arxiv.org/abs/2609.09800) · [TODO.md 里还没做完的工作](TODO.md)

历史文档、日志和输入示例已发布为路径归一化副本。路径填写和公开副本校验方法见[说明](PUBLIC_COPY_NOTICE.md)。

> HBFSim 的一部分功能仍是实验性的，[功能一览](#功能一览)给每项功能标了状态：默认开启、需要显式打开，或者只是 CPU 实验。遇到可以复现的问题，请用[缺陷报告表单](https://github.com/SlugLab/HBFSim/issues/new?template=bug_report.yml)；使用上的疑问，请在 [Q&A 讨论区](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a)提问。

## HBF 为什么重要

LLM 推理需要的显存容量越来越大，而 HBM 的容量与产能都受限。HBF 把 NAND flash 放进计算芯片的封装来扩充 HBM，代价是写入有寿命限制，读延迟也比 HBM 长得多；LLM 推理里读多写少、读的顺序能提前知道的数据，受这两个短板的影响较小。HBF 在推理里到底能带来多少好处，业界看法不一，而各方的结论都来自各自的模型，还没有一方公开过真器件上的测量。

LLM 推理同时受显存带宽与显存容量限制。decode 阶段每生成一个 token，都要把模型参数和 KV cache 从显存读进计算单元，这一阶段的计算全部受访存限制（[Yuan 等，arXiv:2402.16363](https://arxiv.org/abs/2402.16363)；[Pope 等，arXiv:2211.05102](https://arxiv.org/abs/2211.05102)）。模型规模与上下文长度又一代比一代大，需要的显存容量随之上升（[HBF 规范](docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf)第 3 章）。

HBM 的容量和产能都受限。JEDEC 在 2025 年 4 月发布的 HBM4 标准里，单个 stack 容量最高 64 GB，带宽最高 2 TB/s（[JEDEC 新闻稿](https://www.jedec.org/news/pressreleases/jedec%C2%AE-and-industry-leaders-collaborate-release-jesd270-4-hbm4-standard-advancing)）。Micron 截至 2026 年 5 月 28 日的季度报告写到，生产同样比特数的 HBM 比普通 DRAM 需要更多晶圆和洁净室面积，AI 带来的内存与存储需求增长快于行业供给（[Micron Form 10-Q](https://www.sec.gov/Archives/edgar/data/723125/000072312526000015/mu-20260528.htm)）。HBF 的推动方 Sandisk 认为 HBM 受限于容量小、功耗高、成本高（[Sandisk HBF 事实页](https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf)，厂商声明）。

HBF 用 NAND flash 扩充 HBM。一个 HBF stack 由底部的一片 base die 和上面最多 16 片 NAND die 组成，通过 Universal Chiplet Interconnect Express（**UCIe**）与计算芯片连在同一个封装里，走线小于 2 mm（规范第 4 章与第 5.1.1 节）。SK hynix 把 HBF 描述为 HBM 与 SSD 之间的新一层（[SK hynix 新闻稿](https://news.skhynix.com/en/hbf-at-fms-2026/)）。HBF 与 HBM 一起用时，两者容量差得太多，规范要求分开管理（第 11.1.2 节）。

第一份 HBF 技术规范 v0.7.0 由 Sandisk 与 SK hynix 通过 Open Compute Project（**OCP**）在 2026 年 8 月 3 日公开发布（[OCP 上的规范 PDF](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf)，仓库里存有[副本](docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf)；[Sandisk 新闻稿](https://www.sandisk.com/company/newsroom/press-releases/2026/2026-08-03-Sandisk-and-sk-hynix-advance-global-standardization-of-hbf)）。规范写明对业界开放、可供采用；致谢一节列出 Google 与 Tenstorrent 提供了反馈。

厂商公布的容量、带宽与价格目标如下，前两项出自规范原文，后两项是 Sandisk 的声明。

- 容量是 HBM 的 8 到 16 倍，读带宽与 HBM 相同（规范第 3 章）。
- 三个速度档，单个 stack 的用户带宽最高分别为 0.384、1.536、3.072 TB/s；单个 stack 的容量举例为 512 GiB（规范第 4.1 节与表 4）。
- 价格与 HBM 相当（Sandisk 事实页，2025 年 7 月）。
- 第一代产品读带宽 1.6 TB/s，16 片 die 组成的 stack 共 512 GB（Sandisk 事实页）。

NAND 有两个短板，HBF 同样受这两点限制。

- **写入贵，而且有寿命。** 规范写明 HBF 与 HBM 不同，有写入寿命限制。写入以 4 KiB 为单位、在一个 block 内顺序写，器件在写一个 block 的第 0 个 page 时自动先擦除整个 block。通电时在 85 °C 下保证数据保持 24 小时，一般每 24 到 48 小时要刷新一次（规范第 5.4.1 节、第 9 章与第 11.5 节）。
- **读延迟是微秒级。** 规范没有给读延迟。公开研究给 HBF 读延迟取的值在 1 到 20 μs 之间，都是假设值：[TileLens](https://arxiv.org/abs/2607.04031) 写 1 到 10 μs，主实验取 5 μs，扫参扫到 20 μs；SK hynix 研究者的 [H³ 论文](https://doi.org/10.1109/LCA.2026.3660969)假设 20 μs（据该文的中文译文）。GPU 访问一次 HBM 是几百纳秒（例如 GH200 上 Hopper 读本机 HBM 约 344.2 ns，[Fusco 等，arXiv:2408.11556](https://arxiv.org/abs/2408.11556)），按 TileLens 主实验的 5 μs 与 H³ 的 20 μs 计，HBF 读延迟是 344.2 ns 的约 15 到 58 倍。

LLM 推理里有大量读多写少、读的顺序又能提前知道的数据。读多写少，写入的代价就小；读的顺序提前知道，就可以提前读来掩盖读延迟。

- **权重。** 规范第 13.3.1 节写明，每生成一个 token 都要加载一批参数，大小从几 GiB 到几十、几百 GiB；第 13.3.3 节指出，权重与 KV cache 的读写方式不同，两者混在一个区域里，可能对寿命和容量利用率不利，因此可以按通道划分容量，分别存放权重与 KV cache。
- **共享前缀的 KV cache。** 推理常把共享同一前缀（few-shot 示例、system prompt）的请求放在一批里处理（[Hydragen，arXiv:2402.05099](https://arxiv.org/abs/2402.05099)），同一段前缀的 KV cache 因此被一批请求共用。
- **MoE 的 expert 权重。** GPU IP 公司 Oxmiq Labs 在 Hot Chips 2026 的教程里以 Kimi-K3（2.8T 参数）为例：约 1.56 TB 权重里约 93% 是 MoE expert 权重，这部分基本只写一次（据 [ServeTheHome](https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/) 与 [Tom's Hardware](https://www.tomshardware.com/pc-components/ssds/hot-chips-2026-high-bandwidth-flash-promises-massive-bandwidth-and-capacity-but-its-usability-is-extremely-limited-new-memory-format-strikes-a-balance-between-hbm-and-nand-flash) 转述）。
- **逐层执行的顺序。** Transformer 按固定顺序逐层执行，算第 i 层时就知道第 i+1 层要读哪些权重。以 [Llama 3 8B](https://arxiv.org/abs/2407.21783)（BF16）在 [H100 SXM5](https://resources.nvidia.com/en-us-gpu-resources/h100-datasheet-24306)（显存带宽 3.35 TB/s）上 decode 为例，读完一层权重（436,207,616 字节）约需 130 μs，这段时间可以用来预取下一层。130 μs 这个数是我们按公开参数做的估算，假设小 batch decode 完全受读权重的带宽限制；MoE 的路由 expert 要等本层 gate 算完才知道读哪几个，不适用这个估算。

HBF 器件现在还买不到。Sandisk 计划 2027 年送出首批 HBF 推理产品样品（据 [StorageReview](https://www.storagereview.com/news/sandisk-tapes-out-its-first-hbf-memory-die-targets-2027-for-inference-product-samples) 对 Sandisk 2026 年 8 月投资者日的报道）。SK hynix 在 2026 年 Future of Memory and Storage（**FMS**）大会上展示的时间线把完整规范放在 2027 年初、HBF 样品放在 2028 年初（据 Forbes 撰稿人 Thomas Coughlin 的文章，[Yahoo 转载](https://ca.news.yahoo.com/high-bandwidth-flash-advances-2026-040711224.html)）。两家说的「样品」是不是同一种东西，从公开报道里看不出来。

业界对 HBF 的看法分成两派，中间还有一种折中的看法。

- **支持方。** Sandisk 与 SK hynix 是规范的发起方。SK hynix 研究者的 H³ 论文把只读数据（包括共享的预计算 KV cache）放进 HBF、其余放进 HBM，仿真得到的单位功耗吞吐量最高是纯 HBM 系统的 2.69 倍（据该文的中文译文）。
- **怀疑方。** Micron 的三位研究者在 HotInfra '26 研讨会论文里用解析模型比较后认为，同样放进封装，on-package LPDDR 在他们评估的每个工作点上都不输 HBF，原因是 NAND 的写入速率与寿命限制让 HBF 承接不了 KV cache 溢出的写入（[论文 PDF](https://hotinfra.org/2026/papers/hotinfra26-final83.pdf)；作者声明论文观点不一定代表 Micron 官方立场）。
- **折中。** Oxmiq Labs 在 Hot Chips 2026 的教程里认为，HBF 是一个容量档位，只在带宽需求低的场景占优，例如小 batch 的 MoE 和长上下文的稀疏 KV（据 [ServeTheHome](https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/) 转述）。

各方的结论都来自各自的模型，还没有一方公开过真器件上的测量。Sandisk 事实页里的性能数字来自内部测试与仿真，H³ 用的是内部模拟器，Micron 三位研究者用的是 roofline 解析模型，Oxmiq 用的是机架级仿真。在真器件出来之前，要判断哪一方对，需要一个不偏向任何一方的开源模拟器，而且这个模拟器要在真实负载上运行；HBFSim 按这个目标设计。

## 现有办法为什么定不下来

设想一位系统设计者要回答两个问题：把 Qwen3-30B 的 expert 权重放进 HBF，HBM 里只留其中一部分，decode 会慢多少？HBM 和 HBF 各买多少容量才合适？服务一个 LLM 受限于显存容量，而 HBF 容量买多少、哪些张量放进 HBF，都不能等到 HBF 器件出来再决定。

下面每一种现有办法都有 HBFSim 不去做的长处。

- 回放已记录访问序列的存储模拟器便宜、可重复，而且不需要加速器。
- GPU 模拟器给出的微架构细节是 HBFSim 从来看不到的。
- 在小 kernel 上，cycle-accurate 模拟器是正确性的参照。
- 厂商公布的参数是现在唯一能拿到的描述，因为厂商之外还没有人量过 HBF 器件。

要回答 decode 会慢多少、两种容量各买多少，现有办法都定不下来，原因有六条。

1. **cycle-accurate GPU 模拟器在可接受的时间里跑不完一次端到端推理。** Huerta 等人测得，单线程的 Accel-Sim（一个 cycle-accurate GPU 模拟器）跑异构计算基准程序集 Rodinia 里的 lavaMD 要五天以上，比真实硬件慢 10,748,031 倍，开到 16 个线程仍慢 766,423 倍（2024 年第 2 届 Workshop on Computer Architecture Modeling and Simulation，arXiv:2502.14691）；lavaMD 还不是 LLM 负载。

   Accel-Sim 2.0 的作者自己写明，trace 一次 LLM 推理里的每一个 kernel 代价过高（原文「Tracing every kernel in an LLM is prohibitively expensive」），所以只 trace 一个 transformer block，再乘以层数外推整次推理。Accel-Sim 2.0 的作者在 H100 上验证过：单个 block 的延迟乘以 32，与 Llama-3.1-8B 整次推理的实测延迟相差 prefill 0.97%、decode 3.06% 以内（预印本 arXiv:2608.22602）。单层外推仍然要先录一条 trace，同样受第 2 条原因的限制：一条 trace 只对应一种 HBM 与 HBF 的容量划分。
2. **回放 trace 的办法不执行负载，而且一条 trace 只对应一种容量划分。** 存储模拟器回放已记录的访问序列，从头到尾不执行负载。GPU 模拟器的 trace 模式回放已录好的 GPU 指令 trace，不在真实 GPU 上执行 kernel，速度也只有每秒 1.25 万条 warp 指令（Accel-Sim，ISCA 2020）。

   换一种 HBM 与 HBF 的容量划分，推理框架常驻哪些张量、KV cache 怎么分、迁移 kernel 会不会触发都会变。新划分增加或去掉的那些 GPU 指令，都不在已经录好的 trace 里。容量划分会让 trace 失效这个判断来自 HBFSim 论文的分析，还没有做过两种划分各录一条 trace 的对照实验。
3. **规范没有给 NAND 的时间参数。** 规范里查不到 NAND page 读、page 写、擦除的时间，模拟器里没有现成的数可填。
4. **现有内存层与块设备模拟器在主机侧或 NVMe 队列上拦截。** 拦截点是被测程序之外的一个位置，每次访存都必须经过这个位置，实验者可以在这里加等待时间。Cylon（FAST '26）在主机 CPU 的虚拟化页表上拦截，CXLMemSim 在主机内核里用 eBPF 与 CPU 性能计数器拦截，FEMU（FAST '18）在 NVMe 提交队列上拦截。

   SwarmIO（预印本）在 NVMe 队列与 doorbell 这一层拦截，SwarmIO 的提交队列与完成队列放在 GPU 显存里。HBF 的访问由 GPU 的 load/store 单元发出、在封装内完成，不经过主机侧的任何一个拦截点，也不经过任何 NVMe 队列，剩下的办法只有改写被测程序本身的代码。
5. **把参数填进解析模型，和实测曲线差很多。** 我们在一条真实存储路径上量过解析模型与实测曲线的差距：只用固定读延迟加带宽的标量模型，算出的延迟在 1 个 page 时比实测低 10.11%，在 512 个 page 时比实测低 99.93%（14,096 ns 对 20,254,374 ns，出处是 proof [`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`](docs/proofs/2026-08-11-cd8p-vmem-tuning.md)；proof 指 `docs/proofs/` 下每次实验跑完写下的 checkpoint 文档）。实测曲线测的是把 NVMe SSD 当内存用的完整软件路径，含 page fault、驱动等开销，不是 HBF，也不是裸 NAND。
6. **温度会改变同一块芯片的速度。** 同一个 BF16 8192x8192 矩阵乘在同一块 GPU 上，冷机独占时是 379.117 TFLOP/s，热机独占时是 348.427 TFLOP/s，低 8.10%；另一次测量（矩阵乘与 fio 并发 30 秒）是 -3.72%。-8.10% 与 -3.72% 都是 GPU 计算芯片自己降频，不是 HBF 的服务速率，说明的是在一个热状态下标定的时序，换一个热状态不一定成立。HBF 规范也定义了 thermal throttling：结温到达阈值后器件降低功耗，读写响应变慢，严重时暂停所有操作。

### 一条 global load 的延迟在哪里被掩盖

在真实 GPU 上注入延迟，难点在于注入到哪一条指令上。一个 warp 发出 global load 之后不会停下来等结果，它会继续发射后面不读这个结果的指令，直到第一条读结果的指令才停下。同一段访存延迟有多少变成程序真正等待的时间，取决于 load 与第一条读结果的指令之间隔着多少独立的指令。

```text
LDG R8           global load into register R8
IMAD R9,R8,...   first instruction that reads R8
W = time to issue the independent ALU instructions between LDG and IMAD
L = latency of the real access to GPU memory
D = modeled device time

(1) Real hardware
    warp : LDG R8 | ALU ALU ALU ... ALU | IMAD R9,R8,...
    load :        |<----- L ----->|
    stall at IMAD = max(0, L - W)

(2) Wait at the issue point                      <- main default build
    warp : [========== wait D ==========] LDG R8 | ALU ALU ALU ... ALU | IMAD R9,R8,...
    stall = D, and no ALU instruction issues early

(3) Wait at the first use                        <- HBFSIM_ENABLE_TIMING_FUTURES=ON
    warp : LDG R8 | ALU ALU ALU ... ALU | [wait] IMAD R9,R8,...
    HBF  :        |<------------ D ------------>|
    stall at IMAD = max(0, D - W)
```

图里的三个符号：W 是 load 与第一次读结果的指令之间那些独立 ALU 指令的发射时间，L 是真实访存延迟，D 是建模的器件时间。图中线段长度只是示意，不对应任何实测时间。

- 第 (1) 条时间线是真实硬件：真实访存延迟落在 W 之内，warp 就完全不停。
- 第 (2) 条时间线在发射点原地等完整个 D，load 与第一条读结果的指令之间的独立指令一条也没能提前发射，整段建模的器件时间都变成了等待时间。
- 第 (3) 条时间线在发射时登记一条记录（论文里叫 device future），到第一条读结果的指令处才等，停的位置与真实硬件相同。

按 [arXiv 论文](https://arxiv.org/abs/2609.09800)的分析，第 (3) 条时间线（在第一次使用处等待）上，真实访存延迟 L 与建模的器件时间 D 是否落在 W 之内，会出现三种情形。

- 两者都落在 W 之内：模拟运行与真实运行的行为一样。
- 只有真实访存延迟落在 W 之内：模拟运行在真实运行不停的地方多停了一段。
- 两者都落不进 W：两次运行都停，但停的长度不同。

第 (2) 条时间线不论哪种情形都停满 D。

W 有多长，在改写阶段看不到：物理寄存器分配和指令调度都在改写之后才由编译器后端决定。所以改写阶段没法事先算出该等多久，第 (3) 条时间线把等待放在第一次读结果的指令处，停多久由 GPU 按实际的 W 决定。

**`main` 默认构建走的是第 (2) 条时间线。** 第 (3) 条时间线要用编译开关 `HBFSIM_ENABLE_TIMING_FUTURES=ON` 显式打开（还需要 CUDA 13 与 sm_120），仍是实验路径。第 (3) 条时间线的覆盖范围与运行条件见[功能一览](#功能一览)里「在第一次使用处等待」那一行。仓库里还没有同一负载在发射点等待与在第一次使用处等待的成对测量（[TODO.md](TODO.md#correctness-and-fidelity)）。

### HBFSim 与其它几种办法的对比

| 办法 | 执行真实负载 | 跑在真实硬件上 | 建模介质 | 建模温度 |
|---|:---:|:---:|:---:|:---:|
| 回放已记录访问序列的存储模拟器 | ✗ | ✗ | ✓ | ✗ |
| 不运行真正计算 kernel 的 GPU 模拟器（回放已录好的 GPU 指令 trace） | ✗ | ✗ | ✗ | ✗ |
| cycle-accurate 模拟器 | ✓ | ✗ | ✓ | ✗ |
| 厂商参数表 | ✗ | ✗ | ✗ | ✗ |
| HBFSim | ✓ | ✓ | ✓ | ✓¹ |

- 每一行写的是办法，不是具体产品，所以某个具体工具有可能多出表里四项能力中的一项。
- cycle-accurate 模拟器确实执行负载，但在可接受的时间里跑不完一次端到端推理，只能抽一层外推，所以第一列的 ✓ 不代表 cycle-accurate 模拟器能在可接受的时间里回答「decode 会慢多少」「HBM 和 HBF 各买多少容量」这两个问题。
- ¹ HBFSim 的设计把温度、数据保持期限与刷新纳入模型。`main` 上，温度、数据保持期限与刷新的建模是默认关闭的 CPU 实验（EQ3），还没有接进真实 GPU 运行时的时序路径（[TODO.md](TODO.md#modeling-endurance-and-temperature)、[docs/eval/thermal_coverage.md](docs/eval/thermal_coverage.md)）。

## HBFSim 怎么做

HBFSim 的出发点是 [arXiv 论文](https://arxiv.org/abs/2609.09800)里的一个判断：**评估 HBF 不需要模拟 GPU，只需要建模 HBF 对程序可见的效应，再把建模的器件时间注入正在真实 GPU 上运行的程序。**

论文还要求注入不能破坏 GPU 原本用来掩盖访存延迟的并发；`main` 默认构建在发射点等待，还没有做到这一点（见[一条 global load 的延迟在哪里被掩盖](#一条-global-load-的延迟在哪里被掩盖)）。论文另外描述了 Tensor Memory Accelerator（**TMA**，NVIDIA GPU 上异步搬运整块多维数组的单元）传输的建模、运行时预取与温度闭环控制，这些实现尚未合入 `main`。本页其余部分只写 `main` 上的状态。

### 工作原理

整个设计由三个机制组成。

1. **哪些数据算 HBF，由显式注册的地址区间决定。** 只有应用或运行时注册过的地址才被当成 HBF，注册要在第一次 kernel 启动之前完成。指向普通 GPU 显存的指针留在原生路径上，不受影响。

2. **改写 PTX，让 HBFSim 看到 kernel 里的 global load 与 store。** Parallel Thread Execution（**PTX**）是 NVIDIA 编译器为 GPU kernel 生成的中间代码，编译器后端再把 PTX 翻译成 GPU 实际执行的机器码。一条由 bpftime（一个用户态 eBPF 运行时）与 eGPU（一个把 eBPF 程序编译成 PTX、注入正在运行的 GPU kernel 的框架）派生出来的拦截路径自动改写受支持的 global load 与 store 指令，不需要手工改任何一个 CUDA kernel。

   受支持的是标量与向量形式的 global load/store；atomic、`cp.async`、TMA、texture 这类访问不改写，会被计数并报告为不支持（[docs/skills/02-ptx-instrumentation.md](docs/skills/02-ptx-instrumentation.md)）。

   每次 kernel 启动前，HBFSim 会做一次覆盖检查（coverage gate）。如果 HBF 指针会传进 HBFSim 无法证明安全的代码，capacity 区间的 kernel 启动直接被拒绝；timing 区间（两种区间分别对应下文[两种模式](#两种模式)里的 Capacity 模式与 Timing-only 模式）默认放行，但这次启动被记成没有建模（`opaque_unmodeled_timing`），不计入建模覆盖。设 `HBFSIM_INSTRUMENTATION_POLICY=strict` 后，timing 区间也会被拒绝（[docs/skills/03-cuda-memory-semantics.md](docs/skills/03-cuda-memory-semantics.md)）。

   构建会检查改写后的模块是自包含的，并用 CUDA 12.8 的 `ptxas` 汇编这个模块；这仍然是静态证明，不是在真实 GPU 上注入过延迟的证据。

3. **MQSim 参考路径与快速路径并用。** MQSim（SAFARI Research Group 开发的 SSD 模拟器）在这里只当 flash 介质模型用，不当 SSD 主机软件栈模型用；在线运行、只建模介质的 MQSim 叫 MQSim 参考路径（时序模式名 `reference`），给出详细的时序。快速路径（时序模式名 `fast`）是一个 GPU 本地模型，参数来自所选的 profile（`cd8p-vmem-p50` 的参数由实测曲线标定），承担常见的访问，在 GPU 上直接算延迟与带宽，不走请求与完成的往返。时序模式 `hybrid` 在一段可配置的预热之后，把一部分请求送去 MQSim 参考路径（见 [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md)）。

   Capacity 模式不管选哪一种时序模式，一律走 MQSim 参考路径（[docs/skills/07-capacity-address-translation.md](docs/skills/07-capacity-address-translation.md)）。快速路径与 MQSim 参考路径还没有在同一输入上做过一致性对比（[TODO.md](TODO.md#validation)）。

HBFSim 区分四种时间：建模的器件时间、主机服务时间（HBFSim 的主机侧处理一个请求所花的时间）、墙上时间，以及 HBFSim 自身的开销。建模的器件时间是 HBFSim 算出来的 HBF 器件耗时，也就是[一条 global load 的延迟在哪里被掩盖](#一条-global-load-的延迟在哪里被掩盖)一节示意图里的 D；程序在真实 GPU 上运行时，HBFSim 把建模的器件时间注入程序。把四种时间分开很要紧，因为 HBFSim 可以功能上完全正确，而 HBFSim 自身的软件开销比建模的器件时间还大。

现有输出只把四种时间分开了一部分。介质基准与 CUDA 微基准分开报告建模的器件时间与墙上时间，离线 trace 回放另外报告暴露出来的停顿时间；还没有一份输出同时列出四种时间。proof [`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md) 里，建模的器件时间与每个 warp 的请求路径开销是合在一起报告的。

可选的 [UCIe 接口](docs/ucie/README.md)（v0.1.1）把主机请求环（GPU 写入访问请求、由主机侧守护进程读取的共享环形队列）接入可配置的多 stack MQSim 后端，每个 stack 一个 MQSim worker，细节见[功能一览](#功能一览)。UCIe 接口通过 `HBFSIM_ENABLE_UCIE=ON` 显式启用，已有后端仍是默认路径；目前只支持读。UCIe 接口可以经 CUDA 拦截运行时的上下文与 vLLM loader 显式选用，但只在 CPU 上测试过（测试跳过 CUDA 注册），没有 GPU 上的运行记录。

### 架构图

<div align="center">
  <img src="docs/assets/hbfsim-architecture.png" alt="HBFSim 架构：主机侧的 SASS 恢复、PTX pass 与 HBF 地址区间注册表，运行 vLLM 的真实 GPU（改写后的访问经 Device ABI 等待建模的器件时间），带原生路径与 frame cache 的 GPU 显存层（图中标为 HBM tier），带承载文件与 prefetcher 的主机容量服务，以及包含在线 MQSim、UCIe 多 stack 接口和热学与可靠性实验的 HBF 建模部分" width="900">
</div>

图里的部件按状态分三类，图例给出了每一类的画法：第一类是默认运行时的一部分；第二类是需要显式打开的选项；第三类只以 CPU 实验或离线模型形式存在，还没有接入运行时。框旁的 D1–D6 指 arXiv 论文 Design 一节的六个小节。

- **主机侧准备。** PTX pass 改写模块，覆盖检查据此决定放行还是拒绝；Registry 记下哪些地址区间注册成了 HBF。SASS 恢复（图中标为 SASS recovery）是需要显式打开的选项：SASS 是 NVIDIA GPU 实际执行的机器码，对只有 SASS、没有 PTX 的闭源或预编译 kernel，HBFSim 先把选定的 kernel 从 SASS 恢复成 PTX，再交给 PTX pass。
- **真实 GPU。** vLLM 在真实 GPU 上服务 Qwen3-30B。改写过的 load/store 会调用 HBFSim 嵌进 PTX 模块里的设备端辅助函数，图中的 Device ABI 就是这组辅助函数的调用接口；Device ABI 按 Registry 判断地址有没有注册成 HBF。改写过的 load/store 访问注册过的地址时，等待建模的器件时间；timing 区间里不能改写的访问照常执行，所在的那次启动记为没有建模（`opaque_unmodeled_timing`）。
- **GPU 显存这一层（图中标为 HBM tier）。** 访问未注册的地址留在原生路径上，访问 capacity 区间由图中的 frame cache 供给，frame cache 就是 Capacity 模式里放在 GPU 显存里的 page cache。做实验的 RTX PRO 6000 显存是 GDDR7，在实验里扮演 HBM 的角色。
- **主机容量服务。** 从承载文件里取出准确的 page 字节。Prefetcher 属于第三类：`main` 上只有一个默认不编译的离线敏感性模型，运行时预取不在 `main` 上。
- **HBF 建模：时序。** MQSim 参考路径（图中标为 Online MQSim）给出时序。UCIe 多 stack 接口（图中标为 UCIe multistack）是需要显式打开的选项，在 Advanced eXtensible Interface（**AXI**，规范在 UCIe 之上使用的协议层）上做读传输，把 page 跨 stack 条带化并合并请求，每个 stack 一个 MQSim worker。快速路径（时序模式名 `fast`）在 GPU 上直接计算，图中没有单独画出。
- **HBF 建模：热学与可靠性。** 按设计，结温决定 HBF 能维持的速率与数据保持期限；期限逼近就必须刷新，也就是先读一遍再重写一遍；服务策略决定速率与准入；证据输出记录字节数、磨损与平均无故障时间（**MTBF**）。图中 Junction temp、Service policy、Retention、Refresh、Evidence 五个灰色点线框就是热学与可靠性模型。热学与可靠性模型属于第三类：`main` 上是默认关闭的 CPU 实验（EQ3），还没有接进运行时的时序路径。EQ3 只在擦除成功时累加磨损计数，没有寿命模型，所以还不输出 MTBF。

### 两种模式

| 模式 | 改变了什么 | 回答的问题 |
|---|---|---|
| **Timing-only** | 数据仍然留在普通 GPU 显存里，HBFSim 只对注册过的访问注入延迟。 | 当前负载对 HBF 的延迟、带宽与争用有多敏感？ |
| **Capacity** | 注册的数据由文件承载，经过一个有界的、放在 GPU 显存里的 page cache 分页进出。 | 工作集大于 GPU 显存时，当前负载还跑不跑得动，cache 行为是什么样？ |

两种模式共用同一套显式注册区间、同一套 PTX 覆盖规则、同一批命名 HBF profile（profile 是 `configs/profiles/` 下的一份 JSON 文件，写着被模拟 HBF 的读写延迟、通道数、容量等参数，例如快速开始里用的 `nominal.json`）和同一个报告模型。

在 Capacity 模式下，一个上下文里每个注册过的文件区间共享同一个有界 page cache，这个 cache 放在 GPU 显存里（仓库代码与文档里叫 HBM cache）。命中直接落到常驻的 frame；未命中要把承载 page 读进来，计一次建模的介质读；脏 page 淘汰在这个 page 的字节写回承载文件之前，计一次建模的介质编程。

Capacity 模式有两个已知限制。脏 page 现在原地写回，而 HBF 不允许在 block 内随机写，这是一个登记在 [TODO.md](TODO.md#correctness-and-fidelity) 里的 A 级缺陷。page cache 的 frame 可能在 GPU 读完之前被重新分配，这个 race（frame 被提前重新分配）已经测到、还没有修复，已发布的数字没有走到淘汰路径，不受影响（[docs/51-defect-review-and-fixes.md](docs/51-defect-review-and-fixes.md)）。

## 功能一览

状态一列的三种写法：默认开启，指默认构建里就有、运行时可以直接用；需要显式打开，指要设构建选项或运行时开关；CPU 实验，指只在 CPU 上运行、没有接进真实 GPU 运行时。全模型权重注册、三个适配器、Issue 与 Q&A 讨论模板这三行不是单个开关能说清的，这三行的状态一列改为说明现有证据做到了哪一步、还缺什么。

| 功能 | 一句话说明 | 状态 | 证据 |
|---|---|---|---|
| PTX 自动改写与覆盖检查（coverage gate） | 自动改写受支持的 global load/store，不支持的访问计数并上报；capacity 区间遇到无法证明安全的代码就拒绝启动 | 默认开启 | [`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`](docs/proofs/2026-08-11-cd8p-vmem-tuning.md)：每份 pass manifest 报告 30 条改写指令、0 条不支持指令 |
| 显式注册的地址区间 | `hbfsim_register_device` 把一段显存注册为 timing 区间，`hbfsim_map_file` 把文件映射成 capacity 区间 | 默认开启 | [`include/hbfsim/api.h`](include/hbfsim/api.h)，用法见本节末尾的代码 |
| Timing-only 与 Capacity 两种模式 | 只注入延迟；或者由文件承载、经 GPU 显存里的 page cache 分页进出 | 默认开启 | [`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md)、[`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md) |
| MQSim 参考路径与快速路径 | `reference`、`fast`、`hybrid` 三种时序模式 | 默认开启（`HBFSIM_ENABLE_MQSIM` 默认 `ON`） | [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md) |
| UCIe 多 stack 接口（v0.1.1） | AXI 读传输、跨 stack 的 page 条带化、请求合并与共享内存 worker 通信，配合事件驱动的时间推进与批量完成处理；每个 stack 一个 MQSim worker，各 stack 有独立的服务上限；只支持读 | 需要显式打开（`HBFSIM_ENABLE_UCIE=ON`），只在 CPU 上验证 | [docs/ucie](docs/ucie/README.md)、[发布说明](docs/releases/v0.1.1.md)、[`docs/proofs/2026-10-06-ucie-release-cpu.md`](docs/proofs/2026-10-06-ucie-release-cpu.md) |
| SASS 恢复 | 对没有 PTX 的闭源或预编译 kernel，先把选定的 kernel 从 SASS 恢复成 PTX，再插桩；恢复管线来自 hetGPU | 需要显式打开：普通入口默认关闭（`HBFSIM_ENABLE_SASS_LIFTER=0`），研究入口 `scripts/run_with_sass_research.sh` 默认打开 | [tools/sass_runtime](tools/sass_runtime/README.md) |
| 全模型权重注册 | OLMoE-1B-7B-0924：原生 PTX 路径（直接改写编译器生成的 PTX，不经过 SASS 恢复）注册全部 98 个受支持权重 storage，加上 SASS 恢复后注册全部 147 个 storage | 147 个 storage 那一半依赖需要显式打开的 SASS 恢复，只在一个固定请求上做过，没有做干净的源码重建与跨机器 GPU 复现。98 个 storage 那一半同样只在一个固定请求上做过；从源码重新构建的组件只在 8 个 storage 的代表性用例上重新跑通，98 个 storage 没有用重新构建的组件再跑一次 | [98 个 storage 的结果](docs/hbfsim-supported-weights/RESULTS.md)、[98 个 storage 一侧的源码重建记录](docs/hbfsim-supported-weights/REBUILT_SOURCE_VALIDATION.md)、[147 个 storage 的复现包](reproduction/sass-final147-20260926/README.md) |
| 三个适配器 | [`adapters/vllm/`](adapters/vllm/)、[`adapters/vllm_capacity/`](adapters/vllm_capacity/)、[`adapters/llama_cpp/`](adapters/llama_cpp/) | vLLM 与 llama.cpp 有真实 GPU 上的 proof；vLLM capacity 的离线 trace 回放只有 CPU 上的 fixture 测试 | [`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md)、[`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md) 里的 llama.cpp 一节 |
| EQ3 热学与可靠性模型 | 结温、按温度调整的服务速率、数据保持期限与刷新；代码在 `src/eq3_thermal/`、`experiments/eq3_system_thermal/` 等目录 | CPU 实验，默认关闭，没有接进运行时的时序路径 | [docs/eq3_thermal](docs/eq3_thermal/README.md)、[experiments/eq3_system_thermal](experiments/eq3_system_thermal/README.md)、[thermal_coverage.md](docs/eval/thermal_coverage.md) |
| 在第一次使用处等待 | 发射时登记 device future，到第一条读结果的指令处才等待；只覆盖 timing 区间里的 load，只用快速路径（时序模式名 `fast`）的标量模型 | 需要显式打开：编译开关 `HBFSIM_ENABLE_TIMING_FUTURES` 默认关闭；RTX 5090 上 6 个等待语义用例通过；仓库里现有的 profile 没有一份满足准入条件（条件包括 `time_scale` 为 1、profile 不由实测曲线标定；`time_scale` 是 profile 里把建模延迟整体放大的倍数，`nominal.json` 设为 100），用现有 profile 跑不到在第一次使用处等待的路径 | [docs/52-main-gpu-thermal-integration](docs/52-main-gpu-thermal-integration/README.md)、[docs/51](docs/51-defect-review-and-fixes.md) |
| PTX target 兼容修复 | bpftime 的两条构建路径保留 PTX target 特性，并给出编译诊断（提交 `fccfd23`） | 默认开启 | [排错说明](docs/troubleshooting.md) |
| Issue 与 Q&A 讨论模板 | 缺陷报告表单与 Q&A 表单 | 仓库里已提供，不是构建或运行时功能 | [bug_report.yml](.github/ISSUE_TEMPLATE/bug_report.yml)、[q-a.yml](.github/DISCUSSION_TEMPLATE/q-a.yml)、[docs/troubleshooting.md](docs/troubleshooting.md) |

把一段显存注册为 timing 区间，就是下面这几行，摘自 [`benchmarks/cuda/hbf_microbench.cu`](benchmarks/cuda/hbf_microbench.cu) 第 321–330 行；完整的 C API 在 [`include/hbfsim/api.h`](include/hbfsim/api.h) 第 92–107 行。

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

只调用 C API 不会改写 kernel。PTX 改写由 bpftime 拦截路径完成，入口脚本是 `scripts/run_with_bpftime.sh`，用法是 `scripts/run_with_bpftime.sh -- command [args ...]`。注册要在第一次 kernel 启动之前完成。

## 快速开始

研究环境的编译器、依赖版本和完整构建命令，见 [RTX 5090 构建环境文档](docs/build/rtx-5090-environment.md)。RTX 5090 那台机器用来做 OLMoE 的研究构建，文档记录的是 2026 年 10 月 15 日的环境。[HBFSim 已经量到什么](#hbfsim-已经量到什么)一节里，除 OLMoE 全模型权重注册那一条之外，其余 GPU 数字都来自另一台机器：NVIDIA RTX PRO 6000 Blackwell Server Edition，driver 595.84。

遇到构建或运行问题，可按[排错说明和缺陷报告表单](docs/troubleshooting.md)提供环境、命令及首个完整错误。

HBFSim 需要 Native Linux、CMake 3.25 或更新、Ninja、支持 C++20 的编译器、OpenSSL、Python 3 和 Git。CUDA 部分需要 CUDA 12.8 或更新。

介质模拟器与介质模拟器的基准不装 CUDA 也能构建，下面就是这种配置：

```bash
git clone https://github.com/SlugLab/HBFSim.git
cd HBFSim

HBFSIM_ENABLE_CUDA=OFF HBFSIM_ENABLE_MQSIM=ON ./scripts/bootstrap.sh
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

直接照上面的 `ctest` 命令运行，会把 CI 按名字排除的 `vmem_tuning`、`run_with_bpftime`、`context_lifecycle` 三个测试也跑进去，其中 `context_lifecycle` 目前仍会失败。要得到 31/31，用 CI 的写法 `ctest --test-dir build --output-on-failure -E 'vmem_tuning|run_with_bpftime|context_lifecycle'`（出处是 [.github/workflows/ci.yml](.github/workflows/ci.yml) 与 [TODO.md](TODO.md#validation)）。

2026 年 9 月 19 日，在提交 `eee734a` 上、一台没有 CUDA 的机器上：configure 通过，152 个构建目标全部构建通过；34 个测试里，除去 CI 按名字排除的 3 个（`vmem_tuning` 与 `run_with_bpftime` 依赖特定环境，`context_lifecycle` 是尚未修复的缺陷），其余 31 个全部通过。[TODO.md](TODO.md) 记下了 CI 当时排除 `vmem_tuning`、`run_with_bpftime`、`context_lifecycle` 的原因。2026 年 9 月 19 日之后，`vmem_tuning` 改为缺外部标定 CSV 时跳过逐字节复现那一项子测试，不再因为缺这份 CSV 而失败（见[路线图与 TODO](#路线图与-todo)）。

如果机器上装了 libbpf 开发包和 clang，同时又关闭了 CUDA，configure 会在一个 `.cu` 目标上失败，报错是 `CMake can not determine linker language for target: hbfsim_llama_probe_module`。解决办法是直接调用 cmake 并加上 `-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON`（`scripts/bootstrap.sh` 不传这个选项），完整命令见 [docs/eval/runbooks/environment_setup.md](docs/eval/runbooks/environment_setup.md)。

默认分支是 `main`，所以上面的 `git clone` 不需要 `--branch` 参数。

SASS 恢复为实验功能，普通入口**默认关闭**。需要使用时，可按 [SASS 运行工具说明](tools/sass_runtime/)显式开启，并查看实际完成绑定且运行过的层。原生 PTX 插桩有独立路径，不依赖 SASS 恢复的开关。

### 看一次运行

介质基准不需要 GPU。介质基准用确定性的顺序请求驱动 MQSim 参考路径，输出一份 JSON。下面是提交 `0a0ae42`（2026 年 9 月 19 日）构建出来的一次输出，经过删减：去掉了 `schema_version`、`profile`、`workload` 三个顶层字段，并把几个对象压成了一行。

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

`capacity_bytes` 是 64 GiB，比 `nominal.json` 里写的 1 TiB 小，原因是没给 `--capacity-bytes` 时，基准程序把容量截到每个 plane 16 个 block；`hbm_cache_bytes` 也被截到 64 MiB。`nominal.json` 是合成参数，不来自实测。

`timing_ns` 里的 `modeled` 与 `wall` 两个字段要放在一起读。`timing_ns.modeled` 是建模的器件时间，`timing_ns.wall` 是 HBFSim 算出建模的器件时间所花的墙上时间。

介质基准把 `timing_ns.modeled` 与 `timing_ns.wall` 分开报告（CUDA 微基准 `hbf_microbench` 里对应的字段是 `timing.modeled_ns` 与 `wall_ns`），因为一个模拟器可以功能上完全正确，而模拟器自身的开销比建模的器件时间还大。

## HBFSim 已经量到什么

下面每一条都给出了出处文档。除另有说明外，GPU 实验用的是 NVIDIA RTX PRO 6000 Blackwell Server Edition，driver 595.84。RTX PRO 6000 的显存是 GDDR7，不是 HBM，在实验里扮演 HBM 的角色（[docs/eval/capacity_qwen3_30b_a3b.md](docs/eval/capacity_qwen3_30b_a3b.md)）。

- **六个标定断点全部精确吻合。** 实测的是 Dell CD8P 上把 NVMe SSD 当内存用的 vmem 软件路径：在 1、4、16、64、256、512 个连续 4 KiB page 上取 P50 延迟，做成一条标定曲线。把 P50 标定曲线装进 HBFSim 后，在 RTX PRO 6000 上真实运行一个 kernel，六个用例依次读 1、4、16、64、256、512 个 page，由 HBFSim 按标定曲线注入延迟（proof 里把这一步叫 real-GPU replay）。每个用例的建模总量都与源数据精确相符，checksum 与基线一致，不安全启动（HBF 地址传进了 HBFSim 无法证明安全的代码的 kernel 启动）0 次。

  六个断点精确吻合是确定性标定检查，不是交叉验证：六个点全部参与了拟合，没有一个被留出来。出处：[`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`](docs/proofs/2026-08-11-cd8p-vmem-tuning.md)。

- **快速路径比 MQSim 参考路径快约 22 倍。** 在同一个确定性的 Qwen3-30B 用例上，proof 里标为 reference 的那次运行用了 44.469 s，快速路径用了 2.014352 s，约 22 倍是由这两个时间算出来的；两次运行生成的 token 标识符完全一致。

  约 22 倍是 HBFSim 两条路径的墙上时间之比，不是对 HBF 硬件性能的预测。出处：[`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md)。

- **在 vLLM 上注入延迟，输出逐 token 不变。** vLLM 0.15.1 服务 Qwen3-30B，vLLM 源码不改，HBFSim 通过 loader plugin 接入，把 4 个 Triton kernel 变体绑定到改写后的函数。2,304 次 `fused_moe_kernel` 启动里有 24 次是建模启动（HBFSim 为这次启动里的 HBF 访问注入了建模的器件时间，覆盖记录标为 `modeled: true`），输出的 token 标识符与基线一致。

  vLLM 实验只注册了 `model.layers.0.mlp.experts.w13_weight` 这个张量的前 16,384 字节；模型全部权重共 61,064,245,248 字节，全部注册会让 vLLM 的 profiling 阶段产生数量上不可行的同步请求。出处：[`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md)；全部权重的字节数见 [`docs/proofs/2026-08-11-vllm-timing-adapter.md`](docs/proofs/2026-08-11-vllm-timing-adapter.md)。

- **110 GiB 的逻辑地址范围跑在一块 97,887 MiB 的 GPU 上。** 110 GiB 逻辑地址范围配 2 GiB 的 page cache（放在 GPU 显存里），128 次访问全部完成，产生的 checksum 是 `14245581564465502923`，与基线产生的 checksum 相同，不安全启动 0 次。

  110 GiB 这一条是稀疏的逻辑容量证明，不声称 110 GiB 被物理读过。出处：[`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md)。

- **温度改变同一块 GPU 的行为。** 同一个 BF16 8192x8192 矩阵乘，在温度从 35 升到 70 °C 的冷机独占运行里达到 379.117 TFLOP/s，在温度从 61 升到 85 °C 的热机独占运行（proof 里叫 Warm isolated GPU）里只有 348.427 TFLOP/s，相差 -8.10%；热机那次 SM 时钟从约 2,062 MHz 降到 1,642 MHz。出处：[`docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md`](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md)。

  -8.10% 是 GPU 计算芯片自己降频，不是 HBF 的服务速率。另一次测量（矩阵乘与 fio 并发 30 秒，见 [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md)）得到 -3.72%，热学配置文件用的是 -3.72%，见 [TODO.md](TODO.md#modeling-endurance-and-temperature)。HBF 的温度目前还没有接进运行时的时序路径。

- **`cd8p-vmem-p50` 这份时序 profile 由一台真实器件标定而来。** 标定源是一块通过 PCIe 5.0 32 GT/s x4 接入的 Dell DC NVMe CD8P E3.S 1.92TB。这块 Dell CD8P 是普通 PCIe NVMe 端点，不是 CXL 端点（[`docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md`](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md)）。

  只有 `cd8p-vmem-p50` 这一份 profile 来自 CD8P 的实测曲线；`conservative`、`nominal`、`aggressive` 三份命名 profile 是合成参数，快速开始的示例用的就是合成的 `nominal.json`。

- **全模型权重注册与绑定。** 模型是 OLMoE-1B-7B-0924，两次运行都只处理一个固定的短请求。

  原生 PTX 路径注册了全部 98 个受支持权重 storage（13,091,213,312 字节），插桩运行与不经 HBFSim 的基线运行都返回 token `[[70, 13]]`；98 个 storage 那次运行在 RTX 5090 上。其余 49 个 storage（attention projection、router、output head）在原生 PTX 路径上不注册为 HBF、照常执行，不算进 HBF 覆盖。出处：[`docs/hbfsim-supported-weights/RESULTS.md`](docs/hbfsim-supported-weights/RESULTS.md)。

  加上 SASS 恢复路径之后，147 个 storage 那次运行（复现包目录名 `reproduction/sass-final147-20260926/` 里的 final147 指的就是这次运行）注册了全部 147 个去重权重 storage（13,838,323,712 字节），把消费者绑定到 HBF 插桩路径，输出仍是 `[[70, 13]]`；147 个 storage 那次运行同样在 RTX 5090 上（[权重接入对照表](reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/weight-integration-matrix.md)）。详见[实现与验证报告](reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/final147-20260926-v1/REPORT.md)。

  OLMoE 的两次结果都不是通用的 kernel、模型或消费者覆盖；注册了完整的 storage，不等于一次短请求读过每一个字节或每一个 expert。[SASS final147 复现包](reproduction/sass-final147-20260926/README.md)保存 final147 实验实际使用的运行时源码、接口契约、构建步骤和固定请求验收证据，但没有做干净的源码重建，也没有做跨机器的 GPU 复现。

  SASS 恢复依赖的 hetGPU 管线有 5 个上游 PR。[最初的上游 PR 审核](reproduction/sass-final147-20260926/upstream-review/REVIEW_SUMMARY.md)与模型结果分开，单独记录了通用性限制，当时 5 个 PR 都没有合并。之后 5 个 PR 都修复并合入了 `vickiegpt/Concordia` 的 `tmatmul` 分支，合并后的 107 项 CPU 检查全部通过，见[上游修复与合并记录](reproduction/sass-final147-20260926/upstream-repair-followup/README.md)。

构建通过、CPU 测试通过、MQSim 回归通过，加上 PTX 汇编成功，这四件事都不构成真实 GPU 上的证据。

「decode 会慢多少」这个问题，`main` 上还给不出一个数。评估文档写明 time per output token（**TPOT**）不支持，prefill 与 decode 的端到端时延还没有核实（[docs/eval/capacity_qwen3_30b_a3b.md](docs/eval/capacity_qwen3_30b_a3b.md)）。现有结果证明的是正确性与注入能力：注入发生在真实 kernel 上，输出不变。

## 后续 4 KiB 实验基准与构建选项

后续论文实验确定先输入 HBF **总容量 2 TiB、stack 数 4**，默认均分为每个 stack 512 GiB；`nominal` 是后续论文主基准。下表是**已确定的研究设计，不是现成可运行的配置，也不是在 HBF 器件上测得的数据**。容量、每个 stack 的限制和待做的验证见[详细说明](docs/evaluation/hbf-4k-study-baseline.md)。

| 研究档位 | page 大小 | 读 | 写 | 内部通道总数 | 总 page 请求队列条目 | 总带宽上限 |
|---|---:|---:|---:|---:|---:|---:|
| `conservative` | 4 KiB | 20 μs | 200 μs | 16 | 1024 | 128 GB/s |
| `nominal` | 4 KiB | 10 μs | 100 μs | 32 | 2048 | 512 GB/s |
| `aggressive` | 4 KiB | 5 μs | 50 μs | 64 | 4096 | 1000 GB/s |

总带宽上限是唯一的带宽输入。若 stack 数为 `N`，每个 stack 的上限自动取「总带宽上限 ÷ N」并分别执行；`nominal` 是每个 stack 512 ÷ 4 = 128 GB/s，繁忙的 stack 不能借用其他 stack 的空闲额度。`capacity_bytes` 是 HBF 总容量，总带宽上限与总容量分别配置；page 数由容量和 page 大小推导。

程序实现、运行前验证和正式矩阵预算确认仍待完成。仓库现有 `configs/profiles/{conservative,nominal,aggressive}.json` **仍是每份 1 TiB、16 KiB page 的历史合成配置**，没有实现上表的四个 stack 的服务，不能直接拿它们启动新基准。

另一份现有 profile `cd8p-vmem-p50` 不是合成的：`cd8p-vmem-p50` 由 Dell CD8P 的实测延迟曲线标定，但这条曲线包括完整软件路径及其开销。上表三档是研究假设；OCP 的 HBF 规范不证明这里假设的独立读取单元数量或可达速率。现有历史 JSON 均放在 `configs/profiles/`，由带类型的加载器对照 `configs/schema/hbf-profile.schema.json` 校验。

| 构建选项 | 默认值 | 用途 |
|---|---|---|
| `HBFSIM_ENABLE_CUDA` | `ON` | 构建 CUDA 插桩与运行时组件 |
| `HBFSIM_ENABLE_MQSIM` | `ON` | 构建由 MQSim 支撑的主机服务 |
| `HBFSIM_ENABLE_UCIE` | `OFF` | 构建 UCIe 多 stack 接口与主机后端 |
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | 启用 llama.cpp 与 vLLM 集成测试 |
| `HBFSIM_ENABLE_EVAL_TOOLS` | `OFF` | 构建离线评估模型与回放工具 |
| `HBFSIM_ENABLE_TIMING_FUTURES` | `OFF` | 构建「在第一次使用处等待」的实验路径，需要 CUDA 13 与 sm_120 |

`scripts/bootstrap.sh` 只会把 `HBFSIM_ENABLE_CUDA`、`HBFSIM_ENABLE_MQSIM`、`HBFSIM_ENABLE_LLM_TESTS` 三个开关原样传给 cmake，其余选项要直接用 `cmake -D...` 传。另有三个默认关闭的测试与诊断选项，见 [`CMakeLists.txt`](CMakeLists.txt) 第 7–15 行。

## 路线图与 TODO

[TODO.md](TODO.md) 是公开的待办清单，每一项都写明关闭这一项需要什么证据。下面划掉的是已经完成的工作；没有打钩的是还没做完的，其中做了一部分的标为「部分完成」。

**v0.1.0 已完成的工作**（[TODO.md 的 Done 一节](TODO.md#done)）

- [x] ~~加入 Apache-2.0 许可证、`CITATION.cff`、行为准则与安全策略~~
- [x] ~~默认分支由 `eval_base` 改名为 `main`~~
- [x] ~~外部用户 `git clone --recurse-submodules` 不再因私有的 `paper` 子模块失败~~
- [x] ~~加入只用 CPU 的 CI、Issue 与 pull request 模板，以及文档链接检查脚本~~
- [x] ~~加入架构图，重写 README，加入中文 README、`TODO.md`、`CONTRIBUTING.md` 与 `docs/README.md`~~
- [x] ~~在没有私有仓库凭据的机器上走通公开克隆与 CPU 构建~~
- [x] ~~发布 v0.1.0~~

**v0.1.0 之后已完成的工作**

- [x] ~~v0.1.1：可选的 UCIe 多 stack 读接口，接上真实 MQSim 后端，在 CPU 上验证~~（[发布说明](docs/releases/v0.1.1.md)）
- [x] ~~原生 PTX 路径覆盖 OLMoE-1B-7B-0924 全部 98 个受支持权重 storage~~（[结果](docs/hbfsim-supported-weights/RESULTS.md)）
- [x] ~~SASS 恢复路径与 final147 复现包，在一个固定请求上注册 147 个 storage；致谢 hetGPU~~（[复现包](reproduction/sass-final147-20260926/README.md)）
- [x] ~~SASS 恢复改为默认关闭；修复 PTX target 兼容并给出编译诊断；加入排错说明与 Q&A 表单~~（[排错说明](docs/troubleshooting.md)）
- [x] ~~EQ3 热学与可靠性工具合入 `main`，作为隔离的 CPU 实验~~（[集成说明](docs/52-main-gpu-thermal-integration/README.md)）
- [x] ~~用七个提交修复时序与插桩路径上的六组缺陷，并在 RTX 5090 上实测等待语义~~（[缺陷复核与修复](docs/51-defect-review-and-fixes.md)、[RTX 5090 等待语义检查](docs/52-main-gpu-thermal-integration/README.md)）
- [x] ~~历史文档、日志与输入示例改为路径归一化副本，附 `SHA256SUMS` 校验清单~~（[公开副本说明](PUBLIC_COPY_NOTICE.md)）

**部分完成**

- [ ] 在第一次使用处等待。机制已经实现，RTX 5090 上的等待语义检查通过；编译开关默认关闭。仓库里现有的 profile 没有一份满足准入条件（条件包括 `time_scale` 为 1、profile 不由实测曲线标定），所以用现有 profile 跑不到在第一次使用处等待的路径（[docs/51](docs/51-defect-review-and-fixes.md)）。仓库里也还没有与在发射点等待的成对测量（[TODO.md](TODO.md#correctness-and-fidelity)）。
- [ ] 多 stack 建模。只在可选的 UCIe 读路径上做到每个 stack 一个 MQSim worker、按 page 条带化、按 stack 分摊容量与带宽；没有写入路径，没有 GPU 上的运行记录，也没有 2 个 stack 与 8 个 stack 的扫描（[TODO.md](TODO.md#modeling-stacks-links-and-topology)）。
- [ ] `vmem_tuning` 测试缺外部标定 CSV 时不再失败，但没有这份 CSV 仍做不了逐字节复现（[TODO.md](TODO.md#validation)）。
- [ ] 文档链接检查已经进了 pull request 门禁，还不检查外部链接（[TODO.md](TODO.md#repository-and-tooling)）。

**还没做的**

- [ ] 把温度接进运行时的时序路径（[TODO.md](TODO.md#modeling-endurance-and-temperature)）。
- [ ] 留出标定点做验证；六个断点现在全部参与了拟合（[TODO.md](TODO.md#validation)）。
- [ ] 写入改为每个 4 KiB 编程单元计一次写入时间（现在每条 store 指令计一次），Capacity 模式淘汰的 page 不再原地重写（[TODO.md](TODO.md#correctness-and-fidelity)）。
- [ ] 在同一输入上对比快速路径与 MQSim 参考路径（[TODO.md](TODO.md#validation)）。
- [ ] 每个报告的数字都记下 `time_scale`，三份命名 profile 的 `page_bytes` 改为 4096（[TODO.md](TODO.md#correctness-and-fidelity)）。
- [ ] 同一 page 的重复读改由 page buffer 供给，并加入跨 warp 的 page 驻留过滤（[TODO.md](TODO.md#correctness-and-fidelity)）。
- [ ] 跑完评估计划里四个评估问题的正式测量；按 TODO.md 的统计，正式完成的评估问题目前是 0 个（[TODO.md](TODO.md#evaluation-campaign)）。

## 仓库结构

- `src/`：全部 C++ 与 CUDA 生产代码，分 11 个组件，即 CUDA 拦截运行时（`cuda_runtime/`）、PTX pass（`ptxpass_hbf/`）、主机服务（`host_service/`）、UCIe 多 stack 后端（`ucie/`）、热学求解器（`eq3_thermal/`，独立的 CPU 库，不进顶层构建）、MQSim 适配层（`mqsim_adapter/`）、profile 加载器（`profile/`）、prefetch（`prefetch/`）、报告（`reporting/`）、快速模型标定器（`hybrid/`）与 page 协议（`protocol/`）。按代码行数，前三大是 CUDA 拦截运行时、UCIe 多 stack 后端与 PTX pass。
- `include/hbfsim/`：公开头文件，以及跨越主机与设备边界的契约。
- `adapters/`：三个集成适配层，`llama_cpp/`、`vllm/`、`vllm_capacity/`。
- `benchmarks/`：测量驱动程序，包括 CUDA 微基准、MQSim 介质基准（含 UCIe 基准）、prefetch 与 trace 回放。
- `configs/`：JSON fixture，包括命名 HBF profile（UCIe 的 profile 在 `profiles/ucie/`）、三组参数扫描、热学 fixture（`thermal/`、`eq3_thermal/`）、`ptxpass/` 与 `tuning/` 两个目录，以及 `configs/schema/` 下的 4 份 JSON Schema。
- `scripts/`：bootstrap 入口、Python 评估框架、热学标定流水线，以及 `run_with_bpftime.sh`、`run_with_sass_research.sh`、UCIe 复现脚本与文档链接检查脚本。
- `tests/`：CPU 测试、集成测试、GPU 测试、PTX fixture。
- `tools/`：SASS 恢复工具（`sass_runtime/`）、原生 PTX 复现工具（`native_ptx_reproduction/`），以及 EQ3 热学的配置与求解工具。
- `experiments/`：EQ3 热学与维护的 CPU 实验（`eq3_system_thermal/`、`eq3_rate_thermal/`、`eq3_maintenance/`），默认关闭。
- `reproduction/`：SASS final147 复现包（`sass-final147-20260926/`），以及 `aggregate-pass/` 下的 clean-build 回执与其它 JSON 校验记录。
- `patches/`：打给两个固定版本子模块的外部补丁。
- `third_party/`：两个固定版本的子模块，bpftime 与 MQSim。
- `cmake/`：子模块版本固定、PTX 嵌入，以及打过补丁的 MQSim 与 bpftime 构建。
- `docs/`：设计规格、实现计划、proof、评估 runbook、参考论文，以及 EQ3 热学、UCIe、发布说明、OCP 规范原文、构建环境与排错说明。
- `paper/`：一个与构建、测试都无关的子模块。`.gitmodules` 把 `paper/` 标成非激活，`scripts/bootstrap.sh` 只初始化两个构建依赖。

## 文档

- [`docs/README.md`](docs/README.md)：文档索引。
- [`docs/ucie/`](docs/ucie/README.md)：UCIe 接口、多 stack 配置、软件优化与 CPU 复现命令。
- [`docs/releases/v0.1.1.md`](docs/releases/v0.1.1.md)：v0.1.1 实现更新与发布版本构建说明。
- [`docs/proofs/`](docs/proofs/)：proof，存放 [HBFSim 已经量到什么](#hbfsim-已经量到什么)一节里多数实验的 checkpoint 文档，每份都写明主张边界，多数附带运行命令；后来的结果另见 [`docs/hbfsim-supported-weights/`](docs/hbfsim-supported-weights/README.md) 与 [`reproduction/`](reproduction/)。
- [`docs/eval/`](docs/eval/)：评估计划、负载方法学、复现 runbook。
- [`docs/eq3_thermal/`](docs/eq3_thermal/README.md)：EQ3 热学模型的设计与 CPU 验证状态。
- [`docs/hbfsim-supported-weights/`](docs/hbfsim-supported-weights/README.md)：OLMoE 受支持权重的原生 PTX 插桩记录。
- [`docs/skills/`](docs/skills/)：一份阅读顺序，外加每个子系统一份文档，写给第一次读这套代码的人。
- [`docs/reference/`](docs/reference/)：参考笔记，含 CUDA 架构兼容性审查。
- [`docs/troubleshooting.md`](docs/troubleshooting.md)：报错与排错说明。
- [`TODO.md`](TODO.md)：开放的路线图，每一项都写明关闭这一项需要的证据。

## 论文与引用

描述 HBFSim 的论文在 [arXiv:2609.09800](https://arxiv.org/abs/2609.09800)。

```bibtex
@article{hu2026hbfsim,
  title   = {HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution},
  author  = {Hu, Yanpeng and Yang, Yiwei and Zhu, Yuanwu and Zheng, Yusheng and Zhang, Wei and Quinn, Andi},
  journal = {arXiv preprint arXiv:2609.09800},
  year    = {2026}
}
```

`CITATION.cff` 里是同一条引用，GitHub 据此在仓库页面上显示「Cite this repository」按钮。

## 参与贡献

[`CONTRIBUTING.md`](CONTRIBUTING.md) 写明一次贡献需要什么，以及这个仓库里最要紧的一条规矩：一个数字只能和产生这个数字的 proof 一起进仓库。

缺陷报告与功能请求走 [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/) 里的模板，使用上的疑问可以在 [Q&A 讨论区](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a)提问，pull request 按 [`.github/pull_request_template.md`](.github/pull_request_template.md) 提交。

提 pull request 之前，先按上面的快速开始构建并测试一遍，并在 pull request 里写清楚新的测量属于四种时间里的哪一种。

参与方式受[行为准则](CODE_OF_CONDUCT.md)约束。报告安全漏洞请按 [`SECURITY.md`](SECURITY.md) 的办法，不要开公开 issue。

## 许可证

HBFSim 以 Apache License 2.0 发布，完整条款见 [`LICENSE`](LICENSE)。

## 致谢

HBFSim 使用以下项目，其中 bpftime 和 MQSim 作为固定版本的子模块保留。bpftime 以 MIT 许可证发布，HBFSim 的 PTX 改写派生自 bpftime 的拦截路径。

MQSim 由苏黎世联邦理工学院（ETH Zurich）的 SAFARI Research Group 以 MIT 式许可证发布，提供 flash 介质模型，HBFSim 通过一个外部补丁在线驱动这个介质模型。

[hetGPU](https://github.com/vickiegpt/Concordia) 派生自 ZLUDA，以 Apache-2.0 或 MIT 许可证发布。HBFSim 使用 hetGPU 的 SASS→PTX 恢复管线，将选定 NVIDIA kernel 的 SASS 恢复为 PTX，再进行 HBF 访存插桩。感谢 hetGPU 项目的贡献者提供的基础工作。

