# HBF 评估类论文的评估方法先例简报

供 HBFSim 论文评估章设计使用。五篇论文加一份核实文档,每篇按 (a) 方法 / (b) HBF 参数 / (c) 负载与指标 / (d) 模型验证 / (e) dense 与 MoE / (f) 未覆盖 六项报告。标注规则:**一手核实** = 本轮亲手读了本地 .txt 原文并给出行号;**转述** = 来自本项目 README 或文档 44 的登记,本轮未独立复核。

---

## 1. FlashAccel(我们的对比对象)

来源:`/root/hbfsim/HBFSim/docs/ref_article/wang2026-flashaccel-hbf-llm-inference.txt`(arXiv:2607.10186v1,中科院计算所)。全文一手核实(正文部分,第 1–1617 行;末尾参考文献未逐条读)。

**(a) 方法:事件驱动模拟,无真实执行。** 原文(第 1324–1332 行):`We build an event-driven simulator on top of LLMCompass [69] to model the latency of LLM inference... LLMCompass searches tiling strategies to find efficient GEMM mappings on GPU hardware and uses ScaleSim [45] to estimate latency. We extend it with a NAND simulator that models page access latency at plane granularity.` 一手核实。

**(b) HBF 参数**(Table 2,第 1253–1318 行,一手核实):
- 介质:`SLC 96 wordline-layers, 4KB / page`,`256 pages / 256 blocks / 96 planes, 192 Gb / die`;每 plane 容量 256MB。
- 时序:`tR = 4 μs, tProg = 75 μs`,声明沿用 XL-Flash([32])的值,即使每 plane 容量缩到 1/4 也不改,自称保守(第 1290–1294 行)。挑战一节引用同一来源写 tR 约 4 μs 对 HBM 约 100 ns,40 倍差距(第 385–393 行)。
- 堆叠:`8 × Flash Die + 1 × Base Die @ 768GB/s`,每堆 flash 192GB、SRAM 32MB;base die SRAM 8MB,每 plane 32KB SRAM。
- 系统:CSI = 6 HBF 堆 + 6 缩薄的 HBM 堆;CLI = 用 HBF 换掉 6 堆 HBM 里的 5 堆。CSI 读带宽 4.6 TB/s,**峰值写带宽只有 245.8 GB/s**(第 1459 行)。
- 读出单元推算:匹配 H200 的 4.8 TB/s 需要 4,916 个 plane 并发读;hyper page 约 19MB(第 466–479 行)。

**(c) 负载与指标**(Table 1 与 7.1 节,一手核实):四个模型——Qwen3-235B(MoE/GQA,188KB/token)、Qwen3-Coder-480B(MoE/GQA,248KB/token)、LLaMA3.1-405B(**Dense**/GQA,502KB/token)、DeepseekV3-671B(MoE/MLA,70KB/token);FP16(DeepSeek 权重 FP8)。两组序列长度:8.11K/2.53K(LongProc 均值)与 15K/6K(agent 场景)。基线 8×H200(每卡 141GB、4.8 TB/s)。指标:两档 decode SLO(50ms、100ms)下取最大可行 batch 的每 GPU 吞吐(归一化)、消融延迟分解、KV 命中率、tokens/J。头条数字:2.54× 吞吐、1.93× 能效(100ms SLO);消融:关 prefetch/权重布局/KV 布局分别损失 55%/7%/15%,全关损失 65%(第 1420–1434 行)。

**(d) 模型验证:没有。** 全文 grep `validat|calibrat` 零命中(本轮命令核实)。没有任何模拟器对真机、对器件的精度验证;器件不存在,基线 H200 也是模拟出来的,不是跑出来的。一手核实。

**(e) dense vs MoE:没有正面主张。** 相关的间接观察(一手核实):4 卡配置在 50ms SLO 下,`For LLaMA3.1-405B and Qwen3-Coder-480B, loading weights alone takes close to 50ms`,这两个模型无法服务(第 1388–1392 行)——权重体量效应,不是按 dense/MoE 框架讲的;16 卡时 EP 的 all-to-all 通信因 RDMA 带宽降约 9× 而变贵(第 1399–1402 行)——MoE 特有代价。逐模型柱状数字在图里,文本抽取不出来。

**(f) 未覆盖:** `thermal`、`temperature` 全文零命中(本轮 grep 核实;README 登记一致)。无真实执行。耐久度论证靠引用(retention 从 3 年放宽到 3 天可延长 P/E 至多 50×,自己保守取 10×,100K→1M),retention 与温度的关系完全没建模——这正是我们热模型抓住的耦合(README 登记,转述;引用出处一手核实于第 1460–1479 行)。写入侧只算了一笔总账:每 GPU 每秒最多写 988MB KV、开销 4%、五年 TBW 够用。

---

## 2. TileLens

来源:`/root/hbfsim/HBFSim/docs/ref_article/ju2026-tilelens-two-dimensional-memory-layout.txt`(arXiv:2607.04031,Georgia Tech)。全文一手核实。

**(a) 方法:真机采 trace,离线周期级模拟。** 原文(第 1343–1345、1409 行):`We use Macsim [16], a cycle-level GPU simulator, extended with DRAM, RoMe, and HBF memory models. Kernel traces are collected from an NVIDIA H200 GPU using a SASS-level tracer built on NVBit [47].` 时序在模拟器里算,从不回灌真机。一手核实。

**(b) HBF 参数**(6.1 节,第 1376–1403 行,一手核实):
- 带宽:6 堆 HBM3e 合计 4.915 TB/s;`We assume HBF also has 16 channels per stack with the same total bandwidth, but with 4 KB granularity`——HBF 带宽直接假设等于 HBM 总带宽。内部带宽设为总线的 2.5× 以模拟 plane 并发。
- tR:**不取单值,扫描**——`we sweep the NAND page read latency across 1, 2, 5, 10, and 20 µs to cover the range of current and projected flash technologies`。头条结果在 5 μs 点报告。
- 背景引述(第 338–344 行):HBF `∼1.6 TB/s per stack for Gen1`、`8–16× the capacity per stack`、读延迟 `1–10 μs`。
- 结构:daisy-chain 跟随 H3,可选 HBM base die 上 40MB SRAM 缓冲;不给堆叠层数、plane 数——它声明不站队任何具体 LGMS 技术(第 551–553 行)。

**(c) 负载与指标**(一手核实):只有 matmul kernel,不是端到端服务——Qwen-3 30B(MoE,`fused_moe`,128×256 BF16 tile)与 Llama-3.1 70B(dense FFN,64×128 tile),batch 16/64/256。指标:归一化到 HBM-only 的 kernel 执行时间、有效带宽、stall 分解、单请求延迟分布与 CDF、对 tR 和 tile 形状的敏感度、RoMe 对照。头条:传统布局 geomean 慢 1.61–6.49×,tile-major + 自适应预取在 5 μs 下回到 HBM-only 的 1% 之内。

**(d) 模型验证:没有精度验证。** grep `validat|calibrat` 零命中(本轮命令核实)。做的是配置对齐(模拟器按 H200 的 132 SM、cache 大小配置)+ 真机 trace,不是时序对真机的校验。TileLens-HW 自身的 5–7 周期开销论证为可忽略,未建模(第 1362–1369 行)。

**(e) dense vs MoE:有一句直接主张 + 一组可引数字。** 一手核实:
- 第 1356–1359 行:`All configurations remain memory-bound for Qwen due to MoE routing, whereas Llama transitions to compute-bound at a batch size of 256.` 这是这批论文里最明确的一句 dense/MoE 行为差异。
- Qwen fused_moe(128×256 FP16 tile,5 μs HBF):column-major 读放大 10.1×、stall 周期 14.8×、执行时间 11.2×;row-major 放大 3.9×、慢 3.3×(第 534–540 行)。
- 敏感度:20 μs 时连 tile-major 也慢到 3–4×(第 1734–1742 行)。

**(f) 未覆盖:** `thermal`/`temperature` 零命中(本轮 grep)。耐久度只用放置策略回避(权重进 HBF,KV 与激活只放 HBM,第 1249–1255 行),不建写模型。只测 kernel,不测端到端;无容量实验;无真实执行时序(trace 回放)。

---

## 3. HAVEN

来源:`/root/hbfsim/HBFSim/docs/ref_article/hsu2026-haven-hbf-vector-search.txt`(arXiv:2603.01175v1,Georgia Tech / EPFL / UCSD)。全文一手核实。

**(a) 方法:器件级模型 + 真机基线的混合。** HBF 侧:用 3D-FPIM + NeuroSim 对**自己重新设计**的分布式 3D NAND subarray 做投影(第 546–548 行);近存检索单元 Verilog 实现、22nm 综合(1GHz,4.11 mm²,620.3 mW)。基线侧是真机实测:AMD EPYC 7302、512GB DDR4-3200、A100 40GB、8TB PCIe 4.0 NVMe SSD,Faiss + cuVS(第 595–668 行)。HBF 的性能数字是模型算的,DRAM/SSD 的是量的。一手核实。

**(b) HBF 参数**(一手核实):
- 选定配置:`We select a 4 KB page size, 64 blocks per subarray, and 256 WL layers as the baseline HBF configuration`(第 537–539 行);SLC(Table 1 `No. of Bit: 1 (SLC)`);8-Hi 堆叠;512GB/8-Hi 需至少 128 WL 层(第 452–453 行)。
- 带宽:`we assume the maximum bandwidth of 460 GB/s per stack`(与 HBM2E 基础设施兼容,第 530–532 行);功率包络 `an HBM-like 30 W power envelope per stack`(第 523–524 行)。
- 能耗:`we calibrate read energy using industry-grade 3D NAND data, scaling from the ∼30 pJ/bit read energy reported for contemporary devices`(第 517 行)。
- tR:没有以单个数字印在正文,读延迟只在图 8(d) 的曲线里。**注意 README 的警告(转述):项目内流传过的「HAVEN 给 2–16 pJ/bit」在全文检索里不存在,只有约 30 pJ/bit 这一个校准基线;未看 PDF 图轴之前不许引 2–16。**
- 容量引述:`HBF can deliver eight to sixteen times the capacity of HBM within a similar footprint`(第 295–297 行,引industry projections)。

**(c) 负载与指标**(一手核实):IVF-PQ 向量检索(RAG 的检索部件),三个数据集:BIGANN-1B(1B 条 ×128 维,119GB)、SPACEV-1B(1B×100 维,93GB)、Wiki-88M(88M×768 维,252GB);Faiss 16 字节 PQ 码。指标:固定 recall(0.95/0.95/0.9)下的 QPS 与查询延迟,batch 4–32;对 GPU-DRAM、GPU-SSD 两条基线;另有与 ANNA、SmartANNS 加速器的对表(HAVEN 8.1k QPS,1.9×/9×)。头条:重排吞吐至多 20×、延迟至多 40×。

**(d) 模型验证:只有能耗校准与设计空间自检。** 能耗模型「calibrated by industry-grade performance」(第 548 行);「To validate our chosen subarray configuration ... we perform a design space exploration」(第 849 行)——这是验证设计选点,不是验证模型对真件的精度。HBF 性能数字没有任何实测对照。一手核实。

**(e) dense vs MoE:无。** 不是 LLM 推理论文,没有任何相关主张。

**(f) 未覆盖:** 自己不做热仿真——`thermal` 两处命中都只是 30W 包络约束和引用别人对 HBM-GPU 模块的热分析(本轮 grep + README 边界 2,一致);无写入/耐久度(读密集负载);HBF 内部结构是自己的重设计、不是 OCP 规范(README 边界 1,转述);无真实执行注入。

---

## 4. Micron HotInfra'26《Is High-Bandwidth Flash All You Need?》

来源:`/root/hbfsim/HBFSim/docs/ref_article/micron2026-is-hbf-all-you-need.txt`(HotInfra'26,Micron)。全文一手核实。

**(a) 方法:纯解析 roofline,一行代码不跑。** 原文(第 144 行):`The analytically modeled platform (no silicon measurement) is a 72-GPU rack...`;第 182–189 行:roofline 取每算子 compute-bound 与 memory-bound 上界的较大者,扩展成 HBM、D2D、C2C 三路并行的 step model,solver 枚举并行策略取满足 TPOT SLO 的最大 tok/s/kW。一手核实。

**(b) HBF 参数**(Table 1,第 92–130 行,一手核实,每 GPU):`HBF: Cap. 4,096 GiB; BW 8.0 TB/s (rd); Latency 20,000 ns; Energy 8.5 pJ/bit`。补充:`HBF latency matches H3's SLC-NAND assumption`(第 166–168 行);散乱访问下有效带宽系数 `η ≈ 0.36`(2.9 TB/s),原因是 `NAND's ∼20 µs read latency on small fetches`(第 211–213 行);HBF tier 峰值功率 `549 W per GPU`,对比 LPDDR 2 TB/s 时 95 W(第 457–458 行)。对照:HBM 512 GiB、32 TB/s、100ns、3.0 pJ/bit。不给堆叠数/plane 数——它建模的是 tier,不是器件内部。

**(c) 负载与指标**(一手核实):四个 **全部是 MoE** 的模型在 1M token 上下文:DSV4-Pro(1.6T,FP8 权重 1.43 TiB)、DSV4-Flash(284B)、Kimi-K2.6(1T,MLA,约 39 KB/tok KV)、Nemotron-3-Ultra(549B/55B,Mamba-2+GQA 混合)。指标单一:tok/s/kW @ TPOT SLO(10ms/25ms),batch 4–256,画 Pareto 前沿。头条:片上 LPDDR 处处不输,至多 5.6× 于 HBM-only;KV 重的 Kimi 上 LPDDR 比 HBF 好 2.1×。

**(d) 模型验证:无,只有参数敏感性。** 唯一的稳健性论证是 η 扫描:`Raising HBF scattered η from 0.36 to 0.50 leaves every LPDDR-over-HBF ratio byte-identical... even pairing an optimistic HBF (0.50) with a pessimistic LPDDR (0.65) flips no cell`(第 375–384 行)。另有免责声明:基于解析模型与公开信息的早期结果(第 30–34 行)。一手核实。

**(e) dense vs MoE:不比 dense,但给出 MoE 特有的关键结论。** 一手核实:
- `Under the production-default weights-first allocator, HBM+HBF reduces to HBM-only within 0.4% on every operating point... Only expert-offload activates HBF.`(第 450–454 行)——**HBF 只有在 MoE 专家卸载策略下才被用到**,这是对我们 dense/MoE 洞察最直接的旁证。
- KV 重与权重重的 MoE 变体行为不同:writability 差距在 Kimi-K2.6(MLA,KV 重)最大(2.1×),在 KV 装得进 HBM 的另外三个模型上收窄或消失(第 407–423 行)。
- 它自己声明不评估的、恰好利于 HBF 的两个场景:`prefix-cache reuse under tight TTFT constraints, and decode-phase expert streaming when weights exceed LPDDR's on-package capacity`(第 442–449 行)。

**(f) 未覆盖:** `thermal`/`temperature` 零命中(本轮 grep;有功率、无温度);无真实执行;写入侧直接硬性禁止(`the solver hard-rejects any placement that routes KV to it... no NAND program/erase traffic enters the decode model`,第 198–206 行),即写入与耐久度不建模只回避;只算 decode、单步、固定 KV 状态,不算 prefill;5 页 workshop 文;Micron 同时卖 DRAM 与 NAND,立场非中立(README 边界,转述)。

---

## 5. 《HBF Sucks!》(li2026,HBF characterization)

来源:`/root/hbfsim/HBFSim/docs/ref_article/li2026-hbf-characterization-kv-cache.txt`(arXiv:2608.11668v2,北大/复旦)。全文一手核实;引用规矩以 `/root/hbfsim/HBFSim/docs/44-HBF-Sucks论文的核实与已发现问题.md` 第六节为准(该文档本轮全文读过,其内部核实等级标记沿用)。

**(a) 方法:离散事件服务模拟 + 外挂器件模型。** 扩展 TokenSim(第 598–604 行),回放四条完整两小时阿里云 Qwen-Bailian 生产轨迹;热用 3D-ICE 对 16-Hi TLC 堆求**稳态**解,与服务模拟不在同一次运行里耦合——`Two findings need device models the serving simulator does not provide.`(第 754 行);耐久度是 TBW 预算账。一手核实。

**(b) HBF 参数**(Table 1/2,第 375–405、691–723 行,一手核实):
- HBF-1(2028):近端层 `12 GDDR7, 48 GB`(1.344 TB/s H100 / 2.688 B200);HBF `6 stacks, 3 TB`;八卡共享 24 TB @1.2 TB/s(H100)/ 48 TB @2.4(B200)。
- HBF-2(2030):近端层 `3 HBM, 48 GB`;HBF `3 stacks, 1.5 TB`;共享 12 TB @0.6(H100)/ 24 TB @1.2(B200)。
- 时序:读/写延迟**扫描** `8/80, 12/120, 20/200, 30/300 μs`(第 728–730 行);声明 `HBF remains projected hardware, so its service parameters are sourced or swept, not claimed as measured silicon.`(第 603–604 行)。
- 堆叠:16-Hi、128 层 3D-NAND **TLC**(与 FlashAccel/HAVEN 的 SLC 假设不同);热限:80°C 上限在单堆 202.27 GB/s、53.72 W 处被撞到(第 2615–2616 行);文档 44 推算这隐含约 33.2 pJ/bit,与 Micron 表的 8.5 pJ/bit 差 3.9 倍。

**(c) 负载与指标**(一手核实):五个模型——Qwen3-4B、Qwen3-32B(**dense**),DeepSeek-V3.2-685B/37B、GLM-5.2-753B/40B、Kimi-K2.7-Code-1.1T/32B(**MoE**);八卡 H100 与 B200,TP8 + MoE 用 EP;SSD 基线 KIOXIA CM7-V;负载 0.05–32 QPS。指标:TTFT、TBT、端到端延迟、吞吐、SLO goodput、逐层读写字节、写读比、前缀命中率。头条:端到端延迟升 2–5.5×、goodput 降 1.1–2.7×;介质延迟改善 3.75× 只动端到端 <1%(反解出暴露 I/O 占比约 1%)。

**(d) 模型验证:这批论文里做得最规范的一篇,但仍有文档 44 列的缺口。** TokenSim 原始论文对真机配置验证过服务行为(引用),扩展部分列了回归检查(块/字节守恒、存取顺序、确定性回放、逐层计数器);每个数值标为测量/有出处/建模三类之一(第 745–747 行)。文档 44 已核出的问题(引用时必须带上):头条 2–5.5× **不是单变量对照**(论文自己承认,近端层容量与带宽一起砍半);热模型稳态解用在 394–721 ms 窗口上(远短于秒级热时间常数);功耗-带宽只有一条过原点直线,读写系数不可分;耐久度对比两侧口径不对称(厂商 3-DWPD 对写放大=1 的理想假设,0.56× 是两个常数相除);扩展代码不可获取。**唯一可不加限定引用的是 3.75× 介质扫描那一组(单变量)。**

**(e) dense vs MoE:论文不按这个框架讲,但逐模型数字可以按这个轴读。** Table 4(B200,对容量对等 SSD 基线,一手核实,第 1142–1218 行):ΔE2E(HBF-1)Kimi-K2.7 +285.2%、GLM-5.2 +280.0%、DeepSeek-V3.2 +284.7%、Qwen3-32B +267.4%、**Qwen3-4B +184.1%**;ΔTTFT(HBF-1)Kimi +96.6% 对 Qwen3-4B +19.2%。即大 MoE 受害最重、小 dense 最轻。但注意文档 44 缺口三:**MoE 权重的容量账在论文里对不上**(8×48GB=384GB 近端层对 685B/753B/1.1T 参数,若全驻留折合每参数 4.48/4.08/2.79 比特;权重精度与专家驻留策略全文未交代)——引用这些逐模型差异时要带上这个保留。另外逐轨迹的 HBF 驻留率差异大(traceA 27% 对 thinking 4%,第 1036 行附近)。

**(f) 未覆盖:** 无数据保持期模型、无刷新模型(`refresh` 零命中、`Arrhenius` 零命中,文档 44 一手核过);热上限不回灌端到端数字;权重端解析处理、不产生模拟事件;无真实执行。

---

## 综合判断

### 一个读过这批论文的 FAST 审稿人会用什么标准审我们

1. **「没有器件,大家都在模拟」是共识,分界线在模拟的证据等级。** 五篇的方法谱系:纯解析 roofline(Micron)→ 事件驱动模拟(FlashAccel/LLMCompass)→ 真机 trace + 离线周期级模拟(TileLens/Macsim+NVBit)→ 器件物理建模 + 真机基线(HAVEN)→ 离散事件服务模拟 + 生产轨迹(li2026/TokenSim)。**没有一篇做真实执行中的注入,没有一篇对任何实测时序做过精度验证**(FlashAccel 与 TileLens 全文 `validat|calibrat` 零命中,本轮 grep 核实)。这正是 HBFSim 的空位——li2026 自己那句 `Two findings need device models the serving simulator does not provide.` 可以直接当空位陈述引用。但反过来,审稿人会立刻问我们:你的标定验证了什么?这时我们第 5 节弱点 1(六断点零误差是构造出来的、缺交叉验证)会被最先戳到——先例里最规范的 li2026 把每个数标了测量/出处/建模三类,我们至少要做到同等的标注纪律,并补一组未参与拟合的验证点。

2. **参数表 + 出处 + 敏感度扫描是这批论文的通行做法,tR 的分歧本身就是我们要正面处理的事实。** 各家 tR:FlashAccel 4 μs(沿 XL-Flash,SLC);TileLens 扫 1–20 μs、头条报 5 μs;Micron 20 μs(称与 H3 的 SLC-NAND 假设一致);li2026 扫 8–30 μs(TLC);HAVEN 不印单值。**公开文献里 tR 相差 5 倍、介质假设 SLC/TLC 不统一、每比特能耗相差约 4 倍(8/8.5 对 33.2 pJ/bit)。** 审稿人会要求:我们的 profile 落在这条轴的哪里、依据什么、结论对它多敏感。我们弱点 8(三个合成 profile 低于 OCP 规范)在这个背景下必须在投稿前解决或声明。

3. **单变量纪律与限定条件会被对照检查。** li2026 因头条数字非单变量对照已被(包括我们文档 44)抓住;Micron 靠 η 敏感性自保;TileLens 靠延迟扫描自保。我们的 164.70x(弱点 6:介质延迟与请求路径开销未分离)、vLLM 只注册 16 KiB(弱点 4)属于同一类会被抓的混合变量,正文必须自己先说。

4. **写入、耐久度、温度已经从「没人管」变成「有人管了一半」。** FlashAccel 只做 TBW 总账、零温度;Micron 直接禁止 KV 写进 HBF;li2026 做了热与耐久并发现两者都是硬约束——但热是外挂稳态模型、不回灌,且没有 retention/刷新模型。**我们的 MTBF 热模型(温度→保持期限→刷新流量→再发热的闭环)在这批先例里没有任何一篇做过**,这个主张成立;写的时候按文档 44 第五节的顺序:先陈述 li2026 怎么做、它自己怎么声明,再说明外挂稳态模型漏掉什么,最后才说我们怎么做。同时守住文档 44 第三处教训:不要在远小于热时间常数的窗口上用稳态解——这条对我们自己同样生效。

5. **引用 li2026 时严格执行文档 44 第六节的六条硬规矩**(2–5.5× 必带非单变量限定;202.27 GB/s 必带三项限定;0.56× 必说口径不对称;3.75× 扫描可不加限定;不许说成「HBF 不行」;两条第三方未核指控不许下传)。

### dense vs MoE 洞察实验的现成对照点

已有数字里可以直接当对照的,按证据层级排:

- **kernel 层(TileLens,与我们最可比):** 同是 Qwen3-30B(我们 vLLM 用例的模型)、真 H200 trace。可引:`All configurations remain memory-bound for Qwen due to MoE routing, whereas Llama transitions to compute-bound at a batch size of 256`;fused_moe 在 column/row-major 下读放大 10.1×/3.9×、慢 11.2×/3.3×;5 μs 下传统布局 geomean 慢 1.61–6.49×。我们在真实执行里测同一模型的同类 kernel,可以直接对这组数。
- **服务层(li2026):** B200 上对容量对等 SSD 基线,ΔE2E 大 MoE 约 +280–285% 对小 dense +184.1%,ΔTTFT +96.6% 对 +19.2%——大 MoE 受 HBF 化伤害更重的排序。引用时带两条限定:非单变量对照;MoE 权重容量账未交代(文档 44 缺口三)。
- **分配策略层(Micron):** weights-first 下 HBM+HBF 与 HBM-only 差 <0.4%,**只有 MoE 专家卸载才让 HBF 被用到**;KV 重的 MoE(MLA)与权重重的 MoE 行为不同(2.1× writability 差距只在前者出现)。这是「HBF 的价值依赖 MoE 结构」最强的一句先例陈述,来自看衰方(Micron),引用时说明是解析模型、无代码运行、且作者立场非中立。
- **KV 强度轴(FlashAccel Table 1):** 各模型每 token KV 字节数(70–502 KB/token)可当负载刻度用;405B dense 在 4 卡 50ms SLO 下因权重加载约 50ms 而不可服务,是权重流式读取压力的一个可引数字点。

三层对照点没有一层是在真实硬件上带真实专家路由测出来的:TileLens 的 trace 是真的但时序是模拟的,li2026 与 Micron 的专家行为全靠假设(li2026 连专家驻留策略都没交代,Micron 用 H2O/bias-8 的固定热度假设)。**我们的 dense vs MoE 实验能加的增量正是:真实执行中哪些专家真被激活、专家权重何时被读、读了多少字节,不需要假设**——这与文档 44 第五节对边缘部署线写的三条优势一致,对模拟器论文线同样成立。

**来源清单:** 五篇 .txt 全文(路径见各节,均一手核实)、`/root/hbfsim/HBFSim/docs/44-HBF-Sucks论文的核实与已发现问题.md`(一手读过,其内部核实等级沿用)、`/root/hbfsim/HBFSim/docs/ref_article/README.md` 第 325–373、532–605、662–710、1477–1608 行(登记条目,标转述处依赖它)。本轮未修改任何文件。