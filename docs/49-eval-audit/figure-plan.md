# Figure contracts and preview QA

所有图使用同一 `render_figures.py` 中 plotting functions；CSV/JSON 决定坐标、series、数值、provenance。mock generator 完全独立于 renderer；正式运行只替换输入和 manifest，不改 plotting code。每 panel 是冻结切片，其余矩阵维度留在 CSV/exporter 或明确独立 series，禁止暗中平均不同 profiles。全部结果是 long-form metrics，parity 通过相同 cell/run 精确关联，不以两个排序列表 zip。

## Six figures

| Figure | EQ / question | Family / panels | Data grain and minimum | Output |
|---|---|---|---|---|
| fig-e1-hardware-fidelity | EQ1，observable delay/flash service 是否对齐 | Relationship + benchmark；GPU y=x；SSD size P50、QD P99/rate/IOPS；三臂 delta parity | GPU7 D ×10 reps/固定warp-occupancy；storage8 size、8 QD；三臂≥8 paired cells | PNG/PDF/SVG，3×2 panels |
| fig-e2-async-semantics | EQ2，等待是否只在必要时支付 | Lines + oracle；consume residual、issue+consume total、new residual parity | 7 W/D、5 D、3模式、4 op（cp.async条件）；markers 区分四 op | PNG/PDF/SVG，3×1 |
| fig-e3-hbf-feasibility | EQ3，rho×service 的边界 | Matrix + contour；6个 tR 切片 | 每 panel6 rho×6N，paired service metric；不完整网格拒绝 | PNG/PDF/SVG，3×2 |
| fig-e4-expert-union | EQ4，路由与并发 | Lines：union、entropy、Jaccard | 8 active B；real/shuffled/null；所有 E/k 来自输入 | PNG/PDF/SVG，3×1 |
| fig-e5-workload-boundary | EQ4，sparsity/prefetch/concurrency | Lines：MoE、capacity-dense、compute-dense、concurrency service boundary | 6rho×3策略；8B×3策略；其它变量冻结 | PNG/PDF/SVG，2×2 |
| fig-e6-robustness | EQ3/4 supporting appendix | Lines：参数敏感性、byte coverage、extra traffic | 6tR×3 assumption；8B 的coverage/traffic | PNG/PDF/SVG，3×1 |

图3纵轴必须来自 `service_gbs` 同 cell 结果；不是 renderer 从 N/tR 推断出的“测量”。color 是 `decode_norm`，等高线1.05/1.10/1.20仅在数据范围内存在时绘制，不会为了好看伪造不存在的 crossing。所有 hypothetical HBF panels 自动注明参数为假设；formal legend/标题保留 PROJECTED。配置超出已校准 validity domain 时 exporter 必须标 PROJECTED，renderer 不负责科学判定。

图2特别保留消费 residual 与总 exposed stall 的区别：old issue-stall 的 consume residual 可以≈0，总 stall/D 才可能≈1。图2(a)(b)默认展示 tma_g2s、D=5us 切片；完整四 op 与 D 在(c)/结果矩阵；预期趋势不是实验结论。cp.async 暂为 conditional/mock，正式数据不能补一个不存在的实测点。

## Visual specification

- 静态科研 artifact，白底、DejaVu Sans、弱网格、线宽一致。单图series≤5根颜色，marker/空心/虚线同时区分，不靠颜色独立表达语义。
- GPU delay/parity 线性轴，QD/B/size/rho 用对数显示并保留单位；latency us，storage尺寸KiB，service GB/s（十进制）。百分数等高线明确是 normalized decode slowdown。
- 主图是描述性标题，不在 mock 上写“正确/提高/可行”的结论。mock 顶部与中央自动 `MOCK DATA — NOT MEASURED`，不可通过省略 `--watermark` 去掉。
- 误差条按 input replicate bootstrap；MOCK CI 只验证布局。single replicate 不编造 CI，说明无 uncertainty estimate。真实按 run/prompt 做预聚合后输入，不能把层或请求伪装为独立 run。
- 当前是一组可审阅的完整图版，不宣称25个panel已经排进旧doc47的2页预算。论文排版时6图各挑主切片，其余同名图版进appendix；本轮不修改 private paper submodule。

## Preview and replacement

[图1](../../figures/mock/fig-e1-hardware-fidelity.png) · [图2](../../figures/mock/fig-e2-async-semantics.png) · [图3](../../figures/mock/fig-e3-hbf-feasibility.png) · [图4](../../figures/mock/fig-e4-expert-union.png) · [图5](../../figures/mock/fig-e5-workload-boundary.png) · [图6](../../figures/mock/fig-e6-robustness.png)。

完整矩阵与论文切片分离：exporter 选择 figure/panel/series，保留其它条件。改变模型、真实 E/k、profile、replicate 数、延迟值，只改变数据。图外辅助指标（checksum、coverage、bytes、queue等）同 CSV 可以不画，但不得丢失 provenance。renderer 明确报错：缺必需 panel、缺 x/y sibling metric、重复 cell、混入不同 frozen context、缺失 heatmap 格；不能默默丢行后输出“完整”论文图。

当前预览检查：六张 PNG 已查看，坐标单位/marker/图例/水印和5/10/20%轮廓可读，PDF与SVG由同一figure导出。预览数量不足/过密是布局输入问题，不是实验发现。正式 renderer manifest 保存输入和代码hash、依赖版本、各panel点数与最小replicate数。
