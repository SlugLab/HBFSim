# EQ3-MINIMAL-REPAIR-v1：P2 最小诊断修复

状态：局部补丁和固定 CPU 验证已完成。本文不改变
P2 `BLOCKED_WITH_EVIDENCE`、`MODEL_FREEZE` 未成立及盲测封存状态。

## 证据与分类

| 项目 | 分类 | 证据与不变量 | 本轮处理 |
|---|---|---|---|
| RC 越 300–400 K 域时只报通用错误 | `CONFIRMED_BUG`（诊断证据缺失） | `tools/eq3_campaign_rc_runner.cpp` 原路径在 trial solve 后直接 `require`；不变量是失败仍失败，同时必须区分最后合法状态和失败 trial | 写独立 last-valid/trial CSV 和失败 JSON；仍返回非零，不 clamp、不改步长/输入/阈值 |
| 2 mm development 超过 400 K | `DOMAIN_FAILURE` | 独立参考最高 400.911 K；旧 RC 仅保留最后完整 31.1 s/399.975501 K，失败 trial 数值原为 `UNKNOWN` | 不修改物理；新诊断只供未来受影响的小型/正式运行使用，不回填旧 raw |
| 4→2 mm、2→1 mm 空间差 | `NUMERICAL_ACCURACY_LIMIT` | 完整 4→2 mm 最大差 3.253 K；共同前 4 s 结果见下节 | 保留旧 0.25 K 标准和失败状态；共同窗口仅 `DIAGNOSTIC_ONLY` |
| 1 mm 完整 100 s | `RESOURCE_BLOCKED` | 已有 4 s 运行 328.012 s，完整运行估计 2000–2600 s，超过 600 s | 本轮不重启、不切段；长窗口/后端变更仍待确认 |
| 比较器固定初态 300 K、探针 301/330 K | `CONFIRMED_BUG`（通用分析接口） | 实际代码硬编码，导致其他配置被错误解释；历史数据确为 300/301/330 K | 增加可选 `initial_k`/`probes_k`，CLI 为 `--initial-k`/重复 `--probe-k`；默认值保持历史等价 |

以上 P2 状态和数值来自已登记 campaign raw/result。新增的共同窗口归约已由
统一安全入口只读执行，结果为 `DIAGNOSTIC_ONLY`；它不增加物理校准证据。

## 失败快照合同

`eq3_campaign_rc_runner` 在有限但越域的 trial 上写：

- `rc_failure_last_valid.csv`：最后成功积分状态；
- `rc_failure_trial.csv`：实际算出的失败 trial 状态；
- `rc_failure_diagnostic.json`：准确 target time、步号、输入 slot 区间、域版本、
  节点/组/die、越域温度、已完成区间能量以及退出原因。

坐标不在当前 model text 公共类型中，明确记录
`UNKNOWN_NOT_EXPOSED_BY_MODEL_TEXT_API`，不从 ID 推断。模型、events 和 runner
SHA-256 可由现有 launcher 通过新增可选参数传入；未传入时记录
`UNKNOWN_NOT_SUPPLIED`，不伪造哈希。失败 trial 步的能量积分均为
`UNKNOWN_NOT_INTEGRATED`，已完成步的输入、储能、边界损失和残差单列。

若稀疏 solve 本身失败，trial 向量不可信：只保存最后合法状态，状态为
`NUMERICAL_FAILURE`，trial 明确 `UNKNOWN_SOLVER_FAILURE`。输入解析阶段的 NaN/Inf
属于 `INPUT_FAILURE` 边界，没有已计算 trial，因此不会伪造状态文件。所有诊断
文件拒绝覆盖；写盘失败仍向调用者返回失败。

成功路径仍先验证 trial，再提交为当前状态及累计能量。该提交顺序只让失败证据
可见；矩阵、方程、正常步数值、采样点和能量积分未改变。

## 方程、单位和缓存静态审核

runner 的矩阵仍为：对角 `C/dt + G_boundary + sum(G_edge)`，非对角
`-G_edge`；theta 右端为 `C/dt*theta + P + G_boundary*(T_boundary-origin)`。
这与 `C dT/dt = P + sum G(T_neighbor-T) + G_boundary(T_boundary-T)` 的后向欧拉
离散一致。解析器继续要求 `C>0 J/K`、`G_edge>0 W/K`、边界导热非负、功率有限
非负；时间单位为秒，温度为 K。

runner 对固定 `dt` 只构造/分解一次矩阵。通用 `ThermalModel` 的 factor cache
以 `dt` 为 key；`reset()` 和 `restore()` 均清空 cache，配置在对象生命周期内
不可变，因此当前没有发现 stale-factor 路径。既有固定测试覆盖单节点解析解、
步长收敛、内部导热守恒、非 300 K 温度平移、稀疏/稠密同方程及能量一致性；
本轮补充三节点顺序置换等价测试。该审核没有改热方程、物性或公共 ABI。

## 既有 raw 的同窗口比较

输入均为原 train，时间戳 0.1–4.0 s，共 40 帧、275 个相同 sensor；比较保持
初态 300 K、探针 301/330 K 和旧 0.25 K reference 相邻网格阈值。

| 网格对 | 共同窗口最大绝对差 | 最坏 MAE | 状态 |
|---|---:|---:|---|
| 4 mm R01 → 2 mm R02 | 2.710 K | 1.153575 K | `NUMERICAL_FAIL / DIAGNOSTIC_ONLY` |
| 2 mm R02 → 1 mm R03 prefix | 1.277 K | 0.546275 K（最坏 sensor） | `NUMERICAL_FAIL / DIAGNOSTIC_ONLY` |

最大差均出现在 HBF 顶层 die/stack hotspot 一组观察量。差值随网格细化减小，
但两级都未达到 0.25 K，且前 4 s 只有 GPU 激励，不覆盖后续 memory/base 激励；
因此不能由此计算或宣称完整 100 s 收敛阶，也不能替代正式参考验收。

## 固定验证范围

统一串行入口实际执行了：原 v2 binary 的微小越域复现（按预期在原 domain
错误后因不存在 `rc_failure_diagnostic.json` 而失败）、新 runner 编译、
runner/compare 两组固定测试。最终共 15 项：13 项通过，2 项依赖 checkout 外生成
candidate 的可选测试跳过；跳过项不计作通过。首次测试曾因 NaN 输入的预期错误
文本与现有 parser 的实际 `malformed node record` 不同而失败，修正测试契约后重跑
通过，未修改 parser。runner 测试覆盖有限越域快照、
非有限输入拒绝、证据拒绝覆盖、稀疏/稠密一致、能量守恒、温度平移、节点重排。
这些都是软件验证，不是新数值配置、昂贵参考或物理校准。

原始证据位于 `eq3_thermal/plans/minimal-repair-v1/points/`：
`P2-ORIGINAL-DOMAIN-REPRO`、`P2-RC-DIAG-BUILD2`、
`P2-RC-DIAG-TEST`（保留的首轮测试失败）、`P2-RC-DIAG-TEST2`（最终 PASS）和
`P2-COMMON-FIRST4S`。共同窗口完整派生结果为
`eq3_thermal/plans/minimal-repair-v1/P2-COMMON-FIRST4S-DIAGNOSTIC.json`。

未执行任何完整 1 mm solve、GPU 工作、盲测、阈值变更、物性变更或公共 ABI/
checkpoint 变更。P2 未冻结原因仍是参考空间精度不合格、1 mm 完整参考资源受阻，
以及 development 轨迹越出已声明常物性域。
