# HBFSim EQ 重规划：外部官方来源核验

> 分享副本：审计事实、数值和原执行路径保持原记录；仅调整导航。大模型、完整 raw、部分本机清单与可执行二进制未随仓库发布，范围见 [审阅包说明](README.md)。

核验日期：2026-09-08 UTC。范围：附件 K/R1–R19 的定向研究；没有安装模拟器、升级 CUDA、运行 GPU 性能实验或改动全局环境。`VERIFIED_SOURCE` 仅表示本轮读取了来源，不表示 HBFSim 已实现、软件在本机可运行或物理机制已验证。精确软件元数据见 [source-software-freeze.json](source-software-freeze.json)。

## 决策结论

旧 EQ1-B 应降为数值交叉验证/文献附录。MQSim 证明的是其已校准 SSD 域；2017 TSV NAND 是厂商原型，XL-FLASH 是另一类具体器件；它们不构成当前 HBF 的独立物理测量。采用这些组件本身不削弱评测价值，但不能把组件论文的创新或校准结果归为 HBFSim 的机制证据。主文 EQ1 应由原生 GPU 微基准、独立语义 oracle、限定域 SASS 交叉验证和实际流水 kernel 支撑；附录保留器件 profile、单位、参数来源与外推误差。

本轮已找到并读到实际 OCP HBF 规范，不能继续写成“只有公告、未取得规范”。规范可约束架构、协议和管理行为，仍不能替代器件延迟/功耗实测。Accel-Sim 2.0 已实际发布，但公开 release 的完整支持域是 Hopper；论文中的 B200 校准不能上升为 RTX 5090/SM120 校准。

## R1–R19 ledger

以下每行的 access_date 均为 **2026-09-08**；URL 为本轮实际读取的来源。浮动页只作检索入口，执行优先采用冻结版本。

| ID / 类型 / 状态 | 已核验 URL、版本或日期 | 可支持的字段和判断 | 不能支持的字段或结论 |
|---|---|---|---|
| R1 规范 / VERIFIED_SOURCE | [CUDA 13.0.0 archive: PTX ISA 9.0](https://docs.nvidia.com/cuda/archive/13.0.0/parallel-thread-execution/index.html)，章节 9.7.9.25、9.7.9.26、9.7.13.15–16 | async proxy、mbarrier phase/arrival/expected bytes、bulk group、`.read` 与完整完成的差别、TensorMap 更新的指令和目标约束。 | 物理寄存器、scoreboard、MSHR 生命周期、GNIC/SYNCS 内部时序；“PTX 支持”也不是当前编译参数/驱动/硬件实测通过。 |
| R2 编程规范 / VERIFIED_SOURCE | [当前 Asynchronous Data Copies](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/async-copies.html)；执行冻结为 [CUDA 13.0.0 Guide](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-c-programming-guide/index.html#asynchronous-data-copies)，§10.29–10.30 | TMA producer/consumer、可见性、设备端 TensorMap 修改与 release/acquire、每 CTA 的 acquire 要求。 | 最新重构 Guide 的全部特性不能默认兼容旧工具链；示例不能代替当前 ptxas 的 target-specific 接受性检查。 |
| R3 编程规范 / VERIFIED_SOURCE | [当前 Unified/System Memory](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/understanding-memory.html)；[CUDA 13.0.0 mapped memory](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-c-programming-guide/index.html#mapped-memory)，§6.2.6.3、24.1.2.4 | `canMapHostMemory`、映射分配与同步、原生 kernel 对 host memory 的访问。 | PCIe/系统内存不是 HBF；映射内存的原子性/TMA 能力不能由普通 LDG 成功推出。 |
| R4 原始论文 / VERIFIED_SOURCE | [NVBit NVIDIA 论文页](https://research.nvidia.com/publication/2019-10_nvbit-dynamic-binary-instrumentation-framework-nvidia-gpus)，2019-10 | SASS 动态二进制插桩、ISA 可见状态、预编译库观测的依据。 | 周期精确 GPU 模拟、隐藏硬件状态改写、2019 版本对 SM120 的支持；本机 NVBit 安装/功能状态。 |
| R5 原始论文 / VERIFIED_SOURCE | [PTX memory consistency formal analysis](https://research.nvidia.com/publication/2019-04_formal-analysis-nvidia-ptx-memory-consistency-model)，ASPLOS 2019 | scoped memory ordering、axiomatic model、语义 litmus/oracle 的方法学依据。 | 当前 PTX 9.0 所有新增异步/TMA 指令自动被 2019 证明覆盖；周期延迟和设备功耗。 |
| R6 官方项目+release / VERIFIED_SOURCE | [项目页](https://accel-sim.github.io/)；[v2.0.0 release](https://github.com/accel-sim/accel-sim-framework/releases/tag/v2.0.0)，2026-08-25，SHA `64653015f85fb5664c84a10f48527e8897d289d0` | H100/H200、TMA、mbarrier、dynamic try_wait、CGA multicast 的公开 release 支持声明；精确依赖见下一节。 | Blackwell/RTX5090 完整支持与 SM120 校准；HBFSim 的 held-out 误差，或本轮实际模拟结果。 |
| R7 原始论文 / VERIFIED_SOURCE | [arXiv abs](https://arxiv.org/abs/2608.22602)、[HTML v1](https://arxiv.org/html/2608.22602v1)，2026-08-23 | H100/B200 的作者实验与模型机制、TMA/mbarrier/multicast 研究交叉验证候选。 | SM120 校准，或对应 release 已完整包含论文全部实现；论文/网页数值差异须保留，见下节。 |
| R8 原始论文 / VERIFIED_SOURCE | [Accel-Sim ISCA 2020 作者页](https://engineering.purdue.edu/tgrogers/publication/khairy-isca-2020/)，2020 | SASS frontend、microbenchmarks、counter-by-counter correlation 的既有方法。 | Hopper/Blackwell 新异步机制在 2020 版本中的支持。 |
| R9 专利实施例 / VERIFIED_SOURCE | [US20230289189A1](https://patents.google.com/patent/US20230289189A1/en)，2023-09-14 公开，尤其 Fig.21 和 GNIC/GPCARB/gnic2tex 段落 | 可用于构造共享路由、局部网络、多播争用等“应测的假说”。 | 对 SM120 实物资源数量、带宽、仲裁算法、布线布局的规范或测量；专利中的一实施例不可直接写入 simulator 当真实布局。 |
| R10 原始论文+软件 / VERIFIED_SOURCE | [MQSim FAST 2018](https://www.usenix.org/conference/fast18/presentation/tavakkol)；[CMU-SAFARI/MQSim](https://github.com/CMU-SAFARI/MQSim)，观察 head `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`（2025-10-09） | SSD NVMe 多队列、steady-state/GC、端到端请求延迟方法；可限定到 SSD calibration 的 QD/arrival/p99 交叉验证。 | 原始 MQSim 是经实物 HBF 校准的模拟器；通用 SSD GC 策略可无差别移入 OCP HBF profile。本项目实际 MQSim fork/SHA 应另在代码 ledger 绑定。 |
| R11 厂商原型公告 / VERIFIED_SOURCE | [2017 TSV BiCS NAND](https://www.kioxia.com/en-jp/business/news/2017/20170711-1.html)，2017-07-11 | 48-layer TLC、8/16 die、512 GB/1 TB、14×18 mm、1.35/1.85 mm、Toggle DDR 1066 Mbps；历史堆叠可行性。 | 当前 HBF 的 TSV 拓扑、tR/tPROG、热阻/热容、实测服务分布；接口 Mbps 不能直接当 package TB/s。 |
| R12 厂商产品资料 / VERIFIED_SOURCE | [XL-FLASH](https://hk.kioxia.com/en-hk/business/memory/xlflash.html)，浮动产品页，无页内修订日期 | 16-plane、4 KB page、read latency <5 µs；当前页含第二代 SLC/MLC 与具体型号资料。 | TSV HBF 的同一器件；<5 µs 包含任意系统排队/互连/加载全过程；网页值不可扩展到每一温度、密度、耐久状态。 |
| R13a 厂商公告 / VERIFIED_SOURCE | [Sandisk/SK hynix 公告](https://www.sandisk.com/company/newsroom/press-releases/2026/2026-08-03-Sandisk-and-sk-hynix-advance-global-standardization-of-hbf)，2026-08-03 | 规范发布事件与范围。 | 任何未在规范或数据手册中出现的技术数值；不能用公告替代下行规范。 |
| R13b 规范本体 / VERIFIED_SOURCE；本地 PDF 哈希 BLOCKED | [OCP HBF High-Level Base Die Specification](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf)，v0.7.0，2026-08-03，130 页 | 规范 profile、主机接口与热/刷新管理约束，下面给出必要页码与限制。 | 实际 HBF 测量；未定或产品特定数值；网站成功读取不能冒充已下载并 SHA256 冻结。 |
| R14 原始论文+软件 / VERIFIED_SOURCE | [3D-ICE 4.0 v1](https://arxiv.org/html/2512.05823v1)，2025-12-05；[release 4.0](https://github.com/esl-epfl/3d-ice/releases/tag/4.0)，SHA `e0bb6850c5e446363e26936586d625270c87f224` | 材料非均匀/各向异性、layer subdivision、局部网格、steady/transient 数值交叉验证；ROM 对高保真热求解器的 held-out 验证。 | 本项目 floorplan、边界条件、功耗输入已经正确；GPU/HBM/HBF 堆叠物理测温；全部 runtime 路径在线闭环。 |
| R15 原始物理测量论文 / VERIFIED_SOURCE | [HeatWatch HPCA 2018 PDF](https://people.inf.ethz.ch/omutlu/pub/heatwatch-3D-nand-errors-and-self-recovery_hpca18.pdf)，§3、§4.2 | 温度、dwell/self-recovery、P/E、retention 的耦合；被测 3D NAND 的 Ea fit=1.04 eV，95% CI 1.01–1.08 eV，Arrhenius fit R²=0.76，拟合温区 20–70°C。 | 这些数值是目标 HBF 的已校准参数；用单一 Arrhenius 公式解释全部 refresh/RBER；由模型外推推出实际寿命保证。 |
| R16 观测工具规范 / VERIFIED_SOURCE | [Nsight 当前页](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)；[CUPTI 13.0](https://docs.nvidia.com/cupti/13.0.0/main/main.html)。本机 NCU=2025.4.1.0，具体本地文档与哈希见下一节。 | replay/cache/clock 影响、counter 枚举、PM sampling 的统计窗口、overflow、pass 限制；分离原生 wall time 与 profile run。 | 每个 counter 都可在本机访问；把周期性采样称为逐周期无扰动真值；用 profiling wall time 替代 native 性能。 |
| R17 工具规范 / VERIFIED_SOURCE | [git-worktree 2.53.0](https://git-scm.com/docs/git-worktree/2.53.0)，本机 Git=2.53.0 | main/linked worktree 手动迁移后的 repair 行为与管理元数据范围。 | 存在兼容旧前缀时必须 repair；无备份重写工作区外共享仓库元数据。 |
| R18 工具规范 / VERIFIED_SOURCE | [conda-pack 0.8.1 docs](https://conda.github.io/conda-pack/) | 环境前缀 relocation 及 conda-unpack、同 OS 要求、package cache 要求。 | 对旧 prefix 仍有效的兼容软链接迁移必须重打包；替换二进制字符串等同正确迁移。 |
| R19 工具规范 / VERIFIED_SOURCE | [浮动 rsync manpage](https://download.samba.org/pub/rsync/rsync.1) 目前为 3.5.0；执行依据改为 [v3.4.1 对应文档](https://github.com/RsyncProject/rsync/blob/3305a7a063ab0167cab5bf7029da53abaa9fdb6e/rsync.1.md)，2025-01-15 | `-aHAXS --numeric-ids` 的准确含义；最终 `--checksum --dry-run --itemize-changes` 检验；见迁移边界。 | 复制命令成功即完整校验；hardlink 拓扑/稀疏占用/运行中 socket 状态自动完全相同；无 manifest 使用删除同步。 |

## Accel-Sim 2.0 的适用域与停止条件

- GitHub tag API 已把 `v2.0.0` 解析到 `64653015f85fb5664c84a10f48527e8897d289d0`。release 明示 tag 在 8 月 25 日重打过，旧标记 `0db0445` 的差别为文档。这里只记录事实，不执行其建议的 force-fetch。
- [配套 GPGPU-Sim PR 143](https://github.com/accel-sim/gpgpu-sim_distribution/pull/143) 于 2026-08-25 合并，merge SHA 为 `e10018b67a4b668e7b43f89280cf67624f1df4ff`。这是可冻结的性能模型起点；本轮没有把它编译/执行为已验证依赖。
- [该 release 的 setup_environment.sh](https://github.com/accel-sim/accel-sim-framework/blob/64653015f85fb5664c84a10f48527e8897d289d0/gpu-simulator/setup_environment.sh) 默认获取 GPGPU-Sim `dev` 和 pybind11 `master`。因此仅固定 framework tag 不够；若以后安装，须把性能模型、NVBit、配置、编译器和 pybind11 也逐项冻结。
- 官方网页与 release 均明确 H100/H200 的 TMA/mbarrier/multicast 功能；Blackwell B200/RTX5090 属 roadmap/experimental。论文 HTML 却给出了 B200 校准，二者范围不同。没有找到公开 release 对 SM120 的已校准支持承诺。
- 摘要页面与官网写 H100 mean absolute error **13.4%**；HTML v1 正文/Table VI 写 **13.5%**（B200 为 8.9%）。保留各来源原值，不替作者消除差异。其 Pearson=0.99 也不等于每一 kernel 误差可忽略。
- 当前 SM120 上的 cycle-level oracle 状态为 **BLOCKED_SUPPORT_DOMAIN**。后续只在实际受支持架构+配置下运行少量代表 kernel，分别核验 native baseline、memory perturbation 与瓶颈变化；不把全 LLM 模拟或移植 Blackwell 当 EQ1 的前置任务。

## PTX/CUDA 与 profiler 的执行约束

CUDA 13.0.0 对应 PTX ISA 9.0；应同时记录 nvcc/ptxas/driver、`.version`、`.target`、cubin/SASS hash 与实际编译选项。`cp.async.bulk.wait_group.read N` 只闭合源读取（含 TensorMap 读取）完成；无 `.read` 才包含目标写入及对执行线程的可见性。消费者与其他线程的顺序仍需要相应同步。`tensormap.replace` 在 PTX 9.0 支持 `sm_120a`/`sm_120f`，不能因此把普通 `sm_120` 所有编译输入均标为可用。[PTX 13.0.0](https://docs.nvidia.com/cuda/archive/13.0.0/parallel-thread-execution/index.html#data-movement-and-conversion-instructions-tensormap-replace)

归档 C++ Guide 的设备端修改示例仍突出 `sm_90a`；其示例的目标提示不可覆盖 PTX 的完整 target list。新 TensorMap 在每个使用 CTA 内建立适当 proxy acquire，不能用跨 CTA 的普通同步代替。native/shadow 完成联合门仍是软件可观察语义抽象，不能声称改写了硬件内部 scoreboard/SYNCS。

本机 NCU 文档：`/opt/nvidia/nsight-compute/2025.4.1/docs/ProfilingGuide/index.html`，SHA256 `415a4ae5d990c5111c532fcf7ada0014ba98a91072005765fb5b3da74b9b272c`；已读取 replay、cache control、clock control、warp sampling 段落。旧版在线 archive 请求失败时采用随工具安装的文档，没有误用浮动 13.3 页作为版本匹配证据。CUPTI 13.0 PM sampling 要求记录 sample interval、buffer overflow、metrics、pass 数；单 config 不支持多个 pass，不能把未采样周期补齐为实测。

## OCP HBF 的最小规范边界

来源为 [v0.7.0 规范本体](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf)，页码为 PDF 印刷页码：

| 页码 / 节 | 可冻结内容 | 使用限制 |
|---|---|---|
| p16 Tables 3–4 | 512 GiB 示例 cube、4096 B page；速级最大用户带宽 0.384/1.536/3.072 TB/s | 是规范/profile 数值，非实测时延；§5.1.2.2–3 的 94/188 GB/s 每 module ×16 与 Table 4 不完全一致，保留差异。 |
| p106–108 §9.1–9.2 | 工作结温 0–105°C；normal/light/severe/shutdown；severe 完成/报错在途请求后撤销 AXI Ready 并停止发放 credits | RTT/LTT/STT 是阈值接口；本项目阈值与 slowdown 曲线须另有参数来源，不能凭模式名补数。 |
| p111 Table 36 | 封装名义尺寸与若干 TBD | TBD/min/max 留待 v1.0，不能把近似几何当最终物理 ground truth。 |
| p117–118 §11.4–11.5 | 不支持 GC；refresh 与同 die read 冲突；retention 条件产品特定 | 通用 SSD profile 的 GC 不可直接移入；refresh 周期、能量和温变规律需独立约束。 |

这些内容足以影响 EQ2/3 profile 设计和反馈动作；没有提供目标 HBF 的完整 tR/tPROG/能耗/热 RC/retention 实物校准。论文需要把 **规范约束**、**厂商器件参数**、**数值模型**、**物理测量** 四层分开。

## 迁移工具的明确边界

rsync 3.4.1 的 `-a` 等于 `-rlptgoD`，不包含 ACL/xattr/hardlink；`-H -A -X -S` 分别追加硬链接、ACL、扩展属性与稀疏处理。`-l` 保留链接本身，不解引用外部链接。`--numeric-ids` 保留数值 UID/GID。源目标 ACL 必须兼容；root `-X` 不等于任意系统命名空间无条件复制。已有额外 hardlink 不保证被拆散，所以目标专属目录、逐项 manifest 与拓扑检查仍必要。[版本匹配的官方说明](https://github.com/RsyncProject/rsync/blob/3305a7a063ab0167cab5bf7029da53abaa9fdb6e/rsync.1.md)

最终静止状态可用同一过滤规则的 `rsync -aHAXSnci --numeric-ids -- SOURCE/ DEST/` 做一次 checksum dry-run，并独立核对关键 SHA256、路径清单、Git/worktree 状态、元数据和实际稀疏占用。`-c` 读取内容产生校验和来决定是否需传输，成本包括全文件 I/O；它不是对所有文件的 SHA256 证明。没有 `--delete` 时它不会暴露所有目标额外路径，需另比对相对路径 manifest。socket/lock 要按停机与重建规则处理，不列作持久内容等同项。

保留 `/root/hbfsim-exp` 兼容前缀是本次优先策略。Git `repair` 仅在管理链接确实失效时使用，备份管理元数据；主仓库和 linked trees 都改名时从主树传新路径进行 repair。不得将独立 checkout 误作 worktree，也不得因迁移直接重写工作区外的共享主仓库。[Git 2.53.0](https://git-scm.com/docs/git-worktree/2.53.0)

只有需要真实更换环境 prefix 时才考虑隔离的 conda-pack 流程；工具要求同 OS，依赖可用 package cache，执行 conda-unpack 后不能再任意移动已解包环境。当前旧 prefix 兼容且 smoke 成功时没有重打包需求。[conda-pack 0.8.1](https://conda.github.io/conda-pack/)

## 未闭合项与研究停止点

| 项目 | 本轮证据 / 精确状态 | 后续可解锁条件 |
|---|---|---|
| OCP PDF 本地副本 SHA256 | **BLOCKED_HTTP_403**。web 工具已读取 130 页 PDF 的文字；本地下载先因沙箱 DNS 失败，获准直接公开 HTTP 请求后服务器返回 403。没有生成有效 PDF 或声称哈希已冻结。web screenshot 请求返回引用但没有图像 payload，故表格判断采用带页码的 PDF 提取文本。 | 取得官方 PDF 原字节后 hash，人工/图像复核 Tables 3–4、36；规格字段保留当前已明确的差异。 |
| NCU 2025.4 archive 在线页 | **BLOCKED_FETCH**；已用本机同版本安装文档补足，哈希如上。 | 无需阻塞其他研究；性能采样前验证具体 metric 权限和硬件支持。 |
| Accel-Sim SM120 校准 | **BLOCKED_SUPPORT_DOMAIN**，公开 release 未给出该域的校准证据。 | 已冻结且有代表 kernel 校准的具体实现/配置；在此之前仅列独立未来任务。 |
| HBF 参数完整性 | **BLOCKED_PHYSICAL_PARAMETERS**：v0.7.0 包含产品特定项和 TBD，XL-FLASH/2017 TSV 不能拼成同一器件。 | 官方具体器件资料、测量，或显式区间/敏感度研究；后者必须标为外推。 |
| 知乎评论页 | [给定 URL](https://zhuanlan.zhihu.com/p/2070827402964497125) **BLOCKED_FETCH**。只把用户附件给出的审稿意见作为研究问题，未把网页正文假装已读。 | 可访问原文或用户提供正文；不阻塞依据已提供评论完成机制规划。 |

对这些缺口均停止扩张依赖，没有进行无限安装、移植、全矩阵或物理测量替代声明。
