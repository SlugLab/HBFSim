# Hardware ground-truth and workload contract

> Historical frozen audit below. Current source and capability corrections are in [current-state-20260908.md](current-state-20260908.md) and [the canonical plan](../49-new-evaluation-plan.md). Current external versions and specification boundaries: [source research](review-evidence/20260908/source-research.md). Original historical findings retain their source SHA and are not silently renewed.

**预注册草案，待人工审阅后冻结，尚未采集新性能数据。** 实现依据见 [B01–B19/S01–S19](source-ledger.md)，方法来源 X02/doc19 与本轮用户 prompt；阈值和预算均是事前设计选择，不是测量。

## Evidence classes and hardware identity

- MEASURED：真实物理硬件直接量到的 GPU/SSD 时间、事件、字节等。用于 hypothetical profile 的模拟推断性能不得因运行于真实 GPU 就全部标 MEASURED：物理 observed kernel time 与 projected target decode time 分行。
- VALIDATED_MODEL：明确通过某真实硬件 calibration + heldout gates、且落在同一 validity domain 内的模型输出。MQSim 内部对照、六点自拟合、CPU fixture 都不足以获得此等级。
- PROJECTED：hypothetical HBF 参数下的模型或解析输出，含 trace-composed concurrency；即使其组件受硬件校准，外推仍是 PROJECTED。
- MOCK：人工图形预览，一律自动水印。不能把 MOCK 字段手工改成 MEASURED 作为真实替换。

本轮只读 `nvidia-smi` 返回无法与 driver 通信；`lsblk` 可见两块 Dell CD8P、PM1733a 及其他 NVMe，不能仅凭型号认领可用 test volume。GPU GDDR7 介质定位依据用户指定与 doc48，当前未重新识别 GPU 型号/显存/driver。CUDA 12.8/12.9/13.0 工具在 `/usr/local`，但 PATH 未提供它们；有工具不等于有可用 GPU。

设备选择：可用 CXL SSD → CD8P/PCIe5 → 其他稳定 flash。对 CXL 先确认是 block、DAX 或其他入口、实际映射 backing、NUMA/CPU/GPU 路径；不能用文件名或 CXL switch 存在来认定 payload 走 CXL。当前没有做远端盘点或请求负载。正式 run manifest 记录 GPU UUID、显存种类与总量、GPU/内存时钟、power limit、driver/toolchain、NUMA、SSD 型号/序列号/firmware/BDF、链路宽速、测试文件/offset/文件系统、direct-I/O/page-cache 状态、温度、电源与并存任务。

**GDDR7 provides the physical GPU execution substrate for validating address-range dispatch, PTX/SASS instrumentation, latency injection, critical-path placement, and asynchronous execution semantics.** 它不验证 HBM 或物理 HBF 介质；SSD 同样不叫 HBF ground truth。

## EQ1-A: critical-path known-delay test

先建一个独立、只读 dependent pointer chase：预生成 deterministic permutation，next address 必须依赖前一次 load 结果；一个 active lane/warp 负责 chain，其余 lanes 只在需要的 warp 协作路径参与。K=64 hops，额外扫描 K=1/16 作线性检查；所有输入真实驻留 GDDR7。结果 checksum 对 CPU/native。被测 load、独立 timestamp buffer 和最终 checksum 不能落入同一个 HBF treatment range。

D={0,0.5,1,2,5,10,20} us；warps={1,2,4,8,16}；occupancy={low,high}；native / fast-tier logical / HBF logical 三次同源运行。通过 blocks 与动态 shared-memory 限制 residency，保持每 warp 的指令与数据一致；记录实际 active blocks/SM、registers、warps、SM 数。若 occupancy 目标不可达，记 unsupported 配置，不能用标签代替实测 occupancy。

B 的 profile scalar 有非零校验且包含 transfer/serialization [B03]，所以不能令 `read_latency_ns=D` 就宣称精确 added D。最小增量是**仅 benchmark 显式启用**的 deterministic deadline path，保留 range 检查和 coverage gate，D=0 执行相同插桩但不延迟；若先用 epsilon profile，则量出残余，超过误差预算时点不能参加 known-D fidelity。旁路只关闭模型延迟，不放行 unsupported kernel。

每次同步/热身后量：每条 chain 的 `%globaltimer` begin/end、issued delay、wait stamps、CUDA Event kernel time、checksum、rewritten/unsupported 指令、dynamic bypass、covered bytes、unknown bytes。主图 y=(critical_chain(D)-critical_chain(0))/K，x=D，时间单位 us；注明链长度、warp/occupancy 切片。Event delta 单列，不能拿 wall time 替代每访问 critical path。D=0 仅报绝对 ns/us 残差，不做除零相对误差；多 warp 下不同 chain 各自保存值，不能把所有 warp 的 sum 当一条 critical path。

## EQ1-B: calibration / heldout / arrival freeze

尺寸固定 4/8/16/32/64/128/256 KiB 与 1 MiB；QD=1/2/4/8/16/32/64/128；sequential read 与 uniform random read。校准只允许 size={4,16,64,256} KiB 且 QD={1,4,16,64} 的 cell，其他 cell 全为预先锁定 heldout；pattern 和 arrival family 都参与分层。旧六点 vmem 数据已经被模型族看过，只能作为历史 calibration check，不能重新切一半冒充新 heldout。

创建 immutable split manifest（cell ID、seed、arrival hash、calibration/heldout、冻结 UTC、profile hash）；fit 进程只能访问 calibration 目录。完成 profile 冻结后才采 heldout。若重新拟合，validation ID 作废，必须另采 heldout，不能事后换点。

**两个不同 arrival 实验，不混为一谈：**

1. Closed-loop QD：物理设备与模拟器都遵守“completion 后补一条保持 QD”的策略；分别记录 achieved QD 和 completion 时间。两条路径的实际 arrival timestamps 可不同，因为其 completion 不同；比较的是同一个闭环过程。
2. Fixed-arrival trace：预先冻结 offsets、bytes、read/write、arrival_ns、seed；同一序列发给 hardware 和 MQSim。若硬件无法按时提交，保存 requested/actual arrival 与 admission lag，并给 MQSim 重放 actual arrivals，作为另一明确命名的配对。不能把 fio 的 iodepth 与批量同 t=0 提交给 MQSim 当成同到达。

现有 media adapter 跳过 NVMe/PCIe/host stack [B10]；优先单独标定 SSD 可观测端到端的 transfer/host residual，或使用全栈配置验证实盘，再对 adapter 的 media-only 截段另做对照。**不能拿 whole-SSD latency 直接称 NAND tR。** 保留操作粒度映射：4 KiB 到 1 MiB 的 host request 如何拆 NAND pages，complete 时间是所有子请求完成时间；tR/N 不是仅由 host request 大小决定。

记录每请求 submit/complete 延迟，用于 P50/P99；吞吐=completed bytes / steady-state interval，IOPS=completed requests / 同 interval。knee 预定义为最小 QD，其吞吐 ≥扫描最大吞吐的90%，且下一 QD 的增益<10%；不满足则 `knee_not_observed`，不强画拐点。每 cell 10 次独立 process repeat，每次至少30 s稳态、warmup10 s、有效请求数≥10000；P99 按各 run 分布报告，不把 run P99 的平均说成 pooled P99。矩阵用60 s/臂估算，实际 pilot 更新预算。

只读实验优先使用已授权且预填充、内容固定的专用测试文件；三臂同时冻结文件字节与 offset。全盘预处理、写满或 raw write 不属于本轮任务。稳态/预处理不足就写明适用范围，不虚称 MQSim preconditioning 反映了这块 SSD 的现状。

## Three-arm differential

| Arm | Data backing | Modeled service | Purpose |
|---|---|---|---|
| T0 | tmpfs 固定内容 | delay-disabled matched injection（或已量化 epsilon） | 相同软件/插桩基线 |
| THW | 已识别物理 flash 上相同内容 | 同 T0 | 实盘增量 |
| TSIM | tmpfs 相同内容 | 冻结该设备的 model profile | 模型增量 |

ΔT_HW=THW−T0；ΔT_SIM=TSIM−T0，配对同 repeat/seed，比较 signed deltas、parity、排序。THW 不能再叠加 SSD delay profile，否则双计。capacity 的 `pread`/page cache 要验证命中来源，不能把 page-cache 热命中当实盘读取。设备 cache、OS cache、GPU frame cache 各自冻结；“cold”必须写清是哪一层。不要用未包含 empirical curve 的 capacity 路径声称其受该曲线标定 [B04]。接近零的 delta 报绝对误差与噪声区间，不强求相对误差。

## EQ2 clocks and correctness

模型时钟：t_complete=t_issue+D；wait 到达时 residual=max(0,t_complete−t_wait)。W 必须用 issue 返回之后实际 independent ALU 时间校准，不只用 loop count 换算。保存 t_issue_enter/t_issue_return/t_wait_enter/t_wait_exit/t_consume；将 native latency、issue overhead、host lag、residual 分开。对 D=1/2/5/10/20 us 和 W/D=0/.25/.5/.75/1/1.5/2，单独对应 native、old、new。真实 native async completion 与 modeled deadline 都需要满足；模型 oracle 的理想式不覆盖额外 queue/transfer/contention，单流基准先隔离这些项，再做 robustness。

每点必须 checksum_ok=1、stale_reads=early_release=missing_transactions=deadlocks=timeouts=0；issued/completed/drained 守恒；unsupported/unknown 不为零则只报告其子集，不能 claim full coverage。要求动态 TensorMap A→B（A/B 含不同 pattern）、replace global address 或 copy updated descriptor、acquire 后 TMA 读 B；缺 acquire/错 generation 的 negative test 要拒绝。ptxas 优化保持开启，见 async audit 的 SASS mapping contract。

## EQ3 geometry and rho

```
C_fast_effective = C_device_total - resident_nonoffload_weights
                 - KV_cache - runtime_workspace - safety_reserve
rho_requested = C_fast_effective / W_HBF_eligible
```

每 run 从 config、真实张量 headers/shape/dtype、allocator/device stats 和 KV shape 测预算，权重、shared expert/attention、KV、workspace、runtime/safety 不得重复计数。负剩余或无法 fit 记 infeasible，不偷偷 clamp 到零；rho>1 的物理预算保存 raw，图上的 eligible residency 饱和为1并单列 unused bytes。whole-expert placement 的 rounded `rho_achieved` 与 requested 同报。扫描 .0625/.125/.25/.5/.75/1。published 1:8/1:16 只有按该文分母和预算转换后才能作为 rho marker。

| N per cube | Source proposal and conversion | Classification |
|---:|---|---|
| 256 | doc47；issue15 的16 banks/channel ×16 channels；需假设独立 sense | PROJECTED organization interpretation |
| 1024 | issue15：16 banks/die ×4 dies/channel ×16 channels | PROJECTED conversion |
| 1536 | issue15 对 FlashAccel 的96 planes/die ×16 dies/cube 转述 | PROJECTED，外部原文/组织须另核 |
| 4883 | issue15：8 TB/s ×20 us /4096 B /8 cubes ≈4882.8125 | PROJECTED rounded requirement；8 cubes 不是已知事实 |
| 3000 | doc47：3072 GB/s ×4 us /4096 B | PROJECTED throughput requirement, not measured topology |
| 15000 | doc47：3072 GB/s ×20 us /4096 B | PROJECTED throughput requirement, not measured topology |

上述 doc47/issue15 均在 B SHA，issue15 §1 L127–180 是转换原文；架构规范 v0.7.0 的 interface 数字只作为给定 ceiling，不是 sustainable array bandwidth，也未给 tR。`BW_array=N*page_bytes/tR` 用 bytes/s，GB/s=10^9；4KiB=4096B。图纵轴取 steady-state completion bytes/time 的 sustainable service；如果仅计算公式，metric/source_function 必须标解析，不可称 simulator output。

MQSim 目前 chip/channel=1，channels×dies/channel×planes/die 是候选 N 的映射 [B10]，不是独立 sense 能力证明。先固定16 channels，256→16×1×16，1024→16×4×16，1536→16×1×96；3000/4883/15000 无 exact 均匀16-channel 分解，记录 requested/achieved geometry，禁止静默取整；最小配置这些点使用显式解析 projection，理想配置用可验证非均匀 bank 调度模型。还须检查地址 interleaving、容量/plane 几何和 transfer bottleneck。

输入 tR=1/2/4/5/10/20 us。输出 normalized decode time=T_candidate/T_native、normalized throughput=tok/s_candidate/tok/s_native、HBF B/token、hit/miss、QD、P50/P99 modeled latency、utilization。相同生成 token 数时两归一化量互为倒数，但仍单独采集，变化的 active batch/sequence 长度不得强制倒数。边界按慢于基线5/10/20%画等高线；采样区外不外推，缺格不插值；未校准 HBF 全域 PROJECTED。

## EQ4 traces, null, dense and prefetch

真实 checkpoint 必须解析 E、k、layers、expert tensors/dtype/bytes、shared experts、attention 与 KV characteristics。禁止从模型名字猜 E/k；base ModelInventory 只读既有 manifest [B14]，需要 X 的 pretrace inventory/capture 工具与额外预算项。MOCK 的 E=96/k=6 是假想 fixture，不是 Qwen 参数。

每 step×layer×sequence 原始 JSONL 保留 active sequence IDs、token step、expert IDs、frequency、unique union/U/E、entropy=−Σp log2 p、Gini（包含零频专家）、inter-step Jaccard、expert-object reuse distance、touched weight bytes、actual HBF bytes、fast hits/misses、prefetch late/useful/useless、decode-step time。missing/NA 不填零。路由 selected weight bytes 不等于真实 GPU transferred bytes；后者要有独立 capture/counter。

真实 batch trace 优先；若 runner 只支持单序列，则用冻结16 prompts的轨迹按 token step 组合，注明 trace-composed concurrency，不共享真实 scheduler 时间。B=1/2/4/8/16/32/64/128 取 active sequences，不用 max_batch_size。对结束的 sequence 从下一 step union 中移除。

对每 layer，null `U_null/E=1-(1-k/E)^B`；只在相同 E/k 的均匀、跨 sequence 独立 top-k 模型成立。shuffled routing 在每 layer 保留每 sequence 的完整 top-k set，独立打乱 step 顺序，破坏对齐相关性而保留边际热度，使用独立 seed/重复，禁止仅对所有专家统一重命名（那不改变 union）。real trace/shuffled/null 并列，null 不替代真实 trace，不能据其推导 measured throughput。

Dense 两个匹配规则：①capacity matched，eligible total weight bytes（固定精度）与主 MoE ±10%；②active-compute matched，同 batch/length 下每 token active FLOPs ±10%。模型名不提前硬填，inventory 完成后选最近且报告实际差距；相同 tokenizer/task/sequence 协议无法保证时要记录任务差异。只满足一个时明确哪个；**不把这两个对照伪装成等价因果单变量实验**。

三种预取含义冻结：no_prefetch=无软件/async lookahead 的同步 issue-stall 控制；on_demand=无提前猜取但保留正确 async issue/use 与 fast cache；one_layer_ahead=layer l 用合法可用信息对 l+1 发起预取，不偷看真实未来 routing。另可加 future-trace oracle，只叫 oracle upper bound。若两个无预取臂实际路径相同，就合并并说明，而不是人为制造差异。

按实际 deadline 定义 timely coverage=消费前完成的有用 prefetched demand bytes / eligible demand-miss bytes；late=需求到达时未完成的预取；useless bytes=prefetched 后未消费即逐出或 run-end 仍未消费；extra traffic=(total media read bytes−matched on-demand media bytes)/matched on-demand media bytes；prefetch evictions 独立计。每个 speculative request 都必须入同一介质队列，占带宽/容量；同一 request 在 demand promotion 时不双计。replacement 只有使边界 shift 改变>5个百分点或政策排序翻转（含 CI）才升级 ablation。

## Statistics and invalid observations

随机化条件执行顺序，micro/storage 10 repeats、application 5 repeats；warmup 与测量区分、seed/hash/clock/thermal drift 同录。主结果 mean + 95% bootstrap run CI；延迟分位按各 run原始请求计算后报告分布，相关性数据按 prompt/run block bootstrap，不能把同一 decode 的 layers 当独立 repeats。accuracy 同时报 abs error、signed/absolute relative error、median/P95/max；近零 denominator 不算相对值。5/10/20%边界的 CI 来自 paired runtime repeats 或参数敏感性带，二者分开；绝不把参数扫描带称统计 CI。记录失败/timeout/unsupported 在状态工件，不把其填为零延迟或从样本数中悄悄删除。
