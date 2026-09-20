# ECC 与刷新数据身份：最窄剩余结构方案

状态：PROPOSED_NOT_IMPLEMENTED。独立 ECC8 与维护4热集成已完成，但不能宣称刷新降低该 ECC 代理成本。该文件不构成用户批准。

## 问题与最小复现

`run_causal_point.execute` 同时启用 `hbf_read_cost_proxy.mode=conditional_nand_history_v1` 与 shared maintenance 会显式拒绝。前台 `CausalExecutor` 的读取只有 tensor/partition/stripe 身份；`MaintenanceDriver` 有 extent/source_block/version 身份；`ReadCostProxy` 只有逐栈等效年龄。没有可信映射时，成功刷新一页不能重置全栈年龄，也不能确保在途读取使用的源未被擦除。

已有 `CausalMaintenanceAgeAdapter.consume_receipt` 能按真实完成时刻推进受影响 extent 年龄并消费版本提交，因此不需要重写其年龄积分或热求解。现有前台迁移的源保护也应优先复用，不能再开第二套版本账本。

## 最小接口与必需结构变化

1. 隔离 workload 层生成可逆的 tensor 字节范围→extent/版本/stack/channel 映射。整合相同成本的范围，但每次成本加权须保留覆盖关系和有效字节守恒。未知身份拒绝组合，不从任意地址猜 die。
2. 新只读成本入口按同一维护账本查询 extent 年龄、当前物理块的实际 P/E，并将参考温度统一换算。使用已观察温度外推到首次服务时间，不提前修改账本年龄前沿。
3. 读取首次取得资源时获取当前源版本的读引用；成功或失败唯一终止时释放。刷新 program 成功后 CAS 提交新版本，只重置提交 extent 的年龄；旧块 erase 必须等待源读引用排空。并发较新前台写导致 CAS 失败时不得覆盖新映射，已发生的能量仍保留。
4. 刷新和前台仍使用同一个 `CausalTopologyService` 的现有资源；不修改其公平分配算法、不扩展原默认 MQSim、不新增后台线程。物理 NAND 提交仍仅在现有隔离 native 能力边界内对照验证，不声称流体路径实际执行 TB/s NAND。

第1和3项影响请求身份和资源生命周期，不能用“只增加接口”作为豁免。有效 AGENTS.md 的最新阶段条款已授权范围内新增隔离实验模块设计；实施前须进一步判断是否能完全复用现有版本/源保护接口。若仅为获批隔离消费者的接入，可在阶段内继续；若需要改变已有调度或所有权核心，才将具体超出部分提交用户确认。不能仅因旧条款提及重构就重复申请已授予的阶段权限。独立维护、ECC和热敏感性工作不受阻。

## 拟改位置及行为

| 文件/符号 | 拟改行为 |
|---|---|
| `causal_workload.py:CausalExecutor` 的分条提交和完成消费 | 附加真实 extent 覆盖、版本引用和唯一释放；复用已有迁移映射/源保护 |
| 新隔离 extent 成本桥 | 只读同一维护年龄/磨损账本，计算分组预期工作；无自己的资源账本 |
| `ecc_service_adapter.py:ReliabilityCausalService` | 成本输入从逐栈转为已解析范围；关闭时保持现状 |
| `maintenance_driver.py:MaintenanceDriver` 的提交/擦除阶段 | 查询共享源读引用，旧块排空后进入现有 erase 入口 |
| `run_causal_point.py:execute` | 只有完整映射能力通过时才允许组合，保留不支持守卫 |

## 验证、回滚与资源

先固定小数据集：刷新一个 extent 不影响邻居；program失败/CAS冲突不重置年龄；读跨提交使用有效旧版本；旧块读未排空不擦除；重复完成不能重复释放；维护与前台竞争同一资源；无新增输入仍排空/恢复。再做极小 native 元信息语义对照和四拓扑相同输入的新ID paired pilot，记录身份、RSS/wall/磁盘，CPU串行。正式大矩阵复用此前预算方法，但要以该最小 pilot 的实测成本冻结。

所有新行为默认关闭，代码和工件独立、小提交可回滚；旧 raw、基线和已接受结果不覆盖。未完成该方案前，报告可给出维护争用、年龄/磨损和独立 ECC 成本，不能给出刷新—ECC联合收益，也不能标完成整个可靠性闭环。
