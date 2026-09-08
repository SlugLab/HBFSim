# Reviewer response matrix，2026-09-08

本表把质疑绑定机制、入口、观测、验收与限制；定义以[主规范](../49-new-evaluation-plan.md)为准，精确文件/函数/行段/SHA见[EQ1](review-evidence/20260908/eq1-audit.md)和[热审计](review-evidence/20260908/thermal-audit.md)。这是待执行的回应合同，不是已完成实验答辩；参数/initial/重复/预算见[execution](execution-plan.md)，状态见[matrix](run-matrix.csv)。

| 质疑 | 机制 / 真实入口或BLOCKED | 实验与observable | acceptance | 限制 / 当前证据 |
|---|---|---|---|---|
| PTX不是SASS，LDG可以overlap | 当前scalar emitter + `audit_sass_mapping.py` | native LD→actual独立ALU→首SASS use；native/zero/old/deferred | NQ2：首use/实际W、register/spill/访问存活 | PTX虚拟依赖不是物理scoreboard；CPU19/19不证明GPU时序 |
| `[D-W]+`忽略native延迟 | 新分析器字段补丁BLOCKED；数学例已有 | L0/L1/W同原点，S0/S1/DeltaS及三种区间 | NQ1：.5/5us与W6/2/.2给0/3/4.5us | 数学检查非实测；extraD/total标签必须明确 |
| issue已付款后残余小伪装正确 | `run_c6_future_delay_overlap.py`窄诊断存在 | issue_block、consume residual、total exposed、native/zero wall分账 | NQ2：真实W差异及deadline三种位置 | 历史20us在W前过期且K差异未变W，NON_IDENTIFYING；不能只增D |
| 提前native完成释放MSHR/scoreboard | MLP/occupancy/slow-path harness BLOCKED | held-out QD/MLP/occupancy、SASS/cache/sector/queue/admission/helper overhead | NQ3：偏差域可解释；否则收窄 | 软件spin/future非硬件memory stall，不称周期精确 |
| shadow gate漏过try_wait/test_wait | 当前TMA runtime BLOCKED | G→S全观察点、多warp、expect_tx/arrival/phase、多stage | NQ4：native/model/data-ready联合，zero early/stale/missing | 不改硬件不可见SYNCS；parser不是runtime |
| read wait不等于完整写入 | 当前S→G runtime BLOCKED | read-wait后覆盖shared源、独立目标可见性、N0/1动态组 | NQ4：source-read-done与write-visible按PTX范围分别验收 | 不等于全系统/NAND持久化；空commit本身不足证明所有直线代码错误 |
| TensorMap运行时更新/跨tier | generation/range/tile-lifetime补丁BLOCKED | host/device A→B/fence、coords/stride/OOB、多range/page、tile pin/evict | NQ4：descriptor gen/hash、实际bytes与checksum/寿命 | HBM/HBF同PTX global，不猜tier、不解码未经保证opaque布局 |
| multicast/internal channels未建模 | 合法cluster/fanout基准BLOCKED | source fetch、sector、fabric destination bytes分别，observedSM/CTA布局 | NQ3/NQ4：fanout/placement held-out域 | 专利说明可能路径，不证明固定GNIC2TEX/GPCARB条数 |
| 仿真一致仍可能错 | Accel-Sim v2.0.0 Hopper支持；SM120 BLOCKED_OPTIONAL | 少量native/service perturbation/瓶颈交叉验证，release/commit/arch覆盖冻结 | 真支持才执行，NQ3辅助证据 | H100/B200不替代SM120；模型一致不是物理测量 |
| 实际pipeline多stage复用可能过早 | double-buffer GEMM/MoE基准BLOCKED | realasync/forced-serial、stage/window、source/destination寿命、多outstanding | NQ4：先正确再性能 | 单LDG通过不外推TMA/完整LLM |
| 温度只是曲线，没反馈service | P@49f9b2 controller runner已有，当前package BLOCKED | request→command→energy→hotspot→policy→后续command；关admission仍冷却 | NT2/NT3：完成守恒、throttle/recovery与持续轨迹 | prototype窄域，retention counter非refresh执行；刷新队列/能耗链未闭合 |
| off仍算热，差值归因错 | 真off runner BLOCKED；旧stage=off保留原名 | 真off/shadow/active同初态/source/env/raw需求 | NT0/NT4：off–shadow计算开销，shadow–active反馈 | 不能重标旧工件；缺臂不能称完整三臂 |
| 调功耗/阈值制造热收益 | nominal物理输入BLOCKED，test-only历史可复用 | 未触限负对照/近边界/合理持续、8Hi/16Hi、tau | NT1/NT3：预注册来源区间/阈值，≥5tau+窗口 | OCP v0.7.0协议/refresh/backpressure非硅片功耗标定；无差异保留负结果 |
| offered/物理traffic冒充有用吞吐 | completion useful-bytes-bin exporter BLOCKED | 完成useful bytes/window，对照offered/admitted/media read/program/refresh/retry | NT2/G6：各账本守恒、T1窗口对齐 | 旧analyzer合计media物理bytes不能代T1 useful completion |
| 物理容量与HBM分配混淆 | `budget_fast_tier.py`已有；独立staging/reservation BLOCKED | r fixed-total/fixed-HBM，kappa/beta合法单纯形，KV/tile下限 | NC1/NC2：无重复扣、requested/achieved | beta0不合法INFEASIBLE；reservation ablation非实际应用KV |
| N/tR理论上限冒充器件域 | MQSim/replay组件已有，NC adapter待补 | sense/geometry来源、完成GB/s、bytes/放大/hit/queue | NC3/G6：不可映射N只解析 | TSV/XL-FLASH/OCP不能拼成已实测产品 |
| max_batch不是实际并发 | `hf_routing_runner.py`/`routing_metrics.py` | actualB/E/k逐layer/step、real/shuffled/null、union/reuse→traffic→wait | G8/G9：实际scheduler trace才能live | TRACE_COMPOSED不是live；uniform-null仅数学零假设，不预设大batch更差 |
| 预取夹带并发或漏流量 | `run_prefetch.py`/`prefetch_replay.py` | none逐miss串行、on_demand批miss、one_layer_ahead仅已知路由；matched demand并发 | G10：useful/late/useless/extra traffic/queue/pin/thermal守恒，无未来泄漏 | none→on_demand不叫纯预取；FIFO/LRU/CLOCK是replacement |
| dense差异没控制 | inventory已有，matched evidence BLOCKED | capacity-matched与active-compute-matched分别报告dtype/结构/compute偏差 | G8：规则冻结、不隐藏偏差 | 不下载补图，不把未控制差异归因纯稀疏性 |
| 迁移换backing，wall不可比 | migration manifest/G1 | logical/physical/backing UUID/filesystem/cache与model/wall分域 | 同设备/cache或标不同实验域 | PM1733 backing不是MQSim/HBF目标；根盘空间以实际df变化 |
| 改图/声明把缺口写PASS | 新renderer BLOCKED | 新字段/配对/无MOCK、raw/source/model/renderer hash链 | G11：无crossing无contour，单次无CI | 文档/CPU smoke不是完整EQ矩阵或物理HBF实测 |

device-only热replay可独立GPU EQ1推进；thermal LLM和EQ3/EQ4 live需相应GPU门。失败保留且只阻塞依赖，完整矩阵本轮不启动。实现缺口见[minimal followups](minimal-followups.md)。
