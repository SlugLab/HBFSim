# 读取速率驱动的逐栈热模拟 v1

已实现并实际运行，无需生成NAND读写事务。采用用户确认的50pJ/B增量能量模型，保留完整耦合热网络。当前接口对齐OCP v0.7.0 Grade2：每栈16通道，每通道有效上限96GB/s，每栈合计1.536TB/s；原80W@1.6TB/s能量锚点不重标，因此Grade2满速为76.8W。

## 输入、源与温度的粒度

| 层次 | 当前实际能力 | 限制 |
|---|---|---|
| 负载 | 每stack、每channel的分段规定有效读取率；整数ns半开时段 | 不是MQSim实测吞吐，无请求排队、重试或协议仲裁 |
| 容量约束 | 每通道96GB/s、每栈合计1.536TB/s；超限报错 | 不是静默截断或重新解释为延迟 |
| 热源 | 按显式channel→die映射累加40pJ/B到实际die；10pJ/B到本栈base一次 | 一通道/一die是独立工程映射；未获得plane/page物理位置 |
| 温度求解 | 既有2mm面内分辨率、逐层几何、64512cell/255entity/275sensor，20ms步长 | 原P2空间精度限制保留；小于20ms峰值不保证解析 |
| 未访问die | 增量读取热源为零，但保留全部热耦合 | 不等于器件真实idle功耗为零 |
| 输出 | 各die/base的均温和热点、每stack热点、GPU/HBM被动受热、能量与冷却曲线 | 原400K模型域失败不截断，不升级MODEL_FREEZE |

系数array40pJ/B、base10pJ/B属于USER_CONFIRMED_SCENARIO_ASSUMPTION，不是OCP或Sandisk实测能耗。300K是模型参考点，未叠加真实idle/GPU背景时报告增量温升；不能用该参考温度加增量宣称产品实际工作结温。base系数整体覆盖本方案的base活动，不另加一次PHY能量。

| 活跃通道数（每通道满额） | 合计有效读率 | 条件读取增量功耗 |
|---|---:|---:|
| 1 | 96GB/s | 4.8W |
| 4 | 384GB/s | 19.2W |
| 8 | 768GB/s | 38.4W |
| 16 | 1536GB/s | 76.8W |

来源：[OCP v0.7.0，第16页表2/4](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf)。64bit×16GT/s÷8×75%=96GB/s。此前100GB/s/channel是从SanDisk总带宽均分的未运行推算，已由此有明确来源的配置替代；旧预检保留，不当作运行结果。

## 已完成的匹配对照

两个3.2s输入，160个热窗口。hbf0依次为.384/.768/1.536TB/s，再回到.384；hbf1固定.384、hbf2固定.768，hbf3无读取，最后1s共同冷却。两次仅在1.4..2.2s改变hbf0的空间分布：16通道×24GB/s，或4通道×96GB/s。其余功率、时间、初态、求解器与网络一致。

每次输入184.32J，服务输入对账最大误差1.42e-13J，最终热平衡残差约2.80e-9J。耗时16.91/16.74s，子进程峰值约336MiB，输出约14MB/点。没有MQSim、实际I/O、GPU负载或全局环境修改。转换器6项固定测试通过，覆盖分段积分、实体发现、能量分配、通道容量和拒绝非法输入。

均匀分配实例hbf0最高增量43.456K；没有读取的hbf3最高增量10.355K，GPU无额外自身功率却增温约12.000K，说明共享热网络确实消费了新分源输入。它们属于本次有限时长/空间分布和能量假设的结果，不能当各读取率的稳态温度表。

两臂全程hbf0峰值相同，因为峰值发生在分布改变之前；必须比较1.4..2.2s同一时刻各die和base，而不是只看全局峰值。最终对照显示：1.4s前全部entity/sensor逐帧差为0K；改变分布期间集中减均匀的hbf0热点差为+0.086733..+0.118583K，die/base局部差可正可负（−0.047440..+0.119242K）。这是本离散热网络的成对结果，不可提升为已确认0.1K产品预测精度。最终对照和图见工件`PAIR-ANALYSIS01`。

## 交付与复现

- 新转换器与runner：`experiments/eq3_rate_thermal/`。默认无消费者自动启用，需要显式profile/schedule/model/binary路径。
- 接口→生产者→消费者：schedule逐通道速率 → `build_windows`窗口分源能量 → 既有`ThermalService.advance` → 不变的稀疏RC服务 → 逐die/base/stack温度与图。
- 实际预检、输入、运行索引、原始帧、能量、完成收据：外层`eq3_thermal/plans/rate-thermal-v1/`，包括`PREFLIGHT_OCP_V3.json`、`RUN_INDEX.json`及两个OCP-G2点。
- 旧MQSim实验按用户目标转向暂停：57点完成、3旧失败保留、9点未执行；`isolated-maintenance-campaign-v1/campaign-ocp4k-v4/SCOPE_PIVOT_SUMMARY.json`。

未实现也未宣称：真实HBF绝对功耗标定、通道静态开销、每plane/page热点、真实ECC/读重试、Grade3满速153.6W的扩大功率域。当前Grade2方案已可直接更换各栈速率与die分布输入，保持已确认的物理/能量域和完整热网络。
