# Concordia 五个 PR 审核与合并决定

2026-09-26。用户明确授权审查项目规范、编程质量和对应计算架构的通用性，并在条件均满足时合并。实际目标是 `vickiegpt/Concordia` #1–#5，基线 `c60cfd8ab54fd1ec4512f749ad61cccc4a390a05`；并非同名 hetGPU 仓库上的另一组 PR。精确 head 与当前审核状态见 [DECISION.json](DECISION.json)。

**决定：目前 0/5 合并。** #2/#4/#5 有可复现的正确性或兼容性问题；#1/#3 的窄文本输入修复有独立正面证据，但尚未满足全部验证/范围条件。没有因有管理员权限、分支显示 CLEAN 或 QKV 历史成功而跳过验收。

| PR | 独立结果 | 合并决定 |
|---|---|---|
| [#1 FSEL NaN](https://github.com/vickiegpt/Concordia/pull/1) | 两种编译器生成的 payload 和九个直接检查支持已声明编码；与模型名称/尺寸无关。带执行谓词的其他编码安全拒绝。 | 等待 workspace 验证；未发现本次 payload 修改的语义缺陷。 |
| [#2 IMAD.HI](https://github.com/vickiegpt/Concordia/pull/2) | sm_120 编译器生成的非零加数是寄存器对，当前代码只取低32位。独立算例应得7，转换后为0；不支持的操作数还会被过滤并错位。 | 需修复后复审。 |
| [#3 CS2R](https://github.com/vickiegpt/Concordia/pull/3) | 文本 SRZ 寄存器对修复合理，隔离单元子集45/45通过；二进制解码路径缺失特定寄存器语义，不能声称已修复。 | 等待范围说明/未解决审核意见及 workspace 验证。 |
| [#4 GEU/BF16](https://github.com/vickiegpt/Concordia/pull/4) | 普通 RN 和简单 GEU 通过；真实编译器生成的 BF16 `.RZ`、`.RELU` 被无诊断地改成 RN、无 RELU，结果不同。 | 需正确处理或显式拒绝边界形式，再复审。 |
| [#5 寄存器对](https://github.com/vickiegpt/Concordia/pull/5) | 隔离单元子集47/47通过，但仓库已有整数集成 fixture 的立即数乘数被新 guard 拒绝；`.64/.128` 宽访存遗漏拒绝，仍输出 `.u32`。 | 需修复已有兼容性回归与拒绝边界，再复审。 |

## 仓库标准与验证限制

`CONTRIBUTING.md` 要求初始化子模块并使用 `cargo test --workspace`。实际执行的 CPU 编译预检 `cargo test --workspace --offline --no-run` 返回101，缺少 `ext/llvm-project/llvm-sys/Cargo.toml`；失败原文已保存。这是既有依赖阻塞，不是测试通过，也不归因于五个补丁。没有安装漂移版本或运行 GPU 测试。

远端没有 PR CI status checks 或分支规则；CLEAN 仅说明可机械合并。#2/#3/#4/#5 分别仍有2/1/1/4个未解决审核讨论。改动集中于 lifter.rs 并含窄测试，但既有集成测试与二进制入口仍需覆盖。#3/#5 新增区域还有 rustfmt 差异；旧文件本身也存在格式债务，不能把全部格式差异都归咎于新补丁。

## 架构通用性与证据边界

这些修改没有硬编码 QKV 模型、张量地址或尺寸；这不等于指令语义已具有通用性。本次用任意寄存器、独立整数算例、NaN payload、BF16 舍入/RELU 和别名/谓词 fixture 检查实现，离线 CUDA13.1 ptxas/cuobjdump 目标 sm_120；没有宣称其他架构、所有 SASS 编码或 GPU 数值测试均通过。PR3 二进制解码、PR4 FSETP 组合谓词/FTZ、PR5 descriptor atomic 等既有问题与新回归在详报中分别标明。

PTX 语义参见 [NVIDIA 官方 PTX 指令文档](https://docs.nvidia.com/cuda/parallel-thread-execution/)。SASS 非公开完整编码语义采用保存的编译器观察作为证据，不能冒充规范。详细原始检查见 [数值/比较审核](semantics/REPORT.md)、[寄存器对审核](pairs/REVIEW.md)。首次 harness 缓存误复用的无效结果和孤立 predicate fixture 的汇编失败均保留，并明确标记；最终报告仅使用纠正后的隔离结果。

这些审核发现不抹去 HBFSim 固定请求147-storage实际输出通过的原始事实，也不把该结果推广至上述反例或所有消费者。当前 PR 未改动、未合并，历史模型验收与上游普适性验收是两个不同结论。
