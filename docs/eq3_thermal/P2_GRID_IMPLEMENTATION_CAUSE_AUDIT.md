# P2 2→1 mm 网格温差实现原因审计

日期：2026-09-20  
范围：只读审计 `eq3_layered_export.py`、实际 R03 native 3D-ICE 源码、已保存的 R02/R03 输入与传感器结果；未启动求解、构建或大型后处理，也未修改物理参数或实现。

## 结论

`component:hbm3.base:hotspot` 在 15.0 s 的 2→1 mm 差值为 **2.069 K**：R02 2 mm 是 314.966 K（`n4_7_8`），R03 1 mm 是 317.035 K（`n4_15_16`）。本次逐层源码审计没有发现能直接解释该差值的坐标、单位、半单元界面、各向异性轴、功率重复、z 层倒置、Robin 边界或输出展平错误。因此不能把它分类为 `CONFIRMED_BUG`。

现有证据支持把该现象分类为 **NUMERICAL_ACCURACY_LIMIT（局部 cell-max 对网格敏感）**，但这不是“现实器件会相差 2.069 K”的证据。15 s 的热点位于 `hbm3.base` 的北西角附近；同一时段 `hbm3` 全部功率为 0，而与其仅在平面角点相接的 `hbm2` 的 12 个媒体 die 合计输入 64 W。1 mm 网格解析出更窄、更高的角点温度；2 mm 网格把该区域平均到四倍平面面积。与此同时，同一组件的体积均温只从 306.290083 K 变为 306.390026 K，差约 **0.099943 K**。这是“局部最大值支持域改变”而非全组件热量明显不一致的强证据。

物理真实性仍为 **UNKNOWN**：硅材料是 `PROXY`，侧面绝热、残余接触热阻为零且几何为情景假设。代码自洽不能把这些假设升级为器件实测事实。

## 审计对象与可比性

- R02：`plans/campaign-v1/points/R02`，32×32×63、64,512 cells、2 mm、0.02 s step、100 s。
- R03：`plans/decision-execution-v2/points/R03-V2-FULL1MM`，64×64×63、258,048 cells、1 mm、0.02 s step、100 s。
- 两者的 `normalized.json` SHA-256 都是 `7a62356f...64ce06`；总输入能量都是 1365 J，总热容分别为 21.1572437888 与 21.157243788800002 J/K。因此比较使用相同几何、材料、边界和功率历史。
- R02 native 3D-ICE SHA-256 是 `240b598c...7244a7`；R03 storage-ownership v2 native 3D-ICE 是 `ace42d254...ca1d8`。`cc36d620...126e6` 是 `eq3_campaign_stream.py` 的哈希，不是数值引擎哈希。v2 的 `reference/build/3d-ice-storage-owned-u125-v2/source.patch` 只改 factor storage 生命周期和 `sp_ienv(7)` 容量；`DESIGN.md` 明确保留方程、CSC 值、排序和积分。实际 `add_solid_column` 组装式与 stock 对应代码相同。下述 D3 线性恢复又在完整 100 s 传感器输出层排除了这两个 native backend 的可见差异。

### 同网格跨 backend 的 DERIVED_LINEAR 排除

已有 `D3-V2-TRAIN-REF2MM` 使用 R03 相同的 `ace42d254...ca1d8` native backend、2 mm 网格、完整 100 s，但把全部可变热源统一乘以 0.25。只读前置检查确认：

- 除功率数值和功率来源状态标签外，几何、材料、边界、传感器和温度域完全相同；
- 200 个功率区间、组件键以及区间端点相同；每个 `power_w`、每组件积分能量和总能量都精确为 R02 的 0.25；
- 初始温度、top ambient、bottom ambient 都是 300 K，没有非零固定源。

常物性线性系统因此允许在不新求解的情况下恢复

`T_original_pred = 300 K + 4 × (T_D3 - 300 K)`。

对 R02 与恢复后的 D3 全部 **275,000 个 sensor/time rows** 对照，最大绝对差为 **0.002000000000294 K**（91.8 s，`component:hbm3.die3:mean`），小于两端三位小数输出传播界 `0.0005 + 4×0.0005 = 0.0025 K`。摘要保存在 `plans/decision-execution-v2/points/backend-linear-sanity-v2/summary.json`，runner 结果为 PASS；最初把功率 provenance/组件积分也当作非功率输入的严格检查被保留为 `backend-linear-sanity` FAILED，随后只排除已证明属于功率缩放的字段。

有 31,848 个 hotspot row 的回报 cell id 不同；这些发生在三位小数场输出经 0.25 缩放后产生的并列/量化选择中。正温度缩放下 hotspot 温度本身仍满足上述 0.0025 K 界，但不能用 DERIVED_LINEAR 恢复唯一 argmax cell 身份。因此本检查证明的是完整传感器温度层的 backend 等价，不宣称逐 cell 原始场字节相同，也不是新 native 运行或物理验证。

## 1. 网格所有权与源体积守恒

`tools/eq3_layered_export.py`：

- `axes`（23–45）在 x/y 使用统一 pitch 前要求所有实体边界整除，不做坐标 snapping；z 轴始终保留全部几何平面。
- `discretize`（48–89）以 cell center 判定平面所有权，以完整 z slab 判定活动组件；重叠和未填充都会失败。
- 同一函数 85–87 行逐组件核对拥有 cell 的总体积等于组件体积。
- R03 的 `hbm3.base` 是 `[0.016,0,0.001205]` 起、尺寸 `[0.012,0.016,0.00005]` m 的单层硅实体；1 mm 时拥有 192 cells，2 mm 时拥有 48 cells，二者都覆盖相同 9.6e-9 m³。

`reference_files`（135–183）为每个 cell 生成一个同尺寸 floorplan rectangle，并把该 cell 的功率设为 `component_power × cell_volume/component_volume`（157–169）。实际 R03 `L4.flp` 中 `C17360` 的位置/尺寸为 `(16000,15000,1000,1000)` µm；`L4.layout` 把同一区域放进 `M3`，而 `package.stk` 中 `M3` 是各向同性 150 W/(m·K) 的硅。

native 的 `floorplan_matrix_fill`（`reference/build/3d-ice-stock-e0bb685-gnu17-longint/sources/floorplan_matrix.c:150–223`）按 rectangle 与 cell 的重叠面积除以 rectangle 面积；此处一个 rectangle 恰好覆盖一个 cell，矩阵权重为 1。`power_grid_fill`（同树 `sources/power_grid.c:811–907`）随后只调用一次 `fill_sources_floorplan`。因此 exporter 的体积分配没有被 native 再按组件面积复制。

**判定：** 未发现源功率重复或遗漏。生成收据还独立报告 `emitted_energy_j=1365.0000000000107`，与输入 1365 J 的差为浮点舍入量；该收据不能单独证明 native 矩阵正确，但与上述生产者/消费者代码一致。

**可证伪假设 S1：** 若某个 floorplan rectangle 因浮点边界被 native 映射到两个 cells，则其 overlap 权重之和仍应为 1，但空间源会跨界。最小检查是只构建 floorplan matrix 并导出 `C17360` 以及 hbm2 角点元素的非零 row/weight；应恰有一个 row、权重 1。当前输入边界均在整 µm 网格线上，源码和已解析结果不支持该假设，但尚无该矩阵的运行时 dump。

## 2. 半 cell 界面与各向异性

exporter 的独立网络函数 `network`（92–113）对每个正方向邻居使用

`G = A / (d1/(2 k1_axis) + d2/(2 k2_axis))`，

即两个半 cell 热阻串联；该网络用于 RC 导出和观测能量账，不是 native reference 求解器本身。

实际 native 路径为：

- `sources/layer.c:get_thermal_conductivity`（186–226）先按 layout 查当前位置材料，再按 direction 0/1/2 取 x/y/z 导热率。
- parser `bison/stack_description_parser.y:295–339` 把三个输入值依次写入 `[0],[1],[2]`，没有轴交换。
- `sources/thermal_grid.c:get_conductance_top/bottom`（963–1288）和 `north/south/east/west`（1292–1642）返回本 cell 到界面的半 cell conductance。
- 实际 v2 `system_matrix.c:add_solid_column`（732–1045）在 x、y、z 六个方向都用 `PARALLEL(g_side_this,g_opposite_neighbor)` 合成两个半 cell 的串联热阻；对角项加同一 conductance，非对角项写负值。
- `get_capacity`（stock `sources/thermal_grid.c:433–530`）使用 layout 查得的体积热容乘实际 cell 长、宽、高，和 exporter 的 `volume × Cv` 一致。

热点 cell `n4_15_16` 本身是各向同性硅 `[150,150,150]` W/(m·K)。它的西、北邻居均为各向同性 underfill `[1.5,1.5,1.5]`；下邻居 `hbm3.attach`、上邻居 `hbm3.bond0` 均为 bond `[5.5,5.5,113]`。因此，即使把硅的 x/y 轴互换也不会改变该热点；bond 的高 z 导热率由 direction 2 消费，源码路径与输入顺序一致。

用 R03 cell 尺寸直接代入，热点到西/北 underfill 的界面导热约为 1.485×10⁻⁴ W/K；到下方 25 µm bond 的界面约 3.606 W/K，到上方 8 µm bond 约 4.947 W/K。native 半 cell `PARALLEL` 与 exporter 公式得到相同量级和表达式。

**判定：** 半 cell 和各向异性实现未见不一致；`hbm3.base` 位于内部 z=4，任何 top/bottom 特例不会直接作用于该 cell。

**可证伪假设 I1：** 用两种材料、两个 cells 的解析稳态 fixture 分别沿 x/y/z 放置，矩阵界面项应等于上式且对称。仓库已有一般方程证据，但没有绑定本次材料和尺寸的矩阵项收据。若失败才可定为 native interface bug。

## 3. z 位置、stack 顺序与 Robin 边界

exporter 按几何低 z 到高 z 生成 `L0..L62`，但在 `package.stk` 中以 `S62..S0` 逆序声明（`reference_files` 172–181）。这符合 3D-ICE parser 的约定：`stack_description_parser.y:1198–1248` 明确把列表首元素当 top-most、尾元素当 bottom-most，然后从尾到首赋递增 layer offset。因此 `S4/D4/L4` 的 native offset 是 4，与 `reference_grid.json` 的 z index 4 相同。

观测器为每层声明 `Tmap(Sz,"field_z.txt",step)`；native `stack_element_print_thermal_map`（stock `sources/stack_element.c:229–286`）先跳到该 stack element 的 source layer offset，再按 row 外层、column 内层输出。`eq3_layered_observe.py:55–67` 也按行读取并以 x/column 为最快维；123–151 行按 `field_0..field_62` 递增拼接。因此 `index = z·nx·ny + y·nx + x` 与 exporter 的 cell 顺序一致。

边界方面，exporter 只允许 top/bottom Robin 和绝热 sides。`package.stk` 把 1400 与 25 W/(m²·K) 分别缩放为 1.4e-9 与 2.5e-11 W/(µm²·K)。native `get_conductance_top` 的 ambient 分支（1012–1027）以及 `get_conductance_bottom` 的 PCB 分支（1202–1217）都实现 `A/(dz/(2k)+1/h)`；`add_solid_column` 仅在最顶/最底层把该项加到对角，`power_grid_fill` 把 `G·Tamb` 加入 RHS。z=4 的 `hbm3.base` 不会误用 Robin 边界。

**判定：** 未发现 z 反转、层错配、边界单位或边界施加到内部层的证据。

**可证伪假设 Z1：** 读取 native 解析后的 stack dump，应显示 `S0 offset=0`、`S4 offset=4`、`S62 offset=62`，top sink 连到 62、bottom sink 连到 0。源码已强支持此结果，但本轮没有生成运行时 dump。

## 4. 15 s 热点的空间和功率因果

R03 1 mm 最热点 `n4_15_16`：

- 范围 x=[16,17] mm、y=[15,16] mm、z=[1.205,1.255] mm；中心 (16.5,15.5,1.230) mm。
- 西边和北边紧邻 underfill；东边和南边仍为 `hbm3.base`。
- 它是 `hbm3.base` 的北西角 cell。

R02 2 mm 对应最热点 `n4_7_8` 覆盖 x=[16,18] mm、y=[14,16] mm，中心 (17,15,1.230) mm。两个网格的 winner 覆盖同一几何角，但 2 mm cell 的平面面积和热容都是 1 mm winner 的 4 倍。

组件布局显示：`hbm3.base` 占 x=[16,28] mm、y=[0,16] mm；`hbm2.base` 占 x=[0,16] mm、y=[16,28] mm。二者只在 (16,16) mm 角点相接。14.5–15.0 s 时 `hbm2.die0..die11` 每层 5.333333 W，合计 64 W；`hbm3.base` 以及 `hbm3` 所有媒体 die 都是 0 W。传感器轨迹在 14.5–15.0 s 从 313.829 K 连续升至 317.035 K，15.1 s 降至 316.682 K，与 15.0 s 激励切换一致。

离散矩阵只有面邻接，没有角点直接导热。hbm2 的热量要通过角点周围的 underfill/相邻层传到 hbm3。减小 xy cell 后，这条窄空间路径以及局部温度梯度得到更高分辨率；`max` 取的是一个 cell center 的最高值，不是固定面积传感器平均。因此该位置本来就是最容易出现非单调或慢收敛 cell-max 的位置。

**可证伪假设 H1（当前首选）：** 2.069 K 主要是角点附近的空间离散误差。若在相同物理面积上比较，例如对 1 mm 的 x=[16,18], y=[14,16] 四个 cells 做体积平均，再与 2 mm winner 比，差应显著小于 2.069 K；同样，沿离角点距离的剖面应显示差值快速衰减。该检查需要场数据任务读取既有 raw；本审计不重复该任务。

**可证伪假设 H2：** 若 1 mm 的更高峰是输出索引错位，则 `n4_15_16` 周围的场值不会形成以 (16,16) mm 为中心、向 hbm3 内部衰减的连续梯度，或热点会落到非 `hbm3.base` 材料。现有 mapping 已确认该 cell 为 M3 silicon，但邻域温度剖面仍由独立场数据任务验证。

## 5. Hotspot reduction 的含义

`eq3_layered_observe.py:sensor_mapping`（70–85）对 `max` 传感器取组件所有 cell index 的集合；`readings`（88–96）直接选温度最高的 cell，并回报其 grid cell id。没有地址推断、插值或额外平滑。native Tmap 使用 `%7.3f` 输出（`stack_element.c:280`），量化上限约 0.0005 K，不能解释 2.069 K。

在 15.0 s：

| 指标 | 2 mm | 1 mm | 绝对差 |
|---|---:|---:|---:|
| `hbm3.base` hotspot | 314.966 K | 317.035 K | 2.069 K |
| `hbm3.base` volume mean | 306.290083 K | 306.390026 K | 0.099943 K |

所以 2.069 K 是两个不同尺寸 cell 支持域上的离散最大值之差，不能解释成固定位置点温度误差，更不能解释成现实温度波动。

## 6. 剩余未知与最小下一步

1. **场形状证据（最高信息量，读已有 raw）：** 对 14.9、15.0、15.1 s 提取 hbm3/hbm2 角点邻域，不做全局大后处理。验证 H1/H2，报告同面积聚合、剖面和材料边界。
2. **矩阵单元测试：** 静态构造两材料 x/y/z 界面，读取单个矩阵系数和 floorplan 权重；这是验证实现的固定小测试，不改变物理。当前没有必要先修改求解器。
3. **物理结论边界：** 即便 0.5 mm 进一步收敛，也只能说明当前常物性、理想接触、绝热侧边模型的离散收敛；不能验证真实 HBM/HBF 封装温度。器件级结论仍需材料、接触和边界标定。

当前最窄结论是：**没有确认的实现 bug；2.069 K 有明确的角点局部化和 cell-max 网格敏感机制，旧 0.25 K 标准仍然失败，P2 不冻结。**
