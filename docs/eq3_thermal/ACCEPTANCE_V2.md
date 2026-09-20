# EQ3 D2 数值验收 v2 冻结说明

状态：方法和输入派生规则已冻结，尚未用本方法读取新的求解输出。该分析只比较
fast candidate 与一份已经选定的 reference；它不使 reference 获得 0.25 K
离散资格，也不构成 `MODEL_FREEZE`。

## 主判据

对每个预登记传感器和每个预登记窗口，reference 幅值、实际时间加权误差及上限为

```
A = max(T_ref) - min(T_ref)
MAE_tw = integral(abs(T_fast - T_ref), dt) / window_duration
L = min(1 K, 0.25 K + 0.05*A)
```

实现对相邻观测的有符号误差作线性插值，并精确积分其绝对值；若误差在区间内变号，
在零点分成两个三角形，不能用端点绝对误差的梯形高估。窗口边界若落在两个观测
之间，也按相同线性模型插值。reference 和 candidate 必须具有与方法文件完全相同
的传感器集合，并具有彼此完全相同的时间戳，否则拒绝评分。每个窗口分别要求
`MAE_tw <= L`。`package:powered_hotspot` 是由实际网格对所有有功组件取 max 的全局
热点传感器，最大绝对误差还须不超过 2 K。本次离线 P2 sensor CSV 没有被控制器
实际消费，因此 `control_sensor_ids` 为空；不能把名字/归约语义相似冒充 active
consumer。与 `CpuService::stack_temperature` 的 max-over-stack 语义对应的八个
`stack:<id>:hotspot` 仅列为 `candidate_control_sensor_ids`，状态为
`DECLARED_READONLY_CANDIDATE_NOT_ACTIVE`。将来只有实际接入后才可移入控制传感器集，
并应用 2 K 最大绝对误差条件。

能量条件独立检查
`abs(Ein - Eboundary - dU) / max(Ein, 1 J) <= 0.001`。分析器不运行求解器，
也不修改任何 raw。

## legacy_v1 与阈值歧义

`legacy_v1` 是单独结果，不参与 v2 veto。它严格保持历史全轨迹定义：CSV 中的
实际样本作算术平均，不把合成初态放入 MAE；NMAE 分母为
`max(max(Tref)-min(Tref), 1 K)`；上限仍为 MAE 1 K、NMAE 5%、名字含
`hotspot` 的传感器最大误差 2 K，并保留原穿越时序公式。v2 窗口中同时给出的
算术 MAE 只用于对照，不冒充历史全轨迹评分。能量结果与 legacy_v1 并列报告，
不改写历史比较器的状态。

301 K 和 330 K 在这两份历史输入的方法文件中仅为保留的诊断探针，不是器件温限。
初态明确来自本任务模型的 300 K。若 reference 有两个相邻观测都落在阈值
`+/-L` 内，结果另记 `THRESHOLD_AMBIGUOUS`。这条“两相邻点”规则是本地工程推定，
不是用户或文献给出的物理标准；它不自动使整个数值模型失败，也不能据此声称阈值
时序确定正确或系统安全。原穿越时序本身若失败，仍使对应 v2 窗口失败。

## 预登记窗口和传感器

冻结文件为：

- `configs/eq3_thermal/acceptance_v2/train100s.json`：完整 0--100 s；从原事件文件
  在查看新输出前合并得到 34 个 source-on 区间；其在 0--100 s 内的 35 个补集
  区间分别作为 cooling 窗口。因此长尾冷却不能稀释任一受激窗口。
- `configs/eq3_thermal/acceptance_v2/development64s.json`：完整 0--64 s；source-on
  为 0--32 s，cooling 为 32--64 s。

两份文件都锁定 275 个传感器 ID、事件与传感器定义原件 SHA-256、窗口派生计数、
初态、热点及控制传感器角色。train 和 development 的传感器定义集合相同。
这些窗口来自源活动 `[start_s,end_s]` 的并集及其补集，不依赖温度输出。

冻结时文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `tools/eq3_acceptance_v2.py` | `51bd993bc1e6c33627ff2c2a162cedc663a332ed12732189963afc68312f97df` |
| `tools/test_eq3_acceptance_v2.py` | `4213ddf68595c11d0fea35b377f99eb5bb28e7ef40b3a5215d728c990746db7f` |
| `train100s.json` | `6bf88c4003bf43c8490a3f4803fbe9071e545d447fb77e54a307202ce9e3469a` |
| `development64s.json` | `1cfcf88c1716d7a3c4e3584191f0fb43ed52c2df6b39ad10b947680eecb98424` |

固定测试覆盖上下界、低温升、非等间隔时间轴、恒温、受激窗口不被冷却稀释、
热点/控制 2 K 上限、有符号误差区间内过零、能量独立失败、阈值歧义、丢帧、漏传感器、预登记传感器
缺失、单位突变，以及 full/excitation/cooling 三类窗口缺失。首次 9/9 通过的收据
位于 `plans/decision-execution-v2/points/d2-d4-fixed-python/`；加入 exact historical
legacy、预登记集合检查和有符号误差过零积分后的最终复验须由统一串行入口产生新收据，
不能沿用旧哈希。

reference 的空间/时间相邻差仍分别以 0.25 K 目标审核。旧结果的任何 v2 重分析
必须标 `RETROSPECTIVE_V2_REVIEW`，不回填或改写旧 `NUMERICAL_FAIL`。

## D3 all-source steady cap 最小实现边界（未实施）

D3 可在既有 `eq3_campaign_rc_runner` 内增加默认关闭的 steady-envelope 模式，复用
同一个 model/events parser、节点所有权、稀疏矩阵类型和分解后端。它只新增内部
CLI/输出路径，不改 thermal core 公共 ABI、瞬态积分、checkpoint 或正常运行语义。
现有 `--equilibrium-diagnostic` 仅在瞬态矩阵 `C/dt+L` 上验证零源等温不动点，
可以复用其验证方式，但它没有组装稳态 `L` 或求 all-source cap，不能直接替代 D3。

最小计算为：从模型静态功率形成 `P_fixed`；按每个允许 source/group 在全输入中的
逐节点最大映射求上限，再跨组求和形成 `P_cap`，避免只取实际同一时刻总功率而低估
“所有允许组可达上限”的包络。构造只含边和边界散热的稳态 `L`，先验证至少一个
散热出口、非负源、对称正热网络和可分解性，再用同一稀疏路径分别解
`L*theta_fixed=P_fixed`、`L*theta_cap=P_cap`。对 `{1,.75,.5,.25}` 逐个仅计算
`T_ambient+theta_fixed+alpha*theta_cap`，选择不超过 380 K 的最大 alpha；没有候选
则返回 `DOMAIN_REDESIGN_REQUIRED`。同时逐节点检查初态是否被该包络覆盖。

收据需保存每个 source/group 的 cap、逐节点合成 cap、矩阵规模/非零元、分解后端、
残差、环境/初态假设、最大温度节点、四个候选结果和选中 alpha。输出状态只能是
`PREDICTED_ENVELOPE`，直到原生 reference/RC 瞬态验证完成。该方案需要修改
`tools/eq3_campaign_rc_runner.cpp` 的私有 CLI 和矩阵组装分支；本项目前仅为方案，
没有运行计算，也没有改 runner。

建议的最小运行矩阵只有两项，且不读取 blind：先在已生成的 development 事件与
generator/source ledger 上做 `CAP-EXTRACT`，逐组保存 cap 与合成守恒，预计单核、
600 s 内、RSS 小于 2 GiB、派生输出小于 100 MiB；再在既有 2 mm/64512 节点 RC
模型上做一次 `STEADY-ENVELOPE-2MM`，单次 factor、两个 RHS 和四个 alpha 的纯代数
评估。既有同规模瞬态收据显示 factor 13.218 s、`factor_L_nnz=19,798,141`；因此
保守申请单进程 12 GiB、任务 16 GiB、600 s、点磁盘 1 GiB，并在启动前按实时余量
复核。这只是资源估计，不从瞬态总耗时外推稳态必然完成。cap 来源必须是冻结 source
ledger 的 17 组允许上限及 generator 的逐节点权重；若 events 无法无歧义恢复组身份，
该点返回 `CAP_SOURCE_IDENTITY_BLOCKED`，不能按 activity ID 或热点位置猜组。
