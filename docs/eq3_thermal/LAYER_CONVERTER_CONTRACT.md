# 新逐层转换器输入与验收契约 v1

状态 DESIGN_REQUIREMENT，NOT_IMPLEMENTED；四拓扑范围由 topology_scope.json 管理。
这是独立、默认关闭的适配器设计，不能改动现有核心/ABI/PTX/TMA/future/cache。

## 输入与生成

复用经过审查的 device/topology/thermal/power/sensor 分离配置模式。
器件列表提供 stable device_id、physical_type、profile/source ID；组件提供
component_id、parent_device_id、component_role（含 base_die / array_die）、
三维几何、材料、接触、边界与证据。数据路径与热连接是不同对象。
数量、die层数、块数、节点顺序均从列表与几何导出；无4+4、121、255或固定层数限制。
按稳定ID建立映射，输出ID→参考实体/RC节点/传感器收据；排序变化不改变物理结果。

热源有两种显式模式：per_component 的逐组件 P(t)，或 group + 显式成员/权重。
每个 group 权重非负、和为1，成员必须存在且可受热；非均匀逐die功率无需改接口。
等功率只由首例明确配置启用，不是缺省推断。base 与 array 分组独立。
如果同一组件同时有直接源和组源，要求显式声明叠加与来源，否则报重复分配。
零功率也须明确；缺源、缺数据不能静默当零。按区间积分检查源能量与实体能量守恒。

每个 HBM/HBF 的 base 必须显式存在、参与导热和独立功率/传感器映射；若选定未来
器件确无独立 base，需原件证据及专门profile，不能默认省略。GDDR 不凭空生成HBM
base；须按其真实或明确研究物理结构建模。传感器包含GPU、每base/die、stack热点
与显式权重均值；保留未来真实sensor provider而不伪造实测。

## 严格缺口与分阶段校验

输出缺口包含 topology_id、stage、parameter_path、reason、affected_claim。
未知/拼错拓扑、非法数量/配对、缺物性/冷却/功率映射、越界重叠/重复ID/非法单位
必须失败。禁止隐式fallback到mixed-direct、同名HBM物性或旧fixture。

阶段 thermal_only：完整几何/边界/规定功率可支持条件数值参考；未闭合服务参数
须仍登记且明确不作系统结论。阶段 system_behavior：路径/共享资源/仲裁/延迟/
PHY与转发能量/维护规则全部满足，缺任何必要项即阻止该阶段，不阻塞其它就绪域。
不得用“热输入能读”替代系统行为验收。

## 固定软件测试（不是标定矩阵）

- 四拓扑配置识别；每一种不完整参数均返回自己缺口，不变成另一拓扑。
- 非4+4 mixed数量、不同die数、器件与节点重排、不同实体数可生成；总stack仍8。
- 逐die非均匀和显式等权两种映射；base-only输入；能量分配积分守恒。
- 非法ID/数量/配对/负权/权重和/漏源/重复源/无base/无GDDR板域拒绝。
- 不同体积下传感器权重、层序/单位/热边与参考/RC对应一致。
- first-example可保留121/255/17作为回归期望，但不能写入转换器有效域。

通过这些测试只证明软件与输入契约，不证明实际热参数、求解器精度或拓扑服务行为。
新封装参考/RC求解属于下一次具体标定版本确认，不混入软件测试名下执行。
