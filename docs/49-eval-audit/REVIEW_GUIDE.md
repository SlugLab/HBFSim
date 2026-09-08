# HBFSim `eval_base` 中文审阅指南

本分支提供当前实现与 2026-09-08 的 EQ1–EQ4 重规划，供代码和实验设计审阅。发布将 `eval_base` 从旧基线 `fc829992ecdc3ca68881656722b67a31067c5d33` 正常前进已有的 119 个提交到 `254d65a66279fbaffc5c185d04fbe41dc8dbba44`，再加入此前重规划文档、两项文档工具兼容改动及本次审阅材料与导航。这里的两项工具改动是旧矩阵生成器防覆盖与测试对新旧矩阵的分别绑定；本次新增提交没有运行时功能变更。

审计与矩阵的 `source_sha` 仍绑定 `254d65a66279fbaffc5c185d04fbe41dc8dbba44`；发布提交不会重新证明这 119 个提交的科学门禁，完整实验未运行。发布前另外重跑的 21 项 CPU 文档工具与矩阵契约检查通过，见[检查收据](review-evidence/20260908/publication-cpu-checks.json)和[日志](review-evidence/20260908/publication-cpu-checks.log)；这不是 GPU 性能验收。

建议按下列顺序阅读：

1. [EQ1–EQ4 主规范](../49-new-evaluation-plan.md)：研究问题、机制边界和方案变更。
2. [审稿质疑回应矩阵](reviewer-response-matrix.md)：每项质疑对应什么机制、观测和验收。
3. [当前状态与证据复用](current-state-20260908.md)：现有能力、历史来源及不可外推的结论。
4. [执行计划](execution-plan.md)和[300 行实验矩阵](run-matrix.csv)：真实入口、显式阻塞、样本数与资源预算。矩阵为待审阅的注册计划，未启动完整队列。
5. [图表契约](figure-plan.md)：坐标、比较臂、缺失点和有效性要求。
6. [科学主张门禁](claim-gates.md)与[结果数据契约](result-schema.md)：何时可以声称通过，哪些证据不能混用。
7. [代码与来源审计包](review-evidence/20260908/README.md)：EQ1、热机制、EQ3/4 和官方来源记录。
8. [20 个最小后续补丁](minimal-followups.md)：尚未实现的范围、依赖与必要验证。

## 请优先审查的科学阻塞

- EQ1：增量等待 oracle 已修正，但 scalar CPU 检查不关闭 GPU/SASS first-use、独立工作量 W、计时误差和 capacity/TMA 生命周期门禁。当前 donor 能力不能通过整支合并视为已集成。
- EQ2：已有热反馈与 ROM 数值检查有明确 toy/窗口边界；真正 off、实际刷新命令及完成/能耗闭环、完成吞吐分箱和当前 live 组合尚未闭合。
- EQ3：物理容量比 `r` 不能用旧缓存比例代替；独立 staging/reservation、KV 最小预算及源自器件拓扑的映射仍需补丁。
- EQ4：TRACE_COMPOSED、固定 token/horizon 和 offline replay 不等于真实并发服务；dense 匹配、实际 active sequence 与因果预取对照仍需验收。
- 正式图与结论：新 schema/renderer、独立 gate receipts 和完整 raw 绑定仍需实现或补证据。MOCK、CPU PASS、来源读到和迁移成功均不等于实物 HBF 性能验证。

## 分享范围与复现边界

仓库提供实现、计划、原字节历史方案与小型审计收据；`review-evidence/20260908/` 是经明确选拷的发布副本。模型权重、完整 raw、原主机环境、部分外部源码 checkout 和完整迁移清单未上传；记录里的 `/root/hbfsim-exp/...` 等路径只表示原执行环境。需要复现相应实验时，应先按入口所需的 source/build/input hash、数据与门禁单独准备，不能将 clone 成功视为已具备运行条件。

[历史方案](history/20260905/README.md)保留原始内容和 SHA；其中旧相对链接可能依赖原目录布局。本次未重写其字节。当前导航以本指南与主规范为准。迁移与实际轻量检查概览见[发布摘要](review-evidence/20260908/交付结论.md)。
