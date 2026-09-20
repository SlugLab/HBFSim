# 温控目标与器件依据：2026-09-20 复核

USER_CONFIRMED：用户关注持续稳定、高效读取，希望核对 ECC 等高温开销与温控的因果关系。
这是一项解释与模型缺口审核，不自动授权新增温度错误率曲线或修改 MQSim 完成时间。
当前实现的精确行为见 THERMAL_CONTROL_CURRENT_IMPLEMENTATION.md。

| 原始资料 | 直接支持的事实 | 不能据此推导的结论 |
|---|---|---|
| [OCP HBF v0.7.0 §9.2，pp106–108](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf)；本地冻结同版原件 | Normal/Light/Severe/Shutdown；轻度降功耗、严重背压、滞回恢复；表脚注保留维护；在途命令须完成或返回规定错误 | 不能给出某温度下的确定带宽、ECC 迭代数或通用产品阈值 |
| 同版 §5.3，p57 | 高可纠正错误或 UECC 可触发 Base/host 读重试 | 不等于所有高温读取都会多重试，也不是延迟分布标定 |
| 同版 §9，p106 | 运行结温范围0–105°C；85°C、24h是带条件的数据保持指标 | 85°C不能直接充当节流门槛；当前400K数值物性域不等于产品安全上限 |
| [Sandisk HBF Fact Sheet, July 2025](https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf) | 给出高温稳定性设计目标及封装导热改进 | 没有公开 T→RBER→retry→延迟曲线；不能凭宣传目标证明本模型校准，也不能假设HBF必然高温读速崩落 |
| [Sandisk CloudSpeed Gen II, Rev6, p14](https://downloads.sandisk.com/downloads/ess/cloudspeed2-product-specs.pdf) | 特定SSD以寿命保护为目的，65°C进入写节流，63°C退出 | 写节流阈值不可直接转为HBF读阈值 |
| [OCP Datacenter NVMe SSD v2.6 §9.2](https://www.opencompute.org/documents/datacenter-nvme-ssd-specification-v2-6-2-pdf) | 过温保护、足够频率监测与防传感器误触发 | 不能说明节流必须由ECC开销触发 |
| [Park et al., ASPLOS 2021](https://arxiv.org/abs/2104.09611) | 160颗3D NAND实测支持读重试增加延迟，ECC能力与重试优化相关 | 所测芯片不是当前HBF；其系数不可无条件转用 |
| [Luo et al., HeatWatch, HPCA 2018](https://research.ece.cmu.edu/safari/pubs/heatwatch-3D-nand-errors-and-self-recovery_hpca18.pdf) | 温度、保持时间、磨损和恢复历史共同影响错误与合适的读参考电压 | 不能用仅含当前T的单调错误模型覆盖所有情况 |
| [AMD Family15h BKDG, register D18F2xA4, p414](https://www.amd.com/content/dam/amd/en/documents/archived-tech-docs/programmer-references/55072_AMD_Family_15h_Models_70h-7Fh_BKDG.pdf) | 一个实际DRAM控制器可由温度告警触发双倍刷新及命令总线节流 | 是机制范例，不是HBM4刷新参数或ECC延迟曲线 |

## 正确区分的因果关系

1. 器件温度接近限制 → 保护策略主动降低活动 → 吞吐下降或背压。
2. 温度/保持年龄/磨损/温度历史 → 错误风险 → 纠错、读重试或维护开销 → 延迟及有效带宽变化。
3. DRAM温度 → 保持约束及刷新占用 → 可服务时间变化；不能与NAND重读混为同一机制。

这三条链可同时存在；当前代码主要具备第一条的工程闭环，不具备后两条的真实器件标定。
“ECC增加”应具体化为错误数、解码迭代/时长或读重试次数，而不是把固定ECC位数随温度增加。

## 最小后续方案（建议，未改变科学输入）

先保留现有安全状态机作为兜底，采用目标温度带而非单点保证。评价持续交付字节率、
p95/p99时延、时间窗带宽波动、未完成请求、维护积压及可靠性约束；排队时间必须计入。
控制阈值需结合器件允许温度、控制延迟和热惯性余量，不能由现有低温fixture阈值迁移。

第一步只增加已有事实的报表：逐stack温度、交付率和尾延迟，以及后端实际能提供的
错误/重试计数；没有计数就标UNKNOWN。第二步若获得对应HBF原始数据，冻结有来源的
温度/年龄/磨损查表模型和允许域；缺资料时仅可另设明确的SCENARIO_ASSUMPTION。
不能为展示温控收益，预设一条高温性能下降曲线再把结果称为实测规律。

真实重读、纠错和维护若需消耗MQSim媒体/通道资源，必须先给出原调度接口与最小
生命周期方案并取得确认；禁止在最终完成时间上额外拼接一段“ECC延迟”。当前基础
HBM/fabric实现继续推进，不因这项科学缺口暂停独立功能验证。

D3的380K是已授权的数学研究域包络筛选值，并非产品温控门槛；它甚至略高于上述
105°C运行上限。因此DOMAIN_V2数值通过不能称为符合HBF产品热规格。
