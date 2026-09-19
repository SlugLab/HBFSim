# HBFSim EQ3 project instructions

Preserve the verified P1 checkpoint and immutable baseline. No driver/global
environment changes, public ABI/PTX/TMA/future/cache rewrites, whole donor merges,
or unrequested push. Independent, default-off, reversible changes only.
Read docs/eq3_thermal/README.md and the current continuation/handoff records before
work. When the surrounding workspace is available, also read its AGENTS.md and
docs/codex/PROJECT_HANDOFF.md, PROJECT_STATE.md and CODEX_TODO.md.
Distinguish USER_CONFIRMED, DOC_DERIVED and INFERRED assumptions; numerical
references, physical proxies, vendor specifications and uncalibrated projections
are not interchangeable. Never modify raw results or invent missing metadata.

## 实验元信息与用户确认（强制）

在任何大规模实验、正式论文候选实验、批量/多维参数扫描、跨设备批跑或新增 GPU 持续负载之前，必须先向用户展示实验预检单，并取得用户对该版本的明确确认。预检单至少包括：研究问题与假设、证据类型、代码与环境版本、器件/拓扑/几何/功耗/可靠性参数及来源、workload 和初始状态、扫描点与重复数、模拟时长和预计实际耗时、资源预算、对照与消融、观测指标、通过/失败/安全停止条件、输出位置及已知限制。用户沉默、历史泛化授权、代理自行生成的 approval 文件以及“开始前提醒过”均不构成批准。

将预检单保存为可读报告及机器可读 manifest，以实验 ID、版本及内容哈希绑定确认。未确认时状态必须为 PENDING_USER_APPROVAL，禁止提交或后台启动正式任务，禁止拆成多个“小批次”规避。研究问题、物理假设、输入、扫描范围、控制策略或资源预算超出已批准范围时，先停止受影响的后续任务，更新差异并重新确认。只读排查、隔离构建、单元测试和已限定的小规模软件验证可继续；不得把它们改名后冒充正式实验。详细契约见 docs/eq3_thermal/experiment_approval.md。

## 参数闭合与两次冻结

盘点配置、代码常数、CLI/环境默认及实际消费者；未消费字段不能冒充扫描开关。
逐项保存原值/单位、规范化值、原件位置/哈希、条件、推导、转用假设、允许域和
缺失所影响的结论。使用 SPECIFIED / MEASURED / DERIVED / PROXY /
SCENARIO_ASSUMPTION / UNKNOWN_BLOCKING / NOT_USED，禁止混淆证据身份。
DESIGN_FREEZE 冻结器件/拓扑、假设、允许域、标定方法，不虚构最终拟合值；
用户确认有界标定版本后才执行，独立验证后 MODEL_FREEZE，再另批正式 EQ3。
关键输入只有情景依据时结果为 CONDITIONAL_SIMULATED，不是实物已标定。
未知项只阻塞依赖它的结论，不阻塞独立工作。达到停止条件的旧时间检查保留，
不继续机械细化；新几何不得继承旧精度/步长。已查看 heldout 不得再充当模型
选择后的唯一盲测。方向性要求不批准旧四点计划、标定批次或 GPU/云负载。

标定预检不以固定运行次数或首轮耗时估计作为科学停止上限；按原始资料、推导、
逐次误差与可辨识性审核迭代，达到目标停止，无改善先诊断。资源安全/隔离边界及
具体执行版本确认仍适用；该过程要求不构成标定/矩阵/GPU启动批准。改变物理
假设、输入域或拟合方法时更新预检并重新确认。
