# 最小后续补丁与独立任务，2026-09-08

下列新功能代码工作尚未实施；P-D2的历史generator防覆盖保护本轮已完成，剩余新矩阵执行adapter仍待实现。本轮仅迁移兼容、文档工具兼容和已记录最小检查。补丁以[主规范](../49-new-evaluation-plan.md)、[execution](execution-plan.md)、[gates](claim-gates.md)为验收，不靠扩大声明关闭缺口；不绑定整体merge/ABI升级。默认行为和历史raw保持，完成一个补丁后才扩大依赖实验域。

| Patch | 最小范围 / 当前真实触点 | 必要验证与交付 | 依赖 / 停止边界 |
|---|---|---|---|
| P-Q1 双L oracle与结果字段 | 新EQ1分析器/向后兼容schema，区分total/extraD、L0/L1/W、S0/S1/DeltaS、issue/consume/total、时钟域 | 附件三例、missing-pair/unit/clock负测、L1<L0拒绝或独立模型；原旧总服务oracle仍按其域解释 | 不改runtime ABI；数学PASS不关GPU门 |
| P-Q2 实际W与低扰动计时 | `run_c6_future_delay_overlap.py`及隔离benchmark，固定K诊断扩为实际不同长度独立序列 | optimized SASS真实LD/首use；三段deadline位置、native/zero/old/deferred；observer开销单列 | 先证明W差异再扩D；不靠放大D掩盖prework问题 |
| P-Q3 native/MLP锚点 | 新小native device/mapped-host（平台许可时）LDG与MLP/occupancy基准；复用`audit_sass_mapping.py` | held-out W/QD/MLP、cache/sector/register/spill、noise/pairedCI；不支持慢路径即停可选项 | 若SASS证明rawbits提前消费才另做最小依赖位置修复；不预设该bug已发生 |
| P-Q4 async MQSim future桥 | 从当前fast scalar future增独立bounded request→host completion→residual门设计 | 保留已集成queue-depth admission修复，wrong ABI/generation/finite trace/timeout/daemon-loss/queue-full守恒 | 当前路径尚不存在；先fast scalar门，不能只拿donor几个helper或整体thermal merge |
| P-Q5 TMA TIMING窄子集 | donor作为代码参考；动态group和所有poll/wait联合gate逐patch实现 | G→S phase/bytes/try_wait/test_wait、多warp；S→G read-reuse/write-visible；groupN0/1、合法multicast、故障 | 未证明CFG/descriptor仍拒绝；不宣称精确SYNCS内部状态或NAND持久化 |
| P-Q6 TensorMap与capacity寿命 | descriptor generation/fence/range解析和整tile stage/pin/evict分两次审查 | A→B/多range/page/OOB/mixed tier、source/destination寿命、native尾延迟/copy账本 | TIMING与capacity分验；不解码不稳定opaque layout |
| P-T1 真off工具 | P@49f9b2独立`phase2_thermal_load_runner`可选thermal对象/media sink；off不构造ROM/采样，arrival/MQSim/queue路径相同 | 无效ROM下off可跑、shadow失败；无触限off/shadow完成序列同；独立host CPU开销 | 历史stage=off仍原字节；不拼daemon不同路径，不升级全局ABI |
| P-T2 useful完成时间序列 | P runner/service logger添加request完成时useful bytes/bin，保留offered/admitted/pending/physical traffic | 10ms原始bins/100ms图窗的完成守恒，尾窗/整窗区分；read/program/refresh/retry另账 | 缺此不画T1正式吞吐；不能拿media activity bytes替换 |
| P-T3 refresh因果adapter | 背景需求→MQSim真实read/program→完成→寿命/PEC→未来power，与controller可拆 | QD饱和、失败/重试、gate冷却、能量不双计、刷新完成前不更新寿命 | retention counter不等于刷新，当前live refresh链BLOCKED |
| P-T4 nominal输入与数值域 | 独立profile/ROM/source ledger：geometry/material/cooling/energy/阈值区间、GPU/HBM/HBF能量独立 | OCP/3D-ICE资料字段对应、held-out/parameter sensitivity、tau/clock/bin；nominal/stress明确 | 协议规范非硅片校准，无可信tR(T)不发明；旧test-only功率不外推TB/s |
| P-T5 当前live热组合 | 热核心与平台lifecycle隔离；先C thermal-off parity，再窄service接口设计 | 当前source/build/env同，GPU first-consumer/G5/NQ2/G9、actualapp timing、daemon/queue/fault | 不直接merge无共同祖先P/F；门未过只device-only |
| P-T6 transient时间映射 | workload trace time→ROM step/bin有来源的映射/能量守恒 | 解决50.021us trace/10msROM失配，记录采样/聚合误差与held-out | 宏观PROJECTED envelope不解除真实transient BLOCKED |
| P-C1 physical-r驱动 | 实验端显式C_HBM/C_HBF、fixed-total/fixed-HBM、capacity-only/coupled字段与合法profile映射 | r与rho区别、unused容量负对照、sense/N/geometry来源，非法容量拒绝 | 不把旧ratio直接重名；不映射N仅ANALYTICAL |
| P-C2 KV/cache/staging预算 | `budget_fast_tier.py`及replay参数新增独立staging/reservation；C_fixed/KV/cache/staging/other完整分配 | 单纯形、真实KV/tile/stage/pin/alignment、beta0合法性、requested/achieved packing、无重复扣 | workspace参数不得冒充staging实现；reservation标RESERVATION_ABLATION |
| P-W1 matched-demand预取 | `run_prefetch.py`/`prefetch_replay.py`显式demand并发与speculation因素分开，保留三旧policy语义 | on_demand与one_layer_ahead匹配需求并发、useful/late/useless/traffic/pin/queue，预测只用过去 | none→on_demand变化不是纯预取；旧结果标签不改原数值 |
| P-W2 dense与实际concurrency | 完善已有inventory/capture的两类dense匹配和actual scheduler身份 | actualB/E/k/layer/step、成员seed、dtype/compute/capacity偏差，live/trace-composed分域 | 不下载模型填图；固定token/trace-horizon不等同live timing |
| P-W3 runtime预取 | 单独实现demand/prefetch submit→ready→consume→evict/pin，明确容量/热接口 | queue-full/wrap/copy failure/dirty rollback、source寿命、extra流量/energy、同当前tree parity | 等G5/NQ2及G10；offline预测不当runtime收益 |
| P-D1 新schema与renderer | `validate_results.py`/`render_figures.py`添加向后兼容evidence_kind/validation_scope和M/T/C/W字段/panel | 缺配对/单位/hash/unknown、MOCK、单次CI、无crossing、不合法预算/缺thermal臂负测；图artifact QA | 旧six-figure renderer通过不关新图；当前BLOCKED_RENDERER |
| P-D2 新矩阵adapter/注册门 | 旧generator/runner只读其冻结历史matrix；新schema registry显式接入，保留REPLAN_REVIEWED_REGISTRY阻断 | 旧runner不能误读新行或覆盖canonical；每cell真实argv/dependency/source hash；明确执行授权 | 本轮已令旧generator必须显式--output且拒绝覆盖已有文件，并将旧数量断言绑定history；新自动执行adapter未实现，完整队列未启动 |

每补丁提交源码diff、实际测试日志及scope、失败负例、更新file/function/line/SHA ledger和对应gate receipt；相邻补丁不捆绑通过。测试选择针对行为改变与风险，不能用mock/编译/进程PID/旧SOF式“存在性”替代语义或物理证据。大规模模型/仿真器安装、driver/CUDA/全局Conda调整不作为隐藏前提。
