# New HBFSim Evaluation plan — review snapshot, 2026-09-05

本轮完成第一阶段：审计、增量实施计划、矩阵、数据契约与 MOCK 图。**没有实施新的 runtime、合并 feature branch 或运行硬件性能实验。** `docs/47` 的旧 E2 不再是主问题；本文件是本轮新 Evaluation 的入口，旧文档和历史证据保留为来源。

审计基础：`eval_base@fc829992ecdc3ca68881656722b67a31067c5d33`；异步候选：`feature/sm120-exact-stage1@f4dc28b2671c01939d98e4a968e6fb37b2e364d9`。没有把远端跟踪分支当成自动最新承诺，本轮冻结本地已有 Git 对象。所有实现判断按 [source ledger](49-eval-audit/source-ledger.md) 绑定 branch、完整 SHA、文件、函数、行段。`INFERRED` 是代码推断；`STATIC_REPRODUCED` 是本轮编译转换复现；二者都不能填成物理性能 `MEASURED`。

## Four fixed questions

1. **EQ1:** Can HBFSim faithfully reproduce observable GPU-side delay effects and flash-storage service behavior on available hardware?
2. **EQ2:** Does HBFSim preserve asynchronous GPU memory semantics, particularly issue-completion overlap for TMA and deferred global-memory accesses?
3. **EQ3:** Under what combinations of media parallelism, media latency, and fast-tier residency is HBF useful for LLM inference?
4. **EQ4:** How do model sparsity, serving concurrency, and prefetching shift that HBF feasibility boundary?

reference / fast / hybrid 的 runtime、host cost、time_scale、coverage 保留为 reproducibility / appendix table。FIFO/LRU/CLOCK 只有改变 EQ4 结论时才做 ablation。热模型不进入主 EQ；当前 base 没有 temperature→request timing 闭环 [B18,T01]。

## 当前结论与停止点

| Question | 今天的真实能力 | 缺口 / claim gate |
|---|---|---|
| EQ1 GPU | 支持范围识别、ld/st 改写、issue-stall；旧 checksum 与六点标定工件可查 [B01–B06] | 新 dependent chain、globaltimer 与 CUDA Event 分域、已知 D 精确注入；GPU 当前不可用 |
| EQ1 flash | MQSim request size / QD / fixed arrivals、P50/P99/带宽工具与 admission 修复已在 base [B10–B12] | 冻结实盘身份和 calibration/heldout；同 arrival stream；三臂真实 backing/cache 对照 |
| EQ2 | S 分支有 future/TMA/descriptor helpers、CPU 与 GPU test sources [S01–S19] | **不可直接合并**：已复现条件消费漏 wait；普通 cp.async coverage 回退风险；TMA group/CFG/timeout 尚需闭合 |
| EQ3 | 全专家对象容量 replay 与页时序工具可复用 [B13–B16] | 当前串行 replay 不能生成并发服务率–decode 相图；新增 rho 预算与并发事件重放 |
| EQ4 | E/k/layers 可从 inventory 解析；路由 capture 在 X 分支可复用 [B14,X01] | base 缺 capture、每 step active sequences、动态预算和真实预取计时；trace-composed 不得称 live serving |

六图均为人工布局数据 **MOCK DATA — NOT MEASURED**，不表达实测趋势。GDDR7 是物理 GPU 执行载体，验证范围分流、插桩、关键路径延迟、异步语义；不是 HBM 或 HBF 的介质等价物。

## 阅读顺序

- [Current capability audit](49-eval-audit/current-capability-audit.md)：当前实现、已有实验、分支复用、旧文档纠偏。
- [Async/TMA audit](49-eval-audit/async-tma-audit.md)：符号逐项、PTX 语义、CFG、插桩、完成语义、coverage、fail-closed，以及复现。
- [Hardware ground-truth contract](49-eval-audit/hardware-groundtruth-contract.md)：可检验对照、冻结切分、计时域与统计。
- [Figure plan](49-eval-audit/figure-plan.md) 与 [schema](49-eval-audit/result-schema.md)：数据替换契约和六图。
- [Execution plan](49-eval-audit/execution-plan.md)、[run matrix](49-eval-audit/run-matrix.csv)、[claim gates](49-eval-audit/claim-gates.md)：实施顺序、预算、blocking dependencies、最小与理想配置。

## 两个必须修正的方法学细节

**Async 图不能混淆 issue 等待与 consume residual。** 旧 issue-stall 路径已经在 issue 付完 D，consume 点 residual 可能为零；“旧路径约等于 1”只能描述总 exposed stall / D。因此 fig-e2 同时画 consume residual、issue+consume 总等待、new 路径 residual parity。oracle `max(0,1-W/D)` 保留，任何预期趋势仍是 hypothesis。

**N 候选并不一致。** doc47 给 256/1024/3000/15000；issue15 给 256/1024/1536/4883。矩阵保留并集 256/1024/1536/3000/4883/15000，单位统一为每 cube 的独立 sense 单元；不把 channels、dies、planes 或吞吐需求反算数自动当成真硬件拓扑。转换和不确定性见 hardware contract。

## Deliverable commands

在本仓库根目录执行：

```bash
python scripts/eval/validate_results.py --input results/mock/eval.csv
python scripts/eval/render_figures.py --input results/mock/eval.csv --out figures/mock --watermark
python scripts/eval/render_figures.py --input results/measured/eval.csv --out figures/final --strict-no-mock
```

正式命令需要真实 CSV 和相邻 `manifest.json`（含 raw hashes / provenance / calibration domain）。当前 `results/measured` 和 `figures/final` 只有说明文件；没有假冒正式结果。绘图函数不区分 mock/real 算法，只读取数据；MOCK 自动水印，严格模式逐行拒绝 MOCK。

完成到此，等待人工审阅后再实施 runtime 改动和真实实验。
