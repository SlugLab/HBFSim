# 稳定读速控制的资料依据与最小参数边界

日期：2026-09-20  
状态：**证据与窄方案；未修改代码、输入或实验状态**

## 结论

现有权威资料足以支持两件事：HBF 应保留 OCP 定义的分档热保护；读速控制应观察
真实完成率、端到端时延、积压和错误/重试事实，而不能仅凭瞬时温度推算读速。
资料不足以拟合通用的 `temperature -> read bandwidth` 曲线，也不足以把当前 D4 的
26/27/29 °C 工程阈值或固定 20 ms 读取解释成产品参数。

候选实现是一个**默认关闭、位于提交 gate 外层的离散反馈策略**。它尚未实施，以完成字节率为
主目标，以温度模式作硬约束；只调尚未提交请求的准入额度，不改后端已经发生的
完成时间，不追加假想 ECC 延迟。该方案不是 PID/MPC，也不要求改 MQSim 调度。

证据标签沿用项目约定：**SPECIFIED** 是目标规范直接事实；**PROXY** 是非目标器件
实测；**SCENARIO_ASSUMPTION** 是可做敏感性分析的工程选择；**UNKNOWN_BLOCKING**
表示在获得产品资料或实际后端事件前不能形成定量器件结论。

## 可采用的字段与证据

| 拟用字段 | 原始值、单位与条件 | 证据 | 允许的用途 | 不允许的转用 |
|---|---|---|---|---|
| `junction_temperature_c` | 0–105 °C，HBF junction operating range | **SPECIFIED**，[OCP HBF v0.7.0 §9.1, p106](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf) | 输入域检查、遥测 | 105 °C 不是公开的 LTT/STT，也不能当正常控制目标 |
| `rtt_c/ltt_c/stt_c` | MMIO `0x150`，编码为 `60 °C + value*0.5 °C`，约 60–124 °C；值为 implementation-specific | **SPECIFIED**，OCP §5 register table、§9.2 | 有真实寄存器值时直接使用 | 编码范围不是推荐阈值范围；缺寄存器时保持 UNKNOWN |
| `thermal_mode` | Normal 全性能；Light 可自动降低时钟且响应可能变慢；Severe 反压新命令、先完成在途命令，表注只支持维护；Shutdown 报 link error 并关闭 | **SPECIFIED**，OCP §9.2 pp106–108 | 热保护状态与 gate 上限 | 规范没有给出各档的固定带宽比例或延迟倍率 |
| `cecc/uecc/retry_opcode` | CECC/UECC 可要求 block refresh；host-driven retry 时主机按返回 opcode 重发同一读 | **SPECIFIED**，OCP §11.5.1 pp119–120 | 后端实际报告时记账、触发支持的维护/重试 | 不能从温度猜 CECC/UECC、重试次数或重试耗时 |
| `refresh_due` | 周期维护通常 24–48 h，实际 interval、read-count threshold 均 product-specific；同 die read/refresh 不应并发 | **SPECIFIED**，OCP §11.5 p118 | 产品 profile 有值时作期限和冲突约束 | 24–48 h 不是通用保证，也没有给出维护服务时间 |
| `advertised_read_Bps` | 第一代 Sandisk HBF 目标 1.6 TB/s；16 dies、256 Gb/die、512 GB decimal | 厂商资料，[Sandisk HBF Fact Sheet, July 2025](https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf) | 拓扑/量级检查及明确标注的产品目标 | 不能当持续完成率、每栈 SLO、热稳态实测或 gate 默认目标；资料脚注包含内部测试/模拟 |
| `retry_count` 与 `retry_latency_ns` | 160 颗 48-layer 3D TLC、超过 1100 万页；`t_retry=N_retry*(t_R+t_DMA+t_ECC)`。fresh page 可无 retry；2K P/E、1 年 retention 平均 19.9 次 retry、平均 read latency 21 倍 | **PROXY**，[Park et al., ASPLOS 2021](https://people.inf.ethz.ch/~omutlu/pub/Reducing-SSD-Read-Latency-by-Optimizing-Read-Retry_asplos21.pdf) | 证明 retry 必须作为独立可观测开销；构造显式标注的 SSD 敏感性点 | 非目标 HBF，不能把次数、倍数、ECC 72 errors/KiB 或论文时序写入 HBF 默认值 |
| `pec/retention_age/operating_temperature` | 同一论文中 0 P/E、6 个月仍有 54.4% 读取至少 7 次 retry；温度测试为 30/55/85 °C。其样品中温度影响小于 P/E 与年龄，且 30/55 °C 的最终 retry 错误反而比 85 °C 多 5/3 bits | **PROXY**，Park et al. Fig.5、Fig.7 | 说明单调“越热 retry 越多”并不成立；要求分开记录历史状态 | 不能据此宣称降低 HBF 温度必然减少 retry 或提高读速 |
| `temperature_history` | 一家厂商 30–40-layer 3D charge-trap MLC；20–70 °C、1k–10k P/E 拟合；`Ea=1.04 eV`，95% CI 1.01–1.08 eV，`R²=0.76`；温度每秒记录并分段累积 acceleration factor | **PROXY**，[Luo et al., HeatWatch, HPCA 2018](https://www.cs.cmu.edu/~yixinluo/index_files/heatwatch_hpca18.pdf) | 历史累积器的数据结构与机制敏感性；可用 1.01/1.04/1.08 eV 作明确标注的代理敏感性 | 不是 Sandisk HBF 标定；不得外推成 85–105 °C 的绝对 RBER、寿命或 retry 分布 |
| `thermal_step` | Samsung PM963 达阈值后逐级降低性能，并在温度未下降时继续下一档 | 厂商 SSD 方法资料，[Samsung PM963 brochure](https://image.semiconductor.samsung.com/content/samsung/p6/semiconductor/newsroom/tech-blog/samsung-ssd-pm963-brochure/Samsung_PM963-1.pdf) | 佐证离散分档和重新测量的方法 | NAND SSD 产品方法不是 HBF 阈值、档位比例或控制周期 |

Sandisk 的 “no refresh power” 与 OCP 的产品特定周期维护应继续作为不同 profile；
不能在同一结果里同时把二者当成同一器件的已知事实。

## 建议的最小配置契约

下面只定义控制器需要消费的事实。带数值的工程建议均为
**SCENARIO_ASSUMPTION**，不能进入产品参数冻结。

| 字段 | 最小规则 |
|---|---|
| `enabled` | 默认 `false`；启用必须显式选择 `read_rate_gate_v1` |
| `target_completed_read_Bps` | 必填的 workload/SLO 输入；不得默认取 1.6 TB/s。没有来源时为 UNKNOWN，策略不激活 |
| `target_latency_p95_ns` | 只有要声明时延稳定时才必填；必须含 gate 等待、后端服务、retry 和传输 |
| `window_ns` | 建议 fixture 初值 1 s、敏感性范围 0.5–2 s；这是工程窗口。HeatWatch 的 1 s 是可靠性采样实现，不能当控制周期标定 |
| `throughput_tolerance` | 建议 0.05，敏感性 0.02–0.10 |
| `bad_windows/good_windows` | 建议 2/3 个连续窗口，避免单窗口抖动；均为工程假设 |
| `quota_step_fraction` | 建议每次 0.10，敏感性 0.05–0.20；限制在 `[0,1]` |
| `rtt_c/ltt_c/stt_c` | 优先从实际产品寄存器/配置读取并保留来源；缺失时 UNKNOWN，不采用 D4 fixture 阈值 |
| `hard_max_c` | 产品明确值优先；仅有 OCP 时可把 105 °C 用作工作域越界保护/失败记录，不能冒充 STT |
| `observations` | 每 stack、每窗口记录 offered/admitted/completed bytes 与 requests、外部 queue depth/age、gate wait、backend busy/idle、inflight、资源或 link 利用率、backend service、transfer、retry count/time、CECC/UECC、温度及模式；后端不提供的字段保持 UNKNOWN |
| `history` | 若可靠性模型启用，再记录 program temperature、分段温度驻留、retention age、P/E、read count；缺项保持 UNKNOWN |

若需要不改产品模型的故障注入，可使用离散 retry **敏感性集合**
`{0, 1, 4, 8, 20}`，其中 20 仅是把论文的 19.9 四舍五入后的上端代理点；它不是概率
分布。每次 retry 的 HBF 时延和能量仍必须来自实际后端事件或显式 HBF 参数，不能套用
SSD 数值。

## 外部 gate 的简单控制规则

每个窗口先固定统计分母：`offered` 是到达 gate 的真实需求，`completed` 是窗口内
对外完成，跨窗在途请求按真实完成窗口计。控制器不能通过少接收请求来缩小 offered。

定义 `target_met` 同时满足：

1. `offered_Bps >= target_completed_read_Bps`，即窗口确实有足够需求可检验目标；
2. `completed_Bps >= target * (1 - tolerance)`；
3. 若配置了时延 SLO，端到端 p95 不超过它；
4. queue oldest age 和 backlog 没有持续增加；
5. 没有 UECC 或设备失败。

若 offered 不足，结果是 `INSUFFICIENT_DEMAND`，不是稳定或失败。若 completed 不足，
结果是 `UNMET_TARGET`，即使温度降低也不能报成收益。

策略每个窗口至多移动一个 quota 档，并先区分瓶颈证据：

- 若未达标，同时 gate 确实在限制准入、外部积压存在，且后端有观测到的空闲能力，
  才可提高一个 quota 档。不能仅因处于 Normal 或出现坏窗口就提高额度。
- 若后端保持忙碌、内部队列年龄增长、资源利用已饱和或 retry 开销增长，则保持
  `UNMET_TARGET` 并报告瓶颈；提高 quota 会加重拥塞。只有实际观测显示过量在途并发
  导致服务或尾延迟恶化时，才可降低在途上限作诊断性保护。
- 若缺少 backend occupancy、内部队列或 retry 事实，瓶颈原因是 UNKNOWN；保持当前
  quota 并报告，既不盲目增加准入，也不凭温度猜测 retry。
- 达标且存在过量交付、突发波动或无必要的外部排队时，可在任何非紧急温度模式下
  尝试降低一个 quota 档以平滑交付；不必等待进入 Light。下一窗口若破坏目标则回退。
- 达标且稳定时保持当前 quota。恢复到 RTT 以下只解除热保护，不要求无条件把 quota
  提高到 1。
- Light 仍施加产品定义或情景配置的热保护上限，但普通速率反馈在 Normal 同样工作；
  温度是保护与风险信号，不是普通性能反馈的唯一门槛。
- Severe/CATTRIP：服从 OCP 反压语义；在途完成或返回后端规定错误，维护按规范能力处理。
- Shutdown/link error：停止新提交并报告终态；不得自动改写成降速成功。
- CECC、UECC、retry：只消费后端实际事件。CECC 可排入后端支持的 block refresh；
  UECC/retry 依照实际能力处理。没有真实能力时返回 `UNSUPPORTED/UNKNOWN`。

硬温度保护始终高于性能反馈。性能 gate 只影响尚未提交的工作，等待期间仍由现有
事件推进处理在途完成、冷却、控制恢复和维护。回调只记录事实，不重入调度。
来源缺失会阻塞目标器件的温度—ECC—读速量化结论，但不阻塞按上述 UNKNOWN/hold
语义实现和测试这个默认关闭的工程 gate。

## HBF 可转用范围与阻塞缺口

可以直接转用到 HBF 的只有 OCP 模式、寄存器编码、在途处理、CECC/UECC/retry
协议和维护冲突语义。Sandisk 的 1.6 TB/s 是厂商目标量级；SSD 论文的数据只用于
证明 retry 是大且依赖历史的延迟项，以及设计敏感性测试。

以下仍是 **UNKNOWN_BLOCKING**：

- 目标 HBF 的实际 RTT/LTT/STT、传感器误差和报告延迟；
- 目标 HBF 的 ECC 强度、corrected-bit 粒度、retry opcode 表、次数分布；
- 每次 retry 的 sensing、ECC、base/PHY/传输时延与能量，以及能否重叠；
- 温度历史、retention、P/E/read-disturb 到 RBER/retry 的目标器件关系；
- block refresh 的服务时间、能量、资源和目标产品 read-count threshold；
- workload 的最低持续完成率与尾延迟 SLO。

在这些项关闭前，可声称的结果限于“外部门控在工程 fixture 中是否同时满足既定
完成率、时延、积压和 OCP 热保护约束”。不能声称已得到 Sandisk HBF 的温度-ECC-
读速曲线、寿命收益或产品最优温度。

## 后续验证契约

实现前先以固定软件测试验证窗口边界、checkpoint、同时间戳顺序和 quota 单步变化；
再用成对 CPU fixture 报告 offered/admitted/completed、排队、后端 occupancy/service、
retry、温度与能量。成对对照必须使用相同 offered 到达序列、相同统计窗口和相同初态，
并同时报告未完成工作，不能让 gate 改变需求分母来自己证明收益。验收必须覆盖：
gate 限额且后端空闲时小步提高、后端拥塞时禁止盲增、occupancy UNKNOWN 时 hold、
Normal 下达标平滑、稳定达标保持，以及高温降低但完成率失败并保持 `UNMET_TARGET`
的负例。只有实际后端提供 retry/ECC 事件后，才可把相应分账从 UNKNOWN 升级为
OBSERVED。

本文件不批准新实验，不修改现有 D4 六点/九点结论，也不把代理参数写入当前输入。
