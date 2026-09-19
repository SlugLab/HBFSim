# 复用与 base-die 审计

审计基点：00b860854ea79534fcef9c843218cf5d186118ca；结论 DOC_DERIVED。
本报告是源码/输入审计，不是新的热求解或器件标定结果。

## base die 是否模拟了？

**P1 有独立 base 热节点并参与求解；研究逐层候选有独立 base 实体与功率输入，
但尚未接入新逐层求解器。真实 base 功耗/转发行为尚未完成。**

`tools/eq3_thermal_config.py` 对每个 HBM/HBF 生成 `*_base`，以热边连接
interposer 与第一层 array die；有独立热容。relay/dual 的合成转发激励落在
HBM base。`src/eq3_thermal/thermal.cpp` 的 `advance_to` 按所有节点求解，并将
每个 NodeEnergy 分配给对应节点；base 不是仅画在图上的标签。
但 P1 base 热容0.3 J/K、导热及激励为 fixture，不是物理规格；direct 默认
活动只激励第一层 array，不代表已覆盖 base 自热。

研究 `candidate_profile.json` 明确包含4个 HBM base和4个 HBF base，各自50μm、
12×16mm（可旋转），硅 k=150 W/(m·K)、rho=2330 kg/m³、cp=710 J/(kg·K)，
每个有独立 `power_group`。这些是已标注代理/研究假设，不是官方 base 图纸。
由此每个 base 的 C=rho·cp·V=0.01588128 J/K，整片单独垂直 R=t/(kA)
=0.001736111 K/W（不含接触/扩散/其它层，不是整包热阻）。当前 base 16W
激励上界同样是假设，不能从这些值倒推出真实 PHY/控制器功耗。

因此不应重复添加已有 base；应补齐 **实际消费者与功耗语义**：

1. 转换器显式 component_role=base_die，生成独立实体、热容、上下热边和稳定 ID。
2. base 自热独立于 array；输出每 base 温度和能量，同时保留每 die/stack/GPU 输出。
3. base-only 软件激励验证自身升温、向相邻 die/互连传热和能量守恒；不能只检查计数。
4. 将 controller/PHY/relay 操作映射到有来源的 base 或其它组件；HBF 阵列层中
   CMOS/CBA 与 base 逻辑分工 UNKNOWN，禁止全部假定在 base 或重复计能。
5. 无直接 base 功耗证据时保持 SCENARIO_ASSUMPTION，不能声称实物校准；relay
   所需自定义 base 功能及其热/功率变化必须单独声明。

当前1–3的研究转换器消费者未实现，4–5未闭合；列为必须补齐，不能报告完成。

## 拟复用组件逐项判断

| 模块/工件 | 审查结论 | 允许用途 / 复用前验证 |
|---|---|---|
| P1 ThermalNode / NodeEnergy / advance_to | CONDITIONAL_REUSE：动态节点列表、独立能量输入；非均匀逐die表达可用 | 仅独立求解接口，先检查单位、时间、能量守恒、节点重排/缺失/重复及边界；不改 ABI；未证明新几何精度 |
| P1 group sensor | ADAPT_REQUIRED：算术均值不适用于任意厚度/体积 | 新输出显式 mean 权重与热点定义，对齐参考观察量；不能直接拿旧均值作物理对照 |
| eq3_thermal_config.py | FIXTURE_ONLY：有可配置计数、base与数据/热图分离，但用统一fixture C/G/激励，GDDR仅单节点 | 复用分层配置理念，不直接搬其默认物性/配对命名/旧fallback；需独立严格适配 |
| candidate_profile/calibration_power | FIRST_EXAMPLE_INPUT：255/121/17仅这份数据的统计 | 保留证据分类；由通用器件/几何/映射生成，不把统计当格式限制；等功率分配显式可替换 |
| eq3_parameter_check.py | EXAMPLE_REGRESSION_ONLY：硬检查121/17、die数、层高；assert可被-O禁用 | 保留首例回归，不作为通用输入守门；新校验须显式异常和机器可读缺口 |
| eq3_reference.py / eq3_reference_followup.py | NOT_REUSABLE_AS_NEW_CONVERTER：旧两层/等温简化与新输入不匹配 | 仅保留旧案例和失败/时间收敛收据；不能导入新封装冒充已适配 |
| 历史 ROM/floorplan/trace/golden | NOT_ACQUIRED / PARTIAL_RECOVERY；无完整实物金标准 | 按 HISTORICAL_SUITABILITY_AUDIT；不拷贝单HBF模型八份，不继承历史PASS |
| stock 3D-ICE 及 MFIT材料来源 | REFERENCE_ONLY / PROXY，身份已记录；不证明 HBM4/HBF 参数 | 新输入需检查层序、材料单位、z源映射、边界、能量输出；原求解器成功不等于转换器正确 |

以上是有限范围复用审查，不是整库可靠性认证。每次实际复用记录模块SHA、目的、
适用前提、发现的问题、修正与最小测试结果。不得仅因“以前PASS”免审。

## 本轮验证收据

2026-09-19：34项既有Python固定测试通过；P1核心CTest 1/1通过；git diff --check通过。
只读静态检查确认scope中四个ID与现有四拓扑配置一致、均为required且未提升热/
系统状态；候选8个base均具独立同名power_group。scope JSON目前是设计清单消费者，
不是已实现的运行时拒绝器。新求解器运行0次，GPU负载0；基线SHA与干净状态保持。
