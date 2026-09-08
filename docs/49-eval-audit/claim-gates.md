# Claim gates — current contract, 2026-09-08

[主规范](../49-new-evaluation-plan.md)是唯一问题定义。保留G0–G11编号供历史工件兼容；历史PASS只绑定原SHA/profile/input/mode/validation_scope，不能自动关闭新EQ。旧阈值见[原字节历史表](history/20260905/claim-gates.md)。新EQ1先经pilot冻结绝对误差与held-out，不把旧[D-W]+当额外stall oracle。

| Gate | 证据与验收 | 当前边界 / 失败措辞 |
|---|---|---|
| G0 snapshot | source/build/dependency/env/profile/model/trace/renderer哈希、dirty diff、单位/时钟域、迁移路径与mount guard | 当前基线254d65a；缺精确绑定记UNBOUND；迁移以manifest为准 |
| G1 device/resource | GPU UUID/driver/实际频率温度、backing设备/UUID/filesystem/cache、资源独占与空间低水位 | GPU可访问不是语义PASS；新PM1733 backing的capacity wall与旧路径分域；污染记CONTAMINATED |
| G2 known delay | 保留legacy issue-stall检查；新EQ1另需NQ1/NQ2，native/zero/control、checksum、分段stamp、Event/host wall | 历史C6 CAPTURED_UNVALIDATED及不可辨W样本不能关新fidelity；近零用绝对误差 |
| G3 storage scope | 同request/arrival/bytes、预冻结calibration/held-out、cache/preconditioning、完成账本 | SSD/MQSim一致仅相应设备/数值域，不是物理HBF；calibration不是held-out |
| G4 differential | T0/THW/TSIM配对、same content/coverage/backing、信号超过噪声后算相对误差，至少10独立paired runs | 缺三臂只报组件证据；模拟器一致非物理ground truth |
| G5 async scope | 支持op/CFG的first-executed-consumer与native/model/data-ready、optimized SASS、checksum/transaction/timeout/daemon-loss负测 | 本轮CPU19/19仅受限emitter工程；TMA/TensorMap/capacity future未接通，一般CFG/cp.async仍拒绝 |
| G6 service/concurrency | admission/issued/completed/pending守恒、actualQD、多outstanding、完成bytes/window、N映射和compute依赖 | 现有因果replay仅其模型域；serial sum非live wall，interface ceiling非actual service |
| G7 residency | inventory/KV/workspace/非offload权重、eligible分母、requested/achieved rho、NC1/NC2 | 当前budget工具存在，新staging/reservation轴未实现；负预算INFEASIBLE |
| G8 model/dense | checkpoint/config/tensor SHA、dtype/E/k/layers、capacity/active-compute两种匹配及偏差 | inventory不是dense匹配完成；未控制精度/架构差异不归因纯稀疏性 |
| G9 route/serving | layer/step actual IDs/B、real/shuffled/null、seed与trace成员 | 当前capture/replay有入口；TRACE_COMPOSED或固定token单序列不是live/实际并发 |
| G10 prefetch | 同demand并发、speculation submit/complete/consume/evict、useful/late/useless、extra bytes/pin/queue/thermal、无未来泄漏 | none串行和on_demand批发夹带需求并发差异；FIFO/LRU/CLOCK是replacement |
| G11 publication | strict无MOCK、配对/单位/context完整、raw manifest、对应claim门、图代码hash/人工核查 | 旧renderer通过不表示新T1/T2/C1/C2实现；新图BLOCKED_RENDERER |

## 新EQ1门

| Gate | 验收 | 限制 |
|---|---|---|
| NQ1 dual latency | 同issue原点L0/L1/W；extra D满足L1=L0+D；S0=[L0-W]+、S1=[L1-W]+、DeltaS=S1-S0；三种区间，issue_block/consume_residual/total_exposed/native-zero wall分别记账 | 本轮0/3/4.5us只数学核对；L1<L0不在简单正delay注入域 |
| NQ2 identifiable timing | SASS实际LD/首use，actualW跨L0/L1；native/zero/old/deferred配对，pilot冻结noise floor/最小D/绝对误差/重复数和held-out | D在全部W前过期、K变而W不变为NON_IDENTIFYING；不能靠增D/换图通过 |
| NQ3 concurrency | held-out MLP/QD/occupancy/代表pipeline；register/spill/shared/访问/sector/queue/host publication/helper扰动 | 软件future不延长所有native MSHR/scoreboard占用；未知偏差缩窄claim，不称周期精确 |
| NQ4 TMA events | G→S全try_wait/test_wait/blocking联合native/model/data门，phase/arrival/bytes；S→G read-done/write-visible分别，dynamic group/source reuse/multicast/descriptor范围与寿命 | parser/单wait无效；无tile stage/pin/evict不能claim capacity TMA；不等于NAND持久化 |

## 新EQ2门

| Gate | 验收 | 当前状态 / 收窄 |
|---|---|---|
| NT0 arm identity | 真off关闭热子系统；shadow同热计算不反馈；active同模型反馈；同initial T/geometry/cooling/media/需求/seed | 历史stage=off仍算温度，是no-feedback候选；真off/current package接口BLOCKED，不重标历史文件 |
| NT1 numerical domain | geometry/material/cooling、energy/profile/ROM SHA、参数来源区间、3D-ICE或实验held-out数值交叉验证、可复核tau | OCP v0.7.0协议/refresh/thermal backpressure不是硅片功耗校准；test-only/ROM回归仅其数值域 |
| NT2 closed feedback | request/admission→实际media commands→energy→hotspot→policy/refresh→后续service/activity→power；禁admission仍推进时间冷却 | prototype controller可在冻结域复用；retention counter非refresh执行，refresh排队/完成/能耗反馈未闭合 |
| NT3 sustained regime | nominal/stress分开；未触限负对照/近边界/合理持续负载、8Hi/16Hi；至少5tau+统计窗，温度斜率/queue漂移/served-offered守恒 | pilot预算不够热时长则BLOCKED_BUDGET，不假稳态；合理域无差异保留，不降低threshold造差异 |
| NT4 application attribution | off–shadow定位计算开销，shadow–active定位反馈；固定arrival device replay与依赖闭环LLM分开；LLM需G5/NQ2/G9 | 热device-only可独立GPU推进；tokens/s/live不能越门，无可靠tR(T)不发明 |

## 新EQ3门

| Gate | 验收 | 当前状态 / 收窄 |
|---|---|---|
| NC1 physical capacity | r=C_HBF/C_HBM，HBM:HBF=1:r；fixed-total与fixed-HBM分开；capacity-only保留tR/资源/接口，coupled有die/channel/power来源 | 新容量没被使用而性能不变是有效结果；r/地址跨度不是已访问bytes |
| NC2 allocation simplex | C_HBM=C_fixed+C_KV+C_HBF_cache+C_staging+C_other；C_pool=C_HBM-C_fixed；kappa=C_KV/C_pool，beta=C_staging/C_pool；不重复扣且满足KV/tile/stage/pin/alignment | 当前budget缺独立staging/reservation；新扫描BLOCKED；beta0非法INFEASIBLE；reservation标RESERVATION_ABLATION |
| NC3 boundary | N每点sense/几何来源，完成service_gbs、bytes/token/放大/hit/queue/actualB；预注册5/10/20%探索/确认切片，先固定thermal后active | 不可映射N仅ANALYTICAL/PROJECTED；无crossing不画contour；不得混profile/热域 |

## 共用失败与发表规则

eligible dynamic bytes、instrumented/native/bypass/opaque/unknown分账；unknown不填零，unsupported=0不是100%动态覆盖。未覆盖路径只阻塞相应主张，支持子集继续。checksum/stale/early/missing/timeout/daemon-loss失败保留，不转零等待成功。

边界移动需事前5/10/20% slowdown、paired-bootstrap区间与网格分辨率。无replicates只报条件预测，一次不造CI；同warp lanes/层/请求不是独立重复。CI不消除HBF参数不确定性；无crossing写扫描范围未观察到。time_scale≠1须相似性说明，只缩放D不能总wall除scale。

禁止：GDDR7验证HBM/HBF、SSD为HBF ground truth、CPU19pass等于GPU通过、PTX位置等于精确scoreboard、所有TMA/TensorMap已支持、ROM验证等于物理HBF温度验证、所有场景热反馈必需、test-only64.5W/100MB/s线性外推产品TB/s、replay等于live、none→on_demand是纯预取收益、缺配对/来源的图叫新EQ完成。

## 矩阵语义alias与注册门

这些alias只给机器矩阵提供名称映射，不产生新的PASS，也不改变历史G号。

| matrix blocking_gate | 本表对应 |
|---|---|
| NC-TOPOLOGY | NC1及NC3的sense/N/geometry映射 |
| NC-BUDGET | G7与NC2 |
| NC-STAGING | NC2的staging/pin/tile合法性与接口 |
| NT-PAIR | NT0、NT1及NT4的配对归因 |
| EQ2_TRUE_OFF_RUNNER | NT0，P-T1 |
| EQ2_PHYSICAL_INPUTS | NT1/NT3，P-T4 |
| EQ2_COMPLETION_BIN_ACCOUNTING | NT2/G6，P-T2 |
| EQ1_APP_TIMING / THERMAL_COMBINATION_PARITY | G5/NQ2/G9及NT4，P-T5 |
| REAL_TRANSIENT_TIME_MAPPING | NT1/NT4，P-T6 |
| REFRESH_DISPATCH_COMPLETION_ENERGY | NT2，P-T3 |
| Q1-CPU / Q1-ORACLE | G5的CPU子集 / NQ1数学子集，均不代替GPU门 |
| Q1-STRUCTURE / Q1-LIFECYCLE / Q1-OVERLAP | G5/NQ2结构 / G5生命周期 / NQ1与NQ2 |
| Q1-TMA-GS / Q1-TMA-SG | NQ4各自方向 |
| migration_mount_and_space_guard / isolated_single_CPU_no_heavy_copy / verify_binary_and_environment_again_before_run | G0/G1，执行前当前状态核验 |
| REPLAN_REVIEWED_REGISTRY | 新矩阵是待审规划registry，不是自动启动队列；阻止旧注册器直接执行，P-D2后仍需明确执行授权 |
