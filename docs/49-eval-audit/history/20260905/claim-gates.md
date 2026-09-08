# Claim gates — proposed before data collection

本表为事前验收草案，随本计划人工审阅后冻结；数字不是事后拟合出来的门槛。source evidence与capability见 [ledger](source-ledger.md)，目前新性能数据均未测。

| Gate | Required evidence | Acceptance rule | Current status / failure wording |
|---|---|---|---|
| G0 snapshot | branch/SHA、dependency pins、raw hashes、profile/input/model identity | immutable manifest、dirty patch hash、time_scale=1；量纲核对 | base源码已冻结；新实验manifest待采 |
| G1 GPU / device | 实际GPU UUID/介质、SSD backing/BDF、driver、资源占用 | 正确设备、可用运行环境 | 本轮driver不可通信；GPU NOT VERIFIED |
| G2 known delay | chain stamps、native/zero-delay/control、checksum、Event delta | checksum100%；D>0每cell mean abs error≤max(0.10us,0.10D)，P95 abs error≤max(0.20us,0.20D)；D=0报告绝对噪声 | 缺新benchmark与精确D；不写GPU delay fidelity已通过 |
| G3 frozen storage | 新calibration/heldout、相同arrival/bytes、cache/preconditioning scope | heldout P50/throughput/IOPS median relative error≤10%、P95≤20%；P99单列P95≤25%；knee差≤1个QD档 | 未完成；六断点只叫 calibration consistency |
| G4 differential | 配对T0/THW/TSIM、覆盖相同、真实device backing | delta signal>3×baseline noise SD才计算relative error；median≤15%、P95≤25%；n≥10配置时Spearman≥.90 | 缺真实三臂；只报告组件验证，不能称应用级fidelity |
| G5 async semantics | D/W sweep、new/native/old、CFG negative cases、动态descriptor、SASS | 所有checksum/transaction通过；stale/early/missing/deadlock/timeout=0；residual error≤max(.10us,.10D)均值且P95≤max(.20us,.20D)；zero-overlap issue不得支付完整D | 条件消费反例已复现；S不可合并 |
| G6 service rate / concurrency | 同arrival trace、多outstanding、N映射、steady-state completion bytes/time | pending/issued/completed守恒；不偷换interface ceiling；N_requested/achieved显式；serial replay不得产并发性能结论 | 缺concurrent replay/compute timeline |
| G7 fast residency | config/tensor预算、KV/workspace峰值、rho requested/achieved | 无重复扣除；负预算报infeasible；eligible分母可追溯 | 缺动态预算闭合 |
| G8 model/dense matching | 真实checkpoint/config哈希、E/k/layers/bytes、matching report | 不能默认专家数；dense匹配差距≤10%目标，未达到明确偏差 | 当前只是旧外部inventory记录 |
| G9 routing/concurrency | per-step active IDs、真实routing、shuffled/null、trace provenance | actual scheduler trace才能写live concurrency；组合trace只写trace-composed | base缺capture，X可复用 |
| G10 prefetch | speculative submit/completion/consumption/eviction账本 | 总media bytes与queue负载守恒，same demand不双计；不偷看未来；统计timely/late/useless | base只有offline模型，runtime收益未证 |
| G11 final publication | no-MOCK renderer、raw manifest/hash、above gates | strict退出0只是格式门；claim还需对应G2–G10过关 | 当前只有MOCK，正式renderer必须失败 |

边界“显著移动”：在预先声明的5/10/20% slowdown阈值上，service或rho crossing 的paired-bootstrap CI不覆盖零移动，且超过网格分辨率；若只有parameter sensitivity而无replicates，只写条件预测变化。**统计CI不消除hypothetical HBF参数不确定性。** 没crossing时报告“扫描区间内未观察到”，不外推出精确边界。

覆盖门：eligible bytes 分母与未知路径都需记录；不许“unsupported=0”替代动态coverage。若未知/未建模字节>0，禁写完整应用路径fidelity，只允许被覆盖子集；用性能声称时提供coverage sensitivity，不能强制fail-open通过。旧E1.6强制放行不在本轮最小runtime变更内。

## Claims currently forbidden

- GDDR7 validates HBM；GDDR7 emulates physical HBF；CD8P/CXL SSD是HBF ground truth。
- six calibration points zero error ⇒ heldout validation；MQSim与fast相等 ⇒真实SSD已验证。
- `feature/sm120-exact-stage1` 已正确保留全部async/TMA语义、可直接合并；helpers存在就等于完成。
- ordinary cp.async已支持；dynamic TensorMap已通过本轮GPU验收；PTX位置即SASS位置。
- 串行页replay的sum stall是live decode time；configured max_batch_size是真实active sequences。
- E/k由模型名字硬编码；uniform null或synthetic MoE页流是真实Qwen路由。
- 单个hypothetical profile的一条曲线代表实际HBF市场器件；UCIe ceiling等于array sustainable bandwidth。
- 未计算KV/workspace/非offload权重就报告rho；110GiB稀疏地址跨度等于物理读取110GiB。
- offline预取模型带来的收益是当前runtime收益；漏计speculative traffic后仍声称prefetch移动了真实边界。
- GDDR7 memory temperature等于NAND junction；base已有thermal-aware request timing。
- 旧time_scale=100参考运行的减速就是time_scale=1的固有成本。
- 本轮MOCK图的趋势或置信区间是实测发现。
