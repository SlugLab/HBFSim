# 参数缺口、可推演假设与结论边界

2026-09-19；与parameter_registry.json配套。用户已要求证据不足时继续搜集资料、
提出假设并推演合理性，再审核；这不批准任何新的实验负载。

## 已完成的检索边界

本地OCP原PDF、HeatWatch原文、MFIT固定提交材料/几何、HBM-Power原CSV及单位
生成脚本；定向取得Sandisk原PDF、Micron网页快照、3D-ICE论文PDF、Henkel
HC5000原TDS。原件哈希见账本/来源审核；不运行第三方测量或云脚本。
JEDEC官方HBM4页面此次未能取得；Samsung封装网页定向请求40s超时。
这些失败不说明规格不存在，也不允许用二手转述填成官方精确值。

## 逐项处置

| 缺口 | 当前可用事实/假设和推演 | 最小闭合或降级方案 | 阻塞范围 |
|---|---|---|---|
| 实际HBM4/HBF内部几何 | OCP近似10.975×16×0.775mm外包络；主线12×16mm分层热源是显式扩大代理，内部die/bond/base分解自洽 | 本轮固定候选；后续取得package drawing/截面/stack height定义后替换。至少测外尺寸、各die/bond厚度、顶面高度与接触方式 | 实物绝对结温与逐die真实梯度；不阻塞条件性数值参考 |
| 微凸点、TSV/CBA有效k与rho/cp | 采用3D-ICE论文anisotropic bump和MFIT Si；CBA层并不等于硅块+普通bump | 请求厂家截面/体积分数；或同结构热测试coupon。若无，保留材料代理情景，分别报告路径热阻，不能把“类似HBM”变成真值 | HBF物理标定；允许研究结构下结果 |
| TIM/接触 | 厚度由公共平面推导；k=10是MFIT案例代理，论文另有k=5作为将来独立材料情景。HC5000 TDS阻抗含界面且厚1.016mm，不适配本结构 | 最小coupon测两种厚度、两档压力的热阻；以斜率求bulk、截距求双面contact，记录面粗糙度。当前残余contact=0仅理想对照 | 实物冷却预测；不把whole-package R叠加到每条edge |
| 冷却/底部损失 | 顶/底HTC1400/25来自MFIT案例，不是机房冷板。静态C/G≈3.625s只作量级 | 固定profile可先数值核验；实物需独立源阶跃、冷板入口/出口与环境、功率域、空间温度和冷却尾段至少各一条，另留未拟合幅值 | 平台实物温限；不阻塞参考模型 |
| HBF绝对能量 | Sandisk没有绝对J/byte。HBM3E公开三点45.815–46.941pJ/delivered-B；在1.6TB/s仅动态项73.30–75.11W，可用来暴露10W满速假设的证据不足，但**不是HBF区间** | 当前数值激励采用每栈array64/base16W上限，不等于80W产品规格；未来可做“相同aggregate energy/byte”的代理边界情景，单列转用。目标至少idle+3读率+program/erase/maintenance计量，rail归属/物理字节清楚且留一负载验证 | 活动→功耗、绝对能量收益、实际维护热反馈 |
| array/base/PHY分账 | H200是aggregate memory rail，无公开拆分；64/16只是可独立激励的研究幅值 | 不再把aggregate能量加到array后又重复加PHY。实物需rail隔离或可辨识操作差分；暂报告总能量和明确分配假设 | 局部热点/relay发热的实物解释 |
| HBM4命令组织/时序/刷新/容量字节 | 产品页仅36GB12H、2048bit和严格大于速率；选择8Gbps显式研究点，不声称verified bin | 获取具体part datasheet/JESD270-4原文；未取得则B不消费地址字节/服务时序。C只能另批简化服务模型，不能称已标定HBM4 | 容量驻留、HBM服务/延迟/token；不阻塞给定热源B |
| GPU型号/端口面积 | 使用custom compute slab；不是5090/GDDR移植。4HBM合计8192data bits；HBF PHY/端口/面积未闭合 | 分别预算HBM PHY与HBF接口、面积、lane和共享供给；无设计数据则“逻辑可表达、物理可实现性未验证” | 声称实际GPU八栈兼容/拓扑纯收益 |
| Ea与保持 | HeatWatch旧charge-trap MLC 1.04eV、CI1.01–1.08，只适用原实验20–70C/1k–10kPE；85/105C属温区外推，更非Sandisk | 允许后续独立代理profile固定Ea集合[1.01,1.04,1.08]，这是旧拟合区间不是目标HBF uncertainty interval。目标最小3温度×2磨损态×2年龄点并留完整温度轨迹，才谈识别Ea/磨损；无raw则条件年龄模型，不做失效率 | 实际retention/ECC/lifetime，不阻塞热响应 |
| OCP与Sandisk维护语义 | OCP典型24–48h+同die互斥，Sandisk说no-refresh-power，资料范围冲突保留 | 主线C建议OCP式维护+Sandisk组织的组合研究假设，等待明确确认；另一边界“不定期维护”单独profile，不混用 | 维护争用结论必须先选语义 |
| ECC/retry/endurance/read-disturb | 无目标RBER/ECC/重试耗时/PE极限 | P4先只实现已定义维护请求/占用/能量/写量；相应read-retry保持NOT_IMPLEMENTED，报告实际program/erase数而非寿命年数 | 错误概率、延寿年限和ECC收益 |
| HBM/GPU温限与控制阈值 | HBF0–105C是规范域，85C是24hpowered条件；HBM/GPU对应温限未取得 | 获取对应datasheet，或另批明确研究限值；Light=limit−误差−传感器不确定度−时延升温裕度。没有证据不填85C统一阈值 | Safe/Near/Stress正式点选、compute-die-first比例 |
| 五种策略/LLM | 本树无权威五策略集合；paper子模块未初始化。只有预取none/on_demand/one_layer_ahead与cacheCLOCK/LRU/Belady等，不能拼成热策略 | 需用户/Overleaf给出原五种名称。先设计无动作baseline+最简可审计温控，均P4未实现；LLM先恢复冻结trace，不启动新GPU采集 | 五策略矩阵、实测token性能；不阻塞B |

## 为什么现在不拟合所有未知值

同一温度轨迹中，C、导热、接触、HTC与输入功率常有可互相补偿的组合。
只有温度没有功率域/边界计量，拟合好并不能识别真实材料。B-v1因此不将这些
未知参数自由优化；直接构造物理网络，检验空间粗化误差。若将来取得实测，
只拟合由灵敏度/秩检查确认可辨识的少数有效项，且整个未拟合轨迹用于验证。

## 闭环前不能遗漏的接口缺口

当前请求观察不含NAND命令/die/plane/活动区间；控制/可靠性JSON没有执行消费者。
P3需要最小因果活动接口及能量去重，P4维护要有真实模拟资源、失败/在途语义、
完成后年龄更新、耗能与磨损反馈。不得通过改变核心ABI/PTX/TMA/future/cache来
掩盖这些缺口；不能只加一个刷新计数器。非热核心消融逐项NOT_IMPLEMENTED。

### 2026-09-20 HBF ECC conditional-proxy update

USER_CONFIRMED: the device is **HBF**, not HBM; NAND/OCP/SanDisk conditional
proxies are permitted. Target HBF RBER, code strength, UECC probability and decoder
throughput remain unknown. The historical NOT_IMPLEMENTED entry above is retained
as history; an optional actual read-cost consumer now exists under
`experiments/eq3_system_thermal/ecc_proxy/README.md`.

`run_causal_point` consumes temperature-history age and a source-constrained but
assumed retry-effort interpolant through shared media/decoder resources and
activity energy. Fixed integration tests passed; thermal acceptance is separate.
It predicts conditional successful-read effort, not error probability. Initial
wear is a scenario. Per-stack age is not yet linked to maintained tensor extents,
so combined refresh→ECC improvement remains UNSUPPORTED_COMPOSITION. HBM is excluded.
No instantaneous temperature penalty or compulsory positive throttling benefit.
