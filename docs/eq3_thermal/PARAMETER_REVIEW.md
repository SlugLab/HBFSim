# 参数候选审核与软件检查收据

日期2026-09-19。只读来源审核/静态检查，不是实验或MODEL_FREEZE。

Sol分工核对配置消费者与原始来源；Astra独立核对候选几何、方法和预算。
本轮发现并已修正：

1. OCP Table4每档均允许virtual AXI数量1/2/4，不是low/middle/high各对应1/2/4。
2. 预检误称NRMSE，已统一为逐区域normalized MAE，分母max(参考范围,1K)。
3. GPU/HBM/HBF顶面及TIM厚度的父参数已分别列出，避免层数敏感性联动错误。
4. 找到OCP近似外包络后，未运行的48mm/8mm无锚候选改成64mm、12×16mm
   显式放大代理。保留放大9.3394%及内部结构假设，不称为官方尺寸。
5. 低功率测试幅值不能被误作满速HBF；合成幅值用HBM3E代理量级检查后调整，
   不提升为绝对HBF功耗。全上限840W可能超常物性域，明确不验证整个笛卡尔积。
6. 来源审核原先仅标85/105C外推，现同时标明0C也超出HeatWatch20–70C拟合域。
   未观察的生成时刻不填午夜假时间，只记录实际审核日期。

软件检查实际命令（worktree根，CPU固定测试）：

```sh
python3 -B tools/eq3_parameter_check.py --root .
python3 -B -m unittest discover -s tools -p 'test_eq3_*.py' -v
ctest --test-dir <existing-p1-core-build> --output-on-failure
git diff --check
```

结果：静态检查PASS，34Python测试PASS（新增5个固定静态验证测试，含错误拒绝），
P1核心1个CTest目标PASS（原13例），diff检查PASS。无新热求解器运行，原12个
数值配置+3个求解器前失败的计数不变；无GPU负载、无驱动/环境修改。
未修改include/src/root CMake/ABI/PTX/TMA/future/cache，未改baseline。

静态检查能证明：255块互不重叠且在域内，121有源区/17输入组，HBM48die/HBF64die，
63层并集，三条功率轨迹长度/幅值/能量、单组独立激励秩17，预检点数/总时长、
必需账本字段与OCP选项。不能证明工艺可实现、原件参数适用于目标产品、连续
热方程精度、能量闭环或模型已标定。工具没有求解器/网络/批准写入功能。
