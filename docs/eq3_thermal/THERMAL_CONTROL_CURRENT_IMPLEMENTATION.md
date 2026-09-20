# 当前温控实现与 ECC/高温读速证据说明

本文只描述当前 `CpuService` 工程 fixture 的实际代码行为。结论均为
**DOC_DERIVED**；D4 温度阈值和时序参数是 **SCENARIO_ASSUMPTION / ENGINEERING_FIXTURE**，
不是 SSD、DRAM、OCP 或 Sandisk 产品限值。

## 1. 温度如何产生控制状态

`control_sample()` 在每个采样时刻读取一个 stack 的全部 `stack_*` 热节点，使用
其中最高温度。温度依次跨过 Light、Severe、Shutdown 阈值时，建议状态分别为
Light、Severe、Shutdown；否则建议 Normal。降级时才使用滞回：只要当前温度仍
不低于“当前已应用状态的阈值减去 hysteresis”，就保持当前状态。若建议重新等于
已应用状态，已有 pending 动作会被取消。

D4 九点矩阵对全部八个 stack 使用相同参数：

| 参数 | D4 数值 | 含义 |
|---|---:|---|
| 初始温度 | 298.15 K（25 °C） | fixture 初态 |
| 采样周期 | 20 ms | `sample_ns` |
| Light | 299.15 K（26 °C） | 第一档准入限速 |
| Severe | 300.15 K（27 °C） | 停止新的非维护请求 |
| Shutdown | 302.15 K（29 °C） | 停止该端点上的新请求，包括维护 |
| 滞回 | 0.25 K | 仅用于恢复方向 |
| 动作延迟 | 20 ms | 采样建议到动作可应用的延迟 |
| 最小驻留 | 100 ms | 原策略的双向驻留；v2 的恢复驻留 |
| Light 间隔 | 80 ms | 每个受控端点成功准入一次非维护请求后的下次准入时间 |

这些很低的温度档位用于让短 CPU fixture 覆盖状态机。它们与 OCP 的 0–105 °C
工作结温范围、85 °C/24 h powered-on retention 条件均不是同一概念。

原 `hysteresis` 策略把升级和恢复都安排在
`max(now + 20 ms, last_change + 100 ms)`。默认关闭的
`hysteresis_escalation_priority_v2` 只让升级跳过恢复驻留，仍等待 20 ms 动作延迟；
恢复继续使用滞回和 100 ms 驻留。`none` 仍记录温度和建议状态，但不生成动作。

同一时间戳的顺序是：完成并释放资源 → 温度采样 → 应用到期控制动作 → 生成到期
维护 → 准入队列 → 推进热模型。该顺序使控制动作在本时间戳的准入前生效。

## 2. 控制状态实际改变什么

`admit()` 先从 route 得到请求实际经过的全部受控端点和资源，再联合检查：

- Normal：控制状态不阻塞；仍须等待请求到达且全部资源空闲。
- Light：维护不受配额限制；非维护请求必须到达各受控端点的 `next_admit`。成功
  准入后，每个不同端点的 `next_admit` 更新为当前时刻加 80 ms。
- Severe：新的非维护请求受阻，维护仍可准入。
- Shutdown：所有新请求受阻，包括维护。

direct 路径通常检查一个 stack；relay 路径同时检查目标 HBF 和实际经过的伙伴
HBM。所有端点和资源检查完成后才整体预留，失败不会留下部分预留或活动能量。
资源占用还会独立造成等待。

控制没有改变已准入请求的完成时间，也没有改变媒体服务时长、带宽、功率、ECC
或错误概率。D4 的 foreground read 服务时间固定为 20 ms；因此 D4 所见的 20 ms
backend service p95 是输入定义产生的稳定值。控制通过延迟或阻止准入降低活动与
发热，同时可能显著增加排队时间。25 rps/stack 时，受控臂的 backend service
仍为 20 ms，但 backend queue-wait p95 为 10.32 s。不能把“已完成请求的固定服务
时间”解释成高温下 ECC 保持了读速。

## 3. 年龄和维护实际模型

年龄是纯模拟墙钟：`now - last_successful_commit`，尚无成功维护时再加固定
`initial_age_ns`。它没有温度加速、P/E、RBER、read-disturb 或 ECC 项。

- HBF：D4 固定周期 2 s，到期后生成 4 KiB maintenance read（20 ms），成功后
  再生成 program（40 ms）；仅最终成功 program 才清 pending、更新
  `last_commit_ns` 并增加 commit。此处没有温度依赖周期，也不是 OCP 的产品级
  24–48 h 典型刷新间隔。
- HBM4：D4 固定周期 1 s，生成 4 KiB `dram_refresh`（5 ms）；成功完成后更新
  年龄。刷新周期和时延不随温度变化，也没有 DRAM retention/error 模型。
- 维护失败只能来自显式 `fail_maintenance_once`/`fail_fraction` 注入；失败后等待
  一个采样周期重试。注入概率不消费温度。

`old_data_valid` 初始化为 true，当前代码不会基于年龄、温度或错误把它变为
false。program/erase/commit 计数是事件账本，不是寿命、ECC margin 或数据完整性
推断。

## 4. 尚未接通的 ECC/高温读速因果链

当前唯一闭合的反馈是：

`实际活动能量 → 模拟温度 → 控制状态 → 后续请求准入 → 后续活动能量`。

下列链路均未实现：

`温度轨迹 + 数据年龄 + P/E/read-disturb → RBER/retention error → ECC corrected bits
/ margin / uncorrectable error → read retry次数与附加媒体/传输时延及能量 → 完成或失败`。

因此目前无法回答“温控是否通过降低 ECC 压力稳定高温读取速度”。能够回答的只有：
准入节流在该工程 fixture 中降低了活动量和峰温，并将代价表现为排队和未完成工作；
一旦请求开始，其固定 20 ms read 时长与温度无关。

仓库证据也保持这一边界：

- `configs/eq3_thermal/reliability.json` 为 `enabled:false`、`NOT_IMPLEMENTED`，没有
  retention reference、目标 HBF ECC/UBER 或 endurance 标定。
- OCP v0.7.0 的本地登记只给出 85 °C powered-on 24 h retention 条件、产品特定
  P/E/read-disturb，以及典型 24–48 h、产品特定的周期维护；它没有给出可直接
  生成目标 RBER、ECC strength、retry 分布或控制阈值的参数。
- HeatWatch 的 3D-NAND 温度/retention 关系只登记为跨器件机制代理，且原拟合温区
  与磨损范围有限，不能冒充 Sandisk HBF 测量。
- Sandisk fact sheet 的 “no refresh power” 边界与 OCP 的周期维护语义在仓库中
  被明确分成不同 profile；不能同时拿来证明当前 2 s 维护 fixture 的物理真实性。
- HBM4 侧没有目标器件的温度相关 refresh、bank/channel timing、ECC 或 retry
  参数；固定 1 s/5 ms 仅是软件闭环情景。

若以后要检验该问题，最小缺口不是再调当前温控阈值，而是增加有来源且可关闭的
可靠性消费者：按实际温度历史、年龄和磨损生成 raw-error/retry 事实，再让后端的
实际命令阶段消费 retry 时延与能量，并单列 corrected/uncorrectable 结果。缺少这些
环节时，只能报告温度、准入、队列、维护事件和固定服务时延，不能报告 ECC 收益。

## 代码与数据位置

- `src/eq3_thermal/cpu_service.cpp`：`control_sample()`、`apply_controls()`、
  `admit()`、`maintenance_due()`、`complete_due()` 和固定服务时长消费。
- `eq3_thermal/plans/decision-execution-v2/d4-inputs/rate*-*.json`：D4 阈值、采样、
  驻留、维护周期和服务时长。
- `docs/eq3_thermal/D4_CONTROL_COST_REPORT.md`：九点控制成本和排队结果。
- `configs/eq3_thermal/sources.json`、`reliability.json`，以及
  `docs/eq3_thermal/PARAMETER_SOURCE_AUDIT.md`：OCP、Sandisk、HeatWatch 与缺口边界。
