# P2 数值下一步筛选

日期：2026-09-20  
状态：**只读筛选；未生成输入、未运行求解、未修改代码**

## 建议

优先闭合当前系统：保留 2→1 mm 未达到 0.25 K 的
`NUMERICAL_ACCURACY_LIMIT`，P2 reference 继续标为未冻结；不要直接启动 0.5 mm
uniform 求解，也不要为追求 PASS 无限细化。完整 1 mm 已把早先的“前 4 s 不能代表
完整激励”缺口闭合，但完整 100 s 的 hotspot 最大相邻网格差仍为 **2.069 K**，所以
0.25 K 标准仍是失败，而不是已经接近到可据此预计下一层必过。

若论文结论必须依赖合格 reference，下一项应是一个明确的**数值路径选择**：

1. 继续 uniform 0.5 mm：科学序列最直接，且属于用户已授权的同方法数值细化；成本
   和可行性尚未知，可先做显式 guard、输入生成和 symbolic/resource pilot，再决定
   是否值得求解，无需把它虚构为新的用户审批阻塞。
2. 使用 native non-uniform/local refinement：可能降低单元数，但会改变网格序列、
   输入生成、传感器映射和误差解释，属于结构性数值方案，不能作为现有 uniform
   0.5 mm 的便宜替身。

从“快速获得完整系统”的目标看，推荐选择是**当前停止细化并保留失败状态**；
只有需要解除 P2 reference 限制的具体结论时，再从上述两条中选择；其中新方法的
non-uniform/local refinement 仍须明确确认。

## 已完成求解的资源事实

以下均为同一 100 s train、20 ms step、63 个 z slab 的实际完成运行。内存以 solver
自身 stdout 的 peak 为主；运行收据的 PID-only sampling 明确只是 lower bound。

| 运行 | uniform 网格 | cells | wall / emulation | factorization | solver 报告 peak |
|---|---:|---:|---:|---:|---:|
| R02，2 mm | 32×32×63 | 64,512 | 237.649 s / 236.776 s | 6.772 s | 1.17 GB |
| R03-V2-FULL1MM，1 mm | 64×64×63 | 258,048 | 2,759.005 s / 2,753.602 s | 251.894 s | 7.30 GB |

R03 完整运行是在本轮该实验分配的 3,600 s watchdog、12 GiB 单进程、16 GiB 任务
配置下完成的。外层 `solve-operational.json` 对 descendants 采样得到
`7,735,140 KiB`（约 7.38 GiB）任务峰值，与 solver 自报 7.30 GB 相互支持。内层
`DONE.json` 的约 43 MiB 只是 PID wrapper 范围的 lower bound，不能代表完整进程树。
此前 R03-OWNED-PILOT4S 的 4 s prefix 也不能代替完整资源或完整激励结论。

2→1 mm 时 cells 增加 4 倍，实际 emulation 约增加 11.6 倍、solver peak 约增加
6.2 倍，factorization 增幅更大。这只表明当前稀疏分解和求解成本呈明显非线性，
属于 **INFERRED 的风险提示**。稀疏 LU 的 fill、排序、矩阵结构和内存峰值都可能随
网格变化；不能把这些倍率线性或按固定幂次外推到 0.5 mm，也不能据此断言必然 OOM。

## 0.5 mm 当前阻点是什么

0.5 mm uniform 网格为 `128×128×63 = 1,032,192` cells。当前
`tools/eq3_layered_export.py::discretize()` 在实际构造 cell list 前使用固定
`400000` cell safety guard，因此该输入会在静态导出阶段明确失败。

这条 `400000` 是导出器自己的显式软件防护，用于要求先审查生成内存；它不是 native
3D-ICE 的已知 cell 上限，也不是用户曾使用的 12 GiB 单进程限制。当前用户规则已经
改为按实验评估资源，R03 使用 12/16 GiB 是该次实验的具体分配，不能把旧数值当成
0.5 mm 的永久硬上限。反过来，取消固定上限也不会自动证明 0.5 mm 可运行。

若选择 uniform 路径，最小 guard 方案应是把常数改成**默认仍为 400000 的显式参数**，
并在创建百万个 Python cell dict、floorplan map 和 sensor map 之前先计算 shape/cell
count、估计输入与派生文件、核对实时可用 RAM/磁盘，再由绑定的 point scope 提高值。
这只是解除软件前置拒绝，不构成 0.5 mm 资源可行性证据。参数化 guard、静态生成和
有停止条件的 resource pilot 属于已经授权的同方法数值细化范围；本报告因主线优先及
当前信息价值暂不启动它们，而不是等待新的用户批准。

## native 与当前生成链的 non-uniform 能力

静态源码证据显示，固定的 3D-ICE tree **确实包含 non-uniform native 路径**：

- scanner 接受 `non-uniform`；自带 `example_transient_nonuniform.stk` 使用
  `non-uniform true` 和 stack-element `discretization x y`；
- `thermal_data.c` 构建 `Cell_list`、`Cell_pointer` 和 non-uniform connections；
- `system_matrix.c`、`layer.c`、`power_grid.c` 与 inspection/output 路径都有
  non-uniform 分支。

但当前 EQ3 reference producer **没有接入这项能力**。`eq3_layered_export.py` 对
reference 使用一个全局 uniform x/y pitch，要求所有实体边缘落在该网格上；生成的
`dimensions` 没有 `non-uniform true`，stack 中也没有输出逐元素
`discretization`。`axes(mesh_m=None)` 的 geometry-edge Cartesian 网格当前用于独立
RC 路径，不是 native reference 输入。因此准确状态是：

`NATIVE_CAPABILITY_PRESENT / EQ3_REFERENCE_PRODUCER_NOT_CONNECTED`。

“nonuniform power”测试只证明每组件/组功率权重可以不均匀，不能证明 reference
使用了局部空间网格。

## local refinement 的收益与结构改动边界

局部细化可把 0.5 mm 放在高梯度、发热实体边界或已观察 hotspot 周围，其余区域保持
1/2 mm，因此有机会少于 1,032,192 cells。这里的收益只是 **INFERRED**；尚无由目标
几何生成的 cell count、fill-in、运行时间、内存或误差数据。

这不是修改一个 mesh 数字即可完成。最小完整方案仍需：

- 给 normalized geometry 生成确定性的逐 stack-element x/y discretization，并禁止
  snapping、重叠和漏填；
- 让 native non-uniform cell identity 回写到 source weight、component ownership、
  mean/hotspot sensor、边界能量和 checkpoint/输出读取；
- 验证总体积、热容、源能量、界面导热和 Robin 面积守恒；
- 固定 refinement rule，不得看完候选输出后追逐 hotspot；
- 重新定义比较：local mesh 不是 uniform 4→2→1→0.5 的同一序列，不能把结果直接
  填入旧 Richardson/相邻 uniform 收敛阶。

这些改动影响数值网格生产者、native 输入、输出映射和误差方法，属于结构性数值方案；
实施前应提交最小设计并取得确认。native 有相关代码只说明方案可调查，不说明当前
EQ3 链已经可用或正确。

## 0.25 K 标准与实现正确性必须分开

旧的空间标准是相邻 reference 网格在固定传感器、固定时刻上的最大绝对差不超过
0.25 K。保留该标准是合理且保守的：温控边界和 hotspot 风险以绝对 K 判断，最大值
不会被大量低误差 mean 传感器稀释；它也符合预注册要求，不能因失败后再改阈值。

同时要承认它的含义有限：不同网格的 hotspot 是同一物理组件内各自最热 cell，cell ID
和中心位置可能变化。这个 maximum-of-component observable 对局部解析度敏感，不是
同一点场值，也不能用于全局最大值 Richardson 阶估计。mean 是体积加权平均，通常更
平滑，必须与 hotspot 分组报告，不能用较小 mean 误差覆盖 hotspot 失败。

完整 2→1 mm 同窗诊断进一步显示这种差别：

| 100 s 分组 | 全组平均 time-weighted MAE | 最大 registered absolute error |
|---|---:|---:|
| mean（129 sensors） | 0.0253 K | 0.7976 K |
| hotspot（138 sensors） | 0.1043 K | 2.069 K |

最坏 hotspot 是 `component:hbm3.base:hotspot`，15.0 s，2 mm 为 314.966 K、1 mm
为 317.035 K；最坏 mean 是 `component:hbm2.base:mean`，同为 15.0 s，差
0.7976 K。两组都超过 0.25 K 最大差标准，只是 hotspot 更敏感。

静态审计已核对 geometry/material ownership、单位、半 cell 界面导热、Robin 边界、
source weight、层/输出顺序和 sensor reduction，未发现可解释该差异的映射错误。这是
**实现正确性证据**，不是空间收敛证明。反过来，即便以后 0.5 mm 达到 0.25 K，也只
缩小离散误差；材料、接触、功率、边界、几何和目标器件参数的不确定性仍属于总体
物理误差，不能由网格 PASS 消除。

## 具体决策点

| 选择 | 得到什么 | 代价/边界 | 建议 |
|---|---|---|---|
| A. 当前闭合 | 保留完整链路和诚实的 reference 未冻结状态 | 依赖绝对热点精度的物理结论继续受限 | **推荐**；最符合快速闭合，避免无信息的持续优化 |
| B. uniform 0.5 mm | 保持原相邻 uniform 方法 | 1,032,192 cells；需显式 guard 和生成/resource pilot；成本未知 | 已在同方法细化授权内，但当前信息价值不足，暂不启动 |
| C. native local mesh | 可能以较少 cells 增加热点附近解析度 | 需新的生产、映射、守恒与误差方法；不能回填旧 uniform PASS | 作为单独结构性数值方案，不在本轮直接实施 |

因此当前报告的可执行结论是：**A，保持 0.25 K FAIL 并闭合其余系统**。B 是已授权
范围内的可行后续，出现明确论文结论需求时可用一次有停止条件的 resource pilot
启动；C 改变网格方法和映射，仍需明确确认。两者都不应演变为无停止条件的自动细化。

## 证据位置

- R02：`eq3_thermal/plans/campaign-v1/points/R02/runs/R02/{DONE.json,stdout.log,generation_receipt.json}`
- R03：`eq3_thermal/plans/decision-execution-v2/points/R03-V2-FULL1MM/runs/R03-V2-FULL1MM/{DONE.json,stdout.log,generation_receipt.json}`
- 完整空间诊断：`eq3_thermal/plans/decision-execution-v2/points/r03-spatial-full-v2/spatial_diagnostic.json`
- exporter：`tools/eq3_layered_export.py`
- native：`eq3_thermal/reference/build/3d-ice-stock-e0bb685/{flex,sources,include,bin/example_transient_nonuniform.stk}`
- 先前静态审计：`docs/eq3_thermal/P2_4MM_2MM_STATIC_DIAGNOSTIC.md`
