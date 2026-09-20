# 模型作用域与两次冻结

实施更新：通用转换器/固定验证和首例静态双导出已完成，详见CONVERTER_VALIDATION。
参考与RC均未对新封装求解；仅R01参考pilot具备执行包，整套矩阵尚未READY。
物理假设未修改；RC显式3087节点为待确认的新数值版本，未偷偷沿用≤512承诺。
最新用户决定：封装外GDDR不模拟热域；内存16GiB任务/12GiB进程，不授权执行。

状态：首个4+4样例 PRINCIPLE_ACCEPTED_WITH_REQUIREMENTS；设计范围修订v3。
MODEL_FREEZE未发生；执行仍 PENDING_USER_APPROVAL。完整范围以
FOUR_TOPOLOGY_COVERAGE.md、LAYER_CONVERTER_CONTRACT.md及topology_scope.json为准。

最新过程修订：CALIBRATION_PREFLIGHT_v2.md取代v1机械次数上限。10点/856模拟秒
是首轮清单，不是总次数上限；按官方资料、推导、误差和可辨识性审阅迭代。
CPU累计耗时估计改为复核节点，不作为科学停止理由；资源安全和执行版本确认
仍保留。没有新的标定/GPU/矩阵启动授权。

## 实际代码状态

本轮开始HEAD `f1eb88d18627665abf3359d605205d52c1931c73`，分支`eq3-thermal/p1`；
定向只读GitHub核验`eval_base=5d5e9602b41b17bd398a29a54dc3cb88b705d22f`，本地ahead4、behind0。
开始时tracked/untracked均干净；本轮只增加/修改规则、审计和候选输入，不改核心。
原始baseline `5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5` 保持不变。

| 能力 | 实际状态 |
|---|---|
| 独立RC、interval energy、模拟sensor、真off/read_only、checkpoint | P1软件测试通过；不是物理标定 |
| 40mm homogeneous3D-ICE数值案例 | NEW_REFERENCE工具链；非逐die封装 |
| 固定1mm时间检查 | 达到停止条件：最后区域max0.175K/grid热点max0.232K；不得继承到新几何 |
| 当前RC | FAILED，未通过1K/2K目标；无调参掩盖 |
| 历史原floorplan/trace/golden/ROM矩阵 | HISTORICAL_NOT_ACQUIRED/PARTIAL_RECOVERY；保留收据，不能复制八份 |
| 候选逐die数据 | 本轮已生成255块、121有源区、17输入组；静态一致性通过，不是求解结果 |
| 新几何到3D-ICE/RC转换器 | NOT_IMPLEMENTED；现有工具硬编码两层，不能直接吃新JSON |
| P3只读/shadow真实运行时连接 | NOT_IMPLEMENTED |
| P4控制/真实维护闭环 | NOT_IMPLEMENTED |
| P5 Safe/Near/Stress与token结论 | NOT_RUN；审批与能力均未满足 |

原12个数值配置+3个求解器前失败保留，不增加本轮静态检查到数值配置计数。
旧四点EQ3-P2-REFERENCE-REFINEMENT-v1/hash55049cdb…e566仍待批，只能诊断旧案例
空间误差；不是新研究模型的前置硬门槛。本轮不运行该计划。

## 候选主线与备选

推荐A：产品组织锚点+明确研究封装，输出CONDITIONAL_SIMULATED；现在可以准备
逐die参考，不能得出实物绝对温度/寿命。B：等待产品drawing/coupon数据，可提高
物理可信度但没有已知取得时间；只阻塞相关实物claim。C：回到旧40mm案例只能做
回归，不能完成新目标。因此建议A推进并并行补B，不再以C为主要研究工作。

主线4HBM4(12H)+4HBF(16die)、mixed-direct；64mm方形封装，24mm方形compute，
八个12×16mm研究热源。HBM4是Micron组织锚点，HBF是Sandisk目标，OCP外形仅代理。
逐层材料与坐标见candidate_profile.json。数据图与热图分离；同器件重接线不自动
改变热坐标。共享lid/interposer须保留横向热自由度，不能再次做单个等温sink。

强制交付（前三行其它拓扑与mixed-direct同属必交范围，不是可选扩展）：

| 必交拓扑/配置与规格后续域 | 与首例的差异/前置条件 |
|---|---|
| 8HBF direct + GDDR | 物理快存是GDDR，另需board/cooling/PHY；不是“HBM换标签” |
| mixed-direct其它HBM/HBF分配 | 总数8；当前P1默认2+6与候选4+4不同，不能静默复用 |
| 4+4 relay | HBM base转发、共享HBM/NAND/TSV上限及能量需明确；常规HBM4不自动支持 |
| 4对DASH | USER_CONFIRMED名称/拓扑扩展；两条路径不能把同一供给乘二；原论文2对不能当4对实测 |
| OCP384/1536/3072GB/s | 三独立profile；最大高度8/16/16，尚需实际组织/容量选择，不是Sandisk1600 |
| HBM48GB16H | 重新推导高度/TIM/容量、参考与快速模型；不是只改die_count |

## 冻结契约

DESIGN_FREEZE：确认器件、代理/假设、允许输入域、构造/拟合方法、验证和资源上限。
本次候选冻结物性为指定案例值、拟合数0；并不声称它们是唯一真实值。
先完成隔离的新几何输入适配、静态/单位/能量/映射测试；不碰现有运行时。
随后把真实可执行入口、源码SHA、全部输入与环境hash绑定到标定执行版，再取得
该版本确认。当前文档不能冒充已经有可执行新几何生成器。

MODEL_FREEZE：新参考离散检查、同物理观察量RC误差、能量守恒及全新盲测全部
满足后冻结模型/适用域。失败保留，不加未批准拟合/ROM候选。之后另提闭环pilot
及正式EQ3矩阵批准。标定批准从不自动批准GPU/主矩阵/云费用。
