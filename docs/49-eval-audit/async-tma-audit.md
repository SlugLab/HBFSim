# Async / TMA audit — do not merge the candidate head

> Historical frozen audit below. Current source and capability corrections are in [current-state-20260908.md](current-state-20260908.md) and [the canonical plan](../49-new-evaluation-plan.md). Current external versions and specification boundaries: [source research](review-evidence/20260908/source-research.md). Original historical findings retain their source SHA and are not silently renewed.

审计对象 **S = feature/sm120-exact-stage1@f4dc28b2671c01939d98e4a968e6fb37b2e364d9**，对照 B = eval_base@fc829992ecdc3ca68881656722b67a31067c5d33。本文 [Sxx/Bxx](source-ledger.md) 给出逐条文件、函数和行段。结论：**已有可复用机制，但当前不可合并，也不满足完整 EQ2。**

## 1. PTX semantic boundary

采用 [NVIDIA PTX ISA](https://docs.nvidia.com/cuda/parallel-thread-execution/index.html#data-movement-and-conversion-instructions-cp-async-bulk-wait-group) 作为语义基准（本轮官方页面显示 ISA 9.3；实际 artifact 必须另记其 `.version`、target、CUDA/ptxas 版本，不能用新 ISA 替代旧 target 检查）。bulk commit 是每 executing thread 的动态 group；wait N 保留至多最新 N 组；`.read` 只保证 descriptor/source 已读，不保证 destination 写入可见。TensorMap copy 的实际操作是 `tensormap.cp_fenceproxy`，acquire 是 `fence.proxy.tensormap::generic.acquire`，不是假想的独立 `tensormap.copy` / `tensormap.acquire` opcode。[官方 descriptor copy / fence 说明](https://docs.nvidia.com/cuda/parallel-thread-execution/index.html#parallel-synchronization-and-communication-instructions-tensormap-cp-fenceproxy)。

普通 load 的 oracle 是**所选无数据竞争程序的附加可见延迟**，不是完整 NVIDIA scoreboard 模拟。要求真实 operand dependence、first use、load-before-consumer SASS 顺序；不能只凭 PTX 文本断言 ptxas 不会改变调度。

## 2. Symbols, placement and completion

| Symbol / feature | Source | What exists | Remaining gate |
|---|---|---|---|
| `__hbfsim_future_issue` | S10,S11 | fast 计算 ready；reference reserve ring 后返回；timing native op 在 issue，capacity materialization 延后 [S02] | queue admission 可阻塞；不能保证 issue 恒为零开销；D 的起点与 host delay 分开记录 |
| `__hbfsim_future_poll` | S11 | timing 比较 ready；reference 查 completion/timestamp，不消费 slot | 返回 ready-like 状态包括错误路径，只有随后 wait/status 才能裁决成功；daemon/shutdown 测试 |
| `__hbfsim_future_wait` | S15 | target 等待、reference completion、capacity resolved address、wait counters | producer/consumer CFG 正确性；timeout/coverage ledger；不把 wait counter 当 native hardware latency |
| `__hbfsim_tma_issue` | S13 | native timing path 与 deferred/software capacity path；tile/range 拆分 | page fanout、source snapshot、cross-range/OOB、native 与 modeled completion 双条件 |
| `__hbfsim_tma_barrier_poll` | S09,S14 | 查 domain/barrier 下 state；转换器把 native predicate 与 modeled readiness 合取 | phase reuse、多 issuer、predicated poll 的控制依赖；软件 token 无 state 的情况必须完整审计 |
| `__hbfsim_tma_barrier_wait` | S14 | 轮询 barrier helper | loop 本身无统一 deadline；需 watchdog/daemon death 明确终止证明 |
| `__hbfsim_tma_wait_group` | S09,S14 | 等单个 token；`.read` 对部分 direction 提前返回 | source reuse 必须对应 snapshot/read completion；动态 group 由上层正确选 token |
| `__hbfsim_tma_commit_group` | S14 | **空函数** | 不能仅凭空函数认定所有直线程序错误：S09 静态组表承担 bookkeeping；该设计不能直接推广到 loops/branches |
| replace / copy / acquire | S07,S12 | injected begin/commit、registry hash、generation、publication/acquire 检查 | 动态地址 A→B checksum，copy updated descriptor 后 acquire；破坏 generation 必须拒绝 |
| ordinary `cp.async` + wait_group | S06,S07,S19 | parser 只识别 tensor bulk + bulk groups，非 tensor cp.async 未获得 modeled completion | 本轮 conditional；不能画实测支持点；必须恢复 unsupported 计数或专门实现 |
| Future/TMA ABI | S16，两个 helper header L15 | future 64 bytes；base control ABI 4，S ABI 9 | host/device/embed-PTX/loader/reporting 同版本原子更新，错误版本负测 |

## 3. Confirmed counterexample: predicated first consumer

**STATIC_REPRODUCED，本轮 CPU 编译并运行 S 原始转换源码。** `transfer_block` 在识别 uses 后无条件删除 pending future [S01]；`emit_wait` 却在 consumer predicate 为假时跳过 wait [S02]。输入形状：

```ptx
ld.global.u32 %r1, [%rd1];
setp.eq.u32 %p, %r2, 0;
@%p add.u32 %r3, %r1, 1;
add.u32 %r4, %r1, 2;
```

转换成功且没有 rejection，但只在第一条 add 前有带 `%p` 的 wait；后一个无条件 add 前无 wait，也无 ret drain。取 `%p=false`，timing 路径允许第二个 add 提前消费；capacity 路径还可能消费尚未 materialize 的 register。后果属于源码推断，**未声称在 GPU 上观测到了 stale data**。

复现入口：[probe source](evidence/future-predicate-probe.cpp)、[转换输出](evidence/predicated-consumer-output.ptx)、[reproduction script](../../scripts/eval/audit_async_probe.py)。脚本冻结 S SHA，用 Git blob 编译到临时目录；不会修改 runtime。

最小修正方案（尚未实施）：predicated consumer/ordering drain 不能在所有可达路径删除 pending state；保守做法是在后续可能 consume/drain 处保留 guarded wait，已消费状态使重复等待无害；或遇到无法证明的条件消费直接拒绝。补 false→unconditional、true→unconditional、predicated ret、diamond join、loop reissue、predicate 使用 load 结果等反例。

## 4. CFG / instrumentation review

`ptx_analysis` 的普通 future 有 CFG、reaching definitions 和 reissue drain [S01,S03]，但上面的 counterexample 说明 may-consume 和 must-consume 没区分。IR defs/uses 对部分 opcode 显式解码，其余 operands 保守当 uses [S05]，可能提早 drain / 增加开销；必须不把这种保守处理称“exact scoreboard”。IR 的 `without_comment` 只去 `//` [S04]，base 的 block-comment / .loc 回归不能丢。

TMA 的 `analyze_async_objects` 与 transform 用线性遍历中的 descriptor generations、barrier pending 和 committed lists [S08,S09]；并没有在 CFG 各路径传播这些状态。直线单 issue/commit/wait 可以复用，但 loop 中同一静态 op 多次 issue、conditional commit、N>0 保留最新动态组均需负测或 fail-closed 拒绝，不能用静态 token 个数代替动态 group conservation。

`__hbfsim_tma_commit_group` 空体并非独立的充分 bug 证据；真正的合并门槛是证明上述 control-flow 子集和计时/transaction 一致。barrier helper 插在 native poll 后，不自动证明 warp 内 predicate/control guard 一致；错误处理不能把 poll true 当成功完成。

## 5. Coverage and fail-closed review

S 的 unsupported regex [S19] 缺 B 的 cp.async-global/tensor coverage 增强 [B01]；S parser 没有普通 cp.async future [S06,S07]。这是明确的源码差异与覆盖漏洞风险，尚未经过完整 S launch-gate live 路径复现。合并前必须把 B 的 comment/.loc/multiline/async coverage tests 纳入 S，同时对直接 cp.async、bulk non-tensor、unknown TensorMap、版本不支持、wrong ABI、预算耗尽全部验证拒绝。

禁止“unsupported=0 就是 100% coverage”。同时报告 eligible dynamic bytes、instrumented bytes、native bytes、bypass/opaque bytes、unknown bytes。unknown 不设零；fraction 的分母未核实就不生成正式全覆盖点。

## 6. Existing tests versus new requirements

S GPU source 有 ordinary load/store/atomic/branch workload；TMA tests 包含 host/device replace、descriptor copy、phase/source reuse [S17,S18]。这是**测试存在**的证据。本轮没有跑 S GPU 测试。existing future test 通过时间先后和 tail 迁移判断 overlap，部分比较还把 sync overlap 设置为 0；不等价于扫描 D 与 W 后匹配 `max(0,D-W)`。

EQ2 必须新增 5 D × 7 W/D × 4 ops（cp.async 条件项）× native/old/new、每格10次；额外 analytical oracle 非执行臂。完整校验 checksum、stale shared reads、early barrier、missing transaction、timeout/deadlock、issued/drained/coverage、descriptor A→B。覆盖率与失败点照报，不能删失败点后 claim 正确。

## 7. PTX → SASS artifact contract

| Source op | PTX op | Injected helper | PTX issue/wait/consumer | SASS issue/wait/consumer |
|---|---|---|---|---|
| dependent load | ld.global | resolver (B) | 自动导出 op IDs 和行号 | **NOT MEASURED / not mapped this round** |
| future load | ld.global | future_issue / future_wait | producer → ALU → first use | **NOT VERIFIED** |
| TMA G→S | cp.async.bulk.tensor…mbarrier | tma_issue / barrier_poll | native poll 与 model predicate 合取 | **NOT VERIFIED** |
| TMA S→G | …bulk_group | tma_issue / wait_group | commit/wait N 与 source reuse | **NOT VERIFIED** |
| descriptor update | tensormap.replace / cp_fenceproxy / fence.proxy | begin/commit/acquire | A→B generation | **NOT VERIFIED** |

实现阶段导出 `mapping.csv`：branch/git_sha/kernel/source_file/source_line/ptx_sha/ptx_op/helper/op_id/sass_pc/sass_opcode/wait_pc/consumer_pc/ptxas_version/flags/cubin_sha。用 `/usr/local/cuda-13.0/bin/cuobjdump --dump-sass --dump-lineinfo`、`nvdisasm -g` 检查最终 cubin；保留优化与 lineinfo，不全局 -O0。唯有实际证明重排破坏关系才加入最小局部 barrier。

## 8. Host MQSim without a dedicated SM

优先 `GPU issue → bounded async ring/future → host MQSim → completion record → GPU residual wait` [S10,S11,S15]。GPU/host 不能直接相减异时钟 timestamp；模型返回 duration / issue-relative target，GPU 用同一 globaltimer 算 residual。host publication 迟于模型 deadline 时必须单列 service/publication overhead，不能把晚到的 completion 强行提前。容量还要等数据 copy-ready；真实 ready=max(model ready,native ready,copy ready)。测 host core utilization、queue-full issue blocking、completion lag、occupancy/register inflation。dedicated SM 会改变 SM 数量和调度，未证明必要前不采用。
