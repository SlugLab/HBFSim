# Current capability audit

快照、函数与行号由 [source ledger](source-ledger.md) 定义。以下能力判断为 INFERRED；测试结果是执行记录，不是硬件性能测量。

## Execution and control flow

`vLLM loader → hbfsim_register_device / hbfsim_map_file → range table / launch coverage gate → transform_ptx → __hbfsim_resolve → timing fast path 或 host request ring → MQSim / capacity worker → completion → GPU load/store` [B02–B07,B09–B17]。

Timing registration 要求真实显存地址；数据继续驻留 GDDR7，超显存 logical range 只能用 capacity/VMM。每 context 只有一套 timing profile；native/fast-tier/HBF 三个 logical treatment 用同一 benchmark 分三次执行，仅切 range/profile，固定 kernel、数据、launch、seed，不以多 profile 大改架构作为前置条件。`__hbfsim_resolve` 明确把 capacity 送 reference；不能把 timing-only CD8P empirical curve 的标定自动转移到 capacity [B03–B06]。

## Audit table

| Area | Present / evidence | Missing for new EQs | Minimal incremental work |
|---|---|---|---|
| PTX ld/st | resolver 在真实 op 前；unsupported async global 被记录 [B01,B02] | issue/use overlap；动态字节覆盖；精确 known D | EQ1 独立小 benchmark；测试专用 delay override；保留 fail-closed |
| GPU helper | globaltimer、fast channel tail、host completion timeout [B03,B04] | benchmark 的 per-access timing 输出；warp/occupancy 分层 | exporter 记录 issue/wait/consume stamps；固定 D=0 对照 |
| Capacity | 文件 backing、有界 frame cache、CLOCK、page worker [B07–B09] | residency 预算不是 whole device 减 KV/workspace；无 producer prefetch | 分离预算工具与 runtime；预取只在后续 ticket/accounting 闭合后接入 |
| MQSim | media-only、bounded admission、profile geometry、带宽完成下界 [B10,B11] | 与 fio 的端到端 SSD 栈不同；host arrival trace bridge；completion 端 ceiling 不证明相应资源占用 | 先统一请求与到达，验证 QD 1–128 的 service curve；record active/admission QD |
| MQSim bench | size、seed、pattern、arrival-gap、P50/P99、modeled bandwidth [B12] | closed-loop fio QD 与 open-loop generator 不同；无通用 fio arrival importer | exporter/adapter 单独加入实验工具，不修改 MQSim 默认参数 |
| Capacity replay | complete expert placement；hit/miss、reuse、bytes；fast/hybrid/MQSim 可选 [B13–B16] | 逐页 submit 后立即等待，实际 outstanding=1；其 sum stall 不是 decode time | 并发 trace 时间轴与 compute DAG 的实验端耦合，或明确降为串行上界 |
| vLLM | `LLM.generate`、deterministic token、总 generation throughput [B17] | configured batch 不能代替每 step active sequences；opaque allowance 不能证明全覆盖 | capture adapter 只取 X 分支依赖闭合最小集合，加 byte coverage 和 scheduler stamps |
| Model inventory | config 派生 E/k/layers、tensor offset/hash、equal-sized expert contract [B14] | base 读的是 inventory，不自动扫描实际 checkpoint；shared expert、attention、KV/workspace 总预算缺失 | 真实 config/safetensors header inventory；禁止默认 E/k；找不到字段则报不支持 |
| Prefetch | standalone model，不链接进默认 runtime [B19] | speculative producer、completion deadline、late/useful/useless/eviction | 先离线 queued model，后 producer/ring/rollback；政策不单列 EQ |
| Thermal | base control/daemon 没有 thermal timing feedback [B18] | GPU/SSD telemetry 不能给 NAND junction；其他分支不是 base | characterization/appendix；不为四 EQ 引入热闭环 |

## Branch reuse and merge decision

| Branch | Frozen SHA | Reuse decision |
|---|---|---|
| eval_base | fc829992ecdc3ca68881656722b67a31067c5d33 | 工作基础；本轮新增评估文档与脚本 |
| docs/eval-mainline | 297db90e761784463bcbace4803667d5b9fe0028 | doc47/doc48 已选择性导入；新的 EQ 覆盖旧 E2，不复制继承的 runtime |
| exp/hbm-hbf-capacity-qwen3-30b-a3b | 37144843906b3bd71f3fbac1fecc6b5080d82b95 | inventory/replay 已有；TraceCollector、capture compatibility、staging 仅作为后续可复用候选 [X01] |
| feat/prefetch-model-and-accuracy-experiment | 1f19bdbba06c89cbaf9b7e6874f3473dc8123b2b | 只复用独立 offline 模型；`submit_speculative` 只有声明/定义，没有可用 producer 的历史集成结论需保留 [P01, integration manifest] |
| feature/sm120-exact-stage1 | f4dc28b2671c01939d98e4a968e6fb37b2e364d9 | 不合并：见 async audit；64-byte future 与 control ABI 9 不兼容 base ABI 4 |
| fix/ptx-async-copy-coverage | 936b8e548c0ad487975fea0de73938a1949bc156 | 最终 classifier 与后续 comment/.loc 修正已进 base [B01]；SM120 不得覆盖回旧 classifier |
| fix/mqsim-queue-depth-admission | 12ef13809dc13424981c123f13dc31e6d0456a80 | 已通过 hybrid 进入 base [B11]；SM120 integration 必须保留 |
| feature/thermal-reliability | 0069b2eec4d8b9d37cdbc9fbf84035d15572b0dd | 有 policy/refresh/controller 源码 [T01]，不是当前 base 功能；无本轮合并验证 |
| 全链路温度模拟 | fd11c3d98cde74977be7ec504c64429369b6fd3a | 历史独立根快照；不纳入最小依赖闭包；不是当前运行时的热模型证明 |

完整历史集成依据：[EVAL_BASE_INTEGRATION](../eval/EVAL_BASE_INTEGRATION.md)，基于同一个 B SHA；不能把历史 PASS 当成本轮 GPU PASS。

## Existing experiments and portability

本轮重跑现有 optional-tools CPU CTest：**42/42 pass**，29.40 s；[完整日志](evidence/cpu-ctest.log)。该运行使用已有 build-eval-tools（CMAKE_HOME_DIRECTORY 指向当前 base、CUDA OFF/MQSim ON/EVAL_TOOLS ON），没有伪称 fresh configure/build 或 live GPU。静态 fixture、fake driver、模型回归证明工具可用，不证明硬件 fidelity。

历史 `docs/proofs/2026-08-11-cd8p-vmem-tuning.md` 的六点是拟合输入，结果不能重标 VALIDATED_MODEL。`2026-08-11-vllm-exact-live-delay.md` 的选择性范围和 token 一致性是其历史快照范围，不能扩成全模型覆盖。110 GiB 证据是 sparse logical address span / checksum，不是 110 GiB 物理读取。旧 NAND/geometry 扫描须保留其 QD 修复前 provenance，正式矩阵必须用本次 frozen head 重跑。

本地 experiment archive 中存在 Qwen staging、E6 attempt、CUDA cache 等文件，名称本身不是通过证据；本轮未把它们升级成正式数据。`environment/models/qwen3-30b-a3b-current-model-bytes.json` 记的是 gpu01 在 2026-08-28 的外部 checkpoint inventory，自己声明 historical byte identity NOT_PROVEN。真实 checkpoint 尚未在本轮主机重验，因此 MOCK 使用明确的虚构 E/k fixture，完全不填充 Qwen 实测曲线。

## Required-document reconciliation

- doc47 与辅助材料 01–17/README 的旧主线、候选稿、红蓝裁决属于历史设计；本轮保留 heldout、分引擎硬件锚、统计和不确定性纪律，删除主 E2 成本论题。辅助材料的文献“零命中”和既有测量转述不作为本轮重新验证的事实。
- doc48 的 timing/capacity 区分可用；“SRAM 只需新增 profile、readahead 已在 runtime”不适用于 B。40 MB 与 40 MiB 还需分开，显存缓存模型不是物理 SRAM。
- issue07 旧漏洞在 B classifier 已收紧，但在 S 分支又需审计 [B01,S19]；issue10 的 issue stall 在 B 仍成立 [B02–B04]。
- issue15 不是本轮执行全部 23 项的授权：本轮用户明确要求 phase 1 结束即停。其 N 候选与 doc47 不一致，按硬件 contract 保留来源和转换。
- issue18 约束协议、interface ceiling、read/write granularity 的论述适合作为模型假设边界；不能把架构规范读成 GPU 普通 load 访问 HBF 的已实现协议。
- X 的 doc19 复用三臂差分、rho、统计、claim gates；其旧 cost EQ 与 thermal EQ 不进入本轮四 EQ。

文档清单的 bytes/hash/section index 在 [document index](evidence/document-index.json)；源码审计不是对辅助材料所有外部论文的全文重审。
