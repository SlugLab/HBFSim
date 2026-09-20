# 隔离维护实验：消融与九轴敏感性就绪审查

状态：`READINESS_AUDIT_ONLY`，2026-09-20。本审查未启动热求解器，未改变默认
模型、热方程、边、控制策略或科学阈值。依据为阶段 `USER_TASKBOOK.md` §8.3/8.4、
当前隔离实验源码，以及已完成的
`Q1-WEIGHT-MAINT-PILOT03` 原始账本。下文的 `READY` 只表示接口和输入能够形成
可审计实验点，不表示点已获数值结果、实物参数已校准或 P2 资格已改变。

## 已核对的实际基线

`Q1-WEIGHT-MAINT-PILOT03` 是 8 s active 加 2 s recovery observation、20 ms 热窗口的
`mixed_direct/W1/guard_only` 条件工程 pilot。4032 个请求全部完成，64 次维护提交，
输入能量为 1600.0025647932162 J，峰值 396.0463965549566 K。`energy.csv` 有
500 个连续 `WINDOW_TOTAL`，实际出现 121 个带能量组件；GPU 为 1600 J，其余
存储和互连活动合计约 0.002564793216 J。能量系数均标为
`SCENARIO_ASSUMPTION`。这组比例本身不能外推为产品能耗或一般 workload。

manifest 的 `active_ns=8000000000`、`end_ns=10000000000`。500 个原始窗口的
`phase` 全部为 `OBSERVATION` 是正确合同：`OBSERVATION/DRAIN` 表示是否已越过
总观察终点，`ACTIVE/RECOVERY` 是另一条正交分段。转换器保留原始 phase；后处理
按 manifest 的 8 s 边界另行派生 active/recovery segment，不能把 recovery 改名为
drain。该点没有 `end_ns` 后的额外已提交工作排空，summary 的 drain completion 为 0。

工作热服务锁定完整 2 mm 网络：64,512 节点、255 个物理/封装实体、275 个传感器，
初态和上下环境均为 300 K，顶部/底部换热系数分别为 1400/25 W/(m² K)，侧壁绝热。
它在一个进程中只分解一次固定的 `C/dt+G`，逐 20 ms 窗接受组件焦耳。服务和旧
campaign runner 使用同一离散方程、同一 Eigen 分解族；二者成对一致只能证明
**离散实现/适配一致性**，不能作为独立物理参考资格。

证据分类：任务书约束和已批准阶段为 `USER_CONFIRMED`；上述文件内容与数值为
`DOC_DERIVED`；尚未运行的成本估计和建议分组为 `INFERRED`。

## A1：保留同一网络的源隔离响应

### 可做的最小消融

当前完整 RC 系统在线性、温度无关材料假设下可按热源叠加。最小方案固定原始
`C/G`、初态、边界和时间步，对同一个 pilot 能量账本生成以下回放：

1. 一个零活动基线，验证 300 K 等温状态和零静态源；
2. 九个源域：`gpu`，以及 `hbf0..3`、`hbm0..3`。每个内存域保留该 stack 的
   base、全部 die 及互连端点能量，其他域本窗口能量置零；
3. 对每个 GPU/stack 观察量，采用其所属源域运行的温度；同时保存全耦合运行，
   交叉源贡献定义为 `T_full - T_own_domain`。所有运行使用同一完整网络，因此
   自热响应、同一边界冷却、共享封装的被动热容和散热路径都保留；被消去的是
   其他源域对该观察域的温升响应。

该构造应命名为 `SOURCE_ISOLATED_DOMAIN_RESPONSE`。它**不是**删边模型，也不是
“组件之间不存在物理热传导”的全局温度场：其他器件仍作为被动导热/储热路径，
而将九个运行按域拼出的观察集合不满足一个单一全局能量守恒方程。现有
`coupling off` 只跳过标为 `InterComponent` 的直接边，仍可经共享 substrate、
interposer、lid 和边界耦合，并改变自热阻抗；不能用它冒充 A1。

这个九域版本可直接使用现有服务协议，无核心 ABI 变化。每个独立进程只需一份
分解和同样 500 次推进。按既有完整模型一次分解约 10.5 s、一次推进约 0.03 s 的
量级估算，零源加九域约 4–5 CPU 分钟，若保留与 pilot 同粒度 JSON，输出约
0.7–0.8 GiB；这些是 `INFERRED` 预算，启动前仍应由统一 runner 做实际 preflight。

### 严格逐组件版本的边界

pilot 有 121 个实际带能量组件。逐组件各跑一次能得到每个传感器对每个源的响应，
但现有服务只输出实体/传感器归约，不输出节点场。stack hotspot 是非线性 `max`，
不能仅用各次 hotspot 标量拼出“每个节点只保留自身组件源响应”的严格逐组件场。
旧 runner 全场输出可以离线构造该场，但约 122 次完整回放会产生明显更高 I/O 和
因子开销。故严格逐组件 A1 为 `CAPABILITY_GAP/NOT_READY_FOR_MINIMAL_RUN`；九域版
是当前可运行的有界消融，结论仅限跨 GPU/stack 源响应。

## A3：实际 pilot 能量的完整 2 mm 成对回放

新增的实验专用转换器
`experiments/eq3_maintenance/pilot_energy_replay.py` 只读取 `energy.csv` 的
`WINDOW_TOTAL` 行，拒绝间隙、重叠、乱序、未知组件、负值和非有限能量。它按
`rc_grid.json` 中组件 cell 的真实体积权重分配节点焦耳，并写入事件、源/网格/
事件哈希、逐组件守恒和阶段窗口收据。`ACTIVITY` 行不再次积分，避免重复能量。
固定测试 4/4 通过；测试未调用 solver。

最小执行顺序如下，须放入新的 point 目录并由统一资源 runner 执行：

```text
python3 experiments/eq3_maintenance/pilot_energy_replay.py \
  --energy-csv <PILOT03>/energy.csv \
  --grid <RC2MM>/rc_grid.json \
  --events-output <A3>/events.txt \
  --receipt-output <A3>/conversion_receipt.json

<reviewed-eq3_campaign_rc_runner> --run \
  --model <RC2MM>/model.txt --events <A3>/events.txt \
  --step-s 0.02 --slot-s 0.02 --sample-s 0.02 --end-s 10 \
  --min-k 300 --max-k 400 \
  --model-sha256 <actual> --events-sha256 <actual> \
  --runner-source-sha256 <actual> \
  --domain-version EQ3_MAINTENANCE_PILOT_REPLAY_2MM_V1
```

成对比较必须使用相同的 500 个整数纳秒窗口和相同 275 个 sensor 定义，逐时刻
比较 temperature、hotspot cell、全局 min/max，以及累计输入、边界损失、储能变化
和残差。旧 runner 的全节点 stdout 约有 32,320,512 行，应流式归约并受 4 GiB
单点边界约束，不生成无界临时副本。原服务 `thermal.csv` 是另一侧不可修改原始证据。
温度比较不能只比较峰值。成对回放保留原始 `OBSERVATION` phase，另按 manifest
的已声明 8 s active 边界报告 `[0,8 s]` active 与 `(8,10 s]` recovery；这是从
已声明边界派生的 segment，不改写原始 phase，也不冒称 drain。

此点当前为 `READY_FOR_COORDINATED_RUN`，尚未执行。它不使用 1 mm、不重跑 P2，
也不证明 reference 已合格。若温度一致，只能报告
`FULL_2MM_DISCRETE_EQUIVALENCE`；策略重新闭环需要可替换热消费者接口及新的成对
运行，本次开环回放不能代替闭环一致性。

## A2 与其余五项核心消融

| 消融 | 实际生产者/消费者 | 当前状态 | 最小缺口 |
|---|---|---|---|
| A2 无维护争用 | MQSim 维护命令和 fabric 真实共享资源；年龄仅在成功 commit 后更新 | `NOT_READY` | 只有 maintenance on/off，没有“同一维护意图、能量和年龄提交但使用理想独立资源”的实验模式。关闭维护不等价。需实验 backend/fabric 的显式独立资源策略和固定意图回放；不能在分析层伪造。 |
| issue / consumption | 请求已有 arrival/admission/media/fabric delivery/reported completion | `LIFECYCLE_FACTS_ONLY` | 没有 LLM layer consumption deadline 或 issue dependency 的消费者；`external_wait` 不是 consumption wait。 |
| prefetch | 旧计划登记 `none/on_demand/one_layer_ahead` 名称 | `DECLARED_ONLY_FOR_EQ3` | 当前隔离 coordinator 没有已验收的 prefetch 请求生产者，亦无额外物理流量/热量消费者。 |
| fixed / adaptive placement | W1 全局 page、静态 stripe/local page 真实消费 | `FIXED_BASELINE_ONLY` | 没有 adaptive migration、placement decision、搬运能量或映射更新。不可把改变起始页称 adaptive。 |
| page coalescing | 当前每请求严格一个 16 KiB page，stack map 要求同类、对齐单页 | `NOT_READY` | 无多页合并器及后端/能量/完成语义；逻辑计数合并不构成物理 coalescing。 |
| cache sizes | fabric 有 8 MiB HBF、4 MiB HBM 双 bank；profile 另有 64 MiB `hbm_cache_bytes` | `BUFFER_ONLY/DECLARED_CACHE_FIELD` | 双 bank 是转发缓冲，不是权重/KV cache；`hbm_cache_bytes` 未发现隔离链路 cache 命中/替换消费者。不能扫描该字段冒充 cache 消融。 |

因此这五项目前没有合法成对实验 arm。固定 placement 可作为基线事实，但缺少
adaptive 对照。补齐它们会改变 workload 因果或模块职责，超出本就绪审查。

## 九个敏感性轴

`有消费者` 表示当前 pilot 确实读取并影响运行；`范围` 区分注册来源和本阶段
已冻结可扫水平。任务书要求无可辩护范围时只阻塞该轴。

| 轴 | 当前真实消费者 | 已登记值/依据域 | 就绪结论 |
|---|---|---|---|
| Ea | 无；`configs/eq3_thermal/reliability.json` disabled，当前年龄按秒推进 | HeatWatch 旧 3D NAND 20–70°C、1k–10k P/E：1.01/1.04/1.08 eV；不是目标 HBF uncertainty | `RANGE_AVAILABLE_PROXY, CONSUMER_ABSENT`。可用于将来的条件年龄机制敏感性，不能影响当前运行，更不能生成 ECC/失败概率。 |
| TIM 垂直路径 | 已烘焙进完整网络 C/G：k=10 W/(m K)，HBF/HBM/GPU 厚度 125/189/175 µm | 当前值为 MFIT/几何代理；另有 k=5 文献情景提示，但未冻结三水平，HC5000 整体阻抗不适配 | `MODEL_CONSUMER_ACTIVE, SCAN_BLOCKED_RANGE`。改变厚度/材料须重生成一致 C/G；不能逐 edge 乘系数。 |
| 顶部冷却 | model 顶 Robin 边界 | 1400 W/(m² K) 单一 MFIT 代理 | `MODEL_CONSUMER_ACTIVE, SCAN_BLOCKED_RANGE`；没有已冻结低/中/高。 |
| HBF energy/byte | 没有单一 HBF J/B；ledger 分别消费 NAND media 0.05 W、command/data-out 0.01 W 与 fabric 2 pJ/B | 均为工程情景，绝对 HBF read/program/PHY 能量仍缺 | `MULTI_STAGE_CONSUMER_ACTIVE, SCAN_BLOCKED_SEMANTICS_AND_RANGE`。不得把各阶段无条件缩成一个 J/B 旋钮。 |
| ambient | model 初态、top、bottom 均为 300 K | 单一代理值 | `MODEL_CONSUMER_ACTIVE, SCAN_BLOCKED_RANGE`。初态与环境同移是平移诊断；只改环境会引入冷却瞬态，二者不可混称。 |
| GPU 外热 | `ActivityEnergyLedger.flush` 每窗实际加入 GPU 能量 | 当前 pilot 200 W；旧公共 trace 有 40/65/100 W 情景，均非产品校准 | `READY_TO_FREEZE_CONDITIONAL_LEVELS`。接口已经闭合，但三水平组合及共同 workload 尚未在本审查冻结；可不改代码形成 OAT。 |
| HBF 温限 | `ThermalClient` 实际按 353.15/363.15/378.15 K 控制 | 一套 research scenario，产品 limit 仍 UNKNOWN | `CONSUMER_ACTIVE, SCAN_BLOCKED_RANGE`。三个数是 Light/Severe/Shutdown 状态边界，不是一个轴的低/中/高三套配置。 |
| HBM 温限 | 同上，当前同为 353.15/363.15/378.15 K | 一套 research scenario，产品 limit UNKNOWN | `CONSUMER_ACTIVE, SCAN_BLOCKED_RANGE`；不得因数值与 HBF 相同而宣称同一器件限值。 |
| GPU 温限 | `ThermalClient` 实际按 363.15/373.15/383.15 K 控制 | 一套 research scenario，产品 limit UNKNOWN | `CONSUMER_ACTIVE, SCAN_BLOCKED_RANGE`。`compute_die_first` 在没有三套明确限值前不能形成该轴 OAT。 |

当前没有任何轴同时满足“已冻结三水平、实际消费者、相同输入可成对”的完整条件。
GPU 外热最接近就绪，只需在已有登记情景中明确三水平和输入等价规则；Ea 即使有
三点代理，也因运行消费者缺失而不能计入点数。因此本审查的真实可启动敏感性矩阵
为 **0 点**，而不是先写 38 点。若仅 GPU 外热随后被正式冻结为三水平，则去重
物理组合为 `1+(3-1)=3`，乘两个策略为 6 点；这只是计数公式，不是本文件启动授权。

九轴均不得以 2.069 K 局部网格差作为通用实物误差范围。涉及 TIM、冷却或 ambient
的变化必须重生成完整物理一致网络并做低成本能量/响应检查；其结果不自动继承
当前模型资格。

## 本次新增与验证

- `experiments/eq3_maintenance/pilot_energy_replay.py`：A3 只读能量回放转换；
- `experiments/eq3_maintenance/test_pilot_energy_replay.py`：能量/体积分配、阶段保留、
  窗口连续性、未知组件及非法能量固定测试；
- 执行结果：`4 tests`, `PASS`, 约 0.002 s；无 solver、无数值输出、无原件覆盖。
- phase 审核：末 2 s 是 observation 内的 recovery，不是 drain；转换器保持该正交
  语义，本次未修改原始结果或主 coordinator。

尚未执行 A1 或 A3 数值点，未生成敏感性点，未打开 blind 数据。
