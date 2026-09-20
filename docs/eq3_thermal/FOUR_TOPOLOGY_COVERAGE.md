# 四拓扑强制交付与设计冻结修订 v3

## 2026-09-20 minimal repair update

See [EQ3-MINIMAL-REPAIR-v1 report](MINIMAL_REPAIR_REPORT.md) for current three-axis
status. Four CpuService CPU functional regressions pass after route-endpoint
Shutdown/Light repair. Actual MQSim request-level optional gate and thermal
cooling consumer pass CPU tests only. Native MQSim command facts and persistent
channel-group placement now pass fixed CPU tests; production host-service active
control remains unconnected and die maintenance remains unsupported. P2 stays
unfrozen and no live GPU result is claimed. External physical GDDR package
temperature is UNAVAILABLE, not zero external energy/board effect.

## 当前增量状态（保留下面的历史v3设计，不改写冻结清单）

四拓扑现有一条实际 MQSim CPU 工程链，不再仅靠 JSON 或 `CpuService`：HBF 请求
经显式 stack/channel map 进入真实 MQSim 命令路径，HBM 使用参数化场景后端，
package direct/relay/DASH 使用有界双 bank 与独立/共享链路。四个小用例均通过，
且保存逐栈计数、唯一完成和零资源泄漏证据。它们不是四种研究封装热/性能全验收。
P2参考/模型仍未冻结；物理能量、可靠性、生产 host 和 live GPU 缺口仍保留。
三轴完整状态和原始证据入口见[P2/P3/P4实际结果](P2_P3_P4_CAMPAIGN_RESULT.md)。

实施续接修订：用户明确8HBF时GDDR位于封装外，本轮不模拟GDDR温度/板域。
下表原v3板级热模型要求已被此决定覆盖；外部物理身份与系统依赖仍保留。
通用转换器现有固定四拓扑/数量/层数/功率映射测试，首例双后端已静态生成；
热模型仍 NOT_VALIDATED；系统行为仅提升为小型 CPU 工程fixture通过，不能写成
研究拓扑、生产系统或 live GPU 通过。具体证据见 CONVERTER_VALIDATION.md 及
`decision-execution-v2/points/basic-four-topology-actual`。

USER_CONFIRMED（2026-09-19）：4HBM4+4HBF mixed-direct 是首个逐层研究封装和
转换器验证实例；接受已明确分类的厂商规格、代理和研究假设，不代表全部参数
完整，也不缩小最终范围。四种拓扑均为 REQUIRED，不是可选扩展。
本修订不是标定、GPU 或正式实验批准；MODEL_FREEZE 尚未发生。

## 参数、消费者与验收覆盖

以下路径是研究数据图目标，不是已验证的真实服务实现。P1 的声明图仅作为软件
fixture；几何/功率缺口必须显式失败，不能借用其它拓扑填空。

| 必交拓扑 | 器件、几何与连接路径 | 共享资源、功耗归属 | 传感器和模型验证域 | 当前消费者、关键缺口 | 验收条件 |
|---|---|---|---|---|---|
| 8HBF direct + 物理 GDDR 快存 | 8 个 HBF 含各自 base/array；GPU↔HBF直连；外部GDDR按用户要求排除本轮封装热域 | 封装内各HBF及GPU能量；外部GDDR服务/能量不伪造，不借HBM base代替 | GPU、每HBF base/die/stack；不输出GDDR/PCB温度或声称板级耦合已验证 | BasicSystem 实际fixture：8个单channel/单die HBF各3请求，MQSim完成24/24，双bank回压和零泄漏；外部physical GDDR身份保留，但service/temperature均UNAVAILABLE | 封装内能量守恒与热参考仍待验收；GDDR系统服务仍缺，不要求本轮GDDR板级热模拟 |
| mixed-direct，HBM+HBF 总数 8 | 两类存储各自直连 GPU；混合域 nHBM=1..7,nHBF=8−nHBM；4+4 为首例 | 各 array/base/TSV/链路与 GPU 端资源；无 relay 能量 | GPU、每 base/die/stack；当前仅 4HBM12H+4HBF16die 条件研究几何，非全部数量/层数域 | BasicSystem 4+4 actual fixture：每栈3请求，共24个唯一完成；HBF走MQSim，HBM为PARAMETRIC场景，配置中pair/relay为空，最终bank/link全释放 | 非4+4与研究几何/容量/吞吐、真实HBM、逐物理源能量和热参考仍待验收 |
| 4HBM + 4HBF relay | 显式四配对：GPU↔HBM base↔HBF；基础实现不代表常规HBM4产品已有该能力 | HBF source bank→pair-private relay→共享的配对HBM bank/GPU link；不访问HBM array | 同上，额外 base 转发热源仍未接热模型 | BasicSystem actual fixture：4对、每栈3请求、24个唯一完成；HBM local与relay共享有界bank/GPU link，顺序两段fabric完成，零泄漏 | 链路/银行时序仅SCENARIO_ASSUMPTION；真实PHY、能量、热源映射和研究几何仍待验收 |
| 四对 DASH | 每对HBF同时有direct/relay，package route与MQSim native direct placement分账 | 两条drain路径使用独立link；relay占配对HBM bank/GPU link；两bank产生真实外部回压 | 双路径热点、base/array非均匀功率域仍未验证 | BasicSystem actual fixture：每HBF四个交替direct/relay、每HBM三个local，共28个唯一完成；四对映射、共享资源和最终零泄漏通过 | 基础仲裁通过不等于校准带宽/能量、研究封装热验证、生产策略或live GPU通过 |

## 三条独立验收轴

| 拓扑 | 配置覆盖 | 热模型验证 | 系统拓扑行为验证 |
|---|---|---|---|
| 8HBF + GDDR | 工程8HBF派生profile/map PASS；研究profile INCOMPLETE，GDDR热域排除 | NOT_VALIDATED | `BASIC_CPU_HBF_PATH_PASS`；GDDR service/temperature UNAVAILABLE，生产host/live GPU未接 |
| mixed-direct | 工程4+4派生profile/map与实际CPU链 PASS；研究首例配置覆盖保留 | NOT_VALIDATED | `BASIC_CPU_FIXTURE_PASS`；真实HBM、生产host/live GPU未接 |
| relay 4+4 | 工程四配对配置与实际CPU链 PASS；研究profile INCOMPLETE | NOT_VALIDATED | `BASIC_CPU_FIXTURE_PASS`；参数化HBM/链路，不是产品能力验证 |
| DASH 四对 | 工程四配对双路径配置与实际CPU链 PASS；研究profile INCOMPLETE | NOT_VALIDATED | `BASIC_CPU_FIXTURE_PASS`；未校准、未热耦合、非生产策略 |

任何一轴通过不提升其它轴；主线标定通过也不提升其它拓扑。旧 RC 仍 FAILED；
旧 40mm 案例只作测试。完成标准是四行各自完成适用的三轴证据，不是四个 JSON 存在。

## 可共用与必须重新确定的参数

4+4 direct/relay/DASH **在选用完全相同的器件、坐标、层厚、材料、接触、冷却时**，
可共享这些几何/被动热参数及其来源。只重接数据图不自动改热图。研究假设允许
共用 base 尺寸，但新增转发电路若改变面积/层厚/材料必须重建热域；不能声称物理等价。
现有 P1 三份配置 die 数不同，故不满足上述共用前提。

必须逐拓扑确定：端点/方向/路径数、配对、路由策略、仲裁/共享资源容量、链路
速率及延迟、流量放大、PHY/controller/relay 的静态和动态能量、完成时点、功率
映射及维护竞争。相同 payload 不保证相同物理流量/能量。对端能量分别落到 GPU、
HBM/HBF base 或其它有证据的物理组件；禁止把整条链路全部算到任意一端。

8HBF+GDDR 不继承片上 HBM 冷却假设；按用户最新决定，GDDR及板域不进入本轮
封装热模型，不输出或声称其温度。外存 controller/PHY/服务参数只阻塞相应系统
结论；不得改成HBM，也不为其猜测热边界来阻断就绪封装输入。

## 修订后冻结清单与下一次确认

- [x] 原则接受首例；四拓扑 REQUIRED，名称和范围明确。
- [x] base-die 存在性/消费者及代码复用审计：见 REUSE_AND_BASE_DIE_AUDIT.md。
- [x] 通用输入契约：见 LAYER_CONVERTER_CONTRACT.md；17组只属首轮。
- [ ] 经审查的独立转换器与固定软件测试；不改核心/ABI，不启动标定。
- [ ] 四拓扑研究输入分别补齐；缺口逐项解除，不长期降级成可选项。
- [ ] 首例真实可执行标定版本绑定源码、环境、输入、输出、资源、指标及哈希。
- [ ] 用户确认该执行版本后，才运行新封装参考/RC 验证；通过后 MODEL_FREEZE。
- [ ] relay/DASH 和 8HBF+GDDR 分别建立模型域及行为验收，再分阶段提交实验。

下一次执行确认对应首例 **CPU 上的新逐层 3D-ICE 参考与 RC 比较**，不是 GPU
标定，也不是四拓扑正式矩阵。现有预检 v2 的首轮7参考+3RC、856模拟秒仅为候选
清单，转换器未就绪，当前没有可启动执行版。就绪后必须展示实际命令、输入哈希、
点数/时长、初态、拟合方法（当前0自由拟合）、误差/能量验收和安全条件；需明确
确认该版本。没有机械总迭代次数上限，但科学范围改变须重新确认。
