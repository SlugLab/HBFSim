# 通用逐层转换器：实现与执行就绪证据

2026-09-19；隔离开发和固定软件验证。没有运行新研究封装温度求解、资源pilot、
标定、GPU或正式矩阵。原RC的FAILED和旧时间检查停止结论不变。

## 实际路径与复用决定

`tools/eq3_layered_ir.py` 规范化器件/几何/物性/功率/传感器；
`eq3_layered_export.py` 导出stock 3D-ICE与原P1 RC文本格式；
`eq3_layered_observe.py` 从真实场或RC节点输出得到同定义观察量和能量收据。
审查依据见REUSE_AND_BASE_DIE_AUDIT和LAYER_BACKEND_AUDIT。
没有复用旧两层转换器或P1物性；原RC求解器、文本reader和既有测试被复用且未改。
新增独立 `eq3_layered_rc_runner.cpp` 仅使用现有公开接口，不接入顶层默认运行路径。

IR接受um/mm/m及对应字段后缀；每个HBM/HBF base有实体、热容、导热、独立源
和传感器映射。支持逐组件和显式权重，按体积分到切片/单元，源权重与温度权重
分开。array-only与含base的stack统计有不同ID。外部GDDR明确排除封装热域。
识别四拓扑；不同数量/层数fixture可导出。未知拓扑/材料/几何/功率/配对拒绝，
系统缺口不会阻断完整规定功率的thermal_only，但不能提升为系统验证通过。

后端限制均显式：3D-ICE要求本导出路径所有横向实体边缘与均匀网格对齐；非零
额外接触热阻、非绝热侧面及单层双sink不支持；不移动几何、不平均各向异性。
各向异性为全局xyz分量，不能擅自跟器件旋转。候选有限厚度bond/TIM保留。

## 首例实际静态收据

工件在工作树外 `eq3_thermal/generated/layered-v3/`，每个目录含规范化输入、
两个网格、两个sensor映射、floorplan映射、原生输入及generation_receipt.json。
v1/v2目录保留为开发中间收据，执行包只绑定最终v3（补齐原数据图保留）。

| 轨迹 | 时长 | 输入能量 | 实体 / 有源实体 | z层 / 4mm参考单元 / RC节点 | 传感器 |
|---|---:|---:|---|---|---:|
| train | 100s | 1365J | 255 / 121 | 63 / 16128 / 3087 | 275 |
| development | 64s | 9632J | 同上 | 同上 | 275 |
| new_blind | 64s | 12152J | 同上 | 同上 | 275 |

8个base各C=0.01588128J/K，训练各输入26J；各自参考单元/RC节点/传感器ID
见收据。两后端总C=21.1572437888J/K。源总能量守恒误差仅浮点舍入。
这不是温度验证，也不是PHY/控制器/relay实物功耗校准。

参考实际parse-only通过63层/16行/16列/16128单元。该独立driver拒绝插件，
只init/parse/destroy；没有调用Emulator或构造热求解器。原RC CLI的read_only
也成功读入新model/events且不构造solver。不能把这两项报为温度PASS。

## 固定测试与独立检查

保留原34项Python测试与原核心CTest；新增IR、双导出、观察量、审批/启动和
独立runner测试。原始逐测试日志：`eq3_thermal/runs/converter-software-v1/`。
测试总数以最终python-tests-sealed.log为准；driver需显式设置测试binary才启用。
普通发现测试不启动研究模型。已知初轮一个测试对浮点字面量用精确相等不适当，
改为预期数值近似比较；没有修改物理目标或隐藏数值失败。

独立手算：A=2e-6m²，两层1/2mm、kz=4、cv=1e6，C分别0.002/0.004J/K，
串联半层热阻187.5K/W；上下对流另加半层导热，固定断言不调用主生成器推导期望。
非均匀两单元温度310/330K：mean320、真实cell hotspot330，不拿mean当hotspot。
base源1J切成两个单元各0.5J，不复制成2J。四拓扑小fixture、非4+4、单位重排、
负权/重复/空洞/初态冲突等固定测试均有具体断言。

原P1小fixture经新runner运行一次：131节点/10步、输入0.9J，残差约7.49e-12J；
它仅是软件回归，不算新增研究配置。新研究输入只inspect，未构造ThermalModel。

## RC数值版本与资源风险

保留全部层、正交面和共享lid/interposer横向自由度的最粗几何边界笛卡尔网格为
7×7×63=3087节点、8330边。未把它伪装成原建议≤512节点；原预算下收据标
BLOCKED_NODE_BUDGET。拟议新数值版本是这份显式3087节点网格，不是物性修改或ROM。
任何实际RC运行需在执行版确认其节点/时间策略与资源pilot，主线接受不等于已确认。

旧CLI累加时间可制造大量不同double dt缓存；静态全槽边界上界约28GiB，并非
实测RSS。独立runner改为整数slot/local-step索引，功率边界显式对齐，仍用原
隐式Euler引擎。首例100s/5ms inspect-only得到16个dt键，分解缓存约1.220GB；
这是缓存项估算，不含全部进程开销。输出每0.1s而每solver step积分能量与查温区。
不改引擎缓存/ABI，不用此检查声称600s内必然跑完。

## 可迁移构建与静态入口

在源码根，以任意独立BUILD目录执行（不固定服务器路径）：

```sh
cmake -S src/eq3_thermal -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD" -j 2
c++ -std=c++20 -O2 -Wall -Wextra -Wpedantic -I include \
  tools/eq3_layered_rc_runner.cpp "$BUILD/libhbfsim_eq3_thermal.a" \
  -o "$BUILD/eq3_layered_rc_runner"
python3 -B tools/eq3_layered_export.py \
  --profile configs/eq3_thermal/research/candidate_profile.json \
  --power configs/eq3_thermal/research/calibration_power.json \
  --trace train --mesh-um 4000 --step-s 0.02 --output "$GENERATED"
```

本轮实际异构建目录：`eq3_thermal/build/converter-core-relocated`；原核心源码
未改，CTest通过。parse-driver独立构建方法见LAYER_BACKEND_AUDIT。
上面导出入口无solver子进程；RC `--inspect-only` 不构造求解器；`--run` 默认关闭，
研究任务只经版本/哈希批准的launcher启动。launch复制已绑定输入到新目录后执行，
监控全部输出，成功/失败分别保留DONE/FAILED收据，不生成approval。
