# 旧 floorplan / trace / golden / ROM 的当前适用性审核

日期：2026-09-19。用户新增要求：先分析审核旧工件是否适合当前实验，
再决定复用、替换或修改。本文没有启动旧实验、导入旧矩阵或改写旧结果。

## 结论

**不得将旧 golden 或 ROM 直接用于当前四种八-stack EQ3。**
可直接保留的是历史收据、失败结果、来源与方法线索；可借鉴的是分层几何、
能量守恒与独立验证流程。当前拓扑的输入/输出映射、参考温度数据，以及
任何依赖新几何的矩阵必须重新建立或重新验证。无需因为历史模型叫 ROM
而替换现有 RC；先验证当前 RC 是否满足精度和规模要求。

本次已读到的是固定 donor 源码及仓库内历史审计收据；旧实际 .flp、功耗原始
CSV、温度 raw 和 A/B/C/D 矩阵仍为 HISTORICAL_NOT_ACQUIRED。
以下“源码确认”不等于“原始数值已重新取得”。哈希能识别字节，不能证明
物理参数真实、科学设计正确或适用于新的实验。

## 逐项判断

| 对象 | 已核实的旧定义（DOC_DERIVED） | 当前处理 |
| --- | --- | --- |
| 历史哈希/审计 | 2026-09-08 核对旧工件，无新3D-ICE运行；旧精度范围受当时几何等约束 | **保留原样**，仅作历史溯源；不能升级为当前 PASS |
| 拓扑/输入输出 | `case_nodes` 为 gpu、hbm、hbf.base、hbf.s0.l0…；只有一个HBF栈，GPU/HBM聚合 | **重建**当前8HBF+GDDR、混合direct、4+4relay、4对DASH映射；不能复制单栈矩阵八次后忽略共享散热耦合 |
| floorplan | 域50×30mm；HBF footprint16×10.975mm；GPU/HBM/HBF探针位置固定。实际各块边界/材料继承缺失的.flp | **证据不足＋重建**新布局；原件找到后才审核块交叠、材料覆盖与坐标，不由探针反推完整floorplan |
| 垂直结构 | NAND30µm、bond1µm、base50µm、substrate100µm；G1为4µm filler+50µm TIM+75µm cap，8Hi527µm、16Hi775µm | **方法参考**；这些是当时情景值，不是OCP/Sandisk官方层厚。当前逐die模型需明确几何/材料依据及范围 |
| 高度对比混杂 | G0固定775µm，使8/16Hi上方mold分别377/129µm；G2显式TIM/cap但filler252/4µm | **不得直接归因于层数**；高度与上方热阻联动。G1固定顶界面可作为新对照设计思路，不自动成为真实封装 |
| 材料/冷却 | silicon来自上游fixture；mold、bond、substrate、TIM、cap多为Class-C敏感性假设。ambient303.15K，nominal顶HTC1e-7W/(µm²K)=1e5W/(m²K) | **重新定来源/边界**；不同GPU冷却平台不能沿用同一标定。不能把cap的silicon替代值称实际lid材料 |
| 网格/边界补丁 | 4/2/1mm与可选0.5mm非均匀网格；U-fill补丁仅存储估计，bottom-sink补丁改热源向量与边界一致性 | **分开处理**；先保留stock参考。需要底边界时独立回归；不能无条件套用或忽略物理补丁 |
| 功耗轨迹 | golden单位训练1W；held-out按形状缩放至GPU300W、HBM95W、HBF53.72W；另有CL/BW机制fixture GPU30W/HBM5W/每plane0.01J | **方法可留、参数重审**；两套功耗不是同一实验。均不能直接变成H200/HBM4/HBF产品测量，不能用高功率制造收益 |
| 时间尺度 | golden固定10ms；历史某HBM trace总长50.021µs，小于一个ROM步长约199.916倍 | **重建/重采样须证明守恒与时序**；宏观能量envelope只能PROJECTED，不是原始瞬态重放；24h保持也不得改成24s |
| 传感器 | `stack_text` 使用 `T(layer,x,y)`点探针；旧HBF hotspot由这些输出最大值产生 | **替换输出映射**；点探针max不是每层空间max；当前逐stack平均/热点、面积/体积权重与grid hotspot必须分开 |
| golden时间对齐 | `convert_transient`人为写t=0 ambient，后续将索引i-1最后一列放到i×dt，最后一条raw未导出 | **优先重新审核**。若raw首行就是dt，该写法可能正确但裁掉终点；若是t=0或其他约定则可能错位。未取得raw前不宣称已证实整套golden无效，也不沿用精度结论 |
| 训练/验证 | 单位阶跃训练；square_wave、burst、mixed_gpu_hbm_hbf、write_heavy、read_heavy五类整轨迹held-out；检查文件哈希不重叠 | **复用原则**，新封装需独立GPU/各memory源激励、非同步/热点/冷却完整保留轨迹。旧哈希不证明新输入域覆盖 |
| ERA拟合 | 外部组rank192、HBF组rank32；矩阵由unit training构建，held-out在其后计算误差 | **仅方法参考**；源码未显示用held-out直接拟合矩阵，不能指控已证实数据泄漏；历史是否反复以同组调rank仍UNKNOWN |
| ROM物理含义 | runtime224 state、10ms；谱半径8Hi0.965311…/16Hi0.990256…，对应数值τ0.283/1.021s | **不能继承**新的时间常数/时长；224是降阶状态数，不是die数。稳定不等于被动、非负热响应或能量解释成立 |
| ROM身份链 | golden模型hash744707…/a6ee59…；runtime模型hashcc9351…/7634f3… | **必须核对转换链**。不同哈希不自动意味着错误，也不能假设同一矩阵；需要payload、bias/offset、单位与I/O映射比较 |
| 运行时/刷新 | 历史独立runner off仍算ROM，completion可跨bin；retention/refresh为离线需求/能量投影 | **不能移植为当前验收**；保留P1真off、因果时钟、实际完成后维护年龄/能量/磨损更新要求 |

## 可保留与必须替换的最小边界

1. 保留原始字节、哈希收据、源提交与失败记录；原件日后取得放入独立历史目录，
   验哈希后也不覆盖NEW_REFERENCE。
2. 保留几何分层/单位转换/单位源响应/整轨迹held-out/收敛检查的方法。
   旧生成器依赖遗失模板，不能称“脚本还在所以可完整重建”。
3. 当前四拓扑的物理几何、数据路径、功率源及传感器映射独立生成。
   8/16层HBF与12/16层HBM等实际die数逐项绑定，不从OCP最大高度替代产品构成。
4. 当前小型NEW_REFERENCE是抽象GPU+4HBM+4HBF热源铺片模型，**也不是完整逐die
   真实封装**。它仅检验工具链和粗化误差；不能因为比旧模型新就升级为物理金标准。
5. 只有新几何/边界/功率/输出合同固定、参考收敛且RC误差满足预定线后，才决定
   是否需要新ROM。改变拓扑、材料、接触、散热、采样或输出映射要重新生成或证明有效域。
6. H200/HBM3E公开功耗可作独立物理代理；5090/GDDR7只能标定其可观测平台。
   二者都不能提供完整HBF实物金标准。不得把两台平台拼成伪同步实测。

## 如取得原件后的审核顺序

验身份与许可 → 核对原始单位/时间戳/结束点 → 复原完整floorplan及边界 →
核对功率域与面积分配 → 核对sensor输出定义 → 核对训练/保留轨迹 →
检查矩阵维度/离散步长/状态偏移/稳定与非物理响应 → 确定旧作用域复现或新版本。
先做静态/小规模验证；正式矩阵仍需独立用户版本/哈希批准。

## 精确来源

- 固定donor `fd11c3d98cde74977be7ec504c64429369b6fd3a`：
  `experiments/package_thermal/phase2/phase2_3dice_campaign.py`：常量/材料29–55，
  geometry_layers86–104，stack_text118–176，resample/scale_held_out_rows225–257，
  case_nodes260–262，case_provenance270–298，prepare_golden411–465，
  convert_transient534–550。
- 同提交：`experiments/package_thermal/phase2/fit_phase2_rom.py`；
  `plugins/package_thermal/offline/fit_era_model.py` 233–275：先构建矩阵后算held-out误差。
- 当前仓库：`docs/49-eval-audit/review-evidence/20260908/thermal-audit.md`，
  `thermal-golden-check.json`，`thermal-rom-eigencheck.json`。
- 当前恢复范围：`REFERENCE_RECOVERY.md`；新的数值结果单列`P2_NUMERICAL.md`。

证据级别：上述源码事实 DOC_DERIVED；对新封装的重建建议 INFERRED/方法判断；
四种八-stack与DASH映射及不得覆盖基线为 USER_CONFIRMED。
