# 2026-09-08 审阅依据包

这是从原执行环境 `/root/hbfsim-exp/reports/eq-replan-20260908/` 选拷的小型报告和收据，供 GitHub 上审阅；不是完整实验数据集。[中文审阅指南](../../REVIEW_GUIDE.md)给出阅读顺序，[发布摘要](交付结论.md)概括已经完成与仍待验证的范围。

| 主题 | 入口与收据 |
|---|---|
| EQ1 源码/机制 | [审计](eq1-audit.md)、[实验组](eq1-experiments.json)、[CPU 检查](eq1-emitter-tests.json)、[双 L 算例](eq1-delta-stall-oracle.json) |
| EQ2 热机制 | [审计](thermal-audit.md)、[13 项历史复用](thermal-reuse-table.csv)、[CPU cells](thermal-eq2-cells.json)、[hash 检查](thermal-check.json)、[golden 检查](thermal-golden-check.json) |
| EQ3/4 容量与路由 | [审计](eq34-audit.md)、[27 项 CPU 检查](eq34-confirm-cpu-summary.json)、[日志](eq34-confirm-cpu-tests.log)、[执行前 hash](eq34-confirm-hashes-before.json)、[执行后 hash](eq34-confirm-hashes-after.json) |
| 官方来源 | [来源 ledger](source-research.md)、[冻结的软件版本](source-software-freeze.json) |
| 迁移摘要 | [报告](migration-report.md)、[摘要 manifest](migration-manifest.json)、[实际清理空间](migration-cleanup.json)、[smoke 比较](migration-smoke-comparison.json)、[恢复 runbook](migration-runbook.md) |
| 交付检查 | [文档检查](document-validation.json)、[旧流水线日志](legacy_pipeline.log)、[矩阵摘要](matrix-summary.json)、[发布文件清单](bundle-manifest.json) |
| 本次发布前检查 | [21 项 CPU 检查收据](publication-cpu-checks.json)、[日志](publication-cpu-checks.log)；只验证文档工具和矩阵契约 |

## 哪些材料没有上传

- 模型权重、完整原始实验目录、GPU/CPU 编译产物、Conda/工具链及外部 prototype checkout 未包含在本包。
- 完整逐文件迁移清单、全部 Git 仓库明细和完整本地交付清单仍在原主机。`migration-manifest.json`、`retained-temporary-receipts.json` 等 JSON 内的路径字段保留原字节；引用到文件并不表示该文件已被选拷到 GitHub。
- `document-validation.json`、原审计清单和 SHA 描述的是 2026-09-08 原记录。导航改写后的 Markdown 副本以 `bundle-manifest.json` 中 `published_sha256` 为准；`source_sha256` 保留与原记录的关系。
- 原字节历史方案仍在 [history/20260905](../../history/20260905/README.md)。其历史链接可能依赖原布局；本次不重写或重新认证旧实验。

## 路径与脚本

正文代码块、CSV/JSON 中 `/root/hbfsim-exp/...`、`/mnt/disk0/...` 等路径属于原执行环境的 provenance，未替换为虚构的可下载 URL。Markdown 导航已经改成仓库内相对链接，源码行号改用 GitHub `#L...`。

小型 Python 文件是当轮检查或生成记录的副本。除独立算术 fixture 外，部分脚本固定了原主机路径、UUID、外部输入或输出位置；保存脚本不代表任意 clone 可直接执行，也不提供完整环境复现。审阅阶段应先读源码、输入绑定和 [执行计划](../../execution-plan.md)，满足门禁后再准备独立运行目录。分享本包不启动原控制器或实验队列。
