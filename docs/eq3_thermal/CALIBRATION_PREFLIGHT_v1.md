# CALIBRATION_PREFLIGHT v1：逐die研究封装

ID：EQ3-P2-PERDIE-CALIBRATION；v1；PENDING_USER_APPROVAL。
执行就绪性：**BLOCKED_IMPLEMENTATION_AND_EXECUTION_BINDING**。
这是具体设计预检，不是可立即执行的批准记录。设计确认后先完成独立输入转换与
单位测试，再给出包含实际命令/源码SHA/生成输入哈希的执行版；执行版另须确认。
现有eq3_reference_followup.py只支持旧四点，不得用其命令冒充本计划入口。

## 研究问题与证据

在候选64mm、4HBM4+4HBF逐die/共享散热结构及指定材料/边界下，几何构造的
现有RC引擎是否达到同观察量1K平均/2K热点误差？负结果允许。不是标定真实
Sandisk结温，也不是活动→功耗验证；证据为NEW_REFERENCE和CONDITIONAL_SIMULATED。
先直接构造C/G；v1拟合0项、优化0次、ROM0个。若需要拟合/空间改进，先诊断并
另交范围明确的新设计，而不是偷偷增加参数搜索。

## 输入与版本

机器输入：`configs/eq3_thermal/research/{candidate_profile,parameter_registry,calibration_power,calibration_plan}.json`。
代码起点f1eb88d；stock3D-ICE e0bb685，当前binary SHA240b598c6c8fe1f19db2d945c34596db4a14df7f589bc2f205d74104927244a7。
环境eq3-thermal-reference-v1，GCC15.2.0/OpenBLAS0.3.32/Python3.14.4，私有重建记录不变。
输入精确哈希随本轮工作树外DESIGN manifest保存；未来生成器与科学输入一同绑定
执行版。不存在的输入/命令不填假哈希或宣称READY。

每个die/base坐标、厚度、材料和传感器都在profile中。255块加剩余区域underfill，
121有源区域；GPU+8array+8base为17独立功率组。array组内各die等W（不是等体积
功率密度）；只覆盖此输入子空间。所有功率由文件给定，不读真实GPU，不含未知
idle/PHY重复叠加。HBM/HBF热源不是已实现的命令功耗。

源幅值：GPU≤200W，每memoryarray≤64W、base≤16W。80W/stack只是包住
HBM3E代理在1.6TB/s下约73–75W动态量级的合成激励；不是真实HBF上限或分账。
初始/上下环境均300K。所有输入.5s槽，观察.1s。材料常系数域300–400K为研究
假设，超过即停。各组幅值允许域不能理解为所有组合均物理可行。

| 整轨迹 | 精确输入构造 | 时长/能量 | 分工 |
|---|---|---|---|
| train | 17组依次独立4s：.5s槽倍率[0,.25,0,1,1,1,0,0]；最后32s全零 | 100s/1365J | 构造/数值检查；17列独立，不等于121列可辨识 |
| development | 文件中固定异步占空比、混合/热点和最后32s冷却 | 64s/9632J | 误差诊断与模型接受；不能冒充盲测 |
| new_blind | LCG32种子20260920，倍率[0,.2,.6,1]；最后32s冷却，完整值预先保存 | 64s/12152J | 模型字节锁定后才求解/查看；模型改动则不再作唯一盲测 |

这些不是LLM trace。旧heldout已看过，只保留诊断，不复用为本轮盲测。
静态C/G约3.625s支持32s冷却作为初始量级选择，但不是慢模态证明；报告有限时域
结果，无稳态已收敛claim；若尾段不足如实报告，不无界延长。

## 点表与顺序（最多10次，不自动重试）

| ID | 引擎/轨迹 | xy网格 | dt | 模拟时长 |
|---|---|---|---|---|
| R01 | reference/train | 4mm | 20ms | 100s |
| R02 | reference/train | 2mm | 20ms | 100s |
| R03 | reference/train | 1mm | 20ms | 100s |
| R04 | reference/train | 1mm | 10ms | 100s |
| R05 | reference/train | 1mm | 5ms | 100s；仅20→10ms差仍超目标时 |
| R06 | reference/development | 1mm | 选定10或5ms | 64s |
| F01 | current RC新几何/train | ≤512nodes | 同参考 | 100s |
| F02 | current RC新几何/development | 同上 | 同参考 | 64s |
| R07 | reference/new_blind | 1mm | 同参考 | 64s；模型锁定后 |
| F03 | current RC新几何/new_blind | 同上 | 同参考 | 64s；模型锁定后 |

7参考+3快速=10次上限，累计目标模拟856s；跳过R05则9次/756s。
每点1次，确定性重复不生成物理置信区间。旧案例数据不替代任何点。
4/2/1mm下分别16128/64512/258048cells；63个z切片由边界并集计算。
稀疏填充内存可能成为真实停止条件，不以“单元数不多”保证可运行。

## 有界规则与资源

R01先作真实新几何资源pilot；R02前按8×R01峰值+0.5GiB保守预测，须≤5GiB。
R03前用max(8,R02峰值/R01峰值)×R02峰值+0.5GiB，须≤5GiB。超出即
RESOURCE_BLOCKED，不把2mm冒充已通过1mm空间检查。此预测只是保守启动条件，
每进程仍硬限6GiB，任务总8GiB。

全部串行、OMP/BLAS1；最多4总编译线程且不与实验并行。新CPU总上限3600s、
每点墙钟600s、阶段墙钟5400s、新输出4GiB、GPU0、云费用0。参考和runs现有
约568MiB，本轮新增公开原件约11MiB；既有环境/源码另占空间，保留总新增20GiB
边界。墙钟估计10–60min仅资源规划，不是对新多层案例的实测预测；首次资源
pilot决定后续是否可行。不得影响其他vLLM服务。

三空间级别报告差异趋势而非连续解证明；R02→R03所有区域/热点max差≤0.25K
才继续时间检查。20→10ms差≤0.25K则停止细化，否则只允许10→5ms一次；仍不
满足就停。禁止自动新增.5mm、2.5ms、拟合候选或热参数点。

## 验收、失效与输出

保留1K/2K绝对目标；扩展到逐有源区MAE≤1K、真实grid热点max误差≤2K，另提
逐区normalized MAE≤5%（MAE除以max[该区参考整轨迹温度范围,1K]，不是RMSE）避免低激励轻易通过。
阈值301K仅原数值诊断，另330K为研究探针，都不是器件控制限值；穿越误差
≤max(.2s,5%×参考穿越时刻)，单侧漏穿越失败，双方无穿越NOT_APPLICABLE。
完整储热变化+上下边界积分−输入的残差/ max(累计输入J,1J)≤0.001。
以上新增逐die/相对/守恒标准均是待批提案，不修改旧RC FAILED结论。

对比同量：die/base体积均值、栈array体积加权均值、逐cell热点及位置分别输出。
不得用core当前arithmeticgroupmean代替非等厚体积平均，不能用均值max代替gridmax。
输出升降/冷却、可观测tau、峰值时刻、穿越和能量残差；数值、空间粗化和物理
输入不确定性分账。拟合/构造不使用新盲测输出，F02不过则不执行R07/F03。

新目录`eq3_thermal/runs/EQ3-P2-PERDIE-CALIBRATION-vN/<run_id>`；保存输入、
每die/stack输出、有限数量全场snapshot、原始功率、状态、stdout/stderr、实际
资源和hash收据。输出映射须在执行版静态测算磁盘，禁止每step写全场造成预算
爆炸；热平衡统计仍须保留可重算数据，不能因为省磁盘而声称未计算的守恒通过。
预期图：同x轴温度/功率/误差；无traffic/queue/token字段则UNAVAILABLE。

任一单位、输入hash、材料覆盖、时钟、观察量映射、能量、非有限值、负容量/
导热、>400K、资源或输出合同失败即停并保留raw。负结果不允许删点、松阈值
或调物性。数据通过仅支持该研究封装、该输入域的数值近似；不支持实际器件
结温、寿命或控制收益。

## 目前需确认的层级

本轮申请的是上述DESIGN_FREEZE候选（含有界方法提案）；执行仍被“新几何生成器
尚未实现”和“执行版本未批准”双重阻挡。设计确认后继续独立实现和固定回归，
不启动标定；把实际执行版呈现给用户确认后，再按允许点表执行到完成或停止。
