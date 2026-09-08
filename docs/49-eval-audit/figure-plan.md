# 新EQ图契约，2026-09-08

定义见[主规范](../49-new-evaluation-plan.md)，科学判定见[gates](claim-gates.md)，cell状态见[matrix](run-matrix.csv)。此处规定待产出图，不声称已实现。旧六图renderer/MOCK及[旧图契约](history/20260905/figure-plan.md)保持历史用途。新字段/panel/统计扩展是[独立补丁](minimal-followups.md)，完成前新图为BLOCKED_RENDERER。

## 旧新映射

| 新图/EQ | 旧图去向 | 轴与分面 | 数据与门 |
|---|---|---|---|
| M1/EQ1 原生锚点 | fig-e1 GPU部分 | actualW / native与目标exposed stall；L0/L1、warm/cold切片 | native/zero/old/deferred、SASS首use；NQ1/NQ2；mapped-host仅合法LDG域 |
| M2/EQ1 增量stall | fig-e2重构 | W(us) / DeltaS(us)，叠加[L1-W]+-[L0-W]+；issue_block/consume_residual/total_exposed附panel | 三种W区间、L0/L1/D定义、独立paired launches；consume小不隐藏issue付款 |
| M3/EQ1 并发争用 | fig-e1/e2扩展 | achieved MLP/QD/occupancy / extra stall误差与完成吞吐 | NQ3；held-out、register/spill/shared/访问/sector/queue；未知域BLOCKED |
| M4/EQ1 pipeline | fig-e2扩展 | actualstage/window/合法fanout / useful completion或exposed stall | NQ4；G→S/S→G/phase/group/descriptor，source fetch与fabric bytes分账；当前BLOCKED_RUNTIME |
| T1/EQ2 持续吞吐 | 新图 | 模型时间(s) / 完成有用bytes/window所得GB/s；应用门过后另画tokens/s | NT0–NT4；三臂身份/同初态/原始完成账本，不用admission代替完成 |
| T2/EQ2 hotspot | 新图，与T1同轴 | 模型时间(s) / layer热点T(°C) | shadow/active同模型；off无温度，不造平线；标threshold/hysteresis/触限恢复/Tmax/duty/uncertainty |
| C1/EQ3 物理容量 | fig-e3拆分 | r=C_HBF/C_HBM / slowdown或完成service；fixed-total/fixed-HBM与capacity-only/coupled分面 | NC1/NC3；profile几何/N映射；r非rho，新增容量未用可平线 |
| C2/EQ3 内部分配 | fig-e3拆分 | kappa/beta合法单纯形，颜色decode_norm或有用service；固定r/profile/thermal | NC2/NC3，真实KV/tile/stage下限；非法INFEASIBLE/缺实现BLOCKED，禁止跨不可行域插值 |
| W1/EQ4 路由机制 | fig-e4 | actualB / union、entropy/Gini、Jaccard、reuse distance | G8/G9，逐layer/step真实E/k，real/shuffled/uniform-null，null假设明确 |
| W2/EQ4 适用边界 | fig-e5 | actualB或有效驻留 / 5/10/20% slowdown服务边界 | G6–G10/NC；dense匹配与prefetch账本；纯预取匹配同demand并发 |
| A1/附录 | fig-e1 SSD部分、fig-e6 | storage/calibration/held-out及profile/coverage/traffic敏感性 | G3/G4；独立来源域与不确定性，不作主文物理HBF真实性论证 |

## T1/T2数据形状

配对键至少source/env/model/physics-inputs/ROM/geometry/cooling/initial-temperature/seed/raw-demand哈希和replicate；每臂完整package-profile SHA另外保存，不作跨臂相等条件。字段有time_ns、window_start/end、thermal_mode、offered/useful_completed/refresh/read-retry bytes、queue、policy/throttle/hysteresis/duty及单位；T2另有layer/hotspot位置与temperature_C。GPU/HBM/HBF energy分账。旧analyzer只合计media read/program物理bytes，不等于T1 completion useful bytes；此输出缺口阻塞正式T1。

固定arrival replay同原始arrival；live反馈可以改变后续arrival，须保留依赖与实际arrival并作为机制结果。off无温度；shadow为无反馈温度反事实。旧prototype stage=off仍算温度，保留旧名/语义，不画成新真off。缺臂图明确缺失，不称完整三臂。

horizon至少5tau+统计窗口，核对温度斜率、queue漂移与served/offered守恒。窗长采集前冻结且三臂相同，不平滑掉反馈周期。nominal与test-only stress分面，8Hi/16Hi独立geometry来源；replicate CI与参数不确定区间分开。合理参数无差异如实画重合曲线。

## 统计与拒绝规则

按[结果契约](result-schema.md)long-form与明确cell/replicate/context配对，不排序zip。缺配对/单位/source/hash/时钟域/profile/unknown说明拒绝；禁止跨model/profile/geometry/time_scale/thermal-mode暗中平均。兼容MEASURED/VALIDATED_MODEL/PROJECTED/MOCK并记录evidence_kind/validation_scope；HBF外推保留PROJECTED标签。

service_gbs由完成bytes/时间计算，不由renderer按N/tR推“测量”。1.05/1.10/1.20 contour只在可行有效数据范围有crossing时画；无crossing不外推精确边界。未实现轴不用旧rho/workspace换名。近零用绝对ns/us误差、偏差和分位数，不用MAPE。paired CI基于独立run/prompt，一次无CI；warp lanes/层/请求非独立重复。探索与held-out确认点区分；profiler与计时run分离，不把counter当逐条stall精确归因。

科研图导出PNG/PDF/SVG，单位/对数轴明确，marker/线型同时区分，描述性标题不预设结论。MOCK仅布局并强水印，不进正式输出；本轮不造新MOCK图填缺口。manifest记录输入hash、renderer版本/依赖、panel点数、replicate与过滤拒绝原因。

Thermal pair-key clarification: use shared `physics_inputs_sha256`/ROM/media/demand hashes; retain each arm's full package-profile SHA separately. The existing shadow/active package differs only by explicit `name` and `stage`, so full package SHA is not an equality join key. Exclusion rules are recorded; geometry/material/cooling/energy/threshold changes are never masked as arm differences.
