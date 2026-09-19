# 四拓扑强制交付与设计冻结修订 v3

## 当前增量状态（保留下面的历史v3设计，不改写冻结清单）

四拓扑实际CPU资源/能量/完成路径现已有工程fixture测试，不再仅JSON生成。
mixed-direct主线另有六个因果工程pilot；relay伙伴base与HBM直连共享、DASH单一
上游、8HBF外部物理GDDR服务均已测试。它们不是四种研究封装热/性能全验收。
P2参考/模型仍未冻结；物理能量、可靠性与live GPU缺口仍保留。
三轴完整状态和原始证据入口见[P2/P3/P4实际结果](P2_P3_P4_CAMPAIGN_RESULT.md)。

实施续接修订：用户明确8HBF时GDDR位于封装外，本轮不模拟GDDR温度/板域。
下表原v3板级热模型要求已被此决定覆盖；外部物理身份与系统依赖仍保留。
通用转换器现有固定四拓扑/数量/层数/功率映射测试，首例双后端已静态生成；
热模型仍NOT_VALIDATED、系统行为仍NOT_IMPLEMENTED。具体证据见CONVERTER_VALIDATION.md。

USER_CONFIRMED（2026-09-19）：4HBM4+4HBF mixed-direct 是首个逐层研究封装和
转换器验证实例；接受已明确分类的厂商规格、代理和研究假设，不代表全部参数
完整，也不缩小最终范围。四种拓扑均为 REQUIRED，不是可选扩展。
本修订不是标定、GPU 或正式实验批准；MODEL_FREEZE 尚未发生。

## 参数、消费者与验收覆盖

以下路径是研究数据图目标，不是已验证的真实服务实现。P1 的声明图仅作为软件
fixture；几何/功率缺口必须显式失败，不能借用其它拓扑填空。

| 必交拓扑 | 器件、几何与连接路径 | 共享资源、功耗归属 | 传感器和模型验证域 | 当前消费者、关键缺口 | 验收条件 |
|---|---|---|---|---|---|
| 8HBF direct + 物理 GDDR 快存 | 8 个 HBF 含各自 base/array；GPU↔HBF直连；外部GDDR按用户要求排除本轮封装热域 | 封装内各HBF及GPU能量；外部GDDR服务/能量不伪造，不借HBM base代替 | GPU、每HBF base/die/stack；不输出GDDR/PCB温度或声称板级耦合已验证 | 通用转换器fixture保留外部physical GDDR且无热实体；8HBF研究几何/外存服务仍缺 | 封装内能量守恒与热参考验收；系统请求/共享资源与外存行为独立验收，不要求本轮GDDR板级热模拟 |
| mixed-direct，HBM+HBF 总数 8 | 两类存储各自直连 GPU；混合域 nHBM=1..7，nHBF=8−nHBM；4+4 为首例，端点按单独全同类 profile 验证，不静默套用 | 各 array/base/TSV/链路与 GPU 端资源；是否共享 GPU fabric 及上限须声明；无 relay 能量 | GPU、每 base/die/stack；当前仅 4HBM12H+4HBF16die 条件研究几何，非全部数量/层数域 | P1 默认是 2+6 fixture；新 4+4 JSON 仅静态检查，逐层转换器未实现；真实活动→功率未闭合 | 非 4+4、不同 die 数、重排 ID 的配置测试；逐物理源守恒；参考与 RC 独立通过；直连服务路径验收 |
| 4HBM + 4HBF relay | 显式四配对：GPU↔HBM base↔HBF；需研究型 base 转发能力，不能称常规 HBM4 已支持 | HBM base 转发/PHY、HBM GPU 链路、HBF TSV/array 和仲裁竞争；转发不等于访问 HBM array；接收/发送能量分端归属 | 同上，额外 base 转发热源；热几何相同也不能继承路由/功耗验证 | P1 daisy 图和合成 base 激励；无真实 relay 仲裁；P1 HBF8die，与候选 HBF16die 不同；延迟/带宽/能量缺口 | 显式配对和合法 base 能力；争用/队列/因果完成、无伪 DRAM 访问、base 能量不漏不重；本拓扑热域验收 |
| 四对 DASH | USER_CONFIRMED DSAH→DASH；每对 HBF 同时具有 direct/relay 路径，配对及选择规则显式化 | 两路径共享同一 HBF array/TSV 供给；relay 再占 HBM base/链路；选择、分流、切换、回压及端点 PHY 能量单列；不可双算容量 | 同上；需双路径热点、base/array 非均匀功率域验证 | P1 dual 声明图；当前 HBM16H fixture 不是主线12H；真实选择与共享仲裁未实现 | 两路径独立及并发服务测试、共享瓶颈守恒、不重复完成/计能、四配对正确；本域参考/RC 验收 |

## 三条独立验收轴

| 拓扑 | 配置覆盖 | 热模型验证 | 系统拓扑行为验证 |
|---|---|---|---|
| 8HBF + GDDR | 通用双导出fixture PASS；研究profile INCOMPLETE，GDDR热域排除 | NOT_VALIDATED | NOT_IMPLEMENTED |
| mixed-direct | 非4+4 fixture PASS；4+4研究首例双导出/原生parse-only PASS | NOT_VALIDATED | NOT_IMPLEMENTED |
| relay 4+4 | 通用配对/双导出fixture PASS；研究profile INCOMPLETE | NOT_VALIDATED | NOT_IMPLEMENTED |
| DASH 四对 | 通用配对/双导出fixture PASS；研究profile INCOMPLETE | NOT_VALIDATED | NOT_IMPLEMENTED |

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
