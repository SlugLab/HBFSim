# HBFSim Project Rules

These are the stable rules for every Codex session in this workspace. Dynamic
state belongs under docs/codex, not in this file.

## Session startup

Before non-trivial work:

1. Read this file and any applicable nested AGENTS.md or AGENTS.override.md.
2. Read docs/codex/PROJECT_HANDOFF.md and docs/codex/PROJECT_STATE.md.
3. Read the task-relevant environment, reference, experiment, and open-question
   registries.
4. Inspect the relevant source, configuration, build, run, and manuscript files.
5. Load applicable skills from .agents/skills.

Do not treat chat history as the project state of record.

## Evidence and clarification

Classify every material assumption as:

- USER_CONFIRMED: explicitly decided by the user.
- DOC_DERIVED: supported by a registered source or repository evidence.
- INFERRED: a working hypothesis that is not confirmed.

Never present INFERRED as fact. Read available evidence before asking. Stop and
ask only when unresolved uncertainty can materially affect correctness,
scientific validity, HBM/HBF/GPU topology, device parameters, environment or
build targets, data interpretation, substantial resource use, irreversible
actions, paper claims, or cross-run comparability.

For each blocking decision, state why it matters, the options, the recommended
default and reason, and the effect of each option.

## Design and change gates

Use .agents/skills/hbfsim-project-guardrails for non-trivial project work. Use
the installed brainstorming skill before creative or architectural changes.
Small, reversible maintenance may proceed after reading the relevant context.
Large repairs or structural changes require a design and user approval before
implementation.

Do not modify research code, build logic, environments, or experiment design
merely to make an observed error disappear.

## Root-cause debugging

When an error occurs, diagnose it in layers:

1. preserve the exact symptom, command, inputs, logs, and environment;
2. identify the immediate failing component;
3. trace upstream configuration, dependency, build, data, and caller causes;
4. check host, driver, toolchain, resource, and compatibility causes;
5. re-evaluate the design or scientific assumption that made the failure
   possible.

Form a testable hypothesis at each layer and record evidence that confirms or
rules it out. Fix the earliest verified root cause, then rerun the smallest
reproducing check before broader tests. Do not loop through blind retries,
unrelated edits, arbitrary dependency/version changes, or repeated experiments.
After two unsuccessful variants of the same attempted fix, stop, summarize the
evidence, and widen the diagnosis instead of repeating the pattern.

## Portability

Research logic must not hard-code hostnames, server names, GPU UUIDs or
ordinals, absolute server paths, mount points, CUDA/compiler/library paths,
machine-specific environments, or one GPU model. Put machine details in
configuration, environment variables, inventory, or launchers.

Build artifacts are not source of truth. A supported machine must be able to go
from source to recreated environment, build, run, and results. Build separately
per target when architectures differ, record the target, and keep scheduling
logic outside the simulator core.

## Environment control

Assign an environment_id before an experiment matrix. Freeze the OS, kernel,
driver, CUDA/runtime, compiler, Python, simulator, key libraries, and model
dependencies for that matrix. Do not upgrade, downgrade, or install arbitrary
latest dependencies mid-matrix.

If the environment must change, create a new environment_id, document the
reason and affected runs, and explicitly decide whether results remain
comparable.

Every environment_id must have a versioned reproduction package described by
docs/codex/ENVIRONMENT_REPRODUCTION.md. Record exact tool and dependency
versions, source revisions, build target and flags, setup and verification
commands, lockfiles, and checksums. On another server, recreate and validate the
environment from these declarations; do not migrate by copying opaque binaries
or undocumented environments.

## Experiment gate

No non-trivial or costly experiment may start without a completed preflight
based on docs/codex/templates/EXPERIMENT_PREFLIGHT_TEMPLATE.md. It must connect
the research question and paper claim to a mechanism, variables, analytical
model, expected effect, confounders, minimum pilot, resources, output and figure
contracts, interpretation limits, and abort criteria.

Run the minimum informative pilot first. Do not launch a full matrix when the
effect is predicted to be negligible, unidentifiable, dominated by confounders,
or unable to support a useful paper claim or figure.

Resource scheduling must consider CPU, GPU, RAM, storage, PCIe/I/O, compilation,
and thermal interference separately.

## Results and metadata

Every paper-candidate run must have machine-readable metadata conforming to
docs/codex/schemas/experiment-metadata.schema.json. Automatically collect what
can be observed; write UNKNOWN when it cannot be known. Never reconstruct an
unknown value after the run and present it as observed.

Raw data is immutable. Derived data and plots must be reproducible from raw data
plus versioned analysis code. A result is ACCEPTED only after the run completed,
metadata consistency passed, provenance and comparability were reviewed, and
the interpretation supports only the registered claim.

Register experiments and references in their registries. Parameters that affect
scientific conclusions require a traceable source and version.

## Paper and handoff

The canonical manuscript is Overleaf unless the user explicitly changes that
decision. Local LaTeX may support compilation, analysis, figures, or prepared
edits, but must not become an untracked competing manuscript.

Update docs/codex/PROJECT_HANDOFF.md after a major phase, before a server,
account, or session change, or when context/token limits threaten continuity.
Keep it concise and include repository state, environment, jobs, accepted and
invalid results, blockers, decisions, and prioritized next actions.

## Long jobs and agents

Prefer event/status files such as status.json, DONE.json, and FAILED.json.
Callbacks are optional and external to research logic. Without event wakeups,
use estimated-duration checks and bounded exponential backoff, not high-frequency
polling.

Multiple agents are not the default. Use a coordinator and only a few narrowly
scoped workers when parallelism materially reduces wall-clock time without
resource interference or context drift. Hand off through repository documents.

### HBFSim runtime events, GPU reservation, and renewal

For HBFSim GPU runs, use one execution owner for GPU start, reservation,
collection, and cleanup; reuse the existing owner, task records, and lock.
Scheduling reminders may check status but must not become a second launcher.
Before a run starts, bind its stage/run ID and authorization version to source
and configuration identity, host/GPU/boot identity, process identity
(PID/start ticks and, where useful, PGID/cgroup/container), raw output path,
and START/terminal/collection records. Keep preparation, START, execution,
terminal state, collection, and validation distinct; without actual START, do
not record a run as executed.

Estimate completion from observed wall time for comparable model, coverage, and
component combinations. Keep ETA separate from the safety deadline and leave
time for collection, validation, cleanup, and handoff. Prefer actual completion
or failure events. If no callback exists, say that checks are scheduled rather
than event-driven; check short jobs near ETA and long jobs no more often than
about every 30 minutes unless completion, failure, resource risk, a near
deadline, or the user warrants an earlier check. Internal process safety
watchdogs remain active. Check original process identity and terminal evidence
before treating an SSH/tool timeout or missing old watchdog PID as failure, and
never restart solely from an ambiguous status. Keep unchanged status quiet.

When the user asks for a recurring check or follow-up event, inspect and update
the matching existing Codex heartbeat; record its real ID, state, interval,
stage, and terminal path. Do not hand-write scheduling files or create duplicate
events. Pause the matching check after delivery and verify the returned state.

For an authorized SASS/QKV run on the configured and verified target, first identify the
current vLLM process, start time, ownership, model command, container, and GPU.
Only stop the specifically authorized target; do not broadly kill processes or
stop shared cluster services. Recalculate reservation from the active vLLM
startup check, total and free memory, CUDA context, system use, and run peak;
historical 2/3/4 GiB values are evidence, not defaults. Keep a single finite
reservation process with no continuous work, and prepare its verified launcher
and watchdog before opening a GPU gap. Confirm the service has not reclaimed
the GPU before starting the authorized run. Record actual reservation,
watchdog, and worker identities and finite deadlines. Plan for roughly 5% free
GPU and RAM headroom where practical; label predictions as estimates and record
the observed post-start margin. Reuse a live reservation only after identity
verification; do not trust a stale ready file.

Renew a reservation only with a finite deadline and an authorization basis.
Within the current user's authorized SASS/QKV continuation scope, the coordinator
may choose reasonable finite renewals to cover the approved work and collection
without asking at every point. Record each renewal separately, including old
and new deadlines, reason, watchdog identities, unchanged reservation identity,
and completion time; preserve old receipts. For watchdog renewal, keep the GPU
reservation process unchanged: verify the old watchdog's PID/start ticks and
command, prepare the replacement first, stop the old watchdog and confirm exit,
then start a unique replacement tied to the same reservation. Verify the new
watchdog is alive with its finite deadline and actual monitoring log; ensure
preparation and failure handling cover the whole handoff and do not leave an
unbounded reservation. If the user set a total deadline, do not reset or exceed
it by replacing a watchdog without incremental approval. A reservation renewal
does not extend a worker/controller timeout or authorize more scientific scope.
Do not replace or detach timeout controls to evade their limit. At completion,
collect and validate the authorized results,
release this task's reservation/watchdog and leftover workers by verified
identity, record terminal GPU state, and stop its status check. Keep research
logic, reusable source, and launchers portable. The current execution host is
recorded in the task inventory, not a permanent machine restriction.

User clarification (2026-09-25): hostnames, machine/GPU UUIDs and similar
machine identifiers are observed provenance, not fixed acceptance gates or
constants that prevent migration. Discover the configured target and record
its actual identity for each run; do not require a historical machine UUID or
hostname. PID/start-time and current process ownership checks still protect
against operating on the wrong live process. Migration must validate actual
capabilities, environment and resource availability, not historical machine
identity. Preserve frozen running packages and historical receipts; apply this
rule to successor launchers instead of rewriting past evidence.

If automatic approval review rejects an action, do not perform that action or
switch scripts to bypass the rejection. If the rejection appears to confuse the
host or target, use read-only evidence to clarify identity and existing
authorization, then resubmit for review; if it is still rejected, report that
outcome. Never transfer a reservation procedure between servers
without verifying that it applies.

### SASS/QKV model delegation

For routine decisions and reviews within existing authorization, GPT-6 Astra
with medium reasoning is the decision reviewer. Execution subagents, when
delegation materially helps, may use GPT-6 Sol or GPT-6 Luna; do not assign
execution work to GPT-5.6 models. Keep one GPU execution owner, even when CPU
preparation or review is delegated. The coordinator may make authorized
finite reservation renewals without per-point confirmation. Ask the user only
for unresolved material choices that Astra cannot resolve from evidence, or
for changes at boundaries explicitly reserved for user approval, including new
PTX memory-rewrite or device-helper algorithms. This model policy does not
expand scientific scope, override a phase's GPU restrictions, or itself start,
stop, or renew a process. The current continuation and model-policy records are
in `rebuttal_20260921/sass-lifter-20260924/task/resume-giga-20260925-v1/`;
consult them with the matching `CURRENT_HANDOFF.md` for live task state.

## 实验元信息与用户确认（强制）

在任何大规模实验、正式论文候选实验、批量/多维参数扫描、跨设备批跑或新增 GPU 持续负载之前，必须先向用户展示实验预检单，并取得用户对该版本的明确确认。预检单至少包括：研究问题与假设、证据类型、代码与环境版本、器件/拓扑/几何/功耗/可靠性参数及来源、workload 和初始状态、扫描点与重复数、模拟时长和预计实际耗时、资源预算、对照与消融、观测指标、通过/失败/安全停止条件、输出位置及已知限制。用户沉默、历史泛化授权、代理自行生成的 approval 文件以及“开始前提醒过”均不构成批准。

将预检单保存为可读报告及机器可读 manifest，以实验 ID、版本及内容哈希绑定确认。未确认时状态必须为 PENDING_USER_APPROVAL，禁止提交或后台启动正式任务，禁止拆成多个“小批次”规避。研究问题、物理假设、输入、扫描范围、控制策略或资源预算超出已批准范围时，先停止受影响的后续任务，更新差异并重新确认。只读排查、隔离构建、单元测试和已限定的小规模软件验证可继续；不得把它们改名后冒充正式实验。详细契约见 docs/eq3_thermal/experiment_approval.md。

用户后续澄清：小规模工程验证可在逐次验证客观指标改善时持续推进，不机械
以启动次数作为停止理由。预先定义改善指标、精度/成本停止条件；启动失败与
实际数值配置分开统计，固定单元测试不占数值配置数。CPU/RAM/磁盘边界保留；
此澄清不批准正式批跑、参数扫描或GPU负载，也不得以拆小批次绕过上述确认。

## 参数闭合与两次冻结（EQ3）

先盘点配置、代码常数、CLI/环境默认和实际消费者；声明但未消费的字段不能用于
科学扫描。参数逐项保留原值/单位、规范化值、原件位置与哈希、条件、推导、转用
假设、允许域和缺失所影响的结论。SPECIFIED、MEASURED、DERIVED、PROXY、
SCENARIO_ASSUMPTION、UNKNOWN_BLOCKING、NOT_USED 不得相互冒充。

DESIGN_FREEZE 只冻结器件/拓扑、物理假设、参数允许域和标定方法；不虚构待拟合
参数的最终值。经用户确认具体有界标定版本后执行，独立验证后才 MODEL_FREEZE，
随后另行提交正式 EQ3 矩阵确认。方向性指令不批准任何标定批次或旧待批计划。
仅情景假设支撑的关键输入，其结论标 CONDITIONAL_SIMULATED。高影响未知只阻塞
依赖该项的结论；不阻塞独立就绪工作。达到预注册停止条件的数值检查停止细化。
新几何不能继承旧 fixture 的精度、步长或物理标定声明；看过的 heldout 不再当
下一次模型选择后的唯一盲测。确认绑定版本/科学输入哈希，禁止自己生成批准。

用户确认的过程要求：标定预检不以固定运行次数或首轮耗时估计作为科学停止上限。
以原始资料、推导、逐次误差与可辨识性审核迭代；达到目标停止，无改善转入诊断。
保留资源安全/任务隔离边界和具体版本的执行确认；此过程要求不是启动标定、矩阵
或GPU的授权。改变物理假设/输入域/拟合方法等须更新预检并重新确认。

## 四拓扑与复用约束

8HBF direct+物理GDDR、总stack8且数量可配的mixed-direct、4+4 relay、四对DASH
均必须交付；4HBM4+4HBF只为首例。配置覆盖、热验证、系统行为分别验收。
任何代码/工件复用先审查适用域与可靠性并记录证据；历史PASS不自动可复用。
逐层转换器从器件、几何、功率映射生成，不硬编码4+4/121/255/die数/节点顺序。
缺拓扑参数显式报错，禁止静默fallback。17组等权输入只属首轮，保留非均匀逐die
功率；HBM/HBF base独立热实体、自热和输出须有实际消费者。GDDR单列板级/冷却/
外存模型。首例原则接受不代表标定/GPU/矩阵授权，仍须具体执行版本确认。

最新用户修订：8HBF拓扑的GDDR在封装外，本轮封装热域不模拟GDDR，不要求PCB/
GDDR热参数来阻断封装thermal_only；仍保留physical GDDR身份和系统行为缺口。
用户明确调整内存边界为任务总计16GiB、单进程12GiB；其它CPU/磁盘/watchdog边界
不变，不构成研究求解或pilot批准。已授权隔离转换器开发及固定软件测试可继续。
## 已明确授权阶段的执行规则

对用户明确授权的整阶段实验，元信息确认单位为阶段范围、方法和资源边界，不是单个运行点。阶段内每点元信息必须在启动前持久化，代理完成依赖、身份、安全和科学范围检查后可连续执行，不再请求用户逐点回复。允许本阶段明确列出的等价工程修复、数值细化与复验；新hash不自动等于新科学授权需求。范围外的物理参数、输入/研究问题、方法或资源变化才需要重新确认。不得伪造逐点用户签名、绕过检查、拆任务规避预算或以自动授权掩盖数据失败。

当前EQ3-P2-LAYERED-CAMPAIGN-v1授权来源及不可变量见工作区eq3_thermal/plans/campaign-v1；旧逐点确认保留为历史。阶段内依赖/盲测锁定/16GiB任务与12GiB进程/600s/每点4GiB/任务20GiB/GPU0安全门槛仍生效。

## 当前 P2→P3→P4 连续执行授权

当前用户已授权 EQ3-P2-P3-P4-CAMPAIGN-v1 连续执行。每个子任务仍须在启动前
保存元信息并经过身份、依赖、科学域及资源检查，但范围内不逐点或逐阶段
申请确认。出错先自主检索、复现和修复；无法在范围内解决时只暂存受影响
分支，完成其他独立工作。范围外的选择进入待用户决断清单，不伪造批准、
不松科学阈值、不绕开安全启动器。P2未冻结时继续P3/P4显式工程fixture，
不得改称条件物理验证。实际授权与替代关系见eq3_thermal/plans/p2-p3-p4-campaign-v1。
围绕主线推进；无损字节全量验证用于首次链路或相关变化，不反复重验未变工件。

## EQ3 最小侵入修复规则（用户明确要求）

接口优先，默认关闭，最小侵入。
任何影响调度、事件顺序、请求/完成生命周期、资源所有权、
公共ABI、PTX/TMA/future/cache、既有时序或模块职责的重构，
实施前必须提交最小方案并取得用户明确确认。
不能用代码少、位于独立目录或为了修bug作为豁免。
用户未回复不视为同意；待确认分支暂存，其他工作继续。

本轮 EQ3-MINIMAL-REPAIR-v1 已授权已复现局部修复、语义透明且默认关闭的
接口适配和固定 CPU 验证；不是重构、科学方法变更、GPU、push/merge 的授权。

## EQ3-DECISION-EXECUTION-v2（当前明确阶段授权）

用户已采纳任务书D1–D6并授权连续执行：验收v2与legacy_v1并列、原train完整
1mm参考及现有后端数值验证、统一alpha域内分支、新可选升级优先策略与九点
CPU矩阵、默认关闭只读phase接口及既有gate真实CPU消费者。见
eq3_thermal/plans/decision-execution-v2/USER_TASKBOOK.md及user-source.md。
最新补充：取消固定内存/时限/磁盘硬边界，按每点实际需求、机器余量与对其它
服务影响合理评估并记录资源计划/停止条件；不再因原16/12GiB、3600/600s、
4/40GiB阈值机械阻断。仍串行CPU求解/实验、OMP/BLAS1，编译协调不争抢。
既有安全启动器须消费本阶段实际资源计划；保留系统余量和异常停止机制。
这不是GPU/云/新物性/新求解器/ROM/ABI/调度/所有权/真实维护生命周期重构授权。
已定范围不逐点请示；盲测依赖、原400K域、原结果及最小侵入确认规则仍生效。

## 基础 CPU 全链路补充授权（2026-09-20）

用户明确授权 HBM 时序、base SRAM 双缓冲和级联链路仲裁的基础简单实现，
用于快速闭合四拓扑 CPU 链路；先实现正确分配、有限缓冲回压与共享链路互斥，
不追求详细器件优化。默认关闭，复用实际 MQSim 提交/时间推进接口，后端完成
与封装送达分开，不重复计算原延迟。参数未经标定须标工程假设。该授权不含
原 MQSim 调度/事务所有权改造或真实 die 维护生命周期。授权原文见外层
eq3_thermal/plans/decision-execution-v2/basic-system-user-source.md。

## EQ3-ISOLATED-MAINTENANCE-CAMPAIGN-v1（最新明确授权）

用户采纳本阶段任务书并授权连续执行。温度模拟 MQSim 必须与原项目 MQSim 隔离：独立源码副本/补丁、构建、头文件、库、可执行与运行工件；原 third_party/mqsim、默认补丁/后端和生产 ABI/PTX/TMA/future/cache 不变。仅实验副本内批准任务书所列同一 engine/FTL/TSU/NAND 的窄维护身份、真实读写擦、有限目的分配、源版本保护、commit/fail 和逐页年龄计数；不批准完整重写调度/FTL/事件引擎。范围见外层 eq3_thermal/plans/isolated-maintenance-campaign-v1/USER_TASKBOOK.md。
保留原物理假设采用 CONDITIONAL_ENGINEERING_USE，不再为0.25K细化P2；旧失败与盲测锁定不变。授权v3原raw重评分、默认关闭读取率反馈、完整耦合热闭环、先pilot冻结再四拓扑可行矩阵及有依据消融/敏感性。逐点留元信息，无需重复请示。资源按每次实际需求与主机余量有限分配，旧固定上限不机械恢复。GPU0、云0、不删除raw、不push/merge。超出窄结构范围才另行确认。

## 完成通知与低频检查（用户最新要求）

实验运行优先通过已有完成/失败通知唤醒负责代理；没有通知能力时，根据已测
wall time 与剩余工作估算 ETA，到预计完成时再检查。不要逐波、逐秒或反复读取
未变化的状态来唤醒 AI。异常、资源风险及用户询问可提前检查。进程内部用于
看护自身作业的内存/磁盘/watchdog 检查继续保留；这种自动安全检查不需要 AI
参与，不能为减少轮询而取消。记录实际进程/会话与 DONE/FAILED 路径；不得把
尚未配置的通知机制描述为已经启用。

## 用户最新研究目标：速率驱动热模拟（2026-09-20）

主目标改为各HBF stack不同读取速率/通道活动/die分布引起的温度变化，不要求逐页真实读写。优先用既有完整耦合热网络的能量输入接口，避免为标称带宽扩写MQSim。原隔离事务矩阵按用户转向暂停，保留已完成57点、失败和未执行记录。
用户明确采用原80W/stack at1.6TB/s研究包络的条件模拟：array40pJ/B、base10pJ/B；未知idle不填成器件零功耗，只报告增量热。当前通道配置选OCP v0.7.0 Grade2，16channel/stack、96GB/s有效上限/channel、1.536TB/s/stack；能量系数不重标，因此满速76.8W。通道→die为单列工程映射，输入规定速率不得冒充实际后端吞吐。保留每die/base源与完整共享热路径；无新增源的die仍被动受热。热方程、材料、原数值失败、300–400K检查和盲测锁定不变。

## 速率热闭环继续执行（最新用户要求）

用户明确要求继续按模型大小构造读取压力并探索热节流；前两个短开环pilot不代表任务完成。按rate-thermal-control-v1预检连续完成固定测试、两拓扑pilot、范围内持续/等均值突发三策略对照和结果分析；每点留元信息，不逐点等待确认。保持原40/10pJ/B、OCP Grade2与完整耦合热模型；新增流体交付不得冒称真实MQSim/fabric吞吐。

## 四拓扑系统热闭环阶段（2026-09-20最新明确授权）

用户以PLEASE IMPLEMENT THIS PLAN批准EQ3四拓扑热闭环与逐栈读取性能计划。新隔离experiments/eq3_system_thermal复用已有MQSim维护后端和完整热网络，批准四拓扑批量服务、因果workload、可靠性情景代理、60点速率矩阵、指定敏感性和消融。按阶段保存元信息并在固定验证和pilot后连续执行，不重复逐点请求。现有原项目MQSim/默认后端、生产ABI及P2旧失败/400K/盲测边界不变。范围内新增实验模块设计已由本计划授权；超出范围的核心重构仍须确认。资源按实际pilot合理有限分配；GPU0/云0/不删除raw/不push或merge。

## HBFSim 哈希与逐字节校验的适度使用（2026-09-24 用户要求）

- 校验服务于具体风险和当前验收目标，不以增加检查数量作为严谨性的替代。先说明要排除什么错误，再选择最低充分的校验方式；不得因习惯而反复哈希、重复下载、逐字节比较或扫描全部文件。
- 哈希用于确认关键源码、配置、运行产物和传输文件的身份或完整性，不能证明程序语义正确、实际加载正确或输出正确。已有可靠哈希且文件未发生变化时，复用已有记录；只有文件变化、传输恢复、身份存疑或明确验收要求时才重新校验。
- 同一文件一次可信哈希比对已足以满足传输完整性目的时，不再追加逐字节比较。已有同哈希的本地资产可复用，不为换目录或补齐镜像重复传输。保留远端完整原件与清单即可支持追溯时，不要求把所有模块、缓存、重复二进制全部下载到本地。
- 本地优先收集与本次结论直接相关的输入、输出、参数、配置、候选产物、错误及终态日志。其余原件记录远端位置和保留状态；不能把“远端完整保留”“本地关键证据齐全”“全量本地镜像”混为一谈，也不能把最后一项擅自设为推进门槛。
- 输出正确性采用任务事先约定且适合数据类型的判据。确定性整数或token结果可精确比较；浮点结果按项目承诺与既定数值容差处理，不默认要求所有浮点输出逐字节一致。项目只承诺特定输入输出能力时，围绕该能力验证，不追加无关形式证明或扩大测试范围。
- 已明确批准的验收判据不能在看到失败后悄悄放宽；若判据本身不适合目标，应说明原因并明确记录调整，保留原始负结果。减少重复的身份/完整性检查不等于降低结果正确性要求。
- 检查完成且没有新变化、错误或未解决疑点时继续推进，不循环复核。收集及校验预算按必要证据的实际体量安排，避免为无关数据传输占用GPU、延长保留期限或阻塞已授权工作。

## HBFSim 接口、绑定与路径记录（2026-09-25 用户要求）

- 修改接入接口时，同步记录实际 C ABI/参数布局、返回码含义、调用先后、句柄与 context 生命周期、配置及 manifest/sidecar 路径关系；规划伪签名不代替已实现头文件。区分已构建、CPU 已验证、GPU 已验证和未部署。
- 一个运行版本的解释必须使用对应源码/构建收据，不能用另一候选版本的同名文件解释旧二进制。复制路径时同步核对精确来源 join；不得靠放宽匹配或删断言解决路径错误。错误码需沿实际调用链解释，不能把自定义码直接当 CUDA 错误码。
- 接口修复先定位身份、ABI、路径、绑定时序或生命周期层次，再做独立最小修改和直接相关验证；保留原失败，不机械追加全量测试、哈希或补传。实际地址、PID、句柄、期限只保存在各运行收据，不作为固定配置复用。
- 当前 SASS/QKV 接口记录入口：`/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924/reports/interface-binding-contract.md`。后续实现变更须同步更新对应条目及真实验证状态；文档不能替代运行证据。
- 多源码文件的局部重编译/重链接须记录「源码文件 → 编译命令 → 对象成员 → 实际基准 archive → 最终库」对应关系。不能因文件名相似而替换另一个编译单元；编译/链接返回 0 仍需核对受影响导出及动态解析，不把字符串存在检查当成完整接口验证。
- 启动接口须核对真实调用链和实际生效策略：动态符号拦截、Driver函数指针、Frida替换、runtime/Driver及Ex/PTSZ入口可能走不同路径，不能仅凭预加载顺序认定适配器被调用。局部strict检查与全局partial策略必须明确衔接，不能把配置中的名称相似当作策略兼容，也不能为新目标切换全局策略破坏旧路径。相关日志需要记录能区分实际入口的API/CBID及线程；缺失字段不得靠函数名猜测。

## HBFSim 改写正确与模型接入分层验收（2026-09-25 用户要求）

- 新内核至少区分三层证据：`LIFT_OUTPUT_PASS` 表示原生与恢复重编译后同输入输出正确；`INSTRUMENTED_KERNEL_PASS` 表示加入既有HBF插桩后的真实内核输出及地址/服务闭合；`MODEL_CONNECTED_PASS` 表示真实模型调度实际消费该权重并执行对应插桩函数，输出、实际storage地址、模块及服务闭合。前两层均不能计作模型覆盖。
- 记录每层的来源、工件身份、实际运行与失败层次。某层已通过且工件未变时复用该证据；若模型接入失败，应定位入口、ABI、参数、绑定、生命周期或服务连接，不持续重复“仅改写后运行”并把重复成功作为接入进展。
- 扩展原未覆盖权重时，先按真实算子/参数/内核差异划分类别，优先让所有尚未覆盖类别各有代表完成模型接入，再推广至各类全部目标层；已有代表成功的类别不抢在缺失类别前扩层。不能仅凭名称相似认为同一恢复内核能覆盖所有形状或消费者。全部扩展完成后，在最终组件上执行一次承诺范围的全覆盖回归。
- 全覆盖须明确目标集合、去重storage、字节区间和消费者范围；registration不等于instrumentation或modeled service。未触发/不支持的消费者单列，不能用受控内核成功、原生fallback或全局非零计数填充覆盖。保留已成功旧集合及原失败证据，避免无变化重复高成本实验。
- 具体数量、PID、期限和阶段结果只存任务交接，不固化进本规则。新增PTX访存改写或device helper算法变更仍遵守已有审批边界。

## HBFSim 各类权重接入对照复用（2026-09-25 用户要求）

- 各类权重维护可复用接入对照表，记录已成功版本的真实消费者、参数/metadata profile、去重storage注册范围、原PTX与staged PTX角色、manifest/sidecar引用及实际加载路径。后续同类扩层先对照成功记录，核真实差异；不得照抄历史地址或把普通指针、聚合参数及不同生成器的profile混用。接入故障与修复应回填对应类别，避免每层重复定位同一种错误。当前对照表见 `/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924/reports/weight-integration-matrix.md`。
