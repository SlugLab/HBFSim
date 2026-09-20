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

当前用户已授权 EQ3-P2-P3-P4-CAMPAIGN-v1 连续执行。每个子任务仍须在启动前
保存元信息并经过身份、依赖、科学域及资源检查，但范围内不逐点或逐阶段
申请确认。出错先自主检索、复现和修复；无法在范围内解决时只暂存受影响
分支，完成其他独立工作。范围外选择进入待用户决断清单，不伪造批准、不松
科学阈值、不绕安全启动器。P2未通过时P3/P4仅可报告显式工程fixture，不造
MODEL_FREEZE。真实授权见工作区eq3_thermal/plans/p2-p3-p4-campaign-v1。
围绕主线推进；逐字节等价性用于首次链路或相关实现变化，不反复全验未变工件。

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
