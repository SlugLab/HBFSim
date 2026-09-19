# 四拓扑强制交付与设计冻结修订 v3

USER_CONFIRMED（2026-09-19）：4HBM4+4HBF mixed-direct 是首个逐层研究封装和
转换器验证实例；接受已明确分类的厂商规格、代理和研究假设，不代表全部参数
完整，也不缩小最终范围。四种拓扑均为 REQUIRED，不是可选扩展。
本修订不是标定、GPU 或正式实验批准；MODEL_FREEZE 尚未发生。

## 参数、消费者与验收覆盖

以下路径是研究数据图目标，不是已验证的真实服务实现。P1 的声明图仅作为软件
fixture；几何/功率缺口必须显式失败，不能借用其它拓扑填空。

| 必交拓扑 | 器件、几何与连接路径 | 共享资源、功耗归属 | 传感器和模型验证域 | 当前消费者、关键缺口 | 验收条件 |
|---|---|---|---|---|---|
| 8HBF direct + 物理 GDDR 快存 | 8 个 HBF 含各自 base/array；GPU↔HBF 直连，GPU↔板级 GDDR 快存；GDDR 颗粒数、布局、封装、PCB、散热边界待定 | 每 HBF array/TSV/链路及 GPU 侧资源分别计；GDDR controller/PHY、器件、PCB 能量归属待定；不可借 HBM base 代替 | GPU、每 HBF base/die/stack、每 GDDR 器件；板级与封装耦合、冷却域须另验 | P1 all_hbf_direct_8：单 GDDR 热节点 + GPU 耦合 fixture；无研究级板模型、外存服务/功率模型 | 配置明确 physical_type=GDDR；板/冷却/外存参数齐备；能量守恒和热参考通过；直接请求、共享带宽、GDDR 服务行为各自验收 |
| mixed-direct，HBM+HBF 总数 8 | 两类存储各自直连 GPU；混合域 nHBM=1..7，nHBF=8−nHBM；4+4 为首例，端点按单独全同类 profile 验证，不静默套用 | 各 array/base/TSV/链路与 GPU 端资源；是否共享 GPU fabric 及上限须声明；无 relay 能量 | GPU、每 base/die/stack；当前仅 4HBM12H+4HBF16die 条件研究几何，非全部数量/层数域 | P1 默认是 2+6 fixture；新 4+4 JSON 仅静态检查，逐层转换器未实现；真实活动→功率未闭合 | 非 4+4、不同 die 数、重排 ID 的配置测试；逐物理源守恒；参考与 RC 独立通过；直连服务路径验收 |
| 4HBM + 4HBF relay | 显式四配对：GPU↔HBM base↔HBF；需研究型 base 转发能力，不能称常规 HBM4 已支持 | HBM base 转发/PHY、HBM GPU 链路、HBF TSV/array 和仲裁竞争；转发不等于访问 HBM array；接收/发送能量分端归属 | 同上，额外 base 转发热源；热几何相同也不能继承路由/功耗验证 | P1 daisy 图和合成 base 激励；无真实 relay 仲裁；P1 HBF8die，与候选 HBF16die 不同；延迟/带宽/能量缺口 | 显式配对和合法 base 能力；争用/队列/因果完成、无伪 DRAM 访问、base 能量不漏不重；本拓扑热域验收 |
| 四对 DASH | USER_CONFIRMED DSAH→DASH；每对 HBF 同时具有 direct/relay 路径，配对及选择规则显式化 | 两路径共享同一 HBF array/TSV 供给；relay 再占 HBM base/链路；选择、分流、切换、回压及端点 PHY 能量单列；不可双算容量 | 同上；需双路径热点、base/array 非均匀功率域验证 | P1 dual 声明图；当前 HBM16H fixture 不是主线12H；真实选择与共享仲裁未实现 | 两路径独立及并发服务测试、共享瓶颈守恒、不重复完成/计能、四配对正确；本域参考/RC 验收 |

## 三条独立验收轴

| 拓扑 | 配置覆盖 | 热模型验证 | 系统拓扑行为验证 |
|---|---|---|---|
| 8HBF + GDDR | P1_FIXTURE_PASS；研究输入 INCOMPLETE | NOT_VALIDATED | NOT_IMPLEMENTED |
| mixed-direct | P1_FIXTURE_PASS；4+4 研究输入 STATIC_ONLY | NOT_VALIDATED | NOT_IMPLEMENTED |
| relay 4+4 | P1_FIXTURE_PASS；研究输入 INCOMPLETE | NOT_VALIDATED | NOT_IMPLEMENTED |
| DASH 四对 | P1_FIXTURE_PASS；研究输入 INCOMPLETE | NOT_VALIDATED | NOT_IMPLEMENTED |

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

8HBF+GDDR 不继承片上 HBM 冷却假设：另需 GDDR 颗粒组织/位置/封装、PCB 层与
等效导热、板级边界/散热器、GPU 板耦合、外存 controller/PHY 与服务模型。
未知时标 UNKNOWN_BLOCKING_FOR_BOARD_THERMAL / SYSTEM_BEHAVIOR，不填同名 HBM 值。

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
