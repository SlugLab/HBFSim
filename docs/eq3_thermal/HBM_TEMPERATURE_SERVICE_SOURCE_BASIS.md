# HBM 温度、刷新、服务占用与功耗：来源基础和最小候选模型

日期：2026-09-20  
状态：`READ_ONLY_SOURCE_BASIS`；未启用模型、未运行实验、未读取 blind 数据。

## 目的与结论边界

本文件为“以稳定读取速率为温控目标”的最小 HBM 服务模型整理证据。用户已明确
允许在缺失参数时采用有权威资料支撑的合理假设；这一授权不把代理资料变成目标
Micron HBM4 的标定数据，也不批准把 ECC 错误概率从温度凭空生成。

当前可以建立一个默认关闭的 `PARAMETRIC_HBM_SERVICE_PROXY`：用实际 stack 温度
选择刷新倍率，用刷新占用降低可提供读取带宽，再判断目标读取速率能否稳定维持。
直接可用的定量时序来自 AMD 集成 HBM2 平台，不是 Micron HBM4 12H。Micron 当前
公开 HBM4 产品页没有给出 `tREFI`、`tRFC`、刷新能量或温度分档。因此 HBM4 的
绝对刷新功耗、产品级服务占用和 >95 °C 行为仍为 `UNKNOWN_BLOCKING`。

## 证据分层

| 来源 | 器件/范围 | 可用事实 | 本项目分类与限制 |
|---|---|---|---|
| AMD AXI HBM Controller PG276，[Raw Throughput Evaluation](https://docs.amd.com/r/en-US/pg276-axi-hbm/Raw-Throughput-Evaluation) | 集成 HBM2，4H/8H | 基础 `tREFI=3.9 us`；4H `tRFC=260 ns`，8H `tRFC=350 ns`；0–85 °C 使用基础间隔，85–95 °C 使用 `1.95 us`；资料给出的峰值效率损失约 7%/9% | `HBM_DIRECT`，但代际、堆叠高度和控制器均不等于目标 HBM4；只能作时序/占用代理 |
| AMD PG276，[Refresh options](https://docs.amd.com/r/en-US/pg276-axi-hbm/Reorder-Refresh-and-Power-Savings-Options-Tab) | 同上 | 支持 single-bank refresh、lookahead、基于温度调整刷新周期、读写 holdoff 和温控 self-refresh | `HBM_DIRECT`；证明可见占用依赖刷新粒度和调度，不能把 `tRFC/tREFI` 当作所有控制器的精确吞吐损失 |
| AMD PG276，[Features](https://docs.amd.com/r/en-US/pg276-axi-hbm/Features) | 同上 | 温控刷新、可选隐藏 single-row refresh；SECDED ECC、scrub、parity/retry 是分别可选的功能 | `HBM_DIRECT`；只证明能力存在，不给 ECC 错误率或温度曲线 |
| AMD PG276，[HBM configuration](https://docs.amd.com/r/en-US/pg276-axi-hbm/HBM-Configuration-Selection-Tab) 与 [register map](https://docs.amd.com/r/en-US/pg276-axi-hbm/Memory-Controller-Register-Map) | 同上 | stack 温度轮询周期可配置；可读刷新命令、self-refresh cycles、带宽和 ECC 计数 | `HBM_DIRECT`；可作为以后实机校准观测量，目前没有相应观测 |
| AMD DS923，[Recommended Operating Conditions](https://docs.amd.com/r/en-US/ds923-virtex-ultrascale-plus/Recommended-Operating-Conditions)；AMD XAPP1377，[thermal targets](https://docs.amd.com/r/en-US/xapp1377-heatsinks-thermal/Obtaining-Thermal-and-Power-Targets-to-use-with-Thermal-Simulation) | 特定 AMD 集成 HBM 产品 | 建议连续 HBM 温度上限 95 °C；特定 -2LE 产品允许有限 95–105 °C excursion；高温刷新会影响带宽 | `HBM_PLATFORM_DIRECT`；是平台级运行边界，不是 Micron HBM4 产品温限。DS923 对 >95 °C 的倍率措辞不能无条件移植 |
| Micron，[HBM4 product page](https://www.micron.com/products/memory/hbm/hbm4) | 目标代际，公开产品资料 | 36 GB 12H、2048-bit、超过 11 Gb/s、每 stack 超过 2.8 TB/s；相近速度下相对 HBM3E 能效改善 | `HBM4_DIRECT`；未公开刷新时序、温度分档、绝对功耗或 rail 分账。公开峰值带宽不能直接当持续媒体读取带宽 |
| Micron，[DDR5 New Features](https://www.micron.com/content/dam/micron/global/public/products/white-paper/ddr5-new-features-white-paper.pdf) | DDR5 代理 | all-bank refresh 期间目标 banks 不能读写；示例 16 Gb DDR5 的 `tREFI=3.9 us`、`tRFC=295 ns`；same-bank refresh 可让其他 banks 工作 | `DDR_PROXY`；只支持刷新占用语义和量级检查，不能提供 HBM4 参数 |
| Micron，[ECC Brings Reliability and Power Efficiency to Mobile Devices](https://www.micron.com/content/dam/micron/global/public/products/white-paper/ecc-for-mobile-devices-white-paper.pdf) | LPDDR4 代理 | 85–95 °C 使用 2 倍刷新，95–105 °C 使用 4 倍刷新；资料称 8 Gb LPDDR4 在最高温档 all-bank refresh 占用超过 18% | `LPDDR_PROXY`；不能作为 HBM4 的精确倍率、ECC 能力或错误率 |
| 本地 `MICRONHBM4`、HBM3E/H200 证据账本 | 公开产品与平台代理 | H200/HBM3E 聚合 memory-domain 活跃读取能量约 45.815–46.941 pJ/delivered byte | `PLATFORM_ENERGY_PROXY`；不是 refresh energy，不是 per-stack，不可拆成 HBM4 array/base/PHY |
| OCP HBF 0.7.0，本地原件 SHA-256 `307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3` | HBF/NAND | 典型 24–48 h 周期维护；同 die 读/refresh 互斥 | `HBF_ONLY`；它是 NAND 数据维护，不是 DRAM 周期刷新，不参与本 HBM 模型 |

网页资料访问日期为 2026-09-20。本地注册来源和限制见
`configs/eq3_thermal/sources.json`、
`configs/eq3_thermal/research/source_evidence.json`、
`docs/eq3_thermal/PARAMETER_GAPS.md`。

## 可由直接 HBM2 证据复算的服务占用

对于 all-bank、刷新期间不提供读取服务、且不隐藏/重叠刷新的简化情景：

```text
m(T)       = 1, T <= 85 degC
             2, 85 degC < T <= 95 degC
tREFI(T)   = 3.9 us / m(T)
u_refresh  = min(1, tRFC / tREFI(T))
B_refresh  = B_no_refresh * (1 - u_refresh)
```

由 PG276 数值直接推导：

| HBM2 代理 | 温档 | 倍率 | `u_refresh` | 理想剩余服务比例 |
|---|---:|---:|---:|---:|
| 4H, `tRFC=260 ns` | 0–85 °C | 1x | 6.667% | 93.333% |
| 4H, `tRFC=260 ns` | 85–95 °C | 2x | 13.333% | 86.667% |
| 8H, `tRFC=350 ns` | 0–85 °C | 1x | 8.974% | 91.026% |
| 8H, `tRFC=350 ns` | 85–95 °C | 2x | 17.949% | 82.051% |

这些值与 PG276 报告的常温约 7%/9% 峰值效率损失相符。它们是无重叠
all-bank 上界式占用；single-bank、hidden refresh、lookahead、访问局部性和排队
可能降低或改变 host 可见损失。不得把 4H/8H 的 `tRFC` 按 die 数线性外推为 12H。

## 建议的最小候选模型

### 1. 主域：0–95 °C

候选名称：`HBM2_REFRESH_SERVICE_PROXY_FOR_HBM4`，默认关闭。

- 温度输入必须来自被建模 stack 的传感/热节点，不从地址、请求种类或 ECC 事件猜测。
- 用上式的 `m(T)` 和两个显式代理档分别评估：`tRFC=260 ns`
  (`HBM2_4H_PROXY`) 与 `350 ns` (`HBM2_8H_PROXY`)。它们是两个来源明确的情景，
  不是 HBM4 12H 的置信区间；8H 档仍可能低估 12H 服务占用。
- 读取速率可行性条件为：

```text
R_target <= eta_access * B_raw * (1 - u_refresh)
```

  `eta_access` 单独表示地址映射、bank conflicts、协议和请求尺寸造成的效率；必须由
  真实后端或明确工程情景给出。不可把 refresh 损失同时包含在 `eta_access` 和
  `u_refresh` 中。`B_raw` 使用目标配置值时仍保留其来源分类；Micron 的
  `>2.8 TB/s` 是产品页下限描述，不等于任意负载的持续读取率。
- 控制器应以稳定维持 `R_target` 为目标：若温度跨入 2x 档后右侧容量不足，则必须
  报告 `READ_RATE_INFEASIBLE_AT_REFRESH_GRADE`，而不是通过遗漏刷新、把排队当完成或
  虚构 ECC 收益维持目标。
- 阈值附近确定性需要使用同一时间戳的既定温度采样顺序。传感器误差、轮询间隔和
  温升时延尚无目标平台数值，因此 guard band/hysteresis 大小保持 `UNKNOWN`；它们
  不能被随意固定后称为产品阈值。

### 2. 95–105 °C 只作越域/敏感性诊断

AMD 平台将 95 °C 设为连续运行上限，并只对特定产品允许有限 excursion。Micron
LPDDR4 代理在 95–105 °C 使用 4x nominal；DS923 对特定 HBM 平台的文字要求是
高于 95 °C 时刷新率至少为“95 °C 时刷新率”的 4 倍。与 PG276 的 95 °C 档组合时，
后者可能被读作至少 8x nominal。器件、版本和措辞存在歧义。

因此主候选模型在 `T>95 °C` 返回 `OUT_OF_PRIMARY_DOMAIN`，不输出确定的 HBM4
服务率。若仅为安全敏感性诊断，可显式计算 4x 与 8x nominal 两个端点：

| HBM2 代理 | 4x nominal 占用 | 8x nominal 占用 |
|---|---:|---:|
| 4H, 260 ns | 26.667% | 53.333% |
| 8H, 350 ns | 35.897% | 71.795% |

该表只能标为 `EXCURSION_DIAGNOSTIC_ONLY`；不得用于声明 Micron HBM4 95–105 °C
产品行为或安全运行能力。

### 3. 调度粒度

最小实现可先采用每 stack 单服务域的保守 all-bank 占用，输出：

```text
stack, temperature_C, refresh_grade, source_profile,
tREFI_ns, tRFC_ns, refresh_occupancy, available_read_Bps,
target_read_Bps, target_feasible, evidence_class
```

若以后接入真实 controller counters，应该以 pseudo-channel/bank 可见刷新命令、
self-refresh cycles 和实际读取带宽校准 host-visible loss。校准后才能替换保守占用；
不能仅因为硬件支持 single-bank refresh 就假设刷新完全隐藏。

## 功耗与热反馈

当前资料支持“刷新频率升高会增加功耗并减少服务时间”的方向，但没有目标 HBM4
每次刷新能量。物理形式可以先冻结为：

```text
P_total = P_idle(T) + P_read(R,T) + P_refresh(T)
P_refresh(T) = N_refresh_domains * E_REF(T) / tREFI(T)
```

其中 `E_REF(T)`、实际 refresh domain 数、rail 归属和与读操作的能量重叠均为
`UNKNOWN_BLOCKING`。因此：

- 服务占用模型可以在 energy 为 `UNKNOWN` 时独立运行并报告读取率可行性；
- thermal 输入不能把 refresh energy 静默设成 0；
- H200/HBM3E 的 45.815–46.941 pJ/delivered-byte 只可作为聚合活跃读取代理，不能
  代替 `E_REF`，也不能同时记入 array、base 和 PHY；
- 若为了工程闭环必须提供功耗敏感性，只能另设显式无量纲
  `refresh_busy_power_ratio` 情景，并绑定已测 rail 的 idle/active 差值；没有目标测量
  前不推荐给出伪精确数值区间，也不得将该情景用于正式温度或能效结论；
- 最小后续实测需要同一平台的 idle、固定读取率和已知刷新档，记录 stack 温度、
  refresh command/self-refresh counters、delivered bytes 与同一 rail 能量。差分要保留
  controller/PHY/DRAM rail 覆盖范围。

[Micron DRAM power calculator](https://www.micron.com/sales-support/design-tools/dram-power-calculator)
是官方功耗估算入口，但当前公开页面没有提供本目标 HBM4 的可引用刷新 IDD/能量
参数，不能据此补数字。

## ECC、scrub 与可靠性限制

PG276 证明特定 HBM2 controller 可选 SECDED ECC、background scrub 和 parity retry；
Micron LPDDR4 白皮书讨论了 ECC 与高温刷新之间的可能关系。这些事实均不提供目标
HBM4 的下列数据：

- 温度到 raw/corrected/uncorrectable bit-error probability 的曲线；
- scrub 的实际周期、服务占用和能量；
- retry 概率、额外延迟和故障分布；
- 使用 ECC 后可安全降低目标产品刷新率的授权。

故当前模型不得生成 `P(error|T)`、ECC correction 数、retry 或寿命收益。若硬件真实
计数可用，只能把观测到的计数作为事实记录；在没有统计暴露量和校准模型时，也不能
反向解释为温度因果概率。当前 `configs/eq3_thermal/reliability.json` 不消费 ECC/RBER
曲线，这一状态应保持明确。

## 能力状态和最小补证顺序

| 项目 | 当前状态 | 可支持结论 |
|---|---|---|
| 温度触发 1x/2x 刷新语义 | `AVAILABLE_HBM2_DIRECT` | HBM2 代理情景；不能称 HBM4 产品参数 |
| all-bank 服务占用 | `DERIVED_HBM2_PROXY` | 4H/8H 两个显式情景下的读取容量损失 |
| single-bank/hidden refresh | `CAPABILITY_KNOWN_BEHAVIOR_UNCALIBRATED` | 说明 all-bank 模型可能保守；不能设为零损失 |
| HBM4 12H `tRFC/tREFI` | `UNKNOWN_BLOCKING` | 不支持产品级延迟、占用或温档声明 |
| HBM4 refresh energy/power | `UNKNOWN_BLOCKING` | 不支持绝对热反馈和 refresh 能效结论 |
| stack 温度与 refresh counters | `OBSERVABLE_ON_AMD_HBM2_PLATFORM` | 给出未来校准方案；当前没有目标观测 |
| ECC/error/retry 温度模型 | `UNAVAILABLE` | 不生成概率、错误、retry 或寿命收益 |
| >95 °C | `OUT_OF_PRIMARY_DOMAIN` | 只可做 4x/8x excursion 敏感性，不可作 HBM4 正式结果 |

补证优先级为：目标 HBM4 part datasheet/JESD270-4 对应时序与温档；目标 controller
刷新粒度和调度；同平台 counters 与 rail 能量；传感器误差和轮询延迟。获得前，最小
代理应保持默认关闭、参数显式、来源标签随输出保存，并把所有正式 HBM4 温度—服务
结论标为 `CONDITIONAL_SIMULATED`。
