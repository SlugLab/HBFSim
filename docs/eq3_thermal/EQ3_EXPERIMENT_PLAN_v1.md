# EQ3分阶段计划 v1

状态：设计提案，不是实验批准。正式可启动点数目前为0。
主线和B标定见MODEL_SCOPE.md及CALIBRATION_PREFLIGHT_v2.md；v1预检仅保留历史。

## A→B：先参数、再数值模型

A本轮已完成静态盘点、原件核查、逐项决策和候选逐die数据；高影响实物未知不
填假值。两次冻结规则已追加至两级AGENTS，保留旧规则。
B首轮7参考+3快速，10点/856模拟秒，非总次数上限；新几何转换器和执行版审批尚缺。
后续按原件、推导、逐次指标与可辨识性审核，记录每点和累计成本；达到目标或
科学/安全停止条件才停，不因首轮点数用完停。扩大科学范围仍需更新预检确认。
先无自由拟合构造RC；若新几何的当前引擎不能满足误差，保留失败，独立提出最小
空间细化，不预设必须ROM或GPU求解。新盲测只在模型hash锁定后打开。

现有RC失败的只读诊断：tools/eq3_reference.py:157–192只有单个等温spreader，
空间局部散热自由度被消除；这是结构性风险，不是已定量证明的唯一误差来源。
source→spreader contact只写入RC，不写入3D-ICE；本次旧值0保持一致，但不能
靠修改该字段让新对照假公平。输出均值与grid热点不同；参考的面积平均与core
group算术平均在新不等厚die下必须重新映射。单位、时间、功率分配、能量平衡
继续各自检查，不能把时间步收敛解释为解决空间粗化误差。

## C：最小闭环能力与pilot

进入条件：B的MODEL_FREEZE；P3/P4默认关闭的最小适配及以下固定正确性测试
通过；功耗/维护必要参数闭合；具体pilot版本获批。不能由B批准自动启动C。

固定测试须证明：请求arrival/issue/complete因果；不重复能量；运行空闲时冷却；
同die读/维护互斥；后台维护真实占资源；失败/在途请求不消失；成功维护才重置
年龄；program/erase才增加对应磨损；热控制影响后续服务而非仅温度/计数器。
不得为了钩子整体改ABI/PTX/TMA/future/cache。

策略先按真实实现，不虚构五种：

| 对照 | 状态/语义 |
|---|---|
| 插件true-off | P1独立路径已有；不是“无控制器但热/维护仍开”的主baseline |
| 无控制动作 | 计划主baseline；热、可靠性、正常维护均开；P4尚未实现 |
| 最简滞回温控 | 计划可审计对照；Normal/Light/Severe/Shutdown状态、延迟/驻留与原因码；P4尚未实现 |
| 论文五种策略 | BLOCKED_IDENTITY：本树未找到权威集合，paper子模块未初始化，需原稿/用户给出 |

已有none/on_demand/one_layer_ahead是预取策略，CLOCK/LRU/Belady是cache策略；
sequential/random/strided/pointer_chase/mixed_rw是workload；都不能拼成五种热策略。

首次pilot提案：一个冻结合成因果请求workload、3场景×上述2个可用后策略=6配置，
每点1次、同arrival和初始年龄/磨损/缓存；目前因执行链未完，6个均NOT_IMPLEMENTED。
若需要独立Stress-refresh，另加1场景×2=2配置，需在pilot版明确确认。
未来真实token claim需因果LLM执行/trace模型，不能用固定到达率直接换算token/s。

## 场景点选规则：先可行性，后固定点

对每个冻结域，先解析稳态/可观测tau/执行器可控性：
`L*Tss=P+b`、可控热源最低功率时是否仍越界、升温时间与误差裕度；计算每项
NAND/TSV/PHY/HBM资源的前台+维护+重试服务负荷。冷态已过载的不稳定队列不能
归咎于温度。固定GPU外热不可控时，HBF限速可能无解，须报告不可行。

| 场景 | 预注册选择规则 | 暂不能填的量 |
|---|---|---|
| Safe | 规定运行窗内不触Light且裕度大于模型/传感器/时延误差 | 器件温限、真实服务和控制参数 |
| Near | 相同冷初态运行后才入Light，观察降温/恢复/滞回；从证据域按负载递增首次满足者选，不挑收益最大者 | 输入负载点/持续时长等待MODEL_FREEZE |
| Stress-thermal | 域内持续负载进入Severe或执行器不可行边界，记录保护退出 | 未知温限不得用统一85C代替 |
| Stress-refresh（必要时） | 维持24h级物理年龄，用明确先前运行所得年龄分布和公平初态触发维护 | 不能把24h变24s；新写短时无维护是合法null |

每次包含升载、保持、动作、降载/恢复。时长按新模型tau和维护时标固定，至少
覆盖计划观察行为；不用旧4s/10ms作通用时长。未做可行性筛选前不冻结点值，
但不因此停止P3/P4的独立正确性工作。

## D：主实验

在C通过并取得新确认后，一主线profile、一冻结workload的最低主矩阵为
3场景×2策略=6配置；若五种真实策略均完成验收，则3×5=15配置，不先假定。
第二种代表性workload只在有效trace/模型与范围确认后增加：12或30配置。
确定性模型不机械重复三遍来给零方差置信区间；如有随机源，记录种子、独立性和
重复数后重新算总点。比较同固定时窗完成量和等量工作代价，保留未完成/超时队列。

实际LLM候选记录：docs/eval/workload_methodology.md定义Qwen3-30B-A3B BF16、
48层/128experts/top-k8、四个32token prompt、32生成token、seed0、并发1。
这是协议，不是当前已有trace；模型revision/权重hash、路由raw与placement/cache
初态尚缺。本地两个MQSim请求fixture也不能伪称LLM。先恢复冻结原trace，缺失时
另批合成因果服务研究并标SIMULATED，token列UNAVAILABLE。没有新GPU授权。

指标：goodput、p50/p95/p99排队延迟、温限违规时长、状态转换、维护积压/年龄
裕度、分源能量、写入/PE增量、完成守恒。功耗、温度、流量、controller、维护、
queue共用模型时轴；token仅在来源合法时增加，禁造六条齐全曲线。

## 八项消融逐项挂接

| 消融 | 当前状态 | 完成含义 |
|---|---|---|
| issue stall / consumption wait | NOT_IMPLEMENTED_EQ3；非热核心范围受保护 | 仅用已验证挂钩，统一因果时轴 |
| no thermal coupling | NOT_IMPLEMENTED完整版本 | 消除共享动态sink中介耦合，尽量保留自热阻抗；不止删directedge |
| no refresh contention | NOT_IMPLEMENTED | 理想维护资源但需求/年龄/能量/磨损相同；与完全无刷新分开 |
| reference vs fast | 部分：旧抽象热模型比较FAILED | 热求解器与内存服务两条轴独立 |
| no prefetch / prefetch | 现有非热策略可见，EQ3因果接入未验收 | 同时统计隐藏延迟与增加的物理流量/热 |
| fixed / adaptive placement | NOT_IMPLEMENTED_EQ3 | 搬运/更新能量、排队和热点转移 |
| no coalescing | NOT_IMPLEMENTED_EQ3 | 真实NAND字节/排队差，不仅逻辑请求计数 |
| cache sizes | 现有配置不等于EQ3消融READY | 区分权重/KV、容量/带宽与可装载模型 |

优先完成前三项热相关对照：coupling、refresh contention、thermal reference/fast。
若主baseline可复用，每项新增arm数按实际已确认场景/策略计，不把所有维度相乘。

## E：敏感性和扩展（尚非冻结矩阵）

保留9个独立语义轴：Ea、TIM垂直路径、顶部对流路径、HBF能量/byte、ambient、
GPU外热、HBF温限、HBM温限、GPU温限。几何生成的C/G不独立乱扫；TIM厚度变
动须联动公共平面/高度或明确改TIM材料；HTC变动需再核验快速模型。
每轴若3levels，去重OAT为`1+9*(3−1)=19`。单Near/单workload/2策略为38配置，
若其中2个共同baseline已在主矩阵输入等价且登记复用，新增36；不是现在批准38点。
缺乏可辩护端点的轴暂BLOCKED_FOR_SCAN，不能随手nominal×.5/×2。

初步只保留三个交互候选：ambient×顶部冷却、HBF读能量×GPU外热、Ea×初始年龄。
每对若2×2角点、2策略，为12×2=24额外配置；与OAT输入相同点再显式去重。
实际温限/能量域未闭合，以上是准确计数公式及上限提案，不是假装已定实际点。

四拓扑强制交付分阶段推进，不是可选扩展；详见FOUR_TOPOLOGY_COVERAGE.md。
保持相同器件4+4先比direct/relay/DASH；8HBF+GDDR同时
改变内存和冷却，单列归因。OCP三档、HBM16H需自己的组织与热域证明，不复用
未经验证的一套ROM。不要把四拓扑×三规格×九轴×策略×workload全部笛卡尔积。

compute-die-first按无控制动作下首次触及各自物理/研究限制定义，记录并列/
不确定/无触限；提前Light不算物理限值。比例仅为冻结采样域配置占比，给分母/
权重/不确定性，不是现实概率；不预设任何收益或百分比。
