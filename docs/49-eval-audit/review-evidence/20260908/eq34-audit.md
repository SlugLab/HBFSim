# EQ3/EQ4 当前实现与最小 CPU 验证

> 分享副本：审计事实、数值和原执行路径保持原记录；仅调整导航。大模型、完整 raw、部分本机清单与可执行二进制未随仓库发布，范围见 [审阅包说明](README.md)。

审计日期：2026-09-08 UTC。当前源码：`/root/hbfsim-exp/eval-base-integration`，HEAD `254d65a66279fbaffc5c185d04fbe41dc8dbba44`。本报告只读审计现有实现与冻结证据，并运行现有 CPU 单元测试；没有新捕获、GPU 操作、模型下载、模型权重载荷读取或正式实验。新增文件仅在本报告目录，完成后冻结供迁移最终同步。

**结论：当前代码支持 whole-expert 容量记账、真实捕获路由的离线统计和受限前缀预取投影。物理 HBM:HBF 比例扫描、独立 KV reservation/staging 分配、dense 对照及 live serving 性能没有通过本次审计成为可执行或已验证实验。** 不能用任意新 matrix 参数代替已有入口的实际限制。

## 1. 可直接链接的证据与格式边界

| 用途 | 精确路径 | 当前 SHA256 / 状态 |
|---|---|---|
| HF BF16 统一 evaluation inventory | `/root/hbfsim-exp/eval-base-integration/results/manifests/hf-qwen3-30b-a3b-evaluation-inventory-20260905.json` | `c1822bf88f432d3cfd44ef0994730803ab60a963e1d495c8b181054ac75422d6`；9,625,635 B；CHECKPOINT_METADATA |
| 上项绑定的冻结 HF 元数据 | `/root/hbfsim-exp/eval-base-integration/results/manifests/hf-qwen3-30b-a3b-metadata-20260905/` | receipt、COMPLETE、tensors、config 四项 hash 与 inventory 的 model_binding 全部匹配；只证明冻结文件绑定 |
| B16 路由组合索引 | `/root/hbfsim-exp/eval-base-integration/results/batches/20260907-routing-continuation/composition/B16-group0/members.json` | `45dc43f48a8e39c2ae279710201766ae5919d7c448bd8b6d6cbaaa2b273dd1b7`；16 个成员 |
| **B16 真正绑定的旧 HF BF16 inventory** | `/root/hbfsim-exp/eval-base-integration/results/batches/20260907-routing-continuation/composition/B16-group0/metrics/inventory.json` | **`ed64d4c0bd11ee75cb4cd1de4e82ed1b0071fbc4389b8b974e9275382a1e912a`**；15,545,674 B；旧 ModelFingerprint/expert_weights schema；这是该路由 CLI 可用的冻结输入 |
| **当前同名 GGUF/F16 inventory，不能替代上一行** | `/root/hbfsim-exp/eval-base-integration/results/manifests/qwen3-30b-a3b-inventory.json` | **`56ce87191bd02e8b21c08bef2dcf66421becfb761b3a961459e80fcf30a8a1a8`**；4,097,136 B；format=GGUF、checkpoint=`Qwen3-30B-A3B-f16.gguf` |
| 原始 16 成员捕获 | `/root/hbfsim-exp/eval-base-integration/results/gold/hf-routing-runner/real-sixteen-member-capture-attempt-001/arms/capture/member-0000-routing.jsonl` 至 `member-0015-routing.jsonl` | 16/16 逐文件 hash 与 B16 索引一致；member-0000=`0131160ef0988ec8cdb0eb5b9569564bc83329933a28e08f3881d6228fb67cdc` |
| 路由派生结果及其 manifest | `/root/hbfsim-exp/eval-base-integration/results/batches/20260907-routing-continuation/composition/B16-group0/metrics/` | real=`d7bc3b7ba29230aea9d9e5c16d19003f61a253dd98cb4500b2af5071d8ab060a`；shuffled=`0df1324406980a8588ada54db87c800f03631c3f27ff70248a6630c53b905c33`；null=`23222fdb1482d64f3d55f8b6da98dead7e4cc3f8ce468493cb0aab7e7fd7821f`；3/3 与 manifest 一致 |
| 历史 HF 容量记账 | `/root/hbfsim-exp/eval-base-integration/results/gold/hf-inventory-adapter/real-frozen-accounting/summary.json` | CPU_ACCOUNTING_CONTROL_ONLY、PROJECTED、hardware_validated=false；不能作为 GPU 实际驻留 |
| HF012 受限时钟投影输入索引 | `/root/hbfsim-exp/eval-base-integration/results/gold/hf-routing-runner/route-horizon-hf012-inputs-attempt-001/route-horizon.json` | 已定位并读取索引；kind=HF_ROUTE_HORIZON_INPUTS_V1、member=hf012-request0；本轮未重跑其原生 service 或重认证捕获 |

HF BF16 inventory：L=48、E=128、k=8；eligible expert bytes=57,982,058,496；resident non-offloaded bytes=3,082,186,752；tensor bytes=61,064,245,248；page=16,384 B。当前 GGUF/F16 的 eligible bytes 恰好相等，但 resident bytes=3,107,774,464、tensor bytes=61,089,832,960，布局、dtype 与 tensor 身份不同；相同模型名和 eligible 总量不能建立格式等价关系。统一 HF inventory 的 model_binding.legacy_inventory_sha256 恰好指向上表冻结 BF16 旧 inventory，因此新记账与旧路由的联系应通过这个显式桥接字段和正确元数据观察身份表达，不能替换 index hash。

HF inventory 自报 `payload_identity_status=HISTORICAL_ONLY_NOT_CURRENTLY_AUTHENTICATED`、`checkpoint_origin_authenticated=false`、`hardware_validated=false`、`scientific_validation_passed=false`。本次核对冻结文件 SHA 不提升这些状态。16 成员捕获的 triplet-status 为 `PROVISIONAL_ROUTING_CAPTURE_RETURNED_UNVALIDATED`，worker 为 `ARM_RETURNED_UNVALIDATED`；批次 manifest 为 `BOUNDED_ROUTING_BATCH_COMPLETE_FORMAL_GATES_OPEN`、formal_done=0，历史 source_head=`aa5af1d125bbd5c758a0393a099e51e17e7fff51`。hash 一致也不等于独立 capture/GPU gold 已接受。

## 2. EQ3：预算公式、实际参数与未实现维度

源码：[budget_fast_tier.py](../../../../scripts/eval/budget_fast_tier.py#L20)、[evaluation_inventory.py](../../../../scripts/eval/evaluation_inventory.py#L157)。预算函数的独立输入只有 `fast_bytes, active_sequences, context_tokens, kv_element_bytes, workspace_bytes, safety_bytes`，另有不参与物理预算的 `legacy_ratio` 和 HF 身份参数。

```text
KV = active_sequences × context_tokens × layers × heads_kv
     × (key_length + value_length) × kv_element_bytes
C_fast_effective = fast_bytes − resident_non_offloaded_bytes − KV
                   − workspace_bytes − safety_bytes
rho_requested = rho = C_fast_effective / eligible_expert_bytes
rho_achieved = achieved_rho = budget_covered_bytes / eligible_expert_bytes
```

KV 按模型固定维度计算，为容量记账，未查询活跃 GPU allocator。有效容量先按 page 向下取整，再按 `(layer, expert)` 固定顺序贪心选择**整专家**，不部分缓存。`rho_requested` 可大于 1；`rho_achieved` 受整专家数量约束最多为 1。本次额外 TEST_ONLY 小夹具精确得到 effective=984 B、eligible=96 B、rho_requested=10.25、rho_achieved=1.0，见 `eq34-confirm-test-only-budget-oracle.json`，不作为模型实验数据。

主要输出原名：`C_fast_effective, page_aligned_effective_bytes, fast_pages, W_HBF_eligible, rho_requested, rho_achieved, budget_covered_bytes, budget_fully_covered_experts, selected_experts, packing_padding_bytes, unused_bytes, placement_status`。`rho_interpretation=CAPACITY_BUDGET_NOT_OBSERVED_RESIDENCY`，`cache_validation=NOT_EXECUTED`。不应把 requested rho 改名为 observed residency。

实际 CLI：

```text
scripts/eval/evaluation_inventory.py
  --metadata-refresh PATH --output PATH [--page-bytes INTEGER]
scripts/eval/budget_fast_tier.py INVENTORY
  --output PATH --fast-bytes INTEGER --active-sequences INTEGER
  --context-tokens INTEGER --kv-element-bytes INTEGER
  --workspace-bytes INTEGER --safety-bytes INTEGER
  [--legacy-ratio FLOAT] [--hf-metadata-refresh PATH]
```

| 用户要求维度 | 当前状态 / matrix 应如何标注 |
|---|---|
| r=C_HBF/C_HBM，固定总量 vs 固定 HBM | `BLOCKED_NOT_IMPLEMENTED`：预算入口没有 `C_HBF`/physical-r；只有 fast-tier 容量，不能将 rho 冒充物理 r |
| 独立 C_staging / beta | `BLOCKED_NOT_IMPLEMENTED`：没有 staging 字节参数及独立 staging 池；不得把 workspace/safety 暗换名后声称真实 staging |
| 独立 KV reservation / kappa 隔离消融 | `BLOCKED_NOT_IMPLEMENTED`：没有 reservation 开关或 allocator 证据；改变 context/B 会改变模型 KV 记账，不是已实现的 RESERVATION_ABLATION |
| beta=0 无 staging 的容量实现 | `UNSUPPORTED_UNVALIDATED`：CPU 的零拷贝对象 bookkeeping 不能证明 GPU 合法 no-staging。需实际 tile/stage/pin 约束才可判 INFEASIBLE/feasible |
| 负可用容量、无整专家可放、context 超限 | 函数已有拒绝/`INFEASIBLE_NO_WHOLE_EXPERT_FITS`；不可偷偷缩小 KV 或部分专家 |
| CPU rho 容量可行性 | `IMPLEMENTED_ACCOUNTING_ONLY`；缓存驻留/命中与 GPU物理容量须另外验证 |

## 3. EQ4：路由对照与真实并发边界

源码：[routing_metrics.py](../../../../scripts/eval/routing_metrics.py#L44)。入口：`--inventory PATH --members PATH --seed INTEGER --out NEW_DIR`。成员索引显式绑定每个绝对 trace 路径、hash 与 inventory hash；decode 的层/步完整性、top-k 独特性和专家范围会验证。组成方式是按 token_step 对齐独立成员；结束成员消失，`active_sequences` 随节点计算，`max_batch_size` 不是它的替代品。

| 对照 | 当前实现与可用证据 |
|---|---|
| real | 使用已捕获 top-k 序列；本次核对 B16 冻结件可用。结果仍为 PROJECTED/TRACE_COMPOSED，无 live scheduling/serving 时间 |
| shuffled | 每 member、每 layer 独立打乱整个 top-k 集合的 step 顺序，保留集合内部与边际频率；seed 固定，含 source_token_step，不是重新均匀采样 |
| uniform-null | 只产出 `expected_union_fraction = 1-(1-k/E)^active_sequences`；假设独立均匀且每序列 k 个不同专家。**没有伪造 null 路由**；只可用于 union 零假设，不能直接回放流量/等待 |
| dense | 本次定向入口未发现 dense workload/controller 或 capacity-matched / active-compute-matched 协议；`BLOCKED_NOT_IMPLEMENTED_OR_NOT_LOCATED`，不得用不同 dtype/架构的旧曲线充当稀疏性控制 |

现有指标原名：每 step/layer 的 `active_sequences, unique_expert_union, union_fraction, expert_frequency, entropy_bits, gini, jaccard_previous_step`，按 layer 的 frequency/entropy/Gini，以及逐请求 `reuse.distance`（不同专家数量的重用距离）。这些统计不包含真实 `decode_time_ns`/wall-clock。

格式限制：`routing_metrics.load_inventory` 的 CHECKPOINT_METADATA 分支调用 GGUF validator；旧 schema 则走 `adapters/vllm_capacity/model_inventory.py`。它没有通用 `--hf-metadata-refresh` 参数，不能把新 HF evaluation inventory 直接替换成路由 CLI 输入。复用当前 B16 必须绑定表中冻结旧 BF16 inventory；HF 预算与前缀 controller 使用显式 HF adapter 桥接。

## 4. 预取三臂确实不同，但两个基线的区别是发射并发

源码：[prefetch_replay.py](../../../../scripts/eval/prefetch_replay.py#L81)、[run_prefetch.py](../../../../scripts/eval/run_prefetch.py#L103)。三者都使用 CPU whole-expert LRU 缓存、同样的容量与批内 pinning，名称不得被解释成不同 cache 策略。

| policy 原名 | 实际互斥语义 |
|---|---|
| `none` | 当前 demand 的每个 miss 发射后立刻等待，再发下一个；缓存命中可复用。准确图例宜写“串行 demand，关闭预测预取” |
| `on_demand` | 当前 layer/batch 的全部 miss 先发射，共享服务队列，再等待该批所有需求就绪；无未来预测。宜写“并发 demand，关闭预测预取” |
| `one_layer_ahead` | 在 on-demand 基础上，在当前批就绪后的窗口中预测下一层；只用已观察到的同目标层、当前成员历史，rule=`LATEST_OBSERVED_SAME_LAYER_CURRENT_MEMBERS`。无历史不预测，未知未来被修改不应改变此前预测 |

所以 `none` vs `on_demand` 测的是 demand 发射串行/并发；`one_layer_ahead` vs `on_demand` 才隔离预测预取效应。FIFO/LRU/CLOCK 是 replacement；当前实现只有 LRU，不应变成三个“预取算法”。

预留和寿命：所有未完成 fill 占 `used`/reserved 容量；当前批 demand 全体受保护，计算窗口结束前不作为牺牲者。投机预留不能回收未就绪请求/当前 pin；无空间记 `prefetch_skipped.reason=NO_UNPINNED_READY_SPACE`，失败投机预留不先驱逐有效数据。此处验证的是 Python 对象与 CPU 模型，不是 GPU 源 tile、destination tensor、TMA descriptor、真实 allocator 或媒体数据寿命。

每请求账本原名：`request_id, expert, origin, issue_ns, ready_ns, first_demand_ns, consume_ns, eviction_ns, prediction_basis, bytes, expert_payload_bytes, classification`。`bytes` 是整专家页对齐后的 transfer bytes；payload 单列。已消费预取：`ready_ns <= first_demand_ns` 为 `useful`，否则 `late`；正常完整 replay 的未消费预取为 `useless`。demand 自身分类是 `demand`，不能充当 useful prefetch。

控制器流量指标：`traffic_bytes, gross_extra_bytes, saved_bytes, traffic_delta_vs_on_demand_bytes, extra_bytes_vs_on_demand, unused_prefetch_bytes`。`gross_extra_bytes` 按 `(expert, request ordinal)` 对齐 on-demand 请求，并非因果归因；守恒为 `gross_extra_bytes - saved_bytes = traffic_delta_vs_on_demand_bytes`。`extra_bytes_vs_on_demand=max(0, delta)` 会丢失节省的符号，应同时保留 delta/saved/gross。其余原名：`demand_lookups, cache_hits, cache_misses, capacity_bytes, peak_reserved_bytes, final_reserved_bytes`，节点 `residual_ns`。这些并非独立 GPU bandwidth/queue 测量。

`MqsimService` 返回 `service_observations` 和 `topology`，检查请求三阶段与 issued/completed 字节守恒，含 `device_outstanding`/`queue_depth`。当前这些入口没有直接产出完整的 `service_gbs`、GPU utilization 或逐生成 token 的 HBF bytes/token；若后续从事件求导，必须记录精确服务区间和分母，不能从 N/tR 反算后称实测。本次单元测试使用 `TEST_ONLY_CONSTANT_DELAY`，未运行 MQSim service。

## 5. 实际运行入口和 HF 前缀限制

```text
scripts/eval/run_prefetch.py
  --inventory PATH --budget PATH --routes PATH --routing-manifest PATH
  --profile PATH --binary PATH --out NEW_DIR
  (--compute PATH | --route-horizon PATH)
  [--hf-metadata-refresh PATH]
  [--cache-sensitivity RHO_1_32_EXTRA_DIAGNOSTIC|ORIGINAL_RHO_MATRIX_PROJECTED_CONTROL]
  [--initial-residency budget|cold] [--parallel-units INTEGER] [--timeout SECONDS]
```

此入口自动顺序运行全部三 policy，没有 `--policy` 参数。输出与 native binary 被限制在 checkout 内。legacy `--compute` 路径要求 GGUF inventory，compute 记录必须为 `COMPUTE_ONLY_NO_MEDIA_STALL`；HF metadata 不得与该路径混用。HF `--route-horizon` 必须配 `--hf-metadata-refresh`，且与 `--compute` 排他。

[hf_route_horizon_inputs.py](../../../../scripts/eval/hf_route_horizon_inputs.py#L266) 当前对真实 HF 路径写死：E=128、k=8、L=48；一个成员、B=1；预算 context=64、KV 元素2 B、workspace=safety=0；必须 `--initial-residency cold`；固定捕获 prompt=32/output=8；得到 7×48=336 decode 节点、335 个观察间隔和1个缺失末端。rho 默认只接受1/16；显式诊断开关仅1/32；原矩阵开关仅{1/16,1/2,1}。**任意 context/B/r/kappa/beta 扫描不是当前这个 CLI 的可运行范围。** B16 统计不能插入这个单成员时钟路径。

时间语义为 `ROUTE_TO_ROUTE_DEVICE_ELAPSED_INCLUDING_CAPTURE_AND_SCHEDULING`；不能叫 compute-only gold。shuffled 使用同位置原始 gaps，标 `SAME_POSITION_BASE_GAPS_WITH_SHUFFLED_ROUTES`。这些控制只能声称在固定观察 gap 下增加模型 media wait 的反事实前缀，不能声称真实 workload 时间随着路由、缓存、thermal 改变后的 wall-clock。

前缀输出原名：`observed_route_span_ns, projected_terminal_visible_ns, prefix_residual_ns, drain_end_ns, prefix_ready_miss_bytes, terminal_unserved_demand_bytes, terminal_not_ready_bytes, pending_at_horizon`；不输出 `decode_complete_ns` 或 compute gold。末端需求只观察、不服务或消费；`terminal_unserved_demand_bytes` 包含末端全体需求，连 READY 的也在内，`terminal_not_ready_bytes` 才是未准备好的子集。drain 只完成已发请求，不能补成末端消费。

前缀未消费预取分为 `useless_prefix`（已驱逐）、`censored_terminal`（终端需求已观察但无消费）、`censored_horizon`（观测结束）。控制器分别产出 `prefix_timely_prefetch_bytes, prefix_late_consumed_bytes, prefix_evicted_unconsumed_bytes, terminal_censored_prefetch_bytes, horizon_censored_prefetch_bytes`。`timely_coverage` 分母为 on-demand `prefix_ready_miss_bytes`，零分母返回 null；不能用被删失末端声称 useless 或推算 TPOT。

另有 route-only 分析入口 [hf_route_prefetch_opportunity.py](../../../../scripts/eval/hf_route_prefetch_opportunity.py#L548)：`--metadata-refresh --inventory --budget --consistency --routing-manifest --real --shuffled --output --expected-head`，绑定 HF010 特定冻结件。它只测候选可知性/后来需求重合，没有 service clock、预取执行或 GPU；不能把 opportunity 命中改称 timely/useful 传输。

已有捕获工具 [hf_routing_runner.py](../../../../scripts/eval/hf_routing_runner.py#L812) 参数为 `--metadata-bundle --fresh-out --selected-uuid`，可选 `--capture-cuda-route-events`、`--routing-capture-cell routing_capture-01469`；本轮仅定位，未调用。

## 6. 最小 CPU 验证及保留的负结果

最终确认原始日志：[eq34-confirm-cpu-tests.log](eq34-confirm-cpu-tests.log)；摘要：[eq34-confirm-cpu-summary.json](eq34-confirm-cpu-summary.json)；进程约束：[eq34-confirm-process-receipt.json](eq34-confirm-process-receipt.json)。可重现入口是本目录 `eq34-cpu-confirm.py`，它保留首次审计文件并调用 `eq34-cpu-audit.py` 的同一现有测试子集。

- **27/27 通过**：12 个 PrefetchGold（因果性、串行与并发、late 晋升不重复发射、pin/预留守恒、失败预留无副作用、缺失末端）；5 个 RoutingMetricsTests（union/null、频率/entropy/Gini/Jaccard、保留 top-k 边际的 shuffle、reuse distance、输入拒绝）；10 个 InventoryTests（真实小 GGUF 文件的元数据完整性/损坏拒绝、预算扣除、页填充、无整专家可放）。夹具明确 TEST_ONLY，不是模型运行结果。
- 1 个 Python 测试进程，固定单 CPU，wall 0.8282 s，峰值 RSS 85,291,008 B（81.34 MiB）；限制 wall/CPU 各120 s、地址空间1 GiB、每文件1 MiB。测试临时文件在 `eq34-confirm-tmp`，完成后删除。未跑创建子进程的 routing CLI 测试。
- 进程审计钩子拒绝 subprocess/fork/spawn/system；最终无创建尝试。RUSAGE_CHILDREN 启动时已继承0.039204 s，测试中增量约0（浮点舍入3.47e−18），不能把继承计数误判成本次辅助进程。
- [eq34-confirm-hashes-before.json](eq34-confirm-hashes-before.json) 与 [eq34-confirm-hashes-after.json](eq34-confirm-hashes-after.json)：36个输入/源码文件前后全相同；24个证据绑定检查全通过，包括16成员、3路由输出、4个HF元数据字段和正确冻结旧 inventory。
- 首次日志 `eq34-cpu-tests.log` 同样27/27通过，但审计摘要 `eq34-cpu-summary.json` 整体 binding=false、进程退出1，因为审计最初选了当前同名 GGUF inventory 去核对 BF16 路由索引。它被完整保留。随后只读找到 manifest 明示的冻结 `metrics/inventory.json`，未修改原始证据；确认运行选择正确文件后 binding 全过。因此这是**审计输入路径纠正**，不是原始路由损坏，也不是允许将 F16/BF16 混用。

## 7. canonical 状态 / matrix 可采用的边界

| 项目 | 当前可声明状态 | 尚缺的可审计结果 |
|---|---|---|
| HF inventory / whole-expert budget | `CHECKPOINT_METADATA` + `CPU_ACCOUNTING_CONTROL_ONLY` | 当次权重载荷身份、物理 GPU 驻留/cache/staging 实证 |
| real/shuffled/null 统计 | `PROJECTED`, `TRACE_COMPOSED`, 冻结 hash 核验通过 | 新捕获认证、真实 serving concurrency、时间指标 |
| 预取状态机与流量守恒 | `TEST_ONLY_CPU_CONTRACT_PASS` | 当前 bound binary/service 的实际 replay、独立GPU注入/时钟gold |
| HF012 前缀投影入口 | `IMPLEMENTED_RESTRICTED_PROJECTED_PREFIX` | 全 decode 消费末端、通用 B/context/rho 参数、live wall-clock |
| physical r / independent staging / reservation / dense timing | `BLOCKED_NOT_IMPLEMENTED_OR_UNVALIDATED` | 显式实现、合法容量域、dtype/计算或容量匹配协议及实证 |
| thermal × capacity/prefetch 的应用可持续性能 | `BLOCKED_DEPENDENCY_GATES_OPEN` | GPU时间语义、真实媒体事件/热参数及active feedback；不能由固定gap离线复合关闭门禁 |

这些状态可以由 canonical status/matrix 链接本报告与上述 raw log/hash 文件。不得把已有进程退出、metadata 一致、数值模型测试通过或 PROJECTED 路由控制升级为正式实验 DONE。
