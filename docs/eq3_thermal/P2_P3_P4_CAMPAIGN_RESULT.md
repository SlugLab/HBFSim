# EQ3-P2-P3-P4-CAMPAIGN-v1 实际结果

2026-09-19。状态：**P2_BLOCKED_WITH_EVIDENCE；P3_CPU_VALIDATED；
P4_ENGINEERING_FIXTURE_VALIDATED**。不是 MODEL_FREEZE，也不是 P5 论文矩阵。
在阶段授权内完成了可行的主线工作，没有逐点等待审批或重跑 R01。

## 版本、授权与证据入口

- USER_CONFIRMED：用户采纳的连续任务书及真实确认，保存于工作区
  `eq3_thermal/plans/p2-p3-p4-campaign-v1/`。旧确认保留，本阶段记录替代关系。
- 不可修改基线 `5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5`，最终检查 clean。
- 本轮代码/测试截至 `afaac5775204c217f8ee0717177e6b1bcba0ba99`。
  后续结果文档提交不改变已冻结运行版本。
- 最终构建 `integration-v8` 绑定源 `afaac57`；P4-FIXED05 四套 CTest 全通过。
  六个 pilot 使用之前冻结的 `integration-v6`，构建源 `5dc026f`；启动时
  源版本 `8dafb82` 只新增独立分析代码。后续 advisory API/测试未改变服务数值路径，
  没有为文档或新增静态测试重跑六个 pilot。
- P2 原始点索引：`eq3_thermal/plans/campaign-v1/index.json`。
  P3/P4 逐点 manifest/stdout/stderr/result/metadata：新 campaign 的 `points/`。
  派生运行索引、六点指标及警告：`points/P4-FINAL-AUDIT02/stdout.log`；原01保留。
- 独立审查给出 PASSED_WITH_WARNINGS：所有已完成点的 manifest、输入快照、
  stdout/stderr、命令、Git身份、退出码及同一环境身份一致；四项分析器突变负例拒绝。
  这不是所有未知参数/物理验证均通过。

## P2：哪些修复有效，哪些结论仍然失败

| 分支 | 实际证据 | 结论 |
|---|---|---|
| R01/R02 | R01继承不重跑；R02完整100s；4→2mm相邻最大差3.253K | 不满足0.25K |
| 原3087节点RC | train热点诊断差10.9546K；development越域 | 保留失败 |
| 4mm RC | train对同R02最大差3.2232K；development最后完整30.4s后中止 | 改善但未通过 |
| 2mm RC train | 64512节点，10ms/10000步，100s完整；对同网格R02最大差0.173632K，最差区域MAE0.032790K | 仅诊断通过；参考未合格 |
| 1mm参考实现修复 | 去除冗余预分配、纠正factor所有权并调整固定U容量；小型等价/UBSan/故障注入测试通过 | 同方程工程修复，不改物性 |
| R03-OWNED-PILOT4S | 原train前4s/325J、258048单元、200步；328.012s墙钟；峰值5149380KiB；能量相对残差6.476e-8 | 资源prefix通过，不是完整R03 |
| 2→1mm前4s | 最大差1.277K | 仍不满足0.25K |
| 独立2mm development参考 | 完整64s/3200步/9632J；最大400.911K，31.14s首次越400K，44帧越域；能量残差1.291e-8 | DOMAIN_FAILED_AUDIT_ONLY |
| 2mm RC development | DOMAIN_ABORT，最后完整31.1s/399.975501K；真实失败瞬间数值UNKNOWN | 无完整能量/误差验收 |
| R04/R05、正式R06、R07/F03 | 参考精度、资源、development域/模型锁定前置条件未满足 | 未冒充完成；独立盲测未开封 |

2mm RC train实际求解462.381s，观察146.732s；储能0.029986016J，散热
1364.970013955J，相对1365J输入的绝对残差2.9344e-8J。零自由物性拟合、零ROM。

原U容量失败、错误编译flag尝试、旧RC负例都保留。固定版本参考本来已用
MMD_AT_PLUS_A排序。1mm完整100s在现有实现下预计约2000–2600s（**估计，不是实测**），
不执行可预期超600s的重跑，也不切段规避watchdog。温区超限同时出现在独立参考与RC；
尚无依据认定它是可通过随意改功率/边界/阈值消除的代码错误。
进一步长参考预算或更大数值后端改造，以及新物理/输入域，均进入待决清单。

## P3：实际消费者，不只是配置

- `ActivityObserver` 处理 arrival/issue/start/end/complete/fail/cancel，事件身份
  包含阶段；合法 completion 不被去重丢弃，跨窗口按真实已发生时间积分。
- `MqsimObserverAdapter` 接已有 `MqsimOnlineEngine` 的实际 CPU 事件和目标时钟。
  Admission 只标作请求占用代理，**不是 NAND start**；未知 die/plane/物理与链路
  字节保持 UNKNOWN，未由地址猜测。真实媒体结束与延迟报告完成分别记录。
- off 不收集/不构造模型；read_only 不构造求解器；shadow 有模拟传感器与显式
  阈值建议，建议不影响任何请求。实际6请求、QD2及限带宽延后完成对照，三模式
  完成ID/状态/服务时间一致；最终测试也实际消费 shadow 建议。
- 原温度核心、非均匀die/base能量、失败前活动、空闲冷却、恢复测试通过。
- CPU_PATH_VERIFIED / ADAPTER_COMPILED / LIVE_GPU_NOT_TESTED。没有 GPU 负载。

## P4：工程闭环与边界

`CpuService` 是隔离、显式 ENGINEERING_FIXTURE 的小型服务后端，复用原温度
引擎和P3事件账本；不是新物理NAND模拟器，也没有改现有MQSim/ABI/缓存默认路径。
固定测试通过现有 `RequestDispatcher::Engine` 消费并发布真实共享完成槽。

维护实际 enqueue→争用die/base/link→start→read/program→complete或fail。
成功program/DRAM-refresh提交后才更新年龄；失败或排队不清零。program/erase
以实际开始/成功分别计数；read或维护意图不算P/E。这里是一份每die合成数据cohort，
不宣称全容量数据覆盖、ECC/Ea、磨损均衡、块回收或寿命模型已经实现。

Normal/Light/Severe/Shutdown逐栈控制包括采样、动作延迟、驻留、滞回恢复和原因码；
影响后续admission。Severe保留并排空在途工作，Shutdown暂停新维护；积压不丢弃。
无前台时仿真时钟/热模型/维护仍推进。主机wall time和目标仿真时间分开，无host sleep
冒充目标服务时间。checkpoint保留队列、资源、在途、控制、年龄和热状态。

固定测试包含：同die互斥、relay与HBM共享base/link、DASH共享上游、外部GDDR服务，
部分失败能量、成功才提交、显式erase计数、所有控制态、在途排空、空闲恢复、
序列化续跑等价及错误快照拒绝。P4-FIXED01是测试漏算观察窗末的在途program，
修正守恒式而非服务结果；失败原件仍保留。

### 主线工程六点：实际结果，不预设收益

4HBM4-12hi+4HBF-16die，123个fixture节点（含112die/8base/GPU/中介层/散热体）。
初始/环境298.15K，10s模拟，每点1000个相同前台请求，维护常开。
完整事前数学screen、参数、命令、资源和指标在 `P4_ENGINEERING_PREFLIGHT.md`；
所有功耗、控制阈值及维护周期明确为工程选择。2s维护绝非OCP24h换算。

| 候选场景 | 策略 | 完成/排队前台 | 热点K | 已完成请求平均延迟s | 维护提交/积压 |
|---|---|---:|---:|---:|---:|
| Safe | 无动作 | 1000/0 | 298.774644 | 0.02144 | 684/0 |
| Safe | 滞回 | 1000/0 | 298.774644 | 0.02144 | 684/0 |
| Near | 无动作 | 1000/0 | 300.196639 | 0.02144 | 684/0 |
| Near | 滞回 | 1000/0 | 300.208288 | 0.13384 | 684/0 |
| Stress | 无动作 | 1000/0 | 309.404632 | 0.02144 | 684/0 |
| Stress | 滞回 | 448/552 | 304.192403 | 1.06804 | 556/56 |

观察窗末前台在途均0；延迟是**已完成请求条件均值**，不隐去552个尚未完成请求。
Near不降温且延迟更高；Stress降温伴随服务与维护代价，最大数据年龄从1.98s
升到6.14s。两者均不得包装成产品收益。Safe没有控制动作。Stress滞回实际有
Light36/Severe80/Shutdown44次进入记录；延迟/余热允许温度超过动作阈值，
不是一个硬性实时温度上界。

六点包内输入分别22.0332/22.0332/43.9884/43.9884/158.3384/87.2264J。
逐事件隐式步边界通量能量核对，相对残差1.803e-12–1.802e-11；逐组件账本与
模型能量映射差0。独立分析从实际服务区间重新积分功耗，并检查资源互斥、
请求守恒、年龄/提交/P/E，而非拿两个共享错误输出互证。

## 四拓扑三轴，不混报

| 拓扑 | 配置/转换 | 热物理研究验收 | 实际功能路径 |
|---|---|---|---|
| mixed-direct | 通用配置，4+4与2+6固定测试 | P2受阻；主线fixture工程验证非标定 | CPU通过；主线六点通过 |
| 4+4 relay | 四配对、几何共享必须一致 | 尚未验收非均匀转发研究功率域 | CPU争用通过；转发热量只入伙伴base |
| 四对DASH | 明确direct/relay两路同源 | 尚未热物理验收 | CPU共享上游/唯一完成/能量通过 |
| 8HBF+外部GDDR | 物理GDDR保留、不建封装外温度节点 | 8HBF研究几何未闭合；外板热域不在本轮 | CPU外存服务/独立非零能量通过；GDDR温度UNAVAILABLE |

固定拓扑功能fixture使用同一明确2die/stack小模型检查连接与仲裁，不拿旧
relay8die或DASH16HBM层数偷偷替代研究主线。主线123节点另验证12/16层输入。
定量带宽、PHY/控制器真实能量、真实relay能力及DASH切换策略仍是参数/研究缺口。

## 资源、回归与保留项

- 六pilot墙钟合计13.928s，单点2.072–3.300s，OS单子进程峰值≤62664KiB。
  测得raw最大约3.31MB；这些是小型fixture的实测，不能转用到P2大参考。
- 本轮最大P2参考prefix峰值约4.91GiB；各求解/后处理均≤600s，单CPU串行，
  GPU0/云0。最终任务保留约17.03GB十进制（约15.86GiB），未重置跨阶段预算。
  磁盘字段是保守逻辑文件量；累计物理写入UNKNOWN。RSS峰值是OS单子进程峰值，
  聚合采样另列，不能混称精确同时驻留峰值。
- P4-FIXED05四套C++通过；原核心13个CPU测试通过；Python136项完成、9项跳过
  未显式启用的求解器测试，另新增静态主线输入检查通过。此前P2 148项证据保留，
  没有声称本轮跳过项重新运行。未变大raw无损链路不重复逐字节回验。
- 自动元信息部分库版本/build flags为UNKNOWN；显式构建清单、已绑定可执行文件
  是补充证据，不回填成当时自动观测。早期构建未单独绑定静态archive，最终BUILD05
  增补完整输入绑定；历史记录不改写。跨主机重建尚未实测。
- 宿主GPU是否可见不能由本代理NVML失败推断；本轮无GPU授权/负载，不改驱动。
- 代码分小提交，未push/merge；基线与原核心无改动。当前无本任务后台求解。

剩余决策见工作区新campaign `DEFERRED_USER_DECISIONS.md`：参考精度/算时方案、
development域处理，以及物理能量/可靠性参数闭合。P5非热核心消融与正式矩阵
仍 NOT_IMPLEMENTED/NOT_RUN，不提供伪开关或token指标。
