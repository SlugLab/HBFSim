# HBFSim EQ1–EQ4：当前规范，2026-09-08

发布导航：[中文审阅指南](49-eval-audit/REVIEW_GUIDE.md)与[选定审计依据](49-eval-audit/review-evidence/20260908/README.md)。

本文件是唯一研究问题入口；实施、图、矩阵与门禁分别链接维护。基线为当前工作树 `eval/eq1-eq4-implementation@254d65a66279fbaffc5c185d04fbe41dc8dbba44`，并记录本次文档 diff。此次授权为安全迁移、关键审计、文档重规划和最小检查；不包含完整矩阵、ABI升级或候选分支整合。历史原始结果、失败和provenance不改写。

## 研究问题与旧新映射

| 当前问题 | 定义 | 原问题/图去向 |
|---|---|---|
| EQ1 Mechanism-grounded fidelity | 在明确支持域内，额外服务时间如何经GPU overlap、同步与争用变为可观察性能影响？ | 旧EQ1-A + 旧EQ2；旧fig-e1 GPU与fig-e2重组为M1–M4 |
| EQ2 Thermal-feedback necessity | 在有物理参数依据的配置与持续负载下，忽略热反馈会误估多少性能/可持续边界？ | 新增T1吞吐时间曲线、T2温度时间曲线；不预设一定存在差异 |
| EQ3 Capacity and allocation feasibility | HBM:HBF物理容量比及HBM中KV/cache/staging预算如何改变有效驻留与可行域？ | 旧EQ3器件域保留，fig-e3拆为C1物理容量、C2内部分配 |
| EQ4 Workload-dependent applicability | 稀疏性、实际serving concurrency、dense匹配与预取如何移动上述边界？ | 旧EQ4及fig-e4/e5保留为W1/W2；fig-e6为附录敏感性 |

旧EQ1-B取消作为主文独立“物理真实性”论证。MQSim数值/守恒/回归、SSD路径标定及文献边界留在方法/附录；没有可核验堆叠器件测量，不能用SSD一致性验证物理HBF。OCP HBF v0.7.0规范已存在并已取得；规范的协议/架构约束与硅片实测分开。不同来源的TSV几何、XL-FLASH低延迟和HBF目标带宽必须分独立profile，组合只能标PROJECTED并给不确定区间。

## 当前能力与证据范围

当前树已有受限scalar timing future、库存/预算、路由采集与因果trace replay；旧“全部缺失”的2026-09-05描述已过期。scalar future不等于capacity/TMA；shadow native completion不保留所有真实scoreboard/MSHR生命周期。历史C6 timing样本仍CAPTURED_UNVALIDATED，不能关闭fidelity。当前树无package thermal runtime开关；独立prototype有controller闭环证据，其stage=off仍算温度，不符合新off臂。当前GPU可访问不等于GPU语义通过。

证据详情：[当前状态与复用](49-eval-audit/current-state-20260908.md)、[EQ1代码审计](49-eval-audit/review-evidence/20260908/eq1-audit.md)、[热历史审计](49-eval-audit/review-evidence/20260908/thermal-audit.md)、[当前外部来源](49-eval-audit/review-evidence/20260908/source-research.md)。这些审计绑定当前文件或独立历史SHA，不能通过更新文档变成新测量。

## EQ1：两个完成延迟与增量等待

同一issue原点：`L0=native总完成延迟`，`L1=目标总完成延迟=L0+D`（D为额外延迟，先限制L1≥L0），`W=真正可重叠的独立工作`。主oracle是：

```
S0 = max(0,L0-W)
S1 = max(0,L1-W)
DeltaS = S1-S0
W >= L1          => 0
L0 <= W < L1     => L1-W
W < L0 <= L1     => L1-L0
```

L0=.5us、L1=5us、W=6/2/.2us对应0/3/4.5us，是解析单元例子。旧`[D-W]+`仅当D定义为同原点的总服务时间且native开销另行处理时成立。分别输出issue_block、consume_residual、total_exposed_stall、native/instrumented wall time，不能以consume residual小证明正确。保留native操作时完成门概念上为`max(native_done,modeled_done)`；capacity fill后的native尾延迟、地址变换、staging单列，避免双计。

主比较臂native/instrumented-zero/旧issue-stall负对照/candidate deferred。以原生pointer chase与load→独立ALU→consume、warm/cold、MLP/occupancy为锚点；最终SASS需证实未提前消费/消除load。mapped pinned host memory只作平台允许时的独立慢LDG锚点，不等同HBF、不推广TMA/atomic。独立producer/consumer readiness oracle不得共用待测timing helper。Accel-Sim v2.0.0已发布Hopper/TMA支持，SM120不在已发布支持域；因此本机独立周期交叉验证为BLOCKED_OPTIONAL，不能用H100/B200校准替代。

TMA逐项保存G→S phase/arrival/expected bytes/try_wait/test_wait、多warp/multicast；S→G source-read-done与destination-visible分开，read wait后覆盖shared源并检查目标，wait不代表NAND持久化；group N=1/0及phase复用。TensorMap覆盖host/device更新、generation/fence、运行时coords/stride/OOB/mixed-tier区间；opaque布局未验证不解码。capacity TMA要整tile stage/pin和eviction寿命；multicast分别计source fetch、sector、destination bytes。GNIC/GPCARB专利仅说明可能资源，不硬编码内部通道数。predicate/CFG/loop/address dependency/vector/store/atomic/fence/timeout逐项支持或明确拒绝。

D候选0/.5/1/2/5/10/20us；W按实测L0、L1两侧选点，先单访问，再正交MLP/QD=1/2/4/8/16/32与低/中/高occupancy，最后兼容double-buffer GEMM/MoE代表kernel与强制串行负对照。time_scale=1优先；仅缩放D不能把wall time除100。记录寄存器/spill/shared、SASS、访问/sector、频率温度和时钟域，诊断profiling与计时run分离。pilot先确定噪声、最小可分辨D、绝对误差阈值及重复数，再冻结held-out条件；高并发误差不能解释则收窄域。

## EQ2：闭环及持续性能

真正off关闭热子系统；shadow计算温度/状态但不反馈；active同模型施加明确反馈。off–shadow定位计算开销/路径差异，shadow–active定位温度反馈。相同source/env、初温、geometry/material/cooling、原始需求、seed、容量和media参数；live反馈改变后续arrival属于机制结果，固定arrival replay与依赖闭环LLM分开。

必须追完整链：request/admission→实际media commands→energy/power→layer hotspot→policy/refresh→后续service/activity→power。gate禁止admission时仍推进模型时间和冷却；只改结果带宽或CUDA sleep不算闭环。刷新必须产生队列/完成/能耗，retention counter不等同refresh执行。当前prototype可支持controller反馈，不支持live refresh/read-retry链。

先冻结nominal资料支持区间，另设test-only stress；未触限负对照/边界附近/合理域持续负载，8Hi/16Hi切片。不能为分开曲线降低阈值。ROM慢模态决定时长，以5τ+统计窗口为初始要求，核对温度斜率、queue漂移和served/offered守恒。旧4s/8s仅绑定其ROM，旧64.5W与100MB/s源于test-only per-command能耗，禁止线性放大成产品TB/s。GPU/HBM与HBF能量独立记账，无可靠tR(T)不发明函数。

T1画完成的有用bytes/window（或应用计时门过后tokens/s），T2用同模型时间轴画hotspot；off没有温度，不伪造平线，用shadow作无反馈温度反事实。窗长/单位、threshold/hysteresis、触限与恢复、Tmax/duty/power/refresh/能耗与不确定性见[图契约](49-eval-audit/figure-plan.md)。若合理域无差异，报告负结果并说明适用域。

## EQ3：分开两个比例

物理`r=C_HBF/C_HBM`，HBM:HBF=1:r，r=1/2/3/4/6/8/12/16/24/32；全HBM另列可容纳实测或理论对照。固定total和固定HBM分别扫描。capacity-only保持tR/资源/接口；physically coupled变化die/channel/power必须逐项有依据。未使用新增容量可无性能变化。

HBM预算：`C_HBM=C_fixed+C_KV+C_HBF_cache+C_staging+C_other`；fixed列清不可交换权重/workspace/safety，eligible常驻分子分母避免重复。C_pool=C_HBM-C_fixed；kappa=C_KV/C_pool，beta=C_staging/C_pool。kappa=0:.1:.8；beta=0/.01/.02/.04/.08/.12/.16/.24/.32，只跑预算单纯形、真实KV下限及tile/stage/pin/alignment可行点。beta=0无合法实现记INFEASIBLE；reservation标RESERVATION_ABLATION，与真实context/active sequences形成KV的应用实验分开。HBM staging和HBF内部buffer不混称。

当前budget工具可报告未裁剪rho=effective/eligible和packing achieved_rho，尚无独立staging/reservation参数；新三预算性能扫描BLOCKED，不能把workspace参数换名伪装实现。tR=1/2/4/5/10/20us、N=256/1024/1536/3000/4883/15000保留候选，每N需独立sense单位来源和可行几何；不可映射只作ANALYTICAL。先慢/中/快已冻结profile做两个预算扫描，在5/10/20%边界附近事先规定加密，探索与确认点分离，避免全笛卡尔积。service_gbs来自完成账本，附HBF bytes/token、hit/miss、read amplification、queue/utilization、actual active sequences/decode。先固定thermal，再在边界切片加active。

## EQ4：真实工作负载机制

real/shuffled/uniform-null按layer/step及实际B、E、k对齐：`U_null(B)/E=1-(1-k/E)^B`仅是独立均匀、每sequence选k个不同专家的零假设。记录union、frequency/entropy/Gini、Jaccard/reuse distance、expert/HBF bytes/token、hit、queue与decode step time，检验并发→工作集→流量→等待，不预设batch越大越差。

已有prefetch replay三臂的真实含义：`none`逐miss串行，`on_demand`发出该batch所有miss再等待，`one_layer_ahead`只用先前已观察路由预测。前两者同时改变需求并发，不能把差异叫纯预取收益；纯预取比较必须匹配同一demand并发。FIFO/LRU/CLOCK为replacement，非prefetch。记录useful/late/useless、额外流量、源寿命/pinning、队列/热影响。

dense分别capacity-matched和active-compute-matched，精度/结构/计算量差异显式；只用已有且核验的模型，未有dense匹配不下载填图。TRACE_COMPOSED不是live serving，固定token串行捕获也不是实际concurrency；真实LLM性能依赖EQ1门禁。

## 依赖、交付与停止点

[执行计划](49-eval-audit/execution-plan.md) · [机器可读矩阵](49-eval-audit/run-matrix.csv) · [claim gates](49-eval-audit/claim-gates.md) · [结果契约](49-eval-audit/result-schema.md) · [reviewer-response](49-eval-audit/reviewer-response-matrix.md) · [实施缺口](49-eval-audit/minimal-followups.md)。thermal device-only replay可独立于GPU EQ1；thermal LLM和EQ3/4 live必须通过相应语义计时门禁。文件迁移实际状态以[迁移报告](49-eval-audit/review-evidence/20260908/migration-report.md)和manifest为准。

[旧规范原字节](49-eval-audit/history/20260905/README.md)及六张MOCK布局保留历史用途，旧renderer尚未实现新T1/T2/C1/C2扩展；不得把旧图输出叫新EQ完成。完整矩阵等待独立评审与执行授权，本轮CPU/静态/历史hash检查不能替代物理测量。
