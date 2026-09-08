# HBFSim 迁移报告 — 2026-09-08

> 分享副本：审计事实、数值和原执行路径保持原记录；仅调整导航。大模型、完整 raw、部分本机清单与可执行二进制未随仓库发布，范围见 [审阅包说明](README.md)。

状态：**COMPLETE_VERIFIED_AND_SOURCE_CLEANED**。工作区物理位置为 `/mnt/disk0/hbfsim-exp`，保留 `/root/hbfsim-exp -> /mnt/disk0/hbfsim-exp` 兼容入口。已在校验、切换和 smoke 通过后，精确清理本次源隔离目录；根盘实测释放 **131.912 GiB**。最终状态见 [manifest](migration-manifest.json)。

## 设备与实际空间

| 项目 | 清理前 | 清理后 / 最终检查 |
|---|---:|---:|
| 根盘可用 bytes | 16771158016 | 158410854400 |
| 根盘可用 GiB | 15.619 | 147.532 |
| 本次清理前后观测差额 | — | 141639696384 bytes / 131.912 GiB |
| 目标盘可用 bytes | 690323095552（preflight） | 548464287744（最终 guard） |
| 目标预留 / 默认单任务输出估计 | — | 128 / 64 GiB |

目标为 PM1733a，serial `S6USNE0TA08224`，`/dev/nvme2n1p1`，ext4 UUID `37be6892-a8aa-4fae-885d-5feed3b28fab`，实际主机挂载 `rw,relatime`。原根盘为 Lexar `/dev/nvme1n1p2`，并非 CD8P。空间为采样时刻值，会随其他主机写入变化；差额来自 [cleanup receipt](migration-cleanup.json)，不以源目录大小推算。

## 完整性与兼容验证

- 初始盘点：519221 文件、92018 目录、2187 软链接、1 个非活动 FIFO；6 个稀疏文件，67 个外部软链接。allocated unique141174820864B、apparent140039527429B。任务生成报告后，最终源清单为615503条。预算364513120256B，含复制临时8GiB、工具8GiB、后续64GiB、预留128GiB。
- ACL 数字UID/GID、xattr、内部硬链接、稀疏、软链接 pilot 通过；37.65MB实际复制 pilot为0.167s。首次可恢复复制311.709s；最终同步17.085s；全文件checksum dry-run218.444s，exit0且差异日志0B。路径与元数据、6个稀疏文件、内部硬链接和77项独立关键SHA256均通过。目标唯一预期额外路径为本任务归属标记。
- 660个有效Git仓库的HEAD、分支、状态、worktree和submodule关系核对通过。Git将worktree目录显示为新物理前缀；仅对已验证等价的路径行归一化，原始差异另存。无需worktree repair，也未改共享外部Git管理数据。
- 迁移前后9个环境/工具入口检查通过，涵盖Python/Conda、torch导入及vLLM元数据、legacy Python、ptxas/cuobjdump、动态库、历史runner只读计划和GPU身份。16个模型shard头/索引及4096B权重张量只读加载结果一致，67个外部链接均有效。没有执行模型推理。
- 切换前、清理前检查项目相关进程/cwd/fd；没有发现需保留的源写入者。枚举全部挂载点并按精确前缀检查，包含同设备bind mount风险；清理前再次核对全部615503源条目。只移除 `/root/hbfsim-exp.migration-source-20260908`，不跟随软链接，不删除外部硬链接目标或其他备份。

以上进程检查限定本项目，不声称冻结整机。Git、checksum、smoke、cleanup原始记录及最终源清单保存在原执行环境 `/root/hbfsim-exp/reports/eq-replan-20260908/`；本分享包仅收录其中小型报告和收据，不包含完整逐文件清单；[临时记录保留映射](retained-temporary-receipts.json)说明原记录内的 `/tmp` 执行时路径与现存副本。

## 任务状态

| 原任务 | 迁移后状态 | 自动恢复 |
|---|---|---|
| natural-formal-next / 20260908-natural-resume002 | PAUSED_BY_USER；STOPPED，无owned survivors | 否 |
| eval-base-integration / 20260908-resume/chain-003 | PAUSED_BY_USER；STOPPED，无owned survivors | 否 |
| 本轮CPU审计、文档同步与迁移 | 已完成，记录保留 | 不适用 |

原暂停记录 `reports/experiment-pause/paused-status.json` 保持 `automatic_restart_authorized=false`。未修改驱动、全局CUDA/Conda、GPU频率或功耗上限、挂载配置、全局shell配置；未下载模型、启动完整矩阵、合并或推送分支。

## 入口保护、科研可比性与恢复边界

[任务启动器](migration_launch.py)固定检查兼容链接、设备/UUID/rw和空间，将八类缓存环境变量仅传给其子进程，按软件栈fingerprint隔离在目标盘；不改变全局环境。默认每60s重查挂载与128GiB低水位，异常只清理本启动器拥有的新会话并保留原错误。最终主机 `--check` 为GUARD_PASS，安装版本的13项CPU mock测试通过，没有向真实进程发送信号。输出估计用于准入，不是文件系统配额；实际输出需受每cell资源预算约束。

迁移改变backing设备/缓存路径，历史capacity wall time不能直接当作新PM1733a结果。后续逐run记录backing身份和缓存状态、模型profile、搬运/排队开销、model time与wall time；不改历史结果标签。外部 `/home` 模型及 `/opt` 库仍为外部依赖，本轮不迁移或删除它们。

源隔离副本已清理，**不再具有整目录rename回滚能力**；完整校验后的目标副本是当前权威工作区。恢复到根盘需要新的容量审查与逆向迁移，详见 [runbook](migration-runbook.md)。清理后新增/修改的文件仅为本任务交付文档、保留收据和启动器修复；它们另由最终交付清单绑定。

预检中出现的沙箱只读视图、二进制Git diff UTF-8读取失败、ACL名称与数字表示差异均保留诊断，分别经主机只读核验、原字节保存、数字模式校验解决。没有为消除错误修改全局环境。原checksum和历史失败数据保持原样。
