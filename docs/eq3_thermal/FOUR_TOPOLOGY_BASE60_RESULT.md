# 四拓扑基础读取矩阵：已完成结果与范围

60/60点完成并通过收据、字节、分源能量、时间轴与逐栈覆盖审计。条件：每栈16通道×96GB/s，新鲜媒体供给1.536TB/s；40+10pJ/物理读取B；20s新增输入+10s继续排空；完整耦合热网络。输入1.920TB/s是需求，不是宣称硬件可交付该速率。

下表均为相同每HBF栈压力。交付比例是30s结束累计有效交付/20s累计到达；不能当成稳态每栈速度。四栈与八栈总输入不同；相同总需求比较尚须单独配对，不混入本表。

| 拓扑 | 每栈需求TB/s | 策略 | 交付比例 | HBF峰值K | 末尾积压TB |
|---|---:|---|---:|---:|---:|
| all_hbf_direct | 0.384 | guard_only | 100.000% | 334.079 | 0.000 |
| all_hbf_direct | 0.384 | read_rate_feedback_thermal_guard_v1 | 100.000% | 334.079 | 0.000 |
| all_hbf_direct | 0.384 | thermal_hysteresis_guard | 100.000% | 334.079 | 0.000 |
| all_hbf_direct | 0.768 | guard_only | 100.000% | 365.545 | 0.000 |
| all_hbf_direct | 0.768 | read_rate_feedback_thermal_guard_v1 | 100.000% | 363.225 | 0.000 |
| all_hbf_direct | 0.768 | thermal_hysteresis_guard | 100.000% | 363.247 | 0.000 |
| all_hbf_direct | 1.152 | guard_only | 95.600% | 365.540 | 8.110 |
| all_hbf_direct | 1.152 | read_rate_feedback_thermal_guard_v1 | 91.333% | 363.224 | 15.974 |
| all_hbf_direct | 1.152 | thermal_hysteresis_guard | 94.867% | 363.250 | 9.462 |
| all_hbf_direct | 1.536 | guard_only | 72.500% | 365.551 | 67.584 |
| all_hbf_direct | 1.536 | read_rate_feedback_thermal_guard_v1 | 68.860% | 363.222 | 76.530 |
| all_hbf_direct | 1.536 | thermal_hysteresis_guard | 71.700% | 363.247 | 69.550 |
| all_hbf_direct | 1.920 | guard_only | 58.000% | 365.551 | 129.024 |
| all_hbf_direct | 1.920 | read_rate_feedback_thermal_guard_v1 | 55.088% | 363.222 | 137.970 |
| all_hbf_direct | 1.920 | thermal_hysteresis_guard | 57.360% | 363.247 | 130.990 |
| dash | 0.384 | guard_only | 100.000% | 331.938 | 0.000 |
| dash | 0.384 | read_rate_feedback_thermal_guard_v1 | 100.000% | 331.938 | 0.000 |
| dash | 0.384 | thermal_hysteresis_guard | 100.000% | 331.938 | 0.000 |
| dash | 0.768 | guard_only | 100.000% | 365.224 | 0.000 |
| dash | 0.768 | read_rate_feedback_thermal_guard_v1 | 100.000% | 363.158 | 0.000 |
| dash | 0.768 | thermal_hysteresis_guard | 100.000% | 363.164 | 0.000 |
| dash | 1.152 | guard_only | 100.000% | 365.523 | 0.000 |
| dash | 1.152 | read_rate_feedback_thermal_guard_v1 | 99.243% | 363.161 | 0.697 |
| dash | 1.152 | thermal_hysteresis_guard | 100.000% | 363.170 | 0.000 |
| dash | 1.536 | guard_only | 81.900% | 365.522 | 22.241 |
| dash | 1.536 | read_rate_feedback_thermal_guard_v1 | 76.280% | 363.162 | 29.147 |
| dash | 1.536 | thermal_hysteresis_guard | 77.200% | 363.169 | 28.017 |
| dash | 1.920 | guard_only | 65.520% | 365.522 | 52.961 |
| dash | 1.920 | read_rate_feedback_thermal_guard_v1 | 61.024% | 363.162 | 59.867 |
| dash | 1.920 | thermal_hysteresis_guard | 61.760% | 363.169 | 58.737 |
| mixed_direct | 0.384 | guard_only | 100.000% | 331.848 | 0.000 |
| mixed_direct | 0.384 | read_rate_feedback_thermal_guard_v1 | 100.000% | 331.848 | 0.000 |
| mixed_direct | 0.384 | thermal_hysteresis_guard | 100.000% | 331.848 | 0.000 |
| mixed_direct | 0.768 | guard_only | 100.000% | 365.166 | 0.000 |
| mixed_direct | 0.768 | read_rate_feedback_thermal_guard_v1 | 100.000% | 363.155 | 0.000 |
| mixed_direct | 0.768 | thermal_hysteresis_guard | 100.000% | 363.158 | 0.000 |
| mixed_direct | 1.152 | guard_only | 100.000% | 365.519 | 0.000 |
| mixed_direct | 1.152 | read_rate_feedback_thermal_guard_v1 | 99.243% | 363.160 | 0.697 |
| mixed_direct | 1.152 | thermal_hysteresis_guard | 100.000% | 363.166 | 0.000 |
| mixed_direct | 1.536 | guard_only | 82.250% | 365.519 | 21.811 |
| mixed_direct | 1.536 | read_rate_feedback_thermal_guard_v1 | 76.377% | 363.162 | 29.027 |
| mixed_direct | 1.536 | thermal_hysteresis_guard | 77.325% | 363.166 | 27.863 |
| mixed_direct | 1.920 | guard_only | 65.800% | 365.519 | 52.531 |
| mixed_direct | 1.920 | read_rate_feedback_thermal_guard_v1 | 61.102% | 363.162 | 59.747 |
| mixed_direct | 1.920 | thermal_hysteresis_guard | 61.860% | 363.166 | 58.583 |
| relay | 0.384 | guard_only | 100.000% | 332.027 | 0.000 |
| relay | 0.384 | read_rate_feedback_thermal_guard_v1 | 100.000% | 332.027 | 0.000 |
| relay | 0.384 | thermal_hysteresis_guard | 100.000% | 332.027 | 0.000 |
| relay | 0.768 | guard_only | 100.000% | 365.364 | 0.000 |
| relay | 0.768 | read_rate_feedback_thermal_guard_v1 | 100.000% | 363.164 | 0.000 |
| relay | 0.768 | thermal_hysteresis_guard | 100.000% | 363.168 | 0.000 |
| relay | 1.152 | guard_only | 99.983% | 365.524 | 0.015 |
| relay | 1.152 | read_rate_feedback_thermal_guard_v1 | 98.717% | 363.168 | 1.183 |
| relay | 1.152 | thermal_hysteresis_guard | 100.000% | 363.173 | 0.000 |
| relay | 1.536 | guard_only | 81.500% | 365.526 | 22.733 |
| relay | 1.536 | read_rate_feedback_thermal_guard_v1 | 75.867% | 363.168 | 29.654 |
| relay | 1.536 | thermal_hysteresis_guard | 76.975% | 363.174 | 28.293 |
| relay | 1.920 | guard_only | 65.200% | 365.526 | 53.453 |
| relay | 1.920 | read_rate_feedback_thermal_guard_v1 | 60.694% | 363.168 | 60.374 |
| relay | 1.920 | thermal_hysteresis_guard | 61.580% | 363.174 | 59.013 |

## 解读

本组基线未启用温度历史ECC代理，也没有维护请求。它验证路径/资源—能量—热—未来保护闭环，不证明完整可靠性优化。混合直连0.384TB/s保持Normal；0.768及以上已经观察到Light和Severe，不能把0.768直接标成只到Light的Near场景。

混合直连1.536TB/s下，guard_only交付约82.3%，滞回77.3%，反馈76.4%；更积极控制的HBF峰值低约2.36K，但完成工作更少。此结果不支持宣称反馈策略已经获益。1.920超载需求下累计交付下降且积压保留；后10s不能统一称纯冷却。

HBM在全部60点中保持Normal，GPU最高341.16465K低于该情景363.15K Light；因此这些点未检验HBM限制恢复或GPU先触限收益。无控制18点另表保留16个400K越域，并且全部首次Light/Severe来自HBF并列栈，不能给出compute-first普遍结论。

relay/DASH使用自身路径资源与伙伴检查，不回退direct。但本基线没有独立HBM前台/cache流量，不能从其近似结果推导共享链路永远无代价。8HBF只覆盖封装热域，外部GDDR服务/温度UNAVAILABLE。

速率工作负载没有因果token完成，所以token/s为UNAVAILABLE。另有四拓扑真实tiny-template结构派生DAG的ECC八点验证；两组证据分别报告，不能用DAG结果回填速率基线。原生MQSim语义对照不等于实际TB/s硬件吞吐，P2原失败和未冻结状态保留。

可复现派生来源：`BASE-ANALYSIS01/SYSTEM_THERMAL_CAMPAIGN_ANALYSIS.json`；原始启动索引和逐点收据在同阶段目录；现有16幅时间序列图与逐栈温度图不重复求解。维护主19点已完成并通过审计；该场景共享与理想独立维护资源的有效交付差为零，未识别出吞吐惩罚。ECC与refresh数据身份尚未联合接通。
