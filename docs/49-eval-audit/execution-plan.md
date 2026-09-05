# Incremental execution plan

**状态：第一阶段交付，下面的runtime/实验步骤均待人工审阅后执行。** 不把未实现的命令写成现成runner。方法以本轮四EQ为合同；旧doc47/48/19只作为已审计来源。[run-matrix.csv](run-matrix.csv) 是明确展开的条件矩阵，一行一个condition，`repeats`给重复数；不是实验结果。成本为PROJECTED_PLANNING_ESTIMATE。

## Phase 1 completed artifacts

- 审计base与所有指定候选分支、记录source ledger和文档index；复现predicated first-consumer漏wait。
- 统一long-form CSV/JSON schema、validate/render、独立mock generator和matrix generator。
- 六组PNG/PDF/SVG预览；results/measured和figures/final保留为空结果目录。
- 重新运行已有CPU optional-tools CTest；验证mock拒绝与schema负测；见evidence。

## Minimal dependency closure, in order

### A. Freeze and test-only GPU delay harness (G0–G2)

Files to create after approval：`benchmarks/cuda/hbf_dependent_delay.cu`、`scripts/eval/run_gpu_delay.py`；modify `benchmarks/cuda/CMakeLists.txt` 和仅显式benchmark开关控制的device helper delay path。先实现K-hop dependent read/native checksum/timestamp export，再以D=0/0.5us验证instrumented matched baseline；最后扫完整7D×5warps×2occupancy×3logical treatments。覆盖和unknown字节计数走固定schema，原runtime defaults保留。失败时只能报告noise floor/functional coverage，不能放宽delay校验凑通过。

### B. SSD acquisition and pair exporter (G3–G4)

Create `scripts/eval/freeze_storage_split.py`、`collect_storage.py`、`replay_arrivals.py`、`export_results.py`。先冻结设备身份/专用文件/缓存控制和split.json；collector输出requested/actual arrival traces；adapter把同arrival records送MQSim。现有 `hbf_mqsim_bench --bytes --pattern --arrival-gap-ns` 可跑合成steady-state，但不是通用fio replay。闭环QD和fixed-arrival分开实现/验收。三臂复用相同read-only file content与capacity entry；epsilon不能达到noise gate时才加测试专用零delay，禁止双计SSD model delay。

### C. Isolate and repair future/TMA (G5)

不直接cherry-pick整条S分支。先将B classifier/comment/.loc与MQSim QD regressions保留在候选integration；从S取IR/analysis/future/TMA/descriptor/device helpers及其完整host/control/embed ABI依赖。**不能只挑三个helper函数**：64-byte future、control ABI9、context/loader/pointer registry/embedded PTX必须同版本。

第一项先加predicated consumer反例（本轮probe可复用），修正may/must consumption。随后loop/diamond/predicated drain和bulk group动态状态：最小可投稿子集限制为straight-line单group+完整wait，无法证明的CFG明确拒绝。修复ordinary cp.async unsupported detection；仅实现支持时才启用矩阵conditional cp.async点。

Create `scripts/eval/run_async_overlap.py` 与 `audit_sass_mapping.py`，extend `tests/gpu/sm120_future_correctness.cu` / `sm120_tma_correctness.cu` 的D/W/iteration选项。新增动态A→B descriptor checksum、source reuse、barrier phase、N=1/0多group、daemon loss、timeout。编译优化开启；最终cubin的op/helper/wait/consumer mapping齐全才跑性能。不得修完CPU反例后提前宣称GPU semantics正确。

### D. Model inventory and rho accounting (G7–G9)

Create `scripts/eval/inventory_checkpoint.py` 与 `budget_fast_tier.py`；复用X的 `scripts/generate_pretrace_artifacts.py`、`adapters/vllm_capacity/trace_collector.py`/`routed_capture_compat.py`（实际依赖以导入图和tests审计为准），保留B的tensor身份验证。输出E/k/layers、expert/attention/shared bytes、KV形状与实际allocator/workspace峰值。拿不到真实checkpoint就阻塞采集，不能从mock填默认值。

Extend `adapters/vllm_capacity/trace_replay.py:capacity_geometry` 增加显式budget/achieved-rho输入，保持旧ratio模式可复现但不能混作新rho。Capture per-step active sequences/routing；如果只有单序列trace，写trace-composed，独立保存组合seed及成员，不假装在线server。

### E. Concurrent service and decode projection (G6)

Create实验端 `benchmarks/replay/hbf_concurrent_trace_timing.cpp` 或在现有tool加独立选项，默认串行不变。输入issue_ns/consume_deadline、layer/step/sequence/order、shared resource需求；可保留多个outstanding，next completion只推进模拟时钟，不阻塞所有未来发起。output每请求queue/service/completion、QD distribution、byte conservation。

Compute-only/native step trace是独立实测输入；以真实依赖DAG合并compute与memory，不能直接把所有miss latency加到decode。G5未过时只能提交标PROJECTED的保守串行上界，**不能称完整EQ3相图已完成**。N整数几何不能映射的候选保持解析projection，明确source_function，不能称MQSim模拟点。预算表按60 CPU秒/rep估算；pilot决定是否做分层trace压缩，不能丢掉决定queue的arrival关系。

### F. Prefetch boundary and dense comparisons (G8–G10)

先实现实验端one-layer-ahead queue model，预测只使用消费前可用信息，拥有demand/prefetch ID、promotion/late/eviction accounting；cost与bytes进入同一media队列。运行完整EQ4矩阵的事先声明slice，而不是把所有EQ3轴再做全笛卡尔积。

真实runtime producer最后做：`capacity_runtime`/`capacity_worker`/`request_dispatcher`/`capacity_page_service`接submit→complete→ready→consume，ticket wrap、queue-full、copy failure、clean/dirty victim rollback负测先行。X的staging路径依赖context/loader/API，必须逐项验证whole model byte coverage，不能借用一次first-fused尝试作为整模型proof。若runtime未完成，prefetch结论只叫offline PROJECTED。

### G. Export, stats, final release

所有collector经 `export_results.py` 产生本轮CSV/JSON契约及raw/manifest；heldout冻结后不可重调。独立run/block bootstrap，读claim gates决定措辞。`validate_results.py --strict-no-mock`与同一renderer通过后，还需要逐图人工核对provenance和source-artifact chain。没有数据的实验记blocked，不生成占位“真实”结果。

## Matrix and budget

| Group | Conditions × repeats | GPU-h estimate | CPU core-h estimate | Main dependency |
|---|---:|---:|---:|---|
| EQ1 known delay | 210×10 | 1.17 | 1.17 | GPU driver、benchmark、D=0控制 |
| EQ1 flash fidelity | 512×10 | 0 | 85.33 | 实盘/arrival/split/profile |
| EQ1 three arms | 96×10 | 1.33 | 1.33 | GPU、backing/cache control |
| EQ2 async sweep | 420×10 | 3.50 | 3.50 | 修复S；含条件cp.async |
| EQ2 correctness extras | 11×10 | .06 | .06 | CFG、descriptor、故障测试 |
| EQ3 boundary | 216×5 | 0 | 18.00 | rho + concurrent trace/compute model |
| EQ4 routing capture | 24×5 | 12.00 | 12.00 | 真实checkpoint、capture |
| EQ4 workload boundary | 1296×5 | 0 | 108.00 | 3 models×8B×3policy×3rho×2tR×3N |
| EQ4 live confirmations | 36×5 | 9.00 | 9.00 | 完整runtime prefetch/coverage |
| Robustness | 18×5 | 0 | 1.50 | service assumption±20% |
| Mode cost appendix | 9×5 | .75 | .75 | matched input、time_scale=1 |

总计 **2848 conditions / 20485 planned runs，27.81 GPU-h / 240.64 CPU core-h**。其中SSD采集也消耗设备wall time；CPU core-h是保守“一core计费”的预算，不是本轮测得CPU利用率，也不等于elapsed时间。microbench用2–5 s/run，storage/replay60 s/run，routing capture360 s/run，live confirmation180 s/run，分别包括计划warmup/collector开销。参考路径可能远慢于此；pilot先跑3 cell实测，再把估算更新为实际吞吐。

最低配置按matrix `minimum_configuration=yes`：**1477 conditions / 13105 runs，10.44 GPU-h / 122.02 CPU core-h**；不含conditional cp.async和live_confirmation，routing B取1/8/32，EQ4性能只取tR=4、N=1536的slice，其余EQ3全轴保留。建议预留2倍运行缓冲：最低约21 GPU-h/244 CPU core-h，理想约56 GPU-h/482 CPU core-h。**不含修bug、源码整合、编译、模型传输、人工盘点的人力。** 这些数字不是已发生资源使用。

## Minimal publishable versus ideal

**Minimal publishable configuration**：1台可用GDDR7 GPU + 1块身份固定的CD8P（CXL不可用时明确替代）；完整EQ1 known delay与SSD heldout/三臂；EQ2通过受限但明确的ordinary future/TMA G→S与S→G子集、动态descriptor、final SASS mapping；不支持cp.async则保留拒绝负测并限定claim。EQ3全rho/tR/N预测，无法映射N时使用标明解析的点；Qwen真实trace + 至少一个明确匹配规则的dense对照，最低矩阵当前为两种dense预留预算。EQ4可以trace-composed与offline-prefetch projection，只能写相应范围，不得live serving/runtime speedup。此配置仍以G2–G10对应门槛为前提，不是保证可投稿或已完成。

**Ideal configuration**：加真实CXL SSD第二设备；完整多group/loop/predication cp.async/TMA semantic覆盖；真实在线active-sequence调度trace、两类dense匹配、实际runtime prefetch、36格live confirmation；模型参数confidence/外推敏感性并列。第二SSD的预算需再加EQ1 flash/三臂的每设备部分，不包含于当前single-device“ideal matrix”总额；热闭环仍非必须主EQ。

## Today runnable and blockers

今天可运行：已有CPU42项、offline prefetch model、已验证inventory格式上的serial replay、MQSim synthetic arrivals、本轮schema/mocks/probe。今天不可运行GPU live：driver不可通信。Storage只读身份可盘点，但正式性能还缺专用volume与arrival/split contract。缺production的关键项：async conditional-consume修正及完整整合、dynamic coverage/budget、concurrent timing、真正prefetch producer。已有分支可复用 ≠ 可以合并；当前S合并结论为NO-GO。

**到这里停止，等待人工审阅。** 本阶段没有创建实验daemon、改driver、读写SSD payload、下载模型或提交新的runtime实现。
