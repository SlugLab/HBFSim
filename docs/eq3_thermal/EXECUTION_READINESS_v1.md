# 首个真实执行版本：R01参考资源pilot

状态 **READY_FOR_EXECUTION_APPROVAL_R01_ONLY / PENDING_USER_APPROVAL**。
整套RC/标定矩阵 **尚未READY**；本文件不授权启动。DESIGN_FREEZE v3原manifest保留。
用户已经确认首例方向和内存16GiB任务/12GiB进程，不能把两者解释为执行批准。

## 这次确认实际对应什么

只运行一次R01：4HBM4+4HBF mixed-direct规定功率train，新逐层stock 3D-ICE，
4mm横向网格、63层、16128单元、20ms求解步、100s模拟、0.5s原功率槽按相同
功率精确展开至0.1s后端槽；输出每步全场，观察量按0.1s采样。
初态及上下ambient=300K，top/bottom HTC=1400/25W/(m²K)，侧面绝热，额外接触0
仅已标注研究理想化，有限bond/TIM仍有厚度和热阻。总输入1365J，0自由拟合/0ROM。
目的是检查真实资源和数值收支，不是证明物理已标定或RC已通过。

真实科学输入、源码SHA、stock binary/environment、生成物与测试原始日志哈希
由工作树外 `eq3_thermal/plans/layered-execution-v1/experiment_manifest.json` 绑定；
实际launch在同目录launch.json，确认必须绑定其中canonical hash。

取得对应版本用户确认后才可执行（ROOT为当前工件根，CODE为源码根）：

```sh
python3 "$CODE/tools/eq3_layered_launch.py" run \
  --root "$ROOT" --code-root "$CODE" \
  --manifest "$ROOT/eq3_thermal/plans/layered-execution-v1/experiment_manifest.json" \
  --launch "$ROOT/eq3_thermal/plans/layered-execution-v1/launch.json" \
  --approval "$USER_SUPPLIED_CONFIRMATION"
```

这是真实入口，不是旧四点脚本。无确认时会在创建运行目录/启动backend前拒绝。
审批负例仅使用test double，不曾创建能运行真实任务的假批准。

## 资源、观察量与停止

单CPU线程、进程12GiB、任务16GiB、watchdog600s、每点含输入副本的新输出4GiB、
任务新盘20GiB、GPU0、云费用0，测试串行/总编译≤4。R01场文本约0.726GB再加
输入/元数据；是公式推算不是测量。实际耗时、RSS均UNKNOWN；600s是安全中止而非
预测完成时间。运行时采样VmRSS/VmHWM，记录采样峰值下界而非伪称精确最终峰值；
不可观测则UNKNOWN。原生solver的温区由事后完整场检查，不能声称已有实时温区中断钩子。

完整场用真实cell位置和体积映射275个观察量；边界热流由每步场与同一Robin项
独立重建，储能由sum(CΔT)。解析截断/非有限/维度错、温区300–400K外、输入能量
不一致或相对能量残差>.001均失败并保留raw；场量化0.001K须计入误差解释。
R01成功也仅RESOURCE/REFERENCE_PILOT_REVIEW，不是MODEL_FREEZE。
运行后调用 `eq3_layered_observe.py --run-dir RUN --generated GENERATED --output NEW_DERIVED`
做已绑定分析；不得覆盖raw。新盲测无输出，继续封存至模型/映射锁定。

## 原首轮点表的真实处置（不自动排队）

| 点 | 模拟输入 / 目的 | 本轮准备状态与进入条件 |
|---|---|---|
| R01 | train 4mm/20ms/100s | 已生成、真parser无求解通过；本版唯一待批执行点 |
| R02 | train 2mm/20ms/100s | R01资源/格式/能量检查后另绑定输入；场约2.90GB，尚未跑 |
| R03 | train 1mm/20ms/100s | 场约11.61GB，BLOCKED_OUTPUT_POLICY；不能直接启动 |
| R04 | train 1mm/10ms/100s | 场约23.22GB，同上 |
| R05 | train 1mm/5ms/100s，条件点 | 场约46.45GB，同上；时间差目标达成即不继续减半 |
| R06 | development 64s | 4mm静态输入已生成，原1mm/5ms验证点尚受输出策略阻断 |
| F01/F02 | RC train100s / development64s | 3087节点输入已生成；分槽clock入口与inspect已实现；新数值版本/资源pilot须另批，未运行 |
| R07/F03 | new_blind64s | 输入仅静态检查；模型锁定且相关就绪性闭合后另批执行；未看过求解输出 |

原7参考+3RC、856模拟秒只是候选，不是机械总次数上限。当前可确认执行1点，
不冒充全部10点已就绪。高分辨率输出最小后续方案：审查流式场归约/边界逐步积分，
保留原始可重建证据和热点；它是待审核局部输出适配，不擅自改参考引擎或扩大磁盘。
RC最小后续方案：确认显式3087节点版本，再小pilot验证缓存/耗时，不回到单等温sink。

未来数值比较沿用：相邻离散差目标.25K（不是连续误差上界）、逐区MAE≤1K、
热点max误差≤2K、normalized MAE≤.05、能量残差≤.001；穿越误差按已有预检。
只有参考离散/输出与RC误差/独立盲测全部通过才MODEL_FREEZE。扩大输入、方法、
物理假设或资源须更新差异并确认，不因一次pilot通过自动排完整矩阵。
