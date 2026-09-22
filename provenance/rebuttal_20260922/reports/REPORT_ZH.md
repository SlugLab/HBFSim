# HBFSim rebuttal 实验数据复核报告

**复核快照：2026-09-22 08:21:36 UTC**  
**复核方式：**只读重查 giga 上冻结的 `result.json`、正式 validation、覆盖分类和执行收据；未启动 GPU，未修改 raw。数值索引见同目录 `VERIFICATION_INDEX.json`。

## 一、结论摘要

目前最强、可以直接用于 rebuttal 的证据有三组：

1. **R1 访问级闭合成立。** 最终验证为 `PASS`，227/227 项检查通过、0 error。D0 与 nominal 两个插桩臂在正式请求 epoch 内均满足 `N=M=K=6,144`、对应 98,304 B，且输出 token 与 native 完全一致。
2. **R2 六点共同配置范围扩展成立。** 在统一 60 s request timeout 下，16 KiB、1 MiB、16 MiB、64 MiB、256 MiB、512 MiB 六个注册前缀均通过各自 12/12 项验证；每点均为 `N=M=K`、错误计数为 0、输出 token 与 native 一致。随着注册前缀扩大，正式 epoch 内命中注册范围的访问数由 6,144 增至 93,847,552。
3. **全模型参数注册扩展成功，但动态插桩覆盖仍有限。** 147 个去重后的 CUDA parameter storages、共 13,838,323,712 B 均完整注册；派生验证为 `PASS_WITH_SCOPED_BUFFER_REVIEW`，26/26 项通过。正式 epoch 内 `N=M=K=2,178,416,640`，对应 34,854,666,240 B，输出 token 仍与 native 一致。但覆盖流只给 32 个 parameter storages 提供了可归属的 modeled 下界，115 个是 `NO_RECORDED_MATCH`；因此不能写成“全部权重访问都已插桩模拟”。

R3 仍没有成功的页面大小采样结果：已有三次激活尝试均非超时退出且保留失败；最新一次完成 context/probe binding 并正确验证前 9/65 个 cold regions，在第 10 个 region 因 `uninstrumented_pointer_parameter` 终止。35-record 矩阵尚未开始。R4 未执行。

## 二、实验身份与口径

- 模型：OLMoE-1B-7B-0924，冻结 revision `6d84…`。
- GPU：真实 RTX 5090。
- 请求：batch 1、input 128、output 16、seed 0、eager；FlashInfer attention、Triton MoE。
- MoE tuning 使用 `num_stages=1`，其目的是约束到当前支持的 PTX 形式；其余 tile 参数保持。这里不是历史 Qwen 条件的原样复现。
- 所有已验证臂输出 token SHA256 均为 `1aa842abd697c23f4c1336783816b1fe655820f4694fccde4f1773c59732af19`。
- 下文的 `N/M/K` 分别是正式 epoch 内的 **in-range supported accesses / modeled-admitted accesses / service-completed accesses**。它们不是全程序所有 supported accesses。`service_requests` 是按 warp/page 分组后的服务操作数，也不要求等于 N。

## 三、R1：访问级证据

正式验证文件 SHA256 为 `86dc76965ab5fe26b750d1421f32993789b51a927c74bd98fff234e4f1ad377d`，实际包含 227 个布尔检查，全部为真；状态 `PASS`，`errors=[]`。

| 臂 | 生成时间 / s | N=M=K | in-range bytes | grouped service requests | 输出 |
|---|---:|---:|---:|---:|---|
| Native | 0.131551 | 不启用 accounting | — | — | 与两插桩臂一致 |
| D0 matched control | 417.143241 | 6,144 | 98,304 | 6,134 | 一致 |
| Nominal timing | 430.258095 | 6,144 | 98,304 | 6,138 | 一致 |

D0 同时记录了 6,134 个 covered eval 操作，和 grouped service requests 一致，`trace_overflow=0`、`rejected=0`。`trace_drop` 字段不可用，必须保留为 UNKNOWN，而不能补成 0。

R1 能支持的表述是：在相同模型、输入、输出和后端条件下，两个插桩臂对注册范围内的访问实现了访问数与字节数闭合，并保持输出不变。它不能支持“全程序所有权重访问均被模拟”：whole-process coverage 中每个插桩臂只有 72 条 modeled fused 记录，另有 1,423 条 opaque/unknown 记录；coverage 文件没有正式请求 epoch/timestamp，不能把 72 当作正式请求的 launch 总数或分母。

## 四、R2：统一 60 s 配置下的六点范围扩展

六点均使用相同模型、prompt IDs、input/output、seed、warmup、后端、profile、60 s request timeout，以及相同的 core/helper/pass/gate/extension/daemon/agent 组件。每点 validation 都有 12 个检查且全部通过。

| 单个目标 storage 注册前缀 | N=M=K | in-range bytes | grouped service requests | 生成时间 / s |
|---:|---:|---:|---:|---:|
| 16 KiB | 6,144 | 98,304 | 6,135 | 412.606082 |
| 1 MiB | 393,216 | 6,291,456 | 392,007 | 410.866143 |
| 16 MiB | 4,194,304 | 67,108,864 | 4,179,165 | 398.396892 |
| 64 MiB | 14,680,064 | 234,881,024 | 14,632,023 | 406.353820 |
| 256 MiB | 45,088,768 | 721,420,288 | 44,952,928 | 419.766818 |
| 512 MiB | 93,847,552 | 1,501,560,832 | 93,459,086 | 530.649687 |

六点每次正式请求中，四个已插桩模块的 supported accesses 都是 110,473,216、supported bytes 都是 1,695,711,232 B；这不是全程序所有 kernel 的访问数。表中 N/B 只统计与注册范围相交的访问，其中 in-range bytes 是重复访问累计字节，不是唯一权重足迹。每点均满足 count closure、byte closure、同步完成、模块/manifest 身份匹配、目标 fused kernel 无 opaque、错误计数为零、token 与 native 一致。

这组数据表明，随着同一目标 storage 的注册前缀从 16 KiB 增大至 512 MiB，可闭合建模的 in-range 访问量显著扩大。生成时间并未随注册字节单调线性变化，因此不能从六点外推带宽、硬件加速或任意更大范围的耗时。

## 五、全模型参数注册扩展

全模型运行使用独立的 300 s request timeout，因此应作为 R2 的扩展面板，不能作为共同 60 s 曲线的“第七点”。

- 完整注册：147 个去重后的 finalized CUDA parameter storages，13,838,323,712 B。
- 正式 epoch：`N=M=K=2,178,416,640`，in-range bytes 为 34,854,666,240 B。
- grouped service requests：2,170,399,597。
- 生成时间：11,949.734653 s，即约 3 h 19 min 09.735 s。
- 外层运行从 03:55:55Z 到 07:26:09Z，总历时 12,614 s，即 3 h 30 min 14 s；它包含生成时间之外的初始化、验证与清理阶段。
- 四个 accounting modules 均为 COMPLETE，snapshot 前同步与 disable 后同步均完成；unsupported、failed、translation failure、counter overflow 均为 0。
- 输出 token 与冻结 native 相同。
- 最终派生验证：26/26，状态 `PASS_WITH_SCOPED_BUFFER_REVIEW`。原 wrapper 的 exit 2 是已知的 persistent-buffer review 状态，并未被改写为 exit 0。

“全注册”只证明所有实际 parameter storage 区间都被登记，不能证明所有算子都被转换。此次实际 staged 的四个 PTX identity 都是 `fused_moe` 变体。覆盖流的保守下界分类为：32 个 storage `OBSERVED_MODELED`，115 个 `NO_RECORDED_MATCH`。这 115 个对象是 parameter storages，不是 KV cache；`NO_RECORDED_MATCH` 仅表示 whole-process coverage 流中没有可归属的非零地址记录，不能据此断言权重没有被访问。由于 coverage 无 epoch/timestamp，也不能把这项分类升级成正式请求内的逐权重完整覆盖证明。

另有 64 个 float32 attention scale persistent buffers 被单独列为 scope review；它们不是 `named_parameters`，也没有被悄悄计入 147 个 BF16 parameter storages。当前结论严格限定为 learned parameter-storage 注册完整性。

## 六、R3 与 R4 当前状态

R3 有三次真实激活失败，elapsed 分别为 1.092976464 s、1.336957066 s 和 7.707386381 s，return code 均为 1，均不是 timeout。前两次暴露 context 配置问题；第三次 context 建立且 exact binding 成功，前 9 个 cold regions 的 payload 正确，第 10 个 region 的 64-bit offset 参数落在 VMM 范围，被 gate 识别为未插桩 pointer parameter 并拒绝。修复方案仍在准备，不能标记为成功；4/8/16/32/64 KiB 的 35-record 页面矩阵没有开始，因此本报告不给任何页面放大或 cold/warm 数字。

R4（独立 NVBit/SASS 地址审计）未执行；control heartbeat 或队列 observer 不属于 R4。

## 七、缓存语义限制

当前全模型/R1/R2 使用 `timing_backed + hybrid`。Hybrid 的 reference/fast 分流是采样与快速解析近似，不是 GDDR/HBM cache hit/miss。虽然 nominal profile 含 `hbm_cache_bytes=8 GiB`，该字段的实际生产消费者是独立 `capacity_unbacked` runtime；它没有在 timing-backed 权重路径中建立 tag、hit、fill、eviction 或 writeback 状态。R3 的软件 VMM frame cache 是另一条路径，也不能等同 GPU 原生 L2/GDDR cache。因此现有数据不能声称“命中走 cache latency、未命中走 HBF latency”。

## 八、可用于 rebuttal 的英文短段

> We added access-level validation on an OLMoE-1B-7B workload running on an RTX 5090. For the matched D0 and nominal timing arms, all 6,144 accesses intersecting the registered range were admitted and completed (`N=M=K`), covering 98,304 bytes with zero accounting errors and identical output tokens to native execution. Under one common 60-second request-timeout configuration, six registration sizes from 16 KiB to 512 MiB also passed count/byte closure and token-equivalence checks; the number of completed in-range accesses increased from 6,144 to 93,847,552. In a separate full-parameter extension, all 147 unique CUDA parameter storages (13.84 GB) were registered and 2,178,416,640 in-range accesses completed with unchanged output. This last result demonstrates registration and access-accounting closure, while dynamic coverage remains limited to the four staged fused-MoE PTX variants and therefore does not establish that every model-weight access was instrumented.

## 九、不能声称的内容

- 不能把 147 个 storage 全注册写成全部权重访问、全部算子或全部 kernel 已插桩。
- 不能把 `NO_RECORDED_MATCH=115` 写成“115 个权重未访问”，也不能写成 KV cache。
- 不能把 grouped `service_requests` 当作逻辑访问 N。
- 不能把 software generation time 当作硬件性能、带宽或 speedup。
- 不能把 hybrid fast/reference 分流写成 cache hit/miss，也不能把 R3 的 2 GiB 软件 frame pool 写成全模型权重 cache。
- R3 页面大小结论和 R4 独立审计仍不可用。
