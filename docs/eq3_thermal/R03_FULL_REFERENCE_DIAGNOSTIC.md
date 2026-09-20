# R03 完整 1 mm reference：只读后处理结果

日期：2026-09-20  
状态：`COMPLETE_DIAGNOSTIC_ONLY`；`REFERENCE_UNQUALIFIED`；blind 未读取。

## R03 完整性

`R03-V2-FULL1MM` 已完成 100 s 求解和观察：5000 个求解帧、0.1 s 观察间隔、
1000 个时刻、275 个传感器。输入能量为 1365 J，温度范围 300–369.012 K，
相对能量残差为 `1.491468238728679e-6`。solve、observe 和 DONE 收据均成功。
机器可读复核在
`plans/decision-execution-v2/points/R03-V2-FULL1MM/read_only_review.json`。

R01/R02/R03 的 `normalized.json` 和 `events.txt` 哈希分别完全相同：

```text
normalized  7a62356faef94e9d16391c39a1e6ccf81d284843c0d9c3ea0d9471d0d364ce06
events      b1674c3ef83b7373d98eb55317d8109d3e55b00bbb057eefc1cf7b6614cf980e
```

三者都是 100 s、1365 J、275 sensors、0.02 s solver step；参考网格依次为
`16x16x63`、`32x32x63`、`64x64x63`。因此完整空间诊断是在同一输入、同一时间窗和
同一观察量上比较，不再把 1 mm 前 4 s 与完整 100 s 混报。

## 完整同窗空间误差

下表的 time-weighted MAE 是先对每个 sensor 在各预注册窗口积分，再按窗口时长合并，
最后对 275 sensors 取均值。最大误差来自实际 CSV 观测行，并保留当时 hotspot cell。
这些汇总用于诊断，不替换既有 0.25 K/1 K/2 K/5%/0.001 标准。

| 相邻网格 | 窗口 | sensor 平均 time-weighted MAE (K) | 最大绝对误差 (K) |
|---|---|---:|---:|
| 4→2 mm | 完整 0–100 s | 0.143305 | 3.253000 |
| 4→2 mm | 全部受激窗口，合计 34 s | 0.233694 | 3.253000 |
| 4→2 mm | 全部冷却窗口，合计 66 s | 0.096741 | 3.253000 |
| 2→1 mm | 完整 0–100 s | 0.064942 | 2.069000 |
| 2→1 mm | 全部受激窗口，合计 34 s | 0.108907 | 2.069000 |
| 2→1 mm | 全部冷却窗口，合计 66 s | 0.042293 | 2.069000 |

4→2 mm 最坏点是 `stack:hbf3:hotspot`、31.0 s：312.007 K 对 315.260 K；
hotspot cell 从 `n60_3_11` 变为 `n60_7_23`。2→1 mm 最坏点是
`component:hbm3.base:hotspot`、15.0 s：314.966 K 对 317.035 K；cell 从
`n4_7_8` 变为 `n4_15_16`。最坏 cell 身份随网格改变，因此没有计算 Richardson
收敛阶。完整空间误差仍超过原有精度要求，P2 reference 继续不合格。

原始后处理点：
`plans/decision-execution-v2/points/r03-spatial-full-v2`。

## 旧 2 mm RC 对 R03 的回顾评分

候选是已有完整 100 s `F01-RC2MM-10MS`，没有重跑求解。它同样有 275 sensors、
1000 个 0.1 s 观察时刻和 1365 J 输入；RC 能量相对残差为
`2.1497271184372237e-11`，通过 0.001 能量标准。它是旧 2 mm/不同 solver-step 的
工件，输入导出哈希不与 R03 完全相同，所以结果仅为 retrospective diagnostic。

- legacy v1：`NUMERICAL_FAIL`；22 sensors 失败，最大误差 2.045948 K，最坏 MAE
  0.156614 K，最坏 normalized MAE 0.002269。
- v2：`NUMERICAL_FAIL_WITH_THRESHOLD_AMBIGUITY`；19250 个 sensor-window scores 中
  427 个失败，其中 full window 有 19 sensors 失败。阈值歧义共 4023 项，单列而不
  自动决定全模型通过或失败。
- 最坏点是 `component:hbm3.base:hotspot`、15.0 s：R03 为 317.035 K，RC 为
  314.989052 K，绝对误差 2.045948 K。
- v2 的最大受激窗口 time-weighted MAE 为 1.550441 K
  (`excitation-08`)，对应限值 0.912050 K；最大冷却窗口 MAE 为 0.742257 K
  (`cooling-09`)，对应限值 0.712350 K。

首次 v2 调用保留为输入校验失败：旧 RC 与 R03 的浮点时间戳拼写不同。单边适配仍
失败后，确认两端分别来自乘法与累加。最终只对派生副本把时间规范到十进制 0.1 s
网格；最大改动 `1.4211e-14 s`，两端各 275000 行的所有非时间字段哈希在适配前后
保持一致。原始 CSV 未修改。适配器为 `tools/eq3_sensor_time_canonicalize.py`，收据在
`r03-reference-time-canonical-v2` 与 `r03-rc2mm-time-canonical-v2` 点中。

v1、v2 结果分别在 `r03-rc2mm-v1-retro` 和
`r03-rc2mm-v2-retro-common-grid`。两次输入接口失败也原样保存在
`r03-rc2mm-v2-retro` 与 `r03-rc2mm-v2-retro-canonical`，没有删除或重写。

## 结论限制

完整 1 mm 轨迹填补了此前“仅前 4 s”的证据缺口，但没有消除空间误差。R03 仍是
`REFERENCE_UNQUALIFIED`，不能据此 Model Freeze。旧 2 mm RC 对完整 R03 的 v1/v2
均未通过；温度量化为 0.001 K，远小于本次最坏空间/RC 差异，但热点位置跨网格变化
阻止用单个全局最大值推算收敛阶。
