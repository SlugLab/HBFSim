# 基于真实硬件锚点的 HBFSim Evaluation 重构方案

**对象：** HBFSim 论文 Evaluation 章节与实验执行计划
**项目：** [SlugLab/HBFSim](https://github.com/SlugLab/HBFSim)
**日期：** 2026-09-04
**依据：** HBFSim 当前实现与实验状态、OCEAN，以及 FAST/SC/ISCA/TCAD 中具有代表性的模拟器与仿真器工作。

---

## 0. Evaluation 重构结论

**论文的 Evaluation 应从“HBF 参数和应用优化实验的集合”重构为“先建立 HBFSim 的可信度、公平性与可复现性，再通过集中且具有代表性的 HBF 设计问题展示研究价值”。**

当前规划中的 HBM/HBF 容量比例、预取、MoE batch size、边缘—云端对比都具有研究价值，但它们属于 **use case / design-space exploration**，不能替代模拟器论文最核心的三类证据：

1. **Functional fidelity：** 程序结果、内存语义、覆盖判断是否正确；
2. **Performance fidelity：** 延迟分布、带宽饱和、排队、缓存转折点和应用级 slowdown 是否能对齐真实硬件或可信硬件代理；
3. **Practicality：** 达到上述保真度需要付出多少运行时间、内存和插桩开销。

因此建议将当前 14 个细碎问题压缩为 **3 个主 Evaluation Questions，加 1 个有条件保留的问题**：

> **EQ1 — Hardware-grounded fidelity.**
> 对于现有物理代理能够覆盖的行为，HBFSim 能否保持真实 GPU 工作负载的语义，并准确复现闪存后备内存层的延迟分布、带宽饱和、排队效应和缓存/容量转折？

> **EQ2 — Fidelity–cost trade-off.**
> HBFSim 的 reference、fast 与 hybrid 路径分别需要多少成本，其误差—速度 Pareto 前沿如何，研究者应如何选择？

> **EQ3 — Benchmark utility.**
> HBFSim 能否给出一个稳定、可复现、可解释的系统设计边界？以 30B 级 LLM 为例，HBM 有效驻留率、HBF 服务能力、预取与 serving concurrency 如何共同决定性能可行域？

> **EQ4 — Thermal sustainability（有条件保留）.**
> 温度闭环是否改变 HBF 的长期可持续服务率及应用结论？
> **只有在“温度 → HBF 服务率/刷新流量 → 每次访问时序”真正接入运行时，并完成硬件代理校准后，EQ4 才能保留为主问题。**

原 RQ5“边缘到云端”建议从本篇主线移除，可作为附录中的小型前瞻场景，或单独成文。TCO、车载、NAND 类型、die geometry 等也不应与基础保真度争夺正文篇幅。

## Implementation Snapshot

- Base `hybrid` SHA：`b41142288c1d1ca13be4219c320dbfa621a0300f`；
- Experiment branch：`exp/hbm-hbf-capacity-qwen3-30b-a3b`；
- Running worktree HEAD at publication：`7241d6d3212fd07e392be8fcd960e34b52c1edcc`；
- Published experiment-source snapshot：`f29dc572120cfe648ba90f4832ff922b864d8a53`；
- PR #4 integration SHA：`80fd29c30abbcc7aafcc54bde1a27ff0d3dc23c1`；
- PR #5：未进入该 experiment branch 的 ancestry，且其提交没有 patch-equivalent integration；
- Evaluation experiment code：已在上述 source snapshot 中冻结；正式实验正在进行中。

PR #4 已集成到本 Evaluation 使用的 experiment branch；PR #5 未集成。两者在 GitHub 上游的 pull-request 状态独立于该实验快照。所有正式结果必须记录 exact commit SHA；后续实现发生变化时，旧结果不得与新实现无标记混合。

---

# 1. Evaluation 设计约束

## 1.1 科学论证要求

本 Evaluation 设计需满足以下科学论证与复现约束：

- 不能只以 OCP HBF 规范或纯模拟结果证明模拟器准确；
- 必须加入物理硬件锚点，优先考察 U787 上的 CXL SSD；
- DRAM-backed 文件系统可以使用，但只能承担特定角色，不能包装成“真实 HBF”；
- EQ1 是论文基石，所有 MoE/Dense、预取和参数扫描均依赖 EQ1；
- 应参考 FAST 等系统论文在目标硬件未量产时如何建立“代理硬件—模型—应用”证据链；
- 论文核心定位是可供社区使用的 **benchmarking framework / evaluation platform**；
- 数据必须服务于“这个工具给出的比较是否可信”，而不是只追求某个漂亮 speedup；
- 边缘—云端问题范围过大，时间不足时应切除；
- 实验配置需压缩在约 2/3 单栏内，详细配置和原始数据放 artifact；
- 图表应统一视觉语义、坐标字体不小于 18 pt、HBFSim 使用一致的紫色；
- 所有术语、假设、公式和硬件代理边界都必须能由作者本人解释。

## 1.2 “Benchmark 工具”定位需要补充的条件

建议不要只说“HBFSim is a benchmark”。更严谨的表述是：

> **HBFSim is a hardware-grounded, execution-driven benchmarking framework for evaluating hypothetical HBF systems on real GPU workloads.**

原因是“benchmark”不仅是一段模拟器代码，还要求存在稳定的：

- workload/trace suite；
- device profile suite；
- metrics 和统计规范；
- calibration/validation split；
- reference results；
- coverage manifest；
- version、随机种子和环境记录；
- 原始 JSON/CSV 与绘图脚本。

在这些要素打包完成前，称为 **benchmarking framework** 比称为单一 benchmark 更准确。

“公平”也必须操作化。建议在论文中定义：

> 在比较两个 HBF 设计时，HBFSim 固定逻辑工作负载、数据放置、有效 HBM 驻留率、HBF 参数、随机种子、覆盖门、统计方法与 SLO，只改变被研究的一个机制；每次运行同时披露未建模、拒绝和成功建模的访问。

---

# 2. 对当前 Evaluation 规划的全面审核

## 2.1 当前方案的优点

HBFSim 当前已经具备一些模拟器论文非常需要的基础：

- 真实 GPU 上执行 vLLM/llama.cpp，而不是只回放离线 trace；
- 输出 token/checksum 一致性检查；
- timing-only 与 capacity 两种模式；
- MQSim detailed path、fast path 与 sampled hybrid path；
- 显式 HBF range 和 fail-closed coverage gate；
- 版本化 profile、proof 文档和可重复命令；
- 已有 over-VRAM、MoE kernel、热采样和 die/interleave 敏感性基础。

这些内容应保留，但当前写法将其拆成大量并列问题，导致“证据很多、主张却不集中”。

## 2.2 当前方案的主要问题

### 问题 A：14 个问题更像内部 work order，不像论文 Evaluation

当前大纲把语义、标定、开销、容量、温度、覆盖、预取、并行单元、batch、NAND、几何、边缘和 TCO 都单列成问题。对约 3 页的 Evaluation 来说，这会造成：

- 每个问题只能报一个局部数字；
- 验证与 use case 权重相近；
- 审稿人难以判断哪个结果支撑核心 claim；
- 多个问题互相依赖，却被写成彼此独立；
- 图表数量过多，正文无法解释误差和边界。

**修改：** 合并为 EQ1–EQ3；把细项变成子实验、消融或附录。

### 问题 B：现有准确性证据存在“同点拟合、同点验证”

当前 CD8P empirical curve 的六个点是拟合输入，同时又被用于展示零误差。它可以证明实现没有把表读错，却不能证明模型具有外推能力。

**修改：** 预先划分 calibration set 与 held-out validation set。拟合时不可读取 held-out 数据，正文报告 held-out 误差。

### 问题 C：缺少真实硬件与模拟器的同路径 A/B

仅将 MQSim、OCP 参数或解析公式互相比较，只能证明模型之间一致，不能证明真实世界误差。

**修改：** 把 CXL SSD、CD8P、DRAMFS 和 MQSim 赋予不同角色，并尽量让真实硬件与模拟路径共享同一个 GPU workload、页服务、缓存和统计链路。

### 问题 D：raw wall time 混合了目标延迟和模拟器自身开销

当前 Qwen live proof 中的巨大 slowdown 主要说明模拟器路径能工作，不能直接解释成假想 HBF 的性能。必须拆分：

\[
T_{\text{observed}}
=
T_{\text{native}}
+
T_{\text{instrument}}
+
T_{\text{service-path}}
+
T_{\text{modeled}}
\]

**修改：** 增加 instrumentation-only、DRAMFS zero-delay、hardware-backed 和 target-delay 四类控制组，用差分而非原始 wall time比较。

### 问题 E：Implementation snapshot 与分支集成状态必须显式冻结

本 Evaluation 使用独立 experiment branch 冻结实现状态。该分支基于 `hybrid` SHA `b41142288c1d1ca13be4219c320dbfa621a0300f`，包含 PR #3 的 `queue_depth` 修复，并通过 merge commit `80fd29c30abbcc7aafcc54bde1a27ff0d3dc23c1` 集成 PR #4。PR #5 的 head `1f19bdbba06c89cbaf9b7e6874f3473dc8123b2b` 既不是该分支的 ancestor，也不存在 patch-equivalent integration，因此不得把当前快照描述为已经集成 PR #5。GitHub 上 PR #4 与 PR #5 在发布时仍为 Open；上游 PR 状态不改变 experiment branch 的本地集成事实。

PR #3 修复后，旧有 p50/p99/带宽数字需要重新评估；PR #4 的 async-copy coverage 语义仍须通过 EQ1 验证；任何采用 PR #5 readahead 或 synthetic prefetch model 的后续结果都必须绑定新的 implementation snapshot，并与实际预测器、GPU 或设备测量严格区分。当前 vLLM runner 是确定性 offline generation，尚未形成在线 serving 的 TTFT/TPOT/SLO goodput 测量路径。

**修改：** 所有正式实验冻结并记录 exact commit SHA、PR integration SHA、build/test 状态与 profile hash；禁止不同 implementation snapshot 的结果无标记混用。

### 问题 F：热模型若没有闭环，不足以成为主 claim

温度采样曲线或阈值外推本身不等于“温度改变了 HBFSim 的应用性能”。若 runtime 中没有 temperature-dependent service rate、throttling/credit/refresh 行为，温度只能作为旁证。

**修改：** 实现并验证闭环，或将热部分降级为 limitation/future work。不能继续保留“核心挑战 C3”，但 Evaluation 中没有机制响应。

---

# 3. OCEAN 与代表性模拟器论文提供的实验范式

## 3.1 OCEAN：最值得直接借鉴的结构

OCEAN 在其 Evaluation 第 VI 节采用了清晰的顺序：

1. **Experimental Setup；**
2. **Fidelity of OCEAN；**
3. **Use Cases；**
4. **LogP Model Calibration；**
5. **Litmus Tests。**

其 fidelity 部分不是用一个应用 speedup 代替准确性，而是分别用：

- LMbench 验证工作集扩大时的随机访问延迟；
- STREAM 验证共享主机数增加时的带宽与竞争；
- OSU Allgather 验证多主机消息规模变化下的集体通信；
- 真实 CXL Type-3 系统作为硬件基准。

它还把访问时间分解为 sender overhead、link/switch latency、receiver overhead、serialization gap 和 contention，并采用：

\[
T_{\text{stall}}=\max(0,T_{\text{sim}}-T_{\text{IPC}})
\]

避免把已经发生的 IPC 开销再次加进目标延迟。其后才使用 TPC-C、YCSB 和 GROMACS 说明工具可以回答硬件一致性覆盖率、读写比例和 placement policy 等设计问题。

OCEAN 的 litmus tests 也值得借鉴：性能吻合不能替代语义正确，跨主机 store-buffer 和 atomic message-passing 单独验证了 coherence/ordering。

### OCEAN 不能直接照搬的地方

- 它的物理参照能覆盖的是现有 CXL Type-3/转换平台，不是全部 CXL 3.0 fabric 功能；
- OSU 结果中 RDMA 后端与实机仍约有倍数级差距，说明“趋势一致”与“绝对准确”必须区分；
- HBFSim 应比“closely tracks”更具体，报告 MAPE、尾延迟误差、饱和点误差和排名相关性；
- HBFSim 不能用 CPU/CXL 路径的绝对时间直接证明 GPU 封装内 HBF 的绝对时间，只能验证可共享的机制与相对趋势。

## 3.2 Cylon（FAST ’26）：最接近 HBFSim 的硬件代理方法

Cylon 在真实 CXL-SSD 原型 CMM-H 与软件模拟平台之间建立对应，最有价值的三个方法是：

1. **归一化工作集：** 当真实原型和模拟器的 cache capacity 不同，用
   \[
   r=\frac{\text{working-set size}}{\text{cache capacity}}
   \]
   比较缓存转折，而不是直接比较 GB；
2. **极端控制：** 强制 100% hit、0% hit，并把 NAND latency 设为 0，以隔离 cache hit path、miss path 和 emulator overhead；
3. **微基准 + 应用趋势：** 先比较延迟分布、带宽和 cache transition，再比较 Redis/GAPBS 的 normalized slowdown。

这正适合 HBFSim：不能只比较“某个 30B 模型跑了几秒”，还要检查 simulator 是否在工作集跨过 HBM cache 后，于正确位置、以正确形状出现性能转折。

## 3.3 MQSim（FAST ’18）：真实设备、稳态与明确误差

MQSim 先用多块真实 SSD 建立 ground truth，执行预条件化，再分别验证：

- 不同请求大小；
- 读与写；
- queue depth；
- synthetic workload；
- real storage traces；
- 平均与最大误差。

之后才用模拟器研究 inter-flow interference 和 fairness。其核心启示是：

> 如果设备状态、queueing 和 maintenance 没有进入稳定区间，应用级结果即使可重复，也不一定真实。

HBFSim 的真实硬件对比应区分 cold、warm 和 steady-state；对 CXL SSD/闪存代理，应记录温度、cache state、预取开关及写入历史。

## 3.4 FEMU（FAST ’18）：准确性、速度、可扩展性分开回答

FEMU 的 Evaluation 同时验证：

- 对真实 OpenChannel SSD 的时序准确性；
- emulator 在不同线程/队列负载下的可扩展性；
- 模型复杂度增加后误差是否下降；
- guest-observed latency 是否合理。

这支持 HBFSim 把 EQ1 和 EQ2 分开：reference path 更细，不代表适合跑完整 LLM；fast path 更快，也不代表足够准确。

## 3.5 NVMeVirt（FAST ’23）：多配置、分布与重复实验

NVMeVirt 的问题设置覆盖“支持什么、精度如何、可配置性如何、是否能支持研究”。它不仅报平均值，还比较：

- 多类真实设备；
- request-size sweep；
- latency percentile distribution；
- GC 时间序列；
- 应用 benchmark；
- 多次重复和误差条。

HBFSim 应采用相同纪律，至少对微基准执行 10 次、应用执行 5 次，并报告 95% confidence interval 或 bootstrap interval，而不是只给单次数字。

## 3.6 CXL-DMSim：校准、应用对齐与失败区间

CXL-DMSim 使用 FPGA/ASIC CXL 平台校准，然后验证 LMbench、STREAM、Redis/YCSB 等；重要的是它也指出中间 working-set/LLC 区间的模型误差来源，而不是只展示最佳区间。

HBFSim 应建立“误差地图”：在哪些 page size、QD、WSS、访问模式和覆盖比例下可信，在哪些区间只能用于定性比较。

## 3.7 Accel-Sim：对 Benchmark 工具而言，排名比单点误差同样重要

Accel-Sim 使用大量 workload/kernel 和硬件计数器验证，不只看一个 aggregate error，还看跨 workload 的相关性。对于 HBFSim 作为 benchmarking framework，最关键的问题之一是：

> 真实硬件认为 A 配置优于 B 配置时，HBFSim 是否也给出同样排序？

因此必须报告 Spearman/Kendall rank correlation，而不只报告平均绝对误差。

---

# 4. 真实硬件基准的正确角色划分

物理 HBF 尚不可获得时，不能找到一个“完全等价的真实 HBF”。正确做法是建立 **reference hierarchy**，每个参照只支持它能支持的 claim。

| 参照 | 最适合验证 | 不能证明 |
|---|---|---|
| U787 上的 CXL SSD（若为 DAX/NUMA/CXL.mem） | memory-semantic access、DRAM-cache/flash 转折、队列、带宽、读写不对称、应用 slowdown 趋势 | GPU 封装内 UCIe/HBF 的绝对延迟、HBF 专有协议与热耦合 |
| U787 上的 CXL SSD（若只呈现块设备） | 真实闪存服务时间、吞吐饱和、QD 与请求大小行为 | CXL.mem load/store 语义和 GPU 直接访问 |
| Dell CD8P NVMe | NAND/SSD 服务路径、请求大小曲线、排队、热行为代理 | CXL/HBF 接口与封装路径 |
| DRAMFS/tmpfs/memfd | 插桩、VMM、页服务、内存复制和软件路径的 zero-delay control | 任何真实 HBF/闪存性能 |
| MQSim detailed path | 复杂 NAND state、mapping、queueing 的可解释参考 | 物理 ground truth |
| OCP HBF specification | 合法参数范围、协议粒度、功能边界 | 实际设备性能 |
| HBFSim fast/hybrid | 大规模真实工作负载的快速探索 | 在未校准区间内的硬件真值 |

**关键修正：DRAMFS 不是“真实硬件基准”。**
它是必要且有价值的对照，但其作用是测量 HBFSim 已经付出的软件路径开销。只有 CXL SSD、NVMe/闪存设备或其他物理平台才能作为硬件锚点。

---

# 5. U787 硬件接入的两条实施路线

## 5.1 首先执行 Capability Inventory

在设计结果图之前，先确认 U787 上“CXL SSD”的实际形态：

```text
lspci -vv
cxl list -vv
daxctl list
ndctl list
numactl -H
lsblk -o NAME,MODEL,TRAN,SIZE,ROTA,MOUNTPOINTS
nvidia-smi topo -m
```

需要回答：

- 是 CXL Type-3 memory、DAX device、NUMA memory，还是普通 block device？
- 是否存在内置 DRAM cache；容量是否公开？
- page/cache line 粒度是什么？
- 是否支持 mmap/DAX？
- 是否能控制预取、cache policy 或 flush？
- GPU 与设备是否可进行 P2P/host registration？
- IOMMU、虚拟化和驱动是否阻止 GPU 直接映射？
- 可安全使用的 namespace/file/range 是什么？

在此之前，不应把实验写成“GPU 直接访问 CXL SSD”。

## 5.2 首选：同一 GPU 路径下的 hardware-backed A/B

若 HBFSim 的 capacity page service 可以切换 backing backend，建议构造三组：

1. **DRAMFS-zero：** 相同页服务、相同缓存、无目标延迟；
2. **CXLSSD-hardware：** 相同页服务，backing 放在真实 CXL SSD/DAX 上，不额外注入设备延迟；
3. **DRAMFS-simulated：** DRAMFS backing，但注入从真实 CXL SSD 校准得到的 latency/bandwidth/queueing 模型。

保持以下变量完全相同：

- GPU kernel 和请求序列；
- logical range；
- page size；
- HBM cache size；
- replacement/admission；
- arrival process/QD；
- warm/cold state；
- 数据内容与 seed。

比较：

\[
\Delta T_{\text{HW}}
=
T_{\text{CXLSSD-hardware}}
-
T_{\text{DRAMFS-zero}}
\]

\[
\Delta T_{\text{SIM}}
=
T_{\text{DRAMFS-simulated}}
-
T_{\text{DRAMFS-zero}}
\]

而不是直接比较两个完全不同软件栈的 raw wall time。

## 5.3 备选：保持到达时间的 trace-replay validation

若 GPU 无法直接映射/驱动 CXL 设备：

1. 从真实 GPU workload 记录 HBF request trace：
   - page/address；
   - operation；
   - size；
   - arrival timestamp；
   - stream/kernel；
   - in-flight depth；
2. 在 CXL SSD 上按原 arrival gaps 和 QD 回放；
3. HBFSim detailed model 用同一 trace；
4. 比较 request-level CDF、throughput、queueing knee 和 configuration ranking；
5. 应用级 wall time只通过 validated model 推回。

此路线可以支持“media/service-layer fidelity”和“relative trend fidelity”，但不能声称真实 GPU→HBF 端到端绝对误差。

---

# 6. 修订后的 EQ1：Hardware-grounded Fidelity

## 6.1 EQ1 的正式问题

> **For the behaviors that can be grounded in available hardware proxies, how accurately does HBFSim preserve workload semantics and reproduce the latency distribution, bandwidth saturation, queueing, and cache-capacity transition of a flash-backed memory tier?**

它应占 Evaluation 正文最大篇幅，建议占 45%–55%。

## 6.2 EQ1-A：Functional correctness 与 coverage

### 对照组

- native GPU；
- HBFSim instrumentation-only；
- HBFSim zero-delay；
- reference/fast/hybrid；
- capacity hardware-backed；
- capacity simulated-backed。

### 指标

- token IDs bit-exact；
- checksum；
- load/store correctness；
- crash/deadlock/timeouts；
- HBF-targeted kernel launch 总数；
- `modeled`、`no_registered_range`、`opaque_unmodeled`、`rejected` 数量；
- 被改写 memory op 数；
- unsupported op 数；
- HBF bytes 的覆盖比例，而不只按 launch 数统计。

### 必须完成的前置条件

- 当前 experiment branch 已通过 `80fd29c` 集成 PR #4；其 async global copies 覆盖语义必须在冻结快照上完成验证；
- capacity mode 不得 silently bypass；
- 对 fused-MoE kernel 不能只注册 16 KiB 前缀后就泛化到完整专家；
- 每个应用结果必须同时附 coverage manifest。

### 通过门槛

- semantic mismatch：0；
- silent unsupported HBF access：0；
- 所有未建模访问必须显式披露；
- application performance 结论只对 covered bytes/operations 生效。

## 6.3 EQ1-B：Single-request 与 request-size fidelity

### 扫描维度

- operation：read、write；
- size：4 KiB、8 KiB、16 KiB、32 KiB、64 KiB、128 KiB、256 KiB、1 MiB；
- pattern：sequential、random、strided、pointer-chase、mixed read/write；
- state：cold、warm、steady-state；
- QD：1 作为 isolate-latency 起点。

### 输出

- latency CDF；
- P50/P95/P99；
- mean 与 standard deviation；
- hardware vs HBFSim parity scatter；
- 每点 relative error。

## 6.4 EQ1-C：Bandwidth saturation 与 queueing fidelity

### 扫描

\[
QD \in \{1,2,4,8,16,32,64,128\}
\]

对每种 read/write ratio 和 request size测量：

- achieved bandwidth；
- IOPS；
- P50/P95/P99；
- queue waiting time；
- device service time；
- saturation knee。

定义 saturation knee 为达到峰值带宽 90% 的最小 QD，并报告 knee 的误差。当前 experiment branch 已包含 PR #3 的 `queue_depth` 修复；所有 QD 相关结果必须来自包含该修复的明确快照，因此这一项仍是 blocking experiment。

## 6.5 EQ1-D：Cache/capacity transition fidelity

不能直接用不同机器上的 GB 做横轴。采用 Cylon 式归一化：

\[
r = \frac{W_{\text{active}}}{C_{\text{HBM-cache,eff}}}
\]

扫描：

\[
r \in \{0.25,0.5,0.75,1.0,1.25,1.5,2,4\}
\]

`C_HBM-cache,eff` 是扣除模型常驻权重、KV、runtime 和安全余量后的有效缓存；若 CXL SSD 内部 cache 不透明，则通过 microbenchmark 测量 transition knee，不能仅引用厂商标称值。

输出：

- hit/miss ratio；
- dirty eviction；
- media reads/writes；
- read amplification；
- bandwidth；
- P99；
- transition point \(r^*\)；
- hardware 与 HBFSim 的 \(r^*\) 误差。

## 6.6 EQ1-E：Held-out application/trace fidelity

校准点与验证点必须事前冻结。

### 示例 calibration set

- size：4 KiB、16 KiB、64 KiB；
- QD：1、8、32；
- pattern：sequential read、random read；
- 1 个 workload trace。

### 示例 held-out set

- size：8 KiB、32 KiB、128 KiB、1 MiB；
- QD：2、4、16、64、128；
- strided、pointer-chase、mixed read/write；
- MoE-derived page trace；
- 至少一个完整 LLM run；
- 不参与拟合的 CXL SSD 或 CD8P 测量。

### 应用级指标

- normalized slowdown；
- output tokens/s；
- TTFT、TPOT/ITL、P95/P99（若 online serving runner 完成）；
- configuration ranking；
- phase-boundary location。

## 6.7 误差指标

单点相对误差：

\[
e_i=\frac{|\hat{x}_i-x_i|}{x_i}
\]

总体报告：

- median absolute percentage error；
- mean absolute percentage error；
- P95 error；
- maximum error；
- P50/P95/P99 latency error；
- peak bandwidth error；
- saturation-knee error；
- cache-transition error；
- application normalized-slowdown error；
- Spearman \(\rho\) 或 Kendall \(\tau\)；
- CDF distance，可选 KS/Wasserstein。

对接近 0 的量不要使用 MAPE，改报 absolute ns 或 symmetric percentage error。

## 6.8 建议事前注册的 claim gates

这些不是预设结果，而是“达到何种水平才使用何种措辞”的门槛：

| 条件 | 建议门槛 | 可支持措辞 |
|---|---:|---|
| 语义正确 | 100% | preserves application semantics |
| silent unsupported | 0 | fail-closed coverage |
| microbenchmark median error | ≤10% | quantitatively accurate in validated range |
| microbenchmark P95/max error | ≤20% | bounded tail error |
| macro normalized slowdown error | ≤15% | application-level predictive accuracy |
| rank correlation | ≥0.90 | reliable comparative benchmarking |

若绝对误差较高、但排名高度一致，应改写为“trend/ranking fidelity”；若排名也不一致，只能保留 component-level calibration claim。

---

# 7. 修订后的 EQ2：Fidelity–Cost Trade-off

## 7.1 正式问题

> **How much execution cost does HBFSim pay for its fidelity, and how should users choose among its reference, fast, and hybrid modes?**

## 7.2 必须拆分的时间

至少运行以下控制组：

1. Native；
2. instrumentation loaded but no registered range；
3. registered range + range lookup only；
4. capacity path + DRAMFS zero-delay；
5. timing model with zero target delay；
6. detailed reference；
7. fast；
8. hybrid；
9. hardware-backed。

报告：

- `wall_ns`；
- `instrumentation_ns`；
- `service_ns`；
- `modeled_ns`；
- `queue_wait_ns`；
- host CPU utilization；
- GPU idle/stall time；
- memory footprint。

若当前实现只有四个 aggregate counter，应增加足以隔离 zero-delay software path 的控制实验，而不是依赖计数器名字推断。

## 7.3 Fast/hybrid sweep

扫描：

- reference warmup requests；
- reference sample rate；
- request rate；
- number of warps/streams；
- QD；
- working set；
- model size。

主图为 Pareto：

- x：simulation slowdown 或 experiment wall time；
- y：held-out fidelity error；
- 每点标注 sample rate/warmup；
- reference、fast、hybrid 使用固定视觉编码。

当前“44.469 s vs 2.014 s vs 0.270 s”可以放入该图，但必须标记为 **emulator execution cost**，不能写成 projected HBF performance。

## 7.4 可扩展性

至少报告：

- 每秒可处理 HBF request 数；
- 随 warp/stream/QD 增长的 overhead；
- trace/event volume；
- peak control memory；
- host thread/core 使用；
- 多轮运行的 variance。

---

# 8. 修订后的 EQ3：一个集中、连贯的 HBF 研究用例

## 8.1 正式问题

> **Can HBFSim identify a reproducible feasibility boundary for 30B-scale LLM inference, and how do prefetching and serving concurrency move that boundary?**

此问把原来的“容量比例、预取、batch/MoE”整合成一个故事：

1. **基础可行域：** HBM 有效驻留率与 HBF 可持续服务率共同决定性能；
2. **机制扩张：** 预取、缓存和调度将可行域向外扩张；
3. **动态收缩：** batch/concurrency 增大带来专家并集扩大和 KV 挤占，使可行域收缩。

## 8.2 横轴不再使用简单 HBM:HBF 物理容量比

定义：

\[
C_{\text{cache}}=
C_{\text{HBM,total}}
-C_{\text{resident-weight}}
-C_{\text{KV}}
-C_{\text{runtime}}
-C_{\text{safety}}
\]

\[
\rho=
\frac{C_{\text{cache}}}
{W_{\text{HBF-eligible}}}
\]

\(\rho\) 才表示真正能常驻 HBM 的 HBF-eligible 权重比例。固定模型后，继续增加未访问的 HBF 容量不会自动提升性能，因此简单的 `HBM capacity / HBF capacity` 不是好的性能解释变量。

## 8.3 主相图

- x：有效 HBM 驻留率 \(\rho\)；
- y：HBF 可持续服务能力，可用 effective GB/s，或 `(parallel read-out units, tR)`；
- color：normalized TPOT、SLO goodput 或 slowdown；
- contour：5%、10%、20% degradation；
- 分别画 batch/concurrency 1、8、32 或使用小多图；
- 外推区域使用浅色底纹。

主结论不是“某比例最好”，而是：

> 在什么 HBM 驻留率和 HBF 服务能力组合下，系统满足既定 SLO；边界对模型、预取和 concurrency 有多敏感。

## 8.4 Dense–MoE 对照

至少设置两类 dense baseline：

1. **Capacity-matched：** 总参数/总权重规模接近 30B MoE；
2. **Active-compute-matched：** 每 token 激活参数或 FLOPs 接近 30B-A3B。

否则“MoE 更适合 HBF”可能只是因为它每 token 计算量更少，或总权重更大，因果无法分辨。

## 8.5 预取实验的术语和分层

FIFO、LRU、CLOCK 是 replacement policy，不是 prefetch algorithm。建议分为四层：

| 层 | 问题 | 策略 |
|---|---|---|
| Prefetch generation | 取什么、何时发出 | none、next-page、layer-ahead、router-history/model、oracle |
| Admission | 预取页是否进入 HBM | demand-only、always-admit、confidence-aware |
| Replacement | HBM 满时淘汰谁 | 当前 CLOCK、FIFO、LRU、expert-aware、Belady oracle |
| Scheduling | demand 与 prefetch 谁优先 | demand-first、prefetch priority、rate-limited、preemptible |

实验顺序：

1. 固定 CLOCK/admission/scheduling，只换 generator；
2. 固定代表性 generator，比较 CLOCK/FIFO/LRU/expert-aware；
3. 对最佳组合做 demand priority、drain rate、buffer capacity 消融；
4. 再改变 page/chunk/expert 粒度与 layout。

PR #5 提议的 next-page 模型尚未集成到当前 experiment snapshot。其 MoE stream 上的高“accuracy”主要来自**一个专家被选中后，其内部页面连续**；若后续纳入该机制，指标应重命名为：

> **within-expert spatial coverage / within-expert sequential predictability**

它不能替代 expert identity prediction。另行报告：

- expert-identity precision/recall；
- timely coverage；
- late-prefetch rate；
- useless-prefetch bytes；
- prefetch-induced eviction；
- read amplification；
- exposed stall fraction。

## 8.6 Batch size 应改为实际 serving concurrency

需要区分：

- prefill token batch；
- decode sequence concurrency；
- `max_num_batched_tokens` 上限；
- continuous batching 产生的实际 batch。

主横轴使用每个 decode step 实测到的 active sequences，而不是配置上限。

每层/每步记录：

- unique expert union；
- expert overlap/Jaccard；
- routing entropy/Gini；
- expert reuse distance；
- weight bytes requested；
- KV bytes；
- HBM cache occupancy；
- cache hit/miss；
- late/useless prefetch；
- TPOT/P99。

将真实 expert union 与独立均匀路由零假设比较：

\[
\mathbb{E}[U(B)]
=
E\left[1-\left(1-\frac{k}{E}\right)^B\right]
\]

该公式只作基线，不能代替真实 routing trace。

---

# 9. EQ4 热问题的保留条件

当前论文将温度列为核心挑战，但 Evaluation 必须形成完整因果链：

```text
GPU/HBF activity
      ↓
power trace
      ↓
temperature state
      ↓
LTT/STT/RTT / refresh / credits / service rate
      ↓
per-request timing
      ↓
application throughput and P99
```

只有温度曲线，没有后半段时，不能声称“temperature-aware HBF performance simulation”。

## 9.1 保留 EQ4 所需最低证据

- 温度模型对未参与拟合的真实温度 trace 有 held-out error；
- thermal state machine 与 OCP/硬件代理行为一致；
- 每个阈值状态对 bandwidth/latency 的映射可解释；
- detailed 与 fast path 的 thermal semantics 一致，或明确限制；
- 相同 workload 在 thermal-off/on 下结果差异；
- 外推点与实测点在图中严格区分。

## 9.2 若无法按期闭环

建议：

- 从标题、摘要和贡献中移除完整 thermal claim；
- 保留 thermal calibration 作为 preliminary capability；
- 将 retention/endurance/TCO 放 Discussion 或 future work；
- 不让未完成的 C3 削弱已经可验证的 C1/C2。

---

# 10. 正文 Evaluation 的推荐组织

在约 3 页正文预算下：

## 6.1 Methodology and Validation Contract（约 0.4–0.5 页）

- 2/3 单栏以内的 setup table；
- reference hierarchy；
- calibration/held-out split；
- repetitions/CI；
- claim scope。

## 6.2 EQ1: Hardware-Grounded Fidelity（约 1.3–1.5 页）

- correctness/coverage 小表；
- latency CDF/parity；
- QD-bandwidth；
- normalized WSS/cache transition；
- held-out application error。

## 6.3 EQ2: Accuracy–Cost Trade-off（约 0.4–0.5 页）

- time decomposition；
- reference/fast/hybrid Pareto。

## 6.4 EQ3: HBM–HBF Feasibility Boundary（约 0.6–0.8 页）

- phase diagram；
- prefetch 与 concurrency 如何移动边界；
- 不扩展成完整边缘/云端论文。

## 6.5 Limitations（约 0.2 页）

- CXL SSD/NVMe 是 proxy，不是 HBF；
- 被验证的参数范围；
- GPU direct-access 缺失；
- thermal 与 coverage 边界。

详细参数、NAND/die/interleave、TCO、额外模型全部进入 appendix/artifact。

---

# 11. 建议的正文图表

## Figure 1 — Hardware-grounded fidelity，三联图

- (a) hardware/HBFSim latency CDF；
- (b) throughput 与 P99 对 QD；
- (c) predicted vs measured parity scatter；
- 图注直接给 median/P95/max error。

## Figure 2 — Cache/capacity transition

- x：normalized WSS/cache ratio；
- y：normalized throughput 或 P99；
- hardware、reference、fast/hybrid；
- 竖线标 transition point；
- 小图报告 transition error。

## Figure 3 — Cost attribution and Pareto

- 左：native/instrument/zero-delay/model/hardware 的时间拆分；
- 右：reference/fast/hybrid 的 error–runtime Pareto。

## Figure 4 — Research utility phase diagram

- x：effective HBM residency；
- y：sustainable HBF service rate；
- color：normalized TPOT/SLO goodput；
- no-prefetch 与 best-prefetch 两个小图；
- concurrency contour 或小多图。

## 视觉规范

- 轴标题和刻度 ≥18 pt；
- legend 16–18 pt；
- line width ≥2 pt；
- HBFSim 固定紫色，真实硬件黑色/深灰，detailed reference 橙色，native baseline 蓝色或绿色；
- 同时使用 marker/line style，不能只靠颜色；
- 输出 SVG/PDF，PNG 只作预览；
- error bar 或置信带必须可见；
- 所有图使用相同语义的颜色；
- 不使用 3D 柱状图；
- 未实测/外推区域用浅色底纹，并在图注明确写出。

---

# 12. 统计与可复现性规范

## 12.1 重复次数

- microbenchmark：每点至少 10 次；
- application：每点至少 5 次；
- run order 随机化或交错，减少温度与系统漂移；
- 明确 warmup；
- 记录异常点，不静默删除。

## 12.2 统计

- 中位数 + 95% bootstrap CI；
- P50/P95/P99；
- coefficient of variation；
- 对尾延迟使用足够样本量；
- 报告原始 n；
- 多模型/多配置时报告 ranking correlation。

## 12.3 Manifest

每个 run 至少记录：

```text
run_id
timestamp
git_commit
dirty_tree
profile_hash
backend
hardware_inventory
driver/cuda/compiler
model/tokenizer/precision
prompt/input/output lengths
actual decode concurrency
logical range
effective HBM cache
page size
QD
access pattern
read/write ratio
prefetch/admission/replacement/scheduling
seed
repetition
temperature state
coverage counters
modeled/service/overhead/wall time
p50/p95/p99
bandwidth
checksum/token IDs
```

## 12.4 建议的统一 CSV 字段

```text
run_id,eq,backend,mode,profile,pattern,op,size_bytes,qd,
working_set_bytes,cache_bytes,normalized_wss,model,precision,
actual_batch,prefetch,replacement,seed,repeat,
lat_p50_ns,lat_p95_ns,lat_p99_ns,bandwidth_Bps,
modeled_ns,service_ns,overhead_ns,wall_ns,
coverage_modeled,coverage_opaque,coverage_rejected,
checksum_ok
```

---

# 13. 执行顺序与 Go/No-Go Gates

## Gate 0 — Freeze implementation

- 冻结当前 experiment branch 的 exact commit；
- 记录 PR #4 的集成 commit 与验证状态，并明确 PR #5 尚未集成；
- 所有 QD 相关结果使用包含 PR #3 `queue_depth` 修复的实现；
- 记录 build/test 状态与 profile hash；
- 禁止不同 implementation snapshot 的结果无标记混用。

**未通过：** 不开始论文主实验。

## Gate 1 — Identify U787 hardware semantics

- 完成 capability inventory；
- 确认是 CXL.mem/DAX/NUMA 还是 block；
- 确认安全使用方式；
- 验证 same-path hardware-backed 是否可行。

**失败回退：** trace replay，并收窄 claim。

## Gate 2 — Calibration

- 采集 calibration subset；
- 拟合 latency、bandwidth、queueing/cache 参数；
- 冻结 profile；
- 对 calibration point 只报告 fit，不称 validation。

## Gate 3 — Held-out fidelity

- 跑未见 size/QD/pattern/trace；
- 计算误差、rank correlation、transition error；
- 输出 fidelity card。

**若绝对误差未达门槛：** 修正模型或降级为 trend/ranking claim。
**若 ranking 也不稳定：** 不进入 EQ3。

## Gate 4 — Application and practicality

- semantic/coverage；
- detailed/fast/hybrid；
- zero-delay overhead；
- 完整 LLM held-out run。

## Gate 5 — One concentrated use case

- residency × service-rate phase map；
- prefetch boundary shift；
- concurrency boundary shift；
- dense/MoE controls。

## Gate 6 — Optional thermal

只有闭环完成后运行，否则移除主 claim。

---

# 14. 最小可投稿版本与完整版

## 14.1 最小可投稿版本

必须完成：

- U787 CXL SSD 或 CD8P 的硬件锚点；
- DRAMFS zero-delay control；
- calibration/held-out split；
- correctness + coverage；
- latency/QD/cache transition；
- fast/reference/hybrid Pareto；
- 一个 30B LLM phase diagram；
- 所有 QD 相关结果使用包含 PR #3 修复的冻结快照；
- 当前 experiment branch 中 PR #4 coverage integration 完成验证；
- 若最小版本采用 PR #5 的预取机制，先在新的明确快照中集成并验证，否则明确排除该机制。

暂时删除：

- 边缘—云端主 RQ；
- 车载；
- TCO 主结果；
- 大规模 NAND/die/interleave 扫描；
- 未闭环的 thermal main claim；
- 多个预取器的大而全比较。

## 14.2 完整版本

在最小版本上增加：

- 第二个真实设备；
- 第二个 MoE 和第二个 dense；
- online serving/trace；
- FP8/INT4；
- 完整 prefetch/admission/replacement/scheduling；
- thermal closed loop；
- TCO/endurance；
- edge/cloud crossover。

---

# 15. 建议写进论文的主张与限制

## 15.1 推荐主张

> HBFSim is an execution-driven and hardware-grounded benchmarking framework that runs real GPU applications while imposing configurable HBF timing and capacity behavior. Across the behaviors and parameter ranges covered by our physical proxies, it preserves application semantics, reports complete coverage, and reproduces latency, saturation, and cache-capacity trends with quantified error bounds.

数值门槛必须由实际结果填入，不能预写“high fidelity”。

## 15.2 必须主动写出的限制

- 实机是 CXL SSD/NVMe proxy，不是物理 HBF；
- proxy 只能验证共有机制；
- OCP 规范约束合法配置，不提供真实性能真值；
- DRAMFS 只隔离软件开销；
- 未覆盖的 GPU instructions/bytes 不纳入性能 claim；
- fitted point 不属于 validation；
- fast/hybrid 的误差只在 held-out 范围内成立；
- HBF-only 参数扫描是 projection，不是 measurement；
- thermal 未闭环时不声称 temperature-aware performance prediction。

---

# 16. 对原四个实验的最终裁决

| 原实验 | 裁决 | 修改后位置 |
|---|---|---|
| HBM/HBF 容量比例 | 保留，但不再扫简单物理容量比 | EQ3 的 effective HBM residency × HBF service-rate phase map |
| 预取、FIFO/LRU | 保留，但拆分 generation/admission/replacement/scheduling | EQ3 的 boundary-shift 机制实验；PR #5 尚未进入当前快照，采用前须另行集成并验证 |
| batch size–MoE | 保留，但用实际 decode concurrency 和 expert union | EQ3 的动态边界实验 |
| edge/cloud | 从主文移除 | 独立论文或 appendix exploratory scenario |
| thermal | 有条件保留 | 只有闭环与硬件校准完成才作为 EQ4 |
| NAND/die/interleave/TCO | 不作主 EQ | appendix/sensitivity/discussion |

因此，原设想不是被否定，而是被压缩为一个更强的 use case。论文的中心从：

> “HBF 在各种模型和参数下表现如何？”

改为：

> “HBFSim 的结果为什么值得相信，以及它能让研究者作出什么原先无法作出的、可复现的设计决策？”

该组织方式与 HBFSim 作为 hardware-grounded benchmarking framework 的论文定位一致，也更符合模拟器/仿真器论文的验证逻辑。

---

# 17. 参考文献及具体借鉴点

1. **Yiwei Yang et al.** “OCEAN: Open-Source CXL Emulation for Hyperscale Architecture and Networking.” 标注 SC’26，2026。
   借鉴：fidelity-first 组织；真实 CXL Type-3 锚点；latency/bandwidth/contention 分层验证；`Tstall=max(0,Tsim-TIPC)`；calibration decomposition；litmus tests。

2. **Yoon et al.** [“Cylon: A CXL-SSD Emulation Platform,” FAST ’26](https://www.usenix.org/conference/fast26/presentation/yoon).
   借鉴：真实 CXL-SSD 原型对比；normalized WSS/cache ratio；强制 hit/miss 与 zero-latency 控制；微基准之后再做 policy/prefetch use case。

3. **Arash Tavakkol et al.** [“MQSim: A Framework for Enabling Realistic Studies of Modern Multi-Queue SSD Devices,” FAST ’18](https://www.usenix.org/conference/fast18/presentation/tavakkol).
   借鉴：多真实设备；稳态/预条件化；synthetic 与 real trace 双重验证；平均和最大误差；验证后再做系统洞见。

4. **Huaicheng Li et al.** [“FEMU: A Fast, Accurate, Scalable and Extensible NVMe SSD Emulator,” FAST ’18](https://www.usenix.org/conference/fast18/presentation/li).
   借鉴：accuracy、speed、scalability、extensibility 分开论证；真实设备对齐；guest-observed performance。

5. **Sangjin Kim et al.** [“NVMeVirt: A Versatile Software-Defined Virtual NVMe Device,” FAST ’23](https://www.usenix.org/conference/fast23/presentation/kim-sangjin).
   借鉴：多类型设备与配置；percentile distribution；重复实验；GC 时间序列；应用验证；清晰的 Evaluation Questions。

6. **Y. Wang et al.** [“CXL-DMSim: A Full-System CXL Disaggregated Memory Simulator with Comprehensive Silicon Validation,” IEEE TCAD](https://doi.org/10.1109/TCAD.2025.3607145).
   借鉴：硬件校准；LMbench/STREAM/Redis-YCSB 多层验证；显式分析误差区间；验证后再做 CXL-SSD cache/prefetch 扩展。

7. **Gregory R. Ganger et al.** [“Timing-Accurate Storage Emulation,” FAST ’02](https://www.usenix.org/legacy/events/fast02/full_papers/ganger/ganger.pdf).
   借鉴：目标设备尚不存在时，如何把模拟器的 service time 与 emulator overhead 分开，并用 component simulator 验证服务时间。

8. **Mahmoud Khairy et al.** [“Accel-Sim: An Extensible Simulation Framework for Validated GPU Modeling,” ISCA ’20](https://doi.org/10.1109/ISCA45697.2020.00047).
   借鉴：跨大量 workload/kernel 与硬件 counter 的 correlation；对 benchmark 工具而言应验证 ranking，而不只验证单点均值。

9. **Yiwei Yang et al.** [“CXLMemSim: A Pure Software Simulated CXL.mem for Performance Characterization”](https://arxiv.org/abs/2303.06153).
   借鉴：真实硬件稀缺背景下的软件模拟定位；sampling period 与误差/开销权衡；unmodified application。

10. **D. D. Sharma, R. Blankenship, D. S. Berger.** [“An Introduction to the Compute Express Link (CXL) Interconnect,” ACM Computing Surveys, 2024](https://doi.org/10.1145/3669900).
    用途：界定 CXL proxy 与 HBF target 的共同点和差异，避免把 CXL SSD 直接写成 HBF。

11. **OCP HBF Specification v0.7.0.** [HBFSim 仓库中的规范副本](https://github.com/SlugLab/HBFSim/blob/hybrid/docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf).
    用途：参数合法范围、协议粒度和功能边界；不作为性能 ground truth。

12. **HBFSim current experiment work order.** [Experiments needed before submission](https://github.com/SlugLab/HBFSim/blob/hybrid/docs/%E9%87%8D%E8%A6%81%E5%AE%9E%E7%8E%B0%E9%97%AE%E9%A2%98%E4%BB%A5%E5%8F%8A%E9%9C%80%E8%A1%A5%E5%81%9A%E5%AE%9E%E9%AA%8C/15-experiments-we-must-add-before-submission.md).
    用途：把现有 23 项内部任务重新映射到 EQ1–EQ4，而不是逐项放进正文。

13. **HBFSim PR #3.** [Make the profile queue_depth field bound outstanding MQSim requests](https://github.com/SlugLab/HBFSim/pull/3).
    用途：说明旧 queue-depth 结果失效，所有相关数据必须在修复后重跑。

14. **HBFSim PR #4.** [Count asynchronous global copies as unsupported instead of dropping them](https://github.com/SlugLab/HBFSim/pull/4).
    用途：EQ1 coverage 的基础；experiment branch 通过 `80fd29c` 集成，上游 PR 状态独立。

15. **HBFSim PR #5.** [Prefetch: capacity-mode readahead, plus the model and accuracy experiment](https://github.com/SlugLab/HBFSim/pull/5).
    用途：候选预取机制；当前 experiment snapshot 未集成，后续采用时须建立新快照，且合成模型与真实 end-to-end measurement 必须严格区分。

---

## 附：一页式执行摘要

```text
论文定位：
    HBF 优化论文  →  hardware-grounded HBF benchmarking framework

证据顺序：
    语义/覆盖正确
        →  真实硬件代理的延迟、带宽、排队、缓存转折
        →  fast/reference/hybrid 的误差–成本
        →  一个集中 use case

真实参照：
    CXL SSD = system/service proxy
    CD8P    = flash/queue/thermal proxy
    DRAMFS  = zero-delay software-path control
    MQSim   = detailed model reference
    OCP     = target parameter envelope

正文 EQ：
    EQ1 Fidelity
    EQ2 Fidelity–Cost
    EQ3 Research Utility
    EQ4 Thermal（仅闭环完成后）

原四实验：
    容量 + 预取 + batch → 合并为 EQ3 可行域故事
    edge/cloud          → appendix / separate paper

投稿前 blocking：
    freeze exact implementation snapshot and PR integration state
    held-out hardware validation
    zero-delay overhead decomposition
    byte/op coverage
    actual decode concurrency
    rerun all QD-dependent results
```
