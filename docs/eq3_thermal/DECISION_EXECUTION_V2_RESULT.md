# EQ3-DECISION-EXECUTION-v2 实际结果

状态：本阶段执行完成，保留数值/模型能力限制；本任务无在途求解或后处理。

## 实际交付与边界

本轮从 repair/eq3-minimal-v1 的 aef7271 继续，保留原基线
5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5。未 reset、push、merge，未改全局环境，
无 GPU 或云负载。旧失败、旧六点、原 development 400.911 K 越域结果原样保留。
有效 AGENTS 已登记 D1–D6、最小侵入和用户后续授权。固定资源数字已被用户改为
逐实验合理分配，运行收据中的 12/16 GiB 等是当次选择，不是永久不可变上限。

| 工作 | 实际结果 | 解释边界 |
|---|---|---|
| D1 原 train 完整 1 mm/20 ms/100 s | 求解及完整观察完成，求解 2763.60 s，观察 833.56 s，求解峰值采样 RSS 7,735,140 KiB | 完成不是参考精度通过 |
| D2 验收 v2 | 10 项固定测试通过，方法/窗口先冻结；v1 并列保存 | 参考未合格时不 MODEL_FREEZE |
| D3 统一功率域 | alpha=0.25；train/development 原生与 RC 四点完整完成 | 两轨迹温度/能量一致；冻结 v2 train 分段穿越计数未通过 |
| D4 可选升级优先策略 | 固定测试通过，九点工程矩阵完成，默认关闭 | 新旧策略在该输入上结果一致；不声称收益 |
| D5 实际 CPU gate/只读命令阶段 | 隔离编译、4 项原生 C++、13 项 service/client Python 验证通过 | 真实命令事实不是已校准能量；生产 host/GPU 未接入 |
| 基础 HBM/SRAM/级联 | 30 项相关固定检查，四拓扑实际 CPU 组合均通过 | HBM 是参数化工程模型，HBF 是真实 MQSim，fabric 是外部基础模型 |
| 真实 die 维护 | 已交精确窄设计，运行时 UNSUPPORTED_CAPABILITY | 未批准的共享仲裁/数据 commit 生命周期没有实施 |

原始启动命令、输入身份、标准、资源及退出收据集中于外层目录
`eq3_thermal/plans/decision-execution-v2/points/`。所有 PASS 均限定各自软件/工程范围，
没有把异类 suite 相加成“全项目测试总数”。

## 多 stack、拓扑与基础链路

显式 stack map 将持久逻辑页映射到互不重叠的 MQSim channel 组，原生命令事件
反查实际 channel。四拓扑测试都向每个已配置 stack 发出请求；不存在只给一个
stack 发请求的现象。D4 也每栈均等到达，HBM/HBF 完成量差异来自维护、准入和
工程服务模型，不是到达分配失衡。

| 拓扑 | 实际 CPU 请求/完成 | 分配 | 基础连接 |
|---|---:|---|---|
| 8HBF direct + 外部物理 GDDR | 24/24 | 每个 HBF 3 个 | 每栈独立 direct link，有限双 SRAM bank |
| mixed-direct 4HBM+4HBF | 24/24 | 每个 stack 3 个 | 两类 stack 各自 direct 路径，无虚构 relay |
| 四对 relay | 24/24 | 每个 stack 3 个 | HBF 经配对 HBM base；与该 HBM 本地流量共享 GPU link/bank |
| 四对 DASH | 28/28 | 每 HBF 4 个、每 HBM 3 个 | 三链路、direct/relay 两路；不同 ready bank 可并行排出 |

每个请求唯一完成，结束时无 bank/link owner 遗留。HBF 媒体完成、后端 reported
completion、外部门控等待和最终 fabric delivery 分开保存；不修改既有 MQSim
完成时间，不无条件再加 NAND/data-out 服务时间。组合当前要求 MQSim raw callback
与原 reported completion 相等，否则返回 UNSUPPORTED_COMPOSITION，避免不明
aggregate-bandwidth 约束被重复记为 package 传输。

配置、热、系统行为三轴详见 FOUR_TOPOLOGY_V2_STATUS.md。基础模型没有实现 pin perimeter/capacity 的产品校准，
不宣称达到 2–4 TiB 或 12.8–24.5 TB/s。新四拓扑组合尚未与完整热模型闭环。
外部 GDDR 的系统服务与温度为 UNAVAILABLE；不是能量或板级热影响为零。

## 控制代价与用户的新目标

当前已实现的是 per-stack 热点驱动的 Normal/Light/Severe/Shutdown 门控，含采样、
动作延迟、滞回、驻留与真实路径联合准入；新策略只让升级不再被恢复驻留延后。
它没有温度到 ECC/retry 的模型。工程阈值约 26/27/29 °C、固定 20 ms 读取都不能当
HBF 产品参数。

在每栈 25 requests/s、20 s 输入加 10 s 恢复的固定对照中：无控制完成 4000/4000，
两种控制均只完成 2500/4000，积压 1500；最高温度从 301.5925 K 降至 300.7418 K，
已完成请求 p95 从 0.04 s 增至 10.34 s。降温伴随少完成工作，不能称为效率收益。
完整九点温度、队列、维护年龄/积压、能量与删失口径见 D4_CONTROL_COST_REPORT.md。

用户最新目标采纳为：正常调节面向持续 delivered bytes/s、窗口波动、尾延迟和
积压；温度作为风险/保护约束。热保护仍须优先执行。权威来源足以支持机制与
代理情景，但 NAND 温度与 retry 不是普适单调关系；磨损、保留时间和温度历史
也必须区分。HBM2 的温度刷新间隔/占用有公开数值，可作 HBM4 的显式 PROXY，
不能称 HBM4 实测参数。

READ_RATE_CONTROL_SOURCE_BASIS.md、HBM_TEMPERATURE_SERVICE_SOURCE_BASIS.md
列出来源、原值、允许转用和缺口。普通速率反馈最小方案尚未实施；没有把新目标
偷偷写入已完成九点输入。缺数据只限制产品级定量结论，不阻塞基础工程链路。

## 可重建、小提交与撤回

本轮功能提交：5323629（控制策略/矩阵）、c7b35b3（执行路径/资源适配）、
ee55722（v2 标准）、e6ce686（D3/空间诊断）、9d2805b（MQSim 接口/观测/映射）、
afc4609（基础 HBM/fabric/CPU 组合）、1d1a082（温控说明）、1db8d57（测试调用与来源）。
冻结执行源 decision-p2-frozen=ee55722、decision-d3-frozen=e6ce686 与当前分支分别登记。
MQSim 补丁通过现有 patches/private build 流程重建，未以 third_party dirty 发布。
构建及命令入口见每点 manifest；未声称跨主机已验证。

关闭可选策略、native observer、stack map 与外部 basic composition 即恢复原选择；
每个局部提交可独立审查/回退，不使用 reset 覆盖成果或删除 raw。

## 保留的失败

- native-d5-fixed：四项原生检查通过；Python 失败来自错误文案断言和隔离构建路径
  的测试配置，修复测试消费者后 native-d5-fixed-v2 通过。
- D3 第一 attempt：840 W 汇总得到 839.999999999993，驱动错误地做精确比较。
  修复为有量纲合理容差，v2 复用身份验证后的 cap；未改变功率或热方程。
- 原 development 越域、旧六点负结果和旧 v1 判定全部保留；新域不能覆盖它们。

## 温度标准的依据与新问题

0.25K来自项目工程决策，不是OCP/Sandisk或数值分析文献的统一要求。需要网格
误差检查有合理依据，但现有文件没有从读取SLO、测温不确定性、控制保护裕度
反推这个具体值。TEMPERATURE_TOLERANCE_RATIONALE.md解释两处0.25K的区别，
并提出分开工程闭环、读取稳定性充分性和热保护资格；新合同待明确采用，
旧失败不改写。网格间2.069K是同一输入的数值差，不是现实器件温度波动。
同一物理范围的粗化细网格场比较已完成，原因与证据见下文。

## 问题分类与最小处理

| 问题 | 分类 | 实际证据/处理 |
|---|---|---|
| 原relay遗漏受控伙伴端点/Light更新 | CONFIRMED_BUG，已在上一阶段修复 | 本轮继承，不重复改写；固定回归复用/受影响检查通过 |
| D3 cap浮点精确比较 | CONFIRMED_BUG，驱动层 | 840W累计误差约7e-12W；有量纲容差修复，原cap未改 |
| v2原始采样时刻精确比较 | CONFIRMED_BUG，数据接口表示层 | 最大约1.42e-14s；双端Decimal派生规范化，2项固定测试通过；冻结评分未改 |
| 2→1mm热点最大差2.069K | NUMERICAL_ACCURACY_LIMIT，待局部场原因分解 | 旧0.25K FAIL保留；不把网格差称作真实器件变化 |
| 原development400.911K | DOMAIN_FAILURE | 不扩大400K、不裁剪，不重跑同一已知失败 |
| 固定服务时长/功率/整请求占用 | DESIGN_LIMITATION/ENGINEERING_FIXTURE | 不重写CpuService冒充NAND后端；另有真实MQSim阶段事实 |
| 原生成器百万cell防护 | 当前软件能力/资源预检边界 | 不是用户永久内存上限；uniform细化可按已授权范围规划，nonuniform生产链需方案 |
| 真实维护提交/仲裁/commit | DESIGN_LIMITATION | UNSUPPORTED_CAPABILITY；精确结构设计待确认 |
| 升级优先在九点无增益 | NOT_REPRODUCED（该输入未触发策略差别） | 测试覆盖差别，但九点不能宣称新策略更优 |


## P2 完整结果与当前采用范围

原始train 1mm参考完整100s、5000求解帧、1000观察时刻、275传感器，
输入1365J，温度范围300–369.012K，能量残差1.491e-6。

| 相邻网格 | 全窗传感器平均TW-MAE K | 受激段 K | 冷却段 K | 最大差 K |
|---|---:|---:|---:|---:|
| 4→2mm | 0.143305 | 0.233694 | 0.096741 | 3.253 |
| 2→1mm | 0.064942 | 0.108907 | 0.042293 | 2.069 |

这些是各窗口和传感器上的诊断汇总，正式评分仍逐传感器/窗口检查。
完整同窗比较不使用4s prefix冒充全部激励，也没有计算全局最大值Richardson阶。
旧完整RC2mm/10ms对新R03的回顾性评分：v1 NUMERICAL_FAIL（22传感器失败），
v2 NUMERICAL_FAIL_WITH_THRESHOLD_AMBIGUITY（全窗19个失败，共427个传感器窗失败）；
最大差2.045948K，最坏全窗MAE0.156614K，能量残差2.15e-11。旧NMAE与1K/2K/5%
结果保存在原评分文件，不因v2或本轮采用决定改成PASS。

局部场审计给出原因：最坏位置为hbm3.base@15s，来自邻近HBM2的64W激励。
2.069K差=1.5065K细单元峰值相对同面积平均+0.5625K平均后仍有的场差；
同base均温差仅0.099943K。没有发现单位、功率重复、界面、层序或输出索引错误。
新旧native二进制的完整sensor温度线性sanitycheck最大差0.002K，低于量化传播界
0.0025K，也不能解释2K差异。该分解不是连续解真实误差预算。

用户随后明确采纳：只要保留原参数假设且机制物理合理，当前模型用于工程主线。
因此状态是CONDITIONAL_ENGINEERING_USE；不为0.25K继续无限细化，原reference
资格失败仍保留，MODEL_FREEZE=false，blind仍未开启。物理参数/边界未实测标定的
限制不妨碍软件闭环，但不能声称产品温度、寿命或安全裕度已验证。

0.5mm uniform细化仍在已授权方法内，但当前不启动是主线优先的选择，不是旧资源
上限造成的永久阻塞。native nonuniform存在但EQ3生产/映射链未接入，若未来采用
需提交具体结构方案。见P2_NUMERICAL_NEXT_STEP_SCREEN.md。

时间离散证据复用原同2mm完整RC10ms对native20ms最大差0.173632K，以及已有RC
步长诊断；这些不能冒称1mm原生参考已完成20→10ms资格。已知空间未达旧门槛，
本轮不新增昂贵1mm时间细化来掩盖空间差。

## D3 新域完整原生对照与评分

alpha在新输出前冻结为0.25，不按轨迹调参，也未看blind。全源840W稳态包络四候选
最大温度分别为476.4008/432.3006/388.2004/344.1002K；最大满足380K规划裕度的
候选为0.25。380K不是产品温限。新train输入341.25J，development2408J，保持全部
源相对权重、空间、时序与冷却。

| 新域完整点 | 温度范围 K | 能量相对残差 | 求解 wall s | 观察 wall s | 采样任务峰值 KiB |
|---|---|---:|---:|---:|---:|
| train native 2mm/20ms/100s | 300–317.333 | 1.708e-5 | 402.259 | 241.760 | 1309892 |
| train RC 2mm/20ms/100s | 300–317.333353 | 3.056e-11 | 293.558 | 178.980 | 442852 |
| development native 2mm/20ms/64s | 300–325.228 | 1.763e-7 | 259.107 | 131.689 | 998384 |
| development RC 2mm/20ms/64s | 300–325.227640 | 3.070e-11 | 191.867 | 105.598 | 435516 |

RC factor各17.682/15.760s，分解L非零19,798,141。推进与输出没有独立计时，
应为UNKNOWN，不能用总wall减factor冒充实测推进成本。求解wall/sim约2.936/2.998，
每步含setup/输出的摊销约58.7/60.0ms；这个2mm后端不是实时快速模型。运行资源
与墙钟仅作操作收据，不作不同主机负载下的算法性能比较。

| 轨迹 | 原 v1 | 冻结 v2 汇总 | 全窗最坏TW-MAE K | 所有窗最大温差 K |
|---|---|---|---:|---:|
| train | PASS | NUMERICAL_FAIL_WITH_THRESHOLD_AMBIGUITY | 0.000216432 | 0.0005000 |
| development | PASS | PASS_WITH_THRESHOLD_AMBIGUITY | 0.000207961 | 0.0005000 |

train受激/冷却窗最坏TW-MAE分别0.000462149/0.000460106K；development分别
0.000214699/0.000211400K。全部温度绝对/幅值/热点条件与能量条件通过，但不能
把这些分项通过替代上表的冻结汇总。

**train v2失败的具体原因：** 只有`component:hbm1.die7:hotspot`在
excitation-20 [37.5,39.0] 与 cooling-21 [39.0,40.5] 两个窗失败。参考三位小数
输出在39.000s恰好到301K，RC的上穿为39.008801s；逐窗穿越次数因跨边界不同。
两窗MAE仅0.0002075/0.0002078K，最大差均低于0.0005K；全轨迹v1穿越通过。
冻结实现把逐窗legacy穿越失败继续作为v2 veto，即使标THRESHOLD_AMBIGUOUS。
这是分段判据对量化/边界敏感的限制，不是新热方程失败，不通过改输入或重复求解
处理。本轮保留此FAIL，另报告温度/能量离散一致；未自行替换正式穿越合同。

两次后处理问题均保留原记录：D3 attempt-v2在未规范化时间时评分退出失败，
之后仅对双方派生CSV时间字符串规范化重新评分，四份raw原样复用。最终状态
以D3_CANONICAL_SCORE_SUMMARY.json及D3_SCORE_CAUSE_DIAGNOSTIC.json为准；
旧D3_FAILED_v2.json继续存在，不删除或改写。

## 当前未做与唯一待确认的结构项

- 真实MQSim die维护生命周期仍UNSUPPORTED_CAPABILITY。具体文件/符号、
  read/program/erase数据有效性、映射commit/回滚、共享TSU仲裁、唯一完成、
  测试与撤回见MQSIM_DIE_MAINTENANCE_NARROW_DESIGN.md；需要用户明确批准后实施。
- 面向稳定读取率的外部反馈策略当前有来源和窄方案，尚未实现。现有默认关闭
  thermal gate和升级优先策略均保持真实状态；没有虚构温度到ECC/retry的曲线。
- 新基础四拓扑CPU组合尚未与完整热模型耦合。已有单组件Shadow→gate→实际MQSim
  fixture验证了接口闭环；它不能冒充多stack物理热闭环或production/GPU。
- 当前采用是原假设下的工程用途，不是完整MODEL_FREEZE。原高功率域失败、
  参考网格差、时间资格与真实维护缺口分别报告，blind始终未读/未生成。

## 运行索引与复现

外层 `eq3_thermal/plans/decision-execution-v2/RUN_INDEX.json` 索引所有原始收据，
`EXECUTION_RESULT.json`保存阶段最终状态。D3继续脚本run_d3.py为create-only，
已有点不应整批重跑；只读评分续跑为run_d3_score_canonical.py。可用当前工具与
相同明确参数在新输出路径复现诊断，不通过删除原失败目录复用ID。

本阶段最终保留约26.75GB十进制（约24.92GiB），主文件系统剩余约651GB十进制；
无新增环境、GPU或云费用。本轮新提交继续包括4198bcf、fe95453、a24165a、36eae00
以及本汇总提交；源码分支均留本地。
