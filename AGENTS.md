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
